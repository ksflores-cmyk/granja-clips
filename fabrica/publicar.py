"""Publicador: sube cada video terminado al almacén y lo programa con la API GraphQL de Buffer.

Datos de la API (developers.buffer.com, consultada el 17 nov 2026): un solo endpoint
`POST https://api.buffer.com` con `{"query", "variables"}` y `Authorization: Bearer <BUFFER_API_KEY>`.
Sólo urllib. La clave sale de la variable de entorno BUFFER_API_KEY: nunca se imprime ni se guarda.
Donde la documentación deja dudas, decide la introspección del esquema en tiempo de ejecución.

Orden de seguridad (`ejecutar`): PAUSA, luego modo. En ensayo no hay `createPost`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import red
from .config import cargar_yaml, raiz_repo
from .pausa import ZONA, crear_pausa, esta_pausada

ENDPOINT = "https://api.buffer.com"
SERVICIOS = {"shorts": "youtube", "tiktok": "tiktok", "reels": "instagram"}
PLATAFORMAS = tuple(SERVICIOS)
MINUTOS_MINIMOS_FUTURO = 10
UMBRAL_CUOTA = 0.8
# Topes del plan gratis de la API: 150 llamadas/15 min, 600/día, 1000/mes por canal (3000 en total).
TOPES = {"ventana_15min": 150, "dia": 600, "mes": 3000, "canal_mes": 1000}
VENTANA_S = 15 * 60
# Campos opcionales que enviamos: si el esquema no los tiene, se quitan y se anota.
OPCIONALES = {"madeForKids", "privacy", "shouldShareToFeed"}
TIPOS_BASE = ["CreatePostInput", "PostInputMetaData", "YoutubePostMetadataInput",
              "InstagramPostMetadataInput", "DeletePostInput"]

Q_ORGANIZACIONES = "query { account { organizations { id name } } }"
Q_CANALES = ("query { channels(input: { organizationId: %s }) "
             "{ id name displayName service isQueuePaused } }")
M_CREAR = ("mutation($input: CreatePostInput!) { createPost(input: $input) { "
           "... on PostActionSuccess { post { id status dueAt } } "
           "... on MutationError { message } } }")
M_BORRAR = ("mutation($input: DeletePostInput!) { deletePost(input: $input) { "
            "__typename ... on MutationError { message } } }")
Q_TIPO = ("query($n: String!) { __type(name: $n) { name inputFields { name defaultValue "
          "type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } } } }")
Q_CONSULTAS = "query { __type(name: \"Query\") { fields { name args { name } } } }"


class ErrorBuffer(Exception):
    """Fallo de la API de Buffer (red, código o `errors` de GraphQL)."""


class ErrorCuota(ErrorBuffer):
    """Se llegó al 80 % de algún tope de la API: no se llama más."""


class Descartada(Exception):
    """Esta publicación se descarta con su motivo; no frena el resto."""


class AlertaRoja(Exception):
    """Canal ajeno, cola pausada u otro riesgo de publicar en lo que no es nuestro."""


# ---------------------------------------------------------------- cuota

class Cuota:
    """Contador en estado/buffer-cuota.json: ventana de 15 min, día y mes (hora de Guatemala) y por canal."""

    def __init__(self, raiz=None, ahora=None):
        self.ruta = raiz_repo(raiz) / "estado" / "buffer-cuota.json"
        self.ahora = ahora or (lambda: datetime.now(timezone.utc))

    def _leer(self) -> dict:
        t = self.ahora()
        local = t.astimezone(ZONA)
        d = {"ventana": [], "dia": local.date().isoformat(), "llamadas_dia": 0,
             "mes": local.strftime("%Y-%m"), "llamadas_mes": 0, "canales_mes": {}, "notas": []}
        if self.ruta.exists():
            try:
                d.update(json.loads(self.ruta.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        if d["dia"] != local.date().isoformat():
            d["dia"], d["llamadas_dia"] = local.date().isoformat(), 0
        if d["mes"] != local.strftime("%Y-%m"):
            d["mes"], d["llamadas_mes"], d["canales_mes"] = local.strftime("%Y-%m"), 0, {}
        corte = t.timestamp() - VENTANA_S
        d["ventana"] = [x for x in d["ventana"] if x > corte]
        return d

    def _guardar(self, d: dict) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        d["notas"] = d["notas"][-20:]
        self.ruta.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")

    def _usos(self, d, canal=None) -> dict:
        usos = {"ventana_15min": len(d["ventana"]), "dia": d["llamadas_dia"], "mes": d["llamadas_mes"]}
        if canal:
            usos["canal_mes"] = d["canales_mes"].get(canal, 0)
        return usos

    def comprobar(self, canal=None) -> None:
        """Lanza ErrorCuota si alguna cuenta ya está en el 80 % de su tope (y lo anota)."""
        d = self._leer()
        for nombre, usado in self._usos(d, canal).items():
            if usado >= TOPES[nombre] * UMBRAL_CUOTA:
                d["notas"].append(f"{self.ahora().isoformat()} cuota {nombre}: {usado}/{TOPES[nombre]}; "
                                  "no se llama más")
                self._guardar(d)
                raise ErrorCuota(f"cuota de Buffer al 80 % en {nombre} ({usado} de {TOPES[nombre]}); "
                                 "no se llama más")

    def registrar(self, canal=None) -> None:
        d = self._leer()
        d["ventana"].append(self.ahora().timestamp())
        d["llamadas_dia"] += 1
        d["llamadas_mes"] += 1
        if canal:
            d["canales_mes"][canal] = d["canales_mes"].get(canal, 0) + 1
        self._guardar(d)

    def resumen(self) -> dict:
        d = self._leer()
        u = self._usos(d)
        return {n: f"{u[n]}/{TOPES[n]}" for n in u}


# ---------------------------------------------------------------- cliente GraphQL

class ClienteBuffer:
    def __init__(self, clave, transporte=None, raiz=None, dormir=time.sleep, ahora=None):
        if not clave:
            raise ErrorBuffer("falta BUFFER_API_KEY")
        self._clave = clave
        self.transporte = transporte or red.transporte_urllib
        self.dormir = dormir
        self.cuota = Cuota(raiz, ahora)

    def consulta(self, query: str, variables: dict | None = None, canal: str | None = None) -> dict:
        """Una llamada GraphQL. Devuelve `data`; los `errors` lanzan ErrorBuffer."""
        cuerpo = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        cab = {"Authorization": f"Bearer {self._clave}", "Content-Type": "application/json",
               "Accept": "application/json"}

        def contar():
            self.cuota.comprobar(canal)
            self.cuota.registrar(canal)

        estado, _, datos = red.solicitar(self.transporte, "POST", ENDPOINT, cab, cuerpo,
                                         dormir=self.dormir, alerta=contar)
        if estado != 200:
            raise ErrorBuffer(f"POST {red.ruta_de(ENDPOINT)}: código {estado}")
        try:
            r = json.loads(datos.decode("utf-8"))
        except ValueError:
            raise ErrorBuffer(f"POST {red.ruta_de(ENDPOINT)}: la respuesta no es JSON") from None
        if not isinstance(r, dict):
            raise ErrorBuffer(f"POST {red.ruta_de(ENDPOINT)}: respuesta inesperada")
        if r.get("errors"):
            msgs = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e)
                             for e in r["errors"])
            raise ErrorBuffer("GraphQL: " + red.ocultar(msgs, [self._clave])[:400])
        return r.get("data") or {}

    # ---- lecturas ----

    def organizaciones(self) -> list[dict]:
        return self.consulta(Q_ORGANIZACIONES)["account"]["organizations"]

    def canales(self, org_id: str) -> list[dict]:
        return self.consulta(Q_CANALES % _lit(org_id))["channels"]

    def todos_los_canales(self) -> list[dict]:
        canales = []
        for org in self.organizaciones():
            for c in self.canales(org["id"]):
                canales.append({**c, "_org": org["id"]})
        return canales

    def introspeccion(self) -> dict:
        """Esquema normalizado de los tipos de entrada que usamos (ver `normalizar_esquema`)."""
        crudos: dict[str, list] = {}
        pendientes = list(TIPOS_BASE)
        vistos = set()
        while pendientes and len(vistos) < 14:
            nombre = pendientes.pop(0)
            if nombre in vistos:
                continue
            vistos.add(nombre)
            t = self.consulta(Q_TIPO, {"n": nombre}).get("__type")
            if not t:
                continue
            crudos[nombre] = t.get("inputFields") or []
            campos = normalizar_campos(crudos[nombre])
            siguientes = []
            if nombre == "CreatePostInput":
                siguientes.append(campos.get("assets", {}).get("tipo"))
            if nombre == "PostInputMetaData":
                siguientes.append(campos.get("tiktok", {}).get("tipo"))
            if campos.get("video"):
                siguientes.append(campos["video"]["tipo"])
            pendientes += [s for s in siguientes if s]  # el activo de video sale de `assets` y su `video`
        q = self.consulta(Q_CONSULTAS).get("__type") or {}
        return normalizar_esquema(crudos, q.get("fields") or [])


def _lit(valor) -> str:
    """Literal de cadena GraphQL (los ids van en línea, como en la documentación)."""
    return json.dumps(str(valor))


def _base(tipo: dict | None) -> tuple[str | None, bool, bool]:
    """(nombre base, obligatorio, es lista) de un tipo de introspección anidado."""
    obligatorio = bool(tipo) and tipo.get("kind") == "NON_NULL"
    lista = False
    while tipo:
        if tipo.get("kind") == "LIST":
            lista = True
        if tipo.get("name"):
            return tipo["name"], obligatorio, lista
        tipo = tipo.get("ofType")
    return None, obligatorio, lista


def normalizar_campos(input_fields: list[dict]) -> dict:
    """{campo: {tipo, obligatorio, lista}}. Obligatorio = NON_NULL y sin valor por defecto."""
    campos = {}
    for f in input_fields:
        nombre, obligatorio, lista = _base(f.get("type"))
        campos[f["name"]] = {"tipo": nombre, "lista": lista,
                             "obligatorio": obligatorio and f.get("defaultValue") is None}
    return campos


def normalizar_esquema(crudos: dict, campos_query: list[dict] | None = None) -> dict:
    return {"tipos": {n: normalizar_campos(f) for n, f in crudos.items()},
            "consultas": {c["name"]: [a["name"] for a in c.get("args") or []]
                          for c in campos_query or []}}


# ---------------------------------------------------------------- carga validada contra el esquema

def _validar_tipo(nombre: str, valor: dict, esquema: dict, anot: list[str], exigidos=()) -> dict:
    campos = esquema["tipos"].get(nombre)
    if campos is None:
        anot.append(f"tipo {nombre} no consultado: no se valida")
        return valor
    limpio = {}
    for k, v in valor.items():
        if k not in campos:
            if k in OPCIONALES:
                anot.append(f"sobra {nombre}.{k}: el esquema no lo tiene; se quita")
                continue
            raise Descartada(f"el esquema no tiene {nombre}.{k}")
        sub = campos[k]["tipo"]
        if isinstance(v, dict) and sub in esquema["tipos"]:
            v = _validar_tipo(sub, v, esquema, anot)
        elif isinstance(v, list) and sub in esquema["tipos"]:
            v = [_validar_tipo(sub, x, esquema, anot) if isinstance(x, dict) else x for x in v]
        limpio[k] = v
    for k in exigidos:
        if k not in campos:
            raise Descartada(f"el esquema no tiene {nombre}.{k}, necesario aquí")
    for k, c in campos.items():
        if c["obligatorio"] and k not in limpio:
            raise Descartada(f"falta el campo obligatorio {nombre}.{k}")
    return limpio


def hueco_a_utc(dia: date, hora: str, zona: str = "America/Guatemala") -> datetime:
    """Hueco (HH:MM) de un día en su zona, convertido a UTC."""
    h, m = (int(x) for x in hora.split(":"))
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=ZoneInfo(zona)).astimezone(timezone.utc)


def iso_utc(cuando: datetime) -> str:
    return cuando.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def validar_futuro(cuando: datetime, ahora: datetime) -> None:
    if cuando.astimezone(timezone.utc) < ahora.astimezone(timezone.utc) + timedelta(minutes=MINUTOS_MINIMOS_FUTURO):
        raise Descartada(f"el hueco {iso_utc(cuando)} no está al menos {MINUTOS_MINIMOS_FUTURO} "
                         "minutos en el futuro")


def armar_carga(plataforma: str, canal_id: str, texto: str, titulo: str | None, url_video: str,
                cuando: datetime, esquema: dict, borrador: bool = False) -> tuple[dict, list[str]]:
    """Arma el `input` de createPost y lo comprueba contra el esquema. Devuelve (carga, anotaciones).

    Lanza Descartada si falta un campo obligatorio o si el esquema no tiene un campo que sí necesitamos.
    """
    if plataforma not in SERVICIOS:
        raise Descartada(f"plataforma desconocida: {plataforma}")
    carga = {"channelId": canal_id, "text": texto, "schedulingType": "automatic",
             "mode": "customScheduled", "dueAt": iso_utc(cuando),
             "assets": [{"video": {"url": url_video}}]}
    meta = None
    if plataforma == "shorts":
        if not titulo:
            raise Descartada("YouTube exige título")
        meta = {"youtube": {"title": titulo, "categoryId": "20", "privacy": "public",
                            "madeForKids": False}}
    elif plataforma == "reels":
        meta = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    else:  # tiktok: la documentación no da campos; si el esquema exige alguno, se descarta con su motivo
        tipo_tt = esquema["tipos"].get("PostInputMetaData", {}).get("tiktok", {}).get("tipo")
        if tipo_tt in esquema["tipos"] and any(c["obligatorio"] for c in esquema["tipos"][tipo_tt].values()):
            meta = {"tiktok": {}}
    if meta:
        carga["metadata"] = meta
    if borrador:
        carga["saveToDraft"] = True
    anot: list[str] = []
    exigidos = ("saveToDraft",) if borrador else ()
    return _validar_tipo("CreatePostInput", carga, esquema, anot, exigidos), anot


# ---------------------------------------------------------------- canales

def _norm(nombre) -> str:
    return str(nombre or "").strip().lstrip("@").strip().lower()


def _alerta(raiz, motivo: str, escribir: bool) -> None:
    if escribir:
        crear_pausa(raiz, motivo, "publicador")
        ruta = raiz_repo(raiz) / "estado" / "alerta-roja.json"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({
            "fecha": datetime.now(ZONA).replace(microsecond=0).isoformat(),
            "origen": "fabrica.publicar", "motivo": motivo,
            "accion": "N6 abre el issue de alerta roja"}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    raise AlertaRoja(motivo)


def confirmar_canales(canales_api: list[dict], canales_cfg: dict, raiz=None, plataformas=None,
                      escribir: bool = True) -> dict:
    """Para cada plataforma debe haber exactamente un canal con su servicio y su nombre esperado.

    Devuelve {plataforma: id}. Si algo no coincide, o la cola está pausada, es alerta roja: crea PAUSA
    (si `escribir`), deja `estado/alerta-roja.json` para N6 y lanza AlertaRoja. No programa nada.
    """
    plataformas = list(plataformas if plataformas is not None else PLATAFORMAS)
    ids = {}
    for plat in plataformas:
        ficha = canales_cfg[plat]
        esperado, servicio = _norm(ficha["nombre_esperado"]), str(ficha["servicio"]).lower()
        coinciden = [c for c in canales_api
                     if str(c.get("service", "")).lower() == servicio
                     and esperado in (_norm(c.get("name")), _norm(c.get("displayName")))]
        if len(coinciden) != 1:
            _alerta(raiz, f"canal de {plat} no confirmado: se esperaba exactamente un canal "
                          f"'{ficha['nombre_esperado']}' de {servicio} y hay {len(coinciden)}", escribir)
        if coinciden[0].get("isQueuePaused"):
            _alerta(raiz, f"la cola del canal de {plat} ('{ficha['nombre_esperado']}') está pausada "
                          "en Buffer", escribir)
        ids[plat] = coinciden[0]["id"]
    return ids


def guardar_ids_de_canales(raiz, ids: dict) -> None:
    """Escribe buffer_channel_id en config/canales.yml cambiando sólo esos valores."""
    ruta = raiz_repo(raiz) / "config" / "canales.yml"
    lineas = ruta.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, linea in enumerate(lineas):
        m = re.match(r"^(\w+):", linea)
        if m and m.group(1) in ids:
            lineas[i] = re.sub(r"buffer_channel_id:\s*[^,}\s]*", f"buffer_channel_id: {ids[m.group(1)]}", linea)
    ruta.write_text("".join(lineas), encoding="utf-8")


# ---------------------------------------------------------------- textos (playbook, sección 10)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕⌀-⏿\U0001F1E6-\U0001F1FF]")
_GTA = re.compile(r"\bGTA\s*(?:6|VI)\b|\bGTA6\b", re.IGNORECASE)
_TOMA = re.compile(r"^Toma\s+\d+\.\s*")
DIAS_SIN_REPETIR = 7
MAX_TEXTO = 2200
MAX_TITULO = 100


def contar_emojis(texto: str) -> int:
    return len(_EMOJI.findall(texto))


def _limpio(s) -> str:
    return _GTA.sub("GTA 6", " ".join(str(s or "").split()))


def _titulo_youtube(linea: str) -> str:
    if len(linea) <= MAX_TITULO:
        return linea
    corte = linea[:MAX_TITULO - 1].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return corte + "…"


def _hashtags(etiquetas) -> list[str]:
    return [e for e in etiquetas if e.startswith("#")]


def textos(toma: dict, ficha: dict, plataforma: str, raiz=None, hoy: date | None = None) -> dict:
    """Texto de la publicación según el playbook (sección 10).

    `toma`: numero, gancho, pilar, creador (opcional), valor (frases, Instagram), etiquetas (del brief),
    titular y datos (informativa). Devuelve {texto, titulo, credito, etiquetas}; Descartada si no cumple.
    """
    if plataforma not in SERVICIOS:
        raise Descartada(f"plataforma desconocida: {plataforma}")
    reglas = ficha.get("reglas") or {}
    informativa = toma.get("pilar") == "informativa"
    creador = "@RockstarGames" if informativa else _limpio(toma.get("creador") or ficha.get("creador"))
    if not creador:
        raise Descartada("sin crédito: no se publica")
    credito = f"🎥 {creador}"
    obligatorias = [str(e).strip() for e in reglas.get("etiquetas_obligatorias") or []]  # tal cual
    extra = [str(e).strip() for e in toma.get("etiquetas") or []]
    visto = {e.lower() for e in obligatorias}
    extra = [e for e in extra if not (e.lower() in visto or visto.add(e.lower()))]
    # Shorts y TikTok: pocas etiquetas (hasta 3 hashtags); Instagram: de 3 a 5. Las del brief van siempre.
    tope = 5 if plataforma == "reels" else 3
    etiquetas = list(obligatorias)
    for e in extra:
        if len(_hashtags(etiquetas)) < tope:
            etiquetas.append(e)
    if plataforma == "reels" and not 3 <= len(_hashtags(etiquetas)):
        raise Descartada("Instagram necesita de 3 a 5 etiquetas y no hay suficientes")
    divulgacion = str(reglas.get("divulgacion") or "").strip()
    aviso = [] if divulgacion.lower() in ("", "ninguna") else [divulgacion]
    num = f"{int(toma['numero']):03d}" if str(toma.get("numero", "")).isdigit() else str(toma.get("numero", ""))
    gancho = _limpio(toma.get("gancho"))
    if informativa:
        titular = _limpio(toma.get("titular") or gancho)
        if not titular:
            raise Descartada("la informativa necesita titular")
        primera = f"GTA 6: {titular}"
        cuerpo = ["- " + _limpio(d) for d in toma.get("datos") or []]
        bloques = [primera] + ([("\n".join(cuerpo))] if cuerpo else [])
    else:
        if not gancho:
            raise Descartada("sin gancho")
        primera = f"Toma {num}. {gancho}"
        bloques = [primera]
        if plataforma == "reels":
            valor = [_limpio(v) for v in (toma.get("valor") or [])][:2]
            if valor:
                bloques.append(" ".join(valor))
    bloques.append(credito)
    cola = list(aviso)
    if etiquetas:
        cola.append(" ".join(etiquetas))
    texto = "\n\n".join(bloques[:-1]) + "\n\n" + "\n".join([bloques[-1]] + cola)
    if contar_emojis(texto) > 1:
        raise Descartada("el texto lleva más de un emoji")
    if len(texto) > MAX_TEXTO:
        raise Descartada(f"el texto pasa de {MAX_TEXTO} caracteres")
    titulo = _titulo_youtube(primera) if plataforma == "shorts" else None
    if raiz is not None:
        _comprobar_repetido(raiz, texto, plataforma, toma, hoy)
    return {"texto": texto, "titulo": titulo, "credito": credito, "etiquetas": etiquetas}


def _cuerpo_sin_toma(texto: str) -> str:
    return _TOMA.sub("", " ".join(texto.split())).lower()


def _ruta_estado(raiz, nombre: str) -> Path:
    return raiz_repo(raiz) / "estado" / nombre


def _leer_json(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _comprobar_repetido(raiz, texto: str, plataforma: str, toma: dict, hoy: date | None) -> None:
    """El mismo texto no se repite en 7 días: registro propio de textos y `gancho` del marcador."""
    hoy = hoy or datetime.now(ZONA).date()
    desde = hoy - timedelta(days=DIAS_SIN_REPETIR)
    cuerpo = _cuerpo_sin_toma(texto)
    huella = hashlib.sha256(cuerpo.encode("utf-8")).hexdigest()
    for r in _leer_json(_ruta_estado(raiz, "buffer-textos.json"), []):
        if r.get("plataforma") == plataforma and r.get("huella") == huella \
                and date.fromisoformat(r["fecha"]) > desde:
            raise Descartada(f"texto repetido: ya se publicó el {r['fecha']} (no se repite en 7 días)")
    marcador = Path(raiz_repo(raiz) / "marcador.csv")
    if marcador.exists():
        import csv
        gancho = _cuerpo_sin_toma(f"Toma 0. {_limpio(toma.get('gancho'))}")
        with open(marcador, newline="", encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                try:
                    fecha = date.fromisoformat(fila.get("fecha", "")[:10])
                except ValueError:
                    continue
                if fecha > desde and fila.get("plataforma") == plataforma \
                        and str(fila.get("toma")) != str(toma.get("numero")) \
                        and _cuerpo_sin_toma(f"Toma 0. {fila.get('gancho', '')}") == gancho:
                    raise Descartada(f"gancho repetido: ya salió el {fila['fecha']} (no se repite en 7 días)")


# ---------------------------------------------------------------- registro (marcador)

def _anotar_fila(raiz, fila: dict) -> None:  # TODO N4
    """Provisional hasta que llegue `registro.anotar_clip` (N4): escribe la misma fila con anotar_fila."""
    from . import registro
    registro.anotar_fila(raiz, fila)


def _anotar(raiz, fila: dict) -> None:
    from . import registro
    fn = getattr(registro, "anotar_clip", None) or _anotar_fila
    fn(raiz, fila)


# ---------------------------------------------------------------- publicador

class Publicador:
    """`modo`: ensayo (nunca createPost), borrador (createPost con saveToDraft y deletePost) o vivo."""

    def __init__(self, cliente: ClienteBuffer, almacen=None, raiz=None, modo: str = "ensayo",
                 ahora=None, canales: dict | None = None, esquema: dict | None = None):
        if modo not in ("ensayo", "borrador", "vivo"):
            raise ValueError(f"modo desconocido: {modo}")
        self.cliente, self.almacen, self.raiz, self.modo = cliente, almacen, raiz, modo
        self.ahora = ahora or (lambda: datetime.now(timezone.utc))
        self.canales = canales or {}
        self._esquema = esquema

    @property
    def esquema(self) -> dict:
        if self._esquema is None:
            self._esquema = self.cliente.introspeccion()
        return self._esquema

    def _crear(self, carga: dict, canal_id: str) -> dict:
        if self.modo == "ensayo":
            raise ErrorBuffer("createPost no está permitido en ensayo")
        if self.modo == "borrador" and carga.get("saveToDraft") is not True:
            raise ErrorBuffer("en borrador createPost sólo se manda con saveToDraft")
        data = self.cliente.consulta(M_CREAR, {"input": carga}, canal=canal_id)
        r = data.get("createPost") or {}
        if "post" not in r:
            raise Descartada(f"Buffer rechazó la publicación: {red.ocultar(str(r.get('message', 'sin motivo')), [self.cliente._clave])}")
        return r["post"]

    def programar(self, plataforma: str, toma: dict, url_video: str, textos: dict, cuando: datetime,
                  ruta_almacen: str | None = None, ficha: dict | None = None) -> dict:
        """createPost con hora explícita (customScheduled, nunca addToQueue). Devuelve {id, dueAt}."""
        if self.modo != "vivo":
            raise ErrorBuffer("programar sólo se usa en modo vivo")
        canal = self.canales.get(plataforma)
        if not canal:
            raise Descartada(f"canal de {plataforma} sin confirmar")
        validar_futuro(cuando, self.ahora())
        carga, anot = armar_carga(plataforma, canal, textos["texto"], textos.get("titulo"),
                                  url_video, cuando, self.esquema)
        post = self._crear(carga, canal)
        self._registrar(plataforma, toma, textos, cuando, post, ruta_almacen, ficha)
        return {"id": post["id"], "dueAt": post.get("dueAt"), "anotaciones": anot}

    def _registrar(self, plataforma, toma, textos, cuando, post, ruta_almacen, ficha) -> None:
        local = cuando.astimezone(ZONA)
        num = f"{int(toma['numero']):03d}" if str(toma.get("numero", "")).isdigit() else str(toma.get("numero"))
        fuente_id = toma.get("fuente_id") or (ficha or {}).get("id") or ""
        _anotar(self.raiz, {
            "id_clip": f"toma-{num}-{plataforma}", "toma": toma.get("numero"),
            "fecha": local.date().isoformat(), "hora": local.strftime("%H:%M"),
            "plataforma": plataforma, "pilar": toma.get("pilar", ""), "fuente_id": fuente_id,
            "campana": toma.get("campana") or (ficha or {}).get("campana") or "",
            "gancho": toma.get("gancho", ""), "duracion_s": toma.get("duracion_s", ""),
            "credito": textos.get("credito", ""), "url": "", "estado_campana": "",
            "notas": f"origen:{fuente_id} buffer:{post['id']}"})
        textos_previos = _leer_json(_ruta_estado(self.raiz, "buffer-textos.json"), [])
        textos_previos.append({"fecha": self.ahora().astimezone(ZONA).date().isoformat(),
                               "plataforma": plataforma,
                               "huella": hashlib.sha256(_cuerpo_sin_toma(textos["texto"]).encode()).hexdigest()})
        _escribir_json(_ruta_estado(self.raiz, "buffer-textos.json"), textos_previos[-300:])
        if ruta_almacen:
            envios = _leer_json(_ruta_estado(self.raiz, "buffer-envios.json"), [])
            envios.append({"post_id": post["id"], "plataforma": plataforma, "ruta": ruta_almacen,
                           "canal": self.canales.get(plataforma)})
            _escribir_json(_ruta_estado(self.raiz, "buffer-envios.json"), envios)

    # ---- estado de un post ----

    def estado(self, post_id: str, org_id: str | None = None) -> str | None:
        """Estado del post (por ejemplo `sent`). Usa la consulta por id si la introspección la muestra."""
        consultas = self.esquema.get("consultas", {})
        if "post" in consultas:
            if "input" in consultas["post"]:
                q = "query { post(input: { id: %s }) { id status dueAt } }" % _lit(post_id)
            else:
                q = "query { post(id: %s) { id status dueAt } }" % _lit(post_id)
            return (self.cliente.consulta(q).get("post") or {}).get("status")
        # Sin consulta por id: se busca entre los posts ya enviados (sólo sirve para saber si quedó `sent`).
        org_id = org_id or self.cliente.organizaciones()[0]["id"]
        ids = ", ".join(_lit(c) for c in self.canales.values())
        q = ("query { posts(input: { organizationId: %s, filter: { status: [sent], channelIds: [%s] } }) "
             "{ edges { node { id status dueAt } } } }" % (_lit(org_id), ids))
        data = self.cliente.consulta(q)
        for e in (data.get("posts") or {}).get("edges") or []:
            if e["node"]["id"] == post_id:
                return e["node"].get("status")
        return None

    def limpiar_enviados(self) -> list[str]:
        """Borra del almacén los videos cuyos posts ya están en `sent`. Devuelve las rutas borradas."""
        ruta = _ruta_estado(self.raiz, "buffer-envios.json")
        envios = _leer_json(ruta, [])
        por_ruta: dict[str, list] = {}
        for e in envios:
            por_ruta.setdefault(e["ruta"], []).append(e)
        borradas, restantes = [], []
        for r, posts in por_ruta.items():
            if all(self.estado(p["post_id"]) == "sent" for p in posts):
                self.almacen.borrar(r)
                borradas.append(r)
            else:
                restantes += posts
        _escribir_json(ruta, restantes)
        return borradas

    # ---- borrador ----

    def borrador(self, plataforma: str, url_video: str, cuando: datetime) -> dict:
        """createPost con saveToDraft sobre un canal confirmado y deletePost enseguida. No publica nada."""
        if self.modo != "borrador":
            raise ErrorBuffer("borrador sólo se usa en modo borrador")
        canal = self.canales[plataforma]
        carga, anot = armar_carga(plataforma, canal, "Borrador de comprobación (se borra enseguida)",
                                  "Borrador de comprobación", url_video, cuando, self.esquema, borrador=True)
        post = self._crear(carga, canal)
        try:
            entrada = _validar_tipo("DeletePostInput", {"id": post["id"]}, self.esquema, anot)
            r = self.cliente.consulta(M_BORRAR, {"input": entrada}, canal=canal)
        except (ErrorBuffer, Descartada) as e:
            raise ErrorBuffer(f"el borrador {post['id']} se creó pero no se pudo borrar: {e}") from None
        borrado = (r.get("deletePost") or {})
        if "message" in borrado:
            raise ErrorBuffer(f"el borrador {post['id']} no se borró: {borrado['message']}")
        return {"id": post["id"], "borrado": True, "anotaciones": anot}


def _escribir_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- modos y orden de seguridad

SECRETOS_BUFFER = ("BUFFER_API_KEY",)
SECRETOS_ALMACEN = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
COD_OK, COD_ERROR, COD_ALERTA = 0, 1, 3


def _muestra(raiz, plataforma: str, esquema: dict, canal_id: str, ahora: datetime) -> tuple[dict, list[str]]:
    """Arma (sin enviar) una carga de ejemplo con la ficha propia y la valida contra el esquema."""
    from .fuentes import cargar_fuentes
    ficha = next((f for f in cargar_fuentes(raiz) if f.get("id") == "propio-tipografico"),
                 {"id": "muestra", "creador": "@guionvivo", "reglas": {}})
    toma = {"numero": 1, "gancho": "Prueba de ensayo", "pilar": "momentos", "creador": "@guionvivo",
            "valor": ["Una frase de valor de ejemplo."], "etiquetas": ["#GTA6", "#Gaming", "#Clips"]}
    tx = textos(toma, ficha, plataforma)
    cuando = ahora + timedelta(days=1)
    return armar_carga(plataforma, canal_id, tx["texto"], tx["titulo"],
                       "https://ejemplo.invalid/prueba.mp4", cuando, esquema)


def ensayo(raiz, env, salida=print, transporte_buffer=None, transporte_almacen=None, ahora=None) -> int:
    """Ensayo: organización, canales, confirmar_canales, introspección, armar_carga e ida y vuelta del
    almacén. Nunca hace createPost. Sin claves, lo mismo con respuestas simuladas."""
    from .almacen import Almacen, ErrorAlmacen
    from .simulacion import BufferSimulado, SupabaseSimulado
    ahora = ahora or (lambda: datetime.now(timezone.utc))
    faltan_b = [s for s in SECRETOS_BUFFER if not env.get(s)]
    faltan_a = [s for s in SECRETOS_ALMACEN if not env.get(s)]
    for s in faltan_b + faltan_a:
        salida(f"sin {s}")
    sim_b, sim_a = bool(faltan_b), bool(faltan_a)
    if sim_b:
        salida("Buffer: ensayo simulado con respuestas inventadas, pendiente de Kevin (alta de la clave)")
    if sim_a:
        salida("Almacén: ensayo simulado en memoria, pendiente de Kevin (alta de Supabase)")
    codigo = COD_OK
    cfg_canales = cargar_yaml("config/canales.yml", raiz)
    try:
        # Simulado: la cuota de mentira va a una carpeta temporal para no mezclarse con la real.
        raiz_cuota = tempfile.mkdtemp(prefix="cuota-simulada-") if sim_b else raiz
        cliente = ClienteBuffer("simulada" if sim_b else env["BUFFER_API_KEY"],
                                BufferSimulado() if sim_b else transporte_buffer, raiz_cuota, ahora=ahora)
        canales_api = cliente.todos_los_canales()
        salida(f"Organizaciones y canales leídos: {len(canales_api)} canal(es) en Buffer")
        ids = confirmar_canales(canales_api, cfg_canales, raiz, escribir=not sim_b)
        for plat in PLATAFORMAS:
            c = next(c for c in canales_api if c["id"] == ids[plat])
            salida(f"Canal confirmado: {plat} -> {c['service']} · {c.get('displayName') or c.get('name')}")
        if not sim_b:
            guardar_ids_de_canales(raiz, ids)
        esquema = cliente.introspeccion()
        salida(f"Introspección: {len(esquema['tipos'])} tipo(s) de entrada leídos")
        for plat in PLATAFORMAS:
            try:
                _, anot = _muestra(raiz, plat, esquema, ids[plat], ahora())
                salida(f"Carga {plat}: válida contra el esquema" + (f" ({'; '.join(anot)})" if anot else ""))
            except Descartada as e:
                salida(f"Carga {plat}: FALTA o SOBRA algo: {e}")
                codigo = COD_ERROR if not sim_b else codigo
    except AlertaRoja as e:
        salida(f"ALERTA ROJA: {e}")
        salida("Se creó PAUSA y estado/alerta-roja.json; no se programó nada." if not sim_b
               else "(simulado: no se escribió PAUSA)")
        return COD_ALERTA
    except (ErrorBuffer, red.ErrorRed) as e:
        salida(f"ERROR de Buffer: {e}")
        return COD_ERROR
    try:
        if sim_a:
            almacen = Almacen("https://simulado.invalid", "simulada",
                              cargar_yaml("config/almacen.yml", raiz), SupabaseSimulado())
        else:
            almacen = Almacen(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"],
                              cargar_yaml("config/almacen.yml", raiz), transporte_almacen)
        r = almacen.ida_y_vuelta()
        salida(f"Almacén ida y vuelta: subida {'sí' if r['subida'] else 'no'}, URL pública "
               f"comprobada {'sí' if r['url_comprobada'] else 'no'}, borrado {'sí' if r['borrado'] else 'no'}")
    except ErrorAlmacen as e:
        salida(f"ERROR del almacén: {e}")
        codigo = COD_ERROR
    if sim_b:
        salida("Cuota usada (Buffer): 0 llamadas reales (simulado)")
    else:
        salida("Cuota usada (Buffer): " + ", ".join(f"{k} {v}" for k, v in cliente.cuota.resumen().items()))
    salida("Ensayo terminado: no se hizo ningún createPost.")
    return codigo


def borrador(raiz, env, salida=print, transporte_buffer=None, ahora=None) -> int:
    """--borrador: createPost con saveToDraft y deletePost enseguida. Exige la clave real."""
    if not env.get("BUFFER_API_KEY"):
        salida("sin BUFFER_API_KEY: --borrador necesita la clave real y no se puede simular")
        return COD_ERROR
    ahora = ahora or (lambda: datetime.now(timezone.utc))
    try:
        cliente = ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=ahora)
        canales_api = cliente.todos_los_canales()
        ids = confirmar_canales(canales_api, cargar_yaml("config/canales.yml", raiz), raiz)
        pub = Publicador(cliente, None, raiz, "borrador", ahora, ids)
        for plat in PLATAFORMAS:
            r = pub.borrador(plat, "https://ejemplo.invalid/prueba.mp4", ahora() + timedelta(days=1))
            salida(f"Borrador {plat}: creado y borrado ({r['id']})")
    except AlertaRoja as e:
        salida(f"ALERTA ROJA: {e}")
        return COD_ALERTA
    except (ErrorBuffer, Descartada, red.ErrorRed) as e:
        salida(f"ERROR: {e}")
        return COD_ERROR
    return COD_OK


def vivo(raiz, env, entrada: dict, salida=print, transporte_buffer=None, transporte_almacen=None,
         ahora=None) -> int:
    """Modo vivo: sube el video, comprueba la URL y programa con hora explícita en cada plataforma activa.

    `entrada`: {mp4, toma:{numero, gancho, pilar, ...}, fuente_id, fecha (AAAA-MM-DD), hueco (HH:MM)}.
    """
    from .almacen import Almacen, ErrorAlmacen
    from .fuentes import cargar_fuentes, es_publicable
    ahora = ahora or (lambda: datetime.now(timezone.utc))
    activas = list(cargar_yaml("config/modo.yml", raiz).get("plataformas_activas") or [])
    if not activas:
        salida("modo vivo sin plataformas activas: nada que publicar")
        return COD_OK
    faltan = [s for s in SECRETOS_BUFFER + SECRETOS_ALMACEN if not env.get(s)]
    if faltan:
        salida("sin " + ", ".join(faltan) + ": no se publica")
        return COD_ERROR
    ficha = next((f for f in cargar_fuentes(raiz) if f.get("id") == entrada.get("fuente_id")), None)
    if ficha is None:
        salida(f"fuente {entrada.get('fuente_id')!r} fuera de la lista blanca: se descarta")
        return COD_OK
    toma = dict(entrada["toma"])
    toma.setdefault("fuente_id", ficha["id"])
    hoy = ahora().astimezone(ZONA).date()
    destinos = []
    for plat in activas:
        ok, motivo = es_publicable(ficha, hoy, plat, toma.get("pilar"))
        if ok:
            destinos.append(plat)
        else:
            salida(f"{plat}: descartada, la ficha no es publicable ({motivo})")
    if not destinos:
        return COD_OK
    try:
        cliente = ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=ahora)
        ids = confirmar_canales(cliente.todos_los_canales(), cargar_yaml("config/canales.yml", raiz),
                                raiz, plataformas=destinos)
        almacen = Almacen(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"],
                          cargar_yaml("config/almacen.yml", raiz), transporte_almacen)
        pub = Publicador(cliente, almacen, raiz, "vivo", ahora, ids)
        almacen.asegurar_bucket()
        fecha = entrada.get("fecha") or hoy.isoformat()
        try:
            ruta, url = almacen.subir(entrada["mp4"], toma["numero"], fecha)
            almacen.comprobar(url)
        except ErrorAlmacen as e:
            salida(f"subida fallida: se salta el hueco ({e})")
            return COD_OK
        cuando = hueco_a_utc(date.fromisoformat(fecha), entrada["hueco"],
                             cargar_yaml("config/horarios.yml", raiz)["zona"])
        for plat in destinos:
            try:
                tx = textos(toma, ficha, plat, raiz, hoy)
                r = pub.programar(plat, toma, url, tx, cuando, ruta, ficha)
                salida(f"{plat}: programada {r['id']} para {r['dueAt']}")
            except Descartada as e:
                salida(f"{plat}: descartada ({e})")
    except AlertaRoja as e:
        salida(f"ALERTA ROJA: {e}")
        return COD_ALERTA
    except (ErrorBuffer, ErrorAlmacen, red.ErrorRed) as e:
        salida(f"ERROR: {e}")
        return COD_ERROR
    return COD_OK


def ejecutar(raiz=None, ensayo_forzado=False, con_borrador=False, entrada: str | None = None,
             env=None, salida=print, **transportes) -> int:
    """Orden de seguridad: 1) PAUSA, 2) modo (ensayo, --borrador o vivo)."""
    raiz = raiz_repo(raiz)
    env = os.environ if env is None else env
    pausada, motivo = esta_pausada(raiz)
    if pausada:
        salida(f"PAUSA: {motivo or 'sin motivo'}. No se hace ninguna llamada.")
        return COD_OK
    if con_borrador:
        return borrador(raiz, env, salida, transportes.get("transporte_buffer"), transportes.get("ahora"))
    modo = cargar_yaml("config/modo.yml", raiz).get("modo")
    if ensayo_forzado or modo == "ensayo":
        return ensayo(raiz, env, salida, **transportes)
    if modo != "vivo":
        salida(f"modo desconocido: {modo!r}")
        return COD_ERROR
    if not entrada:
        salida("modo vivo: falta --entrada con el video y la toma (JSON)")
        return COD_ERROR
    datos = json.loads(Path(entrada).read_text(encoding="utf-8"))
    return vivo(raiz, env, datos, salida, **transportes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.publicar",
                                 description="Publica en Buffer (o ensaya sin publicar).")
    ap.add_argument("--ensayo", action="store_true", help="fuerza el ensayo (nunca hace createPost)")
    ap.add_argument("--borrador", action="store_true",
                    help="createPost con saveToDraft y deletePost enseguida (explícito, nunca por defecto)")
    ap.add_argument("--entrada", help="JSON con el video y la toma (modo vivo)")
    ap.add_argument("--raiz", default=None)
    a = ap.parse_args(argv)
    if a.ensayo and a.borrador:
        ap.error("--ensayo y --borrador no se combinan")
    return ejecutar(a.raiz, a.ensayo, a.borrador, a.entrada)


if __name__ == "__main__":
    sys.exit(main())

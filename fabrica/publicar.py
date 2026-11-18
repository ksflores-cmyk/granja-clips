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
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import envios_campana, red
from .candado import candado
from .config import cargar_yaml, raiz_repo
from .fuentes import etiqueta_creador
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
           "__typename ... on PostActionSuccess { post { id status dueAt } } "
           "... on MutationError { message } } }")
M_BORRAR = ("mutation($input: DeletePostInput!) { deletePost(input: $input) { "
            "__typename ... on MutationError { message } } }")
Q_TIPO = ("query($n: String!) { __type(name: $n) { name inputFields { name defaultValue "
          "type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } } } }")
Q_CAMPOS_POST = 'query { __type(name: "Post") { fields { name } } }'
Q_CONSULTAS = "query { __type(name: \"Query\") { fields { name args { name } } } }"


class ErrorBuffer(Exception):
    """Fallo de la API de Buffer (red, código o `errors` de GraphQL)."""


class ErrorIncierto(ErrorBuffer):
    """Una mutación pudo haberse aplicado (5xx, tiempo agotado o respuesta ilegible): no se repite."""


class SinConfirmar(ErrorBuffer):
    """Falta el id de canal u organización guardado en config/canales.yml: hace falta un ensayo con claves."""


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

    def consulta(self, query: str, variables: dict | None = None, canal: str | None = None,
                 mutacion: bool = False) -> dict:
        """Una llamada GraphQL. Devuelve `data`; los `errors` lanzan ErrorBuffer.

        Una mutación (createPost, deletePost) NUNCA se reintenta: repetirla podría duplicar la publicación.
        Si el resultado es incierto (5xx, tiempo agotado, respuesta ilegible) lanza ErrorIncierto."""
        cuerpo = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        cab = {"Authorization": f"Bearer {self._clave}", "Content-Type": "application/json",
               "Accept": "application/json"}

        def contar():
            self.cuota.comprobar(canal)
            self.cuota.registrar(canal)

        try:
            estado, _, datos = red.solicitar(self.transporte, "POST", ENDPOINT, cab, cuerpo,
                                             dormir=self.dormir, alerta=contar, reintentar=not mutacion)
        except red.ErrorRed as e:
            if mutacion:
                raise ErrorIncierto(f"{e}; el resultado es incierto, no se repite") from None
            raise ErrorBuffer(str(e)) from None
        if estado != 200:
            msg = f"POST {red.ruta_de(ENDPOINT)}: código {estado}"
            if mutacion and estado >= 500:
                raise ErrorIncierto(msg + "; el resultado es incierto, no se repite")
            raise ErrorBuffer(msg)
        try:
            r = json.loads(datos.decode("utf-8"))
        except ValueError:
            msg = f"POST {red.ruta_de(ENDPOINT)}: la respuesta no es JSON"
            raise (ErrorIncierto(msg + "; el resultado es incierto, no se repite") if mutacion
                   else ErrorBuffer(msg)) from None
        if not isinstance(r, dict):
            raise ErrorBuffer(f"POST {red.ruta_de(ENDPOINT)}: respuesta inesperada")
        if r.get("errors"):
            msgs = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e)
                             for e in r["errors"])
            msg = "GraphQL: " + red.ocultar(msgs, [self._clave])[:400]
            if mutacion:
                raise ErrorIncierto(msg + "; el resultado de la mutación es incierto, no se repite")
            raise ErrorBuffer(msg)
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
    """Sin @, sin mayúsculas, sin espacios ni signos: "Guion Vivo" y "guionvivo" comparan igual."""
    return re.sub(r"[^a-z0-9]", "", str(nombre or "").lower())


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
                      escribir: bool = True, exigir_guardado: bool = False) -> dict:
    """Para cada plataforma debe haber exactamente un canal con su `service` y su `name` esperado.

    `name` se compara sin @ y sin distinguir mayúsculas; `displayName` es sólo informativo y nunca basta.
    Devuelve {plataforma: id}. Si algo no coincide, o la cola está pausada, es alerta roja: crea PAUSA
    (si `escribir`), deja `estado/alerta-roja.json` para N6 y lanza AlertaRoja. No programa nada.

    Si config/canales.yml ya guarda `buffer_channel_id` o `buffer_org_id`, deben ser iguales a los de la API
    (si no, alerta roja). Con `exigir_guardado` (modo vivo), que falte alguno lanza SinConfirmar: primero
    hace falta un ensayo con claves que los confirme.
    """
    plataformas = list(plataformas if plataformas is not None else PLATAFORMAS)
    ids = {}
    for plat in plataformas:
        ficha = canales_cfg[plat]
        esperado, servicio = _norm(ficha["nombre_esperado"]), str(ficha["servicio"]).lower()
        coinciden = [c for c in canales_api
                     if str(c.get("service", "")).lower() == servicio and _norm(c.get("name")) == esperado]
        if len(coinciden) != 1:
            _alerta(raiz, f"canal de {plat} no confirmado: se esperaba exactamente un canal "
                          f"'{ficha['nombre_esperado']}' de {servicio} y hay {len(coinciden)}", escribir)
        canal = coinciden[0]
        if canal.get("isQueuePaused"):
            _alerta(raiz, f"la cola del canal de {plat} ('{ficha['nombre_esperado']}') está pausada "
                          "en Buffer", escribir)
        guardado_canal, guardado_org = ficha.get("buffer_channel_id"), ficha.get("buffer_org_id")
        if exigir_guardado and not (guardado_canal and guardado_org):
            raise SinConfirmar(f"{plat}: falta buffer_channel_id o buffer_org_id en config/canales.yml; "
                               "primero hace falta un ensayo con claves que los confirme")
        if guardado_canal and guardado_canal != canal["id"]:
            _alerta(raiz, f"el canal de {plat} que devuelve Buffer no es el confirmado en config/canales.yml",
                    escribir)
        if guardado_org and canal.get("_org") is not None and guardado_org != canal["_org"]:
            _alerta(raiz, f"el canal de {plat} está en otra organización que la confirmada en "
                          "config/canales.yml", escribir)
        ids[plat] = canal["id"]
    return ids


def organizaciones_de(canales_api: list[dict], ids: dict) -> dict:
    """{plataforma: id de la organización} de los canales confirmados."""
    por_id = {c["id"]: c.get("_org") for c in canales_api}
    return {plat: por_id.get(i) for plat, i in ids.items()}


def guardar_ids_de_canales(raiz, ids: dict, orgs: dict | None = None) -> None:
    """Escribe buffer_channel_id y buffer_org_id en config/canales.yml cambiando sólo esos valores."""
    ruta = raiz_repo(raiz) / "config" / "canales.yml"
    lineas = ruta.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, linea in enumerate(lineas):
        m = re.match(r"^(\w+):", linea)
        if m and m.group(1) in ids:
            lineas[i] = re.sub(r"buffer_channel_id:\s*[^,}\s]*", f"buffer_channel_id: {ids[m.group(1)]}", linea)
            if orgs and orgs.get(m.group(1)):
                lineas[i] = re.sub(r"buffer_org_id:\s*[^,}\s]*", f"buffer_org_id: {orgs[m.group(1)]}", lineas[i])
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


def es_de_campana(ficha: dict) -> bool:
    """Ficha de campaña con brief: tiene `campana` y su material trae el creador y sus etiquetas por red."""
    return bool(ficha.get("campana")) and any(isinstance(m, dict) for m in ficha.get("material") or [])


MAX_HASHTAGS_EXTRA = 3
_HASHTAG = re.compile(r"#\S*")


def _sin_hashtags(t: str) -> str:
    """Quita los hashtags incrustados (el único `#` antes de la divulgación sería un error de brief)."""
    return " ".join(_HASHTAG.sub("", t).split())


def _textos_campana(toma: dict, ficha: dict, plataforma: str, raiz=None, hoy: date | None = None) -> dict:
    """Texto de una campaña con brief (SEGA FM27). Orden fijo:

        Toma nnn. <gancho en español>
        <blanco>
        @footballmanager            (etiquetas_obligatorias, tal cual)
        <etiqueta del creador>      (la de su red; sustituye al crédito propio 🎥)
        #Publicidad                 (reglas.divulgacion: su propia línea, justo después del texto, primer hashtag)
        #uno #dos #tres             (hasta 3 hashtags relacionados: los de la toma y luego reglas.hashtags)
    """
    reglas = ficha.get("reglas") or {}
    creador = _limpio(toma.get("creador"))
    etiqueta = etiqueta_creador(ficha, creador, plataforma)
    if not etiqueta:
        raise Descartada(f"sin etiqueta del creador {creador or '(vacío)'} para {plataforma}: no se publica")
    gancho = _sin_hashtags(_limpio(toma.get("gancho")))
    if not gancho:
        raise Descartada("sin gancho")
    obligatorias = [str(e).strip() for e in reglas.get("etiquetas_obligatorias") or [] if str(e).strip()]
    divulgacion = str(reglas.get("divulgacion") or "").strip()
    if not divulgacion.startswith("#"):
        raise Descartada("la campaña exige una divulgación en hashtag (reglas.divulgacion)")
    vistos = {divulgacion.lower()}
    extras = []
    for e in [str(x).strip() for x in (toma.get("etiquetas") or []) + (reglas.get("hashtags") or [])]:
        if e.startswith("#") and e.lower() not in vistos and len(extras) < MAX_HASHTAGS_EXTRA:
            vistos.add(e.lower())
            extras.append(e)
    num = f"{int(toma['numero']):03d}" if str(toma.get("numero", "")).isdigit() else str(toma.get("numero", ""))
    primera = f"Toma {num}. {gancho}"
    bloques = [primera]
    cola = [*obligatorias, etiqueta, divulgacion] + ([" ".join(extras)] if extras else [])
    texto = "\n\n".join(bloques) + "\n\n" + "\n".join(cola)
    if "#" in texto.split("\n" + divulgacion, 1)[0]:
        raise Descartada("hay un # antes de la divulgación: #Publicidad debe ser el primer hashtag")
    if contar_emojis(texto) > 1:
        raise Descartada("el texto lleva más de un emoji")
    if len(texto) > MAX_TEXTO:
        raise Descartada(f"el texto pasa de {MAX_TEXTO} caracteres")
    titulo = _titulo_youtube(primera) if plataforma == "shorts" else None
    if raiz is not None:
        _comprobar_repetido(raiz, texto, plataforma, toma, hoy)
    return {"texto": texto, "titulo": titulo, "credito": etiqueta,
            "etiquetas": [*obligatorias, divulgacion, *extras]}


def textos(toma: dict, ficha: dict, plataforma: str, raiz=None, hoy: date | None = None) -> dict:
    """Texto de la publicación según el playbook (sección 10).

    `toma`: numero, gancho, pilar, creador (opcional), valor (frases, Instagram), etiquetas (del brief),
    titular y datos (informativa). Devuelve {texto, titulo, credito, etiquetas}; Descartada si no cumple.
    """
    if plataforma not in SERVICIOS:
        raise Descartada(f"plataforma desconocida: {plataforma}")
    reglas = ficha.get("reglas") or {}
    if es_de_campana(ficha):
        return _textos_campana(toma, ficha, plataforma, raiz, hoy)
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

class ResultadoIncierto(Exception):
    """El createPost pudo haberse aplicado: no se repitió. El envío queda `incierto` hasta reconciliarlo."""


RE_EXITO_BORRADO = re.compile(r"^\w*Success$")
ESTADOS_ABIERTOS = ("enviando", "incierto")
PAGINAS_MAX, TAM_PAGINA = 5, 50
HORAS_ABANDONO = 24


class Publicador:
    """`modo`: ensayo (nunca createPost), borrador (createPost con saveToDraft y deletePost) o vivo.

    Libro de envíos `estado/buffer-envios.json`: antes de cada createPost se anota la intención
    (`enviando`); luego queda `creado`, `rechazado`, `incierto` (5xx, tiempo agotado o respuesta ilegible)
    o `no_creado` (la reconciliación no lo encontró en Buffer). Un incierto NUNCA se repite a ciegas:
    `reconciliar()` consulta los posts del canal (por `dueAt` y texto) antes de volver a intentar.

    `limpiar_enviados()` es pública: N6 la llama en cada ciclo para borrar del almacén los videos cuyos
    posts ya están en `sent`. `estado/alerta-roja.json` (alerta de canal) lo consume N6 para abrir el issue.
    """

    def __init__(self, cliente: ClienteBuffer, almacen=None, raiz=None, modo: str = "ensayo",
                 ahora=None, canales: dict | None = None, esquema: dict | None = None,
                 orgs: dict | None = None):
        if modo not in ("ensayo", "borrador", "vivo"):
            raise ValueError(f"modo desconocido: {modo}")
        self.cliente, self.almacen, self.raiz, self.modo = cliente, almacen, raiz, modo
        self.ahora = ahora or (lambda: datetime.now(timezone.utc))
        self.canales = canales or {}
        self.orgs = orgs or {}
        self._esquema = esquema
        self._prof = 0

    @property
    def esquema(self) -> dict:
        if self._esquema is None:
            self._esquema = self.cliente.introspeccion()
        return self._esquema

    # ---- libro de envíos (con candado entre procesos) ----

    def _ruta_libro(self) -> Path:
        return _ruta_estado(self.raiz, "buffer-envios.json")

    @contextmanager
    def _candado(self):
        """Candado del libro: lectura, comprobación, reserva y toda escritura. Reentrante en el proceso."""
        if self._prof:
            self._prof += 1
            try:
                yield
            finally:
                self._prof -= 1
            return
        with candado(self._ruta_libro()):
            self._prof = 1
            try:
                yield
            finally:
                self._prof = 0

    def _libro(self) -> list:
        libro = _leer_json(self._ruta_libro(), [])
        return [_normalizar_envio(e) for e in libro if isinstance(e, dict)]

    def _guardar_libro(self, libro: list) -> None:
        with self._candado():
            _escribir_json(self._ruta_libro(), libro)

    def _marcar(self, id_envio: str, **cambios) -> None:
        with self._candado():
            libro = self._libro()
            for e in libro:
                if e["id"] == id_envio:
                    e.update(cambios)
            self._guardar_libro(libro)

    # ---- createPost ----

    def _crear(self, carga: dict, canal_id: str) -> dict:
        """createPost sin reintento y con resultado estricto.

        Éxito: `__typename == "PostActionSuccess"` con `post.id` no vacío. Rechazo (Descartada): sólo
        `__typename == "MutationError"` con mensaje. Cualquier otra cosa en un 200 (null, sin `__typename`,
        sin post.id, tipo inesperado, `errors`) es ErrorIncierto: no se sabe si Buffer lo creó.
        """
        if self.modo == "ensayo":
            raise ErrorBuffer("createPost no está permitido en ensayo")
        if self.modo == "borrador" and carga.get("saveToDraft") is not True:
            raise ErrorBuffer("en borrador createPost sólo se manda con saveToDraft")
        data = self.cliente.consulta(M_CREAR, {"input": carga}, canal=canal_id, mutacion=True)
        r = data.get("createPost")
        incierto = ErrorIncierto("createPost devolvió una respuesta sin discriminador o inesperada; "
                                 "el resultado es incierto, no se repite")
        if not isinstance(r, dict):
            raise incierto
        tipo, post, mensaje = r.get("__typename"), r.get("post"), r.get("message")
        if tipo == "PostActionSuccess" and isinstance(post, dict) \
                and isinstance(post.get("id"), str) and post["id"].strip():
            return post
        if tipo == "MutationError" and isinstance(mensaje, str) and mensaje.strip():
            raise Descartada("Buffer rechazó la publicación: " + red.ocultar(mensaje, [self.cliente._clave]))
        raise incierto

    def programar(self, plataforma: str, toma: dict, url_video: str, textos: dict, cuando: datetime,
                  ruta_almacen: str | None = None, ficha: dict | None = None) -> dict:
        """createPost con hora explícita (customScheduled, nunca addToQueue). Devuelve {id, dueAt}.

        Lanza Descartada (rechazo explícito o envío ya existente) o ResultadoIncierto. Un envío incierto
        NUNCA se vuelve a enviar: ese hueco se pierde; un hueco perdido es mejor que un duplicado.
        """
        if self.modo != "vivo":
            raise ErrorBuffer("programar sólo se usa en modo vivo")
        canal = self.canales.get(plataforma)
        if not canal:
            raise Descartada(f"canal de {plataforma} sin confirmar")
        validar_futuro(cuando, self.ahora())
        carga, anot = armar_carga(plataforma, canal, textos["texto"], textos.get("titulo"),
                                  url_video, cuando, self.esquema)
        self.reconciliar()
        due = iso_utc(cuando)
        id_envio = f"{plataforma}|{toma.get('numero')}|{due}"
        entrada = {"id": id_envio, "plataforma": plataforma, "toma": toma.get("numero"), "dueAt": due,
                   "canal": canal, "ruta": ruta_almacen, "estado": "enviando", "post_id": None,
                   "intento": uuid.uuid4().hex,
                   "registrado": False, "texto": textos["texto"],
                   "datos": {"toma": toma, "textos": textos, "ficha_id": (ficha or {}).get("id")}}
        with self._candado():                       # lectura, comprobación y reserva, de una vez
            libro = self._libro()
            previo = next((e for e in libro if e["estado"] != "rechazado" and e["plataforma"] == plataforma
                           and (str(e.get("toma")) == str(toma.get("numero")) or e.get("dueAt") == due)), None)
            if previo:
                raise Descartada(f"ya hay un envío {previo['estado']} para {plataforma} (toma "
                                 f"{previo.get('toma')}, {previo.get('dueAt')}): no se repite")
            libro = [e for e in libro if e["id"] != id_envio]   # sólo quedaba un `rechazado`
            libro.append(entrada)
            self._guardar_libro(libro)              # si falla, no se envía nada
        try:
            post = self._crear(carga, canal)
        except ErrorIncierto as e:
            self._intentar_transicion(entrada, "incierto")
            raise ResultadoIncierto(str(e)) from None
        except (Descartada, ErrorBuffer):
            self._intentar_transicion(entrada, "rechazado")
            raise
        try:
            self._cerrar_creado(entrada, post)
        except Exception:
            # Falló el libro o el marcador tras un createPost correcto: no se pierde ni se repite. El envío
            # queda `enviando` o `creado` sin registrar y la próxima reconciliación lo termina.
            pass
        return {"id": post["id"], "dueAt": post.get("dueAt"), "anotaciones": anot}

    def _intentar_transicion(self, entrada: dict, a: str) -> None:
        try:
            self._transicionar(entrada, a)
        except OSError:
            pass

    def _transicionar(self, entrada: dict, a: str, **cambios) -> bool:
        """Bajo candado: relee el envío y sólo cambia su estado si sigue abierto y es el mismo intento.

        Así una transición vieja nunca pisa un `creado` (ni otro intento con el mismo id).
        """
        with self._candado():
            libro = self._libro()
            for e in libro:
                if e["id"] == entrada["id"] and e.get("intento") == entrada.get("intento") \
                        and e["estado"] in ESTADOS_ABIERTOS:
                    e.update(estado=a, **cambios)
                    self._guardar_libro(libro)
                    return True
        return False

    def _cerrar_creado(self, entrada: dict, post: dict) -> None:
        """Pasa el envío a `creado` y escribe el marcador y el registro de textos, una sola vez."""
        with self._candado():
            actual = next((e for e in self._libro() if e["id"] == entrada["id"]
                           and e.get("intento") == entrada.get("intento")), None)
            if actual is None or actual["estado"] not in ESTADOS_ABIERTOS + ("creado",) \
                    or (actual["estado"] == "creado" and actual.get("registrado")):
                return
            self._marcar(entrada["id"], estado="creado", post_id=post["id"])
            d = entrada["datos"]
            self._registrar(entrada["plataforma"], d["toma"], d["textos"], _a_utc(entrada["dueAt"]), post,
                            entrada.get("ruta"), {"id": d.get("ficha_id")})
            self._marcar(entrada["id"], registrado=True)

    def _registrar(self, plataforma, toma, textos, cuando, post, ruta_almacen, ficha) -> None:
        local = cuando.astimezone(ZONA)
        num = f"{int(toma['numero']):03d}" if str(toma.get("numero", "")).isdigit() else str(toma.get("numero"))
        fuente_id = toma.get("fuente_id") or (ficha or {}).get("id") or ""
        if toma.get("campana"):          # primero: es idempotente por post_id y no depende del marcador
            envios_campana.anotar(self.raiz, plataforma, post["id"], cuando, toma)
        _anotar(self.raiz, {
            "id_clip": f"toma-{num}-{plataforma}", "toma": toma.get("numero"),
            "fecha": local.date().isoformat(), "hora": local.strftime("%H:%M"),
            "plataforma": plataforma, "pilar": toma.get("pilar", ""), "fuente_id": fuente_id,
            "campana": toma.get("campana") or "",
            "gancho": toma.get("gancho", ""), "duracion_s": toma.get("duracion_s", ""),
            "credito": textos.get("credito", ""), "url": "", "estado_campana": "",
            "notas": f"origen:{fuente_id} buffer:{post['id']}"})
        previos = _leer_json(_ruta_estado(self.raiz, "buffer-textos.json"), [])
        previos.append({"fecha": self.ahora().astimezone(ZONA).date().isoformat(),
                        "plataforma": plataforma,
                        "huella": hashlib.sha256(_cuerpo_sin_toma(textos["texto"]).encode()).hexdigest()})
        _escribir_json(_ruta_estado(self.raiz, "buffer-textos.json"), previos[-300:])

    # ---- reconciliación ----

    def _campos_post(self) -> set:
        try:
            t = self.cliente.consulta(Q_CAMPOS_POST).get("__type") or {}
        except ErrorBuffer:
            t = {}
        campos = {f["name"] for f in t.get("fields") or []}
        return campos or {"id", "status", "dueAt"}

    def _posts_del_canal(self, canal: str, org: str, campos: set, buscados=None):
        """Posts del canal, paginados (`first`/`after`, hasta PAGINAS_MAX). Devuelve (posts, concluyente).

        Concluyente sólo si se leyó hasta una última página con `pageInfo.hasNextPage == false` válido.
        Una respuesta sin `posts`, sin `edges` o sin `pageInfo`, o con más páginas de las permitidas, no lo es.
        `buscados` (lista de funciones post -> bool): si todos aparecen se deja de leer (sin ser concluyente
        para nada más: cada envío se resuelve por separado).
        """
        sel = " ".join(c for c in ("id", "status", "dueAt", "text") if c in campos)
        posts, cursor = [], None
        for _ in range(PAGINAS_MAX):
            despues = f", after: {_lit(cursor)}" if cursor else ""
            q = ("query { posts(input: { organizationId: %s, filter: { channelIds: [%s] } }, first: %d%s) "
                 "{ edges { node { %s } } pageInfo { hasNextPage endCursor } } }"
                 % (_lit(org), _lit(canal), TAM_PAGINA, despues, sel))
            data = self.cliente.consulta(q, canal=canal).get("posts")
            if not (isinstance(data, dict) and isinstance(data.get("edges"), list)):
                return posts, False
            posts += [e["node"] for e in data["edges"] if isinstance(e, dict) and isinstance(e.get("node"), dict)]
            if buscados and all(any(b(p) for p in posts) for b in buscados):
                return posts, False
            info = data.get("pageInfo")
            if not (isinstance(info, dict) and isinstance(info.get("hasNextPage"), bool)):
                return posts, False          # sin pageInfo válido: lo visto vale, la ausencia no se concluye
            if not info["hasNextPage"]:
                return posts, True
            cursor = info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                return posts, False
        return posts, False

    def reconciliar(self) -> dict:
        """Resuelve los envíos `enviando` o `incierto` mirando los posts del canal (dueAt y texto).

        Sólo hay dos salidas: el listado muestra el post y el envío pasa a `creado` (se registra en el
        marcador), o no aparece y el envío SIGUE `incierto` hasta 24 h después de su `dueAt`, cuando queda
        `abandonado`. Nunca se vuelve a enviar nada. La ausencia sólo es concluyente tras leer hasta la
        última página con `hasNextPage == false` válido; si no, no cambia nada. Cada envío se resuelve por
        separado: encontrar uno no vuelve concluyente la búsqueda de otro. También completa el marcador de los `creado`
        que quedaron sin registrar. Devuelve {id_envio: nuevo estado}.
        """
        resultado = {}
        for e in self._libro():
            if e["estado"] == "creado" and not e.get("registrado") and e.get("post_id") and e.get("datos"):
                try:
                    self._cerrar_creado(e, {"id": e["post_id"]})
                    resultado[e["id"]] = "creado"
                except Exception:
                    pass
        abiertos = [e for e in self._libro() if e["estado"] in ESTADOS_ABIERTOS and e.get("canal")]
        if not abiertos:
            return resultado
        try:
            campos = self._campos_post()
            por_canal: dict[str, tuple] = {}
            for e in abiertos:
                if e["canal"] not in por_canal:
                    org = next((o for p, o in self.orgs.items() if self.canales.get(p) == e["canal"] and o), None)
                    org = org or self.cliente.organizaciones()[0]["id"]
                    por_canal[e["canal"]] = self._posts_del_canal(
                        e["canal"], org, campos,
                        [lambda p, x=x: _mismo_post(p, x) for x in abiertos if x["canal"] == e["canal"]])
        except ErrorBuffer:
            return resultado                      # lectura no concluyente: no cambia nada
        for e in abiertos:
            posts, concluyente = por_canal[e["canal"]]
            hallado = next((p for p in posts if _mismo_post(p, e)), None)
            if hallado:
                try:
                    self._cerrar_creado(e, hallado)
                    resultado[e["id"]] = "creado"
                except Exception:
                    pass
            elif concluyente and self.ahora().astimezone(timezone.utc) > _a_utc(e["dueAt"]) + timedelta(hours=HORAS_ABANDONO):
                if self._transicionar(e, "abandonado"):
                    resultado[e["id"]] = "abandonado"
        return resultado

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

    def estado_y_url(self, post_id: str, org_id: str | None = None) -> tuple[str | None, str | None]:
        """(estado, URL publicada). La URL sale de `externalLink` sólo si la introspección muestra ese campo
        en `Post` y el post ya está `sent`; si Buffer no la da, es None (nunca se inventa)."""
        if not hasattr(self, "_campos_cache"):
            self._campos_cache = self._campos_post()
        sel = "id status dueAt" + (" externalLink" if "externalLink" in self._campos_cache else "")
        consultas = self.esquema.get("consultas", {})
        if "post" in consultas:
            if "input" in consultas["post"]:
                q = "query { post(input: { id: %s }) { %s } }" % (_lit(post_id), sel)
            else:
                q = "query { post(id: %s) { %s } }" % (_lit(post_id), sel)
            nodo = self.cliente.consulta(q).get("post") or {}
        else:
            org_id = org_id or self.cliente.organizaciones()[0]["id"]
            ids = ", ".join(_lit(c) for c in self.canales.values())
            q = ("query { posts(input: { organizationId: %s, filter: { status: [sent], channelIds: [%s] } }) "
                 "{ edges { node { %s } } } }" % (_lit(org_id), ids, sel))
            data = self.cliente.consulta(q)
            nodo = next((e["node"] for e in (data.get("posts") or {}).get("edges") or []
                         if e["node"]["id"] == post_id), {})
        url = nodo.get("externalLink")
        return nodo.get("status"), (url.strip() if isinstance(url, str) and url.strip().startswith("http") else None)

    def limpiar_enviados(self) -> list[str]:
        """Borra del almacén los videos cuyos posts ya están en `sent` (o que no tienen ningún envío vivo).

        Función pública: N6 la llama en cada ciclo. Un video se conserva mientras algún envío suyo esté
        `enviando`, `incierto` o `creado` sin `sent`; se libera con `sent` o cuando el envío queda
        `rechazado` o `abandonado`. Devuelve las rutas borradas.
        """
        self.reconciliar()
        borradas = []
        with self._candado():     # comprobación final, borrado y marca bajo el mismo candado que la reserva
            libro = self._libro()
            por_ruta: dict[str, list] = {}
            for e in libro:
                if e.get("ruta") and not e.get("ruta_borrada"):
                    por_ruta.setdefault(e["ruta"], []).append(e)
            for r, envios in por_ruta.items():
                if any(e["estado"] in ESTADOS_ABIERTOS for e in envios):
                    continue
                vivos = [e for e in envios if e["estado"] == "creado"]
                if not all(e.get("post_id") and self.estado(e["post_id"]) == "sent" for e in vivos):
                    continue
                if any(e["estado"] in ESTADOS_ABIERTOS and e.get("ruta") == r for e in self._libro()):
                    continue          # alguien reservó mientras se consultaba
                self.almacen.borrar(r)
                borradas.append(r)
                # Las entradas se quedan (memoria de lo enviado: impiden repetir un hueco); sólo se marca el video.
                actual = self._libro()
                for e in actual:
                    if e.get("ruta") == r:
                        e["ruta_borrada"] = True
                self._guardar_libro(actual)
        try:                      # el ciclo llama a esta función cada vez: aquí se completan los enlaces de campaña
            envios_campana.completar(self.raiz, self)
        except (ErrorBuffer, OSError, ValueError):
            pass
        return borradas

    def soltar_si_huerfano(self, ruta: str) -> bool:
        """Borra el video recién subido si ningún envío lo usa o todos se rechazaron de forma definitiva.
        Con un envío incierto o abierto, se conserva hasta reconciliarlo. Todo bajo el candado del libro."""
        with self._candado():
            envios = [e for e in self._libro() if e.get("ruta") == ruta]
            if any(e["estado"] != "rechazado" for e in envios):
                return False
            self.almacen.borrar(ruta)
            return True

    # ---- borrador ----

    def borrador(self, plataforma: str, url_video: str, cuando: datetime) -> dict:
        """createPost con saveToDraft sobre un canal confirmado y deletePost enseguida. No publica nada.

        Devuelve {id, borrado, motivo?}. `borrado` es True sólo si deletePost responde con un tipo de éxito
        explícito (`__typename` terminado en Success). Si no se confirma, `borrado: False` y el id se conserva.
        """
        if self.modo != "borrador":
            raise ErrorBuffer("borrador sólo se usa en modo borrador")
        canal = self.canales[plataforma]
        carga, anot = armar_carga(plataforma, canal, "Borrador de comprobación (se borra enseguida)",
                                  "Borrador de comprobación", url_video, cuando, self.esquema, borrador=True)
        post = self._crear(carga, canal)
        out = {"id": post["id"], "borrado": False, "anotaciones": anot}
        try:
            entrada = _validar_tipo("DeletePostInput", {"id": post["id"]}, self.esquema, anot)
            r = self.cliente.consulta(M_BORRAR, {"input": entrada}, canal=canal, mutacion=True)
        except (ErrorBuffer, Descartada) as e:
            out["motivo"] = f"no se pudo borrar: {e}"
            return out
        borrado = r.get("deletePost")
        tipo = borrado.get("__typename") if isinstance(borrado, dict) else None
        if isinstance(borrado, dict) and "message" not in borrado and isinstance(tipo, str) \
                and RE_EXITO_BORRADO.match(tipo) and tipo != "MutationError":
            out["borrado"] = True
        else:
            out["motivo"] = "deletePost no confirmó el borrado con un tipo de éxito"
        return out


def _normalizar_envio(e: dict) -> dict:
    """Entradas del formato anterior (sin `estado`): se leen como `creado` y ya registradas."""
    e = dict(e)
    if e.get("estado") == "no_creado":      # estado retirado: nunca da permiso para reintentar
        e["estado"] = "incierto"
    if "estado" not in e:
        e["estado"] = "creado"
        e.setdefault("registrado", True)
    e.setdefault("plataforma", "")
    e.setdefault("dueAt", None)
    e.setdefault("id", f"{e['plataforma']}|{e.get('toma')}|{e['dueAt']}")
    return e


def _a_utc(iso: str) -> datetime:
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc)


def _mismo_post(post: dict, envio: dict) -> bool:
    try:
        if _a_utc(post["dueAt"]) != _a_utc(envio["dueAt"]):
            return False
    except (KeyError, ValueError, TypeError):
        return False
    if "text" in post and post["text"] is not None:
        return " ".join(str(post["text"]).split()) == " ".join(envio["texto"].split())
    return True


def _escribir_json(ruta: Path, datos) -> None:
    """Escritura atómica (archivo temporal y os.replace)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(datos, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ruta)


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
            guardar_ids_de_canales(raiz, ids, organizaciones_de(canales_api, ids))
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
    except (ErrorAlmacen, red.ErrorRed) as e:
        salida(f"ERROR del almacén: {e}")
        codigo = COD_ERROR
    if sim_b:
        salida("Cuota usada (Buffer): 0 llamadas reales (simulado)")
    else:
        salida("Cuota usada (Buffer): " + ", ".join(f"{k} {v}" for k, v in cliente.cuota.resumen().items()))
    salida("Ensayo terminado: no se hizo ningún createPost.")
    return codigo


def borrador(raiz, env, salida=print, transporte_buffer=None, ahora=None) -> int:
    """--borrador: createPost con saveToDraft y deletePost enseguida. Exige la clave real.

    Si un borrador no se confirma como borrado, lo dice con su id y sale con 1 (hay que borrarlo a mano)."""
    if not env.get("BUFFER_API_KEY"):
        salida("sin BUFFER_API_KEY: --borrador necesita la clave real y no se puede simular")
        return COD_ERROR
    ahora = ahora or (lambda: datetime.now(timezone.utc))
    try:
        cliente = ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=ahora)
        canales_api = cliente.todos_los_canales()
        ids = confirmar_canales(canales_api, cargar_yaml("config/canales.yml", raiz), raiz)
        pub = Publicador(cliente, None, raiz, "borrador", ahora, ids, orgs=organizaciones_de(canales_api, ids))
        for plat in PLATAFORMAS:
            r = pub.borrador(plat, "https://ejemplo.invalid/prueba.mp4", ahora() + timedelta(days=1))
            if not r["borrado"]:
                salida(f"BORRADOR SIN BORRAR en {plat}: id {r['id']} ({r.get('motivo', 'sin confirmar')}); "
                       "bórralo a mano en Buffer")
                return COD_ERROR
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
    toma.setdefault("campana", ficha.get("campana") or "")
    ruta = None
    pub = None
    incierto = False
    try:
        cliente = ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=ahora)
        canales_api = cliente.todos_los_canales()
        # Sólo se programa en el canal cuyo id coincide con el guardado en config/canales.yml.
        ids = confirmar_canales(canales_api, cargar_yaml("config/canales.yml", raiz), raiz,
                                plataformas=destinos, exigir_guardado=True)
        almacen = Almacen(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"],
                          cargar_yaml("config/almacen.yml", raiz), transporte_almacen)
        pub = Publicador(cliente, almacen, raiz, "vivo", ahora, ids,
                         orgs=organizaciones_de(canales_api, ids))
        fecha = entrada.get("fecha") or hoy.isoformat()
        cuando = hueco_a_utc(date.fromisoformat(fecha), entrada["hueco"],
                             cargar_yaml("config/horarios.yml", raiz)["zona"])
        # Todo se valida ANTES de subir: hora, textos, canales y carga contra el esquema.
        listos = {}
        for plat in destinos:
            try:
                validar_futuro(cuando, ahora())
                tx = textos(toma, ficha, plat, raiz, hoy)
                armar_carga(plat, ids[plat], tx["texto"], tx["titulo"],
                            "https://ejemplo.invalid/previa.mp4", cuando, pub.esquema)
                listos[plat] = tx
            except Descartada as e:
                salida(f"{plat}: descartada antes de subir ({e})")
        if not listos:
            return COD_OK
        almacen.asegurar_bucket()
        try:
            ruta, url = almacen.subir(entrada["mp4"], toma["numero"], fecha)
            almacen.comprobar(url)
        except ErrorAlmacen as e:
            salida(f"subida fallida: se salta el hueco ({e})")
            return COD_OK
        for plat, tx in listos.items():
            try:
                r = pub.programar(plat, toma, url, tx, cuando, ruta, ficha)
                salida(f"{plat}: programada {r['id']} para {r['dueAt']}")
            except Descartada as e:
                salida(f"{plat}: descartada ({e})")
            except ResultadoIncierto as e:
                incierto = True
                salida(f"{plat}: resultado INCIERTO, no se repite; el video se conserva hasta reconciliar ({e})")
    except AlertaRoja as e:
        salida(f"ALERTA ROJA: {e}")
        return COD_ALERTA
    except (ErrorBuffer, ErrorAlmacen, red.ErrorRed) as e:
        salida(f"ERROR: {e}")
        return COD_ERROR
    finally:
        if ruta and pub is not None:
            try:
                if pub.soltar_si_huerfano(ruta):
                    salida("el video subido no tiene ningún envío vivo: se borró del almacén")
            except (ErrorAlmacen, ErrorBuffer, red.ErrorRed, OSError) as e:
                salida(f"AVISO: no se pudo borrar el video huérfano {ruta} ({e})")
    return COD_ERROR if incierto else COD_OK


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

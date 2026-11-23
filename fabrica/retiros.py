"""Reclamos y retiros: un post de Buffer en `error` con un mensaje de derechos veta solo al streamer.

Decisión de Kevin (19 nov 2026): ante un reclamo, retiro el mismo día, veto del streamer, alerta roja y se
sigue con los demás. Dos reclamos en 7 días pausan sólo la ficha T (estado/pausa-t.yml); un aviso de cuenta
suspendida lanza `publicar.AlertaRoja` (PAUSA global). Buffer no se entera de lo que la red borra después:
esos retiros entran a mano en retiros.md. Sin LLM, sin dependencias nuevas, sin claves en nada de lo escrito.

`python -m fabrica.retiros --revisar [--seco]`, `--diagnostico` (sólo lectura) o `--abrir-avisos`.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import plazo, publicar, red
from .config import cargar_yaml, raiz_repo
from .fuentes import RE_ID_T, cargar_fuentes, id_ficha_t
from .pausa import ZONA, crear_pausa_t, pausa_t_activa  # noqa: F401 (pausa_t_activa se re-exporta)
from .registro import anotar_registro
from .vetados import RE_CANAL, filas_retiros

PAGINAS_MAX, TAM_PAGINA = 3, 50
DIAS_VENTANA = 7
RECLAMOS_PARA_PAUSA = 2
SUBCAMPOS_ERROR = ("message", "rawError", "details", "code")
RUTA_VISTOS = "estado/retiros-vistos.json"
RUTA_ALERTAS = "estado/alertas-retiro.json"
RUTA_ESQUEMA = "estado/retiros-esquema.json"     # selección del campo `error` (introspección), 7 días
DIAS_ESQUEMA = 7
ETIQUETA = "alerta-roja"
# Plazo propio (revisión del 20 nov): la lectura nunca se come el ciclo. Cada llamada tiene su tope, la espera
# de un reintento (429/5xx) se recorta y, pasado el presupuesto, no se llama más (el ciclo sigue).
PRESUPUESTO_S = 60
TOPE_LLAMADA_S = 20
ESPERA_MAX_S = 5
# Con la cuota de Buffer a este nivel (de su tope) no se lee: la publicación, que se corta al 80 %, va primero.
UMBRAL_CUOTA_RETIROS = 0.6

# Sólo un mensaje claro de derechos o retiro veta (revisión del 20 nov): «removed», «violat» o «claim» sueltos
# (p. ej. «media removed from queue», «violates the size limit», «reclaim») no bastan.
RE_RECLAMO = re.compile(
    r"copyright|derechos de autor|content ?id\b|dmca|infring|infracci|takedown|\bclaim|reclamo|\bstrike|"
    r"\b(removed|retirad[oa]s?|eliminad[oa]s?)\s+(\w+\s+){0,2}?(by|for|due|because|por|debido)\b|"
    r"violat\w*\s+(\w+\s+){0,3}?(community|copyright|guidelines|terms|polic|rules|intellectual)|"
    r"community guidelines|normas de la comunidad|intellectual property|propiedad intelectual", re.IGNORECASE)
# Aviso de cuenta: «suspend», «terminated» o «banned» bastan; «restrict», «blocked» o «disabled» sólo si el
# mensaje habla de la cuenta o el canal («Request blocked» o «video blocked due to copyright» no son la cuenta).
RE_CUENTA = re.compile(r"suspend|terminated|banned|baneada|baneado", re.IGNORECASE)
RE_CUENTA_DEBIL = re.compile(r"restrict|bloquead|blocked|disabled|deshabilitad|inhabilitad", re.IGNORECASE)
RE_SUJETO_CUENTA = re.compile(r"account|cuenta|channel|canal|profile|perfil", re.IGNORECASE)
# Error pasajero de Buffer o de la red (cuota, 5xx, red): nunca veta ni pausa; si además suena a reclamo, se
# anota como dudoso y se avisa para revisar a mano.
RE_PASAJERO = re.compile(
    r"rate.?limit|too many requests|quota|cuota|l[ií]mite de (uso|peticiones|solicitudes|llamadas)|try again|"
    r"int[eé]ntalo de nuevo|intenta de nuevo|vuelve a intentar|\bretry|timed? ?out|tiempo de espera|"
    r"network error|error de red|connection (reset|refused|error|timed|closed)|internal (server )?error|"
    r"server error|error (interno|del servidor)|service unavailable|servicio no disponible|"
    r"temporarily unavailable|temporalmente no disponible|bad gateway|gateway time|\b(429|500|502|503|504)\b",
    re.IGNORECASE)
RE_URL = re.compile(r"https?://\S+")
RE_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+(\.[\w-]+)?")
RE_PAR_CLAVE = re.compile(r"(?i)\b(access[_ ]?token|refresh[_ ]?token|token|api[_ ]?key|apikey|key|secret|"
                          r"password|contraseña|signature|sig|bearer|authorization)\b(\s*[:=]\s*|\s+)[^\s,;]+")
RE_OPACO = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")
RE_PID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

Q_POST = ('query { __type(name: "Post") { fields { name type { name kind ofType { name kind '
          'ofType { name kind } } } } } }')
Q_SUBCAMPOS = "query { __type(name: %s) { fields { name } } }"


@dataclass
class Informe:
    """Lo que dejó (o dejaría, con `--seco`) una revisión."""
    leidos: int = 0
    posts: list[dict] = field(default_factory=list)   # post_id, plataforma, clase, login, fuente, motivo
    reclamos: int = 0
    dudosos: int = 0
    pausa_t: bool = False
    cuenta: str | None = None


def clasificar(mensaje: str) -> str:
    """`reclamo`, `cuenta`, `reclamo+cuenta`, `dudoso` u `otro`, por el mensaje de error (sin mayúsculas).

    Un error pasajero (cuota, 5xx, red, «try again») nunca es reclamo ni cuenta: `dudoso` si además suena a
    reclamo o a cuenta (se avisa sin vetar), `otro` si no."""
    m = mensaje or ""
    r = bool(RE_RECLAMO.search(m))
    c = bool(RE_CUENTA.search(m)) or bool(RE_CUENTA_DEBIL.search(m) and RE_SUJETO_CUENTA.search(m))
    if RE_PASAJERO.search(m):
        return "dudoso" if r or c else "otro"
    return "reclamo+cuenta" if r and c else "reclamo" if r else "cuenta" if c else "otro"


def _corto(texto, secretos=(), largo=120) -> str:
    """Una línea sin barras, URL, claves ni nada con forma de token, de `largo` caracteres como máximo."""
    t = red.ocultar(str(texto or ""), [s for s in secretos if s])
    t = RE_URL.sub("[url]", t)
    t = RE_JWT.sub("[oculto]", t)
    t = RE_PAR_CLAVE.sub(lambda m: m.group(1) + " [oculto]", t)
    t = RE_OPACO.sub("[oculto]", t)
    return " ".join(t.replace("|", "/").split())[:largo]


def _texto_error(e) -> str:
    """El campo `error` de un post (texto, objeto o nulo) como una sola cadena."""
    if isinstance(e, dict):
        return " ".join(str(e[k]) for k in SUBCAMPOS_ERROR if e.get(k) not in (None, ""))
    return str(e) if e else ""


# ---------------------------------------------------------------- estado en disco

def _leer(raiz, ruta: str) -> list:
    d = publicar._leer_json(raiz_repo(raiz) / ruta, [])
    return d if isinstance(d, list) else []


def _guardar(raiz, ruta: str, datos: list) -> None:
    publicar._escribir_json(raiz_repo(raiz) / ruta, datos)


def _alertar(raiz, clave: str, titulo: str, cuerpo: str) -> None:
    """Deja una alerta pendiente para que el workflow abra el issue (una por clave)."""
    alertas = _leer(raiz, RUTA_ALERTAS)
    if not any(isinstance(a, dict) and a.get("clave") == clave for a in alertas):
        _guardar(raiz, RUTA_ALERTAS, alertas + [{"clave": clave, "titulo": titulo, "cuerpo": cuerpo,
                                                 "avisada": False}])


# ---------------------------------------------------------------- lectura de Buffer

def _selector_error(cliente, canal) -> tuple[str, bool]:
    """(selección del campo `error`, `Post` trae `channelId`), según la introspección de `Post`.

    `error` escalar se pide tal cual; si es objeto, sólo los subcampos que existan entre SUBCAMPOS_ERROR;
    si `Post` no tiene `error`, no se pide (el motivo queda vacío). Si la introspección falla (red, 5xx, cuota),
    el `ErrorBuffer` sube y no se lee nada: leer sin el campo dejaría un reclamo real como «otro» para siempre."""
    campos = (cliente.consulta(Q_POST, canal=canal).get("__type") or {}).get("fields") or []
    if not campos:
        raise publicar.ErrorBuffer("retiros: la introspección de Post vino vacía; no se lee sin saber el esquema")
    por_nombre = {f.get("name"): f for f in campos if isinstance(f, dict)}
    sel = ""
    if "error" in por_nombre:
        t = por_nombre["error"].get("type") or {}
        while isinstance(t, dict) and not t.get("name") and t.get("ofType"):
            t = t["ofType"]
        if isinstance(t, dict) and t.get("kind") in ("OBJECT", "INTERFACE") and t.get("name"):
            sub = (cliente.consulta(Q_SUBCAMPOS % publicar._lit(t["name"]), canal=canal).get("__type") or {})
            nombres = {f.get("name") for f in sub.get("fields") or [] if isinstance(f, dict)}
            usar = [n for n in SUBCAMPOS_ERROR if n in nombres]
            sel = "error { %s }" % " ".join(usar) if usar else ""
        else:
            sel = "error"
    return sel, "channelId" in por_nombre


def _esquema(raiz, cliente, canal, hoy: date, escribir: bool) -> tuple[str, bool, bool]:
    """(selección de `error`, trae `channelId`, salió de la caché). La introspección se guarda DIAS_ESQUEMA días
    en estado/retiros-esquema.json para no gastar dos llamadas de la cuota en cada ciclo."""
    d = publicar._leer_json(raiz_repo(raiz) / RUTA_ESQUEMA, {})
    try:
        if isinstance(d, dict) and isinstance(d.get("sel"), str) and (
                hoy - date.fromisoformat(str(d.get("fecha")))).days < DIAS_ESQUEMA:
            return d["sel"], bool(d.get("con_canal")), True
    except ValueError:
        pass
    sel, con_canal = _selector_error(cliente, canal)
    if escribir:
        publicar._escribir_json(raiz_repo(raiz) / RUTA_ESQUEMA,
                                {"sel": sel, "con_canal": con_canal, "fecha": hoy.isoformat()})
    return sel, con_canal, False


def _posts_en_error(cliente, canales: dict, org_id: str, raiz=None, hoy: date | None = None,
                    escribir: bool = False, selector: tuple[str, bool] | None = None) -> list[dict]:
    """Posts con status error de esos canales, paginados (hasta PAGINAS_MAX de TAM_PAGINA). Sólo lecturas.

    `selector` = (selección de `error`, `Post` trae `channelId`) ya conocido (el diagnóstico): no toca la caché."""
    ids = [c for c in canales.values() if c]
    canal = ids[0]
    if selector is not None:
        (sel_error, con_canal), de_cache = selector, False
    else:
        sel_error, con_canal, de_cache = _esquema(raiz, cliente, canal, hoy or date.today(), escribir)
    sel = " ".join(["id", "status", "dueAt", "text"] + (["channelId"] if con_canal else []) + [sel_error]).strip()
    lista = ", ".join(publicar._lit(c) for c in ids)
    posts, cursor = [], None
    for _ in range(PAGINAS_MAX):
        despues = f", after: {publicar._lit(cursor)}" if cursor else ""
        q = ("query { posts(input: { organizationId: %s, filter: { status: [error], channelIds: [%s] } }, "
             "first: %d%s) { edges { node { %s } } pageInfo { hasNextPage endCursor } } }"
             % (publicar._lit(org_id), lista, TAM_PAGINA, despues, sel))
        try:
            data = cliente.consulta(q, canal=canal).get("posts")
        except publicar.ErrorBuffer as e:
            if de_cache and escribir and str(e).startswith("GraphQL"):
                (raiz_repo(raiz) / RUTA_ESQUEMA).unlink(missing_ok=True)   # el esquema cambió: se relee
            raise
        if not (isinstance(data, dict) and isinstance(data.get("edges"), list)):
            break
        posts += [e["node"] for e in data["edges"] if isinstance(e, dict) and isinstance(e.get("node"), dict)]
        info = data.get("pageInfo")
        cursor = info.get("endCursor") if isinstance(info, dict) else None
        if not (isinstance(info, dict) and info.get("hasNextPage") is True and isinstance(cursor, str) and cursor):
            break
    return [p for p in posts if str(p.get("status", "")).lower() == "error"]


def _cuota_justa(cliente, canal) -> str | None:
    """Texto si alguna cuenta de la cuota de Buffer ya pasa UMBRAL_CUOTA_RETIROS de su tope; None si hay holgura."""
    cuota = getattr(cliente, "cuota", None)
    if cuota is None:
        return None
    try:
        usos = cuota._usos(cuota._leer(), canal)
    except Exception:  # noqa: BLE001 (sin contador legible decide Cuota.comprobar en cada llamada)
        return None
    for nombre, usado in usos.items():
        tope = publicar.TOPES.get(nombre)
        if tope and usado >= tope * UMBRAL_CUOTA_RETIROS:
            return f"cuota de Buffer en {nombre} {usado}/{tope}"
    return None


class _Acotado:
    """Pone a un `ClienteBuffer` el plazo propio de la revisión mientras dura el bloque `with` (y lo quita).

    Cada llamada lleva como tope TOPE_LLAMADA_S (y lo que quede del presupuesto); la espera de un reintento
    se recorta a ESPERA_MAX_S; agotado el presupuesto, la llamada siguiente falla como error de red."""

    def __init__(self, cliente, limite_s: float):
        self.cliente, self.limite_s = cliente, float(limite_s)

    def __enter__(self):
        c = self.cliente
        self._previo = (c.transporte, c.dormir)
        fin = time.monotonic() + self.limite_s
        base, dormir_base, limite = c.transporte, c.dormir, self.limite_s

        def transporte(metodo, url, encabezados, cuerpo=None, tiempo=60):
            libre = fin - time.monotonic()
            if libre < 1:
                raise red.ErrorRed(f"retiros: se agotó su presupuesto de {limite:g} s; no se lee más")
            return base(metodo, url, encabezados, cuerpo, min(float(tiempo), TOPE_LLAMADA_S, libre))

        def dormir(s):
            dormir_base(max(0.0, min(float(s), ESPERA_MAX_S, fin - time.monotonic())))

        c.transporte, c.dormir = transporte, dormir
        return c

    def __exit__(self, *exc):
        self.cliente.transporte, self.cliente.dormir = self._previo
        return False


# ---------------------------------------------------------------- quién es el streamer

def _origen(raiz, post: dict, canales: dict) -> dict:
    """{plataforma, fuente, login} del post: primero el libro de envíos, luego el crédito twitch.tv/<login>."""
    pid = post.get("id")
    plataforma = next((p for p, c in canales.items() if c and c == post.get("channelId")), "buffer")
    fichas = {f.get("id"): f for f in cargar_fuentes(raiz) if isinstance(f, dict)}
    entrada = next((e for e in _leer(raiz, "estado/buffer-envios.json")
                    if isinstance(e, dict) and e.get("post_id") == pid), None)
    if entrada:
        plataforma = entrada.get("plataforma") or plataforma
        datos = entrada.get("datos") if isinstance(entrada.get("datos"), dict) else {}
        toma = datos.get("toma") if isinstance(datos.get("toma"), dict) else {}
        fid = datos.get("ficha_id") or toma.get("fuente_id")
        if fid:
            login = str((fichas.get(fid) or {}).get("twitch_login") or "").lower()
            login = login if re.fullmatch(r"[a-z0-9_]{3,25}", login) else ""
            return {"plataforma": _corto(plataforma, largo=20) or "buffer", "fuente": _corto(fid, largo=60)
                    or "desconocida", "login": login or None}
    m = RE_CANAL.search(str(post.get("text") or ""))
    if m:
        login = m.group(1).lower()
        ficha = next((i for i, f in fichas.items() if str(f.get("twitch_login", "")).lower() == login), None)
        return {"plataforma": plataforma, "fuente": ficha or id_ficha_t(login), "login": login}
    return {"plataforma": plataforma, "fuente": "desconocida", "login": None}


# ---------------------------------------------------------------- lo que hace un reclamo

def _fila(raiz, hoy: date, o: dict, pid: str, motivo: str) -> None:
    """Añade la fila de retiros.md (con su encabezado si el archivo no existe)."""
    accion = (f"vetado twitch.tv/{o['login']}; ficha T pausada" if o["login"]
              else "fuente vetada hasta permiso nuevo" if o["fuente"] != "desconocida"
              else "sin fuente identificada; revisar a mano")
    fila = (f"| {hoy.isoformat()} | {o['fuente']} | {o['plataforma']} {pid} | {o['plataforma']} (Buffer) | "
            f"{motivo or 'sin mensaje'} | {accion} |\n")
    ruta = raiz_repo(raiz) / "retiros.md"
    previo = ruta.read_text(encoding="utf-8") if ruta.exists() else (
        "# Retiros\n\n| Fecha | Fuente | Publicación | Quién pidió | Motivo | Acción |\n|---|---|---|---|---|---|\n")
    ruta.write_text(previo + ("" if previo.endswith("\n") else "\n") + fila, encoding="utf-8")


def _vetar_login(raiz, login: str) -> bool:
    """Añade el login a la lista en línea `logins: [...]` de config/vetados.yml sin tocar lo demás."""
    ruta = raiz_repo(raiz) / "config" / "vetados.yml"
    try:
        lineas = ruta.read_text(encoding="utf-8").split("\n")
    except OSError:
        return False
    for i, linea in enumerate(lineas):
        m = re.match(r"^(logins:\s*\[)([^\]]*)(\].*)$", linea)
        if m:
            actuales = [x.strip() for x in m.group(2).split(",") if x.strip()]
            if login not in [a.lower() for a in actuales]:
                lineas[i] = m.group(1) + ", ".join(actuales + [login]) + m.group(3)
                ruta.write_text("\n".join(lineas), encoding="utf-8")
            return True
    return False


def _pausar_ficha_t(raiz, fuente: str) -> None:
    """Escribe `estado: vetada` en su entrada de estado/fichas-t.yml, si existe."""
    from . import detectar        # tardío: detectar importa fuentes
    import yaml
    try:
        datos = yaml.safe_load((raiz_repo(raiz) / "estado" / "fichas-t.yml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    if isinstance(datos, dict):
        datos = datos.get("fichas")
    for f in datos or []:
        if isinstance(f, dict) and f.get("id") == fuente:
            f["estado"] = "vetada"
            detectar.guardar_fichas_t(raiz, [f])


def _aplicar_reclamo(raiz, hoy: date, o: dict, pid: str, motivo: str) -> None:
    """Fila, veto y alerta. Lo llama `revisar` dentro de `plazo.critico()`, junto con la marca de visto."""
    _fila(raiz, hoy, o, pid, motivo)
    if o["login"]:
        _vetar_login(raiz, o["login"])
        if RE_ID_T.match(o["fuente"]):
            _pausar_ficha_t(raiz, o["fuente"])
    _alertar(raiz, f"reclamo|{pid}", f"ALERTA ROJA: reclamo en {o['plataforma']} (fuente {o['fuente']})",
             f"Buffer marcó con error el post {pid} de {o['plataforma']} (fuente {o['fuente']}) por un posible "
             f"reclamo de derechos: {motivo or 'sin mensaje'}. Se retiró la fuente: quedó vetada y anotada en "
             "retiros.md; la fábrica sigue con las demás. Revisa el correo de la plataforma y el post en Buffer. "
             "Con dos reclamos en 7 días se pausa también la ficha T (estado/pausa-t.yml).")


def _recientes(raiz, hoy: date) -> list[tuple[str, str]]:
    """(fecha, fuente) de las filas de retiros.md con fecha en los últimos 7 días GT, hoy incluido (de Buffer o
    escritas a mano)."""
    desde, out = hoy - timedelta(days=DIAS_VENTANA - 1), []
    for celdas in filas_retiros(raiz):
        try:
            fecha = date.fromisoformat(celdas[0][:10])
        except (ValueError, IndexError):
            continue
        if desde <= fecha <= hoy:
            out.append((fecha.isoformat(), celdas[1] if len(celdas) > 1 else ""))
    return out


def _motivo_pausa(filas: list[tuple[str, str]], extra: int) -> str:
    """Motivo de una línea para estado/pausa-t.yml: la regla y qué reclamos la dispararon (sin «: » ni «#»,
    para que la línea `motivo:` siga siendo YAML válido)."""
    vistas = [f + " " + " ".join((_corto(s, largo=40) or "sin fuente").replace(":", " ").replace("#", " ").split())
              for f, s in filas[:5]]
    if len(filas) > 5:
        vistas.append(f"y {len(filas) - 5} más")
    if extra:
        vistas.append(f"{extra} de Buffer sin anotar")
    n = len(filas) + extra
    return (f"{n} reclamos en {DIAS_VENTANA} días (regla de {RECLAMOS_PARA_PAUSA}), ficha T pausada por retiros "
            f"({'; '.join(vistas)}; ver retiros.md)")


def evaluar_pausa(raiz, hoy: date, extra: int = 0, escribir: bool = True) -> bool:
    """Dos reclamos en 7 días crean estado/pausa-t.yml (con el motivo y los reclamos) y una alerta. `extra`: filas
    que `--seco` no escribió. Devuelve si la ficha T queda (o quedaría) en pausa por esta regla. Es lo único de
    este módulo que pausa algo, y nunca crea el PAUSA global."""
    if pausa_t_activa(raiz)[0]:
        return False
    filas = _recientes(raiz, hoy)
    if len(filas) + extra < RECLAMOS_PARA_PAUSA:
        return False
    if escribir:
        motivo = _motivo_pausa(filas, extra)
        with plazo.critico():                   # pausa y alerta van juntas: la alarma dura no corta en medio
            crear_pausa_t(raiz, motivo, "retiros")
            _alertar(raiz, f"pausa-t|{hoy.isoformat()}", "ALERTA ROJA: PAUSA de la ficha T",
                     f"Motivo: {motivo}. Se creó estado/pausa-t.yml: la ficha T no corre; las campañas A, FM27 y "
                     "las informativas siguen (no hay PAUSA global). Para reanudar, Kevin borra "
                     "estado/pausa-t.yml.")
    return True


# ---------------------------------------------------------------- la revisión

def revisar(raiz, cliente, canales: dict, org_id, ahora: datetime, out=print, escribir: bool = True,
            secretos=(), limite_s: float = PRESUPUESTO_S) -> Informe:
    """Lee los posts en `error` de los canales y actúa una sola vez por post; luego evalúa los 2 en 7 días.

    `cliente` None (sin clave) o sin canales: sólo se evalúa la regla con las filas de retiros.md.
    Si la lectura falla, la regla igual se evalúa y el error se relanza para que el ciclo lo anote; nada se veta
    por un fallo de Buffer. La lectura tiene su propio plazo (`limite_s`, con tope por llamada) y no corre con la
    cuota de Buffer justa. `secretos`: claves que nunca pueden salir en lo que se escribe (además de la de Buffer).
    Un aviso de cuenta se lanza al final como `publicar.AlertaRoja`, con todo lo demás ya anotado."""
    raiz = raiz_repo(raiz)
    hoy = ahora.astimezone(ZONA).date()
    inf, fallo = Informe(), None
    secretos = [s for s in [getattr(cliente, "_clave", ""), *secretos] if s]
    posts = []
    if cliente is not None and org_id and any(canales.values()):
        justa = _cuota_justa(cliente, next(c for c in canales.values() if c))
        if justa:
            out(f"retiros: {justa} (más de {UMBRAL_CUOTA_RETIROS:.0%}); no se lee Buffer, la publicación va primero")
        else:
            try:
                with _Acotado(cliente, limite_s):
                    posts = _posts_en_error(cliente, canales, org_id, raiz, hoy, escribir)
            except (publicar.ErrorBuffer, red.ErrorRed, OSError) as e:
                fallo = e
    inf.leidos = len(posts)
    vistos = [str(x) for x in _leer(raiz, RUTA_VISTOS)]
    for post in posts:
        pid = str(post.get("id") or "")
        if not RE_PID.match(pid):
            if pid:
                out("retiros: un post en error trae un id ilegible; se salta")
            continue
        if pid in vistos:
            continue
        mensaje = _texto_error(post.get("error"))
        clase, o = clasificar(mensaje), _origen(raiz, post, canales)
        motivo = _corto(mensaje, secretos)
        inf.posts.append({"post_id": pid, "clase": clase, "motivo": motivo, **o})
        out(f"retiros: post {pid} ({o['plataforma']}) {clase}, fuente {o['fuente']}"
            + (f", streamer twitch.tv/{o['login']}" if o["login"] else "") + f": {motivo or 'sin mensaje'}")
        if "reclamo" in clase:
            inf.reclamos += 1
        if clase == "dudoso":
            inf.dudosos += 1
        if "cuenta" in clase and inf.cuenta is None:
            inf.cuenta = f"Buffer avisa de la cuenta en {o['plataforma']}: {motivo or 'sin mensaje'}"
        if not escribir:
            continue
        # Marca de visto y efectos van juntos: la alarma dura no puede dejar un reclamo visto pero sin vetar.
        with plazo.critico():
            vistos.append(pid)
            _guardar(raiz, RUTA_VISTOS, vistos)        # antes de abrir aviso o lanzar alerta: no se repite
            if clase == "otro":
                anotar_registro(raiz, f"retiros: error de Buffer en {o['plataforma']} {pid}: "
                                      f"{motivo or 'sin mensaje'}")
            elif clase == "dudoso":
                anotar_registro(raiz, f"retiros: posible reclamo o aviso con error pasajero en {o['plataforma']} "
                                      f"{pid}, sin veto: {motivo or 'sin mensaje'}")
                _alertar(raiz, f"dudoso|{pid}", f"REVISAR: posible reclamo en {o['plataforma']} "
                                                f"(fuente {o['fuente']})",
                         f"Buffer marcó con error el post {pid} de {o['plataforma']} (fuente {o['fuente']}) con un "
                         f"mensaje que mezcla un fallo pasajero y un posible reclamo o aviso de cuenta: "
                         f"{motivo or 'sin mensaje'}. No se vetó ni se pausó nada. Revisa el correo de la "
                         "plataforma; si es un reclamo, anótalo a mano en retiros.md.")
            elif "reclamo" in clase:
                _aplicar_reclamo(raiz, hoy, o, pid, motivo)
    inf.pausa_t = evaluar_pausa(raiz, hoy, 0 if escribir else inf.reclamos, escribir)
    if inf.pausa_t:
        out("retiros: dos reclamos en 7 días, la ficha T queda en pausa" if escribir
            else "retiros (seco): se crearía estado/pausa-t.yml (dos reclamos en 7 días)")
    if fallo is not None:
        raise fallo
    if inf.cuenta and escribir:
        raise publicar.AlertaRoja(inf.cuenta)
    return inf


# ---------------------------------------------------------------- diagnóstico (sólo lectura)

PRESUPUESTO_DIAGNOSTICO_S = 120


def _canales_del_diagnostico(cfg: dict, vistos: list[dict]) -> tuple[dict, dict]:
    """({plataforma: id de canal}, {plataforma: id de organización}) del config; si falta el id guardado, por
    servicio y nombre esperado (sin alertas ni escrituras: aquí sólo se mira)."""
    ids, orgs = {}, {}
    for plat in publicar.PLATAFORMAS:
        ficha = cfg.get(plat) or {}
        canal = ficha.get("buffer_channel_id")
        visto = next((c for c in vistos if c.get("id") == canal), None) if canal else next(
            (c for c in vistos if str(c.get("service", "")).lower() == str(ficha.get("servicio", "")).lower()
             and publicar._norm(c.get("name")) == publicar._norm(ficha.get("nombre_esperado"))), None)
        if visto:
            ids[plat], orgs[plat] = visto["id"], ficha.get("buffer_org_id") or visto.get("_org")
    return ids, orgs


def diagnostico(raiz, cliente, cfg: dict, out=print, secretos=()) -> int:
    """Lee de Buffer real lo que `revisar` necesita y lo imprime. SÓLO lectura: no escribe retiros.md, vetados,
    PAUSA, alertas ni issues. Los registros de Actions son públicos (revisión de Codex, 22 nov): de los canales sólo
    se imprime la red y, de cada publicación en error, la red, la fecha y la clase del error (`clasificar`), nunca su
    texto, su id ni el nombre o id de un canal."""
    secretos = [x for x in [getattr(cliente, "_clave", ""), *secretos] if x]
    try:
        with _Acotado(cliente, PRESUPUESTO_DIAGNOSTICO_S):
            vistos = cliente.todos_los_canales()
            out(f"diagnóstico: {len(vistos)} canal(es) visto(s) en Buffer")
            for c in vistos:
                out(f"  canal · {c.get('service')}" + (" · cola pausada" if c.get("isQueuePaused") else ""))
            ids, orgs = _canales_del_diagnostico(cfg, vistos)
            for plat in publicar.PLATAFORMAS:
                if plat not in ids:
                    out(f"diagnóstico: no se encontró el canal de {plat}; se omite")
            if not ids:
                out("diagnóstico: ningún canal configurado coincide; nada que leer")
                return 1
            canal0 = next(iter(ids.values()))
            sel, con_canal = _selector_error(cliente, canal0)
            out(f"diagnóstico: selector de error elegido: {sel or '(Post no tiene campo error; no se pide)'}"
                f" · Post con channelId: {'sí' if con_canal else 'no'}")
            total = 0
            for plat, canal in ids.items():
                org = orgs.get(plat) or cliente.organizaciones()[0]["id"]
                posts = _posts_en_error(cliente, {plat: canal}, org, raiz, selector=(sel, con_canal))
                total += len(posts)
                out(f"diagnóstico: {plat}: {len(posts)} publicación(es) en error")
                for p in posts:
                    txt = _texto_error(p.get("error"))
                    out(f"  {plat} | {_corto(p.get('dueAt'), secretos, 40)} | {clasificar(txt) if txt else 'sin mensaje'}")
    except (publicar.ErrorBuffer, red.ErrorRed, OSError) as e:
        # Sólo la clase del error: su texto puede traer datos de la cuenta y el registro es público (Codex, ronda 2).
        out(f"diagnóstico: falló la consulta a Buffer ({type(e).__name__})")
        return 1
    out(f"diagnóstico: listo, {total} publicación(es) en error en total (sólo lectura, nada escrito)")
    return 0


# ---------------------------------------------------------------- avisos (issues)

def abrir_avisos(raiz, ejecutor=subprocess.run) -> list[str]:
    """Abre con `gh issue create --label alerta-roja` cada alerta pendiente y la marca avisada.

    Si `gh` falla (con y sin la etiqueta) la alerta queda pendiente para la próxima corrida. La etiqueta se crea
    una vez por corrida."""
    alertas = _leer(raiz, RUTA_ALERTAS)
    pendientes = [a for a in alertas if isinstance(a, dict) and not a.get("avisada")]
    if not pendientes:
        return []

    def gh(*args) -> bool:
        try:
            r = ejecutor(["gh", *args], capture_output=True, text=True, timeout=60)
            return getattr(r, "returncode", 1) == 0
        except (OSError, subprocess.SubprocessError):
            return False

    gh("label", "create", ETIQUETA, "--color", "B60205", "--force")
    abiertos = []
    for a in pendientes:
        # Sin la etiqueta (si no se pudo crear) el aviso igual tiene que llegar: un segundo intento sin ella.
        if (gh("issue", "create", "--title", a["titulo"], "--body", a["cuerpo"], "--label", ETIQUETA)
                or gh("issue", "create", "--title", a["titulo"], "--body", a["cuerpo"])):
            a["avisada"] = True
            abiertos.append(a["clave"])
            _guardar(raiz, RUTA_ALERTAS, alertas)
    return abiertos


# ---------------------------------------------------------------- línea de órdenes

def main(argv: list[str] | None = None, env=None, transporte=None, ahora=None, salida=print,
         ejecutor=subprocess.run) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.retiros", description="Reclamos y retiros de Buffer.")
    ap.add_argument("--revisar", action="store_true", help="lee los posts en error y actúa")
    ap.add_argument("--seco", action="store_true", help="con --revisar: sólo lee e imprime, no escribe nada")
    ap.add_argument("--diagnostico", action="store_true",
                    help="sólo lectura: canales, selector de error y publicaciones en error de Buffer real")
    ap.add_argument("--abrir-avisos", action="store_true", help="abre un issue por cada alerta pendiente")
    ap.add_argument("--raiz", default=None)
    a = ap.parse_args(argv)
    raiz = raiz_repo(a.raiz)
    env = os.environ if env is None else env
    clave = env.get("BUFFER_API_KEY", "")
    out = (lambda t: salida(red.ocultar(str(t), [clave] if clave else [])))
    if a.abrir_avisos:
        for c in abrir_avisos(raiz, ejecutor):
            out(f"issue abierto: {c}")
        return 0
    if a.diagnostico:
        if not clave:
            out("diagnóstico: falta BUFFER_API_KEY")
            return 1
        # Ni siquiera la cuota se escribe en el repo: el contador va a una carpeta temporal.
        cliente = publicar.ClienteBuffer(clave, transporte, tempfile.mkdtemp(prefix="cuota-diagnostico-"))
        return diagnostico(raiz, cliente, cargar_yaml("config/canales.yml", raiz), out)
    if not a.revisar:
        ap.error("falta --revisar, --diagnostico o --abrir-avisos")
    if not clave:
        out("retiros: falta BUFFER_API_KEY, se omite")
        return 0
    cfg = cargar_yaml("config/canales.yml", raiz)
    canales = {p: (cfg.get(p) or {}).get("buffer_channel_id") for p in publicar.PLATAFORMAS}
    org = next((c.get("buffer_org_id") for c in cfg.values() if isinstance(c, dict) and c.get("buffer_org_id")), None)
    # `--seco` no escribe nada: ni siquiera la cuota de la API, que va a una carpeta temporal.
    base = tempfile.mkdtemp(prefix="cuota-retiros-") if a.seco else raiz
    try:
        cliente = publicar.ClienteBuffer(clave, transporte, base)
        secretos = [v for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TWITCH_CLIENT_SECRET", "GEMINI_API_KEY",
                                "GH_TOKEN", "GITHUB_TOKEN") if len(v := str(env.get(k) or "")) >= 8]
        inf = revisar(raiz, cliente, canales, org, ahora or datetime.now(timezone.utc), out, escribir=not a.seco,
                      secretos=secretos)
    except publicar.AlertaRoja as e:
        out(f"ALERTA ROJA: {e}")
        return 3
    except (publicar.ErrorBuffer, red.ErrorRed, OSError) as e:
        out(f"retiros: no se pudo leer Buffer: {e}")
        return 1
    out(f"retiros: {inf.leidos} post(s) en error leídos, {len(inf.posts)} nuevo(s), {inf.reclamos} reclamo(s), "
        f"{inf.dudosos} dudoso(s)"
        + (" (seco: no se escribió nada)" if a.seco else ""))
    if a.seco and inf.cuenta:
        out(f"retiros (seco): lanzaría alerta roja de cuenta: {inf.cuenta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

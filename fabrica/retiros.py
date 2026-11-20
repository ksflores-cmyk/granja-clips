"""Reclamos y retiros: un post de Buffer en `error` con un mensaje de derechos veta solo al streamer.

Decisión de Kevin (19 nov 2026): ante un reclamo, retiro el mismo día, veto del streamer, alerta roja y se
sigue con los demás. Dos reclamos en 7 días pausan sólo la ficha T (estado/pausa-t.yml); un aviso de cuenta
suspendida lanza `publicar.AlertaRoja` (PAUSA global). Buffer no se entera de lo que la red borra después:
esos retiros entran a mano en retiros.md. Sin LLM, sin dependencias nuevas, sin claves en nada de lo escrito.

`python -m fabrica.retiros --revisar [--seco]` o `--abrir-avisos`.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import publicar, red
from .config import cargar_yaml, raiz_repo
from .fuentes import RE_ID_T, cargar_fuentes
from .pausa import ZONA, crear_pausa_t, pausa_t_activa  # noqa: F401 (pausa_t_activa se re-exporta)
from .registro import anotar_registro
from .vetados import RE_CANAL, filas_retiros

PAGINAS_MAX, TAM_PAGINA = 3, 50
DIAS_VENTANA = 7
RECLAMOS_PARA_PAUSA = 2
SUBCAMPOS_ERROR = ("message", "rawError", "details", "code")
RUTA_VISTOS = "estado/retiros-vistos.json"
RUTA_ALERTAS = "estado/alertas-retiro.json"
ETIQUETA = "alerta-roja"

RE_RECLAMO = re.compile(
    r"copyright|derechos de autor|content ?id|dmca|infring|infracci|takedown|claim|reclamo|strike|removed|"
    r"retirad|eliminad|violat|community guidelines|normas de la comunidad|intellectual property|"
    r"propiedad intelectual", re.IGNORECASE)
RE_CUENTA = re.compile(r"suspend|restrict|bloquead|blocked|disabled|deshabilitad|banned|terminated", re.IGNORECASE)
RE_URL = re.compile(r"https?://\S+")

Q_POST = ('query { __type(name: "Post") { fields { name type { name kind ofType { name kind '
          'ofType { name kind } } } } } }')
Q_SUBCAMPOS = "query { __type(name: %s) { fields { name } } }"


@dataclass
class Informe:
    """Lo que dejó (o dejaría, con `--seco`) una revisión."""
    leidos: int = 0
    posts: list[dict] = field(default_factory=list)   # post_id, plataforma, clase, login, fuente, motivo
    reclamos: int = 0
    pausa_t: bool = False
    cuenta: str | None = None


def clasificar(mensaje: str) -> str:
    """`reclamo`, `cuenta`, `reclamo+cuenta` u `otro`, por el mensaje de error (sin mayúsculas)."""
    r, c = bool(RE_RECLAMO.search(mensaje or "")), bool(RE_CUENTA.search(mensaje or ""))
    return "reclamo+cuenta" if r and c else "reclamo" if r else "cuenta" if c else "otro"


def _corto(texto, secretos=()) -> str:
    """Motivo en una línea, sin barras, URL ni claves, de 120 caracteres como máximo."""
    t = RE_URL.sub("[url]", red.ocultar(str(texto or ""), [s for s in secretos if s]))
    return " ".join(t.replace("|", "/").split())[:120]


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
    si `Post` no tiene `error`, no se pide (el motivo queda vacío)."""
    try:
        campos = (cliente.consulta(Q_POST, canal=canal).get("__type") or {}).get("fields") or []
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
    except publicar.ErrorBuffer:
        return "", False         # sin introspección se lee sin el campo; la lectura real dirá si falla la clave


def _posts_en_error(cliente, canales: dict, org_id: str) -> list[dict]:
    """Posts con status error de esos canales, paginados (hasta PAGINAS_MAX de TAM_PAGINA)."""
    ids = [c for c in canales.values() if c]
    canal = ids[0]
    sel_error, con_canal = _selector_error(cliente, canal)
    sel = " ".join(["id", "status", "dueAt", "text"] + (["channelId"] if con_canal else []) + [sel_error]).strip()
    lista = ", ".join(publicar._lit(c) for c in ids)
    posts, cursor = [], None
    for _ in range(PAGINAS_MAX):
        despues = f", after: {publicar._lit(cursor)}" if cursor else ""
        q = ("query { posts(input: { organizationId: %s, filter: { status: [error], channelIds: [%s] } }, "
             "first: %d%s) { edges { node { %s } } pageInfo { hasNextPage endCursor } } }"
             % (publicar._lit(org_id), lista, TAM_PAGINA, despues, sel))
        data = cliente.consulta(q, canal=canal).get("posts")
        if not (isinstance(data, dict) and isinstance(data.get("edges"), list)):
            break
        posts += [e["node"] for e in data["edges"] if isinstance(e, dict) and isinstance(e.get("node"), dict)]
        info = data.get("pageInfo")
        cursor = info.get("endCursor") if isinstance(info, dict) else None
        if not (isinstance(info, dict) and info.get("hasNextPage") is True and isinstance(cursor, str) and cursor):
            break
    return [p for p in posts if str(p.get("status", "")).lower() == "error"]


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
            return {"plataforma": plataforma, "fuente": str(fid), "login": login or None}
    m = RE_CANAL.search(str(post.get("text") or ""))
    if m:
        login = m.group(1).lower()
        ficha = next((i for i, f in fichas.items() if str(f.get("twitch_login", "")).lower() == login), None)
        return {"plataforma": plataforma, "fuente": ficha or "t-" + login.replace("_", "-"), "login": login}
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


def _recientes(raiz, hoy: date) -> int:
    """Filas de retiros.md con fecha en los últimos 7 días GT, hoy incluido (de Buffer o escritas a mano)."""
    desde, n = hoy - timedelta(days=DIAS_VENTANA - 1), 0
    for celdas in filas_retiros(raiz):
        try:
            if desde <= date.fromisoformat(celdas[0][:10]) <= hoy:
                n += 1
        except (ValueError, IndexError):
            pass
    return n


def evaluar_pausa(raiz, hoy: date, extra: int = 0, escribir: bool = True) -> bool:
    """Dos reclamos en 7 días crean estado/pausa-t.yml y una alerta. `extra`: filas que `--seco` no escribió.
    Devuelve si la ficha T está (o quedaría) en pausa por esta regla. Nunca crea el PAUSA global."""
    if pausa_t_activa(raiz)[0]:
        return False
    if _recientes(raiz, hoy) + extra < RECLAMOS_PARA_PAUSA:
        return False
    if escribir:
        motivo = f"{RECLAMOS_PARA_PAUSA} reclamos en {DIAS_VENTANA} días (retiros.md): ficha T pausada"
        crear_pausa_t(raiz, motivo, "retiros")
        _alertar(raiz, f"pausa-t|{hoy.isoformat()}", "ALERTA ROJA: PAUSA de la ficha T",
                 f"Hubo {RECLAMOS_PARA_PAUSA} reclamos en {DIAS_VENTANA} días. Se creó estado/pausa-t.yml: la "
                 "ficha T no corre; las campañas A, FM27 y las informativas siguen. Para reanudar, Kevin borra "
                 "estado/pausa-t.yml.")
    return True


# ---------------------------------------------------------------- la revisión

def revisar(raiz, cliente, canales: dict, org_id, ahora: datetime, out=print, escribir: bool = True) -> Informe:
    """Lee los posts en `error` de los canales y actúa una sola vez por post; luego evalúa los 2 en 7 días.

    `cliente` None (sin clave) o sin canales: sólo se evalúa la regla con las filas de retiros.md.
    Si la lectura falla, la regla igual se evalúa y el error se relanza para que el ciclo lo anote.
    Un aviso de cuenta se lanza al final como `publicar.AlertaRoja`, con todo lo demás ya anotado."""
    raiz = raiz_repo(raiz)
    hoy = ahora.astimezone(ZONA).date()
    inf, fallo = Informe(), None
    secretos = [getattr(cliente, "_clave", "")]
    posts = []
    if cliente is not None and org_id and any(canales.values()):
        try:
            posts = _posts_en_error(cliente, canales, org_id)
        except (publicar.ErrorBuffer, red.ErrorRed, OSError) as e:
            fallo = e
    inf.leidos = len(posts)
    vistos = [str(x) for x in _leer(raiz, RUTA_VISTOS)]
    for post in posts:
        pid = str(post.get("id") or "")
        if not pid or pid in vistos:
            continue
        mensaje = _texto_error(post.get("error"))
        clase, o = clasificar(mensaje), _origen(raiz, post, canales)
        motivo = _corto(mensaje, secretos)
        inf.posts.append({"post_id": pid, "clase": clase, "motivo": motivo, **o})
        if escribir:
            vistos.append(pid)
            _guardar(raiz, RUTA_VISTOS, vistos)           # antes de abrir aviso o lanzar alerta: no se repite
        out(f"retiros: post {pid} ({o['plataforma']}) {clase}, fuente {o['fuente']}"
            + (f", streamer twitch.tv/{o['login']}" if o["login"] else "") + f": {motivo or 'sin mensaje'}")
        if clase == "otro":
            if escribir:
                anotar_registro(raiz, f"retiros: error de Buffer en {o['plataforma']} {pid}: {motivo or 'sin mensaje'}")
            continue
        if "reclamo" in clase:
            inf.reclamos += 1
            if escribir:
                _aplicar_reclamo(raiz, hoy, o, pid, motivo)
        if "cuenta" in clase and inf.cuenta is None:
            inf.cuenta = f"Buffer avisa de la cuenta en {o['plataforma']}: {motivo or 'sin mensaje'}"
    inf.pausa_t = evaluar_pausa(raiz, hoy, 0 if escribir else inf.reclamos, escribir)
    if inf.pausa_t:
        out("retiros: dos reclamos en 7 días, la ficha T queda en pausa" if escribir
            else "retiros (seco): se crearía estado/pausa-t.yml (dos reclamos en 7 días)")
    if fallo is not None:
        raise fallo
    if inf.cuenta and escribir:
        raise publicar.AlertaRoja(inf.cuenta)
    return inf


# ---------------------------------------------------------------- avisos (issues)

def abrir_avisos(raiz, ejecutor=subprocess.run) -> list[str]:
    """Abre con `gh issue create --label alerta-roja` cada alerta pendiente y la marca avisada.

    Si `gh` falla la alerta queda pendiente para la próxima corrida. La etiqueta se crea una vez por corrida."""
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
        if gh("issue", "create", "--title", a["titulo"], "--body", a["cuerpo"], "--label", ETIQUETA):
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
    if not a.revisar:
        ap.error("falta --revisar o --abrir-avisos")
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
        inf = revisar(raiz, cliente, canales, org, ahora or datetime.now(timezone.utc), out, escribir=not a.seco)
    except publicar.AlertaRoja as e:
        out(f"ALERTA ROJA: {e}")
        return 3
    except (publicar.ErrorBuffer, red.ErrorRed, OSError) as e:
        out(f"retiros: no se pudo leer Buffer: {e}")
        return 1
    out(f"retiros: {inf.leidos} post(s) en error leídos, {len(inf.posts)} nuevo(s), {inf.reclamos} reclamo(s)"
        + (" (seco: no se escribió nada)" if a.seco else ""))
    if a.seco and inf.cuenta:
        out(f"retiros (seco): lanzaría alerta roja de cuenta: {inf.cuenta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

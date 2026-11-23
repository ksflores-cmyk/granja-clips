"""Robot de envío a Content Rewards (nube, sin la PC de Kevin).

Content Rewards sólo acepta el enlace de un post si se envía en los 30 minutos siguientes a publicarlo
(términos §7.12). No hay API oficial para clippers, así que un navegador sin pantalla (Playwright, dentro de
GitHub Actions) abre la campaña con la sesión de Kevin y pega el enlace.

- Sesión: secreto `CR_SESION` (base64 de un storageState de Playwright, sólo cookies/almacenamiento de
  contentrewards.com y whop.com). Lo crea Kevin con `herramientas/sesion-cr.mjs`; nunca se imprime.
- Qué envía: las filas de `estado/envios-campana.json` (las anota `publicar` al programar un clip de campaña)
  cuya hora programada cayó hace menos de `VENTANA_MIN` minutos y cuya ficha tiene `cr_campaign_id`.
- Resultado: `estado/envios-cr.json` (archivo propio, para no chocar con el commit de `producir`).
- Sesión vencida: `estado/alerta-cr.json` y salida 3; el workflow abre un issue `alerta-roja`.

Uso:
  python -m fabrica.enviar_cr --hay            # salida 0 si hay algo que enviar ahora, 10 si no
  python -m fabrica.enviar_cr [--ensayo]       # espera los enlaces de Buffer y los envía
  python -m fabrica.enviar_cr --url URL --campana CR_ID [--real]   # prueba manual (sin --real no pulsa «Enviar»)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import envios_campana
from .candado import candado
from .config import cargar_yaml, raiz_repo

NOMBRE = "envios-cr.json"
ALERTA = "alerta-cr.json"
VENTANA_MIN = 28          # Content Rewards da 30; 2 de margen
ANTES_MIN = 15            # una fila programada para dentro de 15 min ya cuenta (la corrida espera a Buffer)
ESPERA_MAX_S = 16 * 60    # cuánto espera esta corrida a que Buffer publique y dé el enlace
BASE = "https://contentrewards.com/c/campaigns/"
SALIDA_HAY, SALIDA_NADA, SALIDA_SESION = 0, 10, 3


# ---------------------------------------------------------------- utilidades puras (con pruebas)

def campanas_cr(fuentes: list[dict]) -> dict[str, str]:
    """{nombre de campaña: cr_campaign_id} de las fichas vigentes que tienen `cr_campaign_id`."""
    mapa = {}
    for f in fuentes or []:
        if isinstance(f, dict) and f.get("cr_campaign_id") and f.get("campana") and f.get("estado") == "vigente":
            mapa[str(f["campana"])] = str(f["cr_campaign_id"])
    return mapa


def normalizar_url(url: str) -> str:
    """YouTube: Buffer da `watch?v=ID`; Content Rewards reconoce mejor el enlace del Short."""
    m = re.match(r"https?://(?:www\.)?youtube\.com/watch\?v=([\w-]{6,})", url or "")
    if m:
        return f"https://www.youtube.com/shorts/{m.group(1)}"
    return url


def _hora(fila: dict) -> datetime | None:
    try:
        h = datetime.fromisoformat(str(fila.get("hora")))
    except ValueError:
        return None
    return h if h.tzinfo else h.replace(tzinfo=timezone.utc)


def pendientes(filas: list[dict], hechos: dict, mapa: dict, ahora: datetime) -> list[dict]:
    """Filas de campaña que esta corrida debe enviar: hora en [ahora-VENTANA, ahora+ANTES], campaña con id,
    y sin resultado final en `hechos` (un fallo se reintenta mientras siga dentro de la ventana)."""
    out = []
    for f in filas:
        h = _hora(f)
        if h is None or f.get("campana") not in mapa:
            continue
        if not (ahora - timedelta(minutes=VENTANA_MIN) <= h <= ahora + timedelta(minutes=ANTES_MIN)):
            continue
        previo = hechos.get(f.get("post_id"), {})
        if previo.get("resultado") in ("enviado", "rechazado", "campana_pausada"):
            continue
        out.append(dict(f, cr_campaign_id=mapa[f["campana"]]))
    return out


# Motivos del diálogo con el botón «Enviar» apagado, del más útil al más genérico. La línea del post
# («Publicado hace 2 h…», «sólo hasta 30 minutos…») dice por qué se rechaza ESTE enlace; el resumen
# «N publicaciones antiguas no se pueden enviar» sólo cuenta otras y va después.
MOTIVOS = (
    re.compile(r"Publicado hace|Posted .* ago|hasta 30 minutos|within 30 minutes", re.I),
    re.compile(r"vinculad|not (?:linked|connected)|connect your", re.I),
    re.compile(r"no se puede|cannot|can't", re.I),
)


def motivo_rechazo(texto: str) -> str:
    """La línea del diálogo que explica el rechazo, según la prioridad de MOTIVOS (máx. 200 caracteres)."""
    lineas = [ln.strip() for ln in (texto or "").splitlines() if ln.strip()]
    for patron in MOTIVOS:
        for ln in lineas:
            if patron.search(ln):
                return ln[:200]
    return "botón Enviar desactivado"


def vencido(fila: dict, ahora: datetime) -> bool:
    h = _hora(fila)
    return h is None or ahora > h + timedelta(minutes=VENTANA_MIN)


# ---------------------------------------------------------------- estado

def _ruta(raiz, nombre=NOMBRE) -> Path:
    return raiz_repo(raiz) / "estado" / nombre


def leer_hechos(raiz) -> dict:
    try:
        datos = json.loads(_ruta(raiz).read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def anotar(raiz, post_id: str, **campos) -> None:
    ruta = _ruta(raiz)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with candado(ruta):
        datos = leer_hechos(raiz)
        fila = datos.get(post_id, {})
        fila.update(campos, actualizado=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        datos[post_id] = fila
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ruta.parent, delete=False,
                                         suffix=".tmp") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            f.write("\n")
        Path(f.name).replace(ruta)


def alerta(raiz, motivo: str) -> None:
    ruta = _ruta(raiz, ALERTA)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({"motivo": motivo, "cuando": datetime.now(timezone.utc).isoformat()},
                               ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- navegador

class SesionVencida(Exception):
    pass


def cargar_sesion() -> dict:
    crudo = os.environ.get("CR_SESION", "").strip()
    if not crudo:
        raise SesionVencida("falta el secreto CR_SESION")
    try:
        datos = json.loads(base64.b64decode(crudo).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SesionVencida(f"CR_SESION ilegible ({type(e).__name__})") from None
    if not isinstance(datos, dict) or not datos.get("cookies"):
        raise SesionVencida("CR_SESION sin cookies")
    return datos


ENVIAR = re.compile(r"^\s*(Enviar clip|Submit clip|Submit)\s*$", re.I)
ENLACE = re.compile(r"enlace|link|url", re.I)
ACEPTO = re.compile(r"He leído|I have read|I've read|acepto", re.I)


class Robot:
    def __init__(self, sesion: dict, capturas: Path | None = None):
        from playwright.sync_api import sync_playwright  # sólo aquí: las pruebas no necesitan Playwright
        self._pw = sync_playwright().start()
        self._nav = self._pw.chromium.launch(headless=True)
        self._ctx = self._nav.new_context(storage_state=sesion, locale="es-ES",
                                          viewport={"width": 1280, "height": 900})
        self.capturas = capturas
        if capturas:
            capturas.mkdir(parents=True, exist_ok=True)

    def cerrar(self):
        for x in (self._ctx, self._nav):
            try:
                x.close()
            except Exception:  # noqa: BLE001
                pass
        self._pw.stop()

    def _foto(self, page, nombre):
        if self.capturas:
            try:
                page.screenshot(path=str(self.capturas / f"{nombre}.png"))
            except Exception:  # noqa: BLE001
                pass

    def enviar(self, campaign_id: str, url: str, etiqueta: str, ensayo: bool) -> tuple[str, str]:
        """Devuelve (resultado, detalle). resultado: enviado | ensayo_ok | campana_pausada | rechazado | error."""
        page = self._ctx.new_page()
        try:
            page.goto(BASE + campaign_id, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            if re.search(r"/(login|sign-?in|auth)", page.url) or "whop.com/login" in page.url:
                raise SesionVencida(f"redirigido a {page.url.split('?')[0]}")
            cuerpo = page.inner_text("body")
            if re.search(r"Pausada por la marca|Paused by the brand", cuerpo, re.I):
                self._foto(page, f"{etiqueta}-pausada")
                return "campana_pausada", "la marca pausó la campaña"
            if re.search(r"Iniciar sesión|Sign in|Log in", cuerpo) and "Menú de cuenta" not in cuerpo \
                    and not page.get_by_role("button", name=re.compile("Menú de cuenta|Account menu", re.I)).count():
                raise SesionVencida("la página pide iniciar sesión")

            campo = None
            botones = page.get_by_role("button", name=ENVIAR)
            for i in reversed(range(botones.count())):   # el del cuerpo de la página suele ser el último
                try:
                    botones.nth(i).click(timeout=5000)
                    campo = page.get_by_placeholder(ENLACE).first
                    campo.wait_for(state="visible", timeout=5000)
                    break
                except Exception:  # noqa: BLE001
                    campo = None
            if campo is None:
                self._foto(page, f"{etiqueta}-sin-dialogo")
                return "error", "no se abrió el diálogo de envío"

            campo.fill(url)
            page.wait_for_timeout(4000)                  # Content Rewards revisa el enlace (cuenta vinculada, 30 min)
            dialogo = page.get_by_role("dialog")
            raiz_d = dialogo.first if dialogo.count() else page
            casilla = raiz_d.get_by_role("checkbox")
            if casilla.count():
                if not casilla.first.is_checked():
                    casilla.first.check(force=True)
            else:
                raiz_d.get_by_text(ACEPTO).first.click()
            final = raiz_d.get_by_role("button", name=ENVIAR).last
            page.wait_for_timeout(1500)
            self._foto(page, f"{etiqueta}-listo")
            if not final.is_enabled():
                return "rechazado", motivo_rechazo(raiz_d.inner_text())
            if ensayo:
                return "ensayo_ok", "todo listo; en ensayo no se pulsa Enviar"
            final.click()
            page.wait_for_timeout(5000)
            self._foto(page, f"{etiqueta}-enviado")
            if page.get_by_placeholder(ENLACE).count() and page.get_by_placeholder(ENLACE).first.is_visible():
                return "error", "el diálogo sigue abierto tras pulsar Enviar"
            return "enviado", "Content Rewards aceptó el envío"
        finally:
            page.close()


# ---------------------------------------------------------------- Buffer

def _publicador(raiz):
    from . import publicar
    cfg = cargar_yaml("config/canales.yml", raiz) or {}
    ids = {p: (cfg.get(p) or {}).get("buffer_channel_id") for p in ("shorts", "tiktok", "reels")}
    ids = {p: v for p, v in ids.items() if v}
    orgs = {p: (cfg.get(p) or {}).get("buffer_org_id") for p in ids}
    cliente = publicar.ClienteBuffer(os.environ.get("BUFFER_API_KEY"), None, raiz)
    return publicar.Publicador(cliente, None, raiz, "vivo", None, ids, orgs=orgs)


def _url_de(fila, pub) -> str | None:
    if fila.get("url"):
        return fila["url"]
    try:
        estado, url = pub.estado_y_url(fila["post_id"])
    except Exception as e:  # noqa: BLE001
        print(f"  {fila['plataforma']}: Buffer no respondió ({type(e).__name__})")
        return None
    return url if estado == "sent" else None


# ---------------------------------------------------------------- principal

def principal(argv=None, raiz=None) -> int:
    ap = argparse.ArgumentParser(prog="fabrica.enviar_cr")
    ap.add_argument("--hay", action="store_true")
    ap.add_argument("--ensayo", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--campana")
    ap.add_argument("--real", action="store_true", help="en prueba manual, pulsar Enviar de verdad")
    ap.add_argument("--capturas", default=os.environ.get("CR_CAPTURAS"))
    a = ap.parse_args(argv)
    capturas = Path(a.capturas) if a.capturas else None

    if a.url:                                    # prueba manual
        if not a.campana:
            print("falta --campana (cr_campaign_id)")
            return 2
        try:
            robot = Robot(cargar_sesion(), capturas)
        except SesionVencida as e:
            print(f"sesión: {e}")
            return SALIDA_SESION
        try:
            r, d = robot.enviar(a.campana, normalizar_url(a.url), "manual", ensayo=not a.real)
        except SesionVencida as e:
            print(f"sesión vencida: {e}")
            return SALIDA_SESION
        finally:
            robot.cerrar()
        print(f"manual: {r} — {d}")
        return 0 if r in ("ensayo_ok", "enviado", "campana_pausada", "rechazado") else 1

    mapa = campanas_cr(cargar_yaml("fuentes.yml", raiz) or [])
    ahora = datetime.now(timezone.utc)
    lista = pendientes(envios_campana.leer(raiz), leer_hechos(raiz), mapa, ahora)
    if a.hay:
        print(f"envíos a Content Rewards pendientes: {len(lista)}")
        return SALIDA_HAY if lista else SALIDA_NADA
    if not lista:
        print("nada que enviar a Content Rewards")
        return 0

    try:
        sesion = cargar_sesion()
    except SesionVencida as e:
        alerta(raiz, f"Content Rewards: {e}")
        print(f"sesión: {e}")
        return SALIDA_SESION
    pub = _publicador(raiz)
    robot = Robot(sesion, capturas)
    limite = time.monotonic() + ESPERA_MAX_S
    codigo = 0
    try:
        while lista:
            ahora = datetime.now(timezone.utc)
            siguen = []
            for f in lista:
                et = f"toma{f.get('toma')}-{f['plataforma']}"
                if vencido(f, ahora):
                    anotar(raiz, f["post_id"], resultado="vencido", campana=f["campana"],
                           detalle="pasaron más de 28 min sin enlace de Buffer")
                    print(f"  {et}: vencido sin enlace")
                    continue
                url = _url_de(f, pub)
                if not url:
                    siguen.append(f)
                    continue
                url = normalizar_url(url)
                try:
                    r, d = robot.enviar(f["cr_campaign_id"], url, et, a.ensayo)
                except SesionVencida as e:
                    alerta(raiz, f"Content Rewards: sesión vencida ({e})")
                    print(f"  sesión vencida: {e}")
                    return SALIDA_SESION
                except Exception as e:  # noqa: BLE001
                    r, d = "error", f"{type(e).__name__}: {str(e)[:160]}"
                anotar(raiz, f["post_id"], resultado=r, detalle=d, url=url, campana=f["campana"],
                       plataforma=f["plataforma"], toma=f.get("toma"))
                print(f"  {et}: {r} — {d}")
                if r == "error":
                    codigo = 1
                    siguen.append(f)                # se reintenta en la siguiente vuelta, dentro de la ventana
            lista = siguen
            if not lista or time.monotonic() > limite:
                break
            time.sleep(30)
        for f in lista:
            print(f"  toma{f.get('toma')}-{f['plataforma']}: sin enviar al cerrar la corrida")
    finally:
        robot.cerrar()
    return codigo


if __name__ == "__main__":
    sys.exit(principal())

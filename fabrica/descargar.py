"""Descarga de un candidato autorizado, siempre dentro de una carpeta temporal fuera del repo.

Orden fijo: (1) `fuentes.autoriza_descarga` y `fuentes.puede_descargar` ANTES de abrir una conexión;
(2) descarga; (3) ffprobe mide la duración y se vuelve a comprobar contra `reglas.duracion_s`.
Si la descarga salió bien se llama a `puertas.anotar_visto` (aunque la duración no cumpla, para no
volver a bajar el mismo archivo). La carpeta temporal se borra al salir del `with`, pase lo que pase.

Dos caminos, sin más:
- `material` (o ficha propia) con URL https directa: streaming con tope de 300 MB y `Content-Type` de video.
- clip de Twitch, sólo con `reglas.permite_descarga: true`: `yt-dlp` sin cookies, sin listas, 1080p o menos,
  tope de 120 s. Nunca se descarga de YouTube.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from . import fuentes, puertas
from .editar.componer import sondear_video

TOPE_BYTES = 800 * 1024 * 1024         # el material de campaña pesa hasta ~550 MB (FM27, 18 nov)
TOPE_FUENTE_S = 600.0                   # fuente más larga que esto no se mira: Gemini ve la copia ligera
TOPE_TWITCH_S = 120
BLOQUE = 1 << 20
TIEMPO_ESPERA_S = 60
FORMATO_TWITCH = "best[height<=1080][ext=mp4]/best[height<=1080]"   # todas las alternativas con tope de 1080p


class ErrorDescarga(RuntimeError):
    """La descarga no se hizo o no sirve. El mensaje es el motivo del descarte."""


@dataclass
class Descarga:
    ruta: Path
    duracion: float


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    """No sigue ninguna redirección: la URL autorizada es la única que se abre."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    return urllib.request.build_opener(_SinRedirecciones)


def _abrir_urllib(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "granja-clips-descarga"})
    return _opener().open(req, timeout=TIEMPO_ESPERA_S)  # noqa: S310 (https comprobado antes)


def _es_https(url) -> bool:
    return isinstance(url, str) and url.startswith("https://") and bool(urlparse(url).hostname)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _es_youtube(url: str) -> bool:
    h = _host(url)
    return h in ("youtu.be", "youtube.com") or h.endswith(".youtube.com")


DRIVE = "drive.usercontent.google.com"


def _aviso_drive(url: str, html: str) -> str | None:
    """Si Google Drive devuelve su página de aviso ("no se puede analizar en busca de virus"),
    arma la URL de confirmación con los campos ocultos de ESE formulario. Sólo hacia
    https://drive.usercontent.google.com/download y con exactamente el mismo `id` autorizado;
    si no, None. (Las redirecciones ya las rechaza `_abrir_urllib` antes de contactar el destino.)"""
    import html as _html
    import re
    from urllib.parse import parse_qs, urlencode, urlparse
    ids = parse_qs(urlparse(url).query).get("id") or []
    if len(ids) != 1:
        return None
    for bloque in re.findall(r"<form\b[^>]*>.*?</form>", html, flags=re.S | re.I):
        accion = re.search(r'action="([^"]+)"', bloque)
        if not accion:
            continue
        destino = urlparse(_html.unescape(accion.group(1)))
        if destino.scheme != "https" or destino.netloc != DRIVE or destino.path != "/download":
            continue
        pares = [(_html.unescape(n), _html.unescape(v)) for n, v in
                 re.findall(r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]+value="([^"]*)"', bloque)]
        ids_form = [v for n, v in pares if n == "id"]
        if len(ids_form) != 1 or ids_form[0] != ids[0]:
            return None
        return f"https://{DRIVE}/download?" + urlencode(pares)
    return None


def _titulo(html: str) -> str:
    import re
    m = re.search(r"<title>([^<]{0,80})", html)
    return m.group(1).strip() if m else "sin título"


def bajar_https(url: str, destino: Path, *, abrir=None, tope: int = TOPE_BYTES, _reintento: bool = True) -> Path:
    """Streaming a disco. Exige https y `Content-Type` de video; corta al pasar el tope.
    Con Google Drive, si llega su página de aviso, sigue una vez su formulario de confirmación
    (mismo host y mismo id; nunca otro destino)."""
    if not _es_https(url):
        raise ErrorDescarga(f"URL no https: {url!r}")
    abrir = abrir or _abrir_urllib
    try:
        with abrir(url) as r:
            tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if tipo == "text/html" and _host(url) == DRIVE:
                pagina = r.read(200_000).decode("utf-8", errors="replace")
                siguiente = _aviso_drive(url, pagina) if _reintento else None
                if siguiente:
                    return bajar_https(siguiente, destino, abrir=abrir, tope=tope, _reintento=False)
                raise ErrorDescarga(f"Google Drive devolvió una página y no el video ({_titulo(pagina)!r})")
            if not tipo.startswith("video/"):
                raise ErrorDescarga(f"Content-Type {tipo or 'ausente'!r}: no es video")
            largo = r.headers.get("Content-Length")
            if largo and str(largo).isdigit() and int(largo) > tope:
                raise ErrorDescarga(f"el archivo pesa {int(largo)} bytes y el tope es {tope}")
            total = 0
            with open(destino, "wb") as f:
                while True:
                    bloque = r.read(BLOQUE)
                    if not bloque:
                        break
                    total += len(bloque)
                    if total > tope:
                        raise ErrorDescarga(f"la descarga pasó el tope de {tope} bytes")
                    f.write(bloque)
    except ErrorDescarga:
        destino.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as e:
        destino.unlink(missing_ok=True)
        if 300 <= e.code < 400:
            raise ErrorDescarga(f"redirección HTTP {e.code} rechazada: no se siguen redirecciones") from None
        raise ErrorDescarga(f"HTTP {e.code}") from None
    except OSError as e:   # incluye URLError y tiempos de espera
        destino.unlink(missing_ok=True)
        raise ErrorDescarga(f"fallo de conexión: {type(e).__name__}") from None
    if total == 0:
        destino.unlink(missing_ok=True)
        raise ErrorDescarga("la descarga llegó vacía")
    return destino


def orden_ytdlp(url: str, carpeta: Path) -> list[str]:
    """Orden de yt-dlp: un solo clip, sin cookies ni configuración, mp4 de 1080p o menos, 120 s como máximo."""
    return ["yt-dlp", "--ignore-config", "--no-playlist", "--no-cookies", "--no-part",
            "--match-filter", f"duration<={TOPE_TWITCH_S}", "--max-filesize", str(TOPE_BYTES),
            "-f", FORMATO_TWITCH, "--merge-output-format", "mp4",
            "-o", str(carpeta / "clip.%(ext)s"), "--", url]


def bajar_twitch(url: str, carpeta: Path, *, ejecutor=None) -> Path:
    ejecutor = ejecutor or subprocess.run
    if puertas._id_de_clip_twitch(url) is None:
        raise ErrorDescarga(f"no es la URL de un clip de Twitch: {url!r}")
    try:
        r = ejecutor(orden_ytdlp(url, carpeta), capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ErrorDescarga(f"yt-dlp no corrió: {type(e).__name__}") from None
    if r.returncode != 0:
        raise ErrorDescarga(f"yt-dlp falló (código {r.returncode})")
    salidas = sorted(carpeta.glob("clip.*"))
    if not salidas:
        raise ErrorDescarga("yt-dlp no dejó ningún archivo (¿duración sobre el tope de 120 s?)")
    return salidas[0]


def _autorizar(ficha, candidato, hoy: date) -> None:
    url = candidato.url
    ok, motivo = fuentes.autoriza_descarga(ficha, hoy, "produccion", url, pilar=candidato.pilar)
    if not ok:
        raise ErrorDescarga(f"descarga no autorizada: {motivo}")
    ok, motivo = fuentes.puede_descargar(ficha)
    if not ok:
        raise ErrorDescarga(f"descarga no permitida: {motivo}")
    if candidato.origen == "twitch":
        if (ficha.get("reglas") or {}).get("permite_descarga") is not True:
            raise ErrorDescarga("clip de Twitch sin reglas.permite_descarga: true; sólo enlace, no se descarga")
        if _es_youtube(url):
            raise ErrorDescarga("nunca se descarga de YouTube")
    elif _es_youtube(url):
        raise ErrorDescarga("nunca se descarga de YouTube")


@contextmanager
def descargar(raiz, candidato, ficha: dict | None, hoy: date, *, abrir=None, ejecutor=None):
    """Autoriza, descarga y mide. Entrega `Descarga` dentro del `with`; borra la carpeta al salir.

    Lanza `ErrorDescarga` con el motivo si algo no cumple.
    """
    _autorizar(ficha, candidato, hoy)         # antes de abrir ninguna conexión
    carpeta = Path(tempfile.mkdtemp(prefix="granja-descarga-"))
    try:
        if candidato.origen == "twitch":
            ruta = bajar_twitch(candidato.url, carpeta, ejecutor=ejecutor)
        else:
            from . import release   # import perezoso: release importa este módulo
            if abrir is None and release.es_url_release(candidato.url):   # material privado en el release
                ruta = release.bajar(candidato.url, carpeta / "origen.mp4")
            else:
                ruta = bajar_https(candidato.url, carpeta / "origen.mp4", abrir=abrir)
        puertas.anotar_visto(raiz, candidato.id_origen)
        try:
            duracion = float(sondear_video(ruta)["duracion"])
        except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, OSError) as e:
            raise ErrorDescarga(f"ffprobe no pudo medir el archivo ({type(e).__name__})") from None
        # `reglas.duracion_s` es la duración del clip PUBLICADO: Gemini elige un recorte dentro de ese rango.
        # A la fuente sólo se le exige que alcance el mínimo y que no pase de TOPE_FUENTE_S.
        minimo = ((ficha.get("reglas") or {}).get("duracion_s") or [0])[0]
        if not math.isfinite(duracion) or duracion <= 0:
            raise ErrorDescarga("ffprobe devolvió una duración no válida")
        if duracion < float(minimo):
            raise ErrorDescarga(f"el archivo dura {duracion:.1f} s, menos que el mínimo de {minimo} s del clip")
        if duracion > TOPE_FUENTE_S:
            raise ErrorDescarga(f"el archivo dura {duracion:.1f} s y el tope de una fuente es {TOPE_FUENTE_S:.0f} s")
        yield Descarga(ruta, duracion)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)

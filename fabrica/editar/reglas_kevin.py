"""Reglas de Kevin (tarea #18): cada queja escrita en `reglas-kevin.yml` y, si se puede medir, su revisión en la puerta.

`cargar` valida el archivo; `puntos` devuelve los puntos sí/no que `estilo.revisar` añade al final; las reglas
vigentes sin revisión no frenan nada y salen como `notas_para_criticos`. La puerta falla cerrada: un archivo roto
o una revisión desconocida ponen un punto en rojo.
"""
from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import yaml

from .. import plazo
from ..config import raiz_repo

ARCHIVO = "reglas-kevin.yml"
CAMPOS = ("id", "fecha", "queja", "regla", "revision", "estado")
ESTADOS = ("vigente", "retirada")
RE_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PUNTO_ARCHIVO = "[regla-kevin] reglas-kevin.yml válido"

MEDIA_SILENCIO_DB = -55.0     # volumen medio: por debajo, la toma no se oye aunque tenga algún pico
INSTANTES = 6                 # instantes repartidos para medir el movimiento de una toma con video
MINIATURA_ANCHO = 64          # px de ancho de las miniaturas en gris con que se compara
# Calibrado el 20 nov 2026 con ffmpeg 6.1 y el método de abajo (6 instantes entre 0,3 y 4,3 s, miniaturas de 64 px,
# diferencia media absoluta en gris 0-255, mayor de los 5 pares), todo en H.264 ultrafast 1080x1920:
#   imagen fija (testsrc2, un solo cuadro)                      0,00
#   imagen fija con ruido temporal fuerte (noise 8) a 800 kb/s   0,62
#   color liso con ruido fuerte (noise 20) a 600 kb/s            0,17
#   testsrc2 a la mitad de velocidad / un cambio por segundo     1,38
#   testsrc2 normal                                              9,60
#   mandelbrot en zoom                                          17,49
# El umbral 1,0 queda sobre el peor ruido de compresión medido (0,62) y bajo el movimiento más lento (1,38).
UMBRAL_MOVIMIENTO_TOMA = 1.0
FRANJA_MAX_PX = 16            # una franja de más de 16 px en cualquier borde es una franja


def _fecha_valida(v) -> bool:
    if isinstance(v, date):
        return True
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            date.fromisoformat(v)
            return True
        except ValueError:
            return False
    return False


def cargar(raiz=None) -> list[dict]:
    """Reglas de `reglas-kevin.yml` ya validadas. ValueError con el motivo exacto si algo falla."""
    ruta = raiz_repo(raiz) / ARCHIVO
    if not ruta.is_file():
        raise ValueError(f"falta el archivo {ARCHIVO}")
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{ARCHIVO} no es YAML válido: {str(e).splitlines()[0] if str(e) else e}") from e
    if not isinstance(datos, dict):
        raise ValueError(f"{ARCHIVO} debe ser un diccionario con version y reglas")
    if datos.get("version") != 1:
        raise ValueError(f"{ARCHIVO}: version debe ser 1")
    reglas = datos.get("reglas")
    if not isinstance(reglas, list):
        raise ValueError(f"{ARCHIVO}: reglas debe ser una lista")
    vistos: set[str] = set()
    for i, r in enumerate(reglas, 1):
        if not isinstance(r, dict):
            raise ValueError(f"{ARCHIVO}: la regla {i} debe ser un diccionario")
        et = f"regla {r.get('id') if isinstance(r.get('id'), str) and r.get('id') else i}"
        for c in CAMPOS:
            if c not in r:
                raise ValueError(f"{et}: falta el campo {c}")
            if c != "revision" and (r[c] is None or (isinstance(r[c], str) and not r[c].strip())):
                raise ValueError(f"{et}: el campo {c} está vacío")
        if not isinstance(r["id"], str) or not RE_ID.match(r["id"]):
            raise ValueError(f"{et}: id mal formado (minúsculas, números y guiones): {r['id']!r}")
        if r["id"] in vistos:
            raise ValueError(f"{et}: id repetido")
        vistos.add(r["id"])
        if not _fecha_valida(r["fecha"]):
            raise ValueError(f"{et}: fecha inválida {r['fecha']!r} (AAAA-MM-DD)")
        for c in ("queja", "regla"):
            if not isinstance(r[c], str):
                raise ValueError(f"{et}: el campo {c} debe ser texto")
        if r["estado"] not in ESTADOS:
            raise ValueError(f"{et}: estado desconocido {r['estado']!r} (vigente o retirada)")
        if r["revision"] is not None and (not isinstance(r["revision"], str) or r["revision"] not in REVISIONES):
            raise ValueError(f"{et}: revision desconocida {r['revision']!r} (no está en REVISIONES: "
                             f"{', '.join(sorted(REVISIONES))})")
    return reglas


# --------------------------------------------------------------------------------------------- medidas

def _volumenes(video: Path) -> tuple[float | None, float | None, str | None]:
    """(pico dB, volumen medio dB, motivo del error) con volumedetect."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=plazo.tiempo_para(120))
    if r.returncode != 0:
        return None, None, f"ffmpeg falló (código {r.returncode})"
    mx = re.search(r"max_volume:\s*(-?[0-9.]+|-inf)\s*dB", r.stderr)
    me = re.search(r"mean_volume:\s*(-?[0-9.]+|-inf)\s*dB", r.stderr)
    if not (mx and me):
        return None, None, "el MP4 no tiene audio medible"
    return float(mx.group(1)), float(me.group(1)), None


def _sin_musica(plan: dict, ficha: dict | None) -> bool:
    return bool(((ficha or {}).get("reglas") or {}).get("sin_musica")
                or (plan.get("campana") or {}).get("sin_musica"))


def _fuente_muda(plan: dict) -> bool:
    origen = plan.get("origen") or {}
    if not origen:
        return True
    if origen.get("audio") == "silencio" or (plan.get("campana") or {}).get("audio") == "silencio":
        return True
    from . import estilo
    ruta = Path(str(origen.get("archivo", "")))
    return ruta.is_file() and estilo._pistas_de_audio(ruta) == 0


def _audio(video: Path, plan: dict, ficha: dict | None) -> tuple[bool, str]:
    from . import estilo
    if _sin_musica(plan, ficha) and _fuente_muda(plan):
        return True, "audio no aplica (sin_musica y fuente sin audio: manda el brief)"
    pico, medio, error = _volumenes(Path(video))
    if pico is None:
        return False, f"audio: {error}"
    ok = pico > estilo.UMBRAL_SILENCIO_DB and medio > MEDIA_SILENCIO_DB
    return ok, (f"audio con pico {pico:g} dB (mínimo {estilo.UMBRAL_SILENCIO_DB:g}) y medio {medio:g} dB "
                f"(mínimo {MEDIA_SILENCIO_DB:g})")


def _miniatura(img, ancho: int, alto: int, libre: np.ndarray | None):
    from . import estilo
    g = estilo._miniatura_gris(img, ancho, alto)
    if libre is None:
        return g, np.ones(g.shape, dtype=bool)
    from PIL import Image
    m = Image.fromarray((libre * 255).astype(np.uint8)).resize((ancho, alto), Image.NEAREST)
    return g, np.asarray(m) > 127


def _movimiento_con_video(video: Path, plan: dict, marca) -> tuple[bool, str]:
    from . import estilo
    dur = float(plan.get("duracion") or 0)
    fin = dur - float(marca.m("cierre_s")) - 0.2
    if fin <= 0.3:
        return False, f"movimiento: la toma dura {dur:g} s, no alcanza para medirlo"
    ts = list(np.linspace(0.3, fin, INSTANTES))
    ancho = MINIATURA_ANCHO
    alto = max(1, round(ancho * marca.alto / marca.ancho))
    miniaturas = []
    for t in ts:
        img = estilo._fotograma(Path(video), float(t))
        if img is None:
            return False, f"movimiento: no se pudo leer el fotograma en {t:.2f} s"
        libre = estilo._mascara_placas(plan, float(t), marca) if estilo.es_v2(plan) else None
        miniaturas.append(_miniatura(img, ancho, alto, libre))
    difs = []
    for (ga, ma), (gb, mb) in zip(miniaturas, miniaturas[1:]):
        m = ma & mb
        difs.append(float(np.abs(ga - gb)[m].mean()) if m.any() else 0.0)
    i = int(np.argmax(difs))
    detalle = (f"movimiento: mayor cambio {difs[i]:.2f} entre {ts[i]:.2f} s y {ts[i + 1]:.2f} s "
               f"(mínimo {UMBRAL_MOVIMIENTO_TOMA:g}; pares {', '.join(f'{d:.2f}' for d in difs)})")
    return difs[i] > UMBRAL_MOVIMIENTO_TOMA, detalle


def _movimiento_sin_video(video: Path, plan: dict, marca) -> tuple[bool, str]:
    from . import estilo
    ok, det = estilo.movimiento_visible(Path(video), plan, marca)
    if not ok:
        return False, f"movimiento de entrada: {det}"
    lineas = plan.get("lineas") or []
    if len(lineas) < 2:
        return True, f"movimiento de entrada: {det}; una sola línea de diálogo"
    geo = estilo._geometria(plan, marca)
    if geo is None:
        return False, "movimiento: el plan no trae la geometría del diálogo"
    dur = float(plan.get("duracion") or 0)
    tope = dur - float(marca.m("cierre_s")) - 0.1 if dur > 0 else float("inf")

    def medio(ln):
        return min((float(ln.get("t0", 0)) + float(ln.get("t1", 0))) / 2, tope)
    ta, tb = medio(lineas[0]), medio(lineas[-1])
    y0 = int(geo["y_dialogo"])
    y1 = min(marca.alto, y0 + int(estilo.alto_zona(marca)))
    a, b = estilo._fotograma(Path(video), ta), estilo._fotograma(Path(video), tb)
    if a is None or b is None or a.size != b.size:
        return False, "movimiento: no se pudo leer los fotogramas del diálogo"
    za = np.asarray(a.convert("L").crop((0, y0, marca.ancho, y1)), dtype=np.float32)
    zb = np.asarray(b.convert("L").crop((0, y0, marca.ancho, y1)), dtype=np.float32)
    d = float(np.abs(za - zb).mean())
    return d > estilo.UMBRAL_MOVIMIENTO, (f"{det}; el cuadro cambia {d:.2f} entre la primera línea ({ta:.2f} s) y "
                                          f"la última ({tb:.2f} s) (mínimo {estilo.UMBRAL_MOVIMIENTO:g})")


# ------------------------------------------------------------------------------------------ revisiones

def con_movimiento_y_audio(video: Path, plan: dict, marca, ficha: dict | None) -> tuple[bool, str]:
    """Toda toma se mueve y se oye (el audio no aplica con sin_musica y fuente muda; el movimiento se mide igual)."""
    ok_a, det_a = _audio(video, plan, ficha)
    if plan.get("origen"):
        ok_m, det_m = _movimiento_con_video(video, plan, marca)
    else:
        ok_m, det_m = _movimiento_sin_video(video, plan, marca)
    return ok_a and ok_m, f"{'bien' if ok_a else 'FALLA'} {det_a}; {'bien' if ok_m else 'FALLA'} {det_m}"


def musica_cc0_sin_audio_propio(video: Path, plan: dict, marca, ficha: dict | None) -> tuple[bool, str]:
    """Pieza sin audio propio lleva pista CC0 verificada y el MP4 se oye con ella."""
    from . import estilo
    ok, det = estilo.musica_en_plan(plan, marca, ficha)
    if not ok or estilo.tipo_audio(plan) != "musica":
        return ok, det
    pico, _, error = _volumenes(Path(video))
    if pico is None:
        return False, f"{det}; no se pudo oír el MP4: {error}"
    ok_p = pico > estilo.UMBRAL_SILENCIO_DB
    return ok_p, f"{det}; el MP4 suena con pico {pico:g} dB (mínimo {estilo.UMBRAL_SILENCIO_DB:g})"


def _caja(video: Path, hasta: float) -> tuple[int, int, int, int] | None:
    """(ancho, alto, x, y) de la caja que cropdetect ve en todo el video (reset=0: la unión de todos los cuadros)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-t", f"{hasta:.3f}", "-an", "-vf",
                        "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=plazo.tiempo_para(180))
    cajas = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    return tuple(int(v) for v in cajas[-1]) if cajas else None  # type: ignore[return-value]


def sin_franjas(video: Path, plan: dict, marca, ficha: dict | None) -> tuple[bool, str]:
    """El clip llena el 9:16: plan y bordes con video, y cropdetect sobre el MP4 (sin franjas de más de 16 px)."""
    from . import estilo
    partes: list[str] = []
    ok = True
    if plan.get("origen"):
        ok_pc, det_pc = estilo.pantalla_completa(plan, marca)
        ok &= ok_pc
        partes.append(f"{'bien' if ok_pc else 'FALLA'} plan: {det_pc}")
        if estilo.es_v2(plan):
            ok_bo, det_bo = estilo.bordes_coinciden(Path(video), plan, marca)
            ok &= ok_bo
            partes.append(f"{'bien' if ok_bo else 'FALLA'} bordes: {det_bo}")
    dur = float(plan.get("duracion") or 0)
    # el cierre ocupa el último tramo con su propio fondo: se mide la toma antes de él
    hasta = dur - float(marca.m("cierre_s")) if dur > 0 else 1e6
    caja = _caja(Path(video), hasta)
    if caja is None:
        ok = False
        partes.append("FALLA cropdetect: no dio caja")
    else:
        w, h, x, y = caja
        sobras = {"arriba": y, "abajo": marca.alto - (y + h), "izquierda": x, "derecha": marca.ancho - (x + w)}
        mal = {k: v for k, v in sobras.items() if v > FRANJA_MAX_PX}
        ok &= not mal
        partes.append(f"{'FALLA' if mal else 'bien'} cropdetect: caja {w}x{h} en ({x}, {y}) sobre "
                      f"{marca.ancho}x{marca.alto}" + (f"; franja de {', '.join(f'{k} {v} px' for k, v in mal.items())}"
                                                       f" (máximo {FRANJA_MAX_PX})" if mal else ""))
    return ok, "; ".join(partes)


REVISIONES = {
    "con_movimiento_y_audio": con_movimiento_y_audio,
    "musica_cc0_sin_audio_propio": musica_cc0_sin_audio_propio,
    "sin_franjas": sin_franjas,
}


# ----------------------------------------------------------------------------------------------- puerta

def puntos(video: Path, plan: dict, marca, ficha: dict | None = None) -> list[tuple[str, bool, str]]:
    """[(nombre, ok, detalle)] de las reglas vigentes con revisión; un solo punto rojo si el archivo no carga."""
    try:
        reglas = cargar(marca.raiz)
    except ValueError as e:
        return [(PUNTO_ARCHIVO, False, str(e))]
    res = []
    for r in reglas:
        if r["estado"] != "vigente" or not r["revision"]:
            continue
        nombre = f"[regla-kevin] {r['id']}: {r['regla']}"
        try:
            ok, det = REVISIONES[r["revision"]](Path(video), plan, marca, ficha)
        except Exception as e:  # la puerta falla cerrada: una medida que revienta es un rojo, no un salto
            ok, det = False, f"la revisión {r['revision']} falló: {type(e).__name__}: {e}"
        res.append((nombre, bool(ok), det))
    return res


def notas_para_criticos(raiz=None) -> str:
    """Reglas vigentes sin revisión, una por línea (`<id>: <regla> (queja: «…», fecha)`); '' si no hay o no carga."""
    try:
        reglas = cargar(raiz)
    except ValueError:
        return ""
    return "\n".join(f"{r['id']}: {r['regla']} (queja: «{r['queja']}», {r['fecha']})"
                     for r in reglas if r["estado"] == "vigente" and not r["revision"])

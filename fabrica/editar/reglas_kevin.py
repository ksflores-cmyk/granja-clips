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
PREVIO_PANTALLA = "[plan] Pantalla completa (cuerpo 1080x1920, sin franjas)"   # nombres de los puntos que estilo.revisar ya calculó
PREVIO_BORDES = "[fotograma] Bordes coinciden con la fuente (sin franjas)"
PUNTO_ARCHIVO = "[regla-kevin] reglas-kevin.yml válido"

MEDIA_SILENCIO_DB = -55.0     # volumen medio: por debajo, la toma no se oye aunque tenga algún pico
INSTANTES = 6                 # instantes repartidos para medir el movimiento de una toma con video
MINIATURA_ANCHO = 64          # px de ancho de las miniaturas en gris con que se compara
# Calibrado el 21 nov 2026 (ronda 3, método de una pasada) con ffmpeg 6.1: cuadros de 64x114 en gris con
# scale=flags=area, 6 instantes entre 0,3 y 4,3 s (cuadro round(t*30)), diferencia media absoluta 0-255, mayor de los
# 5 pares, todo en H.264 ultrafast 1080x1920:
#   imagen fija (testsrc2, un solo cuadro)                        0,00
#   imagen fija con ruido temporal fuerte (noise 8 y 12), 800 kb/s  0,81 (el peor; sólo el primer par, luego baja)
#   color liso con ruido fuerte (noise 20) a 600 kb/s              0,02
#   testsrc2 a la mitad de velocidad / un cambio por segundo        1,12
#   testsrc2 normal                                                9,35
#   mandelbrot en zoom                                            17,00
# (Con el método anterior, 6 `_fotograma` a 1080x1920 y reducción con PIL, daba 0,62 / 1,38.)
# El umbral 0,95 queda entre el peor ruido (0,81) y el movimiento más lento medido (1,12).
UMBRAL_MOVIMIENTO_TOMA = 0.95
FPS = 30                      # la puerta exige 30 fps: el instante t es el cuadro round(t * 30)
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

class Medidas:
    """Lo que las revisiones miden sobre un MP4, en una sola pasada de ffmpeg y guardado por (ruta, mtime, tamaño).

    La pasada decodifica el video una vez y lo reparte a tres salidas: `volumedetect` (audio), `cropdetect` (video
    hasta `hasta` s) y los fotogramas a 64 px en gris (rawvideo por stdout) de donde salen los instantes de movimiento.
    `previos` son los resultados que `estilo.revisar` ya calculó ({nombre del punto: (ok, detalle)}).
    """

    def __init__(self, previos: dict | None = None) -> None:
        self.previos = previos or {}
        self._cache: dict = {}

    def pasada(self, video: Path, hasta: float, ancho: int, alto: int, con_cuadros: bool) -> dict:
        v = Path(video)
        st = v.stat()
        clave = (str(v), st.st_mtime_ns, st.st_size, round(hasta, 3), ancho, alto)
        hallado = self._cache.get(clave)
        if hallado is not None and (hallado["cuadros"] is not None or not con_cuadros):
            return hallado
        from . import estilo
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(v)]
        tope = ["-t", f"{hasta:.3f}"] if hasta < 1e5 else []
        crop = "cropdetect=limit=24:round=2:reset=0"
        if con_cuadros:   # un solo decodificador: el video se reparte con split a cropdetect y a la miniatura
            cmd += ["-filter_complex", f"[0:v:0]split=2[a][b];[a]{crop}[c];[b]scale={ancho}:{alto}:flags=area,format=gray[g]",
                    "-map", "[c]", *tope, "-f", "null", "-", "-map", "[g]", *tope, "-f", "rawvideo", "pipe:1"]
        else:
            cmd += ["-map", "0:v:0", *tope, "-an", "-vf", crop, "-f", "null", "-"]
        if estilo._pistas_de_audio(v) > 0:
            cmd += ["-map", "0:a:0", "-vn", "-af", "volumedetect", "-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=plazo.tiempo_para(180))
        err = r.stderr.decode("utf-8", "replace")
        res = {"error": None, "pico": None, "medio": None, "caja": None, "cuadros": None}
        if r.returncode != 0:
            res["error"] = f"ffmpeg falló (código {r.returncode})"
        mx = re.search(r"max_volume:\s*(-?[0-9.]+|-inf)\s*dB", err)
        me = re.search(r"mean_volume:\s*(-?[0-9.]+|-inf)\s*dB", err)
        if mx and me:
            res["pico"], res["medio"] = float(mx.group(1)), float(me.group(1))
        # Con un video todo negro ffmpeg da ancho y alto negativos (crop=-1078:-1918:…): se devuelven tal cual.
        cajas = re.findall(r"crop=(-?\d+):(-?\d+):(-?\d+):(-?\d+)", err)
        if cajas:
            res["caja"] = tuple(int(x) for x in cajas[-1])
        if con_cuadros and r.returncode == 0:
            n = len(r.stdout) // (ancho * alto)
            res["cuadros"] = np.frombuffer(r.stdout[:n * ancho * alto], dtype=np.uint8).reshape(n, alto, ancho)
        self._cache[clave] = res
        return res


def _medidas(medidas: "Medidas | None") -> "Medidas":
    return medidas if medidas is not None else Medidas()


def _pasada(video: Path, plan: dict, marca, medidas: "Medidas | None", con_cuadros: bool = False) -> dict:
    """La pasada única del MP4 (cropdetect hasta antes del cierre; el cierre trae su propio fondo)."""
    dur = float(plan.get("duracion") or 0)
    hasta = dur - float(marca.m("cierre_s")) if dur > 0 else 1e6
    alto = max(1, round(MINIATURA_ANCHO * marca.alto / marca.ancho))
    return _medidas(medidas).pasada(Path(video), hasta, MINIATURA_ANCHO, alto, con_cuadros)


def _volumenes(video: Path, plan: dict, marca, medidas: "Medidas | None" = None
               ) -> tuple[float | None, float | None, str | None]:
    """(pico dB, volumen medio dB, motivo del error) con volumedetect."""
    m = _pasada(video, plan, marca, medidas)
    if m["error"]:
        return None, None, m["error"]
    if m["pico"] is None:
        return None, None, "el MP4 no tiene audio medible"
    return m["pico"], m["medio"], None


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


def _audio(video: Path, plan: dict, ficha: dict | None, marca, med: "Medidas | None" = None) -> tuple[bool, str]:
    from . import estilo
    if _sin_musica(plan, ficha) and _fuente_muda(plan):
        return True, "audio no aplica (sin_musica y fuente sin audio: manda el brief)"
    pico, medio, error = _volumenes(Path(video), plan, marca, med)
    if pico is None:
        return False, f"audio: {error}"
    ok = pico > estilo.UMBRAL_SILENCIO_DB and medio > MEDIA_SILENCIO_DB
    return ok, (f"audio con pico {pico:g} dB (mínimo {estilo.UMBRAL_SILENCIO_DB:g}) y medio {medio:g} dB "
                f"(mínimo {MEDIA_SILENCIO_DB:g})")


def _mascara_miniatura(libre: np.ndarray, ancho: int, alto: int) -> np.ndarray:
    from PIL import Image
    m = Image.fromarray((libre * 255).astype(np.uint8)).resize((ancho, alto), Image.NEAREST)
    return np.asarray(m) > 127


def _movimiento_con_video(video: Path, plan: dict, marca, med: "Medidas | None" = None) -> tuple[bool, str]:
    from . import estilo
    dur = float(plan.get("duracion") or 0)
    fin = dur - float(marca.m("cierre_s")) - 0.2
    if fin <= 0.3:
        return False, f"movimiento: la toma dura {dur:g} s, no alcanza para medirlo"
    ts = list(np.linspace(0.3, fin, INSTANTES))
    m = _pasada(video, plan, marca, med, con_cuadros=True)
    cuadros = m["cuadros"]
    if cuadros is None or len(cuadros) == 0:
        return False, f"movimiento: no se pudieron leer los fotogramas ({m['error'] or 'sin cuadros'})"
    n, alto, ancho = cuadros.shape
    miniaturas = []
    for t in ts:
        i = min(n - 1, int(round(t * FPS)))
        libre = (_mascara_miniatura(estilo._mascara_placas(plan, float(t), marca), ancho, alto)
                 if estilo.es_v2(plan) else np.ones((alto, ancho), dtype=bool))
        miniaturas.append((cuadros[i].astype(np.float32), libre))
    difs = []
    for (ga, ma), (gb, mb) in zip(miniaturas, miniaturas[1:]):
        mk = ma & mb
        difs.append(float(np.abs(ga - gb)[mk].mean()) if mk.any() else 0.0)
    i = int(np.argmax(difs))
    detalle = (f"movimiento: mayor cambio {difs[i]:.2f} entre {ts[i]:.2f} s y {ts[i + 1]:.2f} s "
               f"(mínimo {UMBRAL_MOVIMIENTO_TOMA:g}; pares {', '.join(f'{d:.2f}' for d in difs)})")
    return difs[i] > UMBRAL_MOVIMIENTO_TOMA, detalle


def _movimiento_sin_video(video: Path, plan: dict, marca) -> tuple[bool, str]:
    from . import estilo
    if not plan.get("lineas"):
        return False, "movimiento: pieza sin video y sin líneas de diálogo, no hay nada que se mueva"
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

def con_movimiento_y_audio(video: Path, plan: dict, marca, ficha: dict | None,
                           med: "Medidas | None" = None) -> tuple[bool, str]:
    """Toda toma se mueve y se oye (el audio no aplica con sin_musica y fuente muda; el movimiento se mide igual)."""
    med = _medidas(med)
    if plan.get("origen"):
        _pasada(video, plan, marca, med, con_cuadros=True)   # una sola pasada para audio, caja y movimiento
    ok_a, det_a = _audio(video, plan, ficha, marca, med)
    if plan.get("origen"):
        ok_m, det_m = _movimiento_con_video(video, plan, marca, med)
    else:
        ok_m, det_m = _movimiento_sin_video(video, plan, marca)
    return ok_a and ok_m, f"{'bien' if ok_a else 'FALLA'} {det_a}; {'bien' if ok_m else 'FALLA'} {det_m}"


def musica_cc0_sin_audio_propio(video: Path, plan: dict, marca, ficha: dict | None,
                                med: "Medidas | None" = None) -> tuple[bool, str]:
    """Pieza sin audio propio lleva pista CC0 verificada y el MP4 se oye con ella."""
    from . import estilo
    ok, det = estilo.musica_en_plan(plan, marca, ficha)
    if not ok or estilo.tipo_audio(plan) != "musica":
        return ok, det
    pico, _, error = _volumenes(Path(video), plan, marca, med)
    if pico is None:
        return False, f"{det}; no se pudo oír el MP4: {error}"
    ok_p = pico > estilo.UMBRAL_SILENCIO_DB
    return ok_p, f"{det}; el MP4 suena con pico {pico:g} dB (mínimo {estilo.UMBRAL_SILENCIO_DB:g})"


def sin_franjas(video: Path, plan: dict, marca, ficha: dict | None,
                med: "Medidas | None" = None) -> tuple[bool, str]:
    """El clip llena el 9:16: plan y bordes con video, y cropdetect sobre el MP4 (sin franjas de más de 16 px)."""
    from . import estilo
    partes: list[str] = []
    ok = True
    if plan.get("origen"):
        med = _medidas(med)
        ok_pc, det_pc = med.previos.get(PREVIO_PANTALLA) or estilo.pantalla_completa(plan, marca)
        ok &= ok_pc
        partes.append(f"{'bien' if ok_pc else 'FALLA'} plan: {det_pc}")
        if estilo.es_v2(plan):
            ok_bo, det_bo = med.previos.get(PREVIO_BORDES) or estilo.bordes_coinciden(Path(video), plan, marca)
            ok &= ok_bo
            partes.append(f"{'bien' if ok_bo else 'FALLA'} bordes: {det_bo}")
    # el cierre ocupa el último tramo con su propio fondo: la pasada mide la toma antes de él
    caja = _pasada(Path(video), plan, marca, med)["caja"]
    if caja is None:
        ok = False
        partes.append("FALLA cropdetect: no dio caja")
    elif caja[0] <= 0 or caja[1] <= 0:
        ok = False
        partes.append(f"FALLA cropdetect: el video es todo negro (caja {caja[0]}x{caja[1]})")
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

def puntos(video: Path, plan: dict, marca, ficha: dict | None = None,
           previos: dict | None = None) -> list[tuple[str, bool, str]]:
    """[(nombre, ok, detalle)] de las reglas vigentes con revisión; un solo punto rojo si el archivo no carga."""
    try:
        reglas = cargar(marca.raiz)
    except ValueError as e:
        return [(PUNTO_ARCHIVO, False, str(e))]
    res = []
    med = Medidas(previos)    # una pasada de ffmpeg por MP4, compartida por las revisiones de esta llamada
    if plan.get("origen") and any(r["estado"] == "vigente" and r["revision"] == "con_movimiento_y_audio"
                                  for r in reglas):
        try:
            _pasada(Path(video), plan, marca, med, con_cuadros=True)   # con los cuadros desde el principio
        except (plazo.SinTiempo, subprocess.TimeoutExpired, OSError):
            raise
        except Exception:
            pass   # el error sale, con su motivo, en la revisión que lo necesita
    for r in reglas:
        if r["estado"] != "vigente" or not r["revision"]:
            continue
        nombre = f"[regla-kevin] {r['id']}: {r['regla']}"
        try:
            ok, det = REVISIONES[r["revision"]](Path(video), plan, marca, ficha, med)
        except (plazo.SinTiempo, subprocess.TimeoutExpired, OSError):
            # Fallo de tiempo o de la máquina, no del material: sube para que `toma.py` no gaste la fuente.
            raise
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

"""Corte sin IA: el plan B cuando Gemini no está disponible (caída, sin tiempo, sin clave, modelos 404).

Arma un diccionario con la MISMA forma que la respuesta de Gemini para que pase por `ojos.validar_respuesta`,
`toma.armar`, `componer` y la puerta de estilo de siempre. Sin traducción inventada: `lineas` va vacío y la
acotación en blanco. El recorte se elige por picos de volumen y cambios de escena; el gancho sale del título.

Sólo se usa cuando Gemini NO respondió. Si respondió y vetó (apto false, spoiler, validación roja), eso es un
descarte y aquí no se entra. Es determinista: el mismo video da el mismo recorte.

El análisis (ffmpeg) y el puntaje (`elegir_inicio`) están separados: el puntaje es una función pura de listas
de números y se prueba sin ffmpeg.
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import ojos, plazo
from .lienzo import PUNTUACION, partir
from .marca import Marca

PASO_S = 0.5                    # ventana de volumen
UMBRAL_ESCENA = 0.3
BONO_ESCENA = 1.5               # puntos que vale cada cambio de escena dentro del recorte (un pico fuerte vale ~3)
AJUSTE_ESCENA_S = 1.5           # el inicio se mueve al cambio de escena más cercano dentro de ±1.5 s
PICO_MIN_INICIO_S = 1.0         # el pico más alto no cae en el primer segundo...
PICO_MIN_FINAL_S = 2.0          # ...ni en los dos últimos
OBJETIVO_S = 20.0
TIEMPO_FFMPEG_S = 120
EXTENSIONES = r"\.(?:mp4|mov|mkv|webm|avi|m4v|gif|jpg|jpeg|png)\b"
CONSERVAR = set("'’-.,!?¿¡:…")


@dataclass
class Construccion:
    """Resultado de armar el corte sin IA. `datos` es None si hay que descartar (con `motivo`)."""
    datos: dict | None
    motivo: str | None = None
    gastar: bool = False        # el fallo es del material (video corto, título inservible): la fuente se gasta


# ------------------------------------------------------------------ análisis (ffmpeg → números)

def analizar_video(ruta: Path) -> dict:
    """{"rms": [RMS por ventana de 0.5 s], "escenas": [segundos de cada cambio de escena]}. ffmpeg sobre `ruta`.

    Sin pista de audio, `rms` queda vacío (sólo cuentan las escenas). Un ffmpeg que no puede leer el video
    levanta `subprocess.CalledProcessError`."""
    audio = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(ruta), "-vn", "-ac", "1",
                            "-ar", "8000", "-f", "s16le", "-"], capture_output=True,
                           timeout=plazo.tiempo_para(TIEMPO_FFMPEG_S))
    rms: list[float] = []
    if audio.returncode == 0 and audio.stdout:
        x = np.frombuffer(audio.stdout[: len(audio.stdout) // 2 * 2], dtype="<i2").astype(np.float64)
        por_ventana = int(8000 * PASO_S)
        for i in range(0, len(x), por_ventana):
            trozo = x[i:i + por_ventana]
            rms.append(float(np.sqrt(np.mean(trozo * trozo))))
    video = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(ruta), "-an", "-vf",
                            f"select='gt(scene,{UMBRAL_ESCENA})',showinfo", "-f", "null", "-"],
                           check=True, capture_output=True, text=True, errors="replace",
                           timeout=plazo.tiempo_para(TIEMPO_FFMPEG_S))
    escenas = sorted({round(float(t), 3) for t in re.findall(r"pts_time:(\d+(?:\.\d+)?)", video.stderr)})
    return {"rms": rms, "escenas": escenas}


# ------------------------------------------------------------------ puntaje (función pura)

def _zscores(rms: list[float]) -> np.ndarray:
    x = np.asarray(rms, dtype=np.float64)
    if x.size == 0 or float(x.std()) == 0.0:
        return np.zeros(x.size)
    return (x - x.mean()) / x.std()


def _pico_en_sitio(z: np.ndarray, inicio: float, objetivo: float, paso: float) -> bool:
    """El pico más alto del recorte no cae en el primer segundo ni en los dos últimos. Sin pico, vale."""
    i0 = int(round(inicio / paso))
    trozo = z[i0:i0 + int(round(objetivo / paso))]
    if trozo.size == 0 or float(trozo.max()) <= 0.0:
        return True
    desde = int(np.argmax(trozo)) * paso
    return desde >= PICO_MIN_INICIO_S and desde + paso <= objetivo - PICO_MIN_FINAL_S


def elegir_inicio(rms: list[float], escenas: list[float], duracion: float, objetivo: float,
                  paso: float = PASO_S) -> float:
    """Inicio (s) de la mejor ventana de `objetivo` s. Puntaje = suma de los z-score positivos del RMS dentro
    + BONO_ESCENA por cada cambio de escena dentro. Empates: gana el inicio más temprano."""
    z = _zscores(rms)
    pos = np.maximum(z, 0.0)
    ultimo = max(0.0, duracion - objetivo)
    inicios = sorted({round(i * paso, 6) for i in range(int(ultimo / paso + 1e-9) + 1)} | {round(ultimo, 6)})
    k = int(round(objetivo / paso))
    puntajes: list[tuple[float, float]] = []
    for t in inicios:
        i0 = int(round(t / paso))
        p = float(pos[i0:i0 + k].sum()) + BONO_ESCENA * sum(1 for e in escenas if t <= e < t + objetivo)
        puntajes.append((p, t))
    validos = [(p, t) for p, t in puntajes if _pico_en_sitio(z, t, objetivo, paso)] or puntajes
    mejor = max(p for p, _ in validos)
    inicio = next(t for p, t in validos if p == mejor)
    cerca = [e for e in escenas if abs(e - inicio) <= AJUSTE_ESCENA_S and 0.0 <= e <= ultimo]
    if cerca:
        ajustado = min(cerca, key=lambda e: (abs(e - inicio), e))
        if _pico_en_sitio(z, ajustado, objetivo, paso):
            inicio = ajustado
    return round(inicio, 3)


def elegir_recorte(ruta: Path, rango: tuple[float, float], objetivo_s: float = OBJETIVO_S,
                   duracion: float | None = None, analizar=None) -> tuple[float, float] | None:
    """(inicio, fin) del recorte, o None si el video dura menos que el mínimo del rango.

    El largo es `objetivo_s` acotado al rango de la ficha y a la duración. `ruta` es la copia ligera."""
    lo, hi = rango
    datos = (analizar or analizar_video)(Path(ruta))
    if duracion is None:
        duracion = len(datos["rms"]) * PASO_S
    if duracion < lo:
        return None
    largo = round(max(lo, min(float(objetivo_s), hi, duracion)), 3)
    inicio = elegir_inicio(datos["rms"], datos["escenas"], duracion, largo)
    return inicio, round(inicio + largo, 3)


# ------------------------------------------------------------------ textos

def limpiar_titulo(titulo: str) -> str:
    """Quita URLs, #hashtags, @menciones, extensión de archivo, guiones bajos, emojis y símbolos."""
    t = re.sub(r"(?:https?://|www\.)\S+", " ", str(titulo or ""))
    t = re.sub(r"[#@]\w+", " ", t)
    t = re.sub(EXTENSIONES, " ", t, flags=re.IGNORECASE)
    t = t.replace("_", " ")
    t = "".join(c if (unicodedata.category(c)[0] in "LNM" or c in CONSERVAR or c.isspace()) else " " for c in t)
    return " ".join(t.split()).strip(" -.,:;")


def _cabe_gancho(marca: Marca, gancho: str) -> bool:
    try:
        return len(partir(gancho.upper(), marca.fuente("bold", marca.m("tam_gancho")),
                          marca.ancho - 2 * marca.m("margen"))) <= 2
    except ValueError:
        return False


def _problema_gancho(gancho: str, marca: Marca, temporada: dict) -> str | None:
    """Por qué un gancho no sirve, o None si sirve."""
    palabras = gancho.split()
    if len(palabras) < 2:
        return "menos de 2 palabras"
    if any(p.strip(PUNTUACION).isdigit() for p in palabras):
        return "trae palabras que son sólo números"
    if len(palabras) > marca.max_palabras_gancho:
        return f"más de {marca.max_palabras_gancho} palabras"
    if not _cabe_gancho(marca, gancho):
        return "no cabe en 2 líneas"
    for g in marca.ganchos_prohibidos():
        if ojos._sin_acentos(g) in ojos._sin_acentos(gancho):
            return f"gancho genérico prohibido ({g})"
    for p in temporada.get("palabras_evitar", []):
        if ojos._contiene(gancho, str(p)):
            return f"palabra a evitar ({p})"
    return None


def palabra_lapiz_de(gancho: str) -> str:
    """La palabra más larga del gancho, sin puntuación (la primera si hay empate)."""
    palabras = [p.strip(PUNTUACION) for p in gancho.split()]
    return max((p for p in palabras if p), key=len)


def gancho_de(titulo: str, ficha: dict | None, marca: Marca,
              temporada: dict) -> tuple[str | None, str | None, bool]:
    """(gancho, None, False) o (None, motivo de descarte, definitivo). Del título; si no sirve, `reglas.gancho_sin_ia`
    de la ficha. `definitivo` es True si el título trae una palabra a evitar (el video no mejora con otro intento)."""
    for p in temporada.get("palabras_evitar", []):
        if ojos._contiene(str(titulo or ""), str(p)):
            return None, f"el título contiene {p!r}, palabra a evitar (posible spoiler)", True
    recortado = " ".join(limpiar_titulo(titulo).split()[:marca.max_palabras_gancho])
    problema = _problema_gancho(recortado, marca, temporada)
    if problema is None:
        return recortado, None, False
    respaldo = ((ficha or {}).get("reglas") or {}).get("gancho_sin_ia")
    if isinstance(respaldo, str) and respaldo.strip():
        return " ".join(respaldo.split()), None, False       # luego lo revisa `validar_respuesta` como todo lo demás
    return None, f"el título no da un gancho ({problema}) y la ficha no tiene reglas.gancho_sin_ia", False


def escena_de(creador: str | None, marca: Marca, temporada: dict | None = None) -> str:
    """`INT. STREAM DE <CREADOR>` si cabe en una línea (y no trae palabras a evitar); si no, `INT. STREAM`."""
    nombre = " ".join(re.sub(r"[^\w\s.-]", " ", str(creador or "").lstrip("@")).replace("_", " ").split()).upper()
    corta = "INT. STREAM"
    if not nombre:
        return corta
    escena = f"{corta} DE {nombre}"
    ancho = marca.ancho - 2 * marca.m("margen")
    if marca.fuente("regular", marca.m("tam_escena")).getlength(escena) > ancho:
        return corta
    if any(ojos._contiene(escena, str(p)) for p in (temporada or {}).get("palabras_evitar", [])):
        return corta
    return escena


# ------------------------------------------------------------------ todo junto

def construir(ruta: Path, candidato, ficha: dict | None, marca: Marca, temporada: dict, *, duracion: float,
              rango: tuple[float, float], objetivo_s: float = OBJETIVO_S, analizar=None) -> Construccion:
    """El diccionario con la forma de la respuesta de Gemini, o el motivo del descarte.

    Aún no está validado: `toma` lo pasa por `ojos.validar_respuesta` (con apto y juego_correcto en true)."""
    gancho, motivo, definitivo = gancho_de(getattr(candidato, "titulo", ""), ficha, marca, temporada)
    if gancho is None:     # un título flojo no gasta la fuente: con Gemini de vuelta se puede reintentar
        return Construccion(None, f"sin IA: {motivo}", gastar=definitivo)
    try:
        rec = elegir_recorte(ruta, rango, objetivo_s, duracion, analizar)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        return Construccion(None, f"sin IA: falló el análisis del video ({type(e).__name__})", gastar=False)
    if rec is None:
        return Construccion(None, "sin IA: el video dura menos que el mínimo", gastar=True)
    creador = getattr(candidato, "creador", None) or (ficha or {}).get("creador")
    datos = {
        "apto": True, "motivos": [], "spoiler": False, "desnudez": False, "cinematica": False,
        "juego_correcto": True, "idioma_original": str(getattr(candidato, "idioma", "") or "desconocido"),
        "lineas": [], "gancho": gancho, "palabra_lapiz": palabra_lapiz_de(gancho),
        "escena": escena_de(creador, marca, temporada), "acotacion": "",
        "recorte": {"inicio": rec[0], "fin": rec[1]},
    }
    return Construccion(datos)

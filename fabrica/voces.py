"""Voces: transcripción propia, quién habla, silencios que sobran y golpes de audio (tarea #19).

Todo corre en la nube, sin Gemini y sin `torch`: ffmpeg saca el audio, `faster-whisper` (importado sólo cuando hace
falta; CTranslate2, CPU, int8) transcribe con tiempos por palabra, y numpy hace el resto:

- Hablantes: rasgos por turno (MFCC con banco mel y FFT de numpy, más el tono por autocorrelación), k-medias de 2 con
  semillas fijas (determinista) y silueta. Si las voces no se separan con claridad, todo es del streamer.
- Silencios: ventanas de 20 ms por debajo del umbral y sin palabra encima; la lógica de Auto-Editor (umbral y margen)
  en unas líneas, sin su dependencia. Los silencios largos se cortan, dejando `margen_s` a cada lado.
- Golpes: picos de la envolvente RMS que superan la mediana por `sobre_mediana_db`.

Nada de esto rompe una toma: sin biblioteca, sin modelo o sin audio, `analizar` devuelve `Voces` con el `motivo` y la
toma sigue como antes. Sólo `plazo.SinTiempo` se deja pasar (lo maneja el ciclo).

Los tiempos de `Voces` van en dos mapas: relativos al recorte (la fuente) y ya remapeados al tiempo de la toma
(después de quitar los silencios). `tramos` son los trozos de la fuente que se conservan.

Medición: `python -m fabrica.voces --medir VIDEO --inicio S --duracion S --salida JSON [--modelos base small]`.
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from . import plazo
from .config import cargar_yaml, raiz_repo
from .editar import dialogo as mod_dialogo
from .editar.marca import Marca

SR = 16000                       # audio de trabajo: WAV mono de 16 kHz (lo que espera whisper)
VENTANA_S = 0.02                 # ventana de la envolvente de volumen
PAUSA_TURNO_S = 0.3              # una pausa mayor entre palabras parte el turno de habla
RELLENO_PALABRA_S = 0.05         # las palabras se ensanchan un poco: whisper marca sus tiempos con unos ms de error
NO_SPEECH_MAX = 0.6              # segmento alucinado: no_speech_prob alta...
LOGPROB_MIN = -1.0               # ...y avg_logprob baja
SEMILLAS = tuple(range(8))       # semillas fijas de las k-medias
MIN_TURNOS = 4                   # con menos turnos no hay forma de separar dos voces
MIN_CUADROS = 8                  # un turno con menos cuadros con voz no da rasgos fiables
SEPARACION_CENTROS_MIN = 0.6     # distancia mínima (RMS por rasgo) entre los centros de las dos voces
PESO_TONO = 2.0                  # el tono pesa más que cada coeficiente MFCC suelto
HOLGURA_S = 0.25                 # la línea sigue en pantalla un poco después de su última palabra
MIN_LINEA_VISIBLE_S = 0.8        # y no dura menos que esto (si el hueco hasta la siguiente lo permite)
STREAMER, OTRA = "streamer", "otra"


# ------------------------------------------------------------------ configuración

@dataclass
class Cfg:
    """`config/voces.yml` con los valores por omisión del archivo."""
    activo: bool = True
    modo: str = "principal"
    modelo: str = "base"
    modelos_medir: list = field(default_factory=lambda: ["base", "small"])
    idioma: str | None = None            # sin uso: whisper siempre autodetecta (con idioma dado no detecta nada)
    idioma_prob_min: float = 0.5         # probabilidad mínima del idioma detectado para creer que es español
    p_palabra_min: float = 0.35          # una palabra con menos probabilidad que esto se descarta
    tope_s: float = 150.0
    reserva_s: float = 150.0             # tiempo del ciclo que debe quedar además del tope, para componer y la puerta
    silencio_min_s: float = 0.6
    margen_s: float = 0.12
    umbral_db: float = -40.0
    golpes: dict = field(default_factory=lambda: {"sobre_mediana_db": 12, "piso_db": -30, "separacion_s": 1.0, "max": 8})
    hablantes: dict = field(default_factory=lambda: {"max": 2, "separacion_min": 0.25, "minimo_s": 1.5})
    lineas: dict = field(default_factory=lambda: {"pausa_s": 0.5, "max_s": 4.5})
    alucinaciones: list = field(default_factory=lambda: [
        "subtítulos realizados por la comunidad de amara.org", "gracias por ver", "suscríbete"])
    nombre_otra: str = "OTRA VOZ"

    @classmethod
    def desde_dict(cls, d: dict | None) -> "Cfg":
        base = cls()
        d = d or {}
        datos = {}
        for k, v in d.items():
            if k not in base.__dataclass_fields__:
                continue
            por_omision = getattr(base, k)
            datos[k] = {**por_omision, **v} if isinstance(por_omision, dict) and isinstance(v, dict) else v
        return cls(**datos)

    @classmethod
    def cargar(cls, raiz=None) -> "Cfg | None":
        """La configuración del repo, o None si `config/voces.yml` no existe."""
        if not (raiz_repo(raiz) / "config" / "voces.yml").is_file():
            return None
        return cls.desde_dict(cargar_yaml("config/voces.yml", raiz))


# ------------------------------------------------------------------ resultados

@dataclass
class Transcripcion:
    idioma: str | None
    prob_idioma: float
    segmentos: list          # [{t0, t1, texto, no_speech_prob, avg_logprob, palabras: [{texto, t0, t1, p}]}]
    modelo: str
    segundos: float          # tiempo de transcribir (sin la carga del modelo)
    segundos_carga: float = 0.0
    descartados: dict = field(default_factory=dict)      # {"segmentos": n, "palabras": n} quitados por alucinación


@dataclass
class Voces:
    """Resultado de `analizar`. Los tiempos de `lineas`, `palabras` y `golpes` son los de la toma (ya sin los
    silencios cortados); `turnos`, `tramos` y `cortes` van relativos al recorte (la fuente)."""
    duracion: float                          # del recorte, antes de cortar
    duracion_toma: float                     # de la toma, después de cortar
    lineas: list = field(default_factory=list)      # [{t0, t1, hablante, original, es, voz}] (sólo si el idioma es español)
    palabras: list = field(default_factory=list)    # [{texto, t0, t1}]
    golpes: list = field(default_factory=list)      # [{t, db}]
    tramos: list = field(default_factory=list)      # [(a, b)] de la fuente que se conservan
    cortes: list = field(default_factory=list)      # [(a, b)] silencios quitados
    turnos: list = field(default_factory=list)      # [{t0, t1, voz}] en tiempo de la fuente
    hablantes: int = 0                              # voces distintas que se oyeron
    idioma: str | None = None
    prob_idioma: float = 0.0
    prob_idioma_min: float = 0.5
    descartados: dict = field(default_factory=dict)
    modelo: str | None = None
    segundos: float = 0.0
    motivo: str | None = None                       # por qué falta algo (sin biblioteca, sin modelo, sin audio)
    crudo: dict = field(default_factory=dict, repr=False)   # lo mismo en tiempo de la fuente, para volver a mapear

    @property
    def es_espanol(self) -> bool:
        """Español sólo si whisper lo detectó (nunca se le da el idioma) con probabilidad suficiente."""
        return (bool(self.idioma) and str(self.idioma).lower().startswith("es")
                and float(self.prob_idioma) >= float(self.prob_idioma_min))

    @property
    def hay_cortes(self) -> bool:
        return bool(self.cortes) and self.duracion_toma < self.duracion - 1e-6

    def con_tramos(self, tramos: list) -> "Voces":
        """Copia con todo remapeado al tiempo de la toma que dan estos tramos de la fuente."""
        crudo = self.crudo
        quitados = [] if tramos == [(0.0, self.duracion)] else _quitados(self.duracion, tramos)
        lineas = []
        for ln in crudo.get("lineas", []):
            t0, t1 = a_toma(ln["t0"], tramos), a_toma(ln["t1"], tramos)
            if t1 > t0:
                lineas.append({**ln, "t0": round(t0, 3), "t1": round(t1, 3)})
        palabras = [{"texto": p["texto"], "t0": round(a_toma(p["t0"], tramos), 3),
                     "t1": round(a_toma(p["t1"], tramos), 3)} for p in crudo.get("palabras", [])]
        golpes = [{"t": round(a_toma(g["t"], tramos), 3), "db": g["db"]} for g in crudo.get("golpes", [])]
        return replace(self, duracion_toma=round(sum(b - a for a, b in tramos), 3), lineas=lineas, palabras=palabras,
                       golpes=golpes, tramos=[(float(a), float(b)) for a, b in tramos], cortes=quitados)

    def sin_cortes(self) -> "Voces":
        return self.con_tramos([(0.0, self.duracion)])


# ------------------------------------------------------------------ audio

def extraer_audio(ruta, inicio: float, duracion: float, destino) -> Path:
    """WAV mono de 16 kHz del trozo [inicio, inicio + duracion] del video (ffmpeg). CalledProcessError si no hay audio."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{inicio:g}", "-t", f"{duracion:g}",
                    "-i", str(ruta), "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(destino)],
                   check=True, capture_output=True, timeout=plazo.tiempo_para(120))
    return destino


def leer_wav(ruta) -> tuple[np.ndarray, int]:
    """(muestras float32 en -1..1, tasa) de un WAV de 16 bits mono."""
    with wave.open(str(ruta), "rb") as w:
        sr, canales, ancho = w.getframerate(), w.getnchannels(), w.getsampwidth()
        crudo = w.readframes(w.getnframes())
    if ancho != 2:
        raise ValueError(f"el WAV debe ser de 16 bits (trae {8 * ancho})")
    x = np.frombuffer(crudo, dtype="<i2").astype(np.float32) / 32768.0
    if canales > 1:
        x = x.reshape(-1, canales).mean(axis=1)
    return x, sr


# ------------------------------------------------------------------ transcripción

_modelos: dict = {}
_ultimo_motivo: list = [None]


def motivo_de_fallo() -> str | None:
    """Por qué la última llamada a `transcribir` devolvió None."""
    return _ultimo_motivo[0]


def cargar_modelo(nombre: str):
    """(WhisperModel, segundos de carga). El modelo queda en memoria del proceso; si ya estaba, 0 s."""
    if nombre in _modelos:
        return _modelos[nombre], 0.0
    from faster_whisper import WhisperModel        # perezoso: sin la biblioteca el resto del módulo funciona
    t0 = time.monotonic()
    _modelos[nombre] = WhisperModel(nombre, device="cpu", compute_type="int8")
    return _modelos[nombre], time.monotonic() - t0


def _normal(texto: str) -> str:
    s = unicodedata.normalize("NFD", str(texto).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.]", " ", s)).strip()


PARECIDO_FRASE = 0.85            # un segmento entero así de parecido a una frase de la lista es una alucinación


def _solo_simbolos_o_entre_signos(texto: str) -> bool:
    """Un token como ♪, ♪♪, [Música], (música) o *aplausos*: sin letras ni números, o todo entre [] o ()."""
    t = str(texto).strip()
    if not any(c.isalnum() for c in t):
        return True
    return bool(re.fullmatch(r"[\[(][^\])]*[\])][.!?…]*", t) or re.fullmatch(r"\*[^*]+\*", t))


def es_alucinacion(seg: dict, cfg: Cfg) -> bool:
    """Un segmento inventado por whisper: sólo símbolos o entre corchetes, el texto ENTERO igual o casi igual a una
    frase típica de la lista (no por subcadena: «gracias por ver el stream» con más texto real se queda), o muy
    probable silencio con confianza baja."""
    if _solo_simbolos_o_entre_signos(seg.get("texto", "")):
        return True
    texto = _normal(seg.get("texto", "")).strip(". ")
    for f in cfg.alucinaciones:
        frase = _normal(f).strip(". ")
        if frase and (texto == frase or difflib.SequenceMatcher(None, texto, frase).ratio() >= PARECIDO_FRASE):
            return True
    return (float(seg.get("no_speech_prob") or 0.0) > NO_SPEECH_MAX
            and float(seg.get("avg_logprob") if seg.get("avg_logprob") is not None else 0.0) < LOGPROB_MIN)


def limpiar(tr: Transcripcion, cfg: Cfg) -> Transcripcion:
    """Quita palabras sueltas dudosas (símbolos, entre signos, probabilidad menor a `p_palabra_min`) y segmentos
    alucinados. Cuenta lo descartado en `descartados`."""
    palabras_fuera, buenos = 0, []
    segs_fuera = 0
    for s in tr.segmentos:
        originales = s.get("palabras", [])
        firmes = [w for w in originales if not _solo_simbolos_o_entre_signos(w["texto"])
                  and float(w.get("p", 1.0)) >= cfg.p_palabra_min]
        palabras_fuera += len(originales) - len(firmes)
        if originales and not firmes:                 # todas sus palabras eran basura
            segs_fuera += 1
            continue
        if len(firmes) != len(originales):
            s = {**s, "palabras": firmes, "texto": " ".join(w["texto"] for w in firmes)}
        if es_alucinacion(s, cfg):
            segs_fuera += 1
            palabras_fuera += len(firmes)
            continue
        buenos.append(s)
    return replace(tr, segmentos=buenos, descartados={"segmentos": segs_fuera, "palabras": palabras_fuera})


def _con_whisper(wav, cfg: Cfg, nombre: str) -> Transcripcion:
    modelo, carga = cargar_modelo(nombre)
    muestras, _ = leer_wav(wav)
    t0 = time.monotonic()
    gen, info = modelo.transcribe(muestras, language=None, word_timestamps=True, vad_filter=True,
                                  beam_size=1, condition_on_previous_text=False)
    segmentos = []
    for s in gen:                              # el generador hace el trabajo: aquí se vigila el tiempo
        if time.monotonic() - t0 > cfg.tope_s:
            raise TimeoutError(f"la transcripción pasó de {cfg.tope_s:g} s")
        if not plazo.queda(plazo.MARGEN_S):
            raise plazo.SinTiempo("sin tiempo del ciclo para seguir transcribiendo")
        segmentos.append({
            "t0": float(s.start), "t1": float(s.end), "texto": (s.text or "").strip(),
            "no_speech_prob": float(s.no_speech_prob), "avg_logprob": float(s.avg_logprob),
            "palabras": [{"texto": (w.word or "").strip(), "t0": float(w.start), "t1": float(w.end),
                          "p": float(w.probability)} for w in (s.words or []) if (w.word or "").strip()]})
    return Transcripcion(idioma=info.language, prob_idioma=float(info.language_probability), segmentos=segmentos,
                         modelo=nombre, segundos=time.monotonic() - t0, segundos_carga=carga)


def transcribir(wav, cfg: Cfg, *, modelo: str | None = None, transcriptor=None) -> Transcripcion | None:
    """Transcribe un WAV con faster-whisper (CPU, int8, tiempos por palabra, VAD). None si no se pudo.

    `transcriptor`: `callable(wav, cfg, modelo) -> Transcripcion` para pruebas (sustituye a whisper). Se descartan
    los segmentos alucinados. Cualquier error deja `motivo_de_fallo()` y devuelve None; sólo `plazo.SinTiempo` sube.
    """
    nombre = modelo or cfg.modelo
    cfg = replace(cfg, idioma=None)           # nunca se le da el idioma: con `language` whisper no detecta nada
    _ultimo_motivo[0] = None
    try:
        tr = transcriptor(wav, cfg, nombre) if transcriptor is not None else _con_whisper(wav, cfg, nombre)
    except plazo.SinTiempo:
        raise
    except ImportError as e:
        _ultimo_motivo[0] = f"sin faster-whisper ({type(e).__name__}: {e})"
        return None
    except Exception as e:  # noqa: BLE001 (modelo que no baja, audio raro, tiempo propio: nada rompe la toma)
        _ultimo_motivo[0] = f"transcripción: {type(e).__name__}: {str(e)[:160]}"
        return None
    return limpiar(tr, cfg)


# ------------------------------------------------------------------ volumen, silencios y cortes

def rms_db(muestras: np.ndarray, sr: int, ventana_s: float = VENTANA_S) -> np.ndarray:
    """Volumen (dBFS, RMS) de cada ventana sin traslape. Silencio digital: -100."""
    n = max(1, int(round(sr * ventana_s)))
    k = len(muestras) // n
    if k == 0:
        return np.full(1, -100.0)
    x = np.asarray(muestras[: k * n], dtype=np.float64).reshape(k, n)
    rms = np.sqrt((x * x).mean(axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-5))


def _palabras_planas(segmentos: list) -> list:
    return [p for s in segmentos for p in s.get("palabras", [])]


def silencios(muestras: np.ndarray, sr: int, palabras: list, cfg: Cfg) -> list[tuple[float, float]]:
    """Tramos (a, b) en segundos de al menos `silencio_min_s` con volumen bajo `umbral_db` y sin palabra encima."""
    db = rms_db(muestras, sr)
    calla = db < cfg.umbral_db
    for p in palabras:
        i0 = max(0, int((float(p["t0"]) - RELLENO_PALABRA_S) / VENTANA_S))
        i1 = min(len(db), int(np.ceil((float(p["t1"]) + RELLENO_PALABRA_S) / VENTANA_S)))
        calla[i0:i1] = False
    salida: list[tuple[float, float]] = []
    i = 0
    while i < len(calla):
        if not calla[i]:
            i += 1
            continue
        j = i
        while j < len(calla) and calla[j]:
            j += 1
        a, b = i * VENTANA_S, j * VENTANA_S
        if j == len(calla):                     # el último trozo suelto de audio (menos de una ventana) también cuenta
            b = len(muestras) / sr
        if b - a >= cfg.silencio_min_s:
            salida.append((round(a, 3), round(b, 3)))
        i = j
    return salida


CUADROS_S = 30                   # los bordes de los tramos van en múltiplos de 1/30 s (los fotogramas de la salida)


def _al_cuadro(t: float) -> float:
    return round(round(t * CUADROS_S) / CUADROS_S, 6)


def tramos_a_conservar(duracion: float, silencios_: list, margen_s: float, minimo_s: float) -> list[tuple[float, float]]:
    """Trozos de [0, duracion] que quedan al quitar de cada silencio (a, b) el tramo [a + margen, b - margen].

    Si hay cortes, los bordes se redondean a múltiplos de 1/30 s (sin deriva entre audio y video). Un silencio que
    toca el inicio o el final del recorte se quita entero (no queda un microtramo de `margen_s`). Si lo que queda no
    llega a `minimo_s`, no se corta nada: [(0, duracion)]."""
    entero = (0.0, float(duracion))
    cuadro = 1.0 / CUADROS_S
    cortes = []
    for a, b in sorted(silencios_):
        if b - a <= 2 * margen_s:
            continue
        ini = 0.0 if a <= cuadro else a + margen_s
        fin = float(duracion) if b >= duracion - cuadro else b - margen_s
        cortes.append((_al_cuadro(ini), _al_cuadro(fin)))
    fin_util = math.floor(duracion * CUADROS_S + 1e-9) / CUADROS_S       # el último cuadro entero del recorte
    tramos: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in cortes:
        a, b = max(a, cursor), min(b, fin_util)
        if b <= a:
            continue
        if a > cursor:
            tramos.append((cursor, a))
        cursor = b
    if cursor < fin_util:
        tramos.append((cursor, round(fin_util, 6)))
    total = sum(b - a for a, b in tramos)
    if not cortes or not tramos or total < minimo_s - 1e-9 or len(tramos) == 1 and tramos[0][0] == 0.0:
        return [entero]
    return [(round(a, 6), round(b, 6)) for a, b in tramos]


def _quitados(duracion: float, tramos: list) -> list[tuple[float, float]]:
    """Los huecos de [0, duracion] que los tramos no cubren."""
    salida, cursor = [], 0.0
    for a, b in tramos:
        if a > cursor + 1e-9:
            salida.append((round(cursor, 6), round(a, 6)))
        cursor = b
    if cursor < duracion - 1e-9:
        salida.append((round(cursor, 6), round(duracion, 6)))
    return salida


def a_toma(t: float, tramos: list) -> float:
    """Instante de la fuente (relativo al recorte) -> instante de la toma. Dentro de un tramo quitado, el borde."""
    desplazado = 0.0
    for a, b in tramos:
        if t < a:
            return desplazado
        if t <= b:
            return desplazado + (t - a)
        desplazado += b - a
    return desplazado


def a_fuente(t: float, tramos: list) -> float:
    """Instante de la toma -> instante de la fuente (relativo al recorte). Inverso de `a_toma` dentro de los tramos."""
    desplazado = 0.0
    for a, b in tramos:
        if t < desplazado + (b - a):
            return a + max(0.0, t - desplazado)
        desplazado += b - a
    return float(tramos[-1][1]) if tramos else float(t)


# ------------------------------------------------------------------ golpes

def golpes(muestras: np.ndarray, sr: int, cfg: Cfg) -> list[dict]:
    """Picos de volumen: máximos locales de la envolvente que superan la mediana por `sobre_mediana_db`.

    Separados al menos `separacion_s`; los `max` más fuertes, en orden de tiempo. [{t, db}] (t en segundos)."""
    g = cfg.golpes
    env = rms_db(muestras, sr)
    if len(env) < 3 or int(g["max"]) <= 0:
        return []
    umbral = max(float(np.median(env)) + float(g["sobre_mediana_db"]), float(g.get("piso_db", -100.0)))
    radio = max(1, int(round(0.1 / VENTANA_S)))
    candidatos = []
    for i in np.flatnonzero(env >= umbral):
        vecinos = env[max(0, i - radio): i + radio + 1]
        if env[i] >= vecinos.max() and i == max(0, i - radio) + int(np.argmax(vecinos)):
            candidatos.append((float(env[i]), int(i)))
    elegidos: list[tuple[float, int]] = []
    sep = float(g["separacion_s"]) / VENTANA_S
    for db, i in sorted(candidatos, key=lambda c: (-c[0], c[1])):
        if all(abs(i - j) >= sep for _, j in elegidos):
            elegidos.append((db, i))
        if len(elegidos) >= int(g["max"]):
            break
    return [{"t": round((i + 0.5) * VENTANA_S, 3), "db": round(db, 1)} for db, i in sorted(elegidos, key=lambda c: c[1])]


# ------------------------------------------------------------------ hablantes

def turnos_de_palabras(segmentos: list, pausa_s: float = PAUSA_TURNO_S) -> list[tuple[float, float]]:
    """Turnos de habla (t0, t1): se parten en cada cambio de segmento y en cada pausa mayor a `pausa_s`."""
    turnos: list[tuple[float, float]] = []
    for s in segmentos:
        actual = None
        for p in s.get("palabras", []):
            t0, t1 = float(p["t0"]), float(p["t1"])
            if actual is not None and t0 - actual[1] <= pausa_s:
                actual[1] = max(actual[1], t1)
            else:
                if actual is not None:
                    turnos.append((actual[0], actual[1]))
                actual = [t0, t1]
        if actual is not None:
            turnos.append((actual[0], actual[1]))
    return turnos


def _filtros_mel(n_fft: int, sr: int, n_mel: int = 26) -> np.ndarray:
    def a_mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def de_mel(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)
    puntos = de_mel(np.linspace(a_mel(0.0), a_mel(sr / 2.0), n_mel + 2))
    bins = np.floor((n_fft + 1) * puntos / sr).astype(int)
    banco = np.zeros((n_mel, n_fft // 2 + 1))
    for m in range(1, n_mel + 1):
        izq, centro, der = bins[m - 1], bins[m], bins[m + 1]
        for k in range(izq, centro):
            banco[m - 1, k] = (k - izq) / max(1, centro - izq)
        for k in range(centro, der):
            banco[m - 1, k] = (der - k) / max(1, der - centro)
    return banco


def _dct(n_mel: int, n_coef: int) -> np.ndarray:
    n = np.arange(n_mel)
    return np.cos(np.pi / n_mel * (n + 0.5)[None, :] * np.arange(n_coef)[:, None])


def _cuadros(x: np.ndarray, largo: int, paso: int) -> np.ndarray:
    if len(x) < largo:
        return np.zeros((0, largo))
    n = 1 + (len(x) - largo) // paso
    idx = np.arange(largo)[None, :] + paso * np.arange(n)[:, None]
    return x[idx]


def _tono(x: np.ndarray, sr: int, piso_db: float) -> float:
    """Tono mediano (log Hz) de los cuadros de 40 ms con voz, por autocorrelación. NaN si no hay."""
    largo, paso = int(0.04 * sr), int(0.02 * sr)
    cuadros = _cuadros(x, largo, paso)
    if len(cuadros) == 0:
        return float("nan")
    lag_min, lag_max = int(sr / 400), int(sr / 60)
    n_fft = 1 << int(np.ceil(np.log2(2 * largo)))
    mitad = np.hanning(largo)
    espectro = np.fft.rfft(cuadros * mitad, n_fft, axis=1)
    ac = np.fft.irfft(np.abs(espectro) ** 2, n_fft, axis=1)[:, : lag_max + 2]
    energia = np.maximum(ac[:, 0], 1e-12)
    cero = 20.0 * np.log10(np.sqrt(energia / largo) + 1e-12)
    tonos = []
    for i in range(len(cuadros)):
        if cero[i] < piso_db:
            continue
        r = ac[i, lag_min: lag_max + 1] / energia[i]
        mejor = float(r.max())
        if mejor < 0.4:
            continue
        pico = next((k for k in range(1, len(r) - 1)
                     if r[k] >= 0.85 * mejor and r[k] >= r[k - 1] and r[k] >= r[k + 1]), int(np.argmax(r)))
        tonos.append(np.log(sr / (pico + lag_min)))
    return float(np.median(tonos)) if len(tonos) >= 3 else float("nan")


def rasgos_turno(x: np.ndarray, sr: int) -> np.ndarray | None:
    """Rasgos de un turno: media y desviación de 12 MFCC (banco mel de 26 bandas) y el log-tono mediano.

    None si el turno tiene muy pocos cuadros con voz."""
    if len(x) < int(0.1 * sr):
        return None
    crudo = x
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])                  # preénfasis
    largo, paso = int(0.025 * sr), int(0.010 * sr)
    cuadros = _cuadros(x, largo, paso)
    if len(cuadros) < MIN_CUADROS:
        return None
    energia = 10.0 * np.log10((cuadros ** 2).mean(axis=1) + 1e-12)
    vivos = energia > max(float(energia.max()) - 30.0, -75.0)
    if int(vivos.sum()) < MIN_CUADROS:
        return None
    cuadros = cuadros[vivos] * np.hamming(largo)
    n_fft = 512 if sr <= 16000 else 1024
    potencia = np.abs(np.fft.rfft(cuadros, n_fft, axis=1)) ** 2 / n_fft
    mel = np.log(potencia @ _filtros_mel(n_fft, sr).T + 1e-10)
    mfcc = (mel @ _dct(mel.shape[1], 13).T)[:, 1:]                # sin c0 (volumen)
    return np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0), [_tono(crudo, sr, -60.0)]])


def _kmedias(X: np.ndarray, semilla: int, iteraciones: int = 50) -> tuple[np.ndarray, float]:
    rng = np.random.default_rng(semilla)
    n = len(X)
    c0 = int(rng.integers(n))
    d = ((X - X[c0]) ** 2).sum(axis=1)
    c1 = int(rng.choice(n, p=d / d.sum())) if d.sum() > 0 else (c0 + 1) % n    # k-medias++
    centros = np.stack([X[c0], X[c1]])
    etiquetas = np.full(n, -1)
    for _ in range(iteraciones):
        dist = ((X[:, None, :] - centros[None, :, :]) ** 2).sum(axis=2)
        nuevas = dist.argmin(axis=1)
        if (nuevas == etiquetas).all():
            break
        etiquetas = nuevas
        for k in (0, 1):
            if (etiquetas == k).any():
                centros[k] = X[etiquetas == k].mean(axis=0)
            else:                                               # grupo vacío: se reinicia en el punto más lejano
                centros[k] = X[int(dist.min(axis=1).argmax())]
    inercia = float(((X - centros[etiquetas]) ** 2).sum())
    return etiquetas, inercia


def _silueta(X: np.ndarray, etiquetas: np.ndarray) -> float:
    if len(set(etiquetas.tolist())) < 2:
        return -1.0
    dist = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(axis=2))
    s = []
    for i in range(len(X)):
        mismo = (etiquetas == etiquetas[i])
        mismo[i] = False
        otro = etiquetas != etiquetas[i]
        if not mismo.any():
            s.append(0.0)
            continue
        a, b = dist[i, mismo].mean(), dist[i, otro].mean()
        s.append((b - a) / max(a, b, 1e-12))
    return float(np.mean(s))


def hablantes(muestras: np.ndarray, sr: int, turnos: list, cfg: Cfg) -> list[str]:
    """Voz de cada turno (t0, t1): "streamer" (la que más habla) u "otra". Todo "streamer" si no se separan con claridad.

    Rasgos por turno (MFCC y tono), estandarizados; k-medias de 2 con semillas fijas; se cree en las dos voces sólo si
    la silueta llega a `separacion_min` y la voz minoritaria habla al menos `minimo_s`."""
    todo = [STREAMER] * len(turnos)
    h = cfg.hablantes
    if int(h["max"]) < 2 or len(turnos) < MIN_TURNOS:
        return todo
    rasgos = [rasgos_turno(muestras[int(max(0.0, t0) * sr): int(max(0.0, t1) * sr)], sr) for t0, t1 in turnos]
    validos = [i for i, r in enumerate(rasgos) if r is not None]
    if len(validos) < MIN_TURNOS:
        return todo
    X = np.stack([rasgos[i] for i in validos])
    tonos = X[:, -1]
    if np.isnan(tonos).all():
        X = X[:, :-1]
    else:
        X[np.isnan(tonos), -1] = np.nanmedian(tonos)
    # Estandarizar sin inflar el ruido: una característica que casi no varía entre turnos (la misma voz) no se
    # amplifica; el piso es la variación mínima que cuenta como diferencia real (MFCC, desviaciones, tono).
    piso = np.array([1.0] * 12 + [0.5] * 12 + [0.05])[: X.shape[1]]
    X = (X - X.mean(axis=0)) / np.maximum(X.std(axis=0), piso)
    if not np.isnan(tonos).all():
        X[:, -1] *= PESO_TONO
    mejor, mejor_inercia = None, None
    for semilla in SEMILLAS:
        etiquetas, inercia = _kmedias(X, semilla)
        if mejor is None or inercia < mejor_inercia - 1e-12:
            mejor, mejor_inercia = etiquetas, inercia
    if _silueta(X, mejor) < float(h["separacion_min"]):
        return todo
    # La silueta es relativa: dos mitades de la misma voz también "se separan". Además los centros de los dos grupos
    # deben estar lejos en términos absolutos (diferencia RMS por rasgo, en unidades de variación real).
    dif = X[mejor == 0].mean(axis=0) - X[mejor == 1].mean(axis=0)
    if float(np.sqrt((dif ** 2).mean())) < SEPARACION_CENTROS_MIN:
        return todo
    tiempo = [0.0, 0.0]
    for i, e in zip(validos, mejor):
        tiempo[int(e)] += turnos[i][1] - turnos[i][0]
    if min(tiempo) < float(h["minimo_s"]):
        return todo
    grande = 0 if tiempo[0] >= tiempo[1] else 1
    voz = {i: (STREAMER if int(e) == grande else OTRA) for i, e in zip(validos, mejor)}
    salida = []
    for i in range(len(turnos)):                  # un turno sin rasgos fiables toma la voz del turno anterior
        if i in voz:
            salida.append(voz[i])
        else:
            previo = next((salida[j] for j in range(i - 1, -1, -1)), None)
            siguiente = next((voz[j] for j in range(i + 1, len(turnos)) if j in voz), None)
            salida.append(previo or siguiente or STREAMER)
    return salida


# ------------------------------------------------------------------ líneas de subtítulo

def nombre_visible(creador: str | None, marca: Marca) -> str:
    """El rótulo del streamer: el creador de la ficha sin `twitch.tv/` ni `@`, en mayúsculas y recortado a la columna."""
    s = re.sub(r"^\s*(https?://)?(www\.)?twitch\.tv/", "", str(creador or ""), flags=re.I).strip().lstrip("@").strip()
    s = s.upper() or "STREAMER"
    cfg = marca.pc["dialogo"]
    ancho = (cfg["columna_dialogo"][1] - cfg["columna_dialogo"][0]) - 2 * int(cfg["relleno_x"])
    f = marca.fuente("regular", cfg["tam_nombre"])
    while len(s) > 1 and f.getlength(s) > ancho:
        s = s[:-1].rstrip()
    return s


def _voz_en(t: float, turnos: list) -> str:
    """Voz del turno que contiene el instante `t` (o la del más cercano). Sin turnos, "streamer"."""
    if not turnos:
        return STREAMER
    mejor, dist_mejor = STREAMER, None
    for tn in turnos:
        d = 0.0 if tn["t0"] <= t <= tn["t1"] else min(abs(t - tn["t0"]), abs(t - tn["t1"]))
        if dist_mejor is None or d < dist_mejor:
            mejor, dist_mejor = tn["voz"], d
    return mejor


def asignar_voz(lineas: list, turnos_con_voz: list) -> list[str]:
    """Voz de cada línea (dicts o `Linea`) según el turno con el que más solapa; sin solape, el turno más cercano."""
    def campo(ln, k):
        return float(ln[k] if isinstance(ln, dict) else getattr(ln, k))
    salida = []
    for ln in lineas:
        t0, t1 = campo(ln, "t0"), campo(ln, "t1")
        por_voz: dict[str, float] = {}
        for tn in turnos_con_voz:
            s = min(t1, tn["t1"]) - max(t0, tn["t0"])
            if s > 0:
                por_voz[tn["voz"]] = por_voz.get(tn["voz"], 0.0) + s
        salida.append(max(por_voz, key=por_voz.get) if por_voz else _voz_en((t0 + t1) / 2, turnos_con_voz))
    return salida


def lineas_desde_transcripcion(tr: Transcripcion, voces_turnos: list, marca: Marca, nombre_streamer: str | None,
                               cfg: Cfg) -> list[dict]:
    """Subtítulos [{t0, t1, hablante, original, es, voz}] (tiempo de la fuente) a partir de las palabras.

    La frase se corta en cada cambio de voz, pausa mayor a `lineas.pausa_s`, duración mayor a `lineas.max_s` o cuando
    ya no cabe en `max_lineas_frase` líneas de la columna de la plantilla. Sólo se llama si el idioma es español:
    `es` es el mismo texto (no hay traducción sin IA). `voces_turnos`: [{t0, t1, voz}]."""
    medidas = marca.pc["dialogo"]

    def cabe(frase: str) -> bool:
        try:
            return len(mod_dialogo.lineas_frase(marca, frase, medidas)) <= marca.max_lineas_frase
        except ValueError:                        # una palabra suelta más ancha que la columna
            return False
    pausa, tope = float(cfg.lineas["pausa_s"]), float(cfg.lineas["max_s"])
    nombres = {STREAMER: nombre_visible(nombre_streamer, marca), OTRA: cfg.nombre_otra}
    grupos: list[list[dict]] = []
    voz_g = None
    for p in _palabras_planas(tr.segmentos):
        texto = str(p["texto"]).strip()
        if not texto:
            continue
        palabra = {**p, "texto": texto, "voz": _voz_en((float(p["t0"]) + float(p["t1"])) / 2, voces_turnos)}
        if grupos:
            g = grupos[-1]
            nuevo = " ".join([x["texto"] for x in g] + [texto])
            corta = (palabra["voz"] != voz_g or float(p["t0"]) - float(g[-1]["t1"]) > pausa
                     or float(p["t1"]) - float(g[0]["t0"]) > tope or not cabe(nuevo))
            if not corta:
                g.append(palabra)
                continue
        if not cabe(texto):
            continue                              # una palabra que no cabe sola (ruido de whisper): se omite
        grupos.append([palabra])
        voz_g = palabra["voz"]
    lineas: list[dict] = []
    for i, g in enumerate(grupos):
        t0, ultima = float(g[0]["t0"]), float(g[-1]["t1"])
        siguiente = float(grupos[i + 1][0]["t0"]) if i + 1 < len(grupos) else float("inf")
        t1 = min(ultima + HOLGURA_S, siguiente)
        t1 = max(t1, min(t0 + MIN_LINEA_VISIBLE_S, siguiente))
        frase = " ".join(x["texto"] for x in g)
        lineas.append({"t0": round(t0, 3), "t1": round(t1, 3), "hablante": nombres[g[0]["voz"]],
                       "original": frase, "es": frase, "voz": g[0]["voz"]})
    return lineas


# ------------------------------------------------------------------ todo junto

def _resultados_de_audio(muestras: np.ndarray, sr: int, tr: Transcripcion | None, cfg: Cfg):
    """(turnos con voz, silencios, golpes) en tiempo de la fuente."""
    palabras = _palabras_planas(tr.segmentos) if tr else []
    turnos = turnos_de_palabras(tr.segmentos) if tr else []
    etiquetas = hablantes(muestras, sr, turnos, cfg) if turnos else []
    con_voz = [{"t0": round(t0, 3), "t1": round(t1, 3), "voz": v} for (t0, t1), v in zip(turnos, etiquetas)]
    return con_voz, silencios(muestras, sr, palabras, cfg), golpes(muestras, sr, cfg)


def analizar(ruta_video, inicio: float, duracion: float, marca: Marca, cfg: Cfg, *, minimo_s: float,
             nombre_streamer: str | None, transcriptor=None, modelo: str | None = None) -> Voces:
    """Transcribe, separa voces, busca silencios y golpes del trozo [inicio, inicio + duracion] del video.

    Nunca lanza (salvo `plazo.SinTiempo`): lo que falte queda en `motivo`. `minimo_s`: el corte de silencios no deja
    la toma por debajo de esta duración (si lo haría, no se corta)."""
    t_ini = time.monotonic()
    v = Voces(duracion=float(duracion), duracion_toma=float(duracion), tramos=[(0.0, float(duracion))])
    v.crudo = {"lineas": [], "palabras": [], "golpes": []}
    try:
        with tempfile.TemporaryDirectory(prefix="voces-") as tmp:
            wav = extraer_audio(ruta_video, inicio, duracion, Path(tmp) / "audio.wav")
            muestras, sr = leer_wav(wav)
            tr = transcribir(wav, cfg, modelo=modelo, transcriptor=transcriptor)
    except plazo.SinTiempo:
        raise
    except subprocess.CalledProcessError:
        v.motivo = "sin audio: ffmpeg no pudo sacar la pista de audio"
        v.segundos = time.monotonic() - t_ini
        return v
    except Exception as e:  # noqa: BLE001
        v.motivo = f"audio: {type(e).__name__}: {str(e)[:160]}"
        v.segundos = time.monotonic() - t_ini
        return v
    if tr is None:
        v.motivo = motivo_de_fallo()
    else:
        v.idioma, v.prob_idioma, v.modelo = tr.idioma, tr.prob_idioma, tr.modelo
        v.prob_idioma_min, v.descartados = cfg.idioma_prob_min, dict(tr.descartados)
    turnos, sil, gol = _resultados_de_audio(muestras, sr, tr, cfg)
    v.turnos = turnos
    v.hablantes = len({t["voz"] for t in turnos})
    v.crudo = {"golpes": gol,
               "palabras": [{"texto": p["texto"], "t0": float(p["t0"]), "t1": float(p["t1"])}
                            for p in (_palabras_planas(tr.segmentos) if tr else [])],
               "lineas": (lineas_desde_transcripcion(tr, turnos, marca, nombre_streamer, cfg)
                          if tr and v.es_espanol else [])}
    tramos = tramos_a_conservar(float(duracion), sil, cfg.margen_s, float(minimo_s))
    v = v.con_tramos(tramos)
    v.segundos = round(time.monotonic() - t_ini, 2)
    return v


# ------------------------------------------------------------------ medición (la muestra y la línea de comandos)

def medir(ruta_video, inicio: float, duracion: float, cfg: Cfg, modelos: list, *, transcriptor=None,
          guardar: dict | None = None) -> dict:
    """Mide cada modelo sobre el mismo trozo: segundos de carga (de verdad: la caché de modelos del proceso se vacía
    antes de cada uno) y de transcripción, idioma y texto con la voz de cada segmento; y el análisis de audio
    (hablantes, cortes, golpes) con el primer modelo que funcione. Nada lanza; un fallo queda en `error`.

    `guardar`: si se da, recibe {modelo: Transcripcion} para que quien llame no tenga que transcribir otra vez."""
    salida: dict = {"video": Path(str(ruta_video)).name, "inicio_s": inicio, "duracion_s": duracion,
                    "motor": "faster-whisper", "modelos": [], "hablantes": None, "cortes": [], "golpes": []}
    with tempfile.TemporaryDirectory(prefix="voces-medir-") as tmp:
        try:
            wav = extraer_audio(ruta_video, inicio, duracion, Path(tmp) / "audio.wav")
            muestras, sr = leer_wav(wav)
        except plazo.SinTiempo:
            raise
        except Exception as e:  # noqa: BLE001
            salida["error"] = f"audio: {type(e).__name__}: {str(e)[:160]}"
            return salida
        principal = None
        for nombre in modelos:
            fila: dict = {"modelo": nombre}
            _modelos.clear()                                  # carga medida sin modelo ya cargado
            tr = transcribir(wav, cfg, modelo=nombre, transcriptor=transcriptor)
            if tr is None:
                fila["error"] = motivo_de_fallo()
            else:
                palabras = _palabras_planas(tr.segmentos)
                fila.update({"segundos_carga": round(tr.segundos_carga, 2), "segundos": round(tr.segundos, 2),
                             "idioma": tr.idioma, "prob_idioma": round(tr.prob_idioma, 3),
                             "descartados": dict(tr.descartados), "palabras": len(palabras), "texto": " ".join(s["texto"] for s in tr.segmentos),
                             "segmentos": [{"t0": round(s["t0"], 2), "t1": round(s["t1"], 2), "texto": s["texto"]}
                                           for s in tr.segmentos]})
                principal = principal or tr
                if guardar is not None:
                    guardar[nombre] = tr
            salida["modelos"].append(fila)
        turnos, sil, gol = _resultados_de_audio(muestras, sr, principal, cfg)
        tramos = tramos_a_conservar(float(duracion), sil, cfg.margen_s, 0.0)
        salida["hablantes"] = {"n": len({t["voz"] for t in turnos}), "turnos": turnos}
        salida["cortes"] = [list(c) for c in _quitados(float(duracion), tramos)] if tramos != [(0.0, float(duracion))] else []
        salida["golpes"] = gol
        for fila in salida["modelos"]:                        # la voz de cada segmento, por solape con los turnos
            segs = fila.get("segmentos") or []
            for sg, v in zip(segs, asignar_voz(segs, turnos)):
                sg["voz"] = v
    return salida


def resumen_md(datos: dict, titulo: str) -> str:
    """Resumen legible (Markdown) de `voces.json` de la muestra o de la salida de `medir`."""
    med = datos.get("medicion", datos)
    out = [f"### {titulo}", ""]
    if datos.get("motivo"):
        out.append(f"- Sin voces en el análisis: {datos['motivo']}")
    if med.get("error"):
        out.append(f"- Error de la medición: {med['error']}")
    out.append(f"- Fuente: `{med.get('video')}` desde {med.get('inicio_s')} s, {med.get('duracion_s')} s")
    h = med.get("hablantes") or {}
    cortes, gol = med.get("cortes") or [], med.get("golpes") or []
    out.append(f"- Hablantes: {h.get('n', 0)}; cortes de silencio: {len(cortes)} "
               f"({', '.join(f'{a:g}-{b:g}' for a, b in cortes) or 'ninguno'}); golpes: {len(gol)} "
               f"({', '.join(f'{g['t']:g} s {g['db']:g} dB' for g in gol) or 'ninguno'})")
    if "duracion_toma_s" in datos:
        out.append(f"- Recorte {datos['duracion_recorte_s']:g} s -> toma {datos['duracion_toma_s']:g} s")
    out += ["", "| modelo | carga (s) | transcripción (s) | idioma | prob. | palabras | descartados |",
            "|---|---|---|---|---|---|---|"]
    for m in med.get("modelos", []):
        if "error" in m:
            out.append(f"| {m['modelo']} | - | - | - | - | - | error: {m['error']} |")
        else:
            d = m.get("descartados") or {}
            out.append(f"| {m['modelo']} | {m['segundos_carga']} | {m['segundos']} | {m['idioma']} | {m['prob_idioma']} "
                       f"| {m['palabras']} | {d.get('segmentos', 0)} seg, {d.get('palabras', 0)} pal |")
    for m in med.get("modelos", []):
        if m.get("segmentos"):
            out += ["", f"Texto de `{m['modelo']}` (tiempo, voz):", ""]
            out += [f"- {sg['t0']:g}-{sg['t1']:g} s [{sg.get('voz', '?')}] {sg['texto']}" for sg in m["segmentos"]]
    if datos.get("lineas"):
        out += ["", "Líneas de la toma (tiempo de la toma):", ""]
        out += [f"- {ln['t0']:g}-{ln['t1']:g} s [{ln.get('voz') or '-'}] {ln.get('nombre') or ''}: {ln.get('frase') or ''}"
                for ln in datos["lineas"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.voces", description="Mide la transcripción y el análisis de audio.")
    ap.add_argument("--medir", metavar="VIDEO")
    ap.add_argument("--resumen", nargs="+", metavar="JSON", help="imprime un resumen legible de estos JSON y sale")
    ap.add_argument("--inicio", type=float, default=0.0)
    ap.add_argument("--duracion", type=float)
    ap.add_argument("--salida", metavar="JSON")
    ap.add_argument("--modelos", nargs="+", default=None, help="por omisión, `modelos_medir` de config/voces.yml")
    args = ap.parse_args(argv)
    if args.resumen:
        for ruta in args.resumen:
            if Path(ruta).is_file():
                print(resumen_md(json.loads(Path(ruta).read_text(encoding="utf-8")), Path(ruta).name))
            else:
                print(f"### {Path(ruta).name}\n\n- no hay archivo (aviso: sin esta medición)\n")
        return 0
    if not (args.medir and args.duracion and args.salida):
        ap.error("--medir necesita VIDEO, --duracion y --salida")
    cfg = Cfg.cargar() or Cfg()
    # Sin pista de idioma: así se ve qué idioma detecta cada modelo (el audio puede no ser español).
    res = medir(args.medir, args.inicio, args.duracion, replace(cfg, idioma=None), args.modelos or cfg.modelos_medir)
    Path(args.salida).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    for m in res["modelos"]:
        print(f"{m['modelo']}: " + (f"carga {m['segundos_carga']} s, transcripción {m['segundos']} s, {m['idioma']}"
                                    if "error" not in m else f"sin resultado ({m['error']})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

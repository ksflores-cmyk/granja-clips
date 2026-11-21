"""Puerta técnica por código: `python -m fabrica.editar.tecnica VIDEO PLAN [--voz W --musica W]`.

Antes de cualquier crítico con IA, mide sobre el MP4 lo que un editor humano nunca dejaría pasar. Sin LLM: sólo
ffmpeg/ffprobe, numpy y la biblioteca estándar. Son 5 puntos y cada falla dice qué y dónde (segundo y cuadro o tramo):

1. Subtítulo a tiempo con su palabra (transcripción por palabra del plan).
2. Ningún destello de 1 cuadro (negro o blanco) entre cortes.
3. Ningún congelado de más de `congelados.max_s` en piezas con video.
4. Música al menos `ducking.margen_db` bajo las voces (sidechaincompress), en piezas que mezclan música con voz.
5. Sin recortes de audio (clipping).

Los umbrales viven en `config/tecnica.yml`. Sale 0 sólo si los 5 puntos son sí (un «no aplica» cuenta como sí).
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import subprocess
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path

import numpy as np
import yaml

from .. import plazo
from ..config import raiz_repo
from . import estilo

BASE = "técnica"

# Valores por omisión (los de la especificación); `config/tecnica.yml` los reemplaza clave por clave.
POR_OMISION: dict = {
    "subtitulos": {"tolerancia_s": 0.15, "ventana_s": 1.5, "parecido_min": 0.75, "exigir_transcripcion": False},
    "destellos": {"negro_max": 32, "blanco_min": 224, "fraccion_min": 0.6, "salto_min": 0.4, "cuadros_max": 2},
    "congelados": {"max_s": 0.5, "dif_percentil": 99.5, "dif_max": 3.0},
    "ducking": {"margen_db": 12, "ventana_s": 0.1, "voz_activa_db": -40, "tolerancia_fraccion": 0.02,
                "max_seguidas": 3, "musica_base_db": -8,
                "sidechain": {"threshold": 0.01, "ratio": 16, "attack": 10, "release": 250, "makeup": 1}},
    "clipping": {"umbral": 0.9, "muestras_seguidas": 3, "plano_max": 1e-4},
    "analisis": {"ancho": 135, "alto": 240},
}


def _fusionar(base: dict, nuevo: dict) -> dict:
    salida = copy.deepcopy(base)
    for k, v in (nuevo or {}).items():
        if isinstance(v, dict) and isinstance(salida.get(k), dict):
            salida[k] = _fusionar(salida[k], v)
        else:
            salida[k] = v
    return salida


def cargar(raiz=None) -> dict:
    """Umbrales de `config/tecnica.yml`. Sin el archivo (o si no se lee) se usan los valores por omisión."""
    ruta = raiz_repo(raiz) / "config" / "tecnica.yml"
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        datos = None
    return _fusionar(POR_OMISION, datos if isinstance(datos, dict) else {})


def _s(x: float) -> str:
    """Segundos con dos decimales y coma, como en los mensajes de la tarea."""
    return f"{x:.2f}".replace(".", ",")


def _punto(nombre: str, ok: bool, detalle: str = "") -> tuple[str, bool, str]:
    return (f"[{BASE}] {nombre}", bool(ok), detalle)


def _norm(t) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn")
    return "".join(c for c in sin if c.isalnum())


def tiene_video(plan: dict) -> bool:
    """True si la pieza trae video (plantilla v2, origen o un compilado de segmentos con video)."""
    return bool(estilo.es_v2(plan) or plan.get("origen") or plan.get("tipo") == "compilado")


def con_originales(plan: dict, lineas) -> dict:
    """Copia del plan cuyas líneas traen `original` (`plan_json` lo quita del plan). Se une por posición."""
    salida = copy.deepcopy(plan)
    for i, ln in enumerate(salida.get("lineas") or []):
        if i < len(lineas):
            o = lineas[i].get("original") if isinstance(lineas[i], dict) else getattr(lineas[i], "original", None)
            if o:
                ln["original"] = o
    return salida


def rojos(puntos) -> list[str]:
    """Motivos de descarte de los puntos en rojo: «puerta técnica: …» o «puerta de estilo: …» según el punto."""
    out = []
    for n, ok, det in puntos:
        if not ok:
            puerta = "puerta técnica" if n.startswith(f"[{BASE}]") else "puerta de estilo"
            out.append(f"{puerta}: {n}" + (f" ({det})" if det else ""))
    return out


# ---------------------------------------------------------------- lectura con ffmpeg

def medir_cuadros(video: Path, cfg: dict) -> dict:
    """Una pasada de ffmpeg: miniatura en gris por tubería, cuadro por cuadro, sin guardar el video en memoria.

    Devuelve {fps, n, negro, blanco, ancla, dif}: fracción de píxeles negros y blancos de cada cuadro; el índice del
    cuadro ancla de cada cuadro y el percentil `congelados.dif_percentil` de |diferencia| contra esa ancla.

    El ancla es el primer cuadro del tramo: un cuadro es «igual» si se parece a ella (percentil de la diferencia ≤
    `dif_max`); si no, es el ancla nueva. Comparar contra el ancla y no contra el cuadro anterior es lo que distingue un
    congelado de un movimiento lento: un objeto pequeño que se mueve poco por cuadro cambia menos del 0,5 % de los
    píxeles entre dos cuadros seguidos, pero se aleja de la ancla en pocos cuadros.
    """
    a, h = int(cfg["analisis"]["ancho"]), int(cfg["analisis"]["alto"])
    d, c = cfg["destellos"], cfg["congelados"]
    fps = estilo.sondear(Path(video))["fps"] or 30.0
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-an", "-vf", f"scale={a}:{h}:flags=area,format=gray",
           "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    tope = plazo.tiempo_para(300)
    with tempfile.TemporaryFile() as errores:
        return _leer_cuadros(cmd, tope, errores, a, h, d=d, c=c, fps=fps)


def _leer_cuadros(cmd, tope, errores, a, h, *, d, c, fps) -> dict:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errores)
    vencio = threading.Event()

    def matar():
        vencio.set()
        proc.kill()
    reloj = threading.Timer(tope, matar)      # vigilante: mata a ffmpeg si pasa el tiempo
    reloj.daemon = True
    reloj.start()
    negro, blanco, dif, ancla = [], [], [], []
    n_px, previa = a * h, None
    objetivo = float(c["dif_percentil"]) / 100.0 * n_px
    try:
        while True:
            crudo = proc.stdout.read(n_px)
            if len(crudo) < n_px:
                break
            f = np.frombuffer(crudo, dtype=np.uint8)
            negro.append(np.count_nonzero(f <= d["negro_max"]) / n_px)
            blanco.append(np.count_nonzero(f >= d["blanco_min"]) / n_px)
            p = 0.0
            if previa is not None:
                hist = np.bincount(np.abs(f.astype(np.int16) - previa.astype(np.int16)).ravel(), minlength=256)
                p = float(np.searchsorted(np.cumsum(hist), objetivo, side="left"))
            if previa is None or p > c["dif_max"]:
                previa, p = f, 0.0                       # ancla nueva: este cuadro abre un tramo
                ancla.append(len(negro) - 1)
            else:
                ancla.append(ancla[-1])
            dif.append(p)
    finally:
        reloj.cancel()
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if vencio.is_set():
        raise subprocess.TimeoutExpired(cmd, tope)
    if proc.returncode != 0:
        errores.seek(0)
        raise subprocess.CalledProcessError(proc.returncode, "ffmpeg", stderr=errores.read())
    if not negro:
        raise ValueError(f"ffmpeg no pudo leer cuadros de {video}")
    return {"fps": float(fps), "n": len(negro), "negro": np.array(negro), "blanco": np.array(blanco),
            "ancla": np.array(ancla), "dif": np.array(dif)}


def medir_audio(video: Path, inicio: float | None = None, duracion: float | None = None
                ) -> tuple[np.ndarray, int] | None:
    """(muestras f32 [n, canales], frecuencia) con los canales nativos, o None si el archivo no trae audio.

    `inicio` y `duracion` (s) recortan el tramo que se decodifica (la fuente de una toma)."""
    sonda = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels,sample_rate",
        "-of", "json", str(video)], timeout=plazo.tiempo_para(60)))
    pistas = sonda.get("streams") or []
    if not pistas:
        return None
    try:
        canales, sr = int(pistas[0].get("channels") or 2), int(pistas[0].get("sample_rate") or 48000)
    except (TypeError, ValueError):
        canales, sr = 2, 48000                 # ffprobe no dio números: valores por omisión
    recorte = (["-ss", f"{float(inicio):.3f}"] if inicio else []) + (["-t", f"{float(duracion):.3f}"] if duracion else [])
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *recorte, "-i", str(video), "-vn", "-map", "0:a:0",
                        "-ac", str(canales), "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, timeout=plazo.tiempo_para(300))
    if r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, "ffmpeg", stderr=r.stderr)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    return x[: len(x) // canales * canales].reshape(-1, canales), sr


def _mono(ruta: Path, sr: int = 16000) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(ruta), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "f32le", "-"], capture_output=True, timeout=plazo.tiempo_para(300))
    if r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, "ffmpeg", stderr=r.stderr)
    return np.frombuffer(r.stdout, dtype=np.float32)


# ---------------------------------------------------------------- los 5 puntos (funciones puras sobre las medidas)

def _tramos(mascara: np.ndarray) -> list[tuple[int, int]]:
    """Tramos [inicio, fin) de True consecutivos."""
    if not mascara.size:
        return []
    m = np.concatenate(([False], mascara.astype(bool), [False]))
    cambios = np.flatnonzero(m[1:] != m[:-1])
    return [(int(i), int(j)) for i, j in zip(cambios[::2], cambios[1::2])]


def destellos(med: dict, cfg: dict) -> list[str]:
    """Descripciones de los destellos de 1 a `cuadros_max` cuadros (vacío si no hay)."""
    d, fps = cfg["destellos"], med["fps"]
    falla = []
    for color, frac in (("negro", med["negro"]), ("blanco", med["blanco"])):
        n = len(frac)
        for i, j in _tramos(frac >= d["fraccion_min"]):
            largo = j - i
            if largo > d["cuadros_max"]:
                continue                       # fundido a negro o escena oscura (o clara) larga
            techo = float(frac[i:j].min()) - d["salto_min"]
            vecinos = ([frac[i - 1]] if i > 0 else []) + ([frac[j]] if j < n else [])
            if not vecinos or any(v > techo for v in vecinos):
                continue
            donde = f"cuadro {i}" if largo == 1 else f"cuadros {i} a {j - 1}"
            falla.append(f"destello {color} de {largo} cuadro{'s' if largo > 1 else ''} en {_s(i / fps)} s ({donde})")
    return falla


def congelados(med: dict, cfg: dict) -> tuple[list[str], float]:
    """(tramos congelados de más de `max_s`, tramo de cuadros iguales más largo en s)."""
    c, fps = cfg["congelados"], med["fps"]
    falla, mas_largo = [], 0.0
    ancla = med["ancla"]
    for i, j in _tramos_iguales(ancla):
        dur = (j - i) / fps                    # j - i cuadros iguales a su ancla
        mas_largo = max(mas_largo, dur)
        if dur > c["max_s"]:
            falla.append(f"congelado de {_s(i / fps)} a {_s(j / fps)} s ({_s(dur)} s, cuadros {i} a {j - 1})")
    return falla, mas_largo


def _tramos_iguales(ancla: np.ndarray) -> list[tuple[int, int]]:
    """Tramos [inicio, fin) de cuadros con la misma ancla."""
    if not ancla.size:
        return []
    cortes = np.flatnonzero(ancla[1:] != ancla[:-1]) + 1
    inicios = np.concatenate(([0], cortes))
    fines = np.concatenate((cortes, [len(ancla)]))
    return [(int(i), int(j)) for i, j in zip(inicios, fines)]


def clipping(x: np.ndarray, sr: int, cfg: dict) -> tuple[list[tuple[int, int]], list[str]]:
    """(lista de (canal, muestra) de cada tramo, instantes de los primeros 5) con crestas planas.

    Cresta plana: `muestras_seguidas` o más muestras seguidas del mismo canal con |x| ≥ `umbral` y diferencia entre
    vecinas menor que `plano_max`. Un pico alto pero redondo no cuenta; un recorte duro deja la cresta plana."""
    c = cfg["clipping"]
    k = int(c["muestras_seguidas"])
    tramos: list[tuple[int, int]] = []
    for canal in range(x.shape[1]):
        v = x[:, canal]
        alto = np.abs(v) >= c["umbral"]
        par = np.zeros(len(v), dtype=bool)               # par[i]: la muestra i sigue plana desde la i - 1
        par[1:] = alto[1:] & alto[:-1] & (np.abs(np.diff(v)) < c["plano_max"])
        for i, j in _tramos(par):
            if j - i >= k - 1:
                tramos.append((canal, i - 1))
    tramos.sort(key=lambda t: t[1])
    return tramos, [f"{_s(i / sr)} s" for _, i in tramos[:5]]


def ducking(voz: np.ndarray, musica: np.ndarray, sr: int, cfg: dict) -> tuple[list[tuple[float, float]], int, int]:
    """(tramos malos en s, ventanas con voz juzgadas, ventanas malas) sobre las pistas separadas, mono y a la misma frecuencia."""
    d = cfg["ducking"]
    w = max(1, int(round(d["ventana_s"] * sr)))
    n = min(len(voz), len(musica)) // w
    if n == 0:
        return [], 0, 0

    def db(x):
        r = np.sqrt(np.mean(x[: n * w].reshape(n, w).astype(np.float64) ** 2, axis=1))
        return 20 * np.log10(np.maximum(r, 1e-9))
    v, m = db(voz), db(musica)
    activa = v >= d["voz_activa_db"]
    # La ventana que sólo toca el borde de una frase trae media voz y media música sin comprimir: la voz de referencia es
    # la más fuerte entre la ventana y sus dos vecinas, para no juzgar el borde con una voz a medias.
    ref = v.copy()
    ref[1:] = np.maximum(ref[1:], v[:-1])
    ref[:-1] = np.maximum(ref[:-1], v[1:])
    # La primera ventana de cada frase no se juzga: el compresor aún no ha bajado la música (ataque) y la ventana trae
    # sobre todo el silencio de antes. Una voz que dura menos de dos ventanas no se mide.
    establecida = activa.copy()
    establecida[1:] &= activa[:-1]
    establecida[0] = False
    mala = establecida & (m > ref - d["margen_db"])
    tramos = [(i * d["ventana_s"], j * d["ventana_s"]) for i, j in _tramos(mala)]
    return tramos, int(establecida.sum()), int(mala.sum())


def _ducking_malo(tramos, con_voz: int, malas: int, cfg: dict, ventana: float) -> bool:
    d = cfg["ducking"]
    seguidas = max((round((b - a) / ventana) for a, b in tramos), default=0)
    return bool(con_voz) and (malas > d["tolerancia_fraccion"] * con_voz or seguidas >= d["max_seguidas"])


def _primera_palabra(texto) -> str:
    """Primer token del texto con letras o cifras («— ¡Vamos ya!» da «¡Vamos»); vacío si no hay ninguno."""
    return next((t for t in str(texto or "").split() if _norm(t)), "")


def subtitulos(plan: dict, cfg: dict) -> tuple[bool, str]:
    """Cada línea con voz empieza a `tolerancia_s` o menos de su primera palabra en la transcripción por palabra."""
    s = cfg["subtitulos"]
    lineas = [(i, ln) for i, ln in enumerate(plan.get("lineas") or [], start=1)
              if str(ln.get("frase") or "").strip() and _primera_palabra(ln.get("original"))]
    if not lineas:
        return True, "no aplica (la pieza no trae líneas con voz)"
    palabras = ((plan.get("transcripcion") or {}).get("palabras")) or []
    if not palabras:
        if s["exigir_transcripcion"]:
            return False, "la pieza trae voz y el plan no trae transcripción por palabra (exigir_transcripcion: true)"
        return True, "no aplica (sin transcripción por palabra: entra con la tarea de voces)"
    fallas, medidas = [], []
    for i, ln in lineas:
        t0 = float(ln.get("t0", 0))
        primera = _primera_palabra(ln["original"])
        buscada = _norm(primera)
        cerca = [p for p in palabras if abs(float(p["t0"]) - t0) <= s["ventana_s"]
                 and difflib.SequenceMatcher(None, buscada, _norm(p["texto"])).ratio() >= s["parecido_min"]]
        if not cerca:
            fallas.append(f"línea {i} «{ln['frase']}»: no se encontró «{primera}» cerca de {_s(t0)} s")
            continue
        p = min(cerca, key=lambda q: abs(float(q["t0"]) - t0))
        delta = t0 - float(p["t0"])
        medidas.append(abs(delta))
        if abs(delta) > s["tolerancia_s"]:
            cuando = f"+{_s(delta)} s después de" if delta > 0 else f"−{_s(-delta)} s antes de"
            fallas.append(f"línea {i} «{ln['frase']}»: empieza {cuando} «{primera}» ({_s(float(p['t0']))} s)")
    if fallas:
        return False, "; ".join(fallas) + f" (tolerancia {_s(s['tolerancia_s'])} s)"
    return True, f"{len(lineas)} líneas; desfase máximo {_s(max(medidas))} s (tolerancia {_s(s['tolerancia_s'])} s)"


# ---------------------------------------------------------------- la puerta

def _audio_a_medir(video: Path, plan: dict):
    """((muestras, frecuencia) | None, «en la fuente» | «en el MP4»).

    El recorte duro está en el audio que aún no pasó por el AAC de la toma: el AAC redondea las crestas y borra la
    prueba (y a la inversa, un pico limpio se pasa de 1,0 al codificarse). Si el plan es de video con audio original y la
    fuente sigue en disco, se decodifica su tramo; si no (música, informativa, compilado, fuente borrada), se mide el MP4."""
    origen = plan.get("origen") or {}
    ruta = Path(str(origen.get("archivo") or ""))
    if estilo.tipo_audio(plan) == "original" and origen.get("archivo") and ruta.is_file():
        try:
            m = medir_audio(ruta, float(origen.get("inicio") or 0), float(origen.get("duracion") or plan.get("duracion") or 0) or None)
        except (subprocess.SubprocessError, OSError, ValueError, KeyError):
            m = None
        if m is not None:
            return m, "en la fuente"
    return medir_audio(video), "en el MP4"


def revisar(video: Path, plan: dict, cfg: dict, *, pistas: dict | None = None) -> list[tuple[str, bool, str]]:
    """Los 5 puntos como (nombre, ok, detalle); cada nombre empieza con `[técnica]`. Sin LLM.

    `pistas`: {"voz": Path, "musica": Path}, las pistas separadas de una mezcla (la voz y la música ya procesada).
    """
    video = Path(video)
    res: list[tuple[str, bool, str]] = []
    s, d, c, du, cl = cfg["subtitulos"], cfg["destellos"], cfg["congelados"], cfg["ducking"], cfg["clipping"]

    ok, det = subtitulos(plan, cfg)
    res.append(_punto(f"Subtítulo a ±{round(s['tolerancia_s'] * 1000)} ms de su palabra", ok, det))

    video_con_imagen = tiene_video(plan)
    # Los cuadros sólo se leen si hay algo que medir: los destellos valen en toda pieza; los congelados, con video.
    med = medir_cuadros(video, cfg)
    falla = destellos(med, cfg)
    res.append(_punto("Sin destellos de 1 cuadro entre cortes", not falla,
                      "; ".join(falla) if falla else f"{med['n']} cuadros sin destellos"))

    if not video_con_imagen:
        res.append(_punto(f"Sin congelados de más de {_s(c['max_s'])} s", True,
                          "no aplica (pieza sin video: su movimiento lo mide la puerta de estilo)"))
    else:
        falla, largo = congelados(med, cfg)
        res.append(_punto(f"Sin congelados de más de {_s(c['max_s'])} s", not falla,
                          "; ".join(falla) if falla else f"tramo igual más largo {_s(largo)} s"))

    audio_plan = plan.get("audio")
    tipo = audio_plan.get("tipo") if isinstance(audio_plan, dict) else audio_plan
    nombre_duck = f"Música {du['margen_db']:g} dB bajo la voz (ducking)"
    if pistas is None and tipo != "mezcla":
        res.append(_punto(nombre_duck, True, "no aplica (la pieza no mezcla música con voz)"))
    elif pistas is None:
        res.append(_punto(nombre_duck, False, "el plan declara una mezcla y no trae las pistas separadas de voz y música"))
    else:
        voz, mus = _mono(Path(pistas["voz"])), _mono(Path(pistas["musica"]))
        tramos, con_voz, malas = ducking(voz, mus, 16000, cfg)
        if _ducking_malo(tramos, con_voz, malas, cfg, du["ventana_s"]):
            lista = ", ".join(f"{_s(a)}–{_s(b)} s" for a, b in tramos[:5])
            res.append(_punto(nombre_duck, False,
                              f"la música queda a menos de {du['margen_db']:g} dB de la voz en {lista} "
                              f"({malas} de {con_voz} ventanas con voz)"))
        else:
            res.append(_punto(nombre_duck, True, f"{malas} de {con_voz} ventanas con voz incumplen el margen"
                                                 if con_voz else "sin ventanas con voz"))

    muestras, donde = _audio_a_medir(video, plan)
    if muestras is None:
        res.append(_punto("Sin recortes de audio (clipping)", True, "no aplica (el MP4 no trae audio)"))
    else:
        x, sr = muestras
        tramos, instantes = clipping(x, sr, cfg)
        pico = float(np.abs(x).max()) if x.size else 0.0
        if tramos:
            res.append(_punto("Sin recortes de audio (clipping)", False,
                              f"{len(tramos)} crestas planas (≥ {cl['muestras_seguidas']} muestras seguidas a "
                              f"{cl['umbral']:g} o más, {donde}) en {', '.join(instantes)}"
                              + (f" y {len(tramos) - len(instantes)} más" if len(tramos) > len(instantes) else "")))
        else:
            res.append(_punto("Sin recortes de audio (clipping)", True, f"pico {pico:.3f}, sin crestas planas {donde} (umbral {cl['umbral']:g})"))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.editar.tecnica",
                                 description="Puerta técnica por código sobre un MP4 y su plan JSON.")
    ap.add_argument("video")
    ap.add_argument("plan")
    ap.add_argument("--voz", default=None, help="pista de la voz (WAV) de una mezcla")
    ap.add_argument("--musica", default=None, help="pista de la música ya comprimida (WAV) de una mezcla")
    ap.add_argument("--raiz", default=None)
    args = ap.parse_args(argv)
    if bool(args.voz) != bool(args.musica):
        ap.error("--voz y --musica van juntas")
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    pistas = {"voz": Path(args.voz), "musica": Path(args.musica)} if args.voz else None
    puntos = revisar(Path(args.video), plan, cargar(args.raiz), pistas=pistas)
    print(estilo.informe_md("Puerta técnica", puntos))
    return 0 if all(ok for _, ok, _ in puntos) else 1


if __name__ == "__main__":
    sys.exit(main())

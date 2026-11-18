"""Puerta automática de estilo: `python -m fabrica.editar.estilo VIDEO.mp4 PLAN.json`.

Devuelve una lista de sí o no por punto y sale 0 sólo si todo es sí. Sin LLM: sólo ffprobe, el
plan de la toma y `marca.yml`.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

from . import cierre as mod_cierre
from . import dialogo as mod_dialogo
from . import lienzo
from .lienzo import Toma, _limpia, partir
from .marca import Marca

# Tolerancias de la puerta (compresión H.264); no son medidas de la marca.
# Diferencia media por canal (0 a 255) dentro de la máscara de tinta de la página esperada.
UMBRAL_TINTA = 15.0
# Diferencia media en gris entre miniaturas de 64 px del cuerpo del MP4 y de la fuente.
UMBRAL_CUERPO = 8.0
# La tinta es donde la página esperada difiere del papel en más de este valor (canal máximo).
DIF_TINTA = 48
DILATAR_TINTA_PX = 2
MINIATURA_ANCHO = 64
T_CABECERA_S = 0.5
# La duración real (ffprobe) debe coincidir con la del plan: un fotograma más un margen pequeño.
TOL_DURACION_S = 1 / 30 + 0.1

RE_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣\U0001F1E6-\U0001F1FF]")


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.upper()) if unicodedata.category(c) != "Mn")


def sondear(ruta: Path) -> dict:
    out = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,profile:format=duration",
        "-of", "json", str(ruta)]))
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in out["streams"] if s["codec_type"] == "audio"), {})
    num, _, den = str(v.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"video": v, "audio": a, "duracion": float(out["format"]["duration"]), "fps": fps}


def faststart(ruta: Path) -> bool:
    """True si el átomo `moov` va antes que `mdat` (el MP4 arranca sin descargarse entero)."""
    pos, moov, mdat = 0, None, None
    with open(ruta, "rb") as f:
        tam = f.seek(0, 2)
        while pos + 8 <= tam:
            f.seek(pos)
            cab = f.read(8)
            size, tipo = struct.unpack(">I4s", cab)
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            elif size == 0:
                size = tam - pos
            if tipo == b"moov" and moov is None:
                moov = pos
            if tipo == b"mdat" and mdat is None:
                mdat = pos
            if size < 8:
                break
            pos += size
    return moov is not None and mdat is not None and moov < mdat


def _textos(plan: dict) -> list[str]:
    t = plan.get("toma", {})
    textos = [str(t.get(k, "")) for k in ("gancho", "palabra_lapiz", "escena", "credito")]
    for ln in plan.get("lineas", []):
        textos += [str(ln.get(k) or "") for k in ("nombre", "frase", "acotacion")]
    return [x for x in textos if x]


def _toma(plan: dict) -> Toma:
    t = plan.get("toma", {})
    return Toma(numero=int(t.get("numero", 0)), gancho=str(t.get("gancho", "")),
                palabra_lapiz=str(t.get("palabra_lapiz", "")), lapiz=str(t.get("lapiz", "subrayado")),
                escena=str(t.get("escena", "")), credito=str(t.get("credito", "")))


def _fotograma(video: Path, seg: float) -> Image.Image | None:
    """Fotograma del video en el segundo `seg` (RGB), o None si ffmpeg no lo pudo sacar."""
    with tempfile.TemporaryDirectory(prefix="cuadro-") as tmp:
        png = Path(tmp) / "f.png"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, seg):.3f}",
                            "-i", str(video), "-frames:v", "1", str(png)], capture_output=True)
        if r.returncode != 0 or not png.is_file():
            return None
        img = Image.open(png).convert("RGB")
        img.load()
        return img


def _dilatar(mascara: np.ndarray, px: int) -> np.ndarray:
    alto, ancho = mascara.shape
    p = np.pad(mascara, px)
    out = np.zeros_like(mascara)
    for dy in range(2 * px + 1):
        for dx in range(2 * px + 1):
            out |= p[dy:dy + alto, dx:dx + ancho]
    return out


def dif_tinta(real: Image.Image, esperado: Image.Image, papel: Image.Image,
              caja: tuple[int, int, int, int]) -> float:
    """Diferencia media del fotograma contra lo esperado, sólo en los píxeles de tinta.

    La máscara une la tinta esperada y la tinta real (píxeles que difieren del papel en más de
    DIF_TINTA, canal máximo), dilatada DILATAR_TINTA_PX; así el texto añadido también cuenta. Si
    ninguna tiene tinta, mide toda la región. ValueError si la caja es vacía o sale del cuadro, o
    si el resultado no es un número finito.
    """
    x0, y0, x1, y1 = (int(c) for c in caja)
    ancho, alto = esperado.size
    if not (0 <= x0 < x1 <= ancho and 0 <= y0 < y1 <= alto):
        raise ValueError(f"región vacía o fuera del cuadro {ancho}x{alto}: {caja}")
    if real.size != esperado.size:
        raise ValueError(f"tamaño {real.size} distinto de {esperado.size}")
    a = np.asarray(real.convert("RGB"), dtype=np.int16)[y0:y1, x0:x1]
    b = np.asarray(esperado.convert("RGB"), dtype=np.int16)[y0:y1, x0:x1]
    p = np.asarray(papel.convert("RGB"), dtype=np.int16)[y0:y1, x0:x1]
    tinta = (np.abs(b - p).max(axis=2) > DIF_TINTA) | (np.abs(a - p).max(axis=2) > DIF_TINTA)
    mascara = _dilatar(tinta, DILATAR_TINTA_PX)
    d = np.abs(a - b)
    r = float(d[mascara].mean()) if mascara.any() else float(d.mean())
    if not np.isfinite(r):
        raise ValueError("la comparación no dio un número finito")
    return r


def cierre_visible(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """Demuestra en el video, no en el plan, que el cierre trae CORTE. y el crédito.

    Saca el fotograma de duración − 0.2 s y compara por separado la región de CORTE. y la del
    crédito con la página de cierre que corresponde al plan (sólo sobre la tinta esperada).
    """
    dur = float(plan.get("duracion") or 0)
    if dur <= 0:
        return False, "el plan no trae la duración"
    dur_real = sondear(Path(video))["duracion"]
    if abs(dur_real - dur) > TOL_DURACION_S:
        return False, f"el MP4 dura {dur_real:.2f} s y el plan {dur:.2f} s (tolerancia {TOL_DURACION_S:.2f})"
    esperado = mod_cierre.pagina_cierre(marca, _toma(plan))
    real = _fotograma(Path(video), max(0.0, dur_real - 0.2))
    if real is None:
        return False, "no se pudo leer el fotograma final"
    if real.size != esperado.size:
        return False, f"tamaño {real.size} distinto de {esperado.size}"
    papel = lienzo.papel(marca)
    x0, x1 = int(marca.m("margen")), int(marca.ancho - marca.m("margen"))
    corte_y, credito_y = int(marca.m("corte_y")), int(marca.m("credito_y"))
    regiones = {"CORTE.": (x0, corte_y, x1, credito_y),
                "crédito": (x0, credito_y, x1, int(credito_y + 2 * marca.m("tam_credito")))}
    try:
        difs = {n: dif_tinta(real, esperado, papel, caja) for n, caja in regiones.items()}
    except ValueError as e:
        return False, str(e)
    fallan = [n for n, d in difs.items() if not d < UMBRAL_TINTA]
    det = ", ".join(f"{n} {d:.1f}" for n, d in difs.items()) + f" (tolerancia {UMBRAL_TINTA:.0f})"
    if fallan:
        det += "; falla: " + " y ".join(fallan)
    return not fallan, det


def cabecera_visible(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """El fotograma en t = 0.5 s trae la cabecera (gancho, lápiz, fila, escena) del plan."""
    dur = float(plan.get("duracion") or 0)
    try:
        esperado, y_cont = lienzo.cabecera(marca, _toma(plan))
    except ValueError as e:
        return False, f"el plan no se puede dibujar: {e}"
    real = _fotograma(Path(video), min(T_CABECERA_S, dur / 2) if dur > 0 else T_CABECERA_S)
    if real is None:
        return False, "no se pudo leer el fotograma"
    if real.size != esperado.size:
        return False, f"tamaño {real.size} distinto de {esperado.size}"
    try:
        d = dif_tinta(real, esperado, lienzo.papel(marca), (0, 0, marca.ancho, int(y_cont)))
    except ValueError as e:
        return False, str(e)
    return d < UMBRAL_TINTA, f"diferencia media {d:.1f} (tolerancia {UMBRAL_TINTA:.0f})"


def _geometria(plan: dict, marca: Marca) -> dict | None:
    geo = plan.get("geometria")
    if isinstance(geo, dict) and "y_dialogo" in geo:
        return geo
    if plan.get("origen"):
        return None
    try:
        _, y_cont = lienzo.cabecera(marca, _toma(plan))
    except ValueError:
        return None
    return {"y_contenido": y_cont, "cuerpo": None, "y_dialogo": y_cont + marca.m("alto_dialogo_sin_video")}


def dialogo_visible(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """En el punto medio de cada línea, el video muestra el bloque de diálogo esperado."""
    lineas = plan.get("lineas", [])
    if not lineas:
        return True, "sin líneas de diálogo"
    geo = _geometria(plan, marca)
    if geo is None:
        return False, "el plan no trae la geometría del diálogo"
    dur = float(plan.get("duracion") or 0)
    y0 = int(geo["y_dialogo"])
    y1 = min(marca.alto, y0 + int(marca.m("alto_nombre") + marca.m("alto_acotacion")
                                  + marca.max_lineas_frase * marca.m("alto_linea_frase")))
    papel = lienzo.papel(marca)
    fallos, detalle = [], []
    for i, ln in enumerate(lineas):
        t0, t1 = float(ln.get("t0", 0)), float(ln.get("t1", 0))
        t = (t0 + t1) / 2
        if dur > 0:
            t = min(t, dur - float(marca.m("cierre_s")) - 0.1)
        try:
            capa = mod_dialogo.bloque(marca, y0, nombre=ln.get("nombre"), frase=ln.get("frase"),
                                      acotacion=ln.get("acotacion"))
        except ValueError as e:
            fallos.append(f"línea {i + 1}: {e}")
            continue
        esperado = papel.copy()
        esperado.alpha_composite(capa)
        real = _fotograma(Path(video), t)
        if real is None or real.size != esperado.size:
            fallos.append(f"línea {i + 1}: no se pudo leer el fotograma en {t:.2f} s")
            continue
        try:
            d = dif_tinta(real, esperado, papel, (0, y0, marca.ancho, y1))
        except ValueError as e:
            fallos.append(f"línea {i + 1}: {e}")
            continue
        detalle.append(f"{d:.1f}")
        if not d < UMBRAL_TINTA:
            fallos.append(f"línea {i + 1} distinta en {t:.2f} s (diferencia {d:.1f})")
    if fallos:
        return False, "; ".join(fallos) + f" (tolerancia {UMBRAL_TINTA:.0f})"
    return True, f"diferencias {', '.join(detalle)} (tolerancia {UMBRAL_TINTA:.0f})"


def _miniatura_gris(img: Image.Image, ancho: int, alto: int) -> np.ndarray:
    return np.asarray(img.convert("L").resize((ancho, alto), Image.LANCZOS), dtype=np.float32)


def cuerpo_coincide(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """El cuerpo del MP4 es el metraje de origen en el mismo instante (miniaturas en gris)."""
    origen = plan.get("origen")
    geo = plan.get("geometria") or {}
    cuerpo = geo.get("cuerpo")
    if not origen and not cuerpo:
        return True, "no aplica (toma tipográfica, sin cuerpo de video)"
    if not cuerpo:
        return False, "el plan trae origen pero no la caja del cuerpo"
    faltan = [k for k in ("archivo", "sha256", "inicio", "duracion") if not (origen or {}).get(k) and (origen or {}).get(k) != 0]
    if faltan:
        return False, "el plan declara cuerpo de video pero el origen no trae: " + ", ".join(faltan)
    if cuerpo.get("ancho", 0) <= 0 or cuerpo.get("alto", 0) <= 0:
        return False, f"la caja del cuerpo es inválida: {cuerpo}"
    ruta = Path(str(origen["archivo"]))
    if not ruta.is_file():
        return False, f"el archivo de origen no está: {ruta}"
    from .componer import sha256_archivo
    if sha256_archivo(ruta) != origen["sha256"]:
        return False, "el archivo de origen no coincide con el sha256 del plan"
    dur = float(plan.get("duracion") or 0)
    inicio = float(origen.get("inicio", 0))
    tope = dur - float(marca.m("cierre_s")) - 0.2
    caja = (cuerpo["x"], cuerpo["y"], cuerpo["x"] + cuerpo["ancho"], cuerpo["y"] + cuerpo["alto"])
    alto_min = max(1, int(round(MINIATURA_ANCHO * cuerpo["alto"] / cuerpo["ancho"])))
    difs = []
    for frac in (0.3, 0.7):
        t = min(dur * frac, tope)
        real = _fotograma(Path(video), t)
        fuente = _fotograma(ruta, inicio + t)
        if real is None or fuente is None:
            return False, f"no se pudo leer el fotograma en {t:.2f} s"
        a = _miniatura_gris(real.crop(caja), MINIATURA_ANCHO, alto_min)
        b = _miniatura_gris(fuente, MINIATURA_ANCHO, alto_min)
        difs.append(float(np.abs(a - b).mean()))
    return max(difs) < UMBRAL_CUERPO, \
        f"diferencias {', '.join(f'{d:.1f}' for d in difs)} (tolerancia {UMBRAL_CUERPO:.0f})"


def textos_caben(plan: dict, marca: Marca) -> tuple[bool, str]:
    """Ningún texto se sale de sus márgenes (ni una palabra sola más ancha que la línea)."""
    t = plan.get("toma", {})
    malos: list[str] = []
    util = marca.ancho - 2 * marca.m("margen")
    ancho_col = marca.columna[1] - marca.columna[0]

    def partible(nombre: str, texto: str, f, ancho: float) -> None:
        try:
            partir(texto, f, ancho)
        except ValueError as e:
            malos.append(f"{nombre}: {e}")

    def una_linea(nombre: str, texto: str, f, ancho: float) -> None:
        if texto and f.getlength(texto) > ancho:
            malos.append(f"{nombre} mide {f.getlength(texto):.0f} px y caben {ancho:.0f}")

    partible("gancho", str(t.get("gancho", "")).upper(), marca.fuente("bold", marca.m("tam_gancho")), util)
    una_linea("escena", str(t.get("escena", "")).upper(), marca.fuente("regular", marca.m("tam_escena")), util)
    una_linea("crédito", str(t.get("credito", "")), marca.fuente("regular", marca.m("tam_credito")), util)
    for i, ln in enumerate(plan.get("lineas", []), 1):
        if ln.get("frase"):
            partible(f"frase {i}", str(ln["frase"]), marca.fuente("regular", marca.m("tam_frase")), ancho_col)
        una_linea(f"nombre {i}", str(ln.get("nombre") or "").upper(),
                  marca.fuente("regular", marca.m("tam_nombre")), ancho_col)
    return not malos, "; ".join(malos)


def revisar(video: Path, plan: dict, marca: Marca) -> list[tuple[str, bool, str]]:
    """Lista de (punto, ok, detalle). Cada nombre empieza con su base: [fotograma] o [plan].

    [fotograma] = medido sobre el MP4 (ffprobe o píxeles de sus fotogramas); [plan] = leído del
    plan JSON y de marca.yml.
    """
    res: list[tuple[str, bool, str]] = []

    def punto(base: str, nombre: str, ok: bool, detalle: str = "") -> None:
        res.append((f"[{base}] {nombre}", bool(ok), detalle))

    from .componer import sha256_archivo
    punto("fotograma", "sha256 del MP4 revisado", True, sha256_archivo(Path(video)))
    info = sondear(Path(video))
    v, a = info["video"], info["audio"]
    ancho, alto = marca.ancho, marca.alto
    punto("fotograma", f"Resolución {ancho}x{alto}", (v.get("width"), v.get("height")) == (ancho, alto),
          f"{v.get('width')}x{v.get('height')}")
    punto("fotograma", "Códec h264 + aac", v.get("codec_name") == "h264" and a.get("codec_name") == "aac",
          f"{v.get('codec_name')} + {a.get('codec_name')}")
    punto("fotograma", "Formato yuv420p", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt")))
    punto("fotograma", "30 fps", abs(info["fps"] - 30) < 0.01, f"{info['fps']:.2f}")
    punto("fotograma", "Duración de 7 a 30 s", 7 <= info["duracion"] <= 30, f"{info['duracion']:.2f} s")
    punto("fotograma", "faststart (moov antes de mdat)", faststart(Path(video)))
    dur_plan = float(plan.get("duracion") or 0)
    punto("fotograma", "Duración igual a la del plan", abs(info["duracion"] - dur_plan) <= TOL_DURACION_S,
          f"MP4 {info['duracion']:.2f} s, plan {dur_plan:.2f} s (tolerancia {TOL_DURACION_S:.2f})")

    t = plan.get("toma", {})
    gancho = str(t.get("gancho", ""))
    palabras = gancho.split()
    punto("plan", f"Gancho de {marca.max_palabras_gancho} palabras o menos",
          0 < len(palabras) <= marca.max_palabras_gancho, f"{len(palabras)} palabras")
    f_g = marca.fuente("bold", marca.m("tam_gancho"))
    try:
        lineas_g = partir(gancho.upper(), f_g, marca.ancho - 2 * marca.m("margen"))
        punto("plan", "Gancho en 2 líneas como máximo", 0 < len(lineas_g) <= 2, f"{len(lineas_g)} líneas")
    except ValueError as e:
        punto("plan", "Gancho en 2 líneas como máximo", False, str(e))
    objetivo = _limpia(str(t.get("palabra_lapiz", "")))
    punto("plan", "Palabra del lápiz está en el gancho",
          bool(objetivo) and objetivo in [_limpia(p) for p in palabras], str(t.get("palabra_lapiz", "")))

    malas = []
    for ln in plan.get("lineas", []):
        try:
            mod_dialogo.validar_bloque(marca, ln.get("nombre"), ln.get("frase"), ln.get("acotacion"))
        except ValueError as e:
            malas.append(str(e))
    punto("plan", f"Frases de {marca.max_lineas_frase} líneas o menos en la columna", not malas, "; ".join(malas))
    ok_caben, det_caben = textos_caben(plan, marca)
    punto("plan", "Textos caben en sus márgenes", ok_caben, det_caben)

    punto("plan", "Crédito presente y no vacío", bool(str(t.get("credito", "")).strip()), str(t.get("credito", "")))
    ok_cierre, det_cierre = cierre_visible(Path(video), plan, marca)
    punto("fotograma", "Cierre visible en el video (CORTE. y crédito)", ok_cierre, det_cierre)
    ok_cab, det_cab = cabecera_visible(Path(video), plan, marca)
    punto("fotograma", "Cabecera en el video", ok_cab, det_cab)
    ok_dia, det_dia = dialogo_visible(Path(video), plan, marca)
    punto("fotograma", "Diálogo en el video", ok_dia, det_dia)
    ok_cue, det_cue = cuerpo_coincide(Path(video), plan, marca)
    punto("fotograma", "Cuerpo coincide con el origen", ok_cue, det_cue)

    col = plan.get("colores", {})
    colores_ok = all(str(col.get(k, "")).lower() == str(marca.datos["colores"][k]).lower()
                     for k in ("papel", "tinta", "lapiz_rojo"))
    punto("plan", "Colores iguales a marca.yml", colores_ok)
    fu = plan.get("fuente", {})
    archivos = marca.datos["fuente"]["archivos"]
    fuente_ok = (fu.get("familia") == marca.datos["fuente"]["familia"]
                 and fu.get("archivos") == archivos
                 and all((marca.raiz / archivos[e]).is_file() for e in archivos))
    punto("plan", "Fuente igual a marca.yml (familia y archivos presentes)", fuente_ok, str(fu.get("familia")))

    textos = _textos(plan)
    prohibidas = marca.ganchos_prohibidos()
    hallados = [p for p in prohibidas for x in textos if _sin_acentos(p) in _sin_acentos(x)]
    punto("plan", "Sin ganchos genéricos prohibidos", bool(prohibidas) and not hallados,
          ", ".join(hallados) if hallados else ("lista vacía en marca.yml" if not prohibidas else ""))
    emojis = [x for x in textos if RE_EMOJI.search(x)]
    punto("plan", "Sin emojis", not emojis, "; ".join(emojis))
    return res


def informe_md(titulo: str, puntos: list[tuple[str, bool, str]]) -> str:
    lineas = [f"### {titulo}", ""]
    for nombre, ok, det in puntos:
        lineas.append(f"- {'SÍ' if ok else 'NO'}: {nombre}" + (f" ({det})" if det else ""))
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.editar.estilo",
                                 description="Puerta automática de estilo sobre un MP4 y su plan JSON.")
    ap.add_argument("video")
    ap.add_argument("plan")
    ap.add_argument("--raiz", default=None)
    args = ap.parse_args(argv)
    marca = Marca.cargar(args.raiz)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    puntos = revisar(Path(args.video), plan, marca)
    for nombre, ok, det in puntos:
        print(f"{'SÍ' if ok else 'NO'}  {nombre}" + (f"  [{det}]" if det else ""))
    todo = all(ok for _, ok, _ in puntos)
    print("Puerta de estilo: " + ("EN VERDE" if todo else "EN ROJO"))
    return 0 if todo else 1


if __name__ == "__main__":
    sys.exit(main())

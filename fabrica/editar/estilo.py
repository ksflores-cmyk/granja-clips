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
from .lienzo import Toma, _limpia, partir
from .marca import Marca

# Tolerancia de la comparación del cierre: diferencia media por canal (0 a 255) en la zona de
# CORTE. y crédito entre el fotograma final y la página de cierre esperada. Es la tolerancia de
# la puerta (compresión H.264), no una medida de la marca.
UMBRAL_CIERRE = 8.0

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


def cierre_visible(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """Demuestra en el video, no en el plan, que el cierre trae CORTE. y el crédito.

    Saca el fotograma de duración − 0.2 s y compara la zona de CORTE. y crédito con la página de
    cierre que corresponde al plan. Si el video no tiene cierre o le falta el crédito, la
    diferencia sube muy por encima de la tolerancia.
    """
    t = plan.get("toma", {})
    dur = float(plan.get("duracion") or 0)
    if dur <= 0:
        return False, "el plan no trae la duración"
    esperado = mod_cierre.pagina_cierre(marca, Toma(
        numero=int(t.get("numero", 0)), gancho=str(t.get("gancho", "")),
        palabra_lapiz=str(t.get("palabra_lapiz", "")), lapiz=str(t.get("lapiz", "subrayado")),
        escena=str(t.get("escena", "")), credito=str(t.get("credito", ""))))
    with tempfile.TemporaryDirectory(prefix="cierre-") as tmp:
        png = Path(tmp) / "fin.png"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, dur - 0.2):.3f}",
                            "-i", str(video), "-frames:v", "1", str(png)], capture_output=True)
        if r.returncode != 0 or not png.is_file():
            return False, "no se pudo leer el fotograma final"
        real = Image.open(png).convert("RGB")
        real.load()
    if real.size != esperado.size:
        return False, f"tamaño {real.size} distinto de {esperado.size}"
    y0 = int(marca.m("corte_y"))
    y1 = int(marca.m("credito_y") + 2 * marca.m("tam_credito"))
    x0, x1 = int(marca.m("margen")), int(marca.ancho - marca.m("margen"))
    a = np.asarray(real, dtype=np.int16)[y0:y1, x0:x1]
    b = np.asarray(esperado, dtype=np.int16)[y0:y1, x0:x1]
    dif = float(np.abs(a - b).mean())
    return dif < UMBRAL_CIERRE, f"diferencia media {dif:.1f} (tolerancia {UMBRAL_CIERRE:.0f})"


def revisar(video: Path, plan: dict, marca: Marca) -> list[tuple[str, bool, str]]:
    """Lista de (punto, ok, detalle)."""
    res: list[tuple[str, bool, str]] = []

    def punto(nombre: str, ok: bool, detalle: str = "") -> None:
        res.append((nombre, bool(ok), detalle))

    info = sondear(Path(video))
    v, a = info["video"], info["audio"]
    ancho, alto = marca.ancho, marca.alto
    punto(f"Resolución {ancho}x{alto}", (v.get("width"), v.get("height")) == (ancho, alto),
          f"{v.get('width')}x{v.get('height')}")
    punto("Códec h264 + aac", v.get("codec_name") == "h264" and a.get("codec_name") == "aac",
          f"{v.get('codec_name')} + {a.get('codec_name')}")
    punto("Formato yuv420p", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt")))
    punto("30 fps", abs(info["fps"] - 30) < 0.01, f"{info['fps']:.2f}")
    punto("Duración de 7 a 30 s", 7 <= info["duracion"] <= 30, f"{info['duracion']:.2f} s")
    punto("faststart (moov antes de mdat)", faststart(Path(video)))

    t = plan.get("toma", {})
    gancho = str(t.get("gancho", ""))
    palabras = gancho.split()
    punto(f"Gancho de {marca.max_palabras_gancho} palabras o menos",
          0 < len(palabras) <= marca.max_palabras_gancho, f"{len(palabras)} palabras")
    f_g = marca.fuente("bold", marca.m("tam_gancho"))
    lineas_g = partir(gancho.upper(), f_g, marca.ancho - 2 * marca.m("margen"))
    punto("Gancho en 2 líneas como máximo", 0 < len(lineas_g) <= 2, f"{len(lineas_g)} líneas")
    objetivo = _limpia(str(t.get("palabra_lapiz", "")))
    punto("Palabra del lápiz está en el gancho", bool(objetivo) and objetivo in [_limpia(p) for p in palabras],
          str(t.get("palabra_lapiz", "")))

    malas = []
    for ln in plan.get("lineas", []):
        try:
            mod_dialogo.validar_bloque(marca, ln.get("nombre"), ln.get("frase"), ln.get("acotacion"))
        except ValueError as e:
            malas.append(str(e))
    punto(f"Frases de {marca.max_lineas_frase} líneas o menos en la columna", not malas, "; ".join(malas))

    punto("Crédito presente y no vacío", bool(str(t.get("credito", "")).strip()), str(t.get("credito", "")))
    ok_cierre, det_cierre = cierre_visible(Path(video), plan, marca)
    punto("Cierre visible en el video (CORTE. y crédito)", ok_cierre, det_cierre)

    col = plan.get("colores", {})
    colores_ok = all(str(col.get(k, "")).lower() == str(marca.datos["colores"][k]).lower()
                     for k in ("papel", "tinta", "lapiz_rojo"))
    punto("Colores iguales a marca.yml", colores_ok)
    fu = plan.get("fuente", {})
    archivos = marca.datos["fuente"]["archivos"]
    fuente_ok = (fu.get("familia") == marca.datos["fuente"]["familia"]
                 and all((marca.raiz / archivos[e]).is_file() for e in archivos))
    punto("Fuente igual a marca.yml (familia y archivos presentes)", fuente_ok, str(fu.get("familia")))

    textos = _textos(plan)
    prohibidas = marca.ganchos_prohibidos()
    hallados = [p for p in prohibidas for x in textos if _sin_acentos(p) in _sin_acentos(x)]
    punto("Sin ganchos genéricos prohibidos", bool(prohibidas) and not hallados,
          ", ".join(hallados) if hallados else ("lista vacía en marca.yml" if not prohibidas else ""))
    emojis = [x for x in textos if RE_EMOJI.search(x)]
    punto("Sin emojis", not emojis, "; ".join(emojis))
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

"""Armado del episodio: avances, título, clips (con su meme), outro, unión y capítulos."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from .. import plazo
from ..editar import compilado, lienzo
from ..editar.marca import Marca
from . import memes as mod_memes

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


@dataclass
class Clip:
    numero: int                    # número de toma
    puesto: int                    # puesto en la cuenta atrás
    mp4: Path                      # segmento horizontal (ya con su audio final)
    duracion: float
    creador: str
    nombre: str                    # el nombre que se muestra (hablante)
    caption: str
    pico: float | None = None      # segundo del golpe de volumen (para el meme y los avances)
    meme: dict | None = None       # registro del meme que sonó
    notas: list[str] = field(default_factory=list)


def rango_fechas(desde: date, hasta: date) -> str:
    if desde.month == hasta.month:
        return f"del {desde.day} al {hasta.day} de {MESES[hasta.month - 1]}"
    return f"del {desde.day} de {MESES[desde.month - 1]} al {hasta.day} de {MESES[hasta.month - 1]}"


def _texto_centrado(d: ImageDraw.ImageDraw, ancho: int, y: float, txt: str, f, color) -> None:
    d.text((ancho / 2, y), txt, font=f, fill=color, anchor="mm")


def tarjeta(marca_h: Marca, lineas: list[tuple[str, int, str, str]]) -> Image.Image:
    """Papel de marca a cuadro completo con líneas centradas: (texto, tamaño, estilo, color)."""
    img = lienzo.papel_region(marca_h, 0, 0, marca_h.ancho, marca_h.alto)
    d = ImageDraw.Draw(img)
    alto_total = sum(int(t * 1.35) for _, t, _, _ in lineas)
    y = (marca_h.alto - alto_total) / 2
    for txt, tam, estilo, color in lineas:
        f = marca_h.fuente(estilo, tam)
        while f.getlength(txt) > marca_h.ancho - 240 and tam > 20:
            tam -= 4
            f = marca_h.fuente(estilo, tam)
        _texto_centrado(d, marca_h.ancho, y + tam * 0.675, txt, f, marca_h.color(color))
        y += int(tam * 1.35)
    return img


def tarjeta_titulo(marca_h: Marca, titulo: str, desde: date, hasta: date, episodio: int) -> Image.Image:
    return tarjeta(marca_h, [(marca_h.datos["nombre"].upper(), 34, "bold", "tinta"),
                             (titulo, 96, "bold", "tinta"),
                             (f"Semana {rango_fechas(desde, hasta)} · #{episodio}", 40, "regular", "lapiz_rojo")])


def tarjeta_outro(marca_h: Marca, texto: str, llamado: str, creditos: list[str]) -> Image.Image:
    filas = [(texto, 56, "bold", "tinta"), (llamado, 44, "regular", "lapiz_rojo"), ("", 30, "regular", "tinta"),
             ("Con:", 28, "bold", "tinta")]
    por_fila, fila = [], []
    for c in creditos:
        fila.append(c)
        if len(fila) == 3:
            por_fila.append(" · ".join(fila))
            fila = []
    if fila:
        por_fila.append(" · ".join(fila))
    filas += [(t, 26, "regular", "tinta") for t in por_fila[:8]]
    return tarjeta(marca_h, filas)


def orden_imagen(png: Path, duracion: float, destino: Path, sonido: Path | None = None,
                 ganancia_db: float = -6) -> list[str]:
    """Imagen fija a video de `duracion` s (30 fps, 48 kHz estéreo); con `sonido`, suena al empezar."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-t", f"{duracion:g}", "-i", str(png),
           "-f", "lavfi", "-t", f"{duracion:g}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    if sonido is not None:
        cmd += ["-i", str(sonido)]
        filtro = f"[2:a]volume={ganancia_db:g}dB,aresample=48000[s];[1:a][s]amix=inputs=2:duration=first:normalize=0[a]"
        cmd += ["-filter_complex", filtro, "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    return cmd + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", str(destino)]


def orden_avance(mp4: Path, desde: float, duracion: float, destino: Path) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, desde):g}", "-t", f"{duracion:g}", "-i", str(mp4),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(destino)]


def orden_audio_final(segmento: Path, destino: Path, lufs: float = -16, meme: Path | None = None,
                      t_meme: float = 0.0, ganancia_db: float = -6) -> list[str]:
    """Pasada de audio de cada clip: sonoridad pareja entre streamers y, si toca, el meme en `t_meme`."""
    if meme is None:
        filtro = f"[0:a]loudnorm=I={lufs:g}:TP=-1.5:LRA=11,aresample=48000[a]"
        entradas = ["-i", str(segmento)]
    else:
        ms = int(round(t_meme * 1000))
        filtro = (f"[0:a]loudnorm=I={lufs:g}:TP=-1.5:LRA=11,aresample=48000[o];"
                  f"[1:a]volume={ganancia_db:g}dB,adelay={ms}|{ms}[m];"
                  f"[o][m]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[a]")
        entradas = ["-i", str(segmento), "-i", str(meme)]
    return ["ffmpeg", "-y", "-v", "error", *entradas, "-filter_complex", filtro, "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(destino)]


def momento_meme(clip_dur: float, pico: float | None, meme_dur: float, cierre_s: float = 0.4) -> float | None:
    """Segundo en que entra el meme: justo después del golpe de volumen, sin pisar el cierre. None si no cabe."""
    if pico is None:
        return None
    t = pico + 0.15
    if t + min(meme_dur, 1.0) > clip_dur - cierre_s:
        t = clip_dur - cierre_s - min(meme_dur, 1.0)
    return round(t, 2) if t >= 1.0 else None


def mmss(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def capitulos(intro_s: float, clips: list[Clip]) -> list[tuple[float, str]]:
    """Capítulos de YouTube: el primero en 0:00 (con una intro corta, ya es el primer clip); cada clip con su puesto y
    quién sale; al final, los créditos. Los que durarían menos de 10 s se juntan con el anterior (YouTube los exige
    de 10 s o más)."""
    caps: list[tuple[float, str]] = []
    t = intro_s
    for c in clips:
        titulo = f"#{c.puesto} {c.nombre}: {c.caption}".strip()
        titulo = titulo if len(titulo) <= 90 else titulo[:89] + "…"
        if not caps:
            if intro_s >= 10:
                caps += [(0.0, "Intro"), (t, titulo)]
            else:
                caps.append((0.0, titulo))
        elif t - caps[-1][0] >= 10:
            caps.append((t, titulo))
        t += c.duracion
    if caps and t - caps[-1][0] >= 10:
        caps.append((t, "Créditos"))
    return caps


def unir(marca_h: Marca, partes: list[Path], destino: Path) -> None:
    subprocess.run(compilado.orden_concat(partes, destino, marca_h), check=True, capture_output=True,
                   timeout=plazo.tiempo_para(1800))


def preparar_clip(segmento: Path, destino: Path, clip_dur: float, pico: float | None, meme: mod_memes.Meme | None,
                  cfg_memes: dict) -> dict | None:
    """Pasada de audio del clip con su meme (si cabe). Devuelve el registro del meme que sonó, o None."""
    t = momento_meme(clip_dur, pico, meme.duracion) if meme is not None else None
    usado = meme if (meme is not None and t is not None) else None
    subprocess.run(orden_audio_final(segmento, destino, meme=Path(usado.ruta) if usado else None,
                                     t_meme=t or 0.0, ganancia_db=float(cfg_memes.get("ganancia_db", -6))),
                   check=True, capture_output=True, timeout=plazo.tiempo_para(120))
    return {**usado.registro(), "t": t} if usado else None

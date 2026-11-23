"""Mezcla de música debajo de una voz con ducking (`sidechaincompress`: la voz es la cadena lateral).

Hoy ninguna pieza mezcla música con voz (`componer` lo prohíbe: el audio de un video va tal cual). Queda lista para los
efectos de sonido y cualquier mezcla futura. `mezclar` deja también las pistas separadas (voz y música ya comprimida)
para que la puerta técnica (`tecnica.ducking`) mida el margen entre una y otra. Parámetros: `config/tecnica.yml`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .. import plazo

FORMATO = "aformat=sample_rates=48000:channel_layouts=stereo"


def filtro_ducking(cfg: dict) -> str:
    """Filtro `sidechaincompress` con los parámetros de `cfg["ducking"]["sidechain"]` (entradas: música y voz)."""
    s = cfg["ducking"]["sidechain"]
    return (f"sidechaincompress=threshold={float(s['threshold']):g}:ratio={float(s['ratio']):g}:"
            f"attack={float(s['attack']):g}:release={float(s['release']):g}:makeup={float(s['makeup']):g}")


def rutas_pistas(salida: Path) -> dict[str, Path]:
    salida = Path(salida)
    return {"salida": salida, "voz": salida.with_name(f"{salida.stem}.voz.wav"),
            "musica": salida.with_name(f"{salida.stem}.musica.wav")}


def orden_mezcla(voz: Path, musica: Path, salida: Path, *, duracion: float, cfg: dict, ducking: bool = True) -> list[str]:
    """Orden de ffmpeg (función pura). `ducking=False` mezcla sin comprimir (sólo para comparar en pruebas)."""
    r = rutas_pistas(salida)
    d = f"{duracion:g}"
    base = float(cfg["ducking"]["musica_base_db"])
    comprimir = (f"[m0][v_lat]{filtro_ducking(cfg)}[md]" if ducking else "[v_lat]anullsink;[m0]anull[md]")
    filtros = [
        f"[0:a]{FORMATO},apad,atrim=duration={d},asetpts=PTS-STARTPTS,asplit=3[v_mix][v_lat][v_ref]",
        f"[1:a]{FORMATO},volume={base:g}dB,atrim=duration={d},asetpts=PTS-STARTPTS[m0]",
        comprimir,
        "[md]asplit=2[md_mix][md_ref]",
        "[v_mix][md_mix]amix=inputs=2:duration=first:normalize=0[mix]",
    ]
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(voz), "-stream_loop", "-1", "-i", str(musica),
            "-filter_complex", ";".join(filtros),
            "-map", "[mix]", "-t", d, "-c:a", "pcm_f32le", str(r["salida"]),
            "-map", "[v_ref]", "-t", d, "-c:a", "pcm_f32le", str(r["voz"]),
            "-map", "[md_ref]", "-t", d, "-c:a", "pcm_f32le", str(r["musica"])]


def mezclar(voz: Path, musica: Path, salida: Path, *, duracion: float, cfg: dict, ducking: bool = True) -> dict:
    """Mezcla la música (con la ganancia base y comprimida por la voz) debajo de la voz.

    Escribe `salida` (WAV de la mezcla) y, al lado, `<nombre>.voz.wav` y `<nombre>.musica.wav` (la música ya
    comprimida). Devuelve {"salida", "voz", "musica"} con las tres rutas. La música se repite si es más corta.
    """
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(orden_mezcla(Path(voz), Path(musica), Path(salida), duracion=duracion, cfg=cfg, ducking=ducking),
                   check=True, capture_output=True, timeout=plazo.tiempo_para(300))
    return rutas_pistas(Path(salida))

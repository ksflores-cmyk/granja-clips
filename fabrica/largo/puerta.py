"""Puerta del video largo: lo que se revisa antes de subir. Cualquier rojo y no se sube."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .. import vetados
from ..editar import estilo


def sonoridad(mp4: Path) -> float | None:
    """Sonoridad integrada (LUFS) del episodio, o None si no se pudo medir."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(mp4), "-vn", "-af",
                            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=900)
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
        return float(json.loads(m.group(0))["input_i"]) if m else None
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None


def capitulos_validos(caps: list[tuple[float, str]]) -> tuple[bool, str]:
    if len(caps) < 3:
        return False, f"{len(caps)} capítulos (YouTube pide 3 o más)"
    if caps[0][0] != 0:
        return False, "el primer capítulo no empieza en 0:00"
    cortos = [t for (t, _), (t2, _) in zip(caps, caps[1:]) if t2 - t < 10]
    if cortos:
        return False, f"{len(cortos)} capítulos de menos de 10 s"
    return True, f"{len(caps)} capítulos"


def revisar(mp4: Path, plan: dict, cfg: dict, raiz=None) -> list[tuple[str, bool, str]]:
    puntos = []
    info = estilo.sondear(mp4)
    v = info.get("video") or {}
    ancho, alto = (cfg.get("resolucion") or [1920, 1080])
    puntos.append(("Resolución 16:9", (v.get("width"), v.get("height")) == (ancho, alto),
                   f"{v.get('width')}x{v.get('height')}"))
    puntos.append(("30 fps", abs(float(info.get("fps") or 0) - 30) < 0.01, f"{info.get('fps')}"))
    puntos.append(("Audio AAC", (info.get("audio") or {}).get("codec_name") == "aac",
                   str((info.get("audio") or {}).get("codec_name"))))
    dur = float(info.get("duracion") or 0)
    dur_clips = sum(float(c["duracion"]) for c in plan.get("clips") or [])
    puntos.append(("Duración de los clips", dur_clips >= float(cfg.get("duracion_min_s", 150)),
                   f"{dur_clips:.0f} s de clips (mínimo {cfg.get('duracion_min_s', 150)}); episodio {dur:.0f} s"))
    esperado = float(plan.get("duracion_esperada") or 0)
    puntos.append(("Duración igual al armado", abs(dur - esperado) <= 1.0, f"{dur:.1f} s y el armado dice {esperado:.1f} s"))
    lufs = sonoridad(mp4)
    puntos.append(("Sonoridad", lufs is not None and -20 <= lufs <= -12, f"{lufs} LUFS (entre -20 y -12)"))
    ok, det = capitulos_validos([(float(t), s) for t, s in plan.get("capitulos") or []])
    puntos.append(("Capítulos", ok, det))
    desc = str(plan.get("descripcion") or "")
    sin_credito = [c["creador"] for c in plan.get("clips") or [] if c.get("creador") and c["creador"] not in desc]
    puntos.append(("Créditos en la descripción", not sin_credito,
                   "todos" if not sin_credito else "faltan: " + ", ".join(sin_credito)))
    vet = vetados.logins_vetados(raiz)
    malos = [c["creador"] for c in plan.get("clips") or []
             if str(c.get("creador") or "").rsplit("/", 1)[-1].lower() in vet]
    puntos.append(("Sin creadores vetados", not malos, ", ".join(malos) or "ninguno"))
    n_memes = sum(1 for c in plan.get("clips") or [] if c.get("meme"))
    puntos.append(("Memes con medida", n_memes <= len(plan.get("clips") or []),
                   f"{n_memes} memes en {len(plan.get('clips') or [])} clips"))
    puntos.append(("Título", 0 < len(str(plan.get("titulo") or "")) <= 100, str(plan.get("titulo"))))
    return puntos

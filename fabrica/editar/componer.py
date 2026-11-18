"""Compone el MP4 de una toma: capas PNG (Pillow) y una sola orden de ffmpeg.

MP4 1080x1920 H.264 high yuv420p 30 fps, `-preset veryfast -crf 20`, AAC 128 kb/s 48 kHz,
`+faststart`. El video se escala para caber entero en la caja del cuerpo (ancho de página por el alto libre entre
la cabecera y el diálogo), sin recortar, centrado en horizontal y pegado a la cabecera.
Sin video, el audio es silencio.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from . import cierre as mod_cierre
from . import dialogo as mod_dialogo
from . import lienzo
from .lienzo import Toma
from .marca import Marca


@dataclass
class Linea:
    t0: float
    t1: float
    nombre: str | None = None
    frase: str | None = None
    acotacion: str | None = None
    original: str | None = None      # texto original (sólo para el manifiesto)


def sondear_video(ruta: Path) -> dict:
    """Ancho, alto y presencia de audio de un archivo de video."""
    out = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
        "-of", "json", str(ruta)]))
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    audio = any(s["codec_type"] == "audio" for s in out["streams"])
    try:
        duracion = float(out["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"no se pudo leer la duración de {ruta}") from None
    return {"ancho": int(v["width"]), "alto": int(v["height"]), "audio": audio, "duracion": duracion}


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _par_abajo(n: float) -> int:
    return int(n // 2 * 2)


def alto_zona_dialogo(marca: Marca) -> int:
    """Alto del bloque de diálogo más alto posible: nombre, acotación y las líneas de frase."""
    return int(marca.m("alto_nombre") + marca.m("alto_acotacion")
               + marca.max_lineas_frase * marca.m("alto_linea_frase"))


def alto_caja_cuerpo(marca: Marca, y_contenido: int) -> int:
    """Alto libre para el cuerpo: entre la cabecera y la zona del diálogo, sin invadir el pie."""
    libre = (marca.alto - marca.m("margen_inferior") - y_contenido
             - marca.m("espacio_video_dialogo") - alto_zona_dialogo(marca))
    if libre <= 0:
        raise ValueError(f"no queda alto para el cuerpo bajo la cabecera (y={y_contenido})")
    return _par_abajo(libre)


def ajustar_cuerpo(marca: Marca, y_contenido: int, ancho_v: int, alto_v: int) -> dict:
    """Escala la fuente para que quepa entera en la caja del cuerpo, con su proporción.

    Devuelve {x, y, ancho, alto}: centrada en horizontal y pegada a la cabecera.
    """
    caja_alto = alto_caja_cuerpo(marca, y_contenido)
    if ancho_v <= 0 or alto_v <= 0:
        raise ValueError(f"fuente con tamaño inválido {ancho_v}x{alto_v}")
    if marca.ancho * alto_v <= caja_alto * ancho_v:          # manda el ancho
        ow, oh = marca.ancho, alto_escalado(marca.ancho, ancho_v, alto_v)
    else:                                                      # manda el alto (fuente vertical)
        oh = caja_alto
        ow = int(round(caja_alto * ancho_v / alto_v / 2) * 2)
    ow, oh = min(ow, marca.ancho), min(oh, caja_alto)
    return {"x": (marca.ancho - ow) // 2, "y": int(y_contenido), "ancho": ow, "alto": oh}


def geometria(marca: Marca, y_contenido: int, cuerpo: dict | None) -> dict:
    """Dónde cae cada cosa: cabecera hasta `y_contenido`, cuerpo (o None) y diálogo."""
    if cuerpo is None:
        y_dialogo = y_contenido + marca.m("alto_dialogo_sin_video")
    else:
        y_dialogo = y_contenido + cuerpo["alto"] + marca.m("espacio_video_dialogo")
    return {"y_contenido": int(y_contenido), "cuerpo": cuerpo, "y_dialogo": int(y_dialogo)}


def alto_escalado(ancho_pagina: int, ancho: int, alto: int) -> int:
    """Alto del video al escalarlo al ancho de la página (par, sin recortar)."""
    return int(round(ancho_pagina * alto / ancho / 2) * 2)


def orden_ffmpeg(*, salida: str, duracion: float, ancho: int, cierre_s: float,
                 pagina: str, cierre: str, capas: list[tuple[str, float, float]],
                 video: dict | None = None) -> list[str]:
    """Arma la orden de ffmpeg (función pura; no corre nada).

    `capas`: [(ruta PNG, t0, t1)]. `video`: None o {ruta, inicio, alto, y, audio (bool)}.
    """
    d = f"{duracion:g}"
    entradas = ["-loop", "1", "-framerate", "30", "-t", d, "-i", pagina]
    filtros: list[str] = []
    if video is not None:
        entradas += ["-ss", f"{video['inicio']:g}", "-t", d, "-i", video["ruta"]]
        filtros.append(f"[1:v]scale={video.get('ancho', ancho)}:{video['alto']}:flags=lanczos,fps=30,setsar=1[vid]")
        filtros.append(f"[0:v][vid]overlay={video.get('x', 0)}:{video['y']}:shortest=1[c0]")
    else:
        filtros.append("[0:v]null[c0]")
    base = 2 if video is not None else 1
    actual = "c0"
    for i, (ruta, t0, t1) in enumerate(capas):
        entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", ruta]
        sig = f"c{i + 1}"
        filtros.append(f"[{actual}][{base + i}:v]overlay=0:0:enable='between(t,{t0:g},{t1:g})'[{sig}]")
        actual = sig
    entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", cierre]
    idx_cierre = base + len(capas)
    filtros.append(f"[{actual}][{idx_cierre}:v]overlay=0:0:enable='gte(t,{duracion - cierre_s:.2f})',format=yuv420p[out]")
    if video is not None and video.get("audio", True):
        mapa_audio = "1:a"
    else:
        entradas += ["-f", "lavfi", "-t", d, "-i", "anullsrc=r=48000:cl=stereo"]
        mapa_audio = f"{idx_cierre + 1}:a"
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entradas,
            "-filter_complex", ";".join(filtros), "-map", "[out]", "-map", mapa_audio,
            "-t", d, "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart", salida]


def plan_json(marca: Marca, t: Toma, lineas: list[Linea], duracion: float, *,
              geometria: dict | None = None, origen: dict | None = None) -> dict:
    """Plan de la toma (lo que lee la puerta de estilo): textos, colores y fuente de marca.yml.

    `origen` es {archivo, sha256, inicio, duracion} cuando hay video de fuente y None si no.
    """
    return {
        "toma": {"numero": t.numero, "gancho": t.gancho, "palabra_lapiz": t.palabra_lapiz,
                 "lapiz": t.lapiz, "escena": t.escena, "credito": t.credito},
        "lineas": [{k: v for k, v in asdict(ln).items() if k != "original" and v is not None}
                   for ln in lineas],
        "duracion": duracion,
        "colores": dict(marca.datos["colores"]),
        "fuente": {"familia": marca.datos["fuente"]["familia"],
                   "archivos": dict(marca.datos["fuente"]["archivos"])},
        "origen": origen,
        "geometria": geometria,
    }


def componer(marca: Marca, t: Toma, lineas: list[Linea], salida: Path, *, duracion: float,
             trabajo: Path, fuente_video: Path | None = None, inicio: float = 0.0,
             plan_salida: Path | None = None) -> dict:
    """Renderiza la toma. Devuelve el plan. Lanza ValueError si algún texto no cabe."""
    trabajo = Path(trabajo)
    trabajo.mkdir(parents=True, exist_ok=True)
    # Validar todo antes de pintar o correr ffmpeg
    lienzo.preparar_gancho(marca, t.gancho, t.palabra_lapiz)
    for ln in lineas:
        mod_dialogo.validar_bloque(marca, ln.nombre, ln.frase, ln.acotacion)
        if not 0 <= ln.t0 < ln.t1 <= duracion:
            raise ValueError(f"ventana de tiempo inválida {ln.t0}-{ln.t1} (duración {duracion})")
    pagina, y_contenido = lienzo.cabecera(marca, t)
    video = None
    origen = None
    cuerpo = None
    if fuente_video is not None:
        info = sondear_video(fuente_video)
        if info["duracion"] + 0.05 < inicio + duracion:
            raise ValueError(
                f"la fuente dura {info['duracion']:.2f} s y el recorte pide {inicio:g} + {duracion:g} = "
                f"{inicio + duracion:g} s: el MP4 terminaría antes del cierre")
        cuerpo = ajustar_cuerpo(marca, y_contenido, info["ancho"], info["alto"])
        video = {"ruta": str(fuente_video), "inicio": inicio, "alto": cuerpo["alto"], "ancho": cuerpo["ancho"],
                 "x": cuerpo["x"], "y": cuerpo["y"], "audio": info["audio"]}
        origen = {"archivo": str(fuente_video), "sha256": sha256_archivo(Path(fuente_video)),
                  "inicio": inicio, "duracion": duracion}
    geo = geometria(marca, y_contenido, cuerpo)
    y_dialogo = geo["y_dialogo"]
    if y_dialogo + alto_zona_dialogo(marca) > marca.alto:
        raise ValueError(f"el diálogo se sale del cuadro: termina en {y_dialogo + alto_zona_dialogo(marca)} "
                         f"y el cuadro mide {marca.alto}")
    pagina.save(trabajo / "pagina.png")
    capas = []
    for i, ln in enumerate(lineas):
        ruta = trabajo / f"dialogo_{i:02d}.png"
        mod_dialogo.bloque(marca, y_dialogo, nombre=ln.nombre, frase=ln.frase, acotacion=ln.acotacion).save(ruta)
        capas.append((str(ruta), ln.t0, ln.t1))
    mod_cierre.pagina_cierre(marca, t).save(trabajo / "cierre.png")
    cmd = orden_ffmpeg(salida=str(salida), duracion=duracion, ancho=marca.ancho,
                       cierre_s=float(marca.m("cierre_s")), pagina=str(trabajo / "pagina.png"),
                       cierre=str(trabajo / "cierre.png"), capas=capas, video=video)
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    plan = plan_json(marca, t, lineas, duracion, geometria=geo, origen=origen)
    if plan_salida is not None:
        Path(plan_salida).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan

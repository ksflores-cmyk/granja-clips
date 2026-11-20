"""Compone el MP4 de una toma: capas PNG (Pillow) y una sola orden de ffmpeg.

MP4 1080x1920 H.264 high yuv420p 30 fps, `-preset veryfast -crf 20`, AAC 128 kb/s 48 kHz, `+faststart`.

Plantilla v2 (tomas con video, decisión de Kevin del 19 nov 2026): el clip cubre todo el cuadro con un recorte fijo
(`encuadre.py`, sin zoom ni paneo), conserva su audio original, y la marca va en placas de papel encima (`placas.py`).
Tomas sin video (informativa tipográfica): la página de guion de siempre, con audio en silencio.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from . import agua as mod_agua
from . import cierre as mod_cierre
from . import dialogo as mod_dialogo
from . import encuadre, lienzo, placas
from .lienzo import Toma
from .marca import Marca


PLANTILLA_V2 = "v2-pantalla-completa"


@dataclass
class Linea:
    t0: float
    t1: float
    nombre: str | None = None
    frase: str | None = None
    acotacion: str | None = None
    original: str | None = None      # texto original (sólo para el manifiesto)


def _duracion_pista_video(ruta: Path, v: dict, formato: dict) -> float:
    """Duración de la pista de video (no la del contenedor, que puede ser la del audio).

    Orden: `duration` de la pista; nb_frames / tasa de cuadros; marcas de tiempo de los paquetes
    (del primer PTS válido al final del último paquete).
    """
    def numero(x):
        try:
            n = float(x)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None
    d = numero(v.get("duration"))
    if d:
        return d
    n = numero(v.get("nb_frames"))
    num, _, den = str(v.get("r_frame_rate", "0/1")).partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    if n and fps > 0:
        return n / fps
    paquetes = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,duration_time",
        "-of", "json", str(ruta)])).get("packets", [])
    # Intervalo desde el primer PTS válido hasta el final del último paquete (no "mayor PTS + duración":
    # con una pista que no arranca en 0 eso suma el desfase inicial a la duración).
    pts = []
    for p in paquetes:
        try:
            t = float(p.get("pts_time"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(t):
            pts.append((t, numero(p.get("duration_time"))))
    if pts:
        inicio = min(t for t, _ in pts)
        fin = max(t + (d or 0.0) for t, d in pts)
        if not any(d for _, d in pts) and len(pts) > 1:     # sin duraciones: un paso medio más allá del último
            fin += (max(t for t, _ in pts) - inicio) / (len(pts) - 1)
        if fin - inicio > 0:
            return fin - inicio
    raise ValueError(f"no se pudo leer la duración de la pista de video de {ruta}")


def sondear_video(ruta: Path) -> dict:
    """Ancho, alto, presencia de audio y duración de la pista de video de un archivo."""
    out = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height,duration,nb_frames,r_frame_rate:format=duration",
        "-of", "json", str(ruta)]))
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    audio = any(s["codec_type"] == "audio" for s in out["streams"])
    return {"ancho": int(v["width"]), "alto": int(v["height"]), "audio": audio,
            "duracion": _duracion_pista_video(ruta, v, out.get("format", {}))}


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def alto_zona_dialogo(marca: Marca) -> int:
    """Alto del bloque de diálogo más alto posible de la página de guion (tomas sin video)."""
    return int(marca.m("alto_nombre") + marca.m("alto_acotacion")
               + marca.max_lineas_frase * marca.m("alto_linea_frase"))


def geometria(marca: Marca, y_contenido: int) -> dict:
    """Toma sin video (página de guion): cabecera hasta `y_contenido` y el diálogo bajo la línea de escena."""
    return {"y_contenido": int(y_contenido), "cuerpo": None,
            "y_dialogo": int(y_contenido + marca.m("alto_dialogo_sin_video"))}


def orden_ffmpeg(*, salida: str, duracion: float, ancho: int, cierre_s: float,
                 pagina: str, cierre: str, capas: list[tuple[str, float, float]]) -> list[str]:
    """Orden de ffmpeg de una toma SIN video (función pura; no corre nada). Audio en silencio.

    `capas`: [(ruta PNG a página completa, t0, t1)].
    """
    d = f"{duracion:g}"
    entradas = ["-loop", "1", "-framerate", "30", "-t", d, "-i", pagina]
    filtros: list[str] = ["[0:v]null[c0]"]
    actual = "c0"
    for i, (ruta, t0, t1) in enumerate(capas):
        entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", ruta]
        sig = f"c{i + 1}"
        filtros.append(f"[{actual}][{1 + i}:v]overlay=0:0:enable='between(t,{t0:g},{t1:g})'[{sig}]")
        actual = sig
    entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", cierre]
    idx_cierre = 1 + len(capas)
    filtros.append(f"[{actual}][{idx_cierre}:v]overlay=0:0:enable='gte(t,{duracion - cierre_s:.2f})',format=yuv420p[out]")
    entradas += ["-f", "lavfi", "-t", d, "-i", "anullsrc=r=48000:cl=stereo"]
    return _orden(entradas, filtros, f"{idx_cierre + 1}:a", d, salida)


def orden_ffmpeg_pantalla_completa(*, salida: str, duracion: float, ancho: int, alto: int, video: dict,
                                   placas: list[tuple[str, int, int, float, float]]) -> list[str]:
    """Orden de ffmpeg de una toma CON video en la plantilla v2 (función pura).

    `video`: {ruta, inicio, recorte {x, y, ancho, alto}, audio (bool)}. El clip se recorta (fijo) y se escala para
    cubrir `ancho`x`alto`; encima van las placas `[(ruta PNG, x, y, t0, t1)]`. El audio es siempre el del video de
    origen, tal cual (nunca música ni voz añadidas); sin audio de origen, silencio.
    """
    d = f"{duracion:g}"
    r = video["recorte"]
    entradas = ["-ss", f"{video['inicio']:g}", "-t", d, "-i", video["ruta"]]
    filtros = [f"[0:v]crop={r['ancho']}:{r['alto']}:{r['x']}:{r['y']},scale={ancho}:{alto}:flags=lanczos,"
               f"fps=30,setsar=1[c0]"]
    actual = "c0"
    for i, (ruta, x, y, t0, t1) in enumerate(placas):
        entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", ruta]
        sig = f"c{i + 1}"
        filtros.append(f"[{actual}][{1 + i}:v]overlay={x}:{y}:enable='gte(t,{t0:g})*lt(t,{t1:g})'[{sig}]")
        actual = sig
    filtros.append(f"[{actual}]format=yuv420p[out]")
    if video.get("audio", True):
        mapa_audio = "0:a"
    else:
        entradas += ["-f", "lavfi", "-t", d, "-i", "anullsrc=r=48000:cl=stereo"]
        mapa_audio = f"{1 + len(placas)}:a"
    return _orden(entradas, filtros, mapa_audio, d, salida)


def _orden(entradas: list[str], filtros: list[str], mapa_audio: str, d: str, salida: str) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entradas,
            "-filter_complex", ";".join(filtros), "-map", "[out]", "-map", mapa_audio,
            "-t", d, "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart", salida]


def plan_json(marca: Marca, t: Toma, lineas: list[Linea], duracion: float, *,
              geometria: dict | None = None, origen: dict | None = None, campana: dict | None = None,
              plantilla: str | None = None, audio: str | None = None) -> dict:
    """Plan de la toma (lo que lee la puerta de estilo): textos, colores y fuente de marca.yml.

    `origen` es {archivo, sha256, inicio, duracion} cuando hay video de fuente y None si no. `campana`
    ({id, duracion_s, marca_agua, sin_musica, audio}) sólo se escribe si la toma es de una campaña. `plantilla` y
    `audio` ("original" o "silencio") sólo se escriben en la plantilla v2 de las tomas con video.
    """
    plan = {
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
    if plantilla is not None:
        plan["plantilla"] = plantilla
        plan["audio"] = audio
    if campana is not None:
        plan["campana"] = campana
    return plan


def _campana(ficha: dict | None, audio: str | None) -> dict | None:
    if ficha is None:
        return None
    reglas = ficha.get("reglas") or {}
    if not (ficha.get("marca_agua") or reglas.get("sin_musica") or reglas.get("duracion_s") not in (None, [7, 30])):
        return None
    return {"id": ficha.get("id"), "duracion_s": reglas.get("duracion_s"), "marca_agua": None,
            "sin_musica": bool(reglas.get("sin_musica")), "audio": audio}


def componer(marca: Marca, t: Toma, lineas: list[Linea], salida: Path, *, duracion: float,
             trabajo: Path, fuente_video: Path | None = None, inicio: float = 0.0,
             plan_salida: Path | None = None, ficha: dict | None = None) -> dict:
    """Renderiza la toma. Devuelve el plan. Lanza ValueError si algún texto no cabe.

    Con `fuente_video`: plantilla v2 (pantalla completa, audio original). Sin él: página de guion tipográfica.
    `ficha`: si trae `marca_agua`, el logo va en el lugar fijo de `marca.yml`; ambas cosas quedan declaradas en
    `plan["campana"]`.
    """
    trabajo = Path(trabajo)
    trabajo.mkdir(parents=True, exist_ok=True)
    if fuente_video is not None:
        return _componer_pantalla_completa(marca, t, lineas, Path(salida), duracion=duracion, trabajo=trabajo,
                                           fuente_video=Path(fuente_video), inicio=inicio,
                                           plan_salida=plan_salida, ficha=ficha)
    # Toma sin video: validar todo antes de pintar o correr ffmpeg
    lienzo.preparar_gancho(marca, t.gancho, t.palabra_lapiz)
    for ln in lineas:
        mod_dialogo.validar_bloque(marca, ln.nombre, ln.frase, ln.acotacion)
        if not 0 <= ln.t0 < ln.t1 <= duracion:
            raise ValueError(f"ventana de tiempo inválida {ln.t0}-{ln.t1} (duración {duracion})")
    pagina, y_contenido = lienzo.cabecera(marca, t)
    geo = geometria(marca, y_contenido)
    campana = _campana(ficha, None)
    if ficha is not None and ficha.get("marca_agua"):
        raise ValueError("la campaña exige marca de agua y la toma no lleva video")
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
                       cierre=str(trabajo / "cierre.png"), capas=capas)
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    plan = plan_json(marca, t, lineas, duracion, geometria=geo, origen=None, campana=campana)
    if plan_salida is not None:
        Path(plan_salida).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def _componer_pantalla_completa(marca: Marca, t: Toma, lineas: list[Linea], salida: Path, *, duracion: float,
                                trabajo: Path, fuente_video: Path, inicio: float, plan_salida: Path | None,
                                ficha: dict | None) -> dict:
    for ln in lineas:
        if not 0 <= ln.t0 < ln.t1 <= duracion:
            raise ValueError(f"ventana de tiempo inválida {ln.t0}-{ln.t1} (duración {duracion})")
    info = sondear_video(fuente_video)
    if info["duracion"] + 0.05 < inicio + duracion:
        raise ValueError(
            f"la fuente dura {info['duracion']:.2f} s y el recorte pide {inicio:g} + {duracion:g} = "
            f"{inicio + duracion:g} s: el MP4 terminaría antes del cierre")
    logo = None
    if ficha is not None and ficha.get("marca_agua"):
        logo = marca.raiz / ficha["marca_agua"]
        if not logo.is_file():
            raise ValueError(f"marca de agua ausente: {ficha['marca_agua']}")
    todas = placas.construir(marca, t, lineas, duracion, logo)
    capas = [p.capa() for p in todas]
    problemas = placas.problemas_capas(capas, marca)
    if problemas:
        raise ValueError("las placas no cumplen la pantalla completa: " + "; ".join(problemas))
    recorte = encuadre.elegir_recorte(fuente_video, inicio, duracion, info["ancho"], info["alto"],
                                      marca.ancho, marca.alto, ajustes=marca.pc["recorte"])
    cuerpo = {"x": 0, "y": 0, "ancho": marca.ancho, "alto": marca.alto}
    audio = "original" if info["audio"] else "silencio"
    campana = _campana(ficha, audio)
    if campana is not None and logo is not None:
        placa = next(p for p in todas if p.tipo == "marca_agua")
        campana["marca_agua"] = {"archivo": ficha["marca_agua"], "sha256": sha256_archivo(logo),
                                 "caja": {"x": placa.x, "y": placa.y, "ancho": placa.ancho, "alto": placa.alto}}
    rutas = []
    for i, p in enumerate(todas):
        ruta = trabajo / f"placa_{i:02d}_{p.tipo}.png"
        p.imagen.save(ruta)
        rutas.append((str(ruta), p.x, p.y, p.t0, p.t1))
    video = {"ruta": str(fuente_video), "inicio": inicio, "recorte": recorte, "audio": info["audio"]}
    cmd = orden_ffmpeg_pantalla_completa(salida=str(salida), duracion=duracion, ancho=marca.ancho,
                                         alto=marca.alto, video=video, placas=rutas)
    salida.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    origen = {"archivo": str(fuente_video), "sha256": sha256_archivo(fuente_video),
              "inicio": inicio, "duracion": duracion}
    geo = {"cuerpo": cuerpo, "recorte": recorte, "capas": capas}
    plan = plan_json(marca, t, lineas, duracion, geometria=geo, origen=origen, campana=campana,
                     plantilla=PLANTILLA_V2, audio=audio)
    if plan_salida is not None:
        Path(plan_salida).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan

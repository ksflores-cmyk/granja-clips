"""Compone el MP4 de una toma: capas PNG (Pillow) y una sola orden de ffmpeg.

MP4 1080x1920 H.264 high yuv420p 30 fps, `-preset veryfast -crf 20`, AAC 128 kb/s 48 kHz, `+faststart`.

Plantilla v2 (tomas con video, decisión de Kevin del 19 nov 2026): el clip cubre todo el cuadro con un recorte fijo
(`encuadre.py`, sin zoom ni paneo), conserva su audio original, y la marca va en placas de papel encima (`placas.py`).
Tomas sin video (informativa tipográfica): la página de guion de siempre.
Audio: el de la fuente, tal cual. Sólo una pieza sin audio propio (sin video, o video mudo) lleva `musica` (pista CC0
de `editar.musica`), con fundidos y sonoridad normalizada; el audio de un video nunca se mezcla con música. Sin video
y sin música, silencio.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import plazo
from . import agua as mod_agua
from . import cierre as mod_cierre
from . import dialogo as mod_dialogo
from . import dividida as mod_dividida
from . import encuadre, lienzo, placas
from . import historia as mod_historia
from . import sfx as mod_sfx
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
    voz: str | None = None           # "streamer" | "otra": el color del texto (`marca.yml` voces); None = tinta


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
        "-of", "json", str(ruta)], timeout=plazo.tiempo_para(60))).get("packets", [])
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


def rotacion(v: dict) -> int:
    """Rotación del video en grados (0, 90, 180 o 270), del tag `rotate` o del side_data `rotation`."""
    graus = (v.get("tags") or {}).get("rotate")
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            graus = sd["rotation"]
    try:
        return int(round(float(graus))) % 360
    except (TypeError, ValueError):
        return 0


def sondear_video(ruta: Path) -> dict:
    """Ancho, alto, presencia de audio y duración de la pista de video de un archivo."""
    out = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height,duration,nb_frames,r_frame_rate:stream_tags=rotate:stream_side_data=rotation"
        ":format=duration", "-of", "json", str(ruta)], timeout=plazo.tiempo_para(60)))
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    audio = any(s["codec_type"] == "audio" for s in out["streams"])
    ancho, alto = int(v["width"]), int(v["height"])
    if rotacion(v) in (90, 270):            # ffmpeg autorrota al decodificar: la imagen real es la girada
        ancho, alto = alto, ancho
    return {"ancho": ancho, "alto": alto, "audio": audio,
            "duracion": _duracion_pista_video(ruta, v, out.get("format", {}))}


def canales_audio(ruta: Path) -> int:
    """Canales de la primera pista de audio (0 si no hay o no se pudo leer)."""
    try:
        out = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "json",
            str(ruta)], timeout=plazo.tiempo_para(60)))
        return int(out["streams"][0]["channels"])
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError, TypeError):
        return 0


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


def _filtro_musica(musica: dict, duracion: float) -> str:
    """Cadena de filtros del audio de la pista: formato, fundidos, sonoridad y remuestreo."""
    e, sal = float(musica["entrada_s"]), float(musica["salida_s"])
    return (f"aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d={e:g},"
            f"afade=t=out:st={max(0.0, duracion - sal):g}:d={sal:g},"
            f"loudnorm=I={float(musica['lufs']):g}:TP={float(musica['pico_db']):g}:LRA=11,aresample=48000")


def _entrada_musica(musica: dict, d: str) -> list[str]:
    """Entradas de ffmpeg de la pista: en bucle si es más corta que la toma."""
    return ((["-stream_loop", "-1"] if musica.get("bucle") else [])
            + ["-ss", f"{float(musica.get('inicio_s', 0)):g}", "-t", d, "-i", str(musica["ruta"])])


def orden_ffmpeg(*, salida: str, duracion: float, ancho: int, cierre_s: float,
                 pagina: str, cierre: str, capas: list[tuple[str, float, float]],
                 musica: dict | None = None, entrada_texto: dict | None = None,
                 video: None = None) -> list[str]:
    """Orden de ffmpeg de una toma SIN video (función pura; no corre nada). Con video: `orden_ffmpeg_pantalla_completa`.

    `capas`: [(ruta PNG a página completa, t0, t1)]. Sin `musica`, audio en silencio.
    `musica`: {ruta, inicio_s, entrada_s, salida_s, lufs, pico_db, bucle (bool)}: el audio sale de la pista.
    `entrada_texto`: {duracion_s, subida_px}: cada capa entra con un fundido de opacidad y una subida corta.
    """
    if video is not None:
        raise ValueError("orden_ffmpeg es sólo para tomas sin video: el video va en orden_ffmpeg_pantalla_completa")
    d = f"{duracion:g}"
    entradas = ["-loop", "1", "-framerate", "30", "-t", d, "-i", pagina]
    filtros: list[str] = ["[0:v]null[c0]"]
    actual = "c0"
    for i, (ruta, t0, t1) in enumerate(capas):
        entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", ruta]
        sig = f"c{i + 1}"
        ent = f"{1 + i}:v"
        if entrada_texto is not None:
            dur_e, px = float(entrada_texto["duracion_s"]), float(entrada_texto["subida_px"])
            filtros.append(f"[{ent}]format=rgba,fade=t=in:st={t0:g}:d={dur_e:g}:alpha=1[l{i}]")
            filtros.append(f"[{actual}][l{i}]overlay=x=0:y='if(lt(t,{t0:g}+{dur_e:g}),{px:g}*(1-(t-{t0:g})/{dur_e:g}),0)'"
                           f":eval=frame:enable='between(t,{t0:g},{t1:g})'[{sig}]")
        else:
            filtros.append(f"[{actual}][{ent}]overlay=0:0:enable='between(t,{t0:g},{t1:g})'[{sig}]")
        actual = sig
    entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", cierre]
    idx_cierre = 1 + len(capas)
    filtros.append(f"[{actual}][{idx_cierre}:v]overlay=0:0:enable='gte(t,{duracion - cierre_s:.2f})',format=yuv420p[out]")
    if musica is not None:
        entradas += _entrada_musica(musica, d)
        filtros.append(f"[{idx_cierre + 1}:a]{_filtro_musica(musica, duracion)}[aud]")
        return _orden(entradas, filtros, "[aud]", d, salida)
    entradas += ["-f", "lavfi", "-t", d, "-i", "anullsrc=r=48000:cl=stereo"]
    return _orden(entradas, filtros, f"{idx_cierre + 1}:a", d, salida)


MEDIO_CUADRO_S = 0.016           # un poco menos de medio fotograma (1/60 s)


def _n(x: float) -> str:
    """Segundo con 6 decimales sin ceros de sobra (los bordes de tramo son múltiplos de 1/30: 14.666667)."""
    return f"{float(x):.6f}".rstrip("0").rstrip(".") or "0"


def _expresion_tramos(tramos: list) -> str:
    """Expresión de select: verdadera en los fotogramas que caen dentro de alguno de los tramos [a, b) (segundos del
    recorte). Los bordes se corren medio cuadro para que un fotograma exactamente en el borde (a o b) no dependa del
    redondeo del decimal: el cuadro en `a` entra y el cuadro en `b` no."""
    return "+".join(f"gte(t,{_n(a - MEDIO_CUADRO_S)})*lt(t,{_n(b - MEDIO_CUADRO_S)})" for a, b in tramos)


def _filtro_audio_tramos(tramos: list) -> str:
    """Audio de los tramos pegados, al muestreo exacto (atrim + concat). `aselect` corta por cuadros de audio (~23 ms)
    y cada corte desfasaba el sonido; medido: correlación 0.41 con aselect contra 0.99999 con atrim."""
    n = len(tramos)
    if n == 1:
        return f"[0:a]atrim=start={_n(tramos[0][0])}:end={_n(tramos[0][1])},asetpts=PTS-STARTPTS[a0]"
    partes = [f"[0:a]asplit={n}" + "".join(f"[s{i}]" for i in range(n))]
    partes += [f"[s{i}]atrim=start={_n(a)}:end={_n(b)},asetpts=PTS-STARTPTS[x{i}]" for i, (a, b) in enumerate(tramos)]
    partes.append("".join(f"[x{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a0]")
    return ";".join(partes)


def orden_ffmpeg_pantalla_completa(*, salida: str, duracion: float, ancho: int, alto: int, video: dict,
                                   placas: list[tuple[str, int, int, float, float]],
                                   musica: dict | None = None, tramos: list | None = None) -> list[str]:
    """Orden de ffmpeg de una toma CON video en la plantilla v2 (función pura).

    `video`: {ruta, inicio, recorte {x, y, ancho, alto}, audio (bool)}. El clip se recorta (fijo) y se escala para
    cubrir `ancho`x`alto`. Pantalla dividida: en vez de `recorte`, `paneles` [{recorte, destino {x, y, ancho, alto}}]:
    el video se parte en una copia por panel, cada una con su `crop` y su `scale`, y se juntan (`vstack` si los paneles
    se apilan sin huecos; si no, `overlay` sobre un fondo negro); encima van las placas `[(ruta PNG, x, y, t0, t1)]`. Si el video trae audio, es el de
    origen, tal cual (nunca música ni voz añadidas: ValueError si llega `musica`). Video mudo: la pista `musica`
    (mismo formato que en `orden_ffmpeg`) o, sin ella, silencio.

    `tramos`: [(a, b)] segundos del recorte (relativos a `video["inicio"]`) que se conservan; el resto (silencios) se
    quita con select (video) y atrim + concat (audio), corte seco. `duracion` es entonces la de la salida (la suma de los tramos) y la entrada
    se lee hasta el final del último tramo. Sin `tramos`, la orden es la de siempre.
    """
    if musica is not None and video.get("audio", True):
        raise ValueError("el video trae audio propio: no se le añade música")
    d = f"{duracion:g}"
    d_in = _n(tramos[-1][1]) if tramos else d
    entradas = ["-ss", f"{video['inicio']:g}", "-t", d_in, "-i", video["ruta"]]
    quitar = (f"select='{_expresion_tramos(tramos)}',setpts=N/FRAME_RATE/TB," if tramos else "")
    if video.get("paneles"):
        filtros = _filtros_paneles(video["paneles"], ancho, alto, quitar)
    else:
        r = video["recorte"]
        filtros = [f"[0:v]fps=30,{quitar}crop={r['ancho']}:{r['alto']}:{r['x']}:{r['y']},scale={ancho}:{alto}:flags=lanczos,"
                   f"setsar=1[c0]"]
    actual = "c0"
    for i, (ruta, x, y, t0, t1) in enumerate(placas):
        entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", ruta]
        sig = f"c{i + 1}"
        filtros.append(f"[{actual}][{1 + i}:v]overlay={x}:{y}:enable='gte(t,{t0:g})*lt(t,{t1:g})'[{sig}]")
        actual = sig
    filtros.append(f"[{actual}]format=yuv420p[out]")
    if video.get("audio", True):
        if tramos:
            filtros.append(_filtro_audio_tramos(tramos))
            mapa_audio = "[a0]"
        else:
            mapa_audio = "0:a:0"
    elif musica is not None:
        entradas += _entrada_musica(musica, d)
        filtros.append(f"[{1 + len(placas)}:a]{_filtro_musica(musica, duracion)}[aud]")
        mapa_audio = "[aud]"
    else:
        entradas += ["-f", "lavfi", "-t", d, "-i", "anullsrc=r=48000:cl=stereo"]
        mapa_audio = f"{1 + len(placas)}:a"
    return _orden(entradas, filtros, mapa_audio, d, salida)


def orden_cierre(*, entrada: str, salida: str, duracion: float, ancho: int, alto: int, color: str,
                 destello_s: float, opacidad: float, sfx: str | None = None, ganancia_db: float = -6.0,
                 sonido_s: float = 0.35) -> list[str]:
    """ffmpeg (función pura) del cierre v3: el cuadro sube a `color` hasta `opacidad` en los últimos `destello_s`
    segundos (sin cortar ni alargar el clip) y, con `sfx`, el efecto entra `sonido_s` antes del final, mezclado sin
    normalizar a `ganancia_db`."""
    d = f"{duracion:g}"
    st = max(0.0, duracion - destello_s)
    entradas = ["-i", entrada]
    filtros = [f"color=c={color}:s={ancho}x{alto}:r=30:d={d},format=rgba,colorchannelmixer=aa={opacidad:g},"
               f"fade=t=in:st={st:g}:d={destello_s:g}:alpha=1[f]",
               "[0:v][f]overlay=shortest=1,format=yuv420p[out]"]
    mapa_audio = "0:a:0"
    if sfx:
        entradas += ["-i", sfx]
        ms = int(round(max(0.0, duracion - sonido_s) * 1000))
        filtros.append(f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={ganancia_db:g}dB,"
                       f"adelay={ms}|{ms}[cl]")
        filtros.append("[0:a:0]aformat=sample_rates=48000:channel_layouts=stereo[ab]")
        filtros.append("[ab][cl]amix=inputs=2:normalize=0:duration=first[aout]")
        mapa_audio = "[aout]"
    return _orden(entradas, filtros, mapa_audio, d, salida)


def aplicar_cierre(marca: Marca, mp4: Path, duracion: float, ficha: dict | None, sfx: dict | None,
                   trabajo: Path) -> dict:
    """Pasa el MP4 por `orden_cierre` y devuelve lo que va en `plan["cierre"]`. Sin el efecto verificado, o en una
    ficha con `reglas.sin_musica` (campañas), sólo el destello."""
    cfg = marca.pc["cierre"]
    des, son = cfg["destello"], cfg.get("sonido") or {}
    efecto, motivo = None, None
    if ((ficha or {}).get("reglas") or {}).get("sin_musica"):
        motivo = "la ficha tiene sin_musica: sin sonido de cierre"
    else:
        disponibles = sfx if sfx is not None else mod_sfx.disponibles(marca.raiz)
        efecto = disponibles.get(str(son.get("efecto")))
        if efecto is None:
            motivo = f"el efecto {son.get('efecto')!r} no está verificado o no se bajó: sólo el destello"
    tmp = Path(trabajo) / "cierre.mp4"
    cmd = orden_cierre(entrada=str(mp4), salida=str(tmp), duracion=duracion, ancho=marca.ancho, alto=marca.alto,
                       color="0x" + marca.datos["colores"]["papel"].lstrip("#"),
                       destello_s=float(des["duracion_s"]), opacidad=float(des["opacidad"]),
                       sfx=efecto["ruta"] if efecto else None, ganancia_db=float(son.get("ganancia_db", -6)))
    subprocess.run(cmd, check=True, timeout=plazo.tiempo_para(180))
    shutil.move(str(tmp), str(mp4))
    out = {"estilo": "destello", "duracion_s": float(des["duracion_s"]), "opacidad": float(des["opacidad"]),
           "sonido": ({"id": efecto["id"], "sha256": efecto["sha256"], "ganancia_db": float(son.get("ganancia_db", -6))}
                      if efecto else None)}
    if motivo:
        out["motivo_sin_sonido"] = motivo
    return out


def _filtros_paneles(paneles: list[dict], ancho: int, alto: int, quitar: str = "") -> list[str]:
    """Cadena de filtros de los paneles (función pura): `split`, un `crop`+`scale` por panel y `vstack` u `overlay`."""
    n = len(paneles)
    if n < 2:
        raise ValueError("la pantalla dividida necesita al menos 2 paneles")
    ps = sorted(paneles, key=lambda p: p["destino"]["y"])
    filtros = [f"[0:v]fps=30,{quitar}" + "split=%d%s" % (n, "".join(f"[s{i}]" for i in range(n)))]
    for i, p in enumerate(ps):
        r, d = p["recorte"], p["destino"]
        filtros.append(f"[s{i}]crop={r['ancho']}:{r['alto']}:{r['x']}:{r['y']},"
                       f"scale={d['ancho']}:{d['alto']}:flags=lanczos,setsar=1[p{i}]")
    apilados, y = True, 0
    for p in ps:
        d = p["destino"]
        apilados = apilados and d["x"] == 0 and d["ancho"] == ancho and d["y"] == y
        y += d["alto"]
    if apilados and y == alto:
        filtros.append("".join(f"[p{i}]" for i in range(n)) + f"vstack=inputs={n}[c0]")
        return filtros
    filtros.append(f"color=c=black:s={ancho}x{alto}:r=30,format=yuv420p[f0]")
    for i, p in enumerate(ps):
        d = p["destino"]
        filtros.append(f"[f{i}][p{i}]overlay={d['x']}:{d['y']}[f{i + 1}]")
    filtros.append(f"[f{n}]null[c0]")
    return filtros


def _orden(entradas: list[str], filtros: list[str], mapa_audio: str, d: str, salida: str) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entradas,
            "-filter_complex", ";".join(filtros), "-map", "[out]", "-map", mapa_audio,
            "-t", d, "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart", salida]


def duracion_audio(ruta: Path) -> float:
    """Duración en segundos del audio de un archivo (ffprobe)."""
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "json", str(ruta)], timeout=plazo.tiempo_para(60))
    return float(json.loads(out)["format"]["duration"])


def _tramos_validos(tramos: list | None, duracion: float) -> list[tuple[float, float]] | None:
    """Tramos normalizados, o None si no hay cortes (sin tramos, o un solo tramo que cubre todo). ValueError si no valen."""
    if not tramos:
        return None
    t = [(float(a), float(b)) for a, b in tramos]
    previo = 0.0
    for a, b in t:
        if not (math.isfinite(a) and math.isfinite(b)) or a < previo - 1e-9 or b <= a:
            raise ValueError(f"tramos inválidos (deben crecer y no solaparse): {tramos}")
        previo = b
    if abs(sum(b - a for a, b in t) - duracion) > 0.05:
        raise ValueError(f"los tramos suman {sum(b - a for a, b in t):.3f} s y la duración de la toma es {duracion:g} s")
    if len(t) == 1 and t[0][0] <= 1e-9:
        return None
    return t


def plan_json(marca: Marca, t: Toma, lineas: list[Linea], duracion: float, *,
              geometria: dict | None = None, origen: dict | None = None, campana: dict | None = None,
              plantilla: str | None = None, audio: dict | None = None, movimiento: str | None = None) -> dict:
    """Plan de la toma (lo que lee la puerta de estilo): textos, colores y fuente de marca.yml.

    `origen` es {archivo, sha256, inicio, duracion} cuando hay video de fuente y None si no. `campana`
    ({id, duracion_s, marca_agua, sin_musica, audio}) sólo se escribe si la toma es de una campaña; su `audio` es el
    de la fuente ("original" o "silencio"). `plantilla` sólo se escribe en la plantilla v2 de las tomas con video.
    `audio`: {"tipo": "musica"|"original"|"silencio", ...}; `movimiento`: "entrada_texto" o None.
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
        "audio": audio,
        "movimiento": movimiento,
    }
    if plantilla is not None:
        plan["plantilla"] = plantilla
    if t.subtitulos_del_clip:      # los subtítulos ya vienen en el clip: ni diálogo ni voces nuestras (la puerta de estilo lo lee)
        plan["subtitulos"] = "del clip"
    if t.avisos:            # la toma abre con una placa de aviso (la puerta de estilo lee `avisos` para dibujar la misma)
        plan["avisos"] = list(t.avisos)
        plan["avisos_motivos"] = {k: [str(x) for x in v] for k, v in (t.avisos_motivos or {}).items()}
        if t.spoiler:
            plan["spoiler"] = {"es": True, "motivos": [str(x) for x in (t.avisos_motivos or {}).get("spoiler", [])]}
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


def _musica_ffmpeg(marca: Marca, musica: dict | None, duracion: float) -> dict | None:
    """La pista elegida, con los ajustes de `marca.yml` (sección musica), lista para la orden de ffmpeg."""
    if musica is None:
        return None
    cfg = marca.datos.get("musica") or {}
    if not cfg:
        raise ValueError("marca.yml no define la sección musica")
    inicio_m = float(musica.get("inicio_s") or 0)
    return {"ruta": str(musica["ruta"]), "inicio_s": inicio_m,
            "entrada_s": cfg["entrada_s"], "salida_s": cfg["salida_s"],
            "lufs": cfg["lufs"], "pico_db": cfg["pico_db"],
            "bucle": duracion_audio(Path(musica["ruta"])) - inicio_m < duracion}


def _audio_plan(marca: Marca, musica: dict | None, audio_fuente: bool) -> dict:
    if musica is not None:
        return {"tipo": "musica", "pista": musica["id"], "sha256": musica["sha256"],
                "licencia": f"marca/musica/{musica['id']}/LICENCIA.md", "lufs": marca.datos["musica"]["lufs"],
                "inicio_s": float(musica.get("inicio_s") or 0)}
    return {"tipo": "original" if audio_fuente else "silencio"}


def componer(marca: Marca, t: Toma, lineas: list[Linea], salida: Path, *, duracion: float,
             trabajo: Path, fuente_video: Path | None = None, inicio: float = 0.0,
             plan_salida: Path | None = None, ficha: dict | None = None,
             musica: dict | None = None, movimiento: bool = False, tramos: list | None = None,
             historia: "mod_historia.Planificacion | None" = None, sfx: dict | None = None,
             config_historia: dict | None = None) -> dict:
    """Renderiza la toma. Devuelve el plan. Lanza ValueError si algún texto no cabe.

    Con `fuente_video`: plantilla v2 (pantalla completa, audio original). Sin él: página de guion tipográfica.
    `ficha`: si trae `marca_agua`, el logo va en el lugar fijo de `marca.yml`; ambas cosas quedan declaradas en
    `plan["campana"]`; `reglas.sin_musica` deja el audio original de la fuente tal cual (nunca se añade audio).
    `musica`: pista CC0 ({id, ruta, sha256, inicio_s, ...}, de `editar.musica`) para piezas sin audio propio;
    ValueError si el video trae audio o la ficha tiene `sin_musica`. `movimiento`: entrada de texto sobria
    (sólo piezas sin video). `tramos`: [(a, b)] del recorte (relativos a `inicio`) que se conservan (silencios cortados
    por voces, #19); `duracion` es la de la salida (suma de los tramos). Sólo con video.
    `historia` (`editar.historia.Planificacion`): sólo en tomas v2 con video y con audio en la fuente; el plan se vuelve
    la apertura en frío y los momentos con su tratamiento, en una sola orden de ffmpeg (`duracion` sigue siendo la del
    recorte; la del MP4 es la del mapa). Sin plan, sin audio, con cortes de silencios o con un plan que no valida, la
    toma sale como siempre y `plan["historia"]` dice por qué. `sfx`: {id: {ruta, sha256, ...}} de
    `editar.sfx.disponibles` (por defecto, esos).
    """
    trabajo = Path(trabajo)
    trabajo.mkdir(parents=True, exist_ok=True)
    if musica is not None and ((ficha or {}).get("reglas") or {}).get("sin_musica"):
        raise ValueError("la ficha tiene sin_musica: no se añade música")
    if movimiento and fuente_video is not None:
        raise ValueError("el movimiento de entrada de texto es sólo para piezas sin video")
    if tramos and fuente_video is None:
        raise ValueError("los tramos (silencios cortados) son sólo para piezas con video")
    if fuente_video is not None:
        return _componer_pantalla_completa(marca, t, lineas, Path(salida), duracion=duracion, trabajo=trabajo,
                                           fuente_video=Path(fuente_video), inicio=inicio,
                                           plan_salida=plan_salida, ficha=ficha, musica=musica, tramos=tramos,
                                           historia=historia, sfx=sfx, config_historia=config_historia)
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
    cfg_texto = (marca.datos.get("movimiento") or {}).get("entrada_texto")
    if movimiento and not cfg_texto:
        raise ValueError("marca.yml no define movimiento.entrada_texto")
    cmd = orden_ffmpeg(salida=str(salida), duracion=duracion, ancho=marca.ancho,
                       cierre_s=float(marca.m("cierre_s")), pagina=str(trabajo / "pagina.png"),
                       cierre=str(trabajo / "cierre.png"), capas=capas,
                       musica=_musica_ffmpeg(marca, musica, duracion),
                       entrada_texto=cfg_texto if movimiento else None)
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, timeout=plazo.tiempo_para(300))
    plan = plan_json(marca, t, lineas, duracion, geometria=geo, origen=None, campana=campana,
                     audio=_audio_plan(marca, musica, False),
                     movimiento="entrada_texto" if movimiento else None)
    if historia is not None:
        plan["historia"] = {"proveedor": None, "motivo": "toma sin video: la historia sólo va en tomas v2 con video",
                            "intentos": list(historia.intentos)}
    if plan_salida is not None:
        Path(plan_salida).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def _disposicion_dividida(marca: Marca, t: Toma, lineas: list[Linea], duracion: float, logo, ficha, fuente_video: Path,
                          inicio: float, info: dict, lectura: float | None = None) -> dict | None:
    """None si la pantalla dividida está apagada (marca.yml o ficha). Si no, la decisión de la toma: o bien
    {modo: "dividida", placas, capas, paneles, cara, deteccion, posicion} o {modo: "completa", deteccion} con el motivo
    en `deteccion["motivo"]` (sin facecam, sin sitio para el juego o sin posición libre para las placas)."""
    if not mod_dividida.activa(marca, ficha):
        return None
    det = mod_dividida.detectar(fuente_video, inicio, lectura or duracion, info["ancho"], info["alto"],
                                ajustes=mod_dividida.ajustes_de(marca))
    if det["modo"] != mod_dividida.DIVIDIDA:
        return {"modo": mod_dividida.COMPLETA, "deteccion": det}
    try:
        geo = mod_dividida.paneles(marca, det, info["ancho"], info["alto"])
    except ValueError as e:
        return {"modo": mod_dividida.COMPLETA, "deteccion": {**det, "modo": mod_dividida.COMPLETA, "motivo": str(e)}}
    accion = marca.datos["dividida"]["zona_accion"]
    motivos = []
    for posicion in ("estandar", "alterna"):
        todas = placas.construir(marca, t, lineas, duracion, logo, posicion)
        capas = [p.capa() for p in todas]
        malas = placas.problemas_capas(capas, marca, accion) + mod_dividida.problemas_placas(capas, geo["cara"], marca)
        if not malas:
            return {"modo": mod_dividida.DIVIDIDA, "placas": todas, "capas": capas, "paneles": geo["paneles"],
                    "cara": geo["cara"], "deteccion": det, "posicion": posicion}
        motivos.append(f"{posicion}: " + "; ".join(malas))
    motivo = "ninguna posición de las placas evita la cara y la zona de acción de la división (" + " | ".join(motivos) + ")"
    return {"modo": mod_dividida.COMPLETA, "deteccion": {**det, "modo": mod_dividida.COMPLETA, "motivo": motivo}}


def _componer_pantalla_completa(marca: Marca, t: Toma, lineas: list[Linea], salida: Path, *, duracion: float,
                                trabajo: Path, fuente_video: Path, inicio: float, plan_salida: Path | None,
                                ficha: dict | None, musica: dict | None = None, tramos: list | None = None,
                                historia: "mod_historia.Planificacion | None" = None, sfx: dict | None = None,
                                config_historia: dict | None = None) -> dict:
    tramos = _tramos_validos(tramos, duracion)
    fin_fuente = tramos[-1][1] if tramos else duracion          # hasta dónde se lee de la fuente (relativo al inicio)
    for ln in lineas:
        if not 0 <= ln.t0 < ln.t1 <= duracion:
            raise ValueError(f"ventana de tiempo inválida {ln.t0}-{ln.t1} (duración {duracion})")
    info = sondear_video(fuente_video)
    if info["duracion"] + 0.05 < inicio + fin_fuente:
        raise ValueError(
            f"la fuente dura {info['duracion']:.2f} s y el recorte pide {inicio:g} + {fin_fuente:g} = "
            f"{inicio + fin_fuente:g} s: el MP4 terminaría antes del cierre")
    if musica is not None and info["audio"]:
        raise ValueError("el video trae audio propio: no se le añade música")
    musica_ff = _musica_ffmpeg(marca, musica, duracion)
    logo = None
    if ficha is not None and ficha.get("marca_agua"):
        logo = marca.raiz / ficha["marca_agua"]
        if not logo.is_file():
            raise ValueError(f"marca de agua ausente: {ficha['marca_agua']}")
    tramos_h, motivo_h, cfg_h, sfx_d = None, None, None, {}
    if historia is not None:
        if tramos:
            motivo_h = "hay silencios cortados (voces): historia apagada"
        elif historia.plan is None:
            motivo_h = historia.motivo or "sin plan de historia"
        elif not info["audio"]:
            motivo_h = "la fuente no trae audio: historia apagada"
        else:
            from . import estilo
            cfg_h = config_historia or mod_historia.cargar_config(marca.raiz)
            sfx_d = sfx if sfx is not None else mod_sfx.disponibles(marca.raiz)
            ok_h, motivos_h = mod_historia.validar_plan(
                historia.plan, (inicio, inicio + duracion), estilo.rango_duracion({}, ficha), sfx_d, ficha, cfg_h)
            if ok_h:
                tramos_h = mod_historia.mapa(historia.plan, inicio, inicio + duracion, zoom_max=cfg_h["zoom_max"],
                                             pausa_s=cfg_h["pausa_s"])
            else:
                motivo_h = "el plan no valida: " + "; ".join(motivos_h)
    dur_sal = mod_historia.duracion_salida(tramos_h) if tramos_h else duracion
    lineas_p = (mod_historia.remapear_lineas(lineas, tramos_h, tope=dur_sal - float(marca.m("cierre_s")))
                if tramos_h else lineas)
    # la historia usa un solo recorte (sin paneles): con ella la pantalla dividida no se decide
    div = (None if tramos_h else
           _disposicion_dividida(marca, t, lineas, duracion, logo, ficha, fuente_video, inicio, info, fin_fuente))
    if div is not None and div["modo"] == mod_dividida.DIVIDIDA:
        todas, capas, paneles = div["placas"], div["capas"], div["paneles"]
        recorte = None
    else:
        todas = placas.construir(marca, t, lineas_p, dur_sal, logo)
        capas = [p.capa() for p in todas]
        problemas = placas.problemas_capas(capas, marca)
        if problemas:
            raise ValueError("las placas no cumplen la pantalla completa: " + "; ".join(problemas))
        recorte = encuadre.elegir_recorte(fuente_video, inicio, fin_fuente, info["ancho"], info["alto"],
                                          marca.ancho, marca.alto, ajustes=marca.pc["recorte"])
        paneles = None
    cuerpo = {"x": 0, "y": 0, "ancho": marca.ancho, "alto": marca.alto}
    campana = _campana(ficha, "original" if info["audio"] else "silencio")     # el audio de la fuente
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
    if paneles is not None:
        video["paneles"] = [{"recorte": p["recorte"], "destino": p["destino"]} for p in paneles]
    efectos = []
    if tramos_h:
        usados = {tr.sfx for tr in tramos_h if tr.sfx}
        cmd = mod_historia.orden_ffmpeg(
            salida=str(salida), ancho=marca.ancho, alto=marca.alto, video={**video, "canales": canales_audio(fuente_video)},
            tramos=tramos_h, placas=rutas,
            sfx={i: sfx_d[i]["ruta"] for i in usados}, ganancia_sfx_db=float(cfg_h["ganancia_sfx_db"]),
            pausa_s=float(cfg_h["pausa_s"]))
        efectos = [{"id": tr.sfx, "sha256": sfx_d[tr.sfx]["sha256"], "t": tr.s0,
                    "ganancia_db": float(cfg_h["ganancia_sfx_db"])} for tr in tramos_h if tr.sfx]
    else:
        cmd = orden_ffmpeg_pantalla_completa(salida=str(salida), duracion=duracion, ancho=marca.ancho,
                                             alto=marca.alto, video=video, placas=rutas, musica=musica_ff,
                                             tramos=tramos)
    salida.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, timeout=plazo.tiempo_para(300))
    cierre_plan = None
    if not placas.cierre_con_placa(marca):         # estilo v3, punto 2: destello y claqueta sobre el final
        cierre_plan = aplicar_cierre(marca, salida, dur_sal, ficha, sfx if sfx is not None else None, trabajo)
    origen = {"archivo": str(fuente_video), "sha256": sha256_archivo(fuente_video),
              "inicio": inicio, "duracion": duracion, "audio": "original" if info["audio"] else "silencio"}
    if tramos:
        origen["tramos"] = [[round(a, 3), round(b, 3)] for a, b in tramos]       # sólo si hay silencios cortados
    if paneles is not None:
        geo = {"cuerpo": cuerpo, "disposicion": mod_dividida.DIVIDIDA, "paneles": paneles, "cara": div["cara"],
               "deteccion": div["deteccion"], "posicion_placas": div["posicion"], "capas": capas}
    else:
        geo = {"cuerpo": cuerpo, "recorte": recorte, "capas": capas}
        if div is not None:                      # dividida activa pero sin facecam (o sin sitio): pantalla completa
            geo = {"cuerpo": cuerpo, "disposicion": mod_dividida.COMPLETA, "recorte": recorte,
                   "deteccion": div["deteccion"], "capas": capas}
    audio_plan = _audio_plan(marca, musica, info["audio"])
    if efectos:
        audio_plan = {**audio_plan, "efectos": efectos}
    plan = plan_json(marca, t, lineas_p, dur_sal, geometria=geo, origen=origen, campana=campana,
                     plantilla=PLANTILLA_V2, audio=audio_plan)
    if cierre_plan is not None:
        plan["cierre"] = cierre_plan
    if historia is not None:
        plan["historia"] = (historia.resumen(tramos_h) if tramos_h else
                            {"proveedor": None, "motivo": motivo_h, "intentos": list(historia.intentos)})
    if plan_salida is not None:
        Path(plan_salida).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan

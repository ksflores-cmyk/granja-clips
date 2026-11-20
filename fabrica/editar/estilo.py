"""Puerta automática de estilo: `python -m fabrica.editar.estilo VIDEO.mp4 PLAN.json`.

Devuelve una lista de sí o no por punto y sale 0 sólo si todo es sí. Sin LLM: sólo ffprobe, el
plan de la toma y `marca.yml`.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import agua as mod_agua
from . import cierre as mod_cierre
from . import dialogo as mod_dialogo
from . import lienzo, placas
from .lienzo import Toma, _limpia, partir
from .marca import Marca

# Tolerancias de la puerta (compresión H.264); no son medidas de la marca.
# Diferencia media en gris entre miniaturas de 64 px del cuerpo del MP4 y de la fuente. Calibrada:
# el cuerpo correcto da 0.3 a 1.3 (Sintel y BBB); con la fuente desfasada 0.5, 1 y -1 s da 1.8 a 49,
# y el menor con desfase de 1 s es 4.6 (Sintel, -1 s). Tolerancia 2.5.
# Metraje de juego a 60 fps (N4, render correcto, fuente a 60 fps y salida a 30): testsrc2 0.1 y 0.1;
# testsrc2 con ruido temporal y cambio de tono continuo 0.4 y 0.6; mandelbrot en zoom 0.2 y 0.2. Ningún rojo
# falso, así que no hace falta comparar contra el mejor de ±1 fotograma de la fuente.
UMBRAL_CUERPO = 2.5
# La tinta es donde la página esperada difiere del papel en más de este valor (canal máximo).
DIF_TINTA = 48
DILATAR_TINTA_PX = 2
MINIATURA_ANCHO = 64
PIXEL_DISCREPANTE = 40      # |fotograma - esperado| en gris (tras 1 px de desenfoque) que cuenta como discrepancia
BLOQUE_PX = 24              # tamaño de una letra de la marca
K_BLOQUE = 30               # píxeles discrepantes en un bloque que ponen el punto en rojo
T_CABECERA_S = 0.5
# La duración real (ffprobe) debe coincidir con la del plan: un fotograma más un margen pequeño.
TOL_DURACION_S = 1 / 30 + 0.1

# Marca de agua de campaña (medida en píxeles reales del cuadro, en gris, tras 1 px de desenfoque).
# Calibración con metraje sintético (testsrc2 y mandelbrot): con la capa bien puesta la diferencia contra lo esperado
# es de 0.2 a 3; sin la capa, contra la fuente limpia, pasa de 30. Con un logo no declarado, la esquina da más de 30.
TOL_AGUA = 8.0
SALTO_AGUA = 5.0       # lo esperado debe parecerse al fotograma al menos esto más que la fuente limpia
UMBRAL_LOGO_EXTRA = 12.0
DURACION_FORMATO_S = (7.0, 30.0)

# Plantilla v2 (pantalla completa). La tolerancia de los bordes es la diferencia media en gris, tras 1 px de desenfoque,
# entre las 4 franjas del borde del MP4 y de la fuente recortada y escalada como dice el plan (fuera de las placas).
PLANTILLA_V2 = "v2-pantalla-completa"
BORDE_PX = 24
TOL_BORDE = 6.0

# El MP4 sale a 30 fps y la fuente puede traer 24 o 25: el fotograma del MP4 en `t` puede ser el de la fuente un paso antes
# o después. En las comparaciones con la fuente se acepta el mejor de estos desfases (en segundos).
DESFASES_FUENTE_S = (0.0, -1 / 24, 1 / 24)

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


def _ultimo_fotograma(video: Path) -> Image.Image | None:
    """Último fotograma decodificable del video (RGB), o None si no se pudo sacar."""
    with tempfile.TemporaryDirectory(prefix="ultimo-") as tmp:
        png = Path(tmp) / "u.png"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-sseof", "-0.5",
                            "-i", str(video), "-an", "-update", "1", "-f", "image2", str(png)],
                           capture_output=True)
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


def discrepancia_local(real: Image.Image, esperado: Image.Image, papel: Image.Image,
                       caja: tuple[int, int, int, int]) -> int:
    """Máximo de píxeles discrepantes dentro de un bloque de BLOQUE_PX x BLOQUE_PX.

    Mapa en gris de |fotograma - esperado| tras un desenfoque de 1 px (quita el ruido de H.264),
    mayor que PIXEL_DISCREPANTE, sobre la unión de la tinta esperada y la tinta real (dilatada).
    Una letra cambiada deja un bloque lleno; el ruido de compresión no.

    Calibración (3 muestras reales, MP4 de la propia fábrica): los videos correctos dan 0 en cierre,
    cabecera y diálogo. Cambios de una sola letra, medidos contra el plan original de Sintel: crédito
    "3.0" a "4.0" 299; escena NOCHE a DÍA 392 y NOCHE a NOCHF 77 (el menor); frase "?" por "!" 203 y
    "guardianes" a "guardiapes" 262; gancho "busca" a "buscs" 403 y "dragón" a "dragon" 397.
    K_BLOQUE = 30 queda entre 0 y 77. ValueError si la caja es
    vacía o sale del cuadro.
    """
    x0, y0, x1, y1 = (int(c) for c in caja)
    ancho, alto = esperado.size
    if not (0 <= x0 < x1 <= ancho and 0 <= y0 < y1 <= alto):
        raise ValueError(f"región vacía o fuera del cuadro {ancho}x{alto}: {caja}")
    if real.size != esperado.size:
        raise ValueError(f"tamaño {real.size} distinto de {esperado.size}")

    def gris(img):
        return np.asarray(img.convert("RGB").filter(ImageFilter.GaussianBlur(1)).convert("L"),
                          dtype=np.int16)[y0:y1, x0:x1]
    a, b = gris(real), gris(esperado)
    rgb = lambda img: np.asarray(img.convert("RGB"), dtype=np.int16)[y0:y1, x0:x1]
    p = rgb(papel)
    tinta = (np.abs(rgb(esperado) - p).max(axis=2) > DIF_TINTA) | (np.abs(rgb(real) - p).max(axis=2) > DIF_TINTA)
    disc = (np.abs(a - b) > PIXEL_DISCREPANTE) & _dilatar(tinta, DILATAR_TINTA_PX)
    h, w = disc.shape
    bl = min(BLOQUE_PX, h, w)
    ii = np.pad(disc.astype(np.int64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    sumas = ii[bl:, bl:] - ii[:-bl, bl:] - ii[bl:, :-bl] + ii[:-bl, :-bl]
    return int(sumas.max())


def es_v2(plan: dict) -> bool:
    """True si el plan es de la plantilla v2 (toma con video a pantalla completa)."""
    return plan.get("plantilla") == PLANTILLA_V2


def _placas_del_plan(plan: dict, marca: Marca) -> list[placas.Placa]:
    """Las placas que el plan debe llevar, dibujadas desde el texto del plan y marca.yml. ValueError si no se pueden."""
    en_plan = (plan.get("campana") or {}).get("marca_agua")
    logo = None
    if en_plan and (marca.raiz / en_plan["archivo"]).is_file():
        logo = marca.raiz / en_plan["archivo"]
    return placas.construir(marca, _toma(plan), plan.get("lineas", []), float(plan.get("duracion") or 0), logo)


def _comparar_placa(real: Image.Image, placa: placas.Placa, papel: Image.Image) -> dict[str, int]:
    """Discrepancia por región (la placa entera, o sus regiones con nombre) entre el fotograma y la placa esperada."""
    esperado = real.copy()
    esperado.paste(placa.imagen.convert("RGB"), (placa.x, placa.y))
    regiones = placa.regiones or {"placa": placa.caja()}
    return {n: discrepancia_local(real, esperado, papel, caja) for n, caja in regiones.items()}


def _placas_visibles(video: Path, plan: dict, marca: Marca, pedidas: list[tuple[placas.Placa, float]]) -> tuple[bool, str]:
    """Cada placa pedida, en su instante, está en el MP4 tal como el plan la dibuja (dentro de su caja)."""
    papel = lienzo.papel(marca)
    fallos, detalle = [], []
    for placa, t in pedidas:
        real = _fotograma(Path(video), t)
        if real is None or real.size != papel.size:
            fallos.append(f"{placa.tipo}: no se pudo leer el fotograma en {t:.2f} s")
            continue
        try:
            difs = _comparar_placa(real, placa, papel)
        except ValueError as e:
            fallos.append(f"{placa.tipo}: {e}")
            continue
        d = max(difs.values())
        detalle.append(f"{placa.tipo} {d}")
        if not d < K_BLOQUE:
            fallos.append(f"{placa.tipo} distinta en {t:.2f} s ({d} px discrepantes)")
    if fallos:
        return False, "; ".join(fallos) + f" (máximo {K_BLOQUE} px por bloque)"
    return True, f"diferencias {', '.join(detalle)} (máximo {K_BLOQUE} px por bloque)"


def _instante_medio(plan: dict, marca: Marca, frac: float = 0.5) -> float:
    dur = float(plan.get("duracion") or 0)
    return max(0.0, min(dur * frac, dur - float(marca.m("cierre_s")) - 0.1))


def _del_tipo(esperadas: list[placas.Placa], *tipos: str) -> list[placas.Placa]:
    return [p for p in esperadas if p.tipo in tipos or (p.tipo.startswith("dialogo") and "dialogo" in tipos)]


def _fuente_encuadrada(ruta: Path, t_fuente: float, recorte: dict, marca: Marca) -> Image.Image | None:
    """Fotograma de la fuente recortado con el recorte del plan y escalado a cuadro completo (RGBA)."""
    f = _fotograma(ruta, t_fuente)
    if f is None:
        return None
    x, y, w, h = (int(recorte[k]) for k in ("x", "y", "ancho", "alto"))
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > f.width or y + h > f.height:
        return None
    return f.crop((x, y, x + w, y + h)).resize((marca.ancho, marca.alto), Image.LANCZOS).convert("RGBA")


def _fuente_esperada(plan: dict, marca: Marca, t: float) -> Image.Image | None:
    """Fotograma de la fuente en el instante `t` de la toma, tal como el plan dice que se ve (sin placas)."""
    origen, geo = plan.get("origen") or {}, plan.get("geometria") or {}
    ruta, t_fuente = Path(origen["archivo"]), float(origen["inicio"]) + t
    if es_v2(plan):
        return _fuente_encuadrada(ruta, t_fuente, geo.get("recorte") or {}, marca)
    return _fuente_en_cuerpo(ruta, t_fuente, geo["cuerpo"])


def _mejor_contra_fuente(plan: dict, marca: Marca, t: float, medir, tope: float) -> float | None:
    """Menor valor de `medir(fuente)` con la fuente en `t` y en un paso de fotograma antes o después.

    Se detiene en cuanto baja de `tope`. None si no se pudo leer la fuente.
    """
    mejor = None
    for desfase in DESFASES_FUENTE_S:
        fuente = _fuente_esperada(plan, marca, max(0.0, t + desfase))
        if fuente is None:
            continue
        d = medir(fuente)
        mejor = d if mejor is None else min(mejor, d)
        if mejor < tope:
            break
    return mejor


def _mascara_placas(plan: dict, t: float, marca: Marca) -> np.ndarray:
    """Máscara (alto, ancho) True donde NO hay placa activa en el instante `t` (según las capas del plan)."""
    libre = np.ones((marca.alto, marca.ancho), dtype=bool)
    for c in (plan.get("geometria") or {}).get("capas", []):
        if float(c.get("t0", 0)) <= t < float(c.get("t1", 0)):
            x0, y0 = max(0, int(c["x"])), max(0, int(c["y"]))
            libre[y0:int(c["y"] + c["alto"]), x0:int(c["x"] + c["ancho"])] = False
    return libre


def pantalla_completa(plan: dict, marca: Marca) -> tuple[bool, str]:
    """[plan] El cuerpo es el cuadro entero y el recorte cubre el cuadro dentro de la fuente (sin franjas)."""
    if not es_v2(plan):
        return False, "la toma lleva video y el plan no es de la plantilla v2 de pantalla completa"
    geo, origen = plan.get("geometria") or {}, plan.get("origen") or {}
    esperado = {"x": 0, "y": 0, "ancho": marca.ancho, "alto": marca.alto}
    if geo.get("cuerpo") != esperado:
        return False, f"el cuerpo es {geo.get('cuerpo')} y debe ser {esperado}"
    r = geo.get("recorte") or {}
    try:
        x, y, w, h, esc = (float(r[k]) for k in ("x", "y", "ancho", "alto", "escala"))
    except (KeyError, TypeError, ValueError):
        return False, f"el plan no trae el recorte completo: {r}"
    ruta = Path(str(origen.get("archivo", "")))
    if not ruta.is_file():
        return False, f"el archivo de origen no está: {ruta}"
    from .componer import sondear_video
    info = sondear_video(ruta)
    cubrir = max(marca.ancho / info["ancho"], marca.alto / info["alto"])
    if esc < cubrir - 1e-3:
        return False, f"escala {esc:.3f} menor que la de cubrir {cubrir:.3f}: quedarían franjas"
    if abs(w * esc - marca.ancho) > 2 * esc or abs(h * esc - marca.alto) > 2 * esc:
        return False, f"el recorte {w:g}x{h:g} a escala {esc:.3f} no llena {marca.ancho}x{marca.alto}"
    if x < 0 or y < 0 or x + w > info["ancho"] or y + h > info["alto"]:
        return False, f"el recorte {r} se sale de la fuente {info['ancho']}x{info['alto']}"
    return True, f"cuerpo {marca.ancho}x{marca.alto}, recorte {int(w)}x{int(h)} en ({int(x)}, {int(y)}), modo {r.get('modo')}"


def capas_fuera_de_la_accion(plan: dict, marca: Marca) -> tuple[bool, str]:
    """[plan] Ninguna placa toca la zona de acción, todas van en la zona segura y suman menos del tope del cuadro."""
    capas = (plan.get("geometria") or {}).get("capas")
    if not isinstance(capas, list) or not capas:
        return False, "el plan no trae geometria.capas"
    malas = placas.problemas_capas(capas, marca)
    return not malas, "; ".join(malas) if malas else f"{len(capas)} placas dentro de la zona segura y fuera de la zona de acción"


def capas_coinciden(plan: dict, marca: Marca) -> tuple[bool, str]:
    """[plan] Las capas del plan son las placas que marca.yml dibuja para ese texto (misma caja y mismos tiempos)."""
    try:
        esperadas = [p.capa() for p in _placas_del_plan(plan, marca)]
    except ValueError as e:
        return False, f"el plan no se puede dibujar: {e}"
    capas = (plan.get("geometria") or {}).get("capas")
    if capas == esperadas:
        return True, f"{len(esperadas)} placas iguales a las de marca.yml"
    dif = [f"{a.get('tipo')}" for a, b in zip(capas or [], esperadas) if a != b]
    if len(capas or []) != len(esperadas):
        dif.append(f"el plan trae {len(capas or [])} placas y marca.yml dibuja {len(esperadas)}")
    return False, "capas distintas de las de marca.yml: " + ", ".join(dif)


def bordes_coinciden(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """[fotograma] Las 4 franjas del borde (fuera de las placas) coinciden con la fuente recortada y escalada.

    Un video con franjas negras o de papel falla; una escena oscura de verdad no, porque se compara contra la fuente.
    """
    origen, geo = plan.get("origen") or {}, plan.get("geometria") or {}
    if not origen or not geo.get("recorte"):
        return False, "el plan no trae origen y recorte"
    dur = float(plan.get("duracion") or 0)
    ruta = Path(str(origen.get("archivo", "")))
    if not ruta.is_file():
        return False, f"el archivo de origen no está: {ruta}"
    w, h = marca.ancho, marca.alto
    franjas = {"arriba": (slice(0, BORDE_PX), slice(0, w)), "abajo": (slice(h - BORDE_PX, h), slice(0, w)),
               "izquierda": (slice(0, h), slice(0, BORDE_PX)), "derecha": (slice(0, h), slice(w - BORDE_PX, w))}
    peor, donde = 0.0, ""
    for frac in (0.3, 0.7):
        t = _instante_medio(plan, marca, frac)
        real = _fotograma(Path(video), t)
        if real is None:
            return False, f"no se pudo leer el fotograma en {t:.2f} s"
        libre = _mascara_placas(plan, t, marca)
        a = _gris_desenfocado(real)

        def medir(fuente: Image.Image) -> float:
            if fuente.size != real.size:
                return float("inf")
            b = _gris_desenfocado(fuente)
            peores = [(float(np.abs(a[fy, fx] - b[fy, fx])[libre[fy, fx]].mean()), n)
                      for n, (fy, fx) in franjas.items() if libre[fy, fx].any()]
            medir.donde = max(peores)[1] if peores else ""
            return max((d for d, _ in peores), default=0.0)
        d = _mejor_contra_fuente(plan, marca, t, medir, TOL_BORDE)
        if d is None:
            return False, f"no se pudo leer el fotograma de la fuente en {t:.2f} s"
        if d > peor:
            peor, donde = d, f"{medir.donde} a {t:.1f} s"
    return peor < TOL_BORDE, f"franja más distinta {peor:.1f} ({donde or 'ninguna'}; máximo {TOL_BORDE:g})"


def _cierre_v2(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    dur = float(plan.get("duracion") or 0)
    if dur <= 0:
        return False, "el plan no trae la duración"
    dur_real = sondear(Path(video))["duracion"]
    if abs(dur_real - dur) > TOL_DURACION_S:
        return False, f"el MP4 dura {dur_real:.2f} s y el plan {dur:.2f} s (tolerancia {TOL_DURACION_S:.2f})"
    try:
        cierre = _del_tipo(_placas_del_plan(plan, marca), "cierre")[0]
    except ValueError as e:
        return False, f"el plan no se puede dibujar: {e}"
    cuadros = {"en duración − 0.2 s": _fotograma(Path(video), max(0.0, dur_real - 0.2)),
               "último fotograma": _ultimo_fotograma(Path(video))}
    papel = lienzo.papel(marca)
    fallan: list[str] = []
    det: list[str] = []
    for cuando, real in cuadros.items():
        if real is None:
            return False, f"no se pudo leer el fotograma ({cuando})"
        if real.size != papel.size:
            return False, f"tamaño {real.size} distinto de {papel.size} ({cuando})"
        try:
            difs = _comparar_placa(real, cierre, papel)
        except ValueError as e:
            return False, str(e)
        det.append(f"{cuando}: " + ", ".join(f"{n} {d}" for n, d in difs.items()))
        fallan += [f"{n} ({cuando})" for n, d in difs.items() if not d < K_BLOQUE]
    texto = "; ".join(det) + f" (máximo {K_BLOQUE} px por bloque)"
    if fallan:
        texto += "; falla: " + " y ".join(fallan)
    return not fallan, texto


def mosca_visible(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """[fotograma] La mosca GUION VIVO está en su esquina, dentro de su placa, a mitad de la toma."""
    try:
        esperadas = _del_tipo(_placas_del_plan(plan, marca), "mosca")
    except ValueError as e:
        return False, f"el plan no se puede dibujar: {e}"
    return _placas_visibles(Path(video), plan, marca, [(p, _instante_medio(plan, marca)) for p in esperadas])


def cierre_visible(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """Demuestra en el video, no en el plan, que el cierre trae CORTE. y el crédito.

    Saca el fotograma de duración − 0.2 s y el último fotograma decodificable (un MP4 con los
    últimos cuadros en negro y la misma duración no debe pasar) y compara en cada uno, por separado,
    la región de CORTE. y la del crédito con la página de cierre del plan (sólo sobre la tinta esperada).
    En la plantilla v2 el cierre es una placa sobre el video y se compara dentro de su caja.
    """
    if es_v2(plan):
        return _cierre_v2(video, plan, marca)
    dur = float(plan.get("duracion") or 0)
    if dur <= 0:
        return False, "el plan no trae la duración"
    dur_real = sondear(Path(video))["duracion"]
    if abs(dur_real - dur) > TOL_DURACION_S:
        return False, f"el MP4 dura {dur_real:.2f} s y el plan {dur:.2f} s (tolerancia {TOL_DURACION_S:.2f})"
    esperado = mod_cierre.pagina_cierre(marca, _toma(plan))
    cuadros = {"en duración − 0.2 s": _fotograma(Path(video), max(0.0, dur_real - 0.2)),
               "último fotograma": _ultimo_fotograma(Path(video))}
    papel = lienzo.papel(marca)
    x0, x1 = int(marca.m("margen")), int(marca.ancho - marca.m("margen"))
    corte_y, credito_y = int(marca.m("corte_y")), int(marca.m("credito_y"))
    regiones = {"CORTE.": (x0, corte_y, x1, credito_y),
                "crédito": (x0, credito_y, x1, int(credito_y + 2 * marca.m("tam_credito")))}
    fallan: list[str] = []
    det: list[str] = []
    for cuando, real in cuadros.items():
        if real is None:
            return False, f"no se pudo leer el fotograma ({cuando})"
        if real.size != esperado.size:
            return False, f"tamaño {real.size} distinto de {esperado.size} ({cuando})"
        try:
            difs = {n: discrepancia_local(real, esperado, papel, caja) for n, caja in regiones.items()}
        except ValueError as e:
            return False, str(e)
        det.append(f"{cuando}: " + ", ".join(f"{n} {d}" for n, d in difs.items()))
        fallan += [f"{n} ({cuando})" for n, d in difs.items() if not d < K_BLOQUE]
    texto = "; ".join(det) + f" (máximo {K_BLOQUE} px por bloque)"
    if fallan:
        texto += "; falla: " + " y ".join(fallan)
    return not fallan, texto


def cabecera_visible(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """El fotograma en t = 0.5 s trae la cabecera (gancho, lápiz, fila, escena) del plan.

    En la plantilla v2 son dos placas (fila con TOMA y crédito, y el gancho con escena) y se comparan dentro de su caja.
    """
    dur = float(plan.get("duracion") or 0)
    if es_v2(plan):
        try:
            esperadas = _del_tipo(_placas_del_plan(plan, marca), "fila", "cabecera")
        except ValueError as e:
            return False, f"el plan no se puede dibujar: {e}"
        t = min(T_CABECERA_S, dur / 2) if dur > 0 else T_CABECERA_S
        return _placas_visibles(Path(video), plan, marca, [(p, t) for p in esperadas])
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
        d = discrepancia_local(real, esperado, lienzo.papel(marca), (0, 0, marca.ancho, int(y_cont)))
    except ValueError as e:
        return False, str(e)
    return d < K_BLOQUE, f"{d} px discrepantes en un bloque (máximo {K_BLOQUE})"


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
    if es_v2(plan):
        try:
            esperadas = _del_tipo(_placas_del_plan(plan, marca), "dialogo")
        except ValueError as e:
            return False, str(e)
        dur = float(plan.get("duracion") or 0)
        tope = dur - float(marca.m("cierre_s")) - 0.1
        return _placas_visibles(Path(video), plan, marca,
                                [(p, min((p.t0 + p.t1) / 2, tope) if dur > 0 else (p.t0 + p.t1) / 2)
                                 for p in esperadas])
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
            d = discrepancia_local(real, esperado, papel, (0, y0, marca.ancho, y1))
        except ValueError as e:
            fallos.append(f"línea {i + 1}: {e}")
            continue
        detalle.append(str(d))
        if not d < K_BLOQUE:
            fallos.append(f"línea {i + 1} distinta en {t:.2f} s ({d} px discrepantes)")
    if fallos:
        return False, "; ".join(fallos) + f" (máximo {K_BLOQUE} px por bloque)"
    return True, f"diferencias {', '.join(detalle)} (máximo {K_BLOQUE} px por bloque)"


def _miniatura_gris(img: Image.Image, ancho: int, alto: int) -> np.ndarray:
    return np.asarray(img.convert("L").resize((ancho, alto), Image.LANCZOS), dtype=np.float32)


def _gris_desenfocado(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB").filter(ImageFilter.GaussianBlur(1)).convert("L"), dtype=np.float32)


def _fuente_en_cuerpo(ruta: Path, t_fuente: float, cuerpo: dict) -> Image.Image | None:
    """Fotograma de la fuente escalado a la caja del cuerpo (RGBA), como lo deja el compositor."""
    f = _fotograma(ruta, t_fuente)
    if f is None:
        return None
    return f.resize((int(cuerpo["ancho"]), int(cuerpo["alto"])), Image.LANCZOS).convert("RGBA")


def _diff_region(real: Image.Image, esperado_cuerpo: Image.Image, cuerpo: dict, c: dict) -> float:
    """Diferencia media en gris entre el fotograma real y el cuerpo esperado, dentro de la caja `c` (del cuadro)."""
    x0, y0 = c["x"] - cuerpo["x"], c["y"] - cuerpo["y"]
    a = _gris_desenfocado(real.crop((c["x"], c["y"], c["x"] + c["ancho"], c["y"] + c["alto"])))
    b = _gris_desenfocado(esperado_cuerpo.crop((x0, y0, x0 + c["ancho"], y0 + c["alto"])))
    return float(np.abs(a - b).mean())


def marca_agua_visible(video: Path, plan: dict, marca: Marca, ficha: dict | None) -> tuple[bool, str]:
    """La marca de agua de la campaña, demostrada en el fotograma y sólo si la ficha la declara.

    Rojo si el plan trae una capa que la ficha no declara, si la ficha la declara y el plan no, si el archivo del
    logo no es el declarado, o si en los fotogramas de 30 % y 70 % la esquina no muestra el logo (se compara con la
    fuente más el logo esperado y con la fuente limpia).
    """
    declarada = (ficha or {}).get("marca_agua")
    en_plan = (plan.get("campana") or {}).get("marca_agua")
    if not declarada and not en_plan:
        return True, "no aplica (la ficha no declara marca de agua)"
    if en_plan and not declarada:
        return False, "el plan trae una capa de marca de agua que la ficha no declara"
    if not en_plan:
        return False, f"la ficha declara {declarada} y el plan no la trae"
    if en_plan.get("archivo") != declarada:
        return False, f"el plan usa {en_plan.get('archivo')} y la ficha declara {declarada}"
    ruta_logo = marca.raiz / declarada
    if not ruta_logo.is_file():
        return False, f"el logo declarado no está: {declarada}"
    from .componer import sha256_archivo
    if sha256_archivo(ruta_logo) != en_plan.get("sha256"):
        return False, "el logo del repo no coincide con el sha256 del plan"
    origen, geo = plan.get("origen") or {}, plan.get("geometria") or {}
    cuerpo = geo.get("cuerpo")
    if not origen or not cuerpo:
        return False, "el plan declara marca de agua pero no trae el cuerpo de video"
    placa = mod_agua.logo(ruta_logo, marca)
    try:
        esperada = mod_agua.caja_pantalla(marca, placa.size) if es_v2(plan) else mod_agua.caja(marca, cuerpo, placa.size)
    except ValueError as e:
        return False, str(e)
    if esperada != en_plan.get("caja"):
        return False, f"la caja del plan {en_plan.get('caja')} no es la de marca.yml {esperada}"
    dur = float(plan.get("duracion") or 0)
    tope = dur - float(marca.m("cierre_s")) - 0.2
    detalle = []
    for frac in (0.3, 0.7):
        t = min(dur * frac, tope)
        real = _fotograma(Path(video), t)
        if real is None:
            return False, f"no se pudo leer el fotograma en {t:.2f} s"
        ok_t, txt = False, ""
        for desfase in DESFASES_FUENTE_S:        # el fotograma de la fuente puede ir un paso antes o después
            fuente = _fuente_esperada(plan, marca, max(0.0, t + desfase))
            if fuente is None:
                return False, f"no se pudo leer el fotograma de la fuente en {t:.2f} s"
            limpia = fuente.copy()
            fuente.alpha_composite(placa, (esperada["x"] - cuerpo["x"], esperada["y"] - cuerpo["y"]))
            d_con = _diff_region(real, fuente, cuerpo, esperada)
            d_sin = _diff_region(real, limpia, cuerpo, esperada)
            txt = f"{t:.1f} s: con logo {d_con:.1f}, sin logo {d_sin:.1f}"
            if d_con < TOL_AGUA and d_sin >= d_con + SALTO_AGUA:
                ok_t = True
                break
        detalle.append(txt)
        if not ok_t:
            return False, "el logo no aparece en el fotograma (" + "; ".join(detalle) + ")"
    return True, "; ".join(detalle) + f" (tolerancia {TOL_AGUA:g})"


def logo_extra_en_esquina(video: Path, plan: dict, marca: Marca) -> tuple[bool, str]:
    """Sin capa declarada, la esquina de marca.yml donde iría el logo debe seguir siendo la de la fuente.

    En la plantilla v2 la mosca declarada (arriba) no cuenta como logo extra: el logo va debajo de ella.
    """
    origen, geo = plan.get("origen") or {}, plan.get("geometria") or {}
    cuerpo = geo.get("cuerpo")
    if not origen or not cuerpo:
        return True, "no aplica"
    if not cuerpo_coincide(video, plan, marca)[0]:      # si el cuerpo ya está en rojo, ese punto lo dice; no se duplica
        return True, "no aplica (el cuerpo ya sale en rojo en su propio punto)"
    cfg = marca.marca_agua
    w = int(cfg["ancho"]) + 2 * int(cfg["placa"])
    h = int(round(w * 0.4))
    try:
        c = mod_agua.caja_pantalla(marca, (w, h)) if es_v2(plan) else mod_agua.caja(marca, cuerpo, (w, h))
    except ValueError:
        return True, "no aplica (el cuerpo es más chico que el logo)"
    dur = float(plan.get("duracion") or 0)
    t = min(dur * 0.5, dur - float(marca.m("cierre_s")) - 0.2)
    real = _fotograma(Path(video), t)
    if real is None:
        return False, f"no se pudo leer el fotograma en {t:.2f} s"
    d = _mejor_contra_fuente(plan, marca, t, lambda fuente: _diff_region(real, fuente, cuerpo, c), UMBRAL_LOGO_EXTRA)
    if d is None:
        return False, f"no se pudo leer el fotograma de la fuente en {t:.2f} s"
    return d < UMBRAL_LOGO_EXTRA, f"esquina del logo {d:.1f} (máximo {UMBRAL_LOGO_EXTRA:g})"


UMBRAL_SILENCIO_DB = -45.0      # sobre el pico: un tramo corto de voz o música no se esconde en el promedio


def picos_audio(video: Path) -> tuple[float | None, str | None]:
    """(pico en dB con volumedetect, motivo del error). El silencio digital marca -91 dB o menos."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    if r.returncode != 0:
        return None, f"ffmpeg falló (código {r.returncode}): {(r.stderr or '').strip().splitlines()[-1:] or ['sin detalle']}"
    m = re.search(r"max_volume:\s*(-?[0-9.]+|-inf)\s*dB", r.stderr)
    if not m:
        return None, "volumedetect no dio el pico del audio"
    return (float("-inf") if m.group(1) == "-inf" else float(m.group(1))), None


def _pistas_de_audio(video: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "json", str(video)], capture_output=True, text=True)
    try:
        return len(json.loads(out.stdout).get("streams", []))
    except json.JSONDecodeError:
        return -1


def audio_original(video: Path, plan: dict) -> tuple[bool, str]:
    """El audio del MP4 es el de la fuente (correlación alta), sin audio añadido; sin audio de origen, silencio.

    En la plantilla v2 se comprueba siempre. El plan declara `audio` ("original" o "silencio") y la fuente lo confirma.
    """
    camp = plan.get("campana") or {}
    origen = plan.get("origen") or {}
    declarado = plan.get("audio") or camp.get("audio")
    if es_v2(plan):
        ruta = Path(str(origen.get("archivo", "")))
        if not ruta.is_file():
            return False, f"el archivo de origen no está: {ruta}"
        tiene = _pistas_de_audio(ruta) > 0
        if tiene and declarado != "original":
            return False, f"la fuente trae audio y el plan declara {declarado!r}: debe ser el original"
        if not tiene and declarado != "silencio":
            return False, f"la fuente no trae audio y el plan declara {declarado!r}: debe ser silencio"
    if declarado == "silencio":
        pistas = _pistas_de_audio(Path(video))
        if pistas != 1:
            return False, f"el MP4 trae {pistas} pistas de audio y debe traer exactamente una (silencio)"
        pico, error = picos_audio(Path(video))
        if pico is None:
            return False, f"no se pudo medir el audio: {error}"
        return pico <= UMBRAL_SILENCIO_DB, (f"la fuente no trae audio: el MP4 debe ir en silencio; pico "
                                            f"{pico:.1f} dB (máximo {UMBRAL_SILENCIO_DB:g} dB)")
    if declarado != "original" or not origen:
        return False, f"el plan declara audio {declarado!r}, no el original de la fuente"
    dur = float(plan.get("duracion") or 0)
    inicio = float(origen.get("inicio", 0))

    def pcm(ruta: Path, ss: float, t: float) -> np.ndarray:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ss:.3f}", "-t", f"{t:.3f}", "-i", str(ruta),
                            "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True)
        return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
    largo = max(1.0, dur - float(0.5))
    a, b = pcm(Path(video), 0.0, largo), pcm(Path(origen["archivo"]), inicio, largo)
    n = min(len(a), len(b))
    if n < 800:
        return False, "no se pudo leer el audio"
    a, b = a[:n], b[:n]
    if not b.any():
        return not a.any(), "la fuente es muda y el MP4 también" if not a.any() else "el MP4 trae audio y la fuente es muda"
    # el códec AAC corre la señal unas decenas de milisegundos: se busca el mejor desfase de ±100 ms
    mejor = 0.0
    for lag in range(-800, 801, 40):
        x, y = (a[lag:], b[:n - lag]) if lag >= 0 else (a[:n + lag], b[-lag:])
        den = float(np.linalg.norm(x) * np.linalg.norm(y))
        if den > 0:
            mejor = max(mejor, float(np.dot(x, y)) / den)
    return mejor > 0.9, f"correlación con el audio de la fuente {mejor:.2f} (mínimo 0.90)"


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
    dur_o, ini_o, dur_p = origen["duracion"], origen["inicio"], plan.get("duracion")
    for nombre, v in (("duracion", dur_o), ("inicio", ini_o)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return False, f"origen.{nombre} debe ser un número finito (trae {v!r})"
    if dur_o <= 0 or ini_o < 0:
        return False, f"origen.duracion debe ser positiva e inicio no negativo (trae {dur_o!r} y {ini_o!r})"
    if isinstance(dur_p, bool) or not isinstance(dur_p, (int, float)) or abs(dur_o - dur_p) > 0.01:
        return False, f"origen.duracion {dur_o!r} no coincide con la duración que declara el compositor ({dur_p!r})"
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
    if es_v2(plan):
        return _cuerpo_v2(video, plan, marca, tope)
    caja = (cuerpo["x"], cuerpo["y"], cuerpo["x"] + cuerpo["ancho"], cuerpo["y"] + cuerpo["alto"])
    alto_min = max(1, int(round(MINIATURA_ANCHO * cuerpo["alto"] / cuerpo["ancho"])))
    difs = []
    agua = None
    en_plan = (plan.get("campana") or {}).get("marca_agua")
    if en_plan and (marca.raiz / en_plan["archivo"]).is_file():
        placa = mod_agua.logo(marca.raiz / en_plan["archivo"], marca)
        agua = (placa, en_plan["caja"])
    for frac in (0.3, 0.7):
        t = min(dur * frac, tope)
        real = _fotograma(Path(video), t)
        fuente = _fotograma(ruta, inicio + t)
        if real is None or fuente is None:
            return False, f"no se pudo leer el fotograma en {t:.2f} s"
        a = _miniatura_gris(real.crop(caja), MINIATURA_ANCHO, alto_min)
        if agua is not None:                 # la fuente esperada lleva el logo en su lugar fijo
            fuente = fuente.resize((cuerpo["ancho"], cuerpo["alto"]), Image.LANCZOS).convert("RGBA")
            fuente.alpha_composite(agua[0], (agua[1]["x"] - cuerpo["x"], agua[1]["y"] - cuerpo["y"]))
        b = _miniatura_gris(fuente, MINIATURA_ANCHO, alto_min)
        difs.append(float(np.abs(a - b).mean()))
    return max(difs) < UMBRAL_CUERPO, \
        f"diferencias {', '.join(f'{d:.1f}' for d in difs)} (tolerancia {UMBRAL_CUERPO:.1f})"


def _cuerpo_v2(video: Path, plan: dict, marca: Marca, tope: float) -> tuple[bool, str]:
    """Cuadro completo del MP4 contra la fuente recortada y escalada, fuera de las placas activas en ese instante."""
    dur = float(plan.get("duracion") or 0)
    alto_min = max(1, int(round(MINIATURA_ANCHO * marca.alto / marca.ancho)))
    difs = []
    for frac in (0.3, 0.7):
        t = min(dur * frac, tope)
        real = _fotograma(Path(video), t)
        if real is None:
            return False, f"no se pudo leer el fotograma en {t:.2f} s"
        a = _miniatura_gris(real, MINIATURA_ANCHO, alto_min)
        tapado = Image.fromarray(np.where(_mascara_placas(plan, t, marca), 0, 255).astype(np.uint8))
        tapado = np.asarray(tapado.resize((MINIATURA_ANCHO, alto_min), Image.BOX)) > 0      # toca alguna placa
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):                                     # el reescalado se desborda
            tapado = tapado | np.roll(tapado, (dy, dx), axis=(0, 1))
        libre = ~tapado
        if not libre.any():
            return False, "las placas tapan todo el cuadro"

        def medir(fuente: Image.Image) -> float:
            if fuente.size != real.size:
                return float("inf")
            return float(np.abs(a - _miniatura_gris(fuente, MINIATURA_ANCHO, alto_min))[libre].mean())
        d = _mejor_contra_fuente(plan, marca, t, medir, UMBRAL_CUERPO)
        if d is None:
            return False, f"no se pudo leer el fotograma de la fuente en {t:.2f} s"
        difs.append(d)
    return max(difs) < UMBRAL_CUERPO, \
        f"diferencias {', '.join(f'{d:.1f}' for d in difs)} (tolerancia {UMBRAL_CUERPO:.1f})"


def textos_caben(plan: dict, marca: Marca) -> tuple[bool, str]:
    """Ningún texto se sale de sus márgenes (ni una palabra sola más ancha que la línea)."""
    t = plan.get("toma", {})
    malos: list[str] = []
    util = marca.ancho - 2 * marca.m("margen")
    ancho_col = marca.columna[1] - marca.columna[0]
    pc = marca.pc if es_v2(plan) else None

    def partible(nombre: str, texto: str, f, ancho: float) -> None:
        try:
            partir(texto, f, ancho)
        except ValueError as e:
            malos.append(f"{nombre}: {e}")

    def una_linea(nombre: str, texto: str, f, ancho: float) -> None:
        if texto and f.getlength(texto) > ancho:
            malos.append(f"{nombre} mide {f.getlength(texto):.0f} px y caben {ancho:.0f}")

    if pc is not None:
        rel = 2 * pc["relleno_placa"]
        partible("gancho", str(t.get("gancho", "")).upper(), marca.fuente("bold", pc["cabecera"]["tam_gancho"]),
                 pc["ancho_max_encabezado"] - rel - 2 * pc["lapiz"]["reserva"])
        una_linea("escena", str(t.get("escena", "")).upper(), marca.fuente("regular", pc["cabecera"]["tam_escena"]),
                  pc["ancho_max_encabezado"] - rel)
        fila = marca.fuente("bold", pc["fila"]["tam_toma"]).getlength("TOMA 000") + pc["fila"]["separacion"]
        una_linea("crédito", str(t.get("credito", "")), marca.fuente("regular", pc["fila"]["tam_credito"]),
                  pc["ancho_max_encabezado"] - rel - fila)
        seg = pc["zona_segura"]
        una_linea("crédito del cierre", str(t.get("credito", "")), marca.fuente("regular", pc["cierre"]["tam_credito"]),
                  seg["x1"] - seg["x0"] - 2 * pc["cierre"]["relleno_x"])
        d = pc["dialogo"]
        ancho_col = d["columna_dialogo"][1] - d["columna_dialogo"][0]
        for i, ln in enumerate(plan.get("lineas", []), 1):
            if ln.get("frase"):
                partible(f"frase {i}", str(ln["frase"]), marca.fuente("regular", d["tam_frase"]), ancho_col)
            una_linea(f"nombre {i}", str(ln.get("nombre") or "").upper(), marca.fuente("regular", d["tam_nombre"]),
                      ancho_col)
        return not malos, "; ".join(malos)
    partible("gancho", str(t.get("gancho", "")).upper(), marca.fuente("bold", marca.m("tam_gancho")), util)
    una_linea("escena", str(t.get("escena", "")).upper(), marca.fuente("regular", marca.m("tam_escena")), util)
    una_linea("crédito", str(t.get("credito", "")), marca.fuente("regular", marca.m("tam_credito")), util)
    for i, ln in enumerate(plan.get("lineas", []), 1):
        if ln.get("frase"):
            partible(f"frase {i}", str(ln["frase"]), marca.fuente("regular", marca.m("tam_frase")), ancho_col)
        una_linea(f"nombre {i}", str(ln.get("nombre") or "").upper(),
                  marca.fuente("regular", marca.m("tam_nombre")), ancho_col)
    return not malos, "; ".join(malos)


def rango_duracion(plan: dict, ficha: dict | None) -> tuple[float, float]:
    """Duración permitida: `reglas.duracion_s` de la ficha (o la que declara el plan), dentro del formato."""
    d = ((ficha or {}).get("reglas") or {}).get("duracion_s") if ficha is not None \
        else (plan.get("campana") or {}).get("duracion_s")
    if isinstance(d, (list, tuple)) and len(d) == 2 and all(isinstance(x, (int, float)) for x in d):
        return max(DURACION_FORMATO_S[0], float(d[0])), min(DURACION_FORMATO_S[1], float(d[1]))
    return DURACION_FORMATO_S


def revisar(video: Path, plan: dict, marca: Marca, ficha: dict | None = None) -> list[tuple[str, bool, str]]:
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
    d_min, d_max = rango_duracion(plan, ficha)
    punto("fotograma", f"Duración de {d_min:g} a {d_max:g} s", d_min <= info["duracion"] <= d_max,
          f"{info['duracion']:.2f} s")
    punto("fotograma", "faststart (moov antes de mdat)", faststart(Path(video)))
    dur_plan = float(plan.get("duracion") or 0)
    punto("fotograma", "Duración igual a la del plan", abs(info["duracion"] - dur_plan) <= TOL_DURACION_S,
          f"MP4 {info['duracion']:.2f} s, plan {dur_plan:.2f} s (tolerancia {TOL_DURACION_S:.2f})")

    t = plan.get("toma", {})
    gancho = str(t.get("gancho", ""))
    palabras = gancho.split()
    punto("plan", f"Gancho de {marca.max_palabras_gancho} palabras o menos",
          0 < len(palabras) <= marca.max_palabras_gancho, f"{len(palabras)} palabras")
    v2 = es_v2(plan)
    if v2:
        f_g = marca.fuente("bold", marca.pc["cabecera"]["tam_gancho"])
        ancho_g = (marca.pc["ancho_max_encabezado"] - 2 * marca.pc["relleno_placa"]
                   - 2 * marca.pc["lapiz"]["reserva"])
    else:
        f_g = marca.fuente("bold", marca.m("tam_gancho"))
        ancho_g = marca.ancho - 2 * marca.m("margen")
    try:
        lineas_g = partir(gancho.upper(), f_g, ancho_g)
        punto("plan", "Gancho en 2 líneas como máximo", 0 < len(lineas_g) <= 2, f"{len(lineas_g)} líneas")
    except ValueError as e:
        punto("plan", "Gancho en 2 líneas como máximo", False, str(e))
    objetivo = _limpia(str(t.get("palabra_lapiz", "")))
    punto("plan", "Palabra del lápiz está en el gancho",
          bool(objetivo) and objetivo in [_limpia(p) for p in palabras], str(t.get("palabra_lapiz", "")))

    malas = []
    for ln in plan.get("lineas", []):
        try:
            mod_dialogo.validar_bloque(marca, ln.get("nombre"), ln.get("frase"), ln.get("acotacion"),
                                       marca.pc["dialogo"] if v2 else None)
        except ValueError as e:
            malas.append(str(e))
    punto("plan", f"Frases de {marca.max_lineas_frase} líneas o menos en la columna", not malas, "; ".join(malas))
    ok_caben, det_caben = textos_caben(plan, marca)
    punto("plan", "Textos caben en sus márgenes", ok_caben, det_caben)

    punto("plan", "Crédito presente y no vacío", bool(str(t.get("credito", "")).strip()), str(t.get("credito", "")))
    con_video = bool(plan.get("origen")) or v2
    if con_video:
        ok_pc, det_pc = pantalla_completa(plan, marca)
        punto("plan", "Pantalla completa (cuerpo 1080x1920, sin franjas)", ok_pc, det_pc)
    if v2:
        ok_ca, det_ca = capas_fuera_de_la_accion(plan, marca)
        punto("plan", "Capas fuera de la zona de acción", ok_ca, det_ca)
        ok_cc, det_cc = capas_coinciden(plan, marca)
        punto("plan", "Capas del plan coinciden con las placas de marca.yml", ok_cc, det_cc)
        ok_bo, det_bo = bordes_coinciden(Path(video), plan, marca)
        punto("fotograma", "Bordes coinciden con la fuente (sin franjas)", ok_bo, det_bo)
    ok_cierre, det_cierre = cierre_visible(Path(video), plan, marca)
    punto("fotograma", "Cierre visible en el video (CORTE. y crédito)", ok_cierre, det_cierre)
    ok_cab, det_cab = cabecera_visible(Path(video), plan, marca)
    punto("fotograma", "Cabecera en el video", ok_cab, det_cab)
    ok_dia, det_dia = dialogo_visible(Path(video), plan, marca)
    punto("fotograma", "Diálogo en el video", ok_dia, det_dia)
    if v2:
        ok_mo, det_mo = mosca_visible(Path(video), plan, marca)
        punto("fotograma", "Mosca en el video", ok_mo, det_mo)
    ok_cue, det_cue = cuerpo_coincide(Path(video), plan, marca)
    punto("fotograma", "Cuerpo coincide con el origen", ok_cue, det_cue)

    if (ficha or {}).get("marca_agua") or (plan.get("campana") or {}).get("marca_agua"):
        ok_ma, det_ma = marca_agua_visible(Path(video), plan, marca, ficha)
        punto("fotograma", "Marca de agua de la campaña", ok_ma, det_ma)
    elif plan.get("origen"):
        ok_ex, det_ex = logo_extra_en_esquina(Path(video), plan, marca)
        punto("fotograma", "Sin logos extra no declarados", ok_ex, det_ex)
    if v2:
        ok_au, det_au = audio_original(Path(video), plan)
        punto("fotograma", "Audio original del clip", ok_au, det_au)
    elif ((ficha or {}).get("reglas") or {}).get("sin_musica") or (plan.get("campana") or {}).get("sin_musica"):
        ok_au, det_au = audio_original(Path(video), plan)
        punto("fotograma", "Audio sólo el original (sin música)", ok_au, det_au)

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
    ap.add_argument("--ficha", default=None, help="id de la ficha de fuentes.yml (marca de agua y duración)")
    args = ap.parse_args(argv)
    marca = Marca.cargar(args.raiz)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    ficha = None
    if args.ficha:
        from ..fuentes import cargar_fuentes
        ficha = next((f for f in cargar_fuentes(args.raiz) if f.get("id") == args.ficha), None)
        if ficha is None:
            print(f"ficha {args.ficha!r} no existe en fuentes.yml")
            return 2
    puntos = revisar(Path(args.video), plan, marca, ficha)
    for nombre, ok, det in puntos:
        print(f"{'SÍ' if ok else 'NO'}  {nombre}" + (f"  [{det}]" if det else ""))
    todo = all(ok for _, ok, _ in puntos)
    print("Puerta de estilo: " + ("EN VERDE" if todo else "EN ROJO"))
    return 0 if todo else 1


if __name__ == "__main__":
    sys.exit(main())

"""Cada toma otra vez desde su fuente, en horizontal (1920x1080), con la marca de Guion Vivo.

Reusa la orden de ffmpeg de la plantilla v2 (`componer.orden_ffmpeg_pantalla_completa`): recorte 16:9 entero de la
fuente (sin zoom), silencios cortados como en la toma, subtítulos con contorno, fila TOMA nnn + crédito, mosca y el
cierre de marca (destello papel + claqueta) que hace de corte entre clips.
"""
from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import ImageDraw

from .. import plazo
from ..editar import componer, lienzo, placas
from ..editar.componer import Linea
from ..editar.lienzo import Toma
from ..editar.marca import Marca


def _mezclar(base: dict, extra: dict) -> dict:
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mezclar(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def marca_horizontal(marca: Marca, cfg: dict) -> Marca:
    """Copia de la marca a la resolución del largo, con las medidas de `config/largo.yml` → `marca`."""
    datos = copy.deepcopy(marca.datos)
    datos["resolucion"] = list(cfg.get("resolucion") or [1920, 1080])
    _mezclar(datos, cfg.get("marca") or {})
    return Marca(datos, marca.raiz)


def recorte_16x9(ancho: int, alto: int) -> dict:
    """La ventana 16:9 más grande y centrada de la fuente (coordenadas pares)."""
    if ancho * 9 >= alto * 16:
        h = alto // 2 * 2
        w = min(ancho, int(round(h * 16 / 9))) // 2 * 2
    else:
        w = ancho // 2 * 2
        h = min(alto, int(round(w * 9 / 16))) // 2 * 2
    return {"x": (ancho - w) // 2 // 2 * 2, "y": (alto - h) // 2 // 2 * 2, "ancho": w, "alto": h}


@dataclass
class Tramo:
    inicio: float                       # segundo de la fuente donde empieza la lectura
    duracion: float                     # duración de la salida (suma de los tramos)
    tramos: list | None                 # [(a, b)] relativos a `inicio`, o None
    desplazamiento: float               # segundos que se añadieron delante (las líneas se corren eso)
    notas: list[str] = field(default_factory=list)


def tramo_con_contexto(receta: dict, dur_fuente: float, contexto: float) -> Tramo:
    """El tramo de la toma con `contexto` segundos más a cada lado si la fuente los tiene."""
    tr = receta.get("tramo") or {}
    ini, dur = float(tr.get("inicio") or 0.0), float(tr.get("duracion") or 0.0)
    if dur <= 0:
        dur = min(dur_fuente, float(receta.get("duracion_s") or dur_fuente))
    antes = max(0.0, min(contexto, ini))
    despues = max(0.0, min(contexto, dur_fuente - (ini + dur)))
    tramos = tr.get("tramos")
    if tramos:
        t = [[float(a) + antes, float(b) + antes] for a, b in tramos]
        t[0][0] = 0.0
        t[-1][1] = t[-1][1] + despues
        total = sum(b - a for a, b in t)
        return Tramo(ini - antes, round(total, 3), [(round(a, 3), round(b, 3)) for a, b in t], antes)
    return Tramo(round(ini - antes, 3), round(dur + antes + despues, 3), None, antes)


def lineas_de(receta: dict, desplazamiento: float, duracion: float) -> list[Linea]:
    """Las líneas de la receta, corridas por el contexto añadido; nunca pasan del final menos el cierre."""
    if receta.get("historia") or receta.get("subtitulos_del_clip"):
        return []
    salida = []
    for ln in receta.get("lineas") or []:
        try:
            t0, t1 = float(ln["t0"]) + desplazamiento, float(ln["t1"]) + desplazamiento
        except (KeyError, TypeError, ValueError):
            continue
        t1 = min(t1, duracion - 0.3)
        if t1 - t0 < 0.3 or not (ln.get("frase") or ln.get("acotacion")):
            continue
        salida.append(Linea(t0=round(t0, 3), t1=round(t1, 3), nombre=ln.get("nombre"), frase=ln.get("frase"),
                            acotacion=ln.get("acotacion"), voz=ln.get("voz")))
    return salida


def credito_de(receta: dict, creador: str) -> str:
    return f"Con: {creador or (receta.get('ficha') or {}).get('credito') or ''}".strip()


def placa_puesto(marca_h: Marca, puesto: int, segundos: float = 2.2) -> placas.Placa:
    """El puesto de la cuenta atrás («#7»), en papel, arriba al centro, los primeros segundos del clip."""
    f = marca_h.fuente("bold", 72)
    txt = f"#{puesto}"
    rel = 18
    w = int(f.getlength(txt)) + 2 * rel
    w += w % 2
    h = 72 + 2 * rel
    x, y = (marca_h.ancho - w) // 2 // 2 * 2, int(marca_h.pc["zona_segura"]["y0"])
    img = lienzo.papel_region(marca_h, x, y, w, h)
    ImageDraw.Draw(img).text((w / 2, h / 2), txt, font=f, fill=marca_h.color("lapiz_rojo"), anchor="mm")
    return placas.Placa("puesto", x, y, 0.0, float(segundos), img)


def placas_del_segmento(marca_h: Marca, numero: int, credito: str, lineas: list[Linea], duracion: float,
                        puesto: int | None = None) -> list:
    t = Toma(numero=numero, gancho="", palabra_lapiz="", credito=credito)
    todas = [placas.placa_fila(marca_h, t, duracion), placas.placa_mosca(marca_h, duracion)]
    if puesto is not None:
        todas.append(placa_puesto(marca_h, puesto, min(2.2, duracion / 3)))
    for i, ln in enumerate(lineas):
        try:
            todas.append(placas.placa_dialogo(marca_h, i, ln))
        except ValueError:
            continue                    # una frase que no cabe en 2 líneas se queda sin subtítulo (no frena el clip)
    return todas


def pico_de_audio(mp4: Path, desde: float, hasta: float) -> float | None:
    """Segundo del golpe de volumen más alto entre `desde` y `hasta` (ventanas de 0,25 s), o None sin audio."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                           capture_output=True, timeout=plazo.tiempo_para(60))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
    v = 2000
    n = len(a) // v
    if n < 2:
        return None
    rms = np.sqrt((a[:n * v].reshape(n, v) ** 2).mean(axis=1))
    i0, i1 = max(0, int(desde * 4)), min(n, int(hasta * 4))
    if i1 <= i0:
        return None
    return round((i0 + int(np.argmax(rms[i0:i1]))) / 4, 2)


def render_segmento(marca_h: Marca, receta: dict, numero: int, creador: str, fuente: Path, salida: Path,
                    trabajo: Path, cfg: dict, sfx: dict | None = None, puesto: int | None = None) -> dict:
    """Renderiza la toma en horizontal. Devuelve lo que la puerta y el armado necesitan (duración, líneas, tramo)."""
    trabajo.mkdir(parents=True, exist_ok=True)
    info = componer.sondear_video(fuente)
    tr = tramo_con_contexto(receta, float(info["duracion"]), float(cfg.get("contexto_s", 1.0)))
    lineas = lineas_de(receta, tr.desplazamiento, tr.duracion)
    credito = credito_de(receta, creador)
    todas = placas_del_segmento(marca_h, numero, credito, lineas, tr.duracion, puesto)
    rutas = []
    for i, p in enumerate(todas):
        ruta = trabajo / f"placa_{i:02d}_{p.tipo}.png"
        p.imagen.save(ruta)
        rutas.append((str(ruta), p.x, p.y, p.t0, p.t1))
    recorte = recorte_16x9(int(info["ancho"]), int(info["alto"]))
    video = {"ruta": str(fuente), "inicio": tr.inicio, "recorte": recorte, "audio": info["audio"]}
    cierre_ff, cierre_plan = componer.datos_cierre(marca_h, receta.get("ficha") or {}, sfx)
    cmd = componer.orden_ffmpeg_pantalla_completa(salida=str(salida), duracion=tr.duracion, ancho=marca_h.ancho,
                                                 alto=marca_h.alto, video=video, placas=rutas, tramos=tr.tramos,
                                                 cierre=cierre_ff)
    salida.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True, timeout=plazo.tiempo_para(300))
    plan = {"toma": numero, "credito": credito, "duracion": tr.duracion, "inicio": tr.inicio,
            "tramos": tr.tramos, "recorte": recorte, "lineas": len(lineas), "capas": [p.capa() for p in todas],
            "cierre": cierre_plan, "audio": bool(info["audio"])}
    (trabajo / "segmento.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan

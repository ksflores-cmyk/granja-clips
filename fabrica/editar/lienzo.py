"""Página de guion: papel con grano, fila superior, gancho con lápiz, regla y línea de escena."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import lapiz
from .marca import Marca

PUNTUACION = ".,;:¿?¡!\"'()«»…"


@dataclass
class Toma:
    numero: int
    gancho: str                  # sin "TOMA nnn —"; 6 palabras como máximo
    palabra_lapiz: str           # una palabra del gancho
    lapiz: str = "subrayado"     # subrayado o círculo
    escena: str = ""             # línea de escena
    credito: str = ""            # "Con: ..." o "Fuente: ..."


def partir(texto: str, f: ImageFont.FreeTypeFont, ancho_max: float) -> list[str]:
    """Parte el texto en líneas que caben en `ancho_max` píxeles (por palabras).

    ValueError si una palabra sola excede el ancho: no hay forma de partirla sin salirse.
    """
    lineas: list[str] = []
    actual = ""
    for p in texto.split():
        if f.getlength(p) > ancho_max:
            raise ValueError(f"la palabra {p!r} no cabe en {ancho_max:.0f} px de ancho")
        prueba = (actual + " " + p).strip()
        if f.getlength(prueba) <= ancho_max:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    return lineas


def _limpia(palabra: str) -> str:
    return palabra.strip(PUNTUACION).upper()


def preparar_gancho(marca: Marca, gancho: str, palabra_lapiz: str) -> list[str]:
    """Valida el gancho y devuelve sus líneas en mayúsculas. ValueError con el motivo si no vale."""
    palabras = gancho.split()
    if not palabras:
        raise ValueError("el gancho está vacío")
    if len(palabras) > marca.max_palabras_gancho:
        raise ValueError(f"gancho de {len(palabras)} palabras (máximo {marca.max_palabras_gancho}): {gancho!r}")
    f = marca.fuente("bold", marca.m("tam_gancho"))
    lineas = partir(gancho.upper(), f, marca.ancho - 2 * marca.m("margen"))
    if len(lineas) > 2:
        raise ValueError(f"el gancho no cabe en 2 líneas: {gancho!r}")
    objetivo = _limpia(palabra_lapiz)
    if not objetivo or objetivo not in [_limpia(p) for p in gancho.split()]:
        raise ValueError(f"la palabra del lápiz {palabra_lapiz!r} no está en el gancho {gancho!r}")
    return lineas


@lru_cache(maxsize=8)
def _papel_cache(ancho: int, alto: int, color: tuple, sigma: float, fibra: float, bloque: int, semilla: int):
    rng = np.random.default_rng(semilla)
    base = np.empty((alto, ancho, 3), dtype=np.float32)
    base[:] = color
    grano = rng.normal(0, sigma, (alto, ancho, 1)).astype(np.float32)
    f = rng.normal(0, fibra, (alto // bloque + 1, ancho // bloque + 1, 1)).astype(np.float32)
    f = np.kron(f, np.ones((bloque, bloque, 1), dtype=np.float32))[:alto, :ancho]
    return np.clip(base + grano + f, 0, 255).astype(np.uint8)


def papel(marca: Marca) -> Image.Image:
    """Papel con grano muy suave; la semilla es fija (misma imagen cada vez)."""
    g = marca.m("grano")
    arr = _papel_cache(marca.ancho, marca.alto, marca.color("papel"), float(g["papel"]),
                       float(g["fibra"]), int(g["bloque_fibra"]), int(g["semilla"]))
    return Image.fromarray(arr, "RGB").convert("RGBA")


def papel_region(marca: Marca, x: int, y: int, ancho: int, alto: int) -> Image.Image:
    """El mismo papel de `papel()` recortado en (x, y, ancho, alto): las placas de la plantilla v2 salen de aquí."""
    if not (0 <= x and 0 <= y and ancho > 0 and alto > 0 and x + ancho <= marca.ancho and y + alto <= marca.alto):
        raise ValueError(f"la placa {ancho}x{alto} en ({x}, {y}) se sale del cuadro")
    g = marca.m("grano")
    arr = _papel_cache(marca.ancho, marca.alto, marca.color("papel"), float(g["papel"]),
                       float(g["fibra"]), int(g["bloque_fibra"]), int(g["semilla"]))
    return Image.fromarray(arr[y:y + alto, x:x + ancho], "RGB").convert("RGBA")


def fila_superior(img: Image.Image, marca: Marca, numero: int) -> None:
    """`TOMA nnn` a un lado y la mosca en la esquina superior elegida en marca.yml."""
    f = marca.fuente("bold", marca.m("tam_fila"))
    y = marca.m("fila_superior_y")
    margen = marca.m("margen")
    tinta = marca.color("tinta")
    toma_txt = f"TOMA {numero:03d}"
    mosca = marca.mosca["texto"]
    alfa = int(round(255 * float(marca.m("opacidad_mosca"))))
    capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    if marca.mosca["esquina"] == "superior_izquierda":
        d.text((margen, y), mosca, font=f, fill=tinta + (alfa,))
        d.text((img.width - margen - f.getlength(toma_txt), y), toma_txt, font=f, fill=tinta + (255,))
    else:
        d.text((margen, y), toma_txt, font=f, fill=tinta + (255,))
        d.text((img.width - margen - f.getlength(mosca), y), mosca, font=f, fill=tinta + (alfa,))
    img.alpha_composite(capa)


def cabecera(marca: Marca, t: Toma) -> tuple[Image.Image, int]:
    """Página con fila superior, gancho con lápiz rojo, regla y línea de escena.

    Devuelve la imagen RGB y la coordenada y donde empieza el contenido (video o diálogo).
    """
    lineas = preparar_gancho(marca, t.gancho, t.palabra_lapiz)
    img = papel(marca)
    fila_superior(img, marca, t.numero)
    d = ImageDraw.Draw(img)
    margen = marca.m("margen")
    tinta = marca.color("tinta")
    f = marca.fuente("bold", marca.m("tam_gancho"))
    y = marca.m("gancho_y")
    objetivo = _limpia(t.palabra_lapiz)
    caja = None
    for linea in lineas:
        d.text((margen, y), linea, font=f, fill=tinta)
        if caja is None:
            cursor = 0
            for palabra in linea.split(" "):
                if _limpia(palabra) == objetivo:
                    nucleo = palabra.strip(PUNTUACION)
                    x0 = margen + f.getlength(linea[:cursor + palabra.find(nucleo)])
                    bb = d.textbbox((x0, y), nucleo, font=f)
                    caja = (x0, bb[1], x0 + f.getlength(nucleo), bb[3])
                    break
                cursor += len(palabra) + 1
        y += marca.m("alto_linea_gancho")
    x0, y0, x1, y1 = caja
    tr = lapiz.trazo_para(marca, t.lapiz, t.numero, x1 - x0, y1 - y0)
    if lapiz.normalizar_tipo(t.lapiz) == "subrayado":
        izq = tr.info["sobresale_izq"]
        bajo = marca.m("subrayado")["bajo_palabra"]
        img.alpha_composite(tr, (int(round(x0 - izq - lapiz.RELLENO)), int(round(y1 + bajo - tr.height / 2))))
    else:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        img.alpha_composite(tr, (int(round(cx - tr.width / 2)), int(round(cy - tr.height / 2))))
    y += marca.m("espacio_tras_gancho")
    d.line([(margen, y), (marca.ancho - margen, y)], fill=tinta, width=int(marca.m("regla_grosor")))
    y += marca.m("espacio_tras_regla")
    if t.escena:
        d.text((margen, y), t.escena.upper(), font=marca.fuente("regular", marca.m("tam_escena")), fill=tinta)
        y += marca.m("alto_escena")
    return img.convert("RGB"), int(y + marca.m("espacio_tras_escena"))

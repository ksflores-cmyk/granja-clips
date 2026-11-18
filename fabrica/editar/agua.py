"""Marca de agua de una campaña: el logo que la ficha declara en `marca_agua`.

Lugar, ancho, margen, opacidad y placa salen de `marca.yml` (clave `marca_agua`). Va sobre el cuerpo de video,
en una esquina, para no tapar nunca la cabecera, el diálogo ni la mosca. Una placa de papel detrás del logo oscuro
le da contraste sobre cualquier metraje.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .marca import Marca

POSICIONES = ("cuerpo_inferior_derecha", "cuerpo_inferior_izquierda",
              "cuerpo_superior_derecha", "cuerpo_superior_izquierda")


def logo(ruta: Path, marca: Marca) -> Image.Image:
    """Placa de papel con el logo encima, ya a su tamaño y con la opacidad de marca.yml (RGBA)."""
    cfg = marca.marca_agua
    img = Image.open(ruta).convert("RGBA")
    ancho = int(cfg["ancho"])
    alto = max(1, int(round(ancho * img.height / img.width)))
    img = img.resize((ancho, alto), Image.LANCZOS)
    pad = int(cfg["placa"])
    placa = Image.new("RGBA", (ancho + 2 * pad, alto + 2 * pad), marca.color("papel") + (255,))
    placa.alpha_composite(img, (pad, pad))
    alfa = int(round(255 * float(cfg["opacidad"])))
    placa.putalpha(placa.getchannel("A").point(lambda a: a * alfa // 255))
    return placa


def caja(marca: Marca, cuerpo: dict, tam: tuple[int, int]) -> dict:
    """{x, y, ancho, alto} de la placa dentro del cuadro, en la esquina elegida del cuerpo de video."""
    cfg = marca.marca_agua
    pos, mg = cfg["posicion"], int(cfg["margen"])
    if pos not in POSICIONES:
        raise ValueError(f"marca_agua.posicion {pos!r}: debe ser una de {', '.join(POSICIONES)}")
    w, h = tam
    x = cuerpo["x"] + mg if pos.endswith("izquierda") else cuerpo["x"] + cuerpo["ancho"] - mg - w
    y = cuerpo["y"] + mg if "superior" in pos else cuerpo["y"] + cuerpo["alto"] - mg - h
    if x < cuerpo["x"] or y < cuerpo["y"] or x + w > cuerpo["x"] + cuerpo["ancho"] \
            or y + h > cuerpo["y"] + cuerpo["alto"]:
        raise ValueError("la marca de agua no cabe dentro del cuerpo de video")
    return {"x": int(x), "y": int(y), "ancho": int(w), "alto": int(h)}


def capa(marca: Marca, ruta: Path, cuerpo: dict) -> tuple[Image.Image, dict]:
    """(capa del tamaño del cuadro, caja). La capa va entera como una más de las del compositor."""
    placa = logo(ruta, marca)
    c = caja(marca, cuerpo, placa.size)
    lienzo = Image.new("RGBA", (marca.ancho, marca.alto), (0, 0, 0, 0))
    lienzo.alpha_composite(placa, (c["x"], c["y"]))
    return lienzo, c

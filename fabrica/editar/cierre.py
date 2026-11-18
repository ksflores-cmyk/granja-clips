"""Cierre: página limpia con la fila superior, `CORTE.` a la derecha y el crédito debajo."""
from __future__ import annotations

from PIL import Image, ImageDraw

from . import lienzo
from .marca import Marca


def pagina_cierre(marca: Marca, t: "lienzo.Toma") -> Image.Image:
    img = lienzo.papel(marca)
    lienzo.fila_superior(img, marca, t.numero)
    d = ImageDraw.Draw(img)
    tinta = marca.color("tinta")
    derecha = marca.ancho - marca.m("margen")
    f = marca.fuente("bold", marca.m("tam_corte"))
    d.text((derecha - f.getlength("CORTE."), marca.m("corte_y")), "CORTE.", font=f, fill=tinta)
    if t.credito:
        fc = marca.fuente("regular", marca.m("tam_credito"))
        d.text((derecha - fc.getlength(t.credito), marca.m("credito_y")), t.credito, font=fc, fill=tinta)
    return img.convert("RGB")

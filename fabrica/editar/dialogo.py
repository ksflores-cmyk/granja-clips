"""Bloques de diálogo como en un guion: NOMBRE, (acotación en cursiva) y frase, centrados."""
from __future__ import annotations

from PIL import Image, ImageDraw

from .lienzo import partir
from .marca import Marca


def lineas_frase(marca: Marca, frase: str) -> list[str]:
    ancho = marca.columna[1] - marca.columna[0]
    return partir(frase, marca.fuente("regular", marca.m("tam_frase")), ancho)


def texto_acotacion(acotacion: str) -> str:
    return f"({acotacion.strip().strip('()').strip()})"


def validar_bloque(marca: Marca, nombre: str | None, frase: str | None, acotacion: str | None) -> list[str]:
    """Valida el bloque y devuelve las líneas de la frase. ValueError con el motivo si no vale."""
    if not frase and not acotacion:
        raise ValueError("el bloque de diálogo no tiene frase ni acotación")
    if frase and not nombre:
        raise ValueError(f"la frase necesita un nombre; sin nombre sólo va acotación: {frase!r}")
    lineas: list[str] = []
    if frase:
        lineas = lineas_frase(marca, frase)
        if len(lineas) > marca.max_lineas_frase:
            raise ValueError(f"frase de {len(lineas)} líneas (máximo {marca.max_lineas_frase}): {frase!r}")
    if acotacion:
        f = marca.fuente("italic", marca.m("tam_acotacion"))
        if f.getlength(texto_acotacion(acotacion)) > marca.columna[1] - marca.columna[0]:
            raise ValueError(f"la acotación no cabe en una línea: {acotacion!r}")
    return lineas


def bloque(marca: Marca, y: int, *, nombre: str | None = None, frase: str | None = None,
           acotacion: str | None = None) -> Image.Image:
    """Capa transparente a página completa con el bloque que empieza en `y`.

    Con nombre y frase: NOMBRE, acotación opcional y frase. Sólo acotación: sin nombre.
    """
    lineas = validar_bloque(marca, nombre, frase, acotacion)
    capa = Image.new("RGBA", (marca.ancho, marca.alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    tinta = marca.color("tinta")
    centro = (marca.columna[0] + marca.columna[1]) / 2
    if nombre:
        f = marca.fuente("regular", marca.m("tam_nombre"))
        txt = nombre.upper()
        d.text((centro - f.getlength(txt) / 2, y), txt, font=f, fill=tinta)
        y += marca.m("alto_nombre")
    if acotacion:
        f = marca.fuente("italic", marca.m("tam_acotacion"))
        txt = texto_acotacion(acotacion)
        d.text((centro - f.getlength(txt) / 2, y), txt, font=f, fill=tinta)
        y += marca.m("alto_acotacion")
    f = marca.fuente("regular", marca.m("tam_frase"))
    for linea in lineas:
        d.text((centro - f.getlength(linea) / 2, y), linea, font=f, fill=tinta)
        y += marca.m("alto_linea_frase")
    return capa

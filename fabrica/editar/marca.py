"""Lectura de `marca.yml`: colores, fuentes y medidas. El código no escribe ninguno a mano."""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

import yaml
from PIL import ImageFont

from ..config import raiz_repo


def hex_a_rgb(valor: str) -> tuple[int, int, int]:
    v = valor.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


class Marca:
    """Sistema visual cargado de `marca.yml` (o de un diccionario equivalente, para pruebas)."""

    def __init__(self, datos: dict, raiz: str | Path | None = None) -> None:
        self.datos = datos
        self.raiz = raiz_repo(raiz)

    @classmethod
    def cargar(cls, raiz: str | Path | None = None) -> "Marca":
        r = raiz_repo(raiz)
        with open(r / "marca.yml", encoding="utf-8") as f:
            return cls(yaml.safe_load(f), r)

    def con_maqueta(self, **cambios) -> "Marca":
        """Copia con medidas de la maqueta cambiadas (útil en pruebas)."""
        datos = copy.deepcopy(self.datos)
        datos["maqueta"].update(cambios)
        return Marca(datos, self.raiz)

    # -- colores
    def color(self, nombre: str) -> tuple[int, int, int]:
        return hex_a_rgb(self.datos["colores"][nombre])

    # -- medidas
    @property
    def maqueta(self) -> dict:
        return self.datos["maqueta"]

    def m(self, clave: str):
        return self.maqueta[clave]

    @property
    def ancho(self) -> int:
        return int(self.datos["resolucion"][0])

    @property
    def alto(self) -> int:
        return int(self.datos["resolucion"][1])

    @property
    def columna(self) -> tuple[int, int]:
        c = self.m("columna_dialogo")
        return int(c[0]), int(c[1])

    @property
    def mosca(self) -> dict:
        return self.datos["mosca"]

    @property
    def marca_agua(self) -> dict:
        """Posición, ancho, margen, opacidad y placa del logo de campaña (clave `marca_agua` de marca.yml)."""
        return self.datos["marca_agua"]

    @property
    def pc(self) -> dict:
        """Medidas de la plantilla de pantalla completa (clave `pantalla_completa` de marca.yml)."""
        return self.datos["pantalla_completa"]

    def pc_marca(self) -> "Marca":
        """Copia cuyo trazo de lápiz usa las medidas compactas de la plantilla de pantalla completa."""
        datos = copy.deepcopy(self.datos)
        datos["maqueta"]["circulo"] = copy.deepcopy(self.pc["lapiz"]["circulo"])
        datos["maqueta"]["subrayado"] = copy.deepcopy(self.pc["lapiz"]["subrayado"])
        return Marca(datos, self.raiz)

    @property
    def max_palabras_gancho(self) -> int:
        return int(self.datos["encabezado_max_palabras"])

    @property
    def max_lineas_frase(self) -> int:
        return int(self.datos["subtitulo_max_lineas"])

    # -- fuentes
    def ruta_fuente(self, estilo: str) -> Path:
        return self.raiz / self.datos["fuente"]["archivos"][estilo]

    def fuente(self, estilo: str, tam: int | float) -> ImageFont.FreeTypeFont:
        return _cargar_fuente(str(self.ruta_fuente(estilo)), int(tam))

    # -- prohibidos
    def ganchos_prohibidos(self) -> list[str]:
        """Frases de la lista `prohibido` de marca.yml que nombran ganchos genéricos."""
        salida: list[str] = []
        for item in self.datos.get("prohibido", []):
            if str(item).lower().startswith("ganchos genéricos"):
                dentro = str(item).partition("(")[2].rpartition(")")[0]
                salida += [x.strip() for x in dentro.split(",") if x.strip()]
        return salida


@lru_cache(maxsize=64)
def _cargar_fuente(ruta: str, tam: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(ruta, tam)

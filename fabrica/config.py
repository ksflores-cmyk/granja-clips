"""Rutas del repo y carga de los YAML de configuración."""
from __future__ import annotations

from pathlib import Path

import yaml

RAIZ_POR_DEFECTO = Path(__file__).resolve().parent.parent

ARCHIVOS = {
    "temporada": "temporada.yml",
    "fuentes": "fuentes.yml",
    "marca": "marca.yml",
    "horarios": "config/horarios.yml",
    "modo": "config/modo.yml",
    "canales": "config/canales.yml",
}


def raiz_repo(raiz: str | Path | None = None) -> Path:
    """Devuelve la raíz del repo: el argumento o el directorio padre del paquete."""
    return Path(raiz).resolve() if raiz is not None else RAIZ_POR_DEFECTO


def cargar_yaml(nombre: str, raiz: str | Path | None = None):
    """Carga un YAML relativo a la raíz del repo."""
    ruta = raiz_repo(raiz) / nombre
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cargar_todo(raiz: str | Path | None = None) -> dict:
    """Carga todos los YAML conocidos en un dict por nombre corto."""
    return {clave: cargar_yaml(ruta, raiz) for clave, ruta in ARCHIVOS.items()}

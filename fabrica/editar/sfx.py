"""Efectos de sonido CC0 para clips (tarea #20 «historia»).

El catálogo vive en `marca/sfx/efectos.yml` (con la LICENCIA.md de cada efecto al lado); el audio NO vive en el
repo: está en el release privado `sfx` y los workflows lo bajan a la carpeta de `GRANJA_SFX_DIR`. Un efecto sólo
se usa si está `verificada`, tiene su LICENCIA.md y el archivo bajado coincide con el sha256 anotado.
Nunca lanza: sin carpeta o sin archivos devuelve vacío.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from ..config import raiz_repo
from .musica import sha256_cacheado

VAR_CARPETA = "GRANJA_SFX_DIR"


def carpeta() -> Path | None:
    """La carpeta de audios de `GRANJA_SFX_DIR`, si está definida."""
    v = os.environ.get(VAR_CARPETA, "").strip()
    return Path(v) if v else None


def manifiesto(raiz=None) -> dict:
    try:
        ruta = raiz_repo(raiz) / "marca" / "sfx" / "efectos.yml"
        if not ruta.is_file():
            return {}
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return datos if isinstance(datos, dict) else {}


def disponibles(raiz=None, carpeta_audio: Path | None = None) -> dict[str, dict]:
    """{id: {id, tipo, ruta, sha256, ...}} de los efectos verificados cuyo archivo está con el sha256 anotado."""
    try:
        base = Path(carpeta_audio) if carpeta_audio is not None else carpeta()
        if base is None:
            return {}
        datos = manifiesto(raiz)
        permitidas = {str(x) for x in (datos.get("licencias_permitidas") or [])}
        salida: dict[str, dict] = {}
        for e in datos.get("efectos") or []:
            if not isinstance(e, dict) or not (e.get("id") and e.get("asset") and e.get("sha256")):
                continue
            if e.get("estado") != "verificada" or str(e.get("licencia")) not in permitidas:
                continue
            if not (raiz_repo(raiz) / "marca" / "sfx" / str(e["id"]) / "LICENCIA.md").is_file():
                continue
            ruta = base / str(e["asset"])
            try:
                if ruta.is_file() and sha256_cacheado(ruta) == str(e["sha256"]).lower():
                    salida[str(e["id"])] = {**e, "ruta": str(ruta)}
            except OSError:
                continue
        return salida
    except Exception:
        return {}


def de_tipo(raiz, tipo: str, carpeta_audio: Path | None = None) -> dict | None:
    """El primer efecto disponible de ese tipo, o None."""
    for e in disponibles(raiz, carpeta_audio).values():
        if e.get("tipo") == tipo:
            return e
    return None

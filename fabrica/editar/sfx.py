"""Efectos de sonido CC0 de biblioteca para la historia (como `editar/musica.py`).

El catálogo vive en `marca/sfx/efectos.yml` (lista `efectos`, cada uno con `id`, `tipo`, `estado`, `asset`, `sha256` y su
LICENCIA.md al lado); los archivos NO viven en el repo: están en el release privado `sfx` y los workflows los bajan a
la carpeta de la variable de entorno `GRANJA_SFX_DIR`. Un efecto sólo se usa si está `verificada`, tiene LICENCIA.md y
el archivo bajado coincide con el sha256 anotado.

NOTA: interfaz mínima mientras llega la del constructor de efectos (#20 parte E); el jefe resuelve la unión.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

from ..config import raiz_repo

VAR_CARPETA = "GRANJA_SFX_DIR"


def carpeta() -> Path | None:
    """La carpeta de efectos de `GRANJA_SFX_DIR`, si está definida."""
    v = os.environ.get(VAR_CARPETA, "").strip()
    return Path(v) if v else None


def _catalogo(raiz) -> list[dict]:
    ruta = raiz_repo(raiz) / "marca" / "sfx" / "efectos.yml"
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    lista = datos.get("efectos") if isinstance(datos, dict) else None
    return [e for e in (lista or []) if isinstance(e, dict)]


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def disponibles(raiz=None) -> dict[str, dict]:
    """{id: {id, tipo, ruta, sha256}} de los efectos verificados cuyo archivo bajado tiene el sha256 anotado.
    Vacío si no hay carpeta ni efectos. Nunca lanza."""
    try:
        base = carpeta()
        if base is None:
            return {}
        r = raiz_repo(raiz)
        salida: dict[str, dict] = {}
        for e in _catalogo(raiz):
            try:
                sid = str(e.get("id") or "")
                if (not sid or e.get("estado") != "verificada" or not e.get("sha256") or not e.get("asset")
                        or not (r / "marca" / "sfx" / sid / "LICENCIA.md").is_file()):
                    continue
                ruta = base / str(e["asset"])
                if ruta.is_file() and _sha256(ruta) == str(e["sha256"]).lower():
                    salida[sid] = {"id": sid, "tipo": str(e.get("tipo") or ""), "ruta": str(ruta),
                                   "sha256": str(e["sha256"]).lower()}
            except OSError:
                continue
        return salida
    except Exception:  # noqa: BLE001 (la historia nunca debe caerse por los efectos)
        return {}


def de_tipo(raiz, tipo: str) -> dict | None:
    """El primer efecto disponible (por id) de ese tipo, o None."""
    for sid in sorted(d := disponibles(raiz)):
        if d[sid]["tipo"] == tipo:
            return d[sid]
    return None

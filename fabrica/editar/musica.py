"""Música CC0 de biblioteca para piezas sin audio propio (decisión de Kevin del 19 nov 2026).

El manifiesto vive en `marca/musica/pistas.yml` (con la LICENCIA.md de cada pista al lado); los archivos de
audio NO viven en el repo: están en el release privado `musica` y los workflows los bajan a una carpeta que la
fábrica lee de la variable de entorno `GRANJA_MUSICA_DIR`. Una pista sólo se usa si está `verificada`, tiene su
LICENCIA.md y el archivo bajado coincide con el sha256 anotado.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

from ..config import raiz_repo

VAR_CARPETA = "GRANJA_MUSICA_DIR"
_CACHE_SHA: dict[tuple[str, int, int], str] = {}


def manifiesto(raiz=None) -> dict:
    """Contenido de `marca/musica/pistas.yml` ({} si no existe o no es un diccionario)."""
    ruta = raiz_repo(raiz) / "marca" / "musica" / "pistas.yml"
    if not ruta.is_file():
        return {}
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    return datos if isinstance(datos, dict) else {}


def ruta_licencia(raiz, pista_id: str) -> Path:
    return raiz_repo(raiz) / "marca" / "musica" / str(pista_id) / "LICENCIA.md"


def cargar(raiz=None) -> list[dict]:
    """Pistas verificadas: sha256 anotado, licencia permitida y LICENCIA.md presente."""
    datos = manifiesto(raiz)
    permitidas = {str(x) for x in (datos.get("licencias_permitidas") or [])}
    salida = []
    for p in datos.get("pistas") or []:
        if not isinstance(p, dict):
            continue
        if (p.get("estado") == "verificada" and p.get("sha256") and str(p.get("licencia")) in permitidas
                and p.get("id") and p.get("asset") and ruta_licencia(raiz, p["id"]).is_file()):
            salida.append(dict(p))
    return salida


def carpeta() -> Path | None:
    """La carpeta de audios de `GRANJA_MUSICA_DIR`, si está definida."""
    v = os.environ.get(VAR_CARPETA, "").strip()
    return Path(v) if v else None


def sha256_cacheado(ruta: Path) -> str:
    """sha256 del archivo; se recalcula sólo si cambió su fecha o su tamaño."""
    st = Path(ruta).stat()
    clave = (str(Path(ruta).resolve()), st.st_mtime_ns, st.st_size)
    if clave not in _CACHE_SHA:
        h = hashlib.sha256()
        with open(ruta, "rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""):
                h.update(bloque)
        _CACHE_SHA[clave] = h.hexdigest()
    return _CACHE_SHA[clave]


def disponibles(raiz=None, carpeta_audio: Path | None = None) -> list[dict]:
    """Pistas verificadas cuyo archivo está en la carpeta con el sha256 anotado. Cada una trae `ruta`."""
    base = Path(carpeta_audio) if carpeta_audio is not None else carpeta()
    if base is None:
        return []
    salida = []
    for p in cargar(raiz):
        ruta = base / str(p["asset"])
        try:
            if ruta.is_file() and sha256_cacheado(ruta) == str(p["sha256"]).lower():
                salida.append({**p, "ruta": str(ruta)})
        except OSError:
            continue
    return salida


def elegir(raiz, numero_toma: int, carpeta_audio: Path | None = None) -> dict | None:
    """Rota por número de toma entre las disponibles: dos tomas seguidas no repiten pista."""
    lista = disponibles(raiz, carpeta_audio)
    if not lista:
        return None
    return lista[int(numero_toma) % len(lista)]


def lleva_musica(ficha: dict | None, audio_de_origen: bool) -> bool:
    """Sólo una pieza sin audio propio y sin `reglas.sin_musica` lleva música."""
    if ((ficha or {}).get("reglas") or {}).get("sin_musica"):
        return False
    return not audio_de_origen

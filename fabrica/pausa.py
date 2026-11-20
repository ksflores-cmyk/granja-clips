"""Interruptor PAUSA: si el archivo existe, la fábrica no publica."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import raiz_repo

ZONA = ZoneInfo("America/Guatemala")


def _ruta(raiz) -> Path:
    return raiz_repo(raiz) / "PAUSA"


def leer_pausa(raiz=None) -> dict:
    """Lee PAUSA como un dict clave: valor. Vacío si no existe."""
    ruta = _ruta(raiz)
    if not ruta.exists():
        return {}
    datos = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if ":" in linea:
            clave, _, valor = linea.partition(":")
            datos[clave.strip()] = valor.strip()
    return datos


def esta_pausada(raiz=None) -> tuple[bool, str]:
    """Si existe PAUSA está pausada, sin importar `hasta` (que es informativo)."""
    if not _ruta(raiz).exists():
        return (False, "")
    return (True, leer_pausa(raiz).get("motivo", ""))


def crear_pausa(raiz, motivo: str, autor: str) -> Path:
    """Crea PAUSA con motivo en una línea, fecha de inicio y autor."""
    motivo = " ".join(str(motivo).split())
    if not motivo:
        raise ValueError("La pausa necesita un motivo.")
    desde = datetime.now(ZONA).replace(microsecond=0).isoformat()
    ruta = _ruta(raiz)
    ruta.write_text(
        f"motivo: {motivo}\ndesde: {desde}\nautor: {autor}\n", encoding="utf-8"
    )
    return ruta


# ---------------------------------------------------------------- pausa de la ficha T

def _ruta_t(raiz) -> Path:
    return raiz_repo(raiz) / "estado" / "pausa-t.yml"


def pausa_t_activa(raiz=None) -> tuple[bool, str]:
    """Si existe estado/pausa-t.yml la ficha T está en pausa (las demás fuentes siguen). Devuelve (activa, motivo)."""
    ruta = _ruta_t(raiz)
    if not ruta.exists():
        return (False, "")
    motivo = ""
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.lstrip().startswith("#") and linea.partition(":")[0].strip() == "motivo":
            motivo = linea.partition(":")[2].strip()
    return (True, motivo)


def crear_pausa_t(raiz, motivo: str, autor: str) -> Path:
    """Crea estado/pausa-t.yml con motivo en una línea, fecha de inicio y autor. Se quita borrando el archivo."""
    motivo = " ".join(str(motivo).split())
    if not motivo:
        raise ValueError("La pausa de la ficha T necesita un motivo.")
    desde = datetime.now(ZONA).replace(microsecond=0).isoformat()
    ruta = _ruta_t(raiz)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("# Pausa de la ficha T: sólo Kevin la quita, borrando este archivo.\n"
                    f"motivo: {motivo}\ndesde: {desde}\nautor: {autor}\n", encoding="utf-8")
    return ruta

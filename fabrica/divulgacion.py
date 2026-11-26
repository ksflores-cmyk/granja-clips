"""Divulgación nativa de los clips de campaña (aviso A-20261125-4). Ver config/divulgacion.yml."""
from __future__ import annotations

from .config import cargar_yaml


def es_campana(ficha: dict | None) -> bool:
    return isinstance(ficha, dict) and ficha.get("tipo") == "A"


def permite(raiz, plataforma: str, ficha: dict | None) -> tuple[bool, str]:
    """¿Puede salir por Buffer esta toma en esta red? Las que no son de campaña, siempre; las de campaña, sólo si la
    red admite la divulgación nativa desde Buffer. Sin config, no (más vale no publicar que publicar sin aviso)."""
    if not es_campana(ficha):
        return True, ""
    redes = (cargar_yaml("config/divulgacion.yml", raiz) or {}).get("redes") or {}
    r = redes.get(plataforma) or {}
    if r.get("automatica") is True:
        return True, ""
    return False, str(r.get("motivo") or "sin divulgación nativa configurada para esta red")

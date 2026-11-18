"""Una sola regla de "ficha publicable" y de "ficha descargable" (lista blanca)."""
from __future__ import annotations

from datetime import date, datetime

from .config import cargar_yaml


def cargar_fuentes(raiz=None) -> list[dict]:
    datos = cargar_yaml("fuentes.yml", raiz)
    return [f for f in (datos or []) if isinstance(f, dict)]


def _a_fecha(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def es_publicable(ficha: dict, hoy: date, plataforma: str | None = None,
                  pilar: str | None = None) -> tuple[bool, str]:
    """Verdadero sólo si la ficha cumple todo. El motivo dice cuál condición falló."""
    if ficha.get("estado") != "vigente":
        return False, f"estado {ficha.get('estado')!r}: debe ser vigente"
    if ficha.get("uso") != "produccion":
        return False, f"uso {ficha.get('uso')!r}: debe ser produccion"
    if ficha.get("publicable") is not True:
        return False, "publicable no es true"
    vence = ficha.get("vence")
    if vence is not None:
        f = _a_fecha(vence)
        if f is None:
            return False, f"vence ilegible ({vence!r})"
        if f < _a_fecha(hoy):
            return False, f"vencida el {f}"
    pr = ficha.get("presupuesto_restante")
    if pr == 0 or (isinstance(pr, str) and pr.strip().lower() == "agotado"):
        return False, "presupuesto agotado"
    reglas = ficha.get("reglas") or {}
    if plataforma is not None and plataforma not in (reglas.get("plataformas") or []):
        return False, f"plataforma {plataforma} no está en reglas.plataformas"
    pilares = ficha.get("pilares")
    if ficha.get("tipo") == "D" and pilar is not None and pilar != "informativa":
        return False, "tipo D sólo con pilar informativa"
    if pilar is not None and pilares and pilar not in pilares:
        return False, f"pilar {pilar} no está en los pilares de la ficha"
    return True, "ok"


def _es_https(u) -> bool:
    return isinstance(u, str) and u.startswith("https://") and len(u) > 8


def puede_descargar(ficha: dict) -> tuple[bool, str]:
    tipo = ficha.get("tipo")
    if tipo == "C":
        return True, "material propio"
    if tipo == "D":
        return False, "tipo D no descarga video"
    if tipo in ("A", "B"):
        material = ficha.get("material")
        if isinstance(material, list) and material and all(_es_https(u) for u in material):
            return True, "la ficha trae material con URL https directas"
        if (ficha.get("reglas") or {}).get("permite_descarga") is True:
            return True, "reglas.permite_descarga es true"
        return False, "sin permite_descarga ni material: sólo enlace, no se descarga"
    return False, f"tipo {tipo!r} desconocido"

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
    if ficha.get("tipo") == "D" and pilar != "informativa":
        return False, f"tipo D exige pilar informativa (recibió {pilar!r})"
    if pilar is not None and pilares and pilar not in pilares:
        return False, f"pilar {pilar} no está en los pilares de la ficha"
    return True, "ok"


def _es_https(u) -> bool:
    return isinstance(u, str) and u.startswith("https://") and len(u) > 8


def urls_material(ficha: dict) -> list[str]:
    """URL del material de la ficha. Cada entrada es el texto de la URL o {url, creador, etiquetas}."""
    material = ficha.get("material")
    if not isinstance(material, list):
        return []
    return [m.get("url") if isinstance(m, dict) else m for m in material]


def entrada_material(ficha: dict, url: str | None = None, creador: str | None = None) -> dict | None:
    """La entrada de `material` con esa URL (o con ese creador), siempre como diccionario; None si no hay."""
    for m in ficha.get("material") or []:
        if not isinstance(m, dict):
            continue
        if url is not None and m.get("url") == url:
            return m
        if url is None and creador is not None and str(m.get("creador", "")).strip().lower() == creador.strip().lower():
            return m
    return None


def etiqueta_creador(ficha: dict, creador: str | None, plataforma: str) -> str | None:
    """Cómo se etiqueta al creador recortado en esa red (`etiquetas` de su entrada de material), o None."""
    if not creador:
        return None
    m = entrada_material(ficha, None, creador)
    if m is None:
        return None
    e = (m.get("etiquetas") or {}).get(plataforma)
    return str(e).strip() if isinstance(e, str) and e.strip() else None


def puede_descargar(ficha: dict) -> tuple[bool, str]:
    tipo = ficha.get("tipo")
    if tipo == "C":
        return True, "material propio"
    if tipo == "D":
        return False, "tipo D no descarga video"
    if tipo in ("A", "B"):
        material = urls_material(ficha)
        if material and all(_es_https(u) for u in material):
            return True, "la ficha trae material con URL https directas"
        if (ficha.get("reglas") or {}).get("permite_descarga") is True:
            return True, "reglas.permite_descarga es true"
        return False, "sin permite_descarga ni material: sólo enlace, no se descarga"
    return False, f"tipo {tipo!r} desconocido"


def autoriza_descarga(ficha: dict | None, hoy: date, uso: str, url: str | None = None,
                      pilar: str | None = None) -> tuple[bool, str]:
    """Puerta única ANTES de abrir una conexión. El motivo nombra la condición que falló."""
    if not isinstance(ficha, dict):
        return False, "ficha ausente: la fuente no está registrada en fuentes.yml"
    if ficha.get("estado") != "vigente":
        return False, f"estado {ficha.get('estado')!r}: debe ser vigente"
    vence = ficha.get("vence")
    if vence is not None:
        f = _a_fecha(vence)
        if f is None:
            return False, f"vence ilegible ({vence!r})"
        if f < _a_fecha(hoy):
            return False, f"vencida el {f}"
    ok, motivo = puede_descargar(ficha)
    if not ok:
        return False, f"no descargable: {motivo}"
    if uso == "produccion":
        ok, motivo = es_publicable(ficha, hoy, None, pilar)
        if not ok:
            return False, f"uso produccion no autorizado: {motivo}"
    elif uso == "muestras":
        if ficha.get("uso") != "muestras":
            ok, motivo = es_publicable(ficha, hoy, None, pilar)
            if not ok:
                return False, f"uso muestras no autorizado: la ficha no es de muestras ni publicable ({motivo})"
    else:
        return False, f"uso {uso!r} desconocido (produccion o muestras)"
    material = urls_material(ficha) if ficha.get("material") is not None else None
    if material is not None and (url is None or url not in material):
        return False, f"url fuera de material: {url!r}"
    return True, "ok"

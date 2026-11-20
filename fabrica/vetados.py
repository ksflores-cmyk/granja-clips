"""Lista negra de la ficha T: logins fijos (config/vetados.yml) más los que salen de retiros.md. Sin red."""
from __future__ import annotations

import re

from .config import cargar_yaml, raiz_repo

RE_CANAL = re.compile(r"twitch\.tv/([A-Za-z0-9_]{3,25})")


def _config(raiz) -> dict:
    try:
        d = cargar_yaml("config/vetados.yml", raiz)
    except Exception:  # noqa: BLE001 (archivo ausente o ilegible: sin lista)
        return {}
    return d if isinstance(d, dict) else {}


def _lista(v) -> list[str]:
    return [str(x).strip().lower() for x in v or [] if str(x).strip()] if isinstance(v, list) else []


def equipos_vetados(raiz=None) -> list[str]:
    return _lista(_config(raiz).get("equipos_twitch"))


def filas_retiros(raiz) -> list[list[str]]:
    """Celdas de cada fila de datos de la tabla de retiros.md (sin encabezado ni separador)."""
    ruta = raiz_repo(raiz) / "retiros.md"
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    filas = []
    for linea in lineas:
        s = linea.strip()
        if not s.startswith("|"):
            continue
        celdas = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) or not c for c in celdas) and any(celdas):
            continue                                   # separador
        if celdas and celdas[0].lower() == "fecha":
            continue                                   # encabezado
        filas.append(celdas)
    return filas


def fuentes_retiradas(raiz=None) -> set[str]:
    """Ids de la columna «Fuente» de retiros.md: la fuente pasa a vetada hasta tener permiso nuevo."""
    return {f[1].strip().lower() for f in filas_retiros(raiz) if len(f) > 1 and f[1].strip()}


def _de_retiros(raiz) -> set[str]:
    out: set[str] = set()
    for celdas in filas_retiros(raiz):
        for c in celdas:
            out.update(m.lower() for m in RE_CANAL.findall(c))
        if len(celdas) > 1:
            m = re.fullmatch(r"t-([a-z0-9]+(?:-[a-z0-9]+)*)", celdas[1].strip().lower())
            if m:
                out.add(m.group(1).replace("-", "_"))
    return out


def logins_vetados(raiz=None) -> set[str]:
    return set(_lista(_config(raiz).get("logins"))) | _de_retiros(raiz)

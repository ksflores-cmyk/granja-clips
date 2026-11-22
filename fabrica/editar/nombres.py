"""Puerta de nombres: el nombre de quien habla y el del stream salen SÓLO de la ficha, nunca del modelo.

Determinista, sin LLM. `etiqueta_hablante` y `escena_segura` las usa `toma.armar` para limpiar lo que dice
Gemini; `revisar_nombres` es la puerta que corre después de armar y antes de componer (lista vacía = verde).
"""
from __future__ import annotations

import re
import unicodedata

from ..vetados import logins_vetados

STREAMER = "STREAMER"           # lo único que el modelo puede decir para nombrar al creador
OTRA_VOZ = "OTRA VOZ"           # cualquier otra voz (jugador, narrador, chat...)


def normalizar(t) -> str:
    """Minúsculas, sin acentos, sin @ y con `_` como espacio (para comparar nombres)."""
    s = "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn")
    return " ".join(s.replace("@", "").replace("_", " ").split())


def creador_de(creador: str | None, ficha: dict | None) -> str:
    """El creador de la ficha: el pasado por archivo (campañas), si no `creador` o `twitch_login` de la ficha."""
    f = ficha or {}
    return str(creador or f.get("creador") or f.get("twitch_login") or "").strip()


def nombre_corto(creador: str | None) -> str:
    """`@sebasberon`, `twitch.tv/ana` o `sebasberon` -> `SEBASBERON` / `ANA`."""
    s = str(creador or "").strip().rstrip("/").rsplit("/", 1)[-1].lstrip("@")
    return " ".join(s.split()).upper()


def etiqueta_hablante(hablante: str, creador: str | None) -> str:
    """El nombre corto del creador si el modelo dijo `STREAMER` o el nombre del creador; si no, `OTRA VOZ`."""
    nombre = nombre_corto(creador)
    h = normalizar(hablante)
    if nombre and (h == normalizar(STREAMER) or h == normalizar(nombre)):
        return nombre
    return OTRA_VOZ


def _vetado_en(texto: str, logins) -> str | None:
    t = normalizar(texto)
    for login in sorted(logins):
        n = normalizar(login)
        if n and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", t):
            return login
    return None


def escena_segura(escena: str, creador: str | None, marca, temporada: dict | None, raiz=None) -> str:
    """`INT. STREAM...` del modelo se reemplaza por la escena de la ficha; una escena con un nombre vetado también."""
    from . import sin_ia             # importación diferida: sin_ia importa ojos
    e = str(escena).strip().upper()
    if e.startswith("INT. STREAM") or _vetado_en(e, logins_vetados(raiz)):
        return sin_ia.escena_de(nombre_corto(creador), marca, temporada)
    return e


def revisar_nombres(toma, lineas, creador: str | None, raiz=None, marca=None) -> list[str]:
    """Motivos de rechazo (vacía = verde). `creador` es el de la ficha, ya resuelto con `creador_de`.

    `marca`: si se da, el rótulo del streamer de los subtítulos por voz (`voces.nombre_visible`, que recorta el nombre
    a la columna) también cuenta como el nombre del creador."""
    motivos: list[str] = []
    propio = nombre_corto(creador)
    permitidos = {normalizar(propio)}
    if marca is not None:
        from .. import voces as mod_voces      # importación diferida: voces importa este módulo
        permitidos.add(normalizar(mod_voces.nombre_visible(creador, marca)))
    for i, ln in enumerate(lineas, 1):
        nombre = (ln.nombre or "").strip()
        if nombre:
            if normalizar(nombre) not in permitidos and nombre != OTRA_VOZ:
                motivos.append(f"línea {i}: el hablante {nombre!r} no es el creador ni {OTRA_VOZ}")
        elif ln.frase:
            motivos.append(f"línea {i}: frase sin hablante")
    vetados = {v for v in logins_vetados(raiz) if normalizar(v) != normalizar(propio)}
    campos = [("escena", toma.escena), ("crédito", toma.credito), ("gancho", toma.gancho)]
    for i, ln in enumerate(lineas, 1):
        campos += [(f"nombre de la línea {i}", ln.nombre), (f"acotación de la línea {i}", ln.acotacion),
                   (f"frase de la línea {i}", ln.frase)]
    for donde, texto in campos:
        v = _vetado_en(texto or "", vetados)
        if v:
            motivos.append(f"nombre vetado {v!r} en {donde}")
    return motivos

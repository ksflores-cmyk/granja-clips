"""Cupo de YouTube Shorts (aviso A-20261125-1; decisión de Kevin «2 Shorts», 25 nov 2026).

Buffer gratis deja pasar 2 Shorts por canal cada 24 h, en ventana móvil (support.buffer.com, «Daily posting
limits»). Pasado el cupo, el post se crea y cae en `error` a su hora. Desde ahora la fábrica no crea el post de
Shorts si el cupo está lleno: esa toma sale sólo en TikTok y Reels.

Criterio de «mejor» (lo propuso el Project, 25 nov):
1. Toma de campaña (ficha tipo A que admite Shorts): entra siempre que haya cupo, a cualquier hora (paga).
2. Si no, sólo huecos desde `desde` (13:00 GT por defecto): las mañanas tienen menos alcance y quemarían el cupo
   antes de las horas buenas de la tarde y la noche.

La cuenta va desde el último envío bueno: envíos de Shorts del libro (`estado/buffer-envios.json`) en `creado`,
`enviando` o `incierto` cuyo `dueAt` cae a menos de 24 h + `margen_minutos` de la hora nueva. Los que Buffer marcó
en `error` no cuentan (Buffer no los publicó); si no se puede preguntar a Buffer, cuentan (se prefiere perder un
Short a crear uno que caerá en error).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from .config import cargar_yaml
from .registro import ZONA

ESTADOS_QUE_CUENTAN = ("creado", "enviando", "incierto")
POR_DEFECTO = {"max_24h": 2, "margen_minutos": 10, "desde": "13:00", "campana_cualquier_hora": True,
               "max_consultas": 4}


def config(raiz) -> dict:
    h = cargar_yaml("config/horarios.yml", raiz) or {}
    s = h.get("shorts") or {}
    out = dict(POR_DEFECTO)
    out.update({k: v for k, v in s.items() if k in POR_DEFECTO})
    return out


def _a_utc(iso: str) -> datetime:
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc)


def es_de_campana(ficha: dict | None) -> bool:
    """Ficha tipo A cuyas reglas admiten Shorts."""
    if not isinstance(ficha, dict) or ficha.get("tipo") != "A":
        return False
    plats = (ficha.get("reglas") or {}).get("plataformas")
    return plats is None or "shorts" in plats


def envios_en_ventana(libro: list[dict], cuando: datetime, horas: float = 24, margen_min: int = 10) -> list[dict]:
    """Envíos de Shorts que pueden contar contra el cupo de la hora `cuando`."""
    cuando = cuando.astimezone(timezone.utc)
    ventana = timedelta(hours=horas, minutes=margen_min)
    out = []
    for e in libro or []:
        if not isinstance(e, dict) or e.get("plataforma") != "shorts" or e.get("estado") not in ESTADOS_QUE_CUENTAN:
            continue
        try:
            due = _a_utc(e["dueAt"])
        except (KeyError, ValueError, TypeError):
            continue
        if abs(due - cuando) < ventana:
            out.append(e)
    return out


def cuenta(libro: list[dict], cuando: datetime, ahora: datetime, cfg: dict, estado_fn=None) -> tuple[int, list[str]]:
    """Cuántos Shorts ocupan el cupo de `cuando` y una nota por cada uno que se descontó o no se pudo consultar."""
    notas, n, consultas = [], 0, 0
    for e in envios_en_ventana(libro, cuando, 24, int(cfg["margen_minutos"])):
        ya_salio = _a_utc(e["dueAt"]) <= ahora.astimezone(timezone.utc)
        estado = e.get("estado_buffer")
        if estado is None and ya_salio and e.get("post_id") and estado_fn and consultas < int(cfg["max_consultas"]):
            consultas += 1
            try:
                estado = estado_fn(e["post_id"])
            except Exception as ex:  # noqa: BLE001 — sin respuesta de Buffer, el envío cuenta
                notas.append(f"toma {e.get('toma')}: Buffer no respondió ({type(ex).__name__}), cuenta")
        if str(estado or "").lower() == "error":
            notas.append(f"toma {e.get('toma')}: en error en Buffer, no cuenta")
            continue
        n += 1
    return n, notas


def admite(libro: list[dict], ficha: dict | None, cuando: datetime, ahora: datetime, cfg: dict,
           estado_fn=None) -> tuple[bool, str]:
    """¿Esta toma sale en Shorts? Devuelve (sí/no, motivo para el log)."""
    campana = es_de_campana(ficha)
    local = cuando.astimezone(ZONA)
    h, m = (int(x) for x in str(cfg["desde"]).split(":"))
    if not (campana and cfg["campana_cualquier_hora"]) and local.time() < time(h, m):
        return False, f"antes de las {cfg['desde']} sólo van a Shorts las tomas de campaña"
    n, notas = cuenta(libro, cuando, ahora, cfg, estado_fn)
    extra = f" ({'; '.join(notas)})" if notas else ""
    if n >= int(cfg["max_24h"]):
        return False, f"cupo lleno: {n} de {cfg['max_24h']} Shorts en 24 h{extra}"
    return True, f"cupo {n + 1} de {cfg['max_24h']}" + (" (campaña)" if campana else "") + extra

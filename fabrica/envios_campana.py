"""Registro para el envío manual de enlaces de campaña (`estado/envios-campana.json`).

Content Rewards pide pegar a mano el enlace de cada post (en Reach, 30 minutos tras publicar; en las demás
campañas no hay plazo escrito: se pega lo antes posible). Al programar un clip de campaña se guarda aquí la
plataforma, el `post_id` de Buffer, la hora programada y la campaña. Cuando Buffer dice que el post quedó `sent`,
`completar` escribe la URL publicada si Buffer la da; si no la da, deja "pendiente: copiar el enlace desde la
app". Nunca se inventa una URL.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from .config import raiz_repo
from .registro import ZONA

NOMBRE = "envios-campana.json"
PENDIENTE = "pendiente: copiar el enlace desde la app"
PEGAR = "pegar este enlace en Content Rewards"


def _ruta(raiz) -> Path:
    return raiz_repo(raiz) / "estado" / NOMBRE


def leer(raiz) -> list[dict]:
    ruta = _ruta(raiz)
    if not ruta.is_file():
        return []
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in datos if isinstance(e, dict)] if isinstance(datos, list) else []


def _guardar(raiz, datos: list[dict]) -> None:
    ruta = _ruta(raiz)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ruta.parent, delete=False, suffix=".tmp") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = Path(f.name)
    tmp.replace(ruta)


def anotar(raiz, plataforma: str, post_id: str, cuando: datetime, toma: dict) -> dict:
    """Guarda (una sola vez por `post_id`) el post programado de una campaña. Devuelve la fila."""
    datos = leer(raiz)
    previa = next((e for e in datos if e.get("post_id") == post_id), None)
    if previa:
        return previa
    fila = {"plataforma": plataforma, "post_id": post_id, "hora": cuando.astimezone(ZONA).isoformat(),
            "campana": toma.get("campana") or "", "toma": toma.get("numero"),
            "estado": "programado", "url": None, "nota": "programado: el enlace se completa cuando Buffer lo marque sent"}
    datos.append(fila)
    _guardar(raiz, datos)
    return fila


def completar(raiz, publicador) -> int:
    """Para cada envío aún sin cerrar: si el post quedó `sent`, escribe la URL (o `PENDIENTE`). Devuelve cuántos cambió."""
    datos = leer(raiz)
    cambios = 0
    for e in datos:
        if e.get("estado") == "enviado":          # cerrado: con URL, o con la nota de copiarla desde la app
            continue
        estado, url = publicador.estado_y_url(e["post_id"])
        if estado != "sent":
            continue
        e.update(estado="enviado", url=url, nota=PEGAR if url else PENDIENTE)
        cambios += 1
    if cambios:
        _guardar(raiz, datos)
    return cambios

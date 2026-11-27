"""Recetas de las tomas publicadas: lo necesario para volver a montar una toma más tarde (el video largo semanal).

El MP4 de cada toma se borra del almacén en cuanto Buffer lo publica, así que el video largo se arma otra vez desde la
fuente. La receta guarda de dónde sale el clip (ficha, creador, id y URL de origen), qué tramo se usó, los
silencios cortados y los subtítulos con sus tiempos. Va en `estado/recetas.jsonl` (una línea por toma; el ciclo ya
guarda `estado/`). Nada de rutas temporales ni secretos: el repo es público.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .config import raiz_repo

RUTA = "estado/recetas.jsonl"
CAMPOS_TOMA = ("numero", "gancho", "pilar", "fuente_id", "caption", "frase", "hablante", "campana", "duracion_s",
               "creador", "id_origen")


def ruta(raiz=None) -> Path:
    return raiz_repo(raiz) / RUTA


def receta_de(toma: dict, ficha: dict, plan: dict | None, candidato=None, fecha: date | str | None = None) -> dict:
    """La receta de una toma (dict serializable). `plan` es el de componer (lineas, origen, toma)."""
    plan = plan or {}
    origen = plan.get("origen") or {}
    rec = {k: toma.get(k) for k in CAMPOS_TOMA if toma.get(k) not in (None, "")}
    rec["fecha"] = fecha.isoformat() if isinstance(fecha, (date, datetime)) else (str(fecha) if fecha else None)
    rec["ficha"] = {"id": ficha.get("id"), "tipo": ficha.get("tipo"), "credito": ficha.get("credito"),
                    "creador": ficha.get("creador"), "nombre_visible": ficha.get("nombre_visible"),
                    "twitch_login": ficha.get("twitch_login")}
    if candidato is not None:
        rec["origen"] = {"tipo": getattr(candidato, "origen", None), "url": getattr(candidato, "url", None),
                         "titulo": getattr(candidato, "titulo", None),
                         "duracion_s": getattr(candidato, "duracion_s", None)}
    if origen:
        rec["tramo"] = {"inicio": origen.get("inicio"), "duracion": origen.get("duracion")}
        if origen.get("tramos"):
            rec["tramo"]["tramos"] = origen["tramos"]
    rec["lineas"] = [dict(ln) for ln in plan.get("lineas") or [] if isinstance(ln, dict)]
    rec["escena"] = (plan.get("toma") or {}).get("escena") or None
    if (plan.get("historia") or {}).get("proveedor"):
        rec["historia"] = True        # los tiempos de las líneas son los de la historia (apertura, zoom): no del tramo
    if plan.get("subtitulos") == "del clip":
        rec["subtitulos_del_clip"] = True
    return {k: v for k, v in rec.items() if v is not None}


def guardar(raiz, receta: dict) -> None:
    """Añade la receta (una por número de toma: si ya está, no se repite)."""
    if receta.get("numero") is not None and any(r.get("numero") == receta["numero"] for r in leer(raiz)):
        return
    p = ruta(raiz)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(receta, ensure_ascii=False, sort_keys=True) + "\n")


def leer(raiz=None) -> list[dict]:
    """Todas las recetas (las líneas que no se pueden leer se saltan)."""
    p = ruta(raiz)
    if not p.is_file():
        return []
    salida = []
    for linea in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(linea)
        except ValueError:
            continue
        if isinstance(d, dict):
            salida.append(d)
    return salida


def por_numero(raiz=None) -> dict[int, dict]:
    return {int(r["numero"]): r for r in leer(raiz) if isinstance(r.get("numero"), int)}

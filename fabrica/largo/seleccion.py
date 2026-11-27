"""Qué tomas entran al video largo: las que no salieron como Short en YouTube y siguen siendo publicables."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .. import recetas as mod_recetas
from .. import vetados
from ..config import cargar_yaml, raiz_repo
from ..pausa import ZONA

LIBRO = "estado/buffer-envios.json"
LARGOS = "estado/largos.json"


@dataclass
class Elegida:
    numero: int
    fecha: date
    ficha_id: str
    creador: str
    id_origen: str
    gancho: str = ""
    caption: str = ""
    hablante: str = ""
    duracion_s: float = 0.0
    vistas: int = 0
    receta: dict | None = None
    motivo_short: str = ""          # por qué no salió en Shorts: «cupo» o «rechazado»
    notas: list[str] = field(default_factory=list)


def config(raiz=None) -> dict:
    return cargar_yaml("config/largo.yml", raiz) or {}


def _leer_json(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def episodios(raiz=None) -> list[dict]:
    d = _leer_json(raiz_repo(raiz) / LARGOS, [])
    return d if isinstance(d, list) else []


def tomas_usadas(raiz=None) -> set[int]:
    """Tomas que ya salieron (o están subiendo) en un episodio anterior."""
    usadas = set()
    for e in episodios(raiz):
        if e.get("estado") in ("subido", "publicado", "subiendo"):
            usadas.update(int(n) for n in e.get("tomas") or [])
    return usadas


def _fecha_gt(due: str) -> date | None:
    try:
        return datetime.fromisoformat(str(due).replace("Z", "+00:00")).astimezone(ZONA).date()
    except ValueError:
        return None


def vistas_por_toma(raiz=None) -> dict[int, int]:
    """La mejor lectura de vistas (24 o 72 h) en TikTok o Reels de cada toma, del marcador."""
    ruta = raiz_repo(raiz) / "marcador.csv"
    salida: dict[int, int] = {}
    if not ruta.is_file():
        return salida
    with open(ruta, encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            if fila.get("plataforma") not in ("tiktok", "reels"):
                continue
            try:
                n = int(fila.get("toma") or 0)
            except ValueError:
                continue
            for col in ("vistas_72h", "vistas_24h", "vistas_12h"):
                try:
                    v = int(float(fila.get(col) or 0))
                except ValueError:
                    v = 0
                salida[n] = max(salida.get(n, 0), v)
    return salida


def candidatas(raiz=None, desde: date | None = None, hasta: date | None = None) -> tuple[list[Elegida], list[str]]:
    """(tomas que entran, motivos de las que no). Entra una toma si en el libro de envíos no tiene un Short creado,
    su ficha es de un tipo permitido, el creador no está vetado ni la fuente retirada, y no salió en otro episodio."""
    raiz = raiz_repo(raiz)
    cfg = config(raiz)
    tipos = {str(t).upper() for t in cfg.get("tipos_fichas") or ["T"]}
    libro = _leer_json(raiz / LIBRO, [])
    por_toma: dict[int, dict] = {}
    for e in libro if isinstance(libro, list) else []:
        try:
            n = int(e.get("toma"))
        except (TypeError, ValueError):
            continue
        t = por_toma.setdefault(n, {"plats": {}, "datos": None, "due": e.get("dueAt"), "ficha_id": None})
        t["plats"][str(e.get("plataforma"))] = str(e.get("estado"))
        datos = e.get("datos") or {}
        if datos.get("toma"):
            t["datos"] = datos["toma"]
            t["ficha_id"] = datos.get("ficha_id") or datos["toma"].get("fuente_id")
    recs = mod_recetas.por_numero(raiz)
    usadas = tomas_usadas(raiz)
    vetadas, retiradas = vetados.logins_vetados(raiz), vetados.fuentes_retiradas(raiz)
    vistas = vistas_por_toma(raiz)
    entran, fuera = [], []
    for n in sorted(por_toma):
        t = por_toma[n]
        f = _fecha_gt(t["due"])
        if f is None or (desde and f < desde) or (hasta and f > hasta):
            continue
        datos = t["datos"] or {}
        short = t["plats"].get("shorts")
        if short == "creado":
            continue                                    # salió (o está en cola) como Short
        if n in usadas:
            fuera.append(f"toma {n}: ya salió en un episodio")
            continue
        ficha_id = str(t["ficha_id"] or "")
        tipo = ficha_id[:2] == "t-" and "T" or (recs.get(n, {}).get("ficha", {}).get("tipo") or "").upper()
        if datos.get("campana"):
            fuera.append(f"toma {n}: es de campaña")
            continue
        if tipo not in tipos:
            fuera.append(f"toma {n}: ficha {ficha_id or '¿?'} de tipo {tipo or '¿?'} (entran {', '.join(sorted(tipos))})")
            continue
        login = str(datos.get("creador") or "").rsplit("/", 1)[-1].lower()
        if login and login in vetadas:
            fuera.append(f"toma {n}: {login} está vetado")
            continue
        if ficha_id in retiradas or str(datos.get("id_origen")) in retiradas:
            fuera.append(f"toma {n}: la fuente está retirada")
            continue
        if not datos.get("id_origen"):
            fuera.append(f"toma {n}: el libro no trae el id del clip")
            continue
        entran.append(Elegida(
            numero=n, fecha=f, ficha_id=ficha_id, creador=str(datos.get("creador") or ""),
            id_origen=str(datos["id_origen"]), gancho=str(datos.get("gancho") or ""),
            caption=str(datos.get("caption") or ""), hablante=str(datos.get("hablante") or ""),
            duracion_s=float(datos.get("duracion_s") or 0), vistas=vistas.get(n, 0), receta=recs.get(n),
            motivo_short="rechazado" if short else "cupo"))
    return entran, fuera


def ordenar(elegidas: list[Elegida], cfg: dict) -> list[Elegida]:
    """Cuenta atrás: de menos a más vistas (la mejor cierra). Sin vistas, por número de toma."""
    if cfg.get("orden") == "vistas" and any(e.vistas for e in elegidas):
        return sorted(elegidas, key=lambda e: (e.vistas, e.numero))
    return sorted(elegidas, key=lambda e: e.numero)


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)

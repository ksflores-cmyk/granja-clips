"""Puertas baratas (sin LLM) que filtran candidatos antes de descargar o editar."""
from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from .config import cargar_yaml, raiz_repo
from .detectar import Candidato, ClienteTwitch, ErrorTwitch, config_t
from .fuentes import cargar_fuentes, entrada_material, es_publicable
from .vetados import logins_vetados
from .registro import ZONA


def _ficha(raiz, fuente_id: str) -> dict | None:
    return next((f for f in cargar_fuentes(raiz) if f.get("id") == fuente_id), None)


def puerta_ficha(raiz, cand: Candidato, ahora: datetime, plataforma: str,
                 pilar: str | None = None) -> tuple[bool, str]:
    ficha = _ficha(raiz, cand.fuente_id)
    if ficha is None:
        return False, f"la fuente {cand.fuente_id} no está en fuentes.yml"
    return es_publicable(ficha, ahora.astimezone(ZONA).date(), plataforma, pilar)


def puerta_vetado(raiz, cand: Candidato) -> tuple[bool, str]:
    """Ficha T con el login vetado (config/vetados.yml o retiros.md): no pasa."""
    ficha = _ficha(raiz, cand.fuente_id)
    if ficha is not None and ficha.get("tipo") == "T":
        if str(ficha.get("twitch_login", "")).lower() in logins_vetados(raiz):
            return False, "streamer vetado"
    return True, "ok"


def puerta_cupo_streamer(raiz, cand: Candidato, ahora: datetime, plataforma: str,
                         en_corrida: int = 0) -> tuple[bool, str]:
    """Ficha T: máximo de clips por streamer al día en esa plataforma (marcador de hoy + esta corrida).

    El marcador fecha cada fila con el día del hueco, que de noche puede ser mañana: se cuentan las
    filas de hoy y de mañana (de más, nunca de menos)."""
    ficha = _ficha(raiz, cand.fuente_id)
    if ficha is None or ficha.get("tipo") != "T":
        return True, "ok"
    tope = int(config_t(raiz)["max_clips_por_streamer_dia"])
    d = ahora.astimezone(ZONA).date()
    dias = {d.isoformat(), (d + timedelta(days=1)).isoformat()}
    usados = sum(1 for f in _filas_marcador(raiz)
                 if f.get("fecha") in dias and f.get("plataforma") == plataforma
                 and f.get("fuente_id") == cand.fuente_id) + en_corrida
    if usados >= tope:
        return False, f"cupo del streamer lleno ({usados}/{tope})"
    return True, "ok"


def id_juego_temporada(raiz, cliente: ClienteTwitch | None = None) -> str | None:
    """Id del juego de la temporada. Puede lanzar ErrorTwitch si hay que resolverlo y Helix falla."""
    t = cargar_yaml("temporada.yml", raiz)
    if t.get("twitch_game_id"):
        return str(t["twitch_game_id"])
    if cliente is not None and t.get("nombre_twitch"):
        return cliente.juego_id(t["nombre_twitch"])
    return None


def puerta_juego(cand: Candidato, juego_esperado: str | None, error: str | None = None):
    if cand.origen == "brief":
        return True, "material del brief: se acepta"
    if error:
        return False, f"no se pudo resolver el id del juego: {error}"
    if juego_esperado is None:
        return False, "no se pudo resolver el id del juego de la temporada"
    if cand.juego_id != juego_esperado:
        return False, f"juego {cand.juego_id} distinto del de la temporada ({juego_esperado})"
    return True, "ok"


def puerta_duracion(raiz, cand: Candidato) -> tuple[bool, str]:
    if cand.duracion_s is None:
        if cand.origen == "brief":
            cand.medir_al_descargar = True   # N4 debe medirla al descargar y volver a comprobar
            return True, "duración desconocida (brief): se mide al descargar"
        return False, "duración desconocida"
    ficha = _ficha(raiz, cand.fuente_id) or {}
    minimo = ((ficha.get("reglas") or {}).get("duracion_s") or [0])[0]
    if cand.duracion_s < minimo:
        return False, f"dura {cand.duracion_s}s, menos del mínimo {minimo}s"
    return True, "ok"


def _vistos(raiz) -> set[str]:
    ruta = raiz_repo(raiz) / "estado" / "vistos.txt"
    if not ruta.exists():
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text("", encoding="utf-8")
        return set()
    return {l.strip() for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()}


def anotar_visto(raiz, id_origen: str) -> None:
    from .candado import candado
    _vistos(raiz)  # asegura que exista
    ruta = raiz_repo(raiz) / "estado" / "vistos.txt"
    with candado(ruta), open(ruta, "a", encoding="utf-8") as f:
        f.write(id_origen + "\n")


def quitar_visto(raiz, id_origen: str) -> None:
    """Deshace la ÚLTIMA `anotar_visto` de esa fuente (la reserva de la toma fallida) cuando el descarte fue de
    la máquina (Gemini caído), no de la fuente. Bajo el mismo candado que `anotar_visto`."""
    from .candado import candado
    ruta = raiz_repo(raiz) / "estado" / "vistos.txt"
    if not ruta.exists():
        return
    with candado(ruta):
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        for i in range(len(lineas) - 1, -1, -1):
            if lineas[i].strip() == id_origen:
                del lineas[i]
                ruta.write_text("".join(x + "\n" for x in lineas), encoding="utf-8")
                return


def _filas_marcador(raiz) -> list[dict]:
    with open(raiz_repo(raiz) / "marcador.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


RE_ORIGEN = re.compile(r"origen[:\s]+([^\s;,]+)")   # "origen:<id>" u "origen <id>"


def _id_de_clip_twitch(url: str) -> str | None:
    """Id de un clip de Twitch (clips.twitch.tv/<id> o twitch.tv/<canal>/clip/<id>); None si no lo es."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    partes = [x for x in u.path.split("/") if x]
    if host == "clips.twitch.tv" and len(partes) == 1:
        return partes[0]
    if host in ("twitch.tv", "www.twitch.tv", "m.twitch.tv") and len(partes) == 3 and partes[1] == "clip":
        return partes[2]
    return None


def ids_en_marcador(raiz) -> set[str]:
    """Ids de origen ya registrados en marcador.csv. Convención para N4 al escribir el marcador:
    poner el id de origen completo en `id_clip`, o la URL del clip en `url` (de Twitch se extrae el id; de otras plataformas se compara la URL
    completa), o `origen:<id>` u `origen <id>` en `notas`. Se compara el id entero, nunca por subcadena."""
    ids: set[str] = set()
    for f in _filas_marcador(raiz):
        if f.get("id_clip"):
            ids.add(f["id_clip"].strip())
        url = (f.get("url") or "").strip()
        if url:
            ids.add(url)
            clip = _id_de_clip_twitch(url)
            if clip:
                ids.add(clip)
        ids.update(RE_ORIGEN.findall(f.get("notas") or ""))
    return ids


def puerta_no_repetido(raiz, cand: Candidato) -> tuple[bool, str]:
    ids = [cand.id_origen]
    if cand.origen == "brief":     # el material pasó de Drive al release: su URL de Drive sigue siendo la misma fuente
        ficha = _ficha(raiz, cand.fuente_id)
        entrada = entrada_material(ficha, url=cand.url) if ficha else None
        if entrada and entrada.get("drive_url"):
            ids.append(entrada["drive_url"])
    vistos = _vistos(raiz)
    if any(i in vistos for i in ids):
        return False, "ya está en estado/vistos.txt"
    en_marcador = ids_en_marcador(raiz)
    if any(i and i in en_marcador for i in ids):
        return False, "ya está en el marcador"
    return True, "ok"


def cupo_del_dia(raiz, ahora: datetime, plataforma: str) -> int:
    t = cargar_yaml("temporada.yml", raiz)
    cupos = t["cupos"]
    v = t["ventana_estreno"]
    ini, fin = datetime.fromisoformat(str(v["inicio"])), datetime.fromisoformat(str(v["fin"]))
    if ini <= ahora <= fin:
        return int(cupos["estreno_max_por_dia_por_plataforma"])
    return int(cupos["por_dia_por_plataforma"])


def puerta_cupo(raiz, ahora: datetime, plataforma: str) -> tuple[bool, str]:
    hoy = ahora.astimezone(ZONA).date().isoformat()
    usados = sum(1 for f in _filas_marcador(raiz)
                 if f.get("fecha") == hoy and f.get("plataforma") == plataforma)
    cupo = cupo_del_dia(raiz, ahora, plataforma)
    if usados >= cupo:
        return False, f"cupo del día lleno en {plataforma} ({usados}/{cupo})"
    return True, "ok"


def filtrar(raiz, candidatos: list[Candidato], ahora: datetime, plataforma: str,
            cliente: ClienteTwitch | None = None, pilar: str | None = None):
    """Devuelve (aprobados, descartados[(candidato, motivo)]). Las puertas corren en orden.

    El pilar sale del candidato; si no trae, del argumento. Sin pilar, una ficha tipo D no pasa.
    El id del juego se resuelve al llegar al primer candidato de Twitch; si falla, sólo ésos se descartan.
    """
    aprobados: list[Candidato] = []
    descartados: list[tuple[Candidato, str]] = []
    aprobados_ids: set[str] = set()
    estado = {"hecho": False, "juego": None, "error": None}

    def juego():
        if not estado["hecho"]:
            estado["hecho"] = True
            try:
                estado["juego"] = id_juego_temporada(raiz, cliente)
            except ErrorTwitch as e:
                estado["error"] = str(e)
        return estado["juego"], estado["error"]

    cupo = cupo_del_dia(raiz, ahora, plataforma)
    hoy = ahora.astimezone(ZONA).date().isoformat()
    base = sum(1 for f in _filas_marcador(raiz)
               if f.get("fecha") == hoy and f.get("plataforma") == plataforma)
    for c in candidatos:
        pruebas = [
            lambda: puerta_ficha(raiz, c, ahora, plataforma, c.pilar or pilar),
            lambda: puerta_vetado(raiz, c),
            lambda: puerta_cupo_streamer(
                raiz, c, ahora, plataforma, sum(1 for a in aprobados if a.fuente_id == c.fuente_id)),
            # el id del juego se resuelve aquí, sólo si la ficha ya pasó la primera puerta
            lambda: puerta_juego(c, *(juego() if c.origen != "brief" else (None, None))),
            lambda: puerta_duracion(raiz, c),
            lambda: puerta_no_repetido(raiz, c),
            lambda: (False, "repetido en esta corrida") if c.id_origen in aprobados_ids else (True, "ok"),
            # Cada corrida publica una sola toma: los demás aprobados son suplentes, no otra publicación.
            lambda: (False, f"cupo del día lleno en {plataforma} ({base}/{cupo})")
            if base >= cupo else (True, "ok"),
        ]
        motivo = None
        for p in pruebas:
            ok, m = p()
            if not ok:
                motivo = m
                break
        if motivo is None:
            aprobados.append(c)
            aprobados_ids.add(c.id_origen)
        else:
            descartados.append((c, motivo))
    return aprobados, descartados

"""Detector de candidatos: clips recientes de Twitch (fuentes con twitch_login) y material de brief.

Sin dependencias nuevas: HTTP con urllib. El transporte es inyectable para las pruebas.
Nunca imprime ni guarda claves.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import re
from pathlib import Path

import yaml

from . import plazo
from .config import cargar_yaml, raiz_repo
from .fuentes import RUTA_FICHAS_T, cargar_fuentes, es_publicable
from .pausa import pausa_t_activa
from .vetados import equipos_vetados, logins_vetados

ZONA = ZoneInfo("America/Guatemala")
URL_TOKEN = "https://id.twitch.tv/oauth2/token"
URL_USUARIOS = "https://api.twitch.tv/helix/users"
URL_JUEGOS = "https://api.twitch.tv/helix/games"
URL_CLIPS = "https://api.twitch.tv/helix/clips"
URL_STREAMS = "https://api.twitch.tv/helix/streams"
URL_SEGUIDORES = "https://api.twitch.tv/helix/channels/followers"
URL_EQUIPOS = "https://api.twitch.tv/helix/teams"


class ErrorTwitch(RuntimeError):
    pass


def transporte_urllib(metodo: str, url: str, encabezados: dict, cuerpo: bytes | None = None):
    """Transporte real. Devuelve (estado_http, texto)."""
    req = urllib.request.Request(url, data=cuerpo, headers=encabezados, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=plazo.tiempo_para(20)) as r:  # noqa: S310 (URLs fijas https)
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Sin la query ni claves: sólo host, ruta y tipo de error.
        p = urllib.parse.urlparse(url)
        raise ErrorTwitch(f"fallo de conexión con {p.netloc}{p.path}: {type(e).__name__}") from None


class ClienteTwitch:
    def __init__(self, client_id: str, secret: str, transporte=None, espera=time.sleep,
                 segundos_reintento: float = 2.0):
        self._id = client_id
        self._secret = secret
        self._t = transporte or transporte_urllib
        self._espera = espera
        self._seg = segundos_reintento
        self._token: str | None = None

    def __repr__(self) -> str:  # nunca revela claves
        return "ClienteTwitch(<claves ocultas>)"

    def _pedir(self, metodo, url, encabezados, cuerpo=None) -> dict:
        for intento in (1, 2):
            estado, texto = self._t(metodo, url, encabezados, cuerpo)
            if (estado == 429 or estado >= 500) and intento == 1:
                self._espera(self._seg)
                continue
            if estado != 200:
                raise ErrorTwitch(f"Twitch respondió {estado} en {url.split('?')[0]}")
            try:
                return json.loads(texto)
            except ValueError as e:
                raise ErrorTwitch(f"respuesta no JSON de {url.split('?')[0]}") from e
        raise ErrorTwitch("sin respuesta")  # inalcanzable

    def token_aplicacion(self, client_id: str | None = None, secret: str | None = None) -> str:
        cuerpo = urllib.parse.urlencode({
            "client_id": client_id or self._id, "client_secret": secret or self._secret,
            "grant_type": "client_credentials"}).encode()
        d = self._pedir("POST", URL_TOKEN,
                        {"Content-Type": "application/x-www-form-urlencoded"}, cuerpo)
        self._token = d["access_token"]
        return self._token

    def _auth(self) -> dict:
        if not self._token:
            self.token_aplicacion()
        return {"Client-Id": self._id, "Authorization": f"Bearer {self._token}"}

    def _get(self, base: str, params: list[tuple[str, str]]) -> dict:
        return self._pedir("GET", f"{base}?{urllib.parse.urlencode(params)}", self._auth())

    def usuarios(self, logins: list[str]) -> dict[str, str]:
        """login (minúsculas) -> id de usuario."""
        if not logins:
            return {}
        d = self._get(URL_USUARIOS, [("login", l) for l in logins])
        return {u["login"].lower(): u["id"] for u in d.get("data", [])}

    def juego_id(self, nombre: str) -> str | None:
        d = self._get(URL_JUEGOS, [("name", nombre)])
        datos = d.get("data", [])
        return datos[0]["id"] if datos else None

    def clips(self, broadcaster_id: str, desde: datetime, hasta: datetime,
              primeros: int = 20) -> list[dict]:
        d = self._get(URL_CLIPS, [
            ("broadcaster_id", broadcaster_id),
            ("started_at", _rfc3339(desde)), ("ended_at", _rfc3339(hasta)),
            ("first", str(primeros))])
        return d.get("data", [])

    def streams(self, game_id: str, idioma: str, primeros: int = 20) -> list[dict]:
        d = self._get(URL_STREAMS, [("game_id", game_id), ("language", idioma), ("first", str(primeros))])
        return d.get("data", [])

    def clips_juego(self, game_id: str, desde: datetime, hasta: datetime, primeros: int = 100,
                    paginas: int = 3) -> list[dict]:
        """Clips de un juego, paginando con `pagination.cursor` -> `after`."""
        out: list[dict] = []
        cursor = None
        for _ in range(max(1, paginas)):
            params = [("game_id", game_id), ("started_at", _rfc3339(desde)),
                      ("ended_at", _rfc3339(hasta)), ("first", str(primeros))]
            if cursor:
                params.append(("after", cursor))
            d = self._get(URL_CLIPS, params)
            out.extend(d.get("data", []))
            cursor = (d.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return out

    def usuarios_por_id(self, ids: list[str]) -> dict[str, str]:
        """id de usuario -> login (minúsculas), en lotes de 100."""
        ids = list(dict.fromkeys(str(i) for i in ids))
        out: dict[str, str] = {}
        for i in range(0, len(ids), 100):
            d = self._get(URL_USUARIOS, [("id", x) for x in ids[i:i + 100]])
            out.update({u["id"]: u["login"].lower() for u in d.get("data", [])})
        return out

    def seguidores(self, broadcaster_id: str) -> int:
        d = self._get(URL_SEGUIDORES, [("broadcaster_id", broadcaster_id), ("first", "1")])
        if not isinstance(d, dict) or not isinstance(d.get("total"), int):
            raise ErrorTwitch(f"Twitch no devolvió el total de seguidores de {broadcaster_id}")
        return d["total"]

    def miembros_equipo(self, nombre: str) -> set[str]:
        d = self._get(URL_EQUIPOS, [("name", nombre)])
        datos = d.get("data") or []
        if not datos:
            raise ErrorTwitch(f"el equipo de Twitch {nombre!r} no existe o no se pudo leer")
        miembros = {str(u["user_login"]).lower() for u in datos[0].get("users") or [] if u.get("user_login")}
        if not miembros:   # un equipo vetado sin miembros legibles no se da por bueno (falla cerrada)
            raise ErrorTwitch(f"el equipo de Twitch {nombre!r} llegó sin miembros")
        return miembros


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parsear(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


@dataclass
class Candidato:
    fuente_id: str
    origen: str            # twitch | brief
    id_origen: str
    url: str
    titulo: str
    creador: str
    idioma: str
    juego_id: str | None
    duracion_s: float | None
    vistas: int
    vistas_por_hora: float
    creado_en: datetime | None
    vod_offset: int | None
    pilar: str | None = None            # None = sin pilar declarado; tipo D lo fija a "informativa"
    medir_al_descargar: bool = False    # True: la duración no se conoce; N4 debe medirla y volver a comprobarla


def cliente_desde_entorno(transporte=None) -> ClienteTwitch | None:
    cid = os.environ.get("TWITCH_CLIENT_ID", "")
    sec = os.environ.get("TWITCH_CLIENT_SECRET", "")
    if not cid or not sec:
        return None
    return ClienteTwitch(cid, sec, transporte)


def _de_clip(ficha: dict, c: dict, ahora: datetime) -> Candidato:
    creado = _parsear(c["created_at"])
    horas = max(1.0, (ahora - creado).total_seconds() / 3600)
    vistas = int(c.get("view_count", 0))
    return Candidato(
        fuente_id=ficha["id"], origen="twitch", id_origen=c["id"], url=c["url"],
        titulo=c.get("title", ""), creador=c.get("broadcaster_name", ""),
        idioma=c.get("language", ""), juego_id=c.get("game_id") or None,
        duracion_s=float(c.get("duration", 0)), vistas=vistas,
        vistas_por_hora=vistas / horas, creado_en=creado, vod_offset=c.get("vod_offset"))


# ------------------------------------------------------------------ buscador de la ficha T

CONFIG_T_DEFECTO = {
    "activo": False, "idioma": "es", "seguidores_min": 1000, "seguidores_max": 300000,
    "max_clips_por_streamer_dia": 2, "max_streams": 20, "paginas_clips_juego": 3, "max_streamers": 40,
    "horas": 24, "duracion_s": [10, 45], "plataformas": ["shorts", "tiktok", "reels"],
    "pilares": ["primeras_veces", "momentos"], "vigencia_dias": 2,
}


def config_t(raiz=None) -> dict:
    """config/buscador-t.yml sobre los valores por defecto. Sin archivo, el buscador queda apagado."""
    cfg = dict(CONFIG_T_DEFECTO)
    try:
        d = cargar_yaml("config/buscador-t.yml", raiz)
    except Exception:  # noqa: BLE001
        d = None
    if isinstance(d, dict):
        cfg.update({k: v for k, v in d.items() if k in CONFIG_T_DEFECTO})
    return cfg


def _ficha_t(login: str, bid: str, seguidores: int, juego: str, cfg: dict, hoy,
             hashtags: list | None = None) -> dict:
    return {
        "id": "t-" + login.replace("_", "-"), "tipo": "T", "creador": f"twitch.tv/{login}",
        "credito": f"twitch.tv/{login}", "origen": "twitch", "twitch_login": login,
        "broadcaster_id": str(bid), "seguidores": int(seguidores), "seguidores_leidos": hoy.isoformat(),
        "juego": juego,
        "reglas": {"duracion_s": list(cfg["duracion_s"]), "plataformas": list(cfg["plataformas"]),
                   "permite_descarga": True, "solo_clip": True,
                   # Las de la temporada (hasta 3): Instagram exige de 3 a 5 y Shorts/TikTok admiten 3.
                   "etiquetas_obligatorias": [str(h) for h in (hashtags or [])][:3],
                   "divulgacion": "ninguna", "audiencia": "sin restricción"},
        "pilares": list(cfg["pilares"]), "campana": None, "tarifa_usd_por_mil": None,
        "minimo_por_clip_usd": None, "tope_por_clip_usd": None, "presupuesto_restante": None,
        "vence": (hoy + timedelta(days=int(cfg["vigencia_dias"]))).isoformat(),
        "estado": "vigente", "uso": "produccion", "publicable": True,
        "notas": "Ficha T generada por el buscador: sin permiso, con crédito. Sólo el clip de Twitch.",
    }


def _clave_nombre(n: str) -> str:
    return re.sub(r"\s+", "", str(n or "")).lower()


def detectar_t(raiz, ahora: datetime, cliente: ClienteTwitch | None = None):
    """(candidatos, saltos, fichas). Ver `detectar_t_con_descartes` para el resumen de descartes."""
    return detectar_t_con_descartes(raiz, ahora, cliente)[:3]


def detectar_t_con_descartes(raiz, ahora: datetime, cliente: ClienteTwitch | None = None):
    """Busca clips del juego de la temporada de streamers medianos y pequeños en español.

    Devuelve (candidatos, saltos, fichas, descartes) donde descartes es {motivo: cantidad}
    (la clave `_clips_vistos` cuenta los clips mirados). Falla cerrada:
    si no se puede leer un equipo vetado o los seguidores de un streamer, no se usa."""
    cfg = config_t(raiz)
    saltos: list[str] = []
    descartes: dict[str, int] = {}

    def desc(motivo: str, n: int = 1):
        descartes[motivo] = descartes.get(motivo, 0) + n

    if not cfg["activo"]:
        return [], saltos, [], descartes
    en_pausa, motivo_pausa = pausa_t_activa(raiz)
    if en_pausa:
        return [], [f"ficha T en PAUSA: {motivo_pausa or 'sin motivo'}"], [], descartes
    if cliente is None:
        return [], ["ficha T: sin claves de Twitch, no corre"], [], descartes
    hoy = ahora.astimezone(ZONA).date()
    temporada = cargar_yaml("temporada.yml", raiz) or {}
    try:
        juego = str(temporada["twitch_game_id"]) if temporada.get("twitch_game_id") else None
        if juego is None and temporada.get("nombre_twitch"):
            juego = cliente.juego_id(temporada["nombre_twitch"])
    except ErrorTwitch as e:
        return [], [f"ficha T: no se resolvió el juego: {e}"], [], descartes
    if not juego:
        return [], ["ficha T: no se pudo resolver el id del juego de la temporada"], [], descartes
    nombre_juego = str(temporada.get("nombre") or temporada.get("nombre_twitch") or juego)

    vetados = set(logins_vetados(raiz))
    for equipo in equipos_vetados(raiz):
        try:
            vetados |= cliente.miembros_equipo(equipo)
        except ErrorTwitch as e:
            return [], [f"ficha T no corre: no se pudo leer el equipo vetado {equipo!r} ({e})"], [], descartes
    vetados_nombre = {_clave_nombre(v) for v in vetados}

    desde = ahora - timedelta(hours=int(cfg["horas"]))
    idioma = cfg["idioma"]
    try:
        crudos = cliente.clips_juego(juego, desde, ahora, 100, int(cfg["paginas_clips_juego"]))
    except ErrorTwitch as e:
        return [], [f"ficha T: clips del juego: {e}"], [], descartes
    vistos_n = len(crudos)
    clips: dict[str, dict] = {}
    for c in crudos:
        if c.get("language") != idioma:
            desc("idioma distinto")
        elif c.get("id") and c["id"] not in clips:
            clips[c["id"]] = c
    # Streams en vivo del juego en ese idioma: sus clips recientes, si no trajeron ninguno arriba.
    try:
        en_vivo = cliente.streams(juego, idioma, int(cfg["max_streams"]))
    except ErrorTwitch as e:
        en_vivo = []
        saltos.append(f"ficha T: streams en vivo: {e}")
    con_clips = {c.get("broadcaster_id") for c in clips.values()}
    for st in en_vivo:
        bid = st.get("user_id")
        if not bid or bid in con_clips:
            continue
        con_clips.add(bid)
        try:
            extra = cliente.clips(bid, desde, ahora)
        except ErrorTwitch as e:
            saltos.append(f"ficha T: clips de {st.get('user_login', bid)}: {e}")
            continue
        for c in extra:
            vistos_n += 1
            if c.get("game_id") != juego:
                desc("otro juego")
            elif c.get("language") and c.get("language") != idioma:
                desc("idioma distinto")
            elif c.get("id") and c["id"] not in clips:
                clips[c["id"]] = c

    por_bid: dict[str, list[dict]] = {}
    for c in clips.values():
        if c.get("broadcaster_id"):
            por_bid.setdefault(c["broadcaster_id"], []).append(c)
    if not por_bid:
        return [], saltos, [], _con_vistos(descartes, vistos_n)
    try:
        logins = cliente.usuarios_por_id(list(por_bid))
    except ErrorTwitch as e:
        return [], saltos + [f"ficha T: logins por id: {e}"], [], _con_vistos(descartes, vistos_n)

    orden = sorted(por_bid, key=lambda b: -max(int(c.get("view_count", 0)) for c in por_bid[b]))
    candidatos: list[Candidato] = []
    fichas: list[dict] = []
    consultados = 0
    for bid in orden:
        lista = por_bid[bid]
        login = logins.get(bid)
        if not login:
            desc("sin login", len(lista))
            continue
        nombre = lista[0].get("broadcaster_name", "")
        if login in vetados or _clave_nombre(nombre) in vetados_nombre:
            desc("streamer vetado", len(lista))
            continue
        if consultados >= int(cfg["max_streamers"]):
            desc("tope de streamers por corrida", len(lista))
            continue
        consultados += 1
        try:
            n_seg = cliente.seguidores(bid)
        except ErrorTwitch as e:
            saltos.append(f"ficha T: seguidores de {login}: {e}; streamer fuera")
            desc("seguidores ilegibles", len(lista))
            continue
        if n_seg < int(cfg["seguidores_min"]):
            desc("seguidores por debajo del mínimo", len(lista))
            continue
        if n_seg > int(cfg["seguidores_max"]):
            desc("seguidores por encima del máximo", len(lista))
            continue
        ficha = _ficha_t(login, bid, n_seg, nombre_juego, cfg, hoy, temporada.get("hashtags"))
        mejores = sorted(lista, key=lambda c: -int(c.get("view_count", 0)))[:int(cfg["max_clips_por_streamer_dia"])]
        desc("más de los permitidos por streamer", len(lista) - len(mejores))
        for c in mejores:
            cand = _de_clip(ficha, c, ahora)
            cand.creador = ficha["creador"]
            candidatos.append(cand)
        fichas.append(ficha)
    candidatos.sort(key=lambda x: -x.vistas)
    return candidatos, saltos, fichas, _con_vistos(descartes, vistos_n)


def _con_vistos(descartes: dict, vistos: int) -> dict:
    return {"_clips_vistos": vistos, **descartes}


def guardar_fichas_t(raiz, fichas: list[dict]) -> None:
    """Escribe o actualiza por id estado/fichas-t.yml. Generado: no editar a mano."""
    if not fichas:
        return
    ruta = raiz_repo(raiz) / RUTA_FICHAS_T
    actuales: dict[str, dict] = {}
    try:
        previo = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        for f in previo or []:
            if isinstance(f, dict) and f.get("id"):
                actuales[f["id"]] = f
    except Exception:  # noqa: BLE001
        pass
    for f in fichas:
        actuales[f["id"]] = f
    ruta.parent.mkdir(parents=True, exist_ok=True)
    cabecera = ("# Fichas T generadas por el buscador (fabrica/detectar.py). Generado: no editar a mano.\n"
                "# Un retiro en retiros.md o un login en config/vetados.yml las veta al cargarlas.\n")
    ruta.write_text(cabecera + yaml.safe_dump(list(actuales.values()), allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def detectar_con_motivos(raiz, ahora: datetime, cliente: ClienteTwitch | None = None):
    """Devuelve (candidatos, saltos) donde saltos es una lista de motivos legibles."""
    candidatos: list[Candidato] = []
    saltos: list[str] = []
    hoy = ahora.astimezone(ZONA).date()   # fecha local de Guatemala, igual que las puertas
    if cliente is None:
        cliente = cliente_desde_entorno()
    fichas = cargar_fuentes(raiz)
    for ficha in fichas:
        if ficha.get("tipo") == "T":
            continue          # las fichas T las alimenta sólo detectar_t (clips del juego, no por canal)
        pilar = "informativa" if ficha.get("tipo") == "D" else None
        ok, motivo = es_publicable(ficha, hoy, None, pilar)
        if not ok:
            continue
        if ficha.get("material"):
            for m in ficha["material"]:
                url = m.get("url") if isinstance(m, dict) else m
                creador = (m.get("creador") if isinstance(m, dict) else None) or ficha.get("creador", "")
                candidatos.append(Candidato(
                    fuente_id=ficha["id"], origen="brief", id_origen=url, url=url,
                    titulo=(m.get("archivo") if isinstance(m, dict) and m.get("archivo")
                            else url.rsplit("/", 1)[-1]), creador=str(creador),
                    idioma="", juego_id=None, duracion_s=None, vistas=0,
                    vistas_por_hora=0.0, creado_en=None, vod_offset=None,
                    pilar=pilar, medir_al_descargar=True))
        if ficha.get("origen") == "twitch" and ficha.get("twitch_login"):
            if cliente is None:
                saltos.append(f"{ficha['id']}: sin claves de Twitch, se salta")
                continue
            try:
                ids = cliente.usuarios([ficha["twitch_login"]])
                bid = ids.get(ficha["twitch_login"].lower())
                if bid is None:
                    saltos.append(f"{ficha['id']}: twitch_login no existe en Twitch")
                    continue
                crudos = cliente.clips(bid, ahora - timedelta(hours=24), ahora)
            except ErrorTwitch as e:
                saltos.append(f"{ficha['id']}: {e}")
                continue
            lista = [_de_clip(ficha, c, ahora) for c in crudos]
            for x in lista:
                x.pilar = pilar
            lista.sort(key=lambda x: x.vistas_por_hora, reverse=True)
            candidatos.extend(lista)
    # El material de una campaña pagada (tipo A con campaña) va primero; el resto, por vistas por hora.
    pagadas = {f["id"] for f in fichas if f.get("tipo") == "A" and f.get("campana") and f.get("material")}
    if cliente is not None:
        try:
            ct, st, ft = detectar_t(raiz, ahora, cliente)
        except plazo.SinTiempo:
            raise            # el plazo del ciclo no es un fallo del buscador: lo maneja el ciclo
        except Exception as e:  # noqa: BLE001 (la ficha T nunca tumba la detección normal)
            ct, st, ft = [], [f"ficha T: {type(e).__name__}" + (f": {e}" if isinstance(e, ErrorTwitch) else "")], []
        if ft:
            try:
                guardar_fichas_t(raiz, ft)
            except plazo.SinTiempo:
                raise
            except Exception as e:  # noqa: BLE001 (sin guardar no hay ficha: los candidatos T no pasan la puerta)
                saltos.append(f"ficha T: no se pudo guardar estado/fichas-t.yml ({type(e).__name__})")
                ct = []
        known = {c.id_origen for c in candidatos}
        candidatos.extend(c for c in ct if c.id_origen not in known)
        saltos.extend(st)
    candidatos.sort(key=lambda x: (x.fuente_id not in pagadas or x.origen != "brief", -x.vistas_por_hora))
    return candidatos, saltos


def detectar(raiz, ahora: datetime, cliente: ClienteTwitch | None = None) -> list[Candidato]:
    return detectar_con_motivos(raiz, ahora, cliente)[0]


def diagnostico_t(raiz=None, n: int = 10, cliente: ClienteTwitch | None = None, ahora: datetime | None = None,
                  salida=print) -> int:
    """Corre el buscador de la ficha T sin escribir nada en el repo. Código: 0 con candidatos, 1 sin claves o
    con error, 2 sin candidatos. Nunca imprime claves ni tokens."""
    if cliente is None:
        cliente = cliente_desde_entorno()
    if cliente is None:
        salida("diagnóstico T: faltan TWITCH_CLIENT_ID y TWITCH_CLIENT_SECRET")
        return 1
    ahora = ahora or datetime.now(timezone.utc)
    try:
        cfg = config_t(raiz)
        temporada = cargar_yaml("temporada.yml", raiz) or {}
        if not cfg["activo"]:
            salida("diagnóstico T: config/buscador-t.yml tiene activo: false; no se consulta nada")
        cands, saltos, fichas, descartes = detectar_t_con_descartes(raiz, ahora, cliente)
    except Exception as e:  # noqa: BLE001
        # ErrorTwitch ya viene sin claves ni query; de cualquier otro error sólo el tipo.
        salida(f"diagnóstico T: error {type(e).__name__}" + (f": {e}" if isinstance(e, ErrorTwitch) else ""))
        return 1
    salida(f"juego: {temporada.get('nombre_twitch') or temporada.get('nombre')} "
           f"(id {temporada.get('twitch_game_id', 'por resolver')}); idioma: {cfg['idioma']}")
    salida(f"clips vistos: {descartes.get('_clips_vistos', 0)}")
    for motivo, cuantos in descartes.items():
        if not motivo.startswith("_"):
            salida(f"descartes: {motivo}: {cuantos}")
    for s in saltos:
        salida(f"salto: {s}")
    salida(f"candidatos: {len(cands)} de {len(fichas)} streamers")
    seg = {f["id"]: f["seguidores"] for f in fichas}
    salida("login | seguidores | vistas | dur_s | título | url")
    for c in cands[:max(0, n)]:
        titulo = (c.titulo or "").replace("|", "/").replace("\n", " ")
        titulo = titulo if len(titulo) <= 40 else titulo[:37] + "..."
        salida(f"{c.creador.removeprefix('twitch.tv/')} | {seg.get(c.fuente_id, '?')} | {c.vistas} | "
               f"{c.duracion_s:g} | {titulo} | {c.url}")
    return 0 if cands else 2


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fabrica.detectar")
    ap.add_argument("--diagnostico-t", action="store_true", help="corre el buscador T sin escribir nada")
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args(argv)
    if a.diagnostico_t:
        return diagnostico_t(None, a.n)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

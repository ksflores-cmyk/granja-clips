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

from .fuentes import cargar_fuentes, es_publicable

ZONA = ZoneInfo("America/Guatemala")
URL_TOKEN = "https://id.twitch.tv/oauth2/token"
URL_USUARIOS = "https://api.twitch.tv/helix/users"
URL_JUEGOS = "https://api.twitch.tv/helix/games"
URL_CLIPS = "https://api.twitch.tv/helix/clips"


class ErrorTwitch(RuntimeError):
    pass


def transporte_urllib(metodo: str, url: str, encabezados: dict, cuerpo: bytes | None = None):
    """Transporte real. Devuelve (estado_http, texto)."""
    req = urllib.request.Request(url, data=cuerpo, headers=encabezados, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (URLs fijas https)
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


def detectar_con_motivos(raiz, ahora: datetime, cliente: ClienteTwitch | None = None):
    """Devuelve (candidatos, saltos) donde saltos es una lista de motivos legibles."""
    candidatos: list[Candidato] = []
    saltos: list[str] = []
    hoy = ahora.astimezone(ZONA).date()   # fecha local de Guatemala, igual que las puertas
    if cliente is None:
        cliente = cliente_desde_entorno()
    fichas = cargar_fuentes(raiz)
    for ficha in fichas:
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
    candidatos.sort(key=lambda x: x.vistas_por_hora, reverse=True)   # estable: el material del brief (0) va al final
    return candidatos, saltos


def detectar(raiz, ahora: datetime, cliente: ClienteTwitch | None = None) -> list[Candidato]:
    return detectar_con_motivos(raiz, ahora, cliente)[0]

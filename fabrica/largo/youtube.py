"""Subida del episodio con la API de YouTube (Data API v3), sin librerías: urllib y subida reanudable.

Secretos (GitHub → Settings → Secrets): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. Nunca se imprimen ni van en
una URL. La pantalla de permisos de Google tiene que estar «En producción»: en prueba el refresh token caduca a los 7
días. Ojo: la documentación vieja dice que los videos subidos desde proyectos sin auditar quedan privados; tras subir
se lee el estado real y se avisa si YouTube lo dejó bloqueado.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SECRETOS = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_SUBIDA = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
URL_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
URL_MINIATURA = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={id}"
URL_CANAL = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"
TROZO = 8 * 1024 * 1024


class ErrorYouTube(Exception):
    """Fallo de la API. El mensaje lleva código y razón, nunca el token."""


def faltan(env=None) -> list[str]:
    env = os.environ if env is None else env
    return [s for s in SECRETOS if not env.get(s)]


def _leer(r) -> dict:
    cuerpo = r.read().decode("utf-8") or "{}"
    try:
        return json.loads(cuerpo)
    except ValueError:
        return {}


def _razon(e: urllib.error.HTTPError) -> str:
    try:
        d = json.loads(e.read().decode("utf-8"))
        err = d.get("error")
        if isinstance(err, dict):
            razones = [x.get("reason") for x in err.get("errors") or [] if isinstance(x, dict)]
            return f"{e.code} {err.get('status') or ''} {','.join(r for r in razones if r)}".strip()
        return f"{e.code} {err or ''} {d.get('error_description') or ''}".strip()
    except Exception:  # noqa: BLE001
        return str(e.code)


class Cliente:
    def __init__(self, env=None, abrir=urllib.request.urlopen, dormir=time.sleep):
        env = os.environ if env is None else env
        if faltan(env):
            raise ErrorYouTube("faltan los secretos " + ", ".join(faltan(env)))
        self._cid, self._sec, self._ref = env["YT_CLIENT_ID"], env["YT_CLIENT_SECRET"], env["YT_REFRESH_TOKEN"]
        self.abrir, self.dormir = abrir, dormir
        self._token = None

    def token(self) -> str:
        if self._token:
            return self._token
        datos = urllib.parse.urlencode({"client_id": self._cid, "client_secret": self._sec,
                                        "refresh_token": self._ref, "grant_type": "refresh_token"}).encode()
        req = urllib.request.Request(URL_TOKEN, data=datos,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.abrir(req, timeout=30) as r:
                d = _leer(r)
        except urllib.error.HTTPError as e:
            raise ErrorYouTube(f"no se pudo renovar el token ({_razon(e)}): ¿la app sigue en prueba o se revocó?") \
                from None
        if not d.get("access_token"):
            raise ErrorYouTube("Google no devolvió token")
        self._token = d["access_token"]
        return self._token

    def _pedir(self, metodo: str, url: str, cuerpo: bytes | None = None, cabeceras: dict | None = None,
               esperados=(200,), tiempo: float = 60):
        cab = {"Authorization": f"Bearer {self.token()}", **(cabeceras or {})}
        req = urllib.request.Request(url, data=cuerpo, headers=cab, method=metodo)
        try:
            r = self.abrir(req, timeout=tiempo)
        except urllib.error.HTTPError as e:
            if e.code in esperados:
                return e
            raise ErrorYouTube(f"{metodo} {urllib.parse.urlparse(url).path}: {_razon(e)}") from None
        return r

    def canal(self) -> dict:
        with self._pedir("GET", URL_CANAL) as r:
            d = _leer(r)
        items = d.get("items") or []
        if not items:
            raise ErrorYouTube("la cuenta autorizada no tiene canal de YouTube")
        return {"id": items[0].get("id"), "titulo": (items[0].get("snippet") or {}).get("title")}

    def subir(self, mp4: Path, snippet: dict, status: dict, intentos: int = 5) -> str:
        """Subida reanudable en trozos de 8 MB. Devuelve el id del video."""
        mp4 = Path(mp4)
        total = mp4.stat().st_size
        meta = json.dumps({"snippet": snippet, "status": status}).encode("utf-8")
        with self._pedir("POST", URL_SUBIDA, meta, {"Content-Type": "application/json; charset=UTF-8",
                                                    "X-Upload-Content-Type": "video/mp4",
                                                    "X-Upload-Content-Length": str(total)}) as r:
            sesion = r.headers.get("Location")
        if not sesion:
            raise ErrorYouTube("YouTube no abrió la sesión de subida")
        enviado, fallos = 0, 0
        with open(mp4, "rb") as f:
            while enviado < total:
                f.seek(enviado)
                trozo = f.read(TROZO)
                fin = enviado + len(trozo) - 1
                try:
                    r = self._pedir("PUT", sesion, trozo, {"Content-Length": str(len(trozo)),
                                                           "Content-Range": f"bytes {enviado}-{fin}/{total}"},
                                    esperados=(308,), tiempo=300)
                except (ErrorYouTube, urllib.error.URLError, TimeoutError) as e:
                    fallos += 1
                    if fallos >= intentos:
                        raise ErrorYouTube(f"la subida se cortó {fallos} veces ({e})") from None
                    self.dormir(min(60, 2 ** fallos))
                    enviado = self._confirmado(sesion, total)
                    continue
                with r:
                    if getattr(r, "status", getattr(r, "code", 0)) in (200, 201):
                        d = _leer(r)
                        if d.get("id"):
                            return d["id"]
                        raise ErrorYouTube("la subida terminó sin id de video")
                    rango = r.headers.get("Range")
                enviado = int(rango.rsplit("-", 1)[1]) + 1 if rango else enviado + len(trozo)
        raise ErrorYouTube("la subida terminó sin respuesta final")

    def _confirmado(self, sesion: str, total: int) -> int:
        r = self._pedir("PUT", sesion, b"", {"Content-Length": "0", "Content-Range": f"bytes */{total}"},
                        esperados=(308,))
        with r:
            rango = r.headers.get("Range")
        return int(rango.rsplit("-", 1)[1]) + 1 if rango else 0

    def estado(self, video_id: str) -> dict:
        url = f"{URL_VIDEOS}?part=status,processingDetails&id={urllib.parse.quote(video_id)}"
        with self._pedir("GET", url) as r:
            items = _leer(r).get("items") or []
        return items[0] if items else {}

    def miniatura(self, video_id: str, png: Path) -> None:
        with self._pedir("POST", URL_MINIATURA.format(id=urllib.parse.quote(video_id)), Path(png).read_bytes(),
                         {"Content-Type": "image/png"}):
            pass


def snippet_de(titulo: str, descripcion: str, cfg_yt: dict) -> dict:
    return {"title": titulo[:100], "description": descripcion[:4900], "tags": list(cfg_yt.get("etiquetas") or [])[:15],
            "categoryId": str(cfg_yt.get("categoria", 20)), "defaultLanguage": cfg_yt.get("idioma", "es"),
            "defaultAudioLanguage": cfg_yt.get("idioma", "es")}


def status_de(cfg_yt: dict) -> dict:
    return {"privacyStatus": cfg_yt.get("privacidad", "public"), "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False, "embeddable": True}

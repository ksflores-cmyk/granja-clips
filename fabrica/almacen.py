"""Almacén temporal de videos en Supabase Storage (API REST, sólo urllib).

Secretos por variables de entorno: SUPABASE_URL y SUPABASE_SERVICE_KEY. Nunca se imprimen ni van en una URL.
La clave va igual en `apikey` y en `Authorization: Bearer`, así sirve una clave nueva (sb_secret_...)
o una heredada (service_role, un JWT).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from . import plazo, red
from .config import cargar_yaml
from .pausa import ZONA

TOPE_BYTES_PLAN = 52428800  # 50 MB: límite de archivo del plan gratis


def normalizar_url(url: str) -> str:
    """Base del proyecto (https://<ref>.supabase.co). El panel de Supabase muestra también la URL de la API
    REST (…/rest/v1/); si el secreto se copió así, se quita ese final para no pedir /rest/v1/storage/…"""
    base = str(url).strip().rstrip("/")
    for final in ("/rest/v1", "/storage/v1", "/auth/v1"):
        if base.lower().endswith(final):
            base = base[: -len(final)].rstrip("/")
    return base


class ErrorAlmacen(Exception):
    """Fallo del almacén. El mensaje dice ruta y código, nunca la clave."""


class Almacen:
    def __init__(self, url, clave, config, transporte=None, dormir=time.sleep):
        if not url or not clave:
            raise ErrorAlmacen("falta SUPABASE_URL o SUPABASE_SERVICE_KEY")
        self.url = normalizar_url(url)
        partes = urlparse(self.url)
        if partes.scheme != "https" or not (partes.hostname or "").endswith((".supabase.co", ".invalid")) \
                or partes.path not in ("", "/") or partes.username is not None \
                or any(c in self.url for c in "?#@"):
            # `.invalid` (RFC 2606) nunca resuelve: sólo existe para las pruebas.
            raise ErrorAlmacen("SUPABASE_URL no es la base https://<proyecto>.supabase.co; no se envía la clave")
        self._clave = clave
        self.bucket = config["bucket"]
        self.max_bytes = int(config["max_mb"]) * 1024 * 1024
        self.prefijo = str(config["prefijo"]).strip("/")
        self.transporte = transporte or red.transporte_urllib
        self.dormir = dormir

    @classmethod
    def desde_entorno(cls, raiz=None, env=None, transporte=None, dormir=time.sleep):
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_KEY", ""),
                   cargar_yaml("config/almacen.yml", raiz), transporte, dormir)

    # ---- bajo nivel ----

    def _cabeceras(self, extra=None):
        h = {"apikey": self._clave, "Authorization": f"Bearer {self._clave}"}
        h.update(extra or {})
        return h

    def _pedir(self, metodo, url, extra=None, cuerpo=None, esperados=(200,), anonimo=False, tiempo=60):
        """Petición con reintento único; si el código no es el esperado, ErrorAlmacen sin secretos.

        `anonimo`: sin `apikey` ni `Authorization` (la URL pública no necesita clave, y así se demuestra)."""
        cab = dict(extra or {}) if anonimo else self._cabeceras(extra)
        estado, cab, datos = red.solicitar(self.transporte, metodo, url, cab, cuerpo, tiempo=tiempo, dormir=self.dormir)
        if estado not in esperados:
            raise ErrorAlmacen(f"{metodo} {red.ruta_de(url)}: código {estado}")
        return estado, cab, datos

    def _json(self, cuerpo) -> bytes:
        return json.dumps(cuerpo).encode("utf-8")

    # ---- bucket ----

    def asegurar_bucket(self) -> bool:
        """Crea el bucket si no existe. Devuelve True si lo creó."""
        estado, _, _ = red.solicitar(self.transporte, "GET",
                                     f"{self.url}/storage/v1/bucket/{self.bucket}",
                                     self._cabeceras(), None, dormir=self.dormir)
        if estado == 200:
            return False
        # Supabase responde 400 o 404 cuando el bucket no existe.
        if estado not in (400, 404):
            raise ErrorAlmacen(f"GET /storage/v1/bucket/{self.bucket}: código {estado}")
        cuerpo = {"id": self.bucket, "name": self.bucket, "public": True,
                  "file_size_limit": TOPE_BYTES_PLAN, "allowed_mime_types": ["video/mp4"]}
        self._pedir("POST", f"{self.url}/storage/v1/bucket",
                    {"Content-Type": "application/json"}, self._json(cuerpo), esperados=(200, 201))
        return True

    # ---- archivos ----

    def ruta_objeto(self, mp4, toma, fecha=None) -> str:
        """tomas/AAAA-MM-DD/toma-NNN-<8 del sha256>.mp4, sin espacios."""
        sha = hashlib.sha256(Path(mp4).read_bytes()).hexdigest()[:8]
        dia = fecha or datetime.now(ZONA).date().isoformat()
        try:
            nnn = f"{int(toma):03d}"
        except (TypeError, ValueError):
            nnn = "".join(c for c in str(toma) if c.isalnum()) or "000"
        return f"{self.prefijo}/{dia}/toma-{nnn}-{sha}.mp4"

    def url_publica(self, ruta) -> str:
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{quote(ruta, safe='/')}"

    def subir(self, mp4, toma, fecha=None):
        """Sube el MP4 y devuelve (ruta, url_publica). Rechaza antes de subir lo que pesa de más."""
        mp4 = Path(mp4)
        peso = mp4.stat().st_size
        if peso > self.max_bytes:
            raise ErrorAlmacen(f"{mp4.name}: pesa {peso / 1048576:.1f} MB y el tope es "
                               f"{self.max_bytes // 1048576} MB; no se sube")
        ruta = self.ruta_objeto(mp4, toma, fecha)
        tiempo = min(300, max(60, peso / 1048576 * 4))     # la subida escala con el peso (recortada al plazo)
        self._pedir("POST", f"{self.url}/storage/v1/object/{self.bucket}/{quote(ruta, safe='/')}",
                    {"Content-Type": "video/mp4", "x-upsert": "true"}, mp4.read_bytes(),
                    esperados=(200, 201), tiempo=tiempo)
        return ruta, self.url_publica(ruta)

    def comprobar(self, url) -> None:
        """GET anónimo con Range: exige 200 o 206 y un Content-Type de video."""
        estado, cab, _ = self._pedir("GET", url, {"Range": "bytes=0-0"}, esperados=(200, 206),
                                     anonimo=True)
        tipo = str(cab.get("content-type", "")).lower()
        if not tipo.startswith("video/"):
            raise ErrorAlmacen(f"GET {red.ruta_de(url)}: el tipo es '{tipo or 'ninguno'}', no video")

    def confirmar_ausente(self, url, *, con_clave=False) -> None:
        """Tras borrar: sólo cuenta como ausente un 400 o 404 con el error de objeto no encontrado de
        Storage. Si el archivo aún responde, o es cualquier otro error (403, 503...), lanza ErrorAlmacen."""
        cab = self._cabeceras({"Range": "bytes=0-0"}) if con_clave else {"Range": "bytes=0-0"}
        estado, _, datos = red.solicitar(self.transporte, "GET", url, cab, None, dormir=self.dormir)
        if estado in (200, 206):
            raise ErrorAlmacen(f"GET {red.ruta_de(url)}: sigue respondiendo tras borrar")
        if estado in (400, 404) and _es_objeto_no_encontrado(datos):
            return
        raise ErrorAlmacen(f"GET {red.ruta_de(url)}: código {estado}; no se pudo confirmar que el "
                           "objeto desapareció")

    def confirmar_borrado(self, ruta) -> None:
        """Pregunta al API de Storage (con clave, sin la caché de la CDN) si el objeto sigue existiendo.
        La URL pública puede seguir sirviendo la copia de la CDN un rato después de borrar (pasó en el
        ensayo real del 18 nov), así que no sirve para confirmar el borrado."""
        self.confirmar_ausente(f"{self.url}/storage/v1/object/info/{self.bucket}/{quote(ruta, safe='/')}",
                               con_clave=True)

    def borrar(self, ruta) -> None:
        self._pedir("DELETE", f"{self.url}/storage/v1/object/{self.bucket}",
                    {"Content-Type": "application/json"}, self._json({"prefixes": [ruta]}),
                    esperados=(200, 204))

    # ---- ensayo ----

    def ida_y_vuelta(self) -> dict:
        """Ensayo: sube un MP4 de 1 s, comprueba la URL pública, lo borra y confirma en el API que ya no existe."""
        self.asegurar_bucket()
        with tempfile.TemporaryDirectory(prefix="almacen-") as d:
            mp4 = Path(d) / "prueba.mp4"
            crear_mp4_de_prueba(mp4)
            peso = mp4.stat().st_size
            ruta, url = self.subir(mp4, 0)
            try:
                self.comprobar(url)
            finally:
                self.borrar(ruta)
            self.confirmar_borrado(ruta)
        return {"subida": True, "url_comprobada": True, "borrado": True, "bytes": peso}


def _es_objeto_no_encontrado(datos: bytes) -> bool:
    """Cuerpo JSON de Storage que dice que el OBJETO no existe (no un HTML, ni 'Bucket not found')."""
    try:
        cuerpo = json.loads(datos.decode("utf-8"))
    except ValueError:
        return False
    if not isinstance(cuerpo, dict):
        return False
    error, mensaje = cuerpo.get("error", ""), cuerpo.get("message", "")
    if not (isinstance(error, str) and isinstance(mensaje, str)):
        return False
    texto = f"{error} {mensaje}".lower()
    # Indicación explícita de OBJETO: "Route not found" o "Bucket not found" no valen.
    return "bucket" not in texto and ("object not found" in mensaje.lower() or "object not found" in error.lower())


def crear_mp4_vertical_de_prueba(destino, segundos: int = 5) -> Path:
    """MP4 1080x1920 con audio mudo, como los que publica la fábrica, para el borrador de Buffer
    (Buffer descarga el video y rechaza lo que no puede leer). Sin red."""
    destino = Path(destino)
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=duration={segundos}:size=1080x1920:rate=30",
           "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
           "-t", str(segundos), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
           "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(destino)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=plazo.tiempo_para(120))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ErrorAlmacen(f"ffmpeg no pudo crear el MP4 vertical de prueba ({type(e).__name__})") from None
    if r.returncode != 0 or not destino.exists():
        raise ErrorAlmacen("ffmpeg no pudo crear el MP4 vertical de prueba")
    return destino


def crear_mp4_de_prueba(destino) -> Path:
    """MP4 de 1 s con ffmpeg (fuente testsrc). Sin red."""
    destino = Path(destino)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
           "testsrc=duration=1:size=320x240:rate=15", "-pix_fmt", "yuv420p", "-c:v", "libx264",
           "-movflags", "+faststart", str(destino)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=plazo.tiempo_para(60))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ErrorAlmacen(f"ffmpeg no pudo crear el MP4 de prueba ({type(e).__name__})") from None
    if r.returncode != 0 or not destino.exists():
        raise ErrorAlmacen("ffmpeg no pudo crear el MP4 de prueba")
    return destino

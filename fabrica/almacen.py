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
from urllib.parse import quote

from . import red
from .config import cargar_yaml
from .pausa import ZONA

TOPE_BYTES_PLAN = 52428800  # 50 MB: límite de archivo del plan gratis


class ErrorAlmacen(Exception):
    """Fallo del almacén. El mensaje dice ruta y código, nunca la clave."""


class Almacen:
    def __init__(self, url, clave, config, transporte=None, dormir=time.sleep):
        if not url or not clave:
            raise ErrorAlmacen("falta SUPABASE_URL o SUPABASE_SERVICE_KEY")
        self.url = url.rstrip("/")
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

    def _pedir(self, metodo, url, extra=None, cuerpo=None, esperados=(200,)):
        """Petición con reintento único; si el código no es el esperado, ErrorAlmacen sin secretos."""
        estado, cab, datos = red.solicitar(self.transporte, metodo, url, self._cabeceras(extra),
                                           cuerpo, dormir=self.dormir)
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
        self._pedir("POST", f"{self.url}/storage/v1/object/{self.bucket}/{quote(ruta, safe='/')}",
                    {"Content-Type": "video/mp4", "x-upsert": "true"}, mp4.read_bytes(),
                    esperados=(200, 201))
        return ruta, self.url_publica(ruta)

    def comprobar(self, url) -> None:
        """GET con Range: exige 200 o 206 y un Content-Type de video."""
        estado, cab, _ = self._pedir("GET", url, {"Range": "bytes=0-0"}, esperados=(200, 206))
        tipo = str(cab.get("content-type", "")).lower()
        if not tipo.startswith("video/"):
            raise ErrorAlmacen(f"GET {red.ruta_de(url)}: el tipo es '{tipo or 'ninguno'}', no video")

    def responde(self, url) -> bool:
        """True si la URL pública aún entrega el archivo (200 o 206)."""
        estado, _, _ = red.solicitar(self.transporte, "GET", url, self._cabeceras({"Range": "bytes=0-0"}),
                                     None, dormir=self.dormir)
        return estado in (200, 206)

    def borrar(self, ruta) -> None:
        self._pedir("DELETE", f"{self.url}/storage/v1/object/{self.bucket}",
                    {"Content-Type": "application/json"}, self._json({"prefixes": [ruta]}),
                    esperados=(200, 204))

    # ---- ensayo ----

    def ida_y_vuelta(self) -> dict:
        """Ensayo: sube un MP4 de 1 s, comprueba la URL pública, lo borra y comprueba que ya no responde."""
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
            if self.responde(url):
                raise ErrorAlmacen(f"GET {red.ruta_de(url)}: sigue respondiendo tras borrar")
        return {"subida": True, "url_comprobada": True, "borrado": True, "bytes": peso}


def crear_mp4_de_prueba(destino) -> Path:
    """MP4 de 1 s con ffmpeg (fuente testsrc). Sin red."""
    destino = Path(destino)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
           "testsrc=duration=1:size=320x240:rate=15", "-pix_fmt", "yuv420p", "-c:v", "libx264",
           "-movflags", "+faststart", str(destino)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ErrorAlmacen(f"ffmpeg no pudo crear el MP4 de prueba ({type(e).__name__})") from None
    if r.returncode != 0 or not destino.exists():
        raise ErrorAlmacen("ffmpeg no pudo crear el MP4 de prueba")
    return destino

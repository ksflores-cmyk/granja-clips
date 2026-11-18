"""Transporte HTTP mínimo (urllib) con reintento único. Sin SDK, sin dependencias.

Un transporte es una función `(metodo, url, encabezados, cuerpo, tiempo) -> (estado, encabezados, bytes)`.
Los encabezados de respuesta vuelven con los nombres en minúsculas. Las pruebas usan un transporte falso.
Los mensajes de error dicen método, ruta y código; nunca la URL con parámetros, ni encabezados, ni claves.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

REINTENTABLES = {429, 500, 502, 503, 504}


class ErrorRed(Exception):
    """Fallo de red o respuesta con un código que no se esperaba."""


def ruta_de(url: str) -> str:
    """Sólo la ruta de la URL (sin servidor, sin parámetros)."""
    return urlsplit(url).path or "/"


def ocultar(texto: str, secretos) -> str:
    """Sustituye cualquier secreto que haya llegado a un texto."""
    for s in secretos:
        if s:
            texto = texto.replace(s, "***")
    return texto


def transporte_urllib(metodo, url, encabezados, cuerpo=None, tiempo=60):
    """Transporte real. Los códigos 4xx y 5xx se devuelven como respuesta, no como excepción."""
    if not url.startswith("https://"):
        raise ErrorRed(f"{metodo} {ruta_de(url)}: sólo se admite https")
    peticion = urllib.request.Request(url, data=cuerpo, method=metodo, headers=dict(encabezados))
    try:
        with urllib.request.urlopen(peticion, timeout=tiempo) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        try:
            datos = e.read()
        except OSError:
            datos = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, datos
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ErrorRed(f"{metodo} {ruta_de(url)}: sin respuesta ({type(e).__name__})") from None


def solicitar(transporte, metodo, url, encabezados, cuerpo=None, tiempo=60, dormir=time.sleep,
              espera_s=2.0, alerta=None):
    """Una petición con un solo reintento ante 429 o 5xx.

    `alerta` (opcional) se llama antes de cada intento: sirve para contar la cuota.
    Respeta `retry-after` (hasta 30 s). Devuelve (estado, encabezados, bytes).
    """
    for intento in (1, 2):
        if alerta:
            alerta()
        estado, cabeceras, datos = transporte(metodo, url, encabezados, cuerpo, tiempo)
        if estado in REINTENTABLES and intento == 1:
            try:
                espera = min(float(cabeceras.get("retry-after", espera_s)), 30.0)
            except (TypeError, ValueError):
                espera = espera_s
            dormir(espera)
            continue
        return estado, cabeceras, datos
    raise AssertionError("inalcanzable")  # pragma: no cover

"""Marcador, registro y contador de tomas."""
from __future__ import annotations

import csv
import fcntl
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import raiz_repo

ZONA = ZoneInfo("America/Guatemala")

CABECERA_MARCADOR = [
    "id_clip", "toma", "fecha", "hora", "plataforma", "pilar", "fuente_id", "campana",
    "gancho", "duracion_s", "credito", "url", "vistas_12h", "vistas_24h", "vistas_72h",
    "likes", "compartidos", "comentarios", "guardados", "seguidores", "prueba",
    "estado_campana", "motivo_rechazo", "pago_usd", "notas",
]

CABECERA_ENVIOS = ["fecha", "toma", "plataforma", "url", "campana", "estado", "notas"]


def anotar_fila(raiz, fila: dict) -> None:
    """Añade una fila a marcador.csv. Rechaza claves desconocidas; lo ausente queda vacío."""
    desconocidas = sorted(set(fila) - set(CABECERA_MARCADOR))
    if desconocidas:
        raise ValueError(f"Columnas desconocidas en el marcador: {', '.join(desconocidas)}")
    ruta = raiz_repo(raiz) / "marcador.csv"
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["" if fila.get(c) is None else fila.get(c) for c in CABECERA_MARCADOR])


def anotar_registro(raiz, texto: str) -> str:
    """Añade una línea con fecha y hora de Guatemala a registro.md."""
    sello = datetime.now(ZONA).strftime("%Y-%m-%d %H:%M")
    linea = f"- {sello} GT · {' '.join(str(texto).split())}"
    ruta = raiz_repo(raiz) / "registro.md"
    # Modo anexado: cada línea se escribe de una vez al final; dos corridas no se pisan.
    with open(ruta, "a+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            tam = f.seek(0, os.SEEK_END)
            prefijo = b""
            if tam:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    prefijo = b"\n"
            f.write(prefijo + (linea + "\n").encode("utf-8"))
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return linea


_LINEA_CONTADOR = re.compile(r"^(\s+contador:\s*)(\d+)(.*)$")


def _localizar_contador(lineas: list[str]) -> int:
    lineas = [l.rstrip("\r") for l in lineas]
    """Índice de la línea `contador:` dentro del bloque `toma:`."""
    dentro = False
    for i, linea in enumerate(lineas):
        if re.match(r"^toma:\s*$", linea):
            dentro = True
            continue
        if dentro:
            if linea.strip() and not linea.startswith((" ", "\t")):
                break
            if _LINEA_CONTADOR.match(linea):
                return i
    raise ValueError("No se encontró toma.contador en marca.yml")


def contador_toma(raiz=None) -> int:
    lineas = (raiz_repo(raiz) / "marca.yml").read_text(encoding="utf-8").splitlines()
    return int(_LINEA_CONTADOR.match(lineas[_localizar_contador(lineas)]).group(2))


@contextmanager
def _candado(ruta: Path):
    """Candado exclusivo entre procesos (fcntl; el runner es Linux) sobre un archivo aparte."""
    with open(ruta.with_name(ruta.name + ".lock"), "a+b") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def avanzar_toma(raiz=None) -> int:
    """Sube toma.contador en 1 cambiando sólo esa línea. Devuelve el nuevo valor.

    Lee, incrementa y sustituye bajo un candado de archivo; la sustitución es atómica (os.replace).
    """
    ruta = raiz_repo(raiz) / "marca.yml"
    with _candado(ruta):
        # newline="" conserva los fines de línea originales (LF o CRLF).
        with open(ruta, encoding="utf-8", newline="") as f:
            texto = f.read()
        lineas = texto.split("\n")
        i = _localizar_contador(lineas)
        cr = "\r" if lineas[i].endswith("\r") else ""
        m = _LINEA_CONTADOR.match(lineas[i][: len(lineas[i]) - len(cr)])
        nuevo = int(m.group(2)) + 1
        lineas[i] = f"{m.group(1)}{nuevo}{m.group(3)}{cr}"
        fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".marca-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write("\n".join(lineas))
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp, os.stat(ruta).st_mode & 0o777)
            except OSError:
                pass
            os.replace(tmp, ruta)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return nuevo

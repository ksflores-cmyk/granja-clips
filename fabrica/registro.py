"""Marcador, registro y contador de tomas."""
from __future__ import annotations

import csv
import os
import re
import tempfile
import time
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
    "estado_campana", "motivo_rechazo", "pago_usd", "notas", "historia", "visto_medio_s",
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


def anotar_clip(raiz, candidato, fila: dict | None = None) -> dict:
    """Escribe la fila del marcador de un clip y lo anota como visto, en el mismo paso.

    El id de origen va en `id_clip` y como `origen:<id>` en `notas`, que son los lugares donde
    `puertas.ids_en_marcador` lo busca; además `puertas.anotar_visto` lo apunta en estado/vistos.txt.
    Así las dos defensas contra repetidos no se pueden olvidar. Devuelve la fila persistida (la ya guardada si era un reintento).
    Si la fila es inválida (columnas desconocidas) no se anota nada. Es idempotente: si ya hay una fila con ese id
    y plataforma no escribe otra, y anota el visto igualmente (reintento tras un fallo entre las dos escrituras).
    """
    from . import puertas   # import tardío: puertas importa este módulo
    if fila is None:
        # Forma corta `anotar_clip(raiz, fila)` (la usa publicar.py): el id sale de la propia fila; sin id,
        # sólo se escribe la fila, como antes de existir esta función.
        fila = dict(candidato)
        if not str(fila.get("id_clip") or "").strip():
            anotar_fila(raiz, fila)
            return fila
        id_origen = str(fila["id_clip"]).strip()
    else:
        id_origen = str(candidato.id_origen).strip()
        if not id_origen:
            raise ValueError("el candidato no tiene id_origen")
        fila = dict(fila)
    previas = str(fila.get("notas") or "").strip()
    fila["id_clip"] = id_origen
    fila["notas"] = f"origen:{id_origen}" + (f"; {previas}" if previas else "")
    plataforma = str(fila.get("plataforma") or "")
    with _candado(raiz_repo(raiz) / "marcador.csv"):      # búsqueda y alta bajo el mismo candado
        previa = next((f for f in puertas._filas_marcador(raiz)
                       if (f.get("id_clip") or "").strip() == id_origen and (f.get("plataforma") or "") == plataforma),
                      None)
        if previa is None:              # idempotente: un reintento tras un fallo no duplica la fila
            anotar_fila(raiz, fila)
    puertas.anotar_visto(raiz, id_origen)
    return dict(previa) if previa is not None else fila


def _bloquear(f) -> None:
    """Candado exclusivo sobre el archivo abierto: fcntl en POSIX, msvcrt.locking en Windows."""
    try:
        import fcntl
    except ImportError:
        import msvcrt
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.02)
    fcntl.flock(f, fcntl.LOCK_EX)


def _desbloquear(f) -> None:
    try:
        import fcntl
    except ImportError:
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(f, fcntl.LOCK_UN)


def anotar_registro(raiz, texto: str) -> str:
    """Añade una línea con fecha y hora de Guatemala a registro.md."""
    sello = datetime.now(ZONA).strftime("%Y-%m-%d %H:%M")
    linea = f"- {sello} GT · {' '.join(str(texto).split())}"
    ruta = raiz_repo(raiz) / "registro.md"
    # Modo anexado bajo el candado: cada línea se escribe de una vez al final.
    with _candado(ruta), open(ruta, "a+b") as f:
        tam = f.seek(0, os.SEEK_END)
        prefijo = b""
        if tam:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefijo = b"\n"
        f.seek(0, os.SEEK_END)
        f.write(prefijo + (linea + "\n").encode("utf-8"))
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
    """Candado exclusivo entre procesos (fcntl en POSIX, msvcrt en Windows) sobre un archivo aparte."""
    with open(ruta.with_name(ruta.name + ".lock"), "a+b") as lk:
        _bloquear(lk)
        try:
            yield
        finally:
            _desbloquear(lk)


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

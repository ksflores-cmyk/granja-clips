"""Candado exclusivo de archivo entre procesos: fcntl en POSIX, msvcrt.locking en Windows.

Mismo patrón perezoso que `registro.py` (la importación de fcntl o msvcrt se hace al usarlo, para que el
módulo cargue en cualquier sistema). El candado vive en un archivo `.lock` aparte.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path


def _bloquear(f) -> None:
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


@contextmanager
def candado(ruta: Path):
    """Candado exclusivo sobre `<ruta>.lock`. No es reentrante entre descriptores: no lo anides."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta.with_name(ruta.name + ".lock"), "a+b") as lk:
        _bloquear(lk)
        try:
            yield
        finally:
            _desbloquear(lk)

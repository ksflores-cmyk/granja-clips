"""Plazo del ciclo: nada de lo que hace la fábrica puede colgarse sin límite.

Tres capas, de la más suave a la más dura:
1. Cooperativa: cada llamada externa pide su tiempo con `tiempo_para(tope)`, que lo recorta a lo que queda
   del ciclo (y lanza `SinTiempo` ANTES de empezar si ya no cabe: nada queda a medias).
2. Alarma dura: `alarma(s)` lanza `SinTiempo` desde una señal (SIGALRM) aunque el código esté bloqueado
   en una lectura. `subprocess.run` mata al hijo (ffmpeg, yt-dlp) cuando le sube una excepción.
3. El paso del workflow tiene `timeout-minutes` (ver .github/workflows/fabrica.yml).

Sin `iniciar` no hay plazo: `tiempo_para` devuelve el tope tal cual y las pruebas viejas no cambian.
Las dos cuentas (cooperativa y alarma) pueden contar desde el inicio del job si se da `inicio_epoch`.
"""
from __future__ import annotations

import signal
import threading
import time
from contextlib import contextmanager

LIMITE_CICLO_S = 13 * 60     # plazo cooperativo
ALARMA_S = 14 * 60           # plazo duro (SIGALRM)
MARGEN_S = 5                 # lo que se deja libre al final para cerrar


class SinTiempo(Exception):
    """Se acabó el plazo del ciclo (o no cabe la llamada que se iba a hacer)."""


_estado = {"inicio": None, "limite": None}
_critica = {"nivel": 0, "pendiente": False, "alarma": False}
_salida = [lambda texto: print(texto, flush=True)]


def _ya_pasado(inicio_epoch) -> float:
    if inicio_epoch is None:
        return 0.0
    return max(0.0, time.time() - float(inicio_epoch))


def iniciar(limite_s, *, inicio_epoch=None) -> None:
    """Empieza a contar. `limite_s=None` deja el ciclo sin plazo."""
    if limite_s is None:
        detener()
        return
    _estado["inicio"] = time.monotonic() - _ya_pasado(inicio_epoch)
    _estado["limite"] = float(limite_s)


def detener() -> None:
    _estado["inicio"] = None
    _estado["limite"] = None


def activo() -> bool:
    return _estado["inicio"] is not None


def transcurrido() -> float:
    """Segundos desde el inicio del plazo (0 si no hay plazo)."""
    if not activo():
        return 0.0
    return time.monotonic() - _estado["inicio"]


def restante() -> float:
    if not activo():
        return float("inf")
    return _estado["limite"] - transcurrido()


def queda(segundos) -> bool:
    return restante() >= float(segundos)


def tiempo_para(tope_s, minimo=MARGEN_S) -> float:
    """El tiempo que puede usar una llamada: su tope, recortado a lo que queda del ciclo (menos el margen).

    Lanza `SinTiempo` si lo que queda es menor que `minimo`."""
    if not activo():
        return tope_s
    libre = restante() - MARGEN_S
    if libre < minimo:
        raise SinTiempo(f"quedan {max(libre, 0):.0f} s del plazo del ciclo y la llamada necesita al menos {minimo:g} s")
    return min(tope_s, libre)


@contextmanager
def alarma(segundos):
    """Alarma dura: lanza `SinTiempo` pasados `segundos`, aunque el código esté bloqueado. Sin SIGALRM o fuera
    del hilo principal no hace nada (queda el plazo cooperativo)."""
    posible = (segundos is not None and hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")
               and threading.current_thread() is threading.main_thread())
    if not posible:
        yield
        return

    def _al_sonar(_signum, _frame):
        if _critica["nivel"] > 0:          # sección crítica: se difiere hasta que termine
            _critica["pendiente"] = True
            return
        raise SinTiempo(f"alarma dura: pasaron {segundos:g} s")

    anterior = signal.signal(signal.SIGALRM, _al_sonar)
    _critica.update(pendiente=False, alarma=True)
    signal.setitimer(signal.ITIMER_REAL, max(float(segundos), 0.001))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, anterior)
        _critica.update(pendiente=False, alarma=False)


@contextmanager
def critico():
    """Sección crítica: difiere la alarma dura. Si suena dentro, sólo se marca; al salir del bloque más externo
    se lanza `SinTiempo`. Anidable. Sin alarma activa no hace nada."""
    _critica["nivel"] += 1
    try:
        yield
    except BaseException:
        _critica["nivel"] -= 1
        if _critica["nivel"] == 0:
            _critica["pendiente"] = False      # ya sube otra excepción: no se pisa
        raise
    else:
        _critica["nivel"] -= 1
        if _critica["nivel"] == 0 and _critica["pendiente"]:
            _critica["pendiente"] = False
            raise SinTiempo("alarma dura (diferida tras una sección crítica)")


def registrar(out=None) -> None:
    """Fija la función de salida del log (por defecto, print con flush)."""
    _salida[0] = out if out is not None else (lambda texto: print(texto, flush=True))


def log(texto) -> None:
    """Una línea al log del ciclo (misma salida que las etapas)."""
    _salida[0](str(texto))


def _decimal(x) -> str:
    return f"{x:.1f}"


@contextmanager
def etapa(nombre):
    """Log de inicio y fin de una etapa con segundos. Nunca se traga la excepción."""
    t0 = time.monotonic()
    _salida[0](f"[etapa] {nombre}: inicio (ciclo {_decimal(transcurrido())} s)")
    try:
        yield
    except BaseException as e:
        _salida[0](f"[etapa] {nombre}: fin en {_decimal(time.monotonic() - t0)} s (error: {type(e).__name__})")
        raise
    else:
        _salida[0](f"[etapa] {nombre}: fin en {_decimal(time.monotonic() - t0)} s (ok)")

"""Encuadre de una toma con video: un recorte fijo que cubre el cuadro vertical (sin zoom ni paneo).

La fuente se escala para cubrir el cuadro de salida (escala = max(ancho_salida / ancho, alto_salida / alto)) y se
recorta a esa proporción. Si sobra ancho (fuente apaisada) o alto (fuente más angosta que 9:16), la ventana se
coloca donde hay más acción: unos pocos fotogramas en gris, a baja resolución, dentro del recorte de tiempo; se suma
por columna (o por fila) el movimiento entre fotogramas seguidos más algo de bordes, y se elige la ventana de mayor
energía. Si no supera a la centrada por el margen de `marca.yml` o si falla la lectura, se queda centrada.
Determinista; sólo numpy y ffmpeg.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

# Valores por defecto si no se pasan los de marca.yml (`pantalla_completa.recorte`); la fábrica los pasa siempre.
FOTOGRAMAS = 12
ANCHO_ANALISIS = 192
MARGEN_ACCION = 0.15
PESO_BORDES = 0.2


def escala_cubrir(ancho_v: int, alto_v: int, ancho_salida: int, alto_salida: int) -> float:
    if ancho_v <= 0 or alto_v <= 0:
        raise ValueError(f"fuente con tamaño inválido {ancho_v}x{alto_v}")
    return max(ancho_salida / ancho_v, alto_salida / alto_v)


def _ventana(ancho_v: int, alto_v: int, ancho_salida: int, alto_salida: int) -> tuple[float, int, int]:
    """(escala, ancho, alto) de la ventana de la fuente que, escalada, llena el cuadro de salida."""
    escala = escala_cubrir(ancho_v, alto_v, ancho_salida, alto_salida)
    vw = max(2, min(ancho_v, int(round(ancho_salida / escala))))
    vh = max(2, min(alto_v, int(round(alto_salida / escala))))
    return escala, vw - vw % 2, vh - vh % 2          # pares: el filtro crop de ffmpeg redondea a par en yuv420


def _fotogramas_grises(ruta: Path, inicio: float, duracion: float, ancho_v: int, alto_v: int,
                       n: int, ancho_an: int) -> np.ndarray | None:
    """Pila (n, alto, ancho) de fotogramas en gris, parejos en el tiempo, o None si no se pudo leer."""
    alto_an = max(2, int(round(ancho_an * alto_v / ancho_v)))
    fps = n / max(duracion, 0.1)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{inicio:g}", "-t", f"{duracion:g}", "-i", str(ruta),
                        "-an", "-vf", f"fps={fps:.6f},scale={ancho_an}:{alto_an}:flags=area,format=gray",
                        "-frames:v", str(n), "-f", "rawvideo", "-"], capture_output=True)
    if r.returncode != 0:
        return None
    datos = np.frombuffer(r.stdout, dtype=np.uint8)
    por = ancho_an * alto_an
    cuantos = len(datos) // por
    if cuantos < 3:
        return None
    return datos[:cuantos * por].reshape(cuantos, alto_an, ancho_an).astype(np.float32)


def _mejor_posicion(energia: np.ndarray, ventana: int) -> tuple[int, int, float, float]:
    """(inicio de la mejor ventana, inicio de la centrada, energía de la mejor, energía de la centrada)."""
    acum = np.concatenate([[0.0], np.cumsum(energia)])
    n = len(energia) - ventana + 1
    sumas = acum[ventana:ventana + n] - acum[:n]
    centro = (len(energia) - ventana) // 2
    return int(np.argmax(sumas)), int(centro), float(sumas.max()), float(sumas[centro])


def elegir_recorte(ruta, inicio: float, duracion: float, ancho_v: int, alto_v: int,
                   ancho_salida: int, alto_salida: int, *, ajustes: dict | None = None) -> dict:
    """Recorte fijo en píxeles de la fuente: {x, y, ancho, alto, escala, modo}. `modo` es "accion" o "centro"."""
    aj = {"fotogramas": FOTOGRAMAS, "ancho_analisis": ANCHO_ANALISIS, "margen_accion": MARGEN_ACCION,
          "peso_bordes": PESO_BORDES, **(ajustes or {})}
    escala, vw, vh = _ventana(ancho_v, alto_v, ancho_salida, alto_salida)
    x, y = ((ancho_v - vw) // 2) // 2 * 2, ((alto_v - vh) // 2) // 2 * 2
    salida = {"x": x, "y": y, "ancho": vw, "alto": vh, "escala": round(escala, 6), "modo": "centro"}
    horizontal = ancho_v > vw
    if not horizontal and alto_v <= vh:
        return salida                                  # la fuente ya tiene la proporción: no hay dónde elegir
    try:
        pila = _fotogramas_grises(Path(ruta), float(inicio), float(duracion), ancho_v, alto_v,
                                  int(aj["fotogramas"]), int(aj["ancho_analisis"]))
    except (OSError, ValueError):
        return salida
    if pila is None:
        return salida
    movimiento = np.abs(np.diff(pila, axis=0)).sum(axis=0)          # (alto, ancho): lo que se movió en total
    if float(movimiento.sum()) <= 0:
        return salida                                  # fuente estática o uniforme: nada que seguir
    gx = np.abs(np.diff(pila, axis=2, prepend=pila[:, :, :1])).sum(axis=0)
    gy = np.abs(np.diff(pila, axis=1, prepend=pila[:, :1, :])).sum(axis=0)
    bordes = gx + gy
    if float(bordes.sum()) > 0:                        # los bordes pesan como mucho `peso_bordes` del movimiento
        bordes = bordes * (float(aj["peso_bordes"]) * float(movimiento.sum()) / float(bordes.sum()))
    total = movimiento + bordes
    alto_an, ancho_an = total.shape
    if horizontal:
        energia, ventana, largo_src, largo_an = total.sum(axis=0), int(round(vw * ancho_an / ancho_v)), ancho_v, ancho_an
    else:
        energia, ventana, largo_src, largo_an = total.sum(axis=1), int(round(vh * alto_an / alto_v)), alto_v, alto_an
    ventana = max(1, min(ventana, len(energia)))
    mejor, centro, e_mejor, e_centro = _mejor_posicion(energia, ventana)
    if e_mejor <= e_centro * (1.0 + float(aj["margen_accion"])):
        return salida
    # Cuántos píxeles de la fuente mide cada paso del análisis; la ventana va centrada en la mejor posición.
    paso = largo_src / largo_an
    medio = (mejor + ventana / 2) * paso
    if horizontal:
        x = int(round(min(max(medio - vw / 2, 0), ancho_v - vw))) // 2 * 2
    else:
        y = int(round(min(max(medio - vh / 2, 0), alto_v - vh))) // 2 * 2
    return {**salida, "x": x, "y": y, "modo": "accion"}

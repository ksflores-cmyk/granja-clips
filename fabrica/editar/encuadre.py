"""Encuadre de una toma con video: un recorte que cubre el cuadro vertical, sin zoom; desde el estilo v3 (punto 3, Kevin,
25 nov) con un paneo horizontal muy suave que sigue la acción (`recorte.paneo`, ver `calcular_paneo`).

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

from .. import plazo


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
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{inicio:g}", "-t", f"{duracion:g}", "-i", str(ruta),
                            "-an", "-vf", f"fps={fps:.6f},scale={ancho_an}:{alto_an}:flags=area,format=gray",
                            "-frames:v", str(n), "-f", "rawvideo", "-"], capture_output=True,
                           timeout=plazo.tiempo_para(60))
    except subprocess.TimeoutExpired:     # lectura colgada: se queda centrada (el plazo del ciclo sí sube)
        return None
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
                   ancho_salida: int, alto_salida: int, *, ajustes: dict) -> dict:
    """Recorte de la toma: el fijo de `_recorte_fijo` y, si `ajustes.paneo.activo` y la fuente es apaisada, su paneo
    horizontal (`paneo`: [[t, x], ...], t en segundos desde `inicio`, x en píxeles de la fuente). `x` es el del
    instante 0."""
    r = _recorte_fijo(ruta, inicio, duracion, ancho_v, alto_v, ancho_salida, alto_salida, ajustes=ajustes)
    cfg = ajustes.get("paneo") or {}
    if not cfg.get("activo") or ancho_v <= r["ancho"]:
        return r
    try:
        paneo = calcular_paneo(Path(ruta), float(inicio), float(duracion), ancho_v, alto_v, r["ancho"], r["x"],
                               cfg, float(ajustes.get("peso_bordes", 0.2)))
    except (OSError, ValueError):
        paneo = None
    if not paneo:
        return r
    return {**r, "x": int(paneo[0][1]), "paneo": paneo}


def _recorte_fijo(ruta, inicio: float, duracion: float, ancho_v: int, alto_v: int,
                  ancho_salida: int, alto_salida: int, *, ajustes: dict) -> dict:
    """Recorte fijo en píxeles de la fuente: {x, y, ancho, alto, escala, modo}. `modo` es "accion" o "centro".

    `ajustes` es obligatorio: `pantalla_completa.recorte` de marca.yml (fotogramas, ancho_analisis, margen_accion,
    peso_bordes).
    """
    aj = ajustes
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


# ---------------------------------------------------------------- paneo (estilo v3, punto 3)

def calcular_paneo(ruta: Path, inicio: float, duracion: float, ancho_v: int, alto_v: int, vw: int, x_base: int,
                   cfg: dict, peso_bordes: float = 0.2) -> list[list[float]] | None:
    """Puntos [[t, x], ...] cada `paso_s` del paneo horizontal, o None si no hace falta moverse.

    En cada instante se busca la ventana con más movimiento (y algo de bordes) alrededor de ese momento
    (`suavizado_s`); el objetivo sólo cambia si se aleja más de `zona_muerta` del ancho de la ventana, no se separa del
    encuadre fijo más de `desviacion_max` de ese ancho, avanza como mucho `velocidad_max` del ancho de la fuente por
    segundo y se suaviza con una media móvil de `suavizado_s`. Nunca cambia la escala (sin zoom) ni la altura."""
    paso = float(cfg.get("paso_s", 0.5))
    n = max(3, int(duracion / paso) + 1)
    ancho_an = int(cfg.get("ancho_analisis", 192))
    alto_an = max(2, int(round(ancho_an * alto_v / ancho_v)))
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{inicio:g}", "-t", f"{duracion + paso:g}", "-i", str(ruta),
                            "-an", "-vf", f"fps={1 / paso:.6f},scale={ancho_an}:{alto_an}:flags=area,format=gray",
                            "-frames:v", str(n + 1), "-f", "rawvideo", "-"], capture_output=True,
                           timeout=plazo.tiempo_para(60))
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    datos = np.frombuffer(r.stdout, dtype=np.uint8)
    por = ancho_an * alto_an
    cuantos = len(datos) // por
    if cuantos < 3:
        return None
    pila = datos[:cuantos * por].reshape(cuantos, alto_an, ancho_an).astype(np.float32)
    mov = np.abs(np.diff(pila, axis=0)).sum(axis=1)                       # (cuantos-1, ancho): movimiento por columna
    bordes = np.abs(np.diff(pila, axis=2, prepend=pila[:, :, :1])).sum(axis=1)[1:]
    tot_b = float(bordes.sum())
    if tot_b > 0:
        bordes = bordes * (peso_bordes * float(mov.sum()) / tot_b)
    energia = mov + bordes
    if float(energia.sum()) <= 0:
        return None
    k = max(1, int(round(float(cfg.get("suavizado_s", 2.0)) / paso)))
    nucleo = np.ones(k) / k
    energia = np.apply_along_axis(lambda c: np.convolve(c, nucleo, mode="same"), 0, energia)
    escala = ancho_v / ancho_an
    ventana = max(1, min(ancho_an, int(round(vw / escala))))
    centro_base = x_base + vw / 2
    dev = float(cfg.get("desviacion_max", 0.25)) * vw
    zona = float(cfg.get("zona_muerta", 0.08)) * vw
    vmax = float(cfg.get("velocidad_max", 0.04)) * ancho_v * paso
    objetivos = []
    sostenido = centro_base
    for fila in energia:
        acum = np.concatenate([[0.0], np.cumsum(fila)])
        sumas = acum[ventana:] - acum[:-ventana] if ventana < len(acum) else np.array([acum[-1]])
        c = (int(np.argmax(sumas)) + ventana / 2) * escala
        c = min(max(c, centro_base - dev), centro_base + dev)
        if abs(c - sostenido) > zona:
            sostenido = c
        objetivos.append(sostenido)
    pos = [objetivos[0]]
    for c in objetivos[1:]:
        pos.append(pos[-1] + max(-vmax, min(vmax, c - pos[-1])))
    if len(pos) >= k:
        pos = list(np.convolve(np.pad(pos, (k // 2, k - 1 - k // 2), mode="edge"), nucleo, mode="valid"))
    xs = [int(round(min(max(c - vw / 2, 0), ancho_v - vw))) // 2 * 2 for c in pos]
    if max(xs) - min(xs) < 8:
        return None
    puntos = [[round(i * paso, 3), x] for i, x in enumerate(xs)]
    return [p for p in puntos if p[0] <= duracion + 1e-6] or None


def x_en(recorte: dict, t_rel: float) -> float:
    """x del recorte en el instante `t_rel` (segundos desde el inicio del tramo de la fuente)."""
    paneo = recorte.get("paneo")
    if not paneo:
        return float(recorte["x"])
    if t_rel <= paneo[0][0]:
        return float(paneo[0][1])
    for (t0, x0), (t1, x1) in zip(paneo, paneo[1:]):
        if t_rel <= t1:
            return x0 + (x1 - x0) * (t_rel - t0) / max(t1 - t0, 1e-9)
    return float(paneo[-1][1])


def expresion_x(recorte: dict) -> str:
    """El `x` del filtro crop de ffmpeg: un número o, con paneo, interpolación lineal entre los puntos (entre comillas
    para que las comas no corten el filtro). `t` es el tiempo desde el inicio del tramo de la fuente."""
    paneo = recorte.get("paneo")
    if not paneo:
        return str(int(recorte["x"]))
    expr = f"{paneo[-1][1]:g}"
    for (t0, x0), (t1, x1) in reversed(list(zip(paneo, paneo[1:]))):
        tramo = f"{x0:g}+({x1 - x0:g})*(t-{t0:g})/{max(t1 - t0, 1e-6):g}"
        expr = f"if(lt(t,{t1:g}),{tramo},{expr})"
    return f"'{expr}'"


def problemas_paneo(recorte: dict, ancho_v: int, fps: float = 30.0, max_frac_cuadro: float = 0.002) -> list[str]:
    """Motivos por los que un paneo no vale: fuera de la fuente, puntos desordenados o más rápido que
    `max_frac_cuadro` del ancho de la fuente por fotograma."""
    paneo = recorte.get("paneo")
    if not paneo:
        return []
    m = []
    vw = int(recorte["ancho"])
    for t, x in paneo:
        if x < 0 or x + vw > ancho_v:
            m.append(f"paneo fuera de la fuente en {t:g} s (x {x})")
            break
    for (t0, x0), (t1, x1) in zip(paneo, paneo[1:]):
        if t1 <= t0:
            m.append("puntos del paneo desordenados")
            break
        por_cuadro = abs(x1 - x0) / ((t1 - t0) * fps)
        if por_cuadro > max_frac_cuadro * ancho_v:
            m.append(f"paneo demasiado rápido entre {t0:g} y {t1:g} s ({por_cuadro:.1f} px por fotograma)")
            break
    return m

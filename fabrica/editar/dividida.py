"""Pantalla dividida (tarea #16): detecta la facecam, los estorbos y la zona del juego, y arma los dos paneles.

Determinista, sin red ni modelos descargados: la cara sale del clasificador Haar que trae el paquete
`opencv-python-headless` (`cv2.data.haarcascades`); lo demás es numpy sobre unos fotogramas en gris (los mismos de
`encuadre`). Todas las medidas viven en `marca.yml` (clave `dividida`).

`detectar` nunca rompe la toma: ante cualquier problema (sin cv2, lectura fallida, plazo) devuelve `modo: "completa"`
con su motivo y la pantalla completa de la #5 sigue mandando. Las cajas son {x, y, ancho, alto} en píxeles de la
fuente (los estorbos llevan además `tipo`).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .. import plazo
from . import encuadre

DIVIDIDA, COMPLETA = "dividida", "completa"


# ---------------------------------------------------------------- ajustes e interruptores

def config(marca) -> dict | None:
    return marca.datos.get("dividida")


def ajustes_de(marca) -> dict:
    """Ajustes de `detectar`: `dividida.deteccion` más la proporción del panel del juego (ancho, alto)."""
    cfg = config(marca)
    aj = dict(cfg["deteccion"])
    aj["proporcion_juego"] = [marca.ancho, marca.alto - int(cfg["alto_facecam"])]
    return aj


def activa(marca, ficha: dict | None) -> bool:
    """Interruptores: `dividida.activa` de marca.yml y, por ficha, `reglas.dividida: false`."""
    cfg = config(marca)
    if not cfg or not cfg.get("activa"):
        return False
    return ((ficha or {}).get("reglas") or {}).get("dividida") is not False


# ---------------------------------------------------------------- cajas

def caja(x, y, ancho, alto) -> dict:
    return {"x": int(round(x)), "y": int(round(y)), "ancho": int(round(ancho)), "alto": int(round(alto))}


def _interseca(a: dict, b: dict, margen: float = 0.0) -> bool:
    return (a["x"] < b["x"] + b["ancho"] + margen and b["x"] < a["x"] + a["ancho"] + margen
            and a["y"] < b["y"] + b["alto"] + margen and b["y"] < a["y"] + a["alto"] + margen)


def _dentro(a: dict, b: dict, tol: float = 0.0) -> bool:
    """True si la caja `a` cabe dentro de `b` (con `tol` píxeles de holgura)."""
    return (a["x"] >= b["x"] - tol and a["y"] >= b["y"] - tol
            and a["x"] + a["ancho"] <= b["x"] + b["ancho"] + tol and a["y"] + a["alto"] <= b["y"] + b["alto"] + tol)


def _par(n: float) -> int:
    n = int(round(n))
    return n - n % 2


# ---------------------------------------------------------------- cara (Haar o detector inyectado)

class SinCv2(Exception):
    """No se puede usar OpenCV: el motivo va en el mensaje."""


def detector_haar(aj: dict):
    """Detector por omisión: función gris (uint8, alto x ancho) -> [(x, y, ancho, alto)] en sus píxeles."""
    try:
        import cv2
    except ImportError as e:
        raise SinCv2("no se puede importar cv2 (opencv-python-headless)") from e
    try:
        ruta = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        clasificador = cv2.CascadeClassifier(str(ruta))
        if clasificador.empty():
            raise RuntimeError("clasificador vacío")
    except Exception as e:
        raise SinCv2(f"cv2 {getattr(cv2, '__version__', '?')} no trae el clasificador Haar de caras") from e
    h = aj["haar"]

    def detectar_caras(gris: np.ndarray) -> list[tuple[int, int, int, int]]:
        lado = max(12, int(round(float(h["min_lado"]) * gris.shape[1])))
        cajas = clasificador.detectMultiScale(gris, scaleFactor=float(h["escala"]), minNeighbors=int(h["vecinos"]),
                                              minSize=(lado, lado))
        return [tuple(int(v) for v in c) for c in cajas]
    return detectar_caras


def _cara_estable(pila: np.ndarray, detector, aj: dict) -> tuple[tuple | None, str]:
    """((cx, cy, w, h) mediana en píxeles de análisis, motivo). None si no hay cara estable."""
    n, alto, ancho = pila.shape
    k = max(1, min(int(aj["fotogramas_cara"]), n))
    indices = sorted({int(round(i)) for i in np.linspace(0, n - 1, k)})
    cuadros = []
    for i in indices:
        if not plazo.queda(2):
            raise plazo.SinTiempo("se acabó el plazo buscando la cara")
        gris = np.clip(pila[i], 0, 255).astype(np.uint8)
        cuadros.append([(x + w / 2, y + h / 2, float(w), float(h)) for x, y, w, h in detector(gris)])
    con_cara = sum(1 for c in cuadros if c)
    necesarios = max(1, math.ceil(float(aj["min_fraccion_cara"]) * len(cuadros)))
    if con_cara == 0:
        return None, "no hay ninguna cara en los fotogramas"
    radio = float(aj["max_dispersion"]) * ancho
    mejor, mejor_cuenta = None, 0
    for c in cuadros:
        for cand in c:
            cuenta = sum(1 for otros in cuadros
                         if any(math.hypot(o[0] - cand[0], o[1] - cand[1]) <= radio for o in otros))
            if cuenta > mejor_cuenta:
                mejor, mejor_cuenta = cand, cuenta
    miembros = [o for otros in cuadros for o in otros if math.hypot(o[0] - mejor[0], o[1] - mejor[1]) <= radio]
    if mejor_cuenta < necesarios:
        if con_cara >= necesarios:
            return None, (f"la cara salta de sitio: la posición más repetida sale en {mejor_cuenta} de "
                          f"{len(cuadros)} fotogramas (hacen falta {necesarios})")
        return None, f"cara en {con_cara} de {len(cuadros)} fotogramas (hacen falta {necesarios})"
    med = tuple(float(np.median([m[j] for m in miembros])) for j in range(4))
    dispersion = max(math.hypot(m[0] - med[0], m[1] - med[1]) for m in miembros) / ancho
    if dispersion > float(aj["max_dispersion"]) + 1e-9:
        return None, f"la cara no es estable: dispersión {dispersion:.3f} del ancho"
    return med, ""


# ---------------------------------------------------------------- caja de la facecam

def _caja_facecam(pila: np.ndarray, cara: tuple, aj: dict) -> tuple[int, int, int, int, str]:
    """(x0, y0, x1, y1, cómo) en píxeles de análisis: de la cara hasta el borde persistente más cercano o, sin él,
    con un factor fijo. Siempre dentro del cuadro."""
    n, alto, ancho = pila.shape
    cx, cy, w, h = cara
    fx0, fy0, fx1, fy1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    ex = aj["expansion"]
    bp = aj["borde_persistente"]
    gx = np.abs(np.diff(pila, axis=2, prepend=pila[:, :, :1]))
    gy = np.abs(np.diff(pila, axis=1, prepend=pila[:, :1, :]))
    px = gx.min(axis=0) > float(bp["umbral"])          # borde vertical presente en todos los fotogramas
    py = gy.min(axis=0) > float(bp["umbral"])          # borde horizontal presente en todos los fotogramas
    frac = float(bp["fraccion"])
    ys, ye = int(max(0, math.floor(fy0))), int(min(alto, math.ceil(fy1)))
    xs, xe = int(max(0, math.floor(fx0))), int(min(ancho, math.ceil(fx1)))
    cuales = []

    def col(c):
        return px[ys:ye, c].mean() >= frac if ye > ys and 0 <= c < ancho else False

    def fila(r):
        return py[r, xs:xe].mean() >= frac if xe > xs and 0 <= r < alto else False

    d_min, d_max, d_fija = float(ex["min"]), float(ex["max"]), float(ex["fija"])
    # lado: (desde, hasta, paso, prueba, posición con el factor fijo, límite del cuadro)
    lados = {
        "der": (int(math.ceil(fx1 + d_min * w)), int(min(ancho - 1, math.floor(fx1 + d_max * w))), 1, col,
                fx1 + d_fija * w, ancho),
        "izq": (int(math.floor(fx0 - d_min * w)), int(max(0, math.ceil(fx0 - d_max * w))), -1, col,
                fx0 - d_fija * w, ancho),
        "aba": (int(math.ceil(fy1 + d_min * h)), int(min(alto - 1, math.floor(fy1 + d_max * h))), 1, fila,
                fy1 + d_fija * h, alto),
        "arr": (int(math.floor(fy0 - d_min * h)), int(max(0, math.ceil(fy0 - d_max * h))), -1, fila,
                fy0 - d_fija * h, alto),
    }
    res = {}
    for nombre, (desde, hasta, paso, prueba, fijo, limite) in lados.items():
        hallado = None
        if (paso == 1 and desde <= hasta) or (paso == -1 and desde >= hasta):
            for pos in range(desde, hasta + paso, paso):
                if prueba(pos):
                    hallado = pos
                    break
        if hallado is not None:        # el borde se queda fuera de la caja: se entra unos píxeles
            res[nombre] = float(hallado - paso * max(1, int(round(float(ex["recorte_borde"]) * ancho))))
            cuales.append(nombre)
        else:
            res[nombre] = float(min(max(fijo, 0), limite))
    x0, x1, y0, y1 = res["izq"], res["der"], res["arr"], res["aba"]
    como = "borde persistente en " + "+".join(cuales) if cuales else "factor fijo"
    return int(x0), int(y0), int(math.ceil(x1)), int(math.ceil(y1)), como


# ---------------------------------------------------------------- estorbos

def _componentes(mascara: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Componentes conexas (8 vecinos) de una rejilla booleana: [(i0, j0, i1, j1, celdas)] con i columna, j fila."""
    vistas = np.zeros_like(mascara, dtype=bool)
    filas, cols = mascara.shape
    salida = []
    for j in range(filas):
        for i in range(cols):
            if not mascara[j, i] or vistas[j, i]:
                continue
            pila, celdas = [(j, i)], []
            vistas[j, i] = True
            while pila:
                a, b = pila.pop()
                celdas.append((a, b))
                for da in (-1, 0, 1):
                    for db in (-1, 0, 1):
                        a2, b2 = a + da, b + db
                        if 0 <= a2 < filas and 0 <= b2 < cols and mascara[a2, b2] and not vistas[a2, b2]:
                            vistas[a2, b2] = True
                            pila.append((a2, b2))
            js, is_ = [c[0] for c in celdas], [c[1] for c in celdas]
            salida.append((min(is_), min(js), max(is_) + 1, max(js) + 1, len(celdas)))
    return salida


def _estorbos(pila: np.ndarray, facecam: tuple | None, aj: dict) -> list[dict]:
    """Cajas (píxeles de análisis, con `tipo`) de logos y marcos fijos, chat lateral y alertas que aparecen de golpe."""
    n, alto, ancho = pila.shape
    gx = np.abs(np.diff(pila, axis=2, prepend=pila[:, :, :1]))
    gy = np.abs(np.diff(pila, axis=1, prepend=pila[:, :1, :]))
    borde = ((gx + gy) > float(aj["umbral_borde"])).astype(np.float32)
    cols, filas = int(aj["rejilla"][0]), int(aj["rejilla"][1])
    xs = np.linspace(0, ancho, cols + 1).round().astype(int)
    ys = np.linspace(0, alto, filas + 1).round().astype(int)
    dens = np.zeros((n, filas, cols), dtype=np.float32)
    for j in range(filas):
        for i in range(cols):
            dens[:, j, i] = borde[:, ys[j]:ys[j + 1], xs[i]:xs[i + 1]].mean(axis=(1, 2))
    cx = (xs[:-1] + xs[1:]) / 2 / ancho
    cy = (ys[:-1] + ys[1:]) / 2 / alto
    banda, banda_chat = float(aj["banda"]), float(aj["banda_chat"])
    en_banda = ((cx[None, :] < banda) | (cx[None, :] > 1 - banda) | (cy[:, None] < banda) | (cy[:, None] > 1 - banda))
    lateral = np.broadcast_to((cx[None, :] < banda_chat) | (cx[None, :] > 1 - banda_chat), (filas, cols))
    libre = np.ones((filas, cols), dtype=bool)                          # celdas que no son de la facecam
    if facecam is not None:
        x0, y0, x1, y1 = facecam
        for j in range(filas):
            for i in range(cols):
                if xs[i] < x1 and xs[i + 1] > x0 and ys[j] < y1 and ys[j + 1] > y0:
                    libre[j, i] = False
    movimiento = np.abs(np.diff(pila, axis=0)).mean(axis=0)            # (alto, ancho)
    resto = np.zeros((alto, ancho), dtype=bool)
    for j in range(filas):
        for i in range(cols):
            if libre[j, i] and not en_banda[j, i]:
                resto[ys[j]:ys[j + 1], xs[i]:xs[i + 1]] = True
    mov_resto = float(movimiento[resto].mean()) if resto.any() else float(movimiento.mean())
    med, mx, mn = np.median(dens, axis=0), dens.max(axis=0), dens.min(axis=0)
    estatica = (libre & en_banda & (med >= float(aj["densidad_logo"])) & ((mx - mn) <= float(aj["variacion_estatica"])))
    if mov_resto < float(aj["min_movimiento"]):
        estatica[:] = False                       # el juego casi no se mueve: lo fijo no se distingue de él
    chat = libre & lateral & (med >= float(aj["densidad_chat"])) & ~estatica
    visibles = (dens >= (med + float(aj["salto_alerta"]) / 2)[None]).mean(axis=0)
    alerta = (libre & en_banda & (med <= float(aj["densidad_base_alerta"]))
              & ((mx - med) >= float(aj["salto_alerta"])) & (visibles <= float(aj["max_fraccion_alerta"]))
              & (visibles > 0) & ~estatica & ~chat)
    salida = []
    for tipo, mascara, minimo in (("estatico", estatica, 1), ("chat", chat, int(aj["min_celdas_chat"])),
                                  ("alerta", alerta, int(aj["min_celdas_alerta"]))):
        for i0, j0, i1, j1, celdas in _componentes(mascara):
            if celdas >= minimo:
                salida.append({"tipo": tipo, "caja": (int(xs[i0]), int(ys[j0]), int(xs[i1]), int(ys[j1]))})
    return salida


# ---------------------------------------------------------------- zona del juego

def _integral(a: np.ndarray) -> np.ndarray:
    i = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    i[1:, 1:] = a.cumsum(axis=0).cumsum(axis=1)
    return i


def _suma(i: np.ndarray, ys: np.ndarray, xs: np.ndarray, hh: int, ww: int) -> np.ndarray:
    """Suma de la región hh x ww con esquina en cada (y, x): matriz (len(ys), len(xs))."""
    y0, y1 = ys[:, None], (ys + hh)[:, None]
    x0, x1 = xs[None, :], (xs + ww)[None, :]
    return i[y1, x1] - i[y0, x1] - i[y1, x0] + i[y0, x0]


def _zona_juego(pila: np.ndarray, facecam: tuple, estorbos: list[tuple], aj: dict) -> tuple | None:
    """(x, y, ancho, alto) en píxeles de análisis del recorte con la proporción del panel del juego de mayor acción
    que no toca la facecam y evita los estorbos (si no cabe sin ellos, con castigo por área tapada)."""
    n, alto, ancho = pila.shape
    pw, ph = aj["proporcion_juego"]
    razon = pw / ph
    mov = np.abs(np.diff(pila, axis=0)).sum(axis=0)
    gx = np.abs(np.diff(pila, axis=2, prepend=pila[:, :, :1])).sum(axis=0)
    gy = np.abs(np.diff(pila, axis=1, prepend=pila[:, :1, :])).sum(axis=0)
    bordes = gx + gy
    if float(bordes.sum()) > 0 and float(mov.sum()) > 0:
        bordes = bordes * (float(aj["peso_bordes"]) * float(mov.sum()) / float(bordes.sum()))
    energia = mov + bordes if float(mov.sum()) > 0 else np.zeros_like(mov)
    fx0, fy0, fx1, fy1 = facecam
    energia[max(0, fy0):fy1, max(0, fx0):fx1] = 0
    total = float(energia.sum())
    mascara_f = np.zeros((alto, ancho), dtype=np.float64)
    m = float(aj["margen_facecam"]) * ancho
    mascara_f[int(max(0, fy0 - m)):int(math.ceil(fy1 + m)), int(max(0, fx0 - m)):int(math.ceil(fx1 + m))] = 1
    mascara_e = np.zeros((alto, ancho), dtype=np.float64)
    for x0, y0, x1, y1 in estorbos:
        mascara_e[max(0, y0):y1, max(0, x0):x1] = 1
    i_en, i_f, i_e = _integral(energia), _integral(mascara_f), _integral(mascara_e)
    h_max = min(alto, int(ancho / razon))
    h_min = max(2, int(math.ceil(float(aj["min_alto_juego"]) * alto)))
    if h_max < h_min:
        return None
    mejores: dict[bool, tuple | None] = {False: None, True: None}      # {con_castigo: (puntaje, x, y, w, h)}
    for hh in np.unique(np.linspace(h_max, h_min, 8).astype(int)):
        hh = int(hh)
        ww = min(ancho, int(round(hh * razon)))
        paso = 2
        ys, xs = np.arange(0, alto - hh + 1, paso), np.arange(0, ancho - ww + 1, paso)
        if len(ys) == 0 or len(xs) == 0:
            continue
        toca_f = _suma(i_f, ys, xs, hh, ww) > 0
        capt = _suma(i_en, ys, xs, hh, ww) / total if total > 0 else np.zeros((len(ys), len(xs)))
        sobre_e = _suma(i_e, ys, xs, hh, ww) / float(hh * ww)
        centro = (np.hypot((xs[None, :] + ww / 2) / ancho - 0.5, (ys[:, None] + hh / 2) / alto - 0.5))
        base = capt + float(aj["peso_tamano"]) * hh / h_max - 0.05 * centro
        for castigo, valido, puntaje in ((False, ~toca_f & (sobre_e <= 0), base),
                                         (True, ~toca_f, base - float(aj["peso_estorbo"]) * sobre_e)):
            if not valido.any():
                continue
            p = np.where(valido, puntaje, -np.inf)
            k = int(np.argmax(p))
            j, i = divmod(k, p.shape[1])
            cand = (float(p[j, i]), int(xs[i]), int(ys[j]), ww, hh)
            if mejores[castigo] is None or cand[0] > mejores[castigo][0]:
                mejores[castigo] = cand
    elegido = mejores[False] or mejores[True]
    return None if elegido is None else elegido[1:]


# ---------------------------------------------------------------- detectar

def _a_fuente(c4: tuple, fx: float, fy: float, ancho_v: int, alto_v: int) -> dict:
    """(x0, y0, x1, y1) de análisis -> caja de la fuente, dentro del cuadro."""
    x0, y0 = max(0, int(round(c4[0] * fx))), max(0, int(round(c4[1] * fy)))
    x1, y1 = min(ancho_v, int(round(c4[2] * fx))), min(alto_v, int(round(c4[3] * fy)))
    return caja(x0, y0, x1 - x0, y1 - y0)


def _completa(motivo: str, **extra) -> dict:
    return {"modo": COMPLETA, "motivo": motivo, "cara": None, "facecam": None, "estorbos": [], "juego": None, **extra}


def detectar(ruta, inicio: float, duracion: float, ancho_v: int, alto_v: int, *, ajustes: dict,
             detector=None) -> dict:
    """{"modo": "dividida"|"completa", "motivo", "cara", "facecam", "estorbos", "juego"} en píxeles de la fuente.

    `ajustes`: `dividida.deteccion` de marca.yml más `proporcion_juego` (ver `ajustes_de`). `detector`: función
    gris (uint8, alto x ancho) -> [(x, y, ancho, alto)] en los píxeles de esa imagen; por omisión, Haar de OpenCV.
    Nunca lanza: cualquier fallo devuelve `modo: "completa"` con su motivo.
    """
    try:
        return _detectar(Path(ruta), float(inicio), float(duracion), int(ancho_v), int(alto_v), ajustes, detector)
    except SinCv2 as e:
        return _completa(f"sin OpenCV: {e}")
    except plazo.SinTiempo as e:
        return _completa(f"se acabó el plazo del ciclo ({e})")
    except Exception as e:       # la detección es un extra: jamás debe romper la toma
        return _completa(f"falló la detección ({type(e).__name__}: {e})")


def _detectar(ruta: Path, inicio: float, duracion: float, ancho_v: int, alto_v: int, aj: dict, detector) -> dict:
    if ancho_v <= 0 or alto_v <= 0:
        return _completa(f"fuente con tamaño inválido {ancho_v}x{alto_v}")
    if not plazo.queda(5):
        return _completa("sin tiempo en el plazo del ciclo para detectar")
    if detector is None:
        detector = detector_haar(aj)
    ancho_an = min(int(aj["ancho_analisis"]), ancho_v)
    pila = encuadre._fotogramas_grises(ruta, inicio, duracion, ancho_v, alto_v, int(aj["fotogramas"]), ancho_an)
    if pila is None:
        return _completa("no se pudieron leer los fotogramas")
    n, alto, ancho = pila.shape
    fx, fy = ancho_v / ancho, alto_v / alto
    cara, motivo = _cara_estable(pila, detector, aj)
    if cara is None:
        return _completa(motivo)
    cx, cy, w, h = cara
    if w * h / (ancho * alto) > float(aj["max_area_cara"]):
        return _completa(f"cara grande ({w * h / (ancho * alto):.1%} del cuadro): no es una facecam",
                         cara=_a_fuente((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), fx, fy, ancho_v, alto_v))
    ce = aj["centro_excluido"]
    if ce["x"][0] <= cx / ancho <= ce["x"][1] and ce["y"][0] <= cy / alto <= ce["y"][1]:
        return _completa("la cara está en el centro del cuadro: no es una facecam",
                         cara=_a_fuente((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), fx, fy, ancho_v, alto_v))
    x0, y0, x1, y1, como = _caja_facecam(pila, cara, aj)
    est = _estorbos(pila, (x0, y0, x1, y1), aj)
    zona = _zona_juego(pila, (x0, y0, x1, y1), [e["caja"] for e in est], aj)
    cara_f = _a_fuente((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), fx, fy, ancho_v, alto_v)
    facecam_f = _a_fuente((x0, y0, x1, y1), fx, fy, ancho_v, alto_v)
    estorbos_f = [{**_a_fuente(e["caja"], fx, fy, ancho_v, alto_v), "tipo": e["tipo"]} for e in est]
    if zona is None:
        return _completa("no cabe un recorte del juego de altura suficiente sin tocar la facecam",
                         cara=cara_f, facecam=facecam_f, estorbos=estorbos_f)
    zx, zy, zw, zh = zona
    pw, ph = aj["proporcion_juego"]
    alto_j = min(alto_v, _par(zh * fy))
    ancho_j = _par(alto_j * pw / ph)
    if ancho_j > ancho_v:
        ancho_j = _par(ancho_v)
        alto_j = _par(ancho_j * ph / pw)
    x_j = int(min(max(round((zx + zw / 2) * fx - ancho_j / 2), 0), ancho_v - ancho_j)) // 2 * 2
    y_j = int(min(max(round((zy + zh / 2) * fy - alto_j / 2), 0), alto_v - alto_j)) // 2 * 2
    juego = caja(x_j, y_j, ancho_j, alto_j)
    if _interseca(juego, facecam_f):
        return _completa("el recorte del juego tocaría la facecam tras redondear",
                         cara=cara_f, facecam=facecam_f, estorbos=estorbos_f)
    return {"modo": DIVIDIDA, "motivo": f"facecam por {como}; {len(estorbos_f)} estorbos", "cara": cara_f,
            "facecam": facecam_f, "estorbos": estorbos_f, "juego": juego}


# ---------------------------------------------------------------- paneles (geometría de la salida)

def _recorte(x, y, w, h, destino: dict) -> dict:
    """Recorte de la fuente (pares) que cubre `destino`: escala = la de cubrir, como en `encuadre.escala_cubrir`."""
    w, h = max(2, _par(w)), max(2, _par(h))
    esc = encuadre.escala_cubrir(w, h, destino["ancho"], destino["alto"])
    return {"x": int(x), "y": int(y), "ancho": w, "alto": h, "escala": round(esc, 6)}


def paneles(marca, det: dict, ancho_v: int, alto_v: int) -> dict:
    """{"paneles": [facecam, juego], "cara": caja de la cara en el cuadro}. ValueError con el motivo si no se puede."""
    cfg = config(marca)
    alto_f = int(cfg["alto_facecam"])
    dest_f = {"x": 0, "y": 0, "ancho": marca.ancho, "alto": alto_f}
    dest_j = {"x": 0, "y": alto_f, "ancho": marca.ancho, "alto": marca.alto - alto_f}
    cara, fc, juego = det["cara"], det["facecam"], det["juego"]
    if not (cara and fc and juego):
        raise ValueError("la detección no trae cara, facecam y juego")
    # Panel de la facecam: el recorte más grande con la proporción del panel que cabe en la caja; si la cara con su
    # holgura no cabe así, crece alrededor de la cara (dentro del cuadro).
    asp = dest_f["ancho"] / dest_f["alto"]
    if fc["ancho"] / fc["alto"] >= asp:
        h0, w0 = fc["alto"], fc["alto"] * asp
    else:
        w0, h0 = fc["ancho"], fc["ancho"] / asp
    hol = float(cfg["holgura_cara"])
    cara_h, cara_w = cara["alto"] * (1 + 2 * hol), cara["ancho"] * (1 + 2 * hol)
    h_nec = max(cara_h / float(cfg["max_cara_panel"]), cara_w / asp / float(cfg["max_cara_panel"]))
    h = max(h0, h_nec)
    w = h * asp
    if w > ancho_v or h > alto_v:
        raise ValueError("la cara no cabe entera en un panel de facecam dentro del cuadro de la fuente")
    ccx, ccy = cara["x"] + cara["ancho"] / 2, cara["y"] + cara["alto"] / 2
    x0 = min(max(ccx - w / 2, 0), ancho_v - w)
    y0 = min(max(ccy - float(cfg["cara_centro_y"]) * h, 0), alto_v - h)
    candidatos = []
    if w <= fc["ancho"] and h <= fc["alto"]:        # cabe en la caja: lo primero es que el recorte no salga de ella
        candidatos.append((min(max(x0, fc["x"]), fc["x"] + fc["ancho"] - w),
                           min(max(y0, fc["y"]), fc["y"] + fc["alto"] - h)))
    candidatos.append((x0, y0))                      # si no, centrado en la cara y dentro del cuadro

    def entera(r: dict) -> bool:
        return (cara["x"] - cara["ancho"] * hol >= r["x"] and cara["y"] - cara["alto"] * hol >= r["y"]
                and cara["x"] + cara["ancho"] * (1 + hol) <= r["x"] + r["ancho"]
                and cara["y"] + cara["alto"] * (1 + hol) <= r["y"] + r["alto"])
    rf = None
    for cx_, cy_ in candidatos:
        prueba = _recorte(int(round(cx_)) // 2 * 2, int(round(cy_)) // 2 * 2, w, h, dest_f)
        prueba["x"] = min(prueba["x"], ancho_v - prueba["ancho"])
        prueba["y"] = min(prueba["y"], alto_v - prueba["alto"])
        if entera(prueba):
            rf = prueba
            break
    if rf is None:
        raise ValueError("la cara no queda entera (con su holgura) dentro del panel de la facecam")
    esc = rf["escala"]
    cara_cuadro = caja((cara["x"] - rf["x"]) * esc, (cara["y"] - rf["y"]) * esc,
                       cara["ancho"] * esc, cara["alto"] * esc)
    rj = _recorte(juego["x"], juego["y"], juego["ancho"], juego["alto"], dest_j)
    if rj["x"] + rj["ancho"] > ancho_v or rj["y"] + rj["alto"] > alto_v:
        raise ValueError("el recorte del juego se sale de la fuente")
    return {"paneles": [{"nombre": "facecam", "recorte": rf, "destino": dest_f},
                        {"nombre": "juego", "recorte": rj, "destino": dest_j}],
            "cara": cara_cuadro}


def problemas_placas(capas: list[dict], cara: dict, marca) -> list[str]:
    """Placas (capas del plan) que tocan la cara con su margen: lista de motivos (vacía si ninguna la toca)."""
    margen = float(config(marca)["margen_cara"])
    malas = []
    for c in capas:
        b = {"x": float(c["x"]), "y": float(c["y"]), "ancho": float(c["ancho"]), "alto": float(c["alto"])}
        if _interseca(b, {k: float(v) for k, v in cara.items()}, margen):
            malas.append(f"{c.get('tipo')} toca la cara")
    return malas


# ---------------------------------------------------------------- comprobaciones del plan (las usa la puerta)

def problemas_paneles(geo: dict, marca, ancho_v: int, alto_v: int) -> list[str]:
    """Paneles que cubren el cuadro entero sin huecos ni solapes y recortes que cubren su destino dentro de la fuente."""
    ps = geo.get("paneles")
    if not isinstance(ps, list) or len(ps) < 2:
        return [f"el plan no trae los paneles: {ps}"]
    malas = []
    try:
        orden = sorted(ps, key=lambda p: float(p["destino"]["y"]))
        y = 0.0
        for p in orden:
            d, r = p["destino"], p["recorte"]
            if float(d["x"]) != 0 or float(d["ancho"]) != marca.ancho:
                malas.append(f"el panel {p.get('nombre')} no ocupa todo el ancho ({d})")
            if float(d["y"]) > y + 1e-6:
                malas.append(f"hueco de {float(d['y']) - y:g} px antes del panel {p.get('nombre')}")
            elif float(d["y"]) < y - 1e-6:
                malas.append(f"el panel {p.get('nombre')} se solapa con el anterior")
            y = max(y, float(d["y"]) + float(d["alto"]))
            x0, y0, w, h, esc = (float(r[k]) for k in ("x", "y", "ancho", "alto", "escala"))
            cubrir = max(float(d["ancho"]) / w, float(d["alto"]) / h)
            if esc < cubrir - 1e-3:
                malas.append(f"el panel {p.get('nombre')}: escala {esc:.3f} menor que la de cubrir {cubrir:.3f}")
            if abs(w * esc - float(d["ancho"])) > 2 * esc or abs(h * esc - float(d["alto"])) > 2 * esc:
                malas.append(f"el recorte del panel {p.get('nombre')} no llena su destino")
            if x0 < 0 or y0 < 0 or x0 + w > ancho_v or y0 + h > alto_v:
                malas.append(f"el recorte del panel {p.get('nombre')} se sale de la fuente {ancho_v}x{alto_v}")
        if abs(y - marca.alto) > 1e-6:
            malas.append(f"los paneles llegan hasta y={y:g} y el cuadro mide {marca.alto}")
    except (KeyError, TypeError, ValueError) as e:
        malas.append(f"paneles mal formados ({type(e).__name__}: {e})")
    return malas


def problemas_division(plan: dict, marca) -> list[str]:
    """Reglas de la disposición dividida sobre el plan: facecam arriba con su alto, juego abajo sin tocar la facecam,
    la cara de la detección dentro del recorte de arriba y ninguna placa sobre ella."""
    geo = plan.get("geometria") or {}
    cfg = config(marca)
    malas = []
    try:
        ps = {p["nombre"]: p for p in geo["paneles"]}
        pf, pj = ps["facecam"], ps["juego"]
        alto_f = float(cfg["alto_facecam"])
        d = pf["destino"]
        if float(d["y"]) != 0 or abs(float(d["alto"]) - alto_f) > float(cfg["tolerancia_alto"]) * alto_f:
            malas.append(f"el panel de la facecam debe ir arriba con alto {alto_f:g} ± "
                         f"{float(cfg['tolerancia_alto']):.0%} y va en y={d['y']} con alto {d['alto']}")
        if float(pj["destino"]["y"]) < float(d["y"]) + float(d["alto"]) - 1e-6:
            malas.append("el panel del juego no va debajo del de la facecam")
        det = geo["deteccion"]
        fc, cara = det["facecam"], det["cara"]
        rf, rj = pf["recorte"], pj["recorte"]
        rfc = {k: rf[k] for k in ("x", "y", "ancho", "alto")}
        rjc = {k: rj[k] for k in ("x", "y", "ancho", "alto")}
        if _interseca(rjc, fc):
            malas.append("el recorte del juego toca la facecam de la fuente")
        if not _dentro(cara, rfc, tol=2):
            malas.append("el panel de arriba no es la facecam: la cara detectada no está dentro de su recorte")
        cx, cy = rfc["x"] + rfc["ancho"] / 2, rfc["y"] + rfc["alto"] / 2
        if not (fc["x"] <= cx <= fc["x"] + fc["ancho"] and fc["y"] <= cy <= fc["y"] + fc["alto"]):
            malas.append("el panel de arriba no es la facecam: su recorte no está centrado en ella")
        c = geo["cara"]
        if not _dentro(c, {"x": 0, "y": 0, "ancho": marca.ancho, "alto": float(d["alto"])}, tol=1):
            malas.append("la cara no queda entera dentro del panel de arriba")
        malas += problemas_placas(geo.get("capas") or [], c, marca)
    except (KeyError, TypeError, ValueError) as e:
        malas.append(f"el plan dividido está incompleto ({type(e).__name__}: {e})")
    return malas


# ---------------------------------------------------------------- muestra de la detección (para las muestras)

def dibujar(imagen, det: dict, ruta) -> None:
    """Guarda en `ruta` (PNG) el fotograma con las cajas detectadas: cara, facecam, estorbos y juego."""
    from PIL import ImageDraw
    img = imagen.convert("RGB")
    d = ImageDraw.Draw(img)
    grosor = max(3, img.width // 300)

    def marco(c, color, etiqueta):
        d.rectangle((c["x"], c["y"], c["x"] + c["ancho"], c["y"] + c["alto"]), outline=color, width=grosor)
        d.text((c["x"] + grosor + 2, c["y"] + grosor + 2), etiqueta, fill=color)
    if det.get("juego"):
        marco(det["juego"], (0, 220, 0), "juego")
    if det.get("facecam"):
        marco(det["facecam"], (0, 140, 255), "facecam")
    for e in det.get("estorbos") or []:
        marco(e, (255, 160, 0), f"estorbo: {e.get('tipo', '')}")
    if det.get("cara"):
        marco(det["cara"], (255, 0, 0), "cara")
    d.text((8, 8), f"modo {det.get('modo')}: {det.get('motivo')}", fill=(255, 255, 255))
    img.save(ruta)

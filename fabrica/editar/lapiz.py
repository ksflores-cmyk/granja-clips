"""Trazos de lápiz rojo: subrayados y círculos generados por código con semillas fijas.

Biblioteca: 5 subrayados y 5 círculos (`marca/trazos/`). Cada toma elige `numero % 5` dentro de
su tipo. Los PNG de la biblioteca se escriben con una palabra de referencia; al componer, el mismo
trazo (misma semilla) se genera a la medida de la palabra real, para que el círculo la rodee con
margen sin cruzar las letras.

Regenerar la biblioteca: `python -m fabrica.editar.lapiz --generar`.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from .marca import Marca

TIPOS = ("subrayado", "circulo")
N_TRAZOS = 5
SEMILLAS = {
    "subrayado": [2101, 2102, 2103, 2104, 2105],
    "circulo": [3101, 3102, 3103, 3104, 3105],
}
PALABRA_REF = (340, 60)       # caja de la palabra de referencia para los PNG de la biblioteca
SUPERMUESTREO = 4
RELLENO = 6                   # píxeles de aire alrededor del trazo en el PNG
DIR_TRAZOS = Path(__file__).resolve().parents[2] / "marca" / "trazos"


def normalizar_tipo(tipo: str) -> str:
    t = tipo.strip().lower().replace("í", "i")
    if t not in TIPOS:
        raise ValueError(f"tipo de lápiz desconocido: {tipo!r} (subrayado o círculo)")
    return t


def indice_trazo(numero: int) -> int:
    """Trazo de la biblioteca para una toma: no se repite en dos tomas seguidas."""
    return int(numero) % N_TRAZOS


def _suave(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _afinado(t: float) -> float:
    """1 en el centro del trazo y se afina hacia los extremos (0.35 en la punta)."""
    borde = 0.14
    return 0.35 + 0.65 * _suave(min(t, 1 - t) / borde)


def _pintar(lienzo: Image.Image, puntos, grosor_base: float, rng: random.Random, k: int) -> None:
    d = ImageDraw.Draw(lienzo)
    n = len(puntos) - 1
    for i in range(n):
        t = i / max(1, n - 1)
        g = grosor_base * _afinado(t) * rng.uniform(0.94, 1.06)
        op = int(255 * rng.uniform(0.80, 0.95))
        a, b = puntos[i], puntos[i + 1]
        d.line([a, b], fill=op, width=max(1, int(round(g * k))))
        r = g * k / 2
        d.ellipse([a[0] - r, a[1] - r, a[0] + r, a[1] + r], fill=op)
    r = grosor_base * _afinado(1.0) * k / 2
    p = puntos[-1]
    d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=200)


def _a_rgba(alfa: Image.Image, color: tuple[int, int, int], tam: tuple[int, int]) -> Image.Image:
    alfa = alfa.resize(tam, Image.LANCZOS)
    capa = Image.new("RGBA", tam, color + (0,))
    capa.putalpha(alfa)
    return capa


def trazo_subrayado(marca: Marca, ancho_palabra: float, indice: int) -> Image.Image:
    """Subrayado: grosor 6 a 9 px, curva leve, sobresale 12 a 24 px por cada lado.

    La imagen mide (ancho de palabra + sobresalientes) de ancho; la línea va en el centro vertical.
    """
    cfg = marca.m("subrayado")
    rng = random.Random(SEMILLAS["subrayado"][indice % N_TRAZOS])
    k = SUPERMUESTREO
    izq = rng.uniform(*cfg["sobresale"])
    der = rng.uniform(*cfg["sobresale"])
    grosor = rng.uniform(*cfg["grosor"])
    largo = ancho_palabra + izq + der
    alto = int(math.ceil(grosor + 2 * RELLENO + cfg["alto_extra"]))
    ancho = int(math.ceil(largo + 2 * RELLENO))
    lienzo = Image.new("L", (ancho * k, alto * k), 0)
    n = 60
    inclin = rng.uniform(-cfg["inclinacion_max"], cfg["inclinacion_max"])
    fase = rng.uniform(0, math.tau)
    amp = rng.uniform(*cfg["amplitud"])
    pts = []
    for i in range(n + 1):
        t = i / n
        x = RELLENO + largo * t
        y = alto / 2 + inclin * largo * (t - 0.5) + math.sin(fase + t * 4.2) * amp + rng.uniform(-0.35, 0.35)
        pts.append((x * k, y * k))
    _pintar(lienzo, pts, grosor, rng, k)
    img = _a_rgba(lienzo, marca.color("lapiz_rojo"), (ancho, alto))
    img.info["sobresale_izq"] = izq
    return img


def _puntos_circulo(marca: Marca, w: float, h: float, indice: int, n_exp: float, extra: float):
    cfg = marca.m("circulo")
    rng = random.Random(SEMILLAS["circulo"][indice % N_TRAZOS])
    irr = float(cfg["irregularidad"])
    rx = w / 2 + cfg["margen_x"] + extra
    ry = h / 2 + cfg["margen_y"] + extra
    a0 = rng.uniform(0, math.tau)
    sobrepaso = math.radians(rng.uniform(*cfg["sobrepaso_grados"]))
    giro = math.radians(rng.uniform(-cfg["giro_max_grados"], cfg["giro_max_grados"]))
    p1, p2 = rng.uniform(0, math.tau), rng.uniform(0, math.tau)
    grosor = rng.uniform(*cfg["grosor"])
    total = math.tau + sobrepaso
    n = 140
    pts = []
    for i in range(n + 1):
        t = i / n
        a = a0 + total * t
        # irregularidad suave de ±irr y una apertura leve hacia afuera al cerrar
        f = 1 + irr * (0.6 * math.sin(2 * a + p1) + 0.4 * math.sin(3 * a + p2))
        f *= 1 + 0.05 * _suave(t)
        c, s = math.cos(a), math.sin(a)
        x = rx * f * math.copysign(abs(c) ** (2 / n_exp), c)
        y = ry * f * math.copysign(abs(s) ** (2 / n_exp), s)
        pts.append((x * math.cos(giro) - y * math.sin(giro), x * math.sin(giro) + y * math.cos(giro)))
    return pts, grosor, rng


def trazo_circulo(marca: Marca, ancho_palabra: float, alto_palabra: float, indice: int) -> Image.Image:
    """Círculo que rodea la palabra con margen: no cruza las letras.

    Radio horizontal = mitad del ancho + 28 px; vertical = mitad del alto + 20 px; irregularidad
    ±6 %, giro leve, sobrepaso del cierre de 15° a 35°, grosor 5 a 8 px que se afina en los
    extremos. El centro de la imagen es el centro de la palabra. Como la esquina de un rectángulo
    queda fuera de una elipse con esos radios, la curva es una superelipse; se busca el exponente
    más bajo con el que el trazo no pisa el rectángulo de la palabra.
    """
    k = SUPERMUESTREO
    cfg = marca.m("circulo")
    for extra in (0.0, *(float(h) for h in cfg["holguras_extra"])):
        for n_exp in (2.6, 3.0, 3.5, 4.0, 5.0, 6.5):
            pts, grosor, rng = _puntos_circulo(marca, ancho_palabra, alto_palabra, indice, n_exp, extra)
            mx = max(abs(x) for x, _ in pts) + grosor / 2 + RELLENO
            my = max(abs(y) for _, y in pts) + grosor / 2 + RELLENO
            ancho, alto = int(math.ceil(2 * mx)), int(math.ceil(2 * my))
            if ancho % 2:
                ancho += 1
            if alto % 2:
                alto += 1
            if _libre(pts, grosor, ancho_palabra, alto_palabra, float(cfg["holgura_letra"])):
                lienzo = Image.new("L", (ancho * k, alto * k), 0)
                px = [((ancho / 2 + x) * k, (alto / 2 + y) * k) for x, y in pts]
                _pintar(lienzo, px, grosor, rng, k)
                return _a_rgba(lienzo, marca.color("lapiz_rojo"), (ancho, alto))
    raise ValueError("no se pudo trazar un círculo que no cruce la palabra")


def _libre(pts, grosor: float, w: float, h: float, holgura_letra: float) -> bool:
    """Ningún punto del trazo (con medio grosor) entra en el rectángulo de la palabra."""
    holgura = grosor / 2 + holgura_letra
    for x, y in pts:
        dx = max(abs(x) - w / 2, 0)
        dy = max(abs(y) - h / 2, 0)
        dentro_x = abs(x) < w / 2 + holgura
        dentro_y = abs(y) < h / 2 + holgura
        if dentro_x and dentro_y:
            return False
    return True


def trazo_para(marca: Marca, tipo: str, numero: int, ancho_palabra: float, alto_palabra: float) -> Image.Image:
    """Trazo de la biblioteca que le toca a la toma `numero`, a la medida de la palabra."""
    tipo = normalizar_tipo(tipo)
    i = indice_trazo(numero)
    if tipo == "subrayado":
        return trazo_subrayado(marca, ancho_palabra, i)
    return trazo_circulo(marca, ancho_palabra, alto_palabra, i)


# ---------------------------------------------------------------- biblioteca en PNG

def nombre_png(tipo: str, indice: int) -> str:
    return f"{tipo}-{indice + 1:02d}.png"


def generar_biblioteca(marca: Marca, carpeta: Path | None = None) -> list[Path]:
    """Escribe los 10 PNG (5 subrayados y 5 círculos). Mismos píxeles en cada corrida."""
    carpeta = Path(carpeta) if carpeta else DIR_TRAZOS
    carpeta.mkdir(parents=True, exist_ok=True)
    escritos = []
    for i in range(N_TRAZOS):
        for tipo in TIPOS:
            img = (trazo_subrayado(marca, PALABRA_REF[0], i) if tipo == "subrayado"
                   else trazo_circulo(marca, PALABRA_REF[0], PALABRA_REF[1], i))
            ruta = carpeta / nombre_png(tipo, i)
            img.save(ruta, optimize=False, compress_level=9)
            escritos.append(ruta)
    return escritos


def cargar_trazo(tipo: str, indice: int, carpeta: Path | None = None) -> Image.Image:
    """Carga un PNG de la biblioteca (tamaño de referencia)."""
    carpeta = Path(carpeta) if carpeta else DIR_TRAZOS
    return Image.open(carpeta / nombre_png(normalizar_tipo(tipo), indice % N_TRAZOS)).convert("RGBA")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.editar.lapiz")
    ap.add_argument("--generar", action="store_true", help="escribe los 10 trazos en marca/trazos/")
    ap.add_argument("--salida", default=None, help="carpeta de salida (por defecto marca/trazos)")
    args = ap.parse_args(argv)
    if not args.generar:
        ap.print_help()
        return 2
    for ruta in generar_biblioteca(Marca.cargar(), args.salida):
        print(ruta)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""La historia (#20): el edit como relato, con apertura en frío y momentos con sentimiento y tratamiento.

El plan es un JSON con números y valores de listas cerradas, sin ningún texto propio (nada de él se dibuja):

    {"apertura": {"t0": 21.4, "t1": 23.6},
     "momentos": [{"t0": 9.0, "t1": 12.5, "sentimiento": "tension", "tratamiento": "zoom_suave", "sfx": null}, ...]}

Los tiempos son segundos del archivo enviado a los ojos (como `recorte`). Este módulo valida el plan (`validar_plan`),
lo convierte en tramos de salida (`mapa`, `fuente_en`, `remapear_lineas`), lo pide en cadena (`planificar`: Gemini en
la misma llamada de los ojos, segundo proveedor apagado, reglas sin IA) y arma la orden de ffmpeg (`orden_ffmpeg`,
función pura). La historia nunca descarta ni gasta una fuente: si algo falla, la toma sale como siempre.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import math
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .. import plazo
from ..config import cargar_yaml, raiz_repo

SENTIMIENTOS = ("tension", "sorpresa", "risa", "triunfo", "frustracion", "alivio", "asombro")
TRATAMIENTOS = ("ninguno", "zoom_suave", "camara_lenta", "sfx", "pausa")
CLAVES_PLAN = ("apertura", "momentos")
CLAVES_APERTURA = ("t0", "t1")
CLAVES_MOMENTO = ("t0", "t1", "sentimiento", "tratamiento", "sfx")
PROVEEDORES = ("gemini", "segundo", "sin-ia")      # `historia.proveedor` en el plan de la toma y en el marcador
RE_ID_SFX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

TOL = 1e-6
MIN_LINEA_S = 0.3                   # igual que `toma.MIN_LINEA_S`
FPS = 30
PASO_S = 0.5                        # ventana de volumen de las señales (igual que `sin_ia.PASO_S`)
APERTURA_SIN_IA_S = 2.0             # sin IA: 2 s alrededor del pico más alto del último 40 %
SEPARACION_SIN_IA_S = 2.0           # sin IA: separación mínima entre los centros de dos momentos
VENTANA_SIN_IA_S = 2.0              # sin IA: ancho de un momento (zoom y efecto)
VENTANA_LENTA_S = 1.4               # sin IA: ancho del momento en cámara lenta (1.5 s o menos)
VENTANA_PAUSA_S = 1.0               # sin IA: ancho del momento que termina en la pausa
PLANO_MIN_S = 2.0                   # debe quedar un tramo plano (velocidad 1, sin zoom ni efecto) de 2 s o más
MARGEN_PLANO_S = 0.15               # la puerta mide a esta distancia del borde de un tramo plano
TOPE_SEGUNDO_FOTOGRAMAS = 6
LADO_MAX_FOTOGRAMA = 512

DEFAULTS = {
    "activo": True, "apertura_min_s": 1.0, "apertura_max_s": 3.0, "apertura_final_pct": 40,
    "momentos_min": 2, "momentos_max": 4, "momento_min_s": 0.8,
    "camara_lenta_max": 1, "camara_lenta_momento_max_s": 1.5, "pausa_max": 1, "pausa_s": 0.4,
    "zoom_max_n": 2, "zoom_max": 1.12, "sfx_max": 2, "sfx_en_campanas": False, "ganancia_sfx_db": -6,
    "segundo": {"activo": False, "nombre": "segundo-proveedor", "motivo": "GitHub Models retirado el 30 jul 2026",
                "url": "", "modelo": "", "secreto": "", "tiempo_s": 60, "max_por_ciclo": 1},
}
ZOOM_MAX = DEFAULTS["zoom_max"]
PAUSA_S = DEFAULTS["pausa_s"]


class ErrorHistoria(RuntimeError):
    pass


def cargar_config(raiz=None) -> dict:
    """`config/historia.yml` sobre los valores por defecto (la forma la valida `fabrica.validar`)."""
    try:
        datos = cargar_yaml("config/historia.yml", raiz)
    except (OSError, ValueError):
        datos = {}
    datos = datos if isinstance(datos, dict) else {}
    cfg = {**DEFAULTS, **{k: v for k, v in datos.items() if k != "segundo"}}
    seg = datos.get("segundo")
    cfg["segundo"] = {**DEFAULTS["segundo"], **(seg if isinstance(seg, dict) else {})}
    return cfg


# ------------------------------------------------------------------ el mapa: tramos de salida

@dataclass(frozen=True)
class Tramo:
    """Un tramo de la salida: `s0`-`s1` en el MP4 y `f0`-`f1` en la fuente (segundos del archivo)."""
    s0: float
    s1: float
    f0: float
    f1: float
    velocidad: float = 1.0                       # 1.0, 0.5 o 0.0 (congelado)
    zoom: tuple[float, float] | None = None      # (desde, hasta) a lo largo del tramo
    sfx: str | None = None                       # id del efecto, que suena al empezar el tramo

    @property
    def dur(self) -> float:
        return self.s1 - self.s0

    @property
    def plano(self) -> bool:
        """Velocidad 1, sin zoom y sin efecto: ahí el MP4 es la fuente tal cual (la puerta mide aquí)."""
        return self.velocidad == 1.0 and self.zoom is None and self.sfx is None

    def a_dict(self) -> dict:
        d = asdict(self)
        d["zoom"] = list(self.zoom) if self.zoom else None
        return d


def _r(x: float) -> float:
    return round(float(x), 6)


def _cuadro(x: float) -> float:
    """`x` segundos llevados al cuadro más cercano de la salida (a 30 fps)."""
    return round(round(x * FPS) / FPS, 6)


def mapa(plan: dict, inicio: float, fin: float, *, zoom_max: float = ZOOM_MAX, pausa_s: float = PAUSA_S) -> list[Tramo]:
    """Tramos de salida (función pura): primero la apertura y luego todo el recorte `inicio`-`fin` partido en los
    momentos con su tratamiento. La cámara lenta dura el doble en la salida; la pausa añade un tramo congelado
    con el último cuadro del momento. Los bordes de salida caen en cuadros de 30 fps (así lo que mide la puerta es
    lo que hace ffmpeg): `s0`/`s1` son múltiplos de 1/30 y el audio de cada tramo tiene muestras exactas."""
    ap = plan["apertura"]
    crudos: list[dict] = [{"dur": float(ap["t1"]) - float(ap["t0"]), "f0": float(ap["t0"]), "f1": float(ap["t1"])}]
    cursor = float(inicio)

    def plano(f0: float, f1: float) -> None:
        if f1 - f0 > TOL:
            crudos.append({"dur": f1 - f0, "f0": f0, "f1": f1})

    for m in plan["momentos"]:
        t0, t1 = float(m["t0"]), float(m["t1"])
        plano(cursor, t0)
        tr = m["tratamiento"]
        if tr == "camara_lenta":
            crudos.append({"dur": 2 * (t1 - t0), "f0": t0, "f1": t1, "velocidad": 0.5})
        else:
            crudos.append({"dur": t1 - t0, "f0": t0, "f1": t1,
                           "zoom": (1.0, float(zoom_max)) if tr == "zoom_suave" else None,
                           "sfx": m.get("sfx") if tr == "sfx" else None})
            if tr == "pausa":
                crudos.append({"dur": float(pausa_s), "f0": t1, "f1": t1, "velocidad": 0.0})
        cursor = t1
    plano(cursor, float(fin))
    tramos, acumulado, s0 = [], 0.0, 0.0
    for c in crudos:
        acumulado += c["dur"]
        s1 = max(_cuadro(acumulado), round(s0 + 1 / FPS, 6))
        tramos.append(Tramo(_r(s0), _r(s1), _r(c["f0"]), _r(c["f1"]), c.get("velocidad", 1.0), c.get("zoom"),
                            c.get("sfx")))
        s0 = s1
    return tramos


def duracion_salida(tramos: list[Tramo]) -> float:
    return float(tramos[-1].s1) if tramos else 0.0


def _tramo_en(tramos: list[Tramo], t: float) -> Tramo:
    for tr in tramos:
        if tr.s0 - TOL <= t < tr.s1 - TOL:
            return tr
    return tramos[-1]


def fuente_en(tramos: list[Tramo], t: float) -> tuple[float, float]:
    """(segundo de la fuente, factor de zoom) del instante `t` de la salida."""
    tr = _tramo_en(tramos, t)
    if tr.velocidad == 0.0:
        return tr.f0, 1.0
    t = min(max(t, tr.s0), tr.s1)
    zoom = 1.0
    if tr.zoom:
        zoom = tr.zoom[0] + (tr.zoom[1] - tr.zoom[0]) * ((t - tr.s0) / tr.dur if tr.dur > 0 else 0.0)
    return tr.f0 + (t - tr.s0) * tr.velocidad, zoom


def tramos_planos(tramos: list[Tramo], minimo: float = 0.0) -> list[Tramo]:
    return [tr for tr in tramos if tr.plano and tr.dur >= minimo - TOL]


def instante_plano(tramos: list[Tramo], t: float, tope: float | None = None,
                   margen: float = MARGEN_PLANO_S, desde: float | None = None) -> float | None:
    """El instante de un tramo plano más cercano a `t` (a `margen` de sus bordes, entre `desde` y `tope`), o None."""
    mejor = None
    for tr in tramos_planos(tramos, 2 * margen + 0.1):
        lo, hi = tr.s0 + margen, tr.s1 - margen
        if desde is not None:
            lo = max(lo, desde)
        if tope is not None:
            hi = min(hi, tope)
        if hi < lo:
            continue
        c = min(max(t, lo), hi)
        if mejor is None or abs(c - t) < abs(mejor - t) - TOL:
            mejor = c
    return mejor


def tramo_plano_largo(tramos: list[Tramo], minimo: float = PLANO_MIN_S) -> Tramo | None:
    """El tramo plano más largo (de `minimo` s o más); el primero si hay empate."""
    cands = tramos_planos(tramos, minimo)
    return max(cands, key=lambda tr: (round(tr.dur, 6), -tr.s0), default=None)


def _salida_de(principales: list[Tramo], f: float, fin: bool) -> float | None:
    """Segundo de salida del segundo `f` de la fuente en el flujo principal (sin pausas ni apertura)."""
    for tr in principales:
        if tr.velocidad == 0.0:
            continue
        dentro = (tr.f0 < f <= tr.f1 + TOL) if fin else (tr.f0 - TOL <= f < tr.f1)
        if dentro:
            return tr.s0 + (f - tr.f0) / tr.velocidad
    return None


def _con(ln, t0: float, t1: float):
    if isinstance(ln, dict):
        return {**ln, "t0": _r(t0), "t1": _r(t1)}
    return dataclasses.replace(ln, t0=_r(t0), t1=_r(t1))


def _tiempos(ln) -> tuple[float, float]:
    return (float(ln["t0"]), float(ln["t1"])) if isinstance(ln, dict) else (float(ln.t0), float(ln.t1))


def remapear_lineas(lineas: list, tramos: list[Tramo], tope: float | None = None) -> list:
    """Las líneas (tiempos relativos al recorte) pasan al tiempo de salida en el tramo principal; si una cae en la
    ventana de la apertura, se repite recortada a ella (mismo texto, nada inventado). Las líneas de salida no se
    solapan; una más corta que `MIN_LINEA_S` se descarta; `tope` es el instante donde empieza el cierre."""
    if len(tramos) < 2:
        return list(lineas)
    ap, principales = tramos[0], tramos[1:]
    inicio = principales[0].f0
    salida = []
    for ln in lineas:
        r0, r1 = _tiempos(ln)
        a, b = inicio + r0, inicio + r1
        o0, o1 = _salida_de(principales, a, False), _salida_de(principales, b, True)
        if o0 is not None and o1 is not None and o1 > o0:
            salida.append((o0, o1, ln))
        elif o0 is not None and o1 is None:                  # termina más allá del último tramo: hasta el final
            salida.append((o0, principales[-1].s1, ln))
        lo, hi = max(a, ap.f0), min(b, ap.f1)
        if hi - lo > TOL:
            salida.append((ap.s0 + lo - ap.f0, ap.s0 + hi - ap.f0, ln))
    salida.sort(key=lambda x: (x[0], x[1]))
    final, previo = [], 0.0
    for o0, o1, ln in salida:
        o0 = max(o0, previo)
        if tope is not None:
            o1 = min(o1, tope)
        if o1 - o0 + TOL < MIN_LINEA_S:
            continue
        final.append(_con(ln, o0, o1))
        previo = o1
    return final


# ------------------------------------------------------------------ validación (código nuestro, no del modelo)

def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def es_campana(ficha: dict | None) -> bool:
    """Ficha de campaña: tipo A con `cr_campaign_id` o con `permiso_url` de campaña."""
    f = ficha or {}
    return f.get("tipo") == "A" and bool(f.get("cr_campaign_id") or f.get("permiso_url"))


def sfx_prohibido(ficha: dict | None, cfg: dict | None = None) -> str | None:
    """Por qué no hay efectos de sonido con esta ficha, o None si se permiten."""
    cfg = cfg or DEFAULTS
    if cfg.get("sfx_en_campanas") is True:
        return None
    if ((ficha or {}).get("reglas") or {}).get("sin_musica"):
        return "la ficha tiene reglas.sin_musica"
    if es_campana(ficha):
        return "la ficha es de campaña"
    return None


def duracion_esperada(plan: dict, recorte: tuple[float, float], pausa_s: float = PAUSA_S) -> float:
    """Duración de salida: recorte + apertura + lo que agregan la cámara lenta y las pausas."""
    ini, fin = float(recorte[0]), float(recorte[1])
    d = (fin - ini) + (float(plan["apertura"]["t1"]) - float(plan["apertura"]["t0"]))
    for m in plan["momentos"]:
        if m["tratamiento"] == "camara_lenta":
            d += float(m["t1"]) - float(m["t0"])
        elif m["tratamiento"] == "pausa":
            d += pausa_s
    return d


def _esquema(plan) -> list[str]:
    """Motivos por claves o valores fuera del esquema (sin texto propio). Vacío si el plan sólo trae lo permitido."""
    if not isinstance(plan, dict):
        return ["el plan no es un objeto JSON"]
    m = []
    extra = sorted(str(k) for k in plan if k not in CLAVES_PLAN)
    falta = [k for k in CLAVES_PLAN if k not in plan]
    if extra:
        m.append("claves fuera del esquema: " + ", ".join(extra))
    if falta:
        m.append("faltan claves: " + ", ".join(falta))
    ap = plan.get("apertura")
    if "apertura" in plan:
        if not isinstance(ap, dict):
            m.append("apertura no es un objeto")
        else:
            extra_a = sorted(str(k) for k in ap if k not in CLAVES_APERTURA)
            if extra_a:
                m.append("apertura: claves fuera del esquema: " + ", ".join(extra_a))
            for k in CLAVES_APERTURA:
                if not _num(ap.get(k)):
                    m.append(f"apertura.{k} falta o no es un número")
    momentos = plan.get("momentos")
    if "momentos" in plan:
        if not isinstance(momentos, list):
            m.append("momentos no es una lista")
        else:
            for i, mo in enumerate(momentos, 1):
                if not isinstance(mo, dict):
                    m.append(f"momento {i}: no es un objeto")
                    continue
                extra_m = sorted(str(k) for k in mo if k not in CLAVES_MOMENTO)
                if extra_m:
                    m.append(f"momento {i}: claves fuera del esquema: " + ", ".join(extra_m))
                for k in ("t0", "t1"):
                    if not _num(mo.get(k)):
                        m.append(f"momento {i}: {k} falta o no es un número")
                if mo.get("sentimiento") not in SENTIMIENTOS:
                    m.append(f"momento {i}: sentimiento {mo.get('sentimiento')!r} no está en la lista cerrada")
                if mo.get("tratamiento") not in TRATAMIENTOS:
                    m.append(f"momento {i}: tratamiento {mo.get('tratamiento')!r} no está en la lista cerrada")
                s = mo.get("sfx")
                if "sfx" not in mo:
                    m.append(f"momento {i}: falta sfx (null si no hay efecto)")
                elif s is not None and not (isinstance(s, str) and RE_ID_SFX.match(s)):
                    m.append(f"momento {i}: sfx {s!r} no es un id de efecto")
    return m


def sin_texto_propio(plan) -> tuple[bool, str]:
    """[plan] El plan no trae claves ni textos fuera del esquema (sentimiento y tratamiento de listas cerradas)."""
    m = _esquema(plan)
    return not m, "; ".join(m) if m else "sólo números y valores de listas cerradas"


def validar_plan(plan, recorte: tuple[float, float], rango_duracion: tuple[float, float], sfx_disponibles,
                 ficha: dict | None = None, cfg: dict | None = None) -> tuple[bool, list[str]]:
    """(ok, motivos): esquema, apertura, momentos, tratamientos, efectos y duración de salida, con un motivo por falla."""
    cfg = {**DEFAULTS, **(cfg or {})}
    m = _esquema(plan)
    if m:
        return False, m
    ini, fin = float(recorte[0]), float(recorte[1])
    ids = set(sfx_disponibles or [])
    ap, momentos = plan["apertura"], plan["momentos"]

    # apertura: el desenlace, de apertura_min_s a apertura_max_s, dentro del recorte y en su último 40 %
    a0, a1 = float(ap["t0"]), float(ap["t1"])
    d_ap = a1 - a0
    if not (cfg["apertura_min_s"] - TOL <= d_ap <= cfg["apertura_max_s"] + TOL):
        m.append(f"apertura de {d_ap:.2f} s: debe durar de {cfg['apertura_min_s']:g} a {cfg['apertura_max_s']:g} s")
    if a0 < ini - TOL or a1 > fin + TOL:
        m.append(f"apertura {a0:g}-{a1:g} s fuera del recorte {ini:g}-{fin:g} s")
    limite = fin - (fin - ini) * cfg["apertura_final_pct"] / 100
    if a0 < limite - TOL:
        m.append(f"apertura empieza en {a0:g} s: debe caer en el último {cfg['apertura_final_pct']:g} % del recorte "
                 f"(desde {limite:.2f} s)")

    # momentos
    n = len(momentos)
    if not cfg["momentos_min"] <= n <= cfg["momentos_max"]:
        m.append(f"{n} momentos: debe haber de {cfg['momentos_min']} a {cfg['momentos_max']}")
    previo = None
    for i, mo in enumerate(momentos, 1):
        t0, t1 = float(mo["t0"]), float(mo["t1"])
        if t0 < ini - TOL or t1 > fin + TOL:
            m.append(f"momento {i} {t0:g}-{t1:g} s fuera del recorte {ini:g}-{fin:g} s")
        if t1 - t0 < cfg["momento_min_s"] - TOL:
            m.append(f"momento {i} dura {t1 - t0:.2f} s: mínimo {cfg['momento_min_s']:g} s")
        if previo is not None:
            if t0 < previo[0] - TOL:
                m.append(f"momento {i} no está ordenado: empieza en {t0:g} s, antes que el anterior")
            elif t0 < previo[1] - TOL:
                m.append(f"momento {i} se solapa con el anterior ({t0:g} s < {previo[1]:g} s)")
        previo = (t0, t1)
    if momentos and all(mo["tratamiento"] == "ninguno" for mo in momentos):
        m.append("ningún momento lleva tratamiento: al menos uno debe ser distinto de ninguno")

    # tratamientos
    por = {t: [mo for mo in momentos if mo["tratamiento"] == t] for t in TRATAMIENTOS}
    if len(por["camara_lenta"]) > cfg["camara_lenta_max"]:
        m.append(f"{len(por['camara_lenta'])} cámaras lentas: máximo {cfg['camara_lenta_max']}")
    for mo in por["camara_lenta"]:
        if float(mo["t1"]) - float(mo["t0"]) > cfg["camara_lenta_momento_max_s"] + TOL:
            m.append(f"cámara lenta de {float(mo['t1']) - float(mo['t0']):.2f} s de fuente: máximo "
                     f"{cfg['camara_lenta_momento_max_s']:g} s (sale al doble)")
    if len(por["pausa"]) > cfg["pausa_max"]:
        m.append(f"{len(por['pausa'])} pausas: máximo {cfg['pausa_max']}")
    if len(por["zoom_suave"]) > cfg["zoom_max_n"]:
        m.append(f"{len(por['zoom_suave'])} zooms suaves: máximo {cfg['zoom_max_n']}")
    if len(por["sfx"]) > cfg["sfx_max"]:
        m.append(f"{len(por['sfx'])} efectos de sonido: máximo {cfg['sfx_max']}")
    prohibe = sfx_prohibido(ficha, cfg)
    for i, mo in enumerate(momentos, 1):
        s = mo["sfx"]
        if s is not None and mo["tratamiento"] != "sfx":
            m.append(f"momento {i}: sfx {s!r} sólo va con tratamiento sfx")
        if mo["tratamiento"] == "sfx":
            if s is None:
                m.append(f"momento {i}: el tratamiento sfx pide el id de un efecto")
            elif s not in ids:
                m.append(f"momento {i}: el efecto {s!r} no está verificado y disponible")
            if prohibe:
                m.append(f"momento {i}: efectos de sonido prohibidos ({prohibe})")

    # duración de salida
    lo, hi = float(rango_duracion[0]), float(rango_duracion[1])
    dur = duracion_esperada(plan, recorte, cfg["pausa_s"])
    if not lo - TOL <= dur <= hi + TOL:
        m.append(f"duración de salida {dur:.2f} s: debe caer entre {lo:g} y {hi:g} s")
    if not m:
        tr = mapa(plan, ini, fin, zoom_max=cfg["zoom_max"], pausa_s=cfg["pausa_s"])
        if tramo_plano_largo(tr) is None:
            m.append(f"no queda un tramo plano (velocidad 1, sin zoom ni efecto) de {PLANO_MIN_S:g} s o más "
                     "para comprobar el audio original")
    return not m, m


# ------------------------------------------------------------------ el prompt (sección 9 de ojos_prompt.md)

RUTA_PROMPT = Path(__file__).resolve().parent.parent / "ojos_prompt.md"
RE_SECCION = re.compile(r"<!--historia-->\n(.*?)<!--/historia-->\n", re.S)


def instruccion(cfg: dict | None, ids_sfx, prohibe_sfx: str | None = None) -> str:
    """La sección «9. historia» de `ojos_prompt.md` con los límites de la config y los efectos disponibles.
    La usan Gemini (misma llamada de los ojos) y el segundo proveedor."""
    cfg = {**DEFAULTS, **(cfg or {})}
    m = RE_SECCION.search(RUTA_PROMPT.read_text(encoding="utf-8"))
    if not m:
        return ""
    ids = [str(i) for i in (ids_sfx or [])]
    if prohibe_sfx:
        efectos = f"Sin efectos en esta pieza ({prohibe_sfx}): `sfx` va siempre en null y no uses el tratamiento sfx."
    elif ids:
        efectos = "Efectos disponibles para `sfx` (sólo estos ids): " + ", ".join(ids) + "."
    else:
        efectos = "No hay efectos de sonido disponibles: `sfx` va siempre en null y no uses el tratamiento sfx."
    cambios = {
        "{{APERTURA_MIN}}": f"{cfg['apertura_min_s']:g}", "{{APERTURA_MAX}}": f"{cfg['apertura_max_s']:g}",
        "{{APERTURA_FINAL}}": f"{cfg['apertura_final_pct']:g}",
        "{{MOMENTOS_MIN}}": str(cfg["momentos_min"]), "{{MOMENTOS_MAX}}": str(cfg["momentos_max"]),
        "{{MOMENTO_MIN}}": f"{cfg['momento_min_s']:g}", "{{LENTA_MAX_S}}": f"{cfg['camara_lenta_momento_max_s']:g}",
        "{{PAUSA_S}}": f"{cfg['pausa_s']:g}", "{{ZOOM_MAX}}": f"{cfg['zoom_max']:g}",
        "{{ZOOM_N}}": str(cfg["zoom_max_n"]), "{{SFX_N}}": str(cfg["sfx_max"]), "{{EFECTOS}}": efectos,
        "{{SENTIMIENTOS}}": ", ".join(SENTIMIENTOS), "{{TRATAMIENTOS}}": ", ".join(TRATAMIENTOS),
    }
    texto = m.group(1)
    for k, v in cambios.items():
        texto = texto.replace(k, v)
    return texto


def quitar_seccion(texto: str) -> str:
    """El prompt sin la sección de historia (los marcadores desaparecen)."""
    return RE_SECCION.sub("", texto)


def esquema_gemini() -> dict:
    """`historia` en el `responseSchema` de Gemini (subconjunto de OpenAPI): sentimiento y tratamiento de lista cerrada."""
    numero = {"type": "NUMBER"}
    return {"type": "OBJECT", "nullable": True, "properties": {
        "apertura": {"type": "OBJECT", "properties": {"t0": numero, "t1": numero}, "required": ["t0", "t1"]},
        "momentos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "t0": numero, "t1": numero,
            "sentimiento": {"type": "STRING", "enum": list(SENTIMIENTOS)},
            "tratamiento": {"type": "STRING", "enum": list(TRATAMIENTOS)},
            "sfx": {"type": "STRING", "nullable": True}},
            "required": ["t0", "t1", "sentimiento", "tratamiento", "sfx"]}}},
        "required": ["apertura", "momentos"]}


# ------------------------------------------------------------------ señales del video (sin IA y segundo proveedor)

def escenas_scenedetect(ruta: Path, inicio: float, fin: float) -> list[float] | None:
    """Cambios de escena con PySceneDetect (`ContentDetector`) si está instalado; None si no (o si falla)."""
    try:
        from scenedetect import ContentDetector, detect
    except ImportError:
        return None
    try:
        pares = detect(str(ruta), ContentDetector(), start_time=float(inicio), end_time=float(fin))
        cortes = [float(p[0].get_seconds()) for p in pares[1:]]
    except Exception:  # noqa: BLE001 (cualquier fallo de la librería: se usa el filtro scene de ffmpeg)
        return None
    return sorted({round(c, 3) for c in cortes if inicio < c < fin})


def escenas_ffmpeg(ruta: Path, inicio: float, fin: float, umbral: float = 0.3) -> list[float]:
    """Respaldo: el filtro `scene` de ffmpeg sobre el recorte."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{inicio:.3f}", "-t", f"{fin - inicio:.3f}",
                        "-i", str(ruta), "-an", "-vf", f"select='gt(scene,{umbral:g})',showinfo", "-f", "null", "-"],
                       check=True, capture_output=True, text=True, errors="replace",
                       timeout=plazo.tiempo_para(120))
    ts = {round(inicio + float(t), 3) for t in re.findall(r"pts_time:(\d+(?:\.\d+)?)", r.stderr)}
    return sorted(t for t in ts if inicio < t < fin)


def rms_por_ventana(ruta: Path, inicio: float, fin: float, paso: float = PASO_S) -> list[float]:
    """RMS del audio por ventana de `paso` s dentro del recorte (vacío si no hay audio)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{inicio:.3f}", "-t",
                        f"{fin - inicio:.3f}", "-i", str(ruta), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       capture_output=True, timeout=plazo.tiempo_para(120))
    if r.returncode != 0 or not r.stdout:
        return []
    x = np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], dtype="<i2").astype(np.float64)
    por = int(8000 * paso)
    return [float(np.sqrt(np.mean(x[i:i + por] ** 2))) for i in range(0, len(x), por)]


def senales(ruta: Path, inicio: float, fin: float) -> dict:
    """{"inicio", "paso", "rms", "escenas", "detector"} del recorte (tiempos de las escenas en segundos del archivo)."""
    esc = escenas_scenedetect(ruta, inicio, fin)
    detector = "scenedetect"
    if esc is None:
        detector, esc = "ffmpeg", escenas_ffmpeg(ruta, inicio, fin)
    return {"inicio": float(inicio), "paso": PASO_S, "rms": rms_por_ventana(ruta, inicio, fin),
            "escenas": esc, "detector": detector}


# ------------------------------------------------------------------ eslabón 3: reglas sin IA

def _techo3(x: float) -> float:
    return math.ceil(x * 1000 - 1e-9) / 1000


def _piso3(x: float) -> float:
    return math.floor(x * 1000 + 1e-9) / 1000


def _zscores(rms: list[float]) -> np.ndarray:
    x = np.asarray(rms, dtype=np.float64)
    if x.size == 0 or float(x.std()) == 0.0:
        return np.zeros(x.size)
    return (x - x.mean()) / x.std()


def _candidatos(sen: dict, ini: float, fin: float) -> list[dict]:
    """Picos de volumen y cambios de escena del recorte, del más fuerte al más débil (empate: el más temprano).
    Cada uno: {t, fuerza, corto (pico aislado), tipo}."""
    paso = float(sen.get("paso", PASO_S))
    z = _zscores(list(sen.get("rms") or []))
    out = []
    for i in range(len(z)):
        izq = z[i - 1] if i > 0 else -9.0
        der = z[i + 1] if i + 1 < len(z) else -9.0
        if z[i] > 0.5 and z[i] >= izq and z[i] >= der:
            out.append({"t": ini + (i + 0.5) * paso, "fuerza": float(z[i]),
                        "corto": bool(z[i] - max(izq, der) >= 0.5), "tipo": "pico"})
    for e in sen.get("escenas") or []:
        e = float(e)
        if ini + 0.4 < e < fin - 0.4:
            i = min(len(z) - 1, max(0, int((e - ini) / paso))) if len(z) else -1
            out.append({"t": e, "fuerza": 1.5 + (max(float(z[i]), 0.0) if i >= 0 else 0.0), "corto": False,
                        "tipo": "escena"})
    out.sort(key=lambda c: (-round(c["fuerza"], 6), c["t"]))
    return out


def _ventana(centro: float, largo: float, ini: float, fin: float) -> tuple[float, float]:
    t0 = min(max(centro - largo / 2, ini), max(ini, fin - largo))
    return _techo3(t0), _piso3(min(fin, t0 + largo))


_SENTIMIENTO_DE = {"sfx": "sorpresa", "camara_lenta": "asombro", "pausa": "tension", "zoom_suave": "tension",
                   "ninguno": "alivio"}


def plan_sin_ia(sen: dict, recorte: tuple[float, float], rango_duracion: tuple[float, float], *,
                sfx_golpe: str | None = None, ficha: dict | None = None, cfg: dict | None = None,
                sfx_ids=None) -> dict | None:
    """Plan por reglas con las señales del video (determinista: el mismo video da el mismo plan).

    Apertura = 2.0 s alrededor del pico más alto del último 40 %. De 2 a 3 momentos en los picos y cambios de
    escena más fuertes: el pico corto más cercano al final va en cámara lenta; el más fuerte de los demás lleva
    el efecto golpe (si está permitido y hay efecto) o zoom suave; el instante antes del remate lleva la pausa si
    cabe. Devuelve el primer plan que valida (si no cabe la pausa o la cámara lenta, se prueba sin ellas) o None."""
    cfg = {**DEFAULTS, **(cfg or {})}
    ini, fin = float(recorte[0]), float(recorte[1])
    d = fin - ini
    if sfx_golpe is not None and (sfx_prohibido(ficha, cfg) or sfx_golpe not in set(sfx_ids or [sfx_golpe])):
        sfx_golpe = None
    cands = _candidatos(sen, ini, fin)
    # el remate: el pico (o escena) más fuerte del último 40 %
    desde = fin - d * cfg["apertura_final_pct"] / 100
    finales = [c for c in cands if c["t"] >= desde]
    tr = finales[0]["t"] if finales else fin - 1.0
    largo_ap = min(APERTURA_SIN_IA_S, cfg["apertura_max_s"], d * cfg["apertura_final_pct"] / 100)
    a0 = max(_techo3(desde), _techo3(min(tr - largo_ap / 2, fin - largo_ap)))
    a1 = _piso3(min(fin, a0 + largo_ap))
    apertura = {"t0": a0, "t1": a1}

    elegidos: list[dict] = []
    for c in cands:
        if not (ini + 0.8 <= c["t"] <= fin - 0.8):
            continue
        if all(abs(c["t"] - e["t"]) >= SEPARACION_SIN_IA_S - TOL for e in elegidos):
            elegidos.append(c)
        if len(elegidos) == 3:
            break
    for frac in (0.3, 0.6, 0.45):                         # sin señales: momentos repartidos a lo largo del recorte
        if len(elegidos) >= 2:
            break
        t = ini + d * frac
        if all(abs(t - e["t"]) >= SEPARACION_SIN_IA_S - TOL for e in elegidos):
            elegidos.append({"t": t, "fuerza": 0.0, "corto": False, "tipo": "relleno"})
    elegidos.sort(key=lambda c: c["t"])

    def construir(con_lenta: bool, con_pausa: bool) -> dict:
        trat: dict[int, str] = {}
        lenta = None
        if con_lenta:
            cortos = [i for i, c in enumerate(elegidos) if c["corto"] and c["t"] >= ini + 0.6 * d]
            if cortos:
                lenta = min(cortos, key=lambda i: (-round(elegidos[i]["fuerza"], 6), elegidos[i]["t"]))
                trat[lenta] = "camara_lenta"
        resto = sorted((i for i in range(len(elegidos)) if i != lenta),
                       key=lambda i: (-round(elegidos[i]["fuerza"], 6), elegidos[i]["t"]))
        zooms = 0
        for k, i in enumerate(resto):
            if k == 0 and sfx_golpe is not None:
                trat[i] = "sfx"
            elif zooms < cfg["zoom_max_n"]:
                trat[i] = "zoom_suave"
                zooms += 1
            else:
                trat[i] = "ninguno"
        momentos = []
        for i, c in enumerate(elegidos):
            tt = trat.get(i, "ninguno")
            t0, t1 = _ventana(c["t"], VENTANA_LENTA_S if tt == "camara_lenta" else VENTANA_SIN_IA_S, ini, fin)
            momentos.append({"t0": t0, "t1": t1, "sentimiento": _SENTIMIENTO_DE[tt], "tratamiento": tt,
                             "sfx": sfx_golpe if tt == "sfx" else None})
        if con_pausa and len(momentos) < cfg["momentos_max"] and cfg["pausa_max"] >= 1:
            for k in range(0, 30):                        # el instante antes del remate, corriéndose si estorba
                e = _piso3(tr - 0.2 - 0.1 * k)
                p0 = _techo3(e - VENTANA_PAUSA_S)
                if p0 < ini:
                    break
                if all(e <= mo["t0"] + TOL or p0 >= mo["t1"] - TOL for mo in momentos):
                    momentos.append({"t0": p0, "t1": e, "sentimiento": "tension", "tratamiento": "pausa",
                                     "sfx": None})
                    break
        momentos.sort(key=lambda mo: (mo["t0"], mo["t1"]))
        for i in range(1, len(momentos)):                 # sin solapes: el siguiente empieza donde termina el anterior
            if momentos[i]["t0"] < momentos[i - 1]["t1"]:
                momentos[i] = {**momentos[i], "t0": momentos[i - 1]["t1"]}
        return {"apertura": dict(apertura), "momentos": momentos}

    ids = [sfx_golpe] if sfx_golpe else []
    for con_lenta, con_pausa in ((True, True), (True, False), (False, False)):
        plan = construir(con_lenta, con_pausa)
        ok, _ = validar_plan(plan, (ini, fin), rango_duracion, ids, ficha, cfg)
        if ok:
            return plan
    return None


# ------------------------------------------------------------------ eslabón 2: cliente chat/completions genérico

def transporte_urllib(url: str, encabezados: dict, cuerpo: bytes, tiempo: float) -> tuple[int, str]:
    """POST real. Devuelve (estado_http, texto). Nunca deja encabezados (la clave) en un error."""
    req = urllib.request.Request(url, data=cuerpo, headers=encabezados, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:  # noqa: S310 (la URL la fija la config, https)
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErrorHistoria(f"fallo de conexión con el segundo proveedor: {type(e).__name__}") from None


def fotogramas_jpeg(ruta: Path, inicio: float, fin: float, n: int = TOPE_SEGUNDO_FOTOGRAMAS,
                    lado: int = LADO_MAX_FOTOGRAMA) -> list[bytes]:
    """`n` fotogramas JPEG repartidos por el recorte, de `lado` px o menos en el lado largo."""
    out = []
    for i in range(n):
        t = inicio + (fin - inicio) * (i + 0.5) / n
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(ruta),
                            "-frames:v", "1", "-vf",
                            f"scale='if(gt(iw,ih),min({lado},iw),-2)':'if(gt(iw,ih),-2,min({lado},ih))'",
                            "-f", "image2pipe", "-c:v", "mjpeg", "-"],
                           capture_output=True, timeout=plazo.tiempo_para(60))
        if r.returncode == 0 and r.stdout:
            out.append(r.stdout)
    return out


_usadas_segundo = [0]


def reiniciar_contador() -> None:
    _usadas_segundo[0] = 0


def cuerpo_segundo(cfg_seg: dict, instr: str, fotogramas: list[bytes], lineas: list, sen: dict | None,
                   recorte: tuple[float, float]) -> bytes:
    """Petición chat/completions: la instrucción, 6 fotogramas JPEG (base64), las líneas transcritas y las señales."""
    contexto = {"recorte": {"inicio": recorte[0], "fin": recorte[1]},
                "lineas": [{"t0": _tiempos(ln)[0], "t1": _tiempos(ln)[1]} for ln in lineas or []],
                "senales": {k: (sen or {}).get(k) for k in ("rms", "escenas") if sen}}
    partes = [{"type": "text", "text": "Contexto del clip (JSON): " + json.dumps(contexto)}]
    for jpg in fotogramas[:TOPE_SEGUNDO_FOTOGRAMAS]:
        partes.append({"type": "image_url",
                       "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")}})
    cuerpo = {"model": cfg_seg["modelo"], "temperature": 0.2, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": instr + "\n\nResponde únicamente con el JSON del plan."},
                           {"role": "user", "content": partes}]}
    return json.dumps(cuerpo).encode("utf-8")


def llamar_segundo(cfg: dict, *, recorte, ids_sfx, prohibe_sfx, ruta_video=None, lineas=None, sen=None,
                   entorno=None, transporte=None, fotogramas=None, intentos: list[str]) -> dict | None:
    """El plan que devuelve el segundo proveedor (sin validar), o None. 429, 5xx y red son pasajeros; nunca imprime
    el secreto; el tiempo es el de la config recortado a lo que quede del plazo."""
    seg = cfg["segundo"]
    nombre = seg.get("nombre") or "segundo"
    if seg.get("activo") is not True:
        intentos.append(f"segundo: apagado ({seg.get('motivo') or 'sin motivo'})")
        return None
    env = os.environ if entorno is None else entorno
    clave = env.get(seg.get("secreto") or "", "")
    if not (seg.get("url") and seg.get("modelo") and seg.get("secreto")) or not clave:
        intentos.append(f"segundo: sin secreto o sin configurar ({seg.get('secreto') or 'sin nombre'})")
        return None
    if _usadas_segundo[0] >= int(seg.get("max_por_ciclo", 1)):
        intentos.append("segundo: tope de llamadas del ciclo")
        return None
    try:
        tiempo = min(float(seg.get("tiempo_s", 60)), plazo.restante() - plazo.MARGEN_S)
    except Exception:  # noqa: BLE001
        tiempo = float(seg.get("tiempo_s", 60))
    if tiempo < 5:
        intentos.append("segundo: sin tiempo (plazo del ciclo)")
        return None
    _usadas_segundo[0] += 1
    try:
        fotos = fotogramas if fotogramas is not None else fotogramas_jpeg(Path(ruta_video), recorte[0], recorte[1])
    except (subprocess.SubprocessError, OSError, TypeError) as e:
        intentos.append(f"segundo: no se pudieron sacar fotogramas ({type(e).__name__})")
        return None
    cuerpo = cuerpo_segundo(seg, instruccion(cfg, ids_sfx, prohibe_sfx), fotos, lineas or [], sen, recorte)
    encabezados = {"Content-Type": "application/json", "Authorization": f"Bearer {clave}"}
    try:
        estado, texto = (transporte or transporte_urllib)(seg["url"], encabezados, cuerpo, tiempo)
    except ErrorHistoria as e:
        intentos.append(f"{nombre}: {e} (pasajero)")
        return None
    if estado == 429 or 500 <= estado < 600:
        intentos.append(f"{nombre}: HTTP {estado} (pasajero)")
        return None
    if estado != 200:
        intentos.append(f"{nombre}: HTTP {estado}")
        return None
    try:
        contenido = json.loads(texto)["choices"][0]["message"]["content"]
        plan = json.loads(contenido) if isinstance(contenido, str) else contenido
    except (ValueError, KeyError, IndexError, TypeError):
        intentos.append(f"{nombre}: HTTP 200 sin un plan JSON legible")
        return None
    if not isinstance(plan, dict):
        intentos.append(f"{nombre}: HTTP 200 pero el plan no es un objeto")
        return None
    intentos.append(f"{nombre}: HTTP 200")
    return plan


# ------------------------------------------------------------------ catálogo de efectos (verificados)

def efecto_verificado(raiz, sfx_id: str, sha: str | None) -> tuple[bool, str]:
    """El efecto está en `marca/sfx/efectos.yml` como verificado, con LICENCIA.md y el sha256 del plan."""
    import yaml
    r = raiz_repo(raiz)
    ruta = r / "marca" / "sfx" / "efectos.yml"
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False, "no se pudo leer marca/sfx/efectos.yml"
    e = next((x for x in (datos.get("efectos") or []) if isinstance(x, dict) and x.get("id") == sfx_id), None)
    if e is None:
        return False, f"el efecto {sfx_id!r} no está en efectos.yml"
    if e.get("estado") != "verificada":
        return False, f"el efecto {sfx_id!r} está {e.get('estado')!r}, no verificado"
    if not (r / "marca" / "sfx" / str(sfx_id) / "LICENCIA.md").is_file():
        return False, f"falta marca/sfx/{sfx_id}/LICENCIA.md"
    if not e.get("sha256") or str(e["sha256"]).lower() != str(sha or "").lower():
        return False, f"el sha256 del plan no coincide con el de efectos.yml para {sfx_id!r}"
    return True, f"efecto {sfx_id} verificado (CC0)"


# ------------------------------------------------------------------ la cadena

@dataclass
class Planificacion:
    plan: dict | None
    proveedor: str | None
    intentos: list[str] = field(default_factory=list)
    motivo: str | None = None          # por qué no hay plan (sólo cuando plan es None)

    def resumen(self, tramos: list[Tramo] | None = None) -> dict:
        """Lo que guarda el plan de la toma en `historia`."""
        if self.plan is None:
            return {"proveedor": None, "motivo": self.motivo or "sin plan", "intentos": list(self.intentos)}
        return {"proveedor": self.proveedor, "plan": self.plan,
                "mapa": [t.a_dict() for t in tramos] if tramos is not None else None,
                "intentos": list(self.intentos)}


def planificar(*, raiz=None, ficha: dict | None, recorte: tuple[float, float], rango_duracion: tuple[float, float],
               sfx: dict | None = None, datos_gemini: dict | None = None, ruta_video: Path | None = None,
               lineas: list | None = None, sen: dict | None = None, cfg: dict | None = None,
               entorno=None, transporte=None, fotogramas=None) -> Planificacion:
    """Gemini (el plan que vino en la misma llamada de los ojos) → segundo proveedor (apagado) → reglas sin IA.
    El primero que valida gana. Si ninguno valida, `plan` es None y la toma sale como siempre."""
    cfg = cfg or cargar_config(raiz)
    intentos: list[str] = []
    if cfg.get("activo") is not True:
        return Planificacion(None, None, ["historia: apagada en config/historia.yml"], "apagada en config/historia.yml")
    sfx = sfx or {}
    prohibe = sfx_prohibido(ficha, cfg)
    ids = [] if prohibe else sorted(sfx)

    def valida(plan, quien: str) -> bool:
        ok, motivos = validar_plan(plan, recorte, rango_duracion, ids, ficha, cfg)
        if not ok:
            intentos.append(f"{quien}: plan no válido: " + "; ".join(motivos[:3]))
        return ok

    plan_g = (datos_gemini or {}).get("historia")
    if plan_g is not None:
        if valida(plan_g, "gemini"):
            intentos.append("gemini: plan válido")
            return Planificacion(plan_g, "gemini", intentos)
    else:
        intentos.append("gemini: sin plan de historia en la respuesta")

    plan_s = llamar_segundo(cfg, recorte=recorte, ids_sfx=ids, prohibe_sfx=prohibe, ruta_video=ruta_video,
                            lineas=lineas, sen=sen, entorno=entorno, transporte=transporte, fotogramas=fotogramas,
                            intentos=intentos)
    if plan_s is not None and valida(plan_s, cfg["segundo"].get("nombre") or "segundo"):
        return Planificacion(plan_s, "segundo", intentos)

    if sen is None and ruta_video is not None:
        try:
            sen = senales(Path(ruta_video), recorte[0], recorte[1])
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            intentos.append(f"sin-ia: no se pudieron leer las señales del video ({type(e).__name__})")
    if sen is None:
        return Planificacion(None, None, intentos, "ninguna fuente de plan valida (sin señales del video)")
    golpe = None
    if not prohibe:
        golpe = next((sfx[i]["id"] for i in sorted(sfx) if sfx[i].get("tipo") == "golpe"), None)
    plan = plan_sin_ia(sen, recorte, rango_duracion, sfx_golpe=golpe, ficha=ficha, cfg=cfg, sfx_ids=ids)
    if plan is not None:
        intentos.append(f"sin-ia: plan válido ({sen.get('detector', 'señales dadas')})")
        return Planificacion(plan, "sin-ia", intentos)
    intentos.append("sin-ia: ningún plan por reglas cabe en el rango de duración")
    return Planificacion(None, None, intentos, "ningún eslabón dio un plan válido")


# ------------------------------------------------------------------ la orden de ffmpeg (función pura)

def _g(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def _cadena_zoom(z0: float, z1: float, cuadros: int, ancho: int, alto: int) -> str:
    """Zoom suave centrado de `z0` a `z1` en `cuadros` cuadros: zoompan sobre el cuadro ya escalado, sin franjas."""
    paso = (z1 - z0) / max(cuadros - 1, 1)
    return (f"zoompan=z='{_g(z0)}+{_g(paso)}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={ancho}x{alto}:fps={FPS}")


def orden_ffmpeg(*, salida: str, ancho: int, alto: int, video: dict, tramos: list[Tramo],
                 placas: list[tuple[str, int, int, float, float]], sfx: dict | None = None,
                 ganancia_sfx_db: float = -6.0, pausa_s: float = PAUSA_S) -> list[str]:
    """Una sola orden de ffmpeg para una toma con historia (función pura; no corre nada).

    `video`: {ruta, inicio, recorte {x, y, ancho, alto}, canales (1 = mono)}. `tramos`: el `mapa`. `placas`: [(PNG, x, y, t0, t1)] en
    tiempo de salida. `sfx`: {id: ruta} de los efectos que usan los tramos. Entradas: 0 = la apertura (`-ss`/`-t`
    sobre la fuente, para no acumular cuadros), 1 = el recorte, luego las placas y los efectos. Cada tramo se
    corta con `trim`/`atrim`; la cámara lenta es `setpts=2*PTS` + `atempo=0.5` antes de `fps=30`; la pausa clona el
    último cuadro con `tpad` y va en silencio; el zoom es suave y centrado; todo se une con `concat` sin fundidos; los
    efectos entran con `adelay` + `amix=normalize=0`."""
    ap, principales = tramos[0], tramos[1:]
    inicio = float(video["inicio"])
    duracion = duracion_salida(tramos)
    r = video["recorte"]
    encuadre = (f"crop={r['ancho']}:{r['alto']}:{r['x']}:{r['y']},scale={ancho}:{alto}:flags=lanczos,setsar=1")
    fmt_base = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    # una fuente mono se duplica a los dos canales sin bajarle volumen (el upmix por defecto resta 3 dB a cada canal)
    audio_fmt = ("pan=stereo|c0=c0|c1=c0," if int(video.get("canales") or 2) == 1 else "") + fmt_base
    dur_rec = max(tr.f1 for tr in principales if tr.velocidad != 0.0) - inicio
    entradas = ["-ss", _g(ap.f0), "-t", _g(ap.f1 - ap.f0), "-i", video["ruta"],
                "-ss", _g(inicio), "-t", _g(dur_rec), "-i", video["ruta"]]
    filtros: list[str] = []
    etiquetas: list[str] = []
    for i, tr in enumerate(tramos):
        v, a = f"v{i}", f"a{i}"
        # cada tramo dura un número exacto de cuadros (el de la línea de tiempo de salida redondeada) y su audio las
        # mismas muestras: así el concat no corre el audio respecto del video de un tramo a otro
        n = max(1, int(round(tr.s1 * FPS)) - int(round(tr.s0 * FPS)))
        muestras = n * (48000 // FPS)
        if i == 0:
            cabeza = f"[0:v]trim=duration={_g(tr.dur)},setpts=PTS-STARTPTS,fps={FPS}"
            audio = f"[0:a]atrim=duration={_g(tr.dur)},asetpts=PTS-STARTPTS,{audio_fmt}"
        elif tr.velocidad == 0.0:
            ult = max(0.0, tr.f0 - inicio - 0.034)
            cabeza = (f"[1:v]trim=start={_g(ult)}:end={_g(tr.f0 - inicio)},setpts=PTS-STARTPTS,fps={FPS},"
                      f"trim=end_frame=1,tpad=stop_mode=clone:stop={n - 1}")
            audio = f"anullsrc=r=48000:cl=stereo,atrim=duration={_g(tr.dur)},asetpts=PTS-STARTPTS,{fmt_base}"
        else:
            f0, f1 = tr.f0 - inicio, tr.f1 - inicio
            lenta = tr.velocidad == 0.5
            cabeza = (f"[1:v]trim=start={_g(f0)}:end={_g(f1)},setpts="
                      + ("2*(PTS-STARTPTS)" if lenta else "PTS-STARTPTS") + f",fps={FPS}")
            audio = (f"[1:a]atrim=start={_g(f0)}:end={_g(f1)},asetpts=PTS-STARTPTS"
                     + (",atempo=0.5" if lenta else "") + f",{audio_fmt}")
        cadena = f"{cabeza},{encuadre}"
        if tr.zoom:
            cadena += "," + _cadena_zoom(tr.zoom[0], tr.zoom[1], n, ancho, alto)
        filtros.append(f"{cadena},tpad=stop_mode=clone:stop=2,trim=end_frame={n},setpts=PTS-STARTPTS,"
                       f"format=yuv420p[{v}]")
        filtros.append(f"{audio},apad=whole_len={muestras},atrim=end_sample={muestras},asetpts=PTS-STARTPTS[{a}]")
        etiquetas.append(f"[{v}][{a}]")
    filtros.append("".join(etiquetas) + f"concat=n={len(tramos)}:v=1:a=1[vc][ac]")
    d = _g(duracion)
    actual = "vc"
    for i, (ruta, x, y, t0, t1) in enumerate(placas):
        entradas += ["-loop", "1", "-framerate", str(FPS), "-t", d, "-i", ruta]
        sig = f"c{i + 1}"
        filtros.append(f"[{actual}][{2 + i}:v]overlay={x}:{y}:enable='gte(t,{_g(t0)})*lt(t,{_g(t1)})'[{sig}]")
        actual = sig
    filtros.append(f"[{actual}]format=yuv420p[out]")
    mapa_audio = "[ac]"
    usados = [(tr, (sfx or {}).get(tr.sfx)) for tr in tramos if tr.sfx]
    if usados:
        base = 2 + len(placas)
        mezcla = ["[ac]"]
        for k, (tr, ruta) in enumerate(usados):
            if not ruta:
                raise ValueError(f"el efecto {tr.sfx!r} no tiene archivo")
            entradas += ["-i", str(ruta)]
            ms = int(round(tr.s0 * 1000))
            filtros.append(f"[{base + k}:a]{fmt_base},atrim=duration={_g(tr.dur)},volume={_g(ganancia_sfx_db)}dB,"
                           f"adelay={ms}|{ms}[e{k}]")
            mezcla.append(f"[e{k}]")
        filtros.append("".join(mezcla) + f"amix=inputs={len(mezcla)}:normalize=0:duration=first[aud]")
        mapa_audio = "[aud]"
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entradas,
            "-filter_complex", ";".join(filtros), "-map", "[out]", "-map", mapa_audio,
            "-t", d, "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart", salida]

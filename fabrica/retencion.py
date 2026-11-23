"""Prueba de retención (`config/prueba-retencion.yml`): toma corta (10-14 s, gancho en el primer segundo) contra control.

Las funciones de asignación y de ajuste de largo son puras. `python -m fabrica.retencion --informe` lee
`marcador.csv` y compara los dos brazos por red (método: procedimientos/prueba-retencion.md).
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import date, datetime

from .config import cargar_yaml, raiz_repo
from .registro import ZONA

RUTA_CFG = "config/prueba-retencion.yml"
CORTA, CONTROL = "corta", "control"
GANA_PCT_VISTO = 20.0        # el ganador necesita un % visto al menos 20 % mayor...
CAE_VISTAS_24H = 10.0        # ...y que las vistas a 24 h no caigan más de 10 %


def cargar(raiz=None) -> dict | None:
    """La config de la prueba, o None si el archivo no existe."""
    try:
        return cargar_yaml(RUTA_CFG, raiz)
    except FileNotFoundError:
        return None


def _fecha(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def brazo(numero, cfg, hoy: date | None = None) -> str | None:
    """`"corta"` (número par) o `"control"` (impar); None si la prueba está inactiva, no hay config o aún no llega `inicio`.
    `hoy` por omisión es la fecha de Guatemala."""
    if not isinstance(cfg, dict) or cfg.get("activa") is not True:
        return None
    inicio = _fecha(cfg.get("inicio"))
    hoy = hoy or datetime.now(ZONA).date()
    if inicio is not None and hoy < inicio:
        return None
    try:
        n = int(numero)
    except (TypeError, ValueError):
        return None
    return CORTA if n % 2 == 0 else CONTROL


def etiqueta(cfg: dict, el_brazo: str) -> str:
    """Valor de la columna `prueba` del marcador: `corta-10-14:corta`."""
    return f"{cfg['nombre']}:{el_brazo}"


def rango_corta(cfg: dict, rango_ficha: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """Rango de duración del brazo corta, acotado al de la ficha; None si no se cruzan (la toma va a control)."""
    lo, hi = (float(x) for x in cfg["brazos"][CORTA]["duracion_s"])
    if rango_ficha is not None:
        lo, hi = max(lo, float(rango_ficha[0])), min(hi, float(rango_ficha[1]))
    return (lo, hi) if lo <= hi else None


def ajustar_recorte(inicio: float, fin: float, duracion_fuente: float,
                    rango: tuple[float, float]) -> tuple[float, float] | None:
    """(inicio, fin) dentro de `rango` sin estirar el tiempo. Si pasa del máximo se recorta el final; si queda corto se
    toma más fuente (primero hacia delante, luego hacia atrás). None si la fuente dura menos que el mínimo."""
    lo, hi = rango
    if duracion_fuente < lo - 1e-6:
        return None
    largo = fin - inicio
    if largo > hi + 1e-6:
        fin = inicio + hi
    elif largo < lo - 1e-6:
        fin = min(inicio + lo, duracion_fuente)
        inicio = max(0.0, fin - lo)
    return round(inicio, 3), round(fin, 3)


# ------------------------------------------------------------------ informe

REDES = ("shorts", "tiktok", "reels")


def _num(v) -> float | None:
    try:
        x = float(str(v).strip().replace(",", "."))
    except ValueError:
        return None
    return x if x == x else None


def _brazo_de(prueba: str, nombre: str) -> str | None:
    p, _, b = (prueba or "").strip().partition(":")
    return b if p == nombre and b in (CORTA, CONTROL) else None


def resumen(filas: list[dict], cfg: dict) -> dict:
    """{red: {brazo: {n, n_visto, pct_visto, vistas_24h, faltan}}} con las filas del marcador."""
    minimo = int(cfg.get("min_tomas_por_brazo", 10))
    acum: dict = {}
    for f in filas:
        b = _brazo_de(f.get("prueba", ""), str(cfg["nombre"]))
        if b is None:
            continue
        a = acum.setdefault((f.get("plataforma") or "").strip(), {}).setdefault(b, {"n": 0, "pct": [], "v24": []})
        a["n"] += 1
        visto, dur, v24 = _num(f.get("visto_medio_s")), _num(f.get("duracion_s")), _num(f.get("vistas_24h"))
        if visto is not None and dur:
            a["pct"].append(100.0 * visto / dur)
        if v24 is not None:
            a["v24"].append(v24)
    salida: dict = {}
    for red, brs in acum.items():
        for b in (CORTA, CONTROL):
            a = brs.get(b, {"n": 0, "pct": [], "v24": []})
            salida.setdefault(red, {})[b] = {
                "n": a["n"], "n_visto": len(a["pct"]),
                "pct_visto": statistics.fmean(a["pct"]) if a["pct"] else None,
                "vistas_24h": statistics.fmean(a["v24"]) if a["v24"] else None,
                "faltan": max(0, minimo - len(a["pct"])),
            }
    return salida


def veredicto(res: dict) -> str:
    """`corta`, `control` o `seguir` para los datos de una red (`res[brazo]`). Sin datos o con faltantes, `seguir`."""
    c, k = res.get(CORTA), res.get(CONTROL)
    if not c or not k or c["faltan"] or k["faltan"] or not c["pct_visto"] or not k["pct_visto"]:
        return "seguir"
    v24 = (c["vistas_24h"], k["vistas_24h"])
    caida = (100.0 * (1 - v24[0] / v24[1]) if v24[1] else 0.0) if None not in v24 else None
    corta_gana = c["pct_visto"] >= k["pct_visto"] * (1 + GANA_PCT_VISTO / 100) and (caida is None or caida <= CAE_VISTAS_24H)
    if corta_gana:
        return CORTA
    if k["pct_visto"] >= c["pct_visto"] * (1 + GANA_PCT_VISTO / 100):
        caida_k = (100.0 * (1 - v24[1] / v24[0]) if v24[0] else 0.0) if None not in v24 else None
        if caida_k is None or caida_k <= CAE_VISTAS_24H:
            return CONTROL
    return "seguir"


def informe(raiz=None) -> str:
    raiz = raiz_repo(raiz)
    cfg = cargar(raiz)
    if not cfg:
        return "Sin prueba de retención (falta config/prueba-retencion.yml)."
    with open(raiz / "marcador.csv", newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    r = resumen(filas, cfg)
    lineas = [f"Prueba {cfg['nombre']} (activa: {'sí' if cfg.get('activa') else 'no'}, desde {cfg.get('inicio')}; "
              f"mínimo {cfg.get('min_tomas_por_brazo', 10)} tomas con dato por brazo)",
              "red     brazo    tomas  con visto  % visto  vistas 24h  estado"]
    if not r:
        lineas.append("(aún no hay filas del marcador con esta prueba)")
    for red in sorted(r, key=lambda x: (REDES.index(x) if x in REDES else 9, x)):
        v = veredicto(r[red])
        for b in (CORTA, CONTROL):
            d = r[red][b]
            pct = f"{d['pct_visto']:.1f} %" if d["pct_visto"] is not None else "—"
            v24 = f"{d['vistas_24h']:.0f}" if d["vistas_24h"] is not None else "—"
            estado = f"faltan {d['faltan']}" if d["faltan"] else "ok"
            lineas.append(f"{red:<7} {b:<8} {d['n']:>5}  {d['n_visto']:>9}  {pct:>7}  {v24:>10}  {estado}")
        lineas.append(f"{red:<7} veredicto: {v}")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fabrica.retencion", description=__doc__)
    p.add_argument("--informe", action="store_true", help="tabla por red y brazo desde marcador.csv")
    a = p.parse_args(argv)
    if not a.informe:
        p.print_help()
        return 2
    print(informe())
    return 0


if __name__ == "__main__":
    sys.exit(main())

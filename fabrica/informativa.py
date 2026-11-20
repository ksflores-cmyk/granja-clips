"""Informativa tipográfica: una toma sin video armada con una entrada de `informativas.yml`.

Camino de N2 (`componer` sin `fuente_video`), ficha `propio-tipografico` y puerta de estilo completa.
Lleva una pista de música CC0 (`editar.musica`) y movimiento de entrada de texto; sin pista disponible no se compone.
La entrada sólo se marca `usada: true` cuando se publicó de verdad (modo vivo): `marcar_usada`.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .config import raiz_repo
from .editar import estilo, musica as mod_musica
from .editar.componer import Linea, componer
from .editar.lienzo import Toma
from .editar.marca import Marca
from .editar.toma import Resultado, fila_marcador, lapiz_de
from .registro import ZONA, contador_toma

FICHA_ID = "propio-tipografico"
DURACION_S = 11.0
CIERRE_PERDIDO_S = 0.5            # el último medio segundo es el cierre
NOMBRE_DATO = "EL DATO"
ETIQUETAS = ("#GTA6", "#Gaming", "#Clips")    # Instagram pide de 3 a 5; no llevan datos
CREDITO = "Fuente: Rockstar Games Support"
ESCENA = "INT. TU CONSOLA — NOCHE"
TOPE_PORCENTAJE = 0.10
DIAS_VENTANA = 7
CLAVES = ("id", "gancho", "palabra_lapiz", "titular", "datos", "fuente_url", "verificado_el",
          "vigente_desde", "vigente_hasta", "usada")


def ruta(raiz) -> Path:
    return raiz_repo(raiz) / "informativas.yml"


def cargar(raiz=None) -> list[dict]:
    r = ruta(raiz)
    if not r.exists():
        return []
    datos = yaml.safe_load(r.read_text(encoding="utf-8")) or {}
    lista = datos.get("informativas") if isinstance(datos, dict) else None
    return [e for e in (lista or []) if isinstance(e, dict)]


def _dt(valor) -> datetime | None:
    try:
        d = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=ZONA)


def vigente(entrada: dict, ahora: datetime) -> bool:
    """Sin usar y dentro de su vigencia (con zona horaria)."""
    if entrada.get("usada") is not False:
        return False
    desde, hasta = _dt(entrada.get("vigente_desde")), _dt(entrada.get("vigente_hasta"))
    return bool(desde and hasta and desde <= ahora <= hasta)


def elegir(raiz, ahora: datetime) -> dict | None:
    """La primera entrada vigente y sin usar, en el orden del archivo."""
    return next((e for e in cargar(raiz) if vigente(e, ahora)), None)


def _filas(raiz) -> list[dict]:
    import csv
    with open(raiz_repo(raiz) / "marcador.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def cabe_en_el_tope(raiz, ahora: datetime) -> tuple[bool, str]:
    """La informativa no pasa del 10 % de las tomas ya publicadas en los últimos 7 días (las programadas a futuro no cuentan), contando la nueva.

    Sin nada publicado en esos 7 días, la primera toma del canal puede ser informativa."""
    local = ahora.astimezone(ZONA)
    desde = (local - timedelta(days=DIAS_VENTANA)).date().isoformat()
    corte = (local.date().isoformat(), local.strftime("%H:%M"))
    tomas: dict[str, str] = {}
    for f in _filas(raiz):
        momento = (f.get("fecha") or "", (f.get("hora") or "00:00").strip())
        # sólo lo ya publicado: dentro de la ventana y con fecha y hora menores o iguales a ahora
        if momento[0] >= desde and momento <= corte and f.get("toma"):
            tomas[f["toma"]] = f.get("pilar") or ""
    total = len(tomas)
    if total == 0:
        return True, "primera toma del canal"
    inf = sum(1 for p in tomas.values() if p == "informativa")
    parte = (inf + 1) / (total + 1)
    if parte > TOPE_PORCENTAJE:
        return False, f"la informativa pasaría del {TOPE_PORCENTAJE:.0%} de lo publicado en 7 días ({inf + 1}/{total + 1})"
    return True, "ok"


def toma_dict(entrada: dict, numero: int) -> dict:
    """La toma tal como la espera `publicar.textos` (pilar informativa, titular y datos)."""
    return {"numero": numero, "gancho": entrada["gancho"], "pilar": "informativa",
            "titular": entrada["titular"], "datos": list(entrada["datos"]),
            "fuente_id": FICHA_ID, "campana": "", "duracion_s": DURACION_S, "etiquetas": list(ETIQUETAS)}


def _lineas(entrada: dict) -> list[Linea]:
    datos = [str(d).strip() for d in entrada["datos"]]
    tope = DURACION_S - CIERRE_PERDIDO_S
    paso = tope / len(datos)
    lineas = []
    for i, d in enumerate(datos):
        ln = Linea(t0=round(i * paso, 3), t1=round((i + 1) * paso, 3), nombre=NOMBRE_DATO, frase=d)
        if i == 0:
            ln.acotacion = "según el soporte de Rockstar"
        lineas.append(ln)
    return lineas


def armar(raiz, entrada: dict, ahora: datetime, salida: Path) -> Resultado:
    """Compone el MP4, corre la puerta de estilo completa y devuelve el Resultado (como `hacer_toma`)."""
    raiz = raiz_repo(raiz)
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    try:
        marca = Marca.cargar(raiz)
        numero = contador_toma(raiz)
        pista = mod_musica.elegir(raiz, numero)
        if pista is None:
            return Resultado(False, ["informativa: sin pista de música CC0 disponible "
                                     f"(GRANJA_MUSICA_DIR={mod_musica.carpeta() or ''})"])
        toma = Toma(numero=numero, gancho=entrada["gancho"].strip(), palabra_lapiz=entrada["palabra_lapiz"].strip(),
                    lapiz=lapiz_de(numero), escena=str(entrada.get("escena") or ESCENA).upper(), credito=CREDITO)
        mp4 = salida / f"toma-{numero:03d}-{entrada['id']}.mp4"
        plan_ruta = mp4.with_suffix(".plan.json")
        with tempfile.TemporaryDirectory(prefix="trabajo-informativa-") as trabajo:
            plan = componer(marca, toma, _lineas(entrada), mp4, duracion=DURACION_S, trabajo=Path(trabajo),
                            fuente_video=None, inicio=0.0, plan_salida=plan_ruta,
                            musica=pista, movimiento=True)
        puntos = estilo.revisar(mp4, plan, marca)
    except (ValueError, KeyError, subprocess.SubprocessError, OSError) as e:
        return Resultado(False, [f"informativa: {type(e).__name__}: {e}"])
    rojos = [f"puerta de estilo: {n}" + (f" ({det})" if det else "") for n, ok, det in puntos if not ok]
    if rojos:
        mp4.unlink(missing_ok=True)
        return Resultado(False, rojos, None, plan, None, None, puntos)
    plan_ruta.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    fila = fila_marcador(_Cand(), {"id": FICHA_ID, "campana": None}, toma, DURACION_S, ahora)
    fila["pilar"] = "informativa"
    return Resultado(True, [], mp4, plan, None, None, puntos, fila)


class _Cand:
    """Lo mínimo que `fila_marcador` lee de un candidato."""
    pilar = "informativa"


def marcar_usada(raiz, id_entrada: str) -> bool:
    """Cambia `usada: false` por `usada: true` sólo en la entrada indicada, sin reescribir el resto."""
    r = ruta(raiz)
    lineas = r.read_text(encoding="utf-8").split("\n")
    dentro, hecho = False, False
    for i, l in enumerate(lineas):
        m = re.match(r"^\s*- id:\s*(\S+)\s*$", l)
        if m:
            dentro = m.group(1).strip("\"'") == id_entrada
        if dentro and re.match(r"^\s+usada:\s*false\b", l):
            lineas[i] = re.sub(r"false", "true", l, count=1)
            hecho = True
            break
    if hecho:
        r.write_text("\n".join(lineas), encoding="utf-8")
    return hecho

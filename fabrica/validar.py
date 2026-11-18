"""Puerta de configuración: `python -m fabrica.validar [--raiz RUTA]`.

Sale 0 sin errores y 1 con errores. Los avisos no cambian el código de salida.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .config import ARCHIVOS, raiz_repo
from .registro import CABECERA_ENVIOS, CABECERA_MARCADOR

TIPOS = {"A", "B", "C", "D"}
ORIGENES = {"twitch", "youtube", "kick", "drive", "propio", "web"}
ESTADOS = {"vigente", "vencida", "pausada", "vetada"}
USOS = {"produccion", "muestras"}
PLATAFORMAS = {"shorts", "tiktok", "reels"}
SERVICIOS = {"shorts": "youtube", "tiktok": "tiktok", "reels": "instagram"}
MODOS = {"ensayo", "vivo"}
EXT_BINARIAS = {".ttf", ".otf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}
EXT_MEDIA = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".wav", ".mp3"}
IGNORAR_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv"}
RE_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RE_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
RE_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PATRONES_CLAVES = [
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
MARCA_FALSO = "EJEMPLO-FALSO"  # en pruebas/, una línea con esta marca se ignora


class Informe:
    def __init__(self) -> None:
        self.errores: list[str] = []
        self.avisos: list[str] = []

    def error(self, msg: str) -> None:
        self.errores.append(msg)

    def aviso(self, msg: str) -> None:
        self.avisos.append(msg)


def _es_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _cargar(raiz: Path, rel: str, inf: Informe):
    ruta = raiz / rel
    if not ruta.exists():
        inf.error(f"{rel}: no existe")
        return None
    try:
        dato = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        inf.error(f"{rel}: YAML inválido ({e})")
        return None
    if dato is None:
        inf.error(f"{rel}: está vacío o sólo tiene comentarios")
    return dato


def _claves(dato, rel: str, claves: list[str], inf: Informe) -> bool:
    if not isinstance(dato, dict):
        inf.error(f"{rel}: debe ser un diccionario")
        return False
    ok = True
    for c in claves:
        if c not in dato:
            inf.error(f"{rel}: falta la clave '{c}'")
            ok = False
    return ok


def _hoy() -> date:
    return datetime.now(ZoneInfo("America/Guatemala")).date()


def validar_temporada(t, inf: Informe) -> None:
    rel = "temporada.yml"
    if not _claves(t, rel, ["id", "nombre", "fecha_estreno", "ventana_estreno",
                            "palabras_evitar", "cupos", "pilares"], inf):
        return
    v = t["ventana_estreno"]
    if _claves(v, f"{rel}: ventana_estreno", ["inicio", "fin"], inf):
        try:
            if datetime.fromisoformat(str(v["inicio"])) >= datetime.fromisoformat(str(v["fin"])):
                inf.error(f"{rel}: ventana_estreno.inicio debe ser anterior a fin")
        except ValueError:
            inf.error(f"{rel}: ventana_estreno con fechas ISO inválidas")
    if not isinstance(t["palabras_evitar"], list) or not t["palabras_evitar"]:
        inf.error(f"{rel}: palabras_evitar debe ser una lista no vacía")
    _claves(t["cupos"], f"{rel}: cupos",
            ["por_dia_por_plataforma", "estreno_max_por_dia_por_plataforma"], inf)


def validar_fuentes(f, inf: Informe) -> None:
    rel = "fuentes.yml"
    if not isinstance(f, list):
        inf.error(f"{rel}: debe ser una lista de fichas")
        return
    if not f:
        inf.aviso(f"{rel}: sin fuentes: la fábrica no tendrá qué publicar")
        return
    vistos: set = set()
    for i, ficha in enumerate(f):
        if not isinstance(ficha, dict):
            inf.error(f"{rel}[{i}]: la ficha debe ser un diccionario")
            continue
        fid = ficha.get("id")
        et = f"{rel}[{fid or i}]"
        if not isinstance(fid, str) or not RE_ID.match(fid):
            inf.error(f"{et}: id debe estar en minúsculas con guiones")
        elif fid in vistos:
            inf.error(f"{et}: id repetido")
        else:
            vistos.add(fid)
        tipo = ficha.get("tipo")
        if tipo not in TIPOS:
            inf.error(f"{et}: tipo debe ser A, B, C o D")
        if ficha.get("origen") not in ORIGENES:
            inf.error(f"{et}: origen inválido (permitidos: {', '.join(sorted(ORIGENES))})")
        if ficha.get("estado") not in ESTADOS:
            inf.error(f"{et}: estado inválido (permitidos: {', '.join(sorted(ESTADOS))})")
        if ficha.get("uso") not in USOS:
            inf.error(f"{et}: uso debe ser produccion o muestras")
        if not isinstance(ficha.get("publicable"), bool):
            inf.error(f"{et}: publicable debe ser booleano")
        elif ficha.get("uso") == "muestras" and ficha["publicable"] is not False:
            inf.error(f"{et}: uso muestras exige publicable false")
        reglas = ficha.get("reglas")
        if not isinstance(reglas, dict):
            inf.error(f"{et}: falta reglas")
        else:
            d = reglas.get("duracion_s")
            if not (isinstance(d, list) and len(d) == 2 and all(_es_int(x) and 1 <= x <= 180 for x in d)
                    and d[0] <= d[1]):
                inf.error(f"{et}: reglas.duracion_s debe ser [min, max] enteros de 1 a 180 con min <= max")
            p = reglas.get("plataformas")
            if not (isinstance(p, list) and p and set(p) <= PLATAFORMAS):
                inf.error(f"{et}: reglas.plataformas debe ser una lista dentro de shorts, tiktok, reels")
        if tipo in ("A", "B"):
            url = ficha.get("permiso_url")
            if not (isinstance(url, str) and url.startswith("https://") and len(url) > 8):
                inf.error(f"{et}: tipo {tipo} exige permiso_url https")
            if not ficha.get("permiso_leido"):
                inf.error(f"{et}: tipo {tipo} exige permiso_leido")
        if tipo == "A":
            if ficha.get("tarifa_usd_por_mil") is None:
                inf.error(f"{et}: tipo A exige tarifa_usd_por_mil")
            if not ficha.get("vence"):
                inf.error(f"{et}: tipo A exige vence")
        if tipo == "D":
            if ficha.get("pilares") != ["informativa"]:
                inf.error(f"{et}: tipo D sólo con pilar informativa")
            if ficha.get("campana") is not None:
                inf.error(f"{et}: tipo D exige campana null")
        if ficha.get("estado") in ESTADOS and ficha.get("estado") != "vigente" \
                and ficha.get("publicable") is True:
            inf.aviso(f"{et}: estado {ficha['estado']} con publicable true; "
                      "la puerta de N3 exigirá estado vigente para descargar o publicar")
        vence = ficha.get("vence")
        if ficha.get("estado") == "vigente" and isinstance(vence, date) and vence < _hoy():
            inf.aviso(f"{et}: ficha vigente con vence {vence} ya pasado; debe pasar a vencida")


def validar_marca(m, inf: Informe) -> None:
    rel = "marca.yml"
    if not _claves(m, rel, ["colores", "fuente", "toma", "movimiento", "sonido",
                            "encabezado_max_palabras", "subtitulo_max_lineas"], inf):
        return
    if isinstance(m["colores"], dict):
        for nombre in ("papel", "tinta", "lapiz_rojo"):
            if not RE_HEX.match(str(m["colores"].get(nombre, ""))):
                inf.error(f"{rel}: colores.{nombre} debe ser hex #RRGGBB")
    else:
        inf.error(f"{rel}: colores debe ser un diccionario")
    if not (isinstance(m["fuente"], dict) and m["fuente"].get("familia") == "Courier Prime"):
        inf.error(f"{rel}: fuente.familia debe ser Courier Prime")
    c = m["toma"].get("contador") if isinstance(m["toma"], dict) else None
    if not (_es_int(c) and c >= 1):
        inf.error(f"{rel}: toma.contador debe ser entero >= 1")
    mov = m["movimiento"] if isinstance(m["movimiento"], dict) else {}
    if mov.get("transiciones") != "ninguna":
        inf.error(f"{rel}: movimiento.transiciones debe ser ninguna")
    if mov.get("zoom_automatico") is not False:
        inf.error(f"{rel}: movimiento.zoom_automatico debe ser false")
    son = m["sonido"] if isinstance(m["sonido"], dict) else {}
    if son.get("musica_de_biblioteca") is not False:
        inf.error(f"{rel}: sonido.musica_de_biblioteca debe ser false")
    e = m["encabezado_max_palabras"]
    if not (_es_int(e) and 1 <= e <= 6):
        inf.error(f"{rel}: encabezado_max_palabras debe ser entero de 1 a 6")
    s = m["subtitulo_max_lineas"]
    if not (_es_int(s) and 1 <= s <= 2):
        inf.error(f"{rel}: subtitulo_max_lineas debe ser entero de 1 a 2")
    validar_maqueta(m, inf)


CLAVES_MAQUETA = [
    "margen", "columna_dialogo", "fila_superior_y", "tam_fila", "gancho_y", "tam_gancho",
    "alto_linea_gancho", "espacio_tras_gancho", "regla_grosor", "espacio_tras_regla", "tam_escena",
    "alto_escena", "espacio_tras_escena", "tam_nombre", "alto_nombre", "tam_acotacion",
    "alto_acotacion", "tam_frase", "alto_linea_frase", "espacio_video_dialogo",
    "alto_dialogo_sin_video", "tam_corte", "corte_y", "tam_credito", "credito_y", "cierre_s",
    "opacidad_mosca", "grano", "circulo", "subrayado",
]
ESQUINAS = {"superior_derecha", "superior_izquierda"}
ESTILOS_FUENTE = ("regular", "bold", "italic", "bolditalic")


def _positivos(dato, ruta: str, inf: Informe) -> None:
    """Todo número de la maqueta debe ser positivo (recorre diccionarios y listas)."""
    rel = "marca.yml: maqueta"
    if isinstance(dato, dict):
        for k, v in dato.items():
            _positivos(v, f"{ruta}.{k}", inf)
    elif isinstance(dato, list):
        if not dato:
            inf.error(f"{rel}: {ruta} no puede ser una lista vacía")
        for i, v in enumerate(dato):
            _positivos(v, f"{ruta}[{i}]", inf)
    elif isinstance(dato, bool) or not isinstance(dato, (int, float)):
        inf.error(f"{rel}: {ruta} debe ser un número")
    elif dato <= 0:
        inf.error(f"{rel}: {ruta} debe ser positivo")


def validar_maqueta(m, inf: Informe) -> None:
    rel = "marca.yml"
    mosca = m.get("mosca")
    if not isinstance(mosca, dict):
        inf.error(f"{rel}: falta mosca")
    else:
        if mosca.get("esquina") not in ESQUINAS:
            inf.error(f"{rel}: mosca.esquina debe ser superior_derecha o superior_izquierda")
        if not (isinstance(mosca.get("texto"), str) and mosca["texto"].strip()):
            inf.error(f"{rel}: mosca.texto es obligatorio")
    maq = m.get("maqueta")
    if not isinstance(maq, dict):
        inf.error(f"{rel}: falta la sección maqueta")
        return
    for c in CLAVES_MAQUETA:
        if c not in maq:
            inf.error(f"{rel}: maqueta: falta la clave '{c}'")
    _positivos(maq, "maqueta", inf)
    col = maq.get("columna_dialogo")
    if isinstance(col, list) and len(col) == 2 and all(isinstance(x, (int, float)) for x in col):
        if col[0] >= col[1]:
            inf.error(f"{rel}: maqueta.columna_dialogo debe ser [inicio, fin] con inicio < fin")
    elif "columna_dialogo" in maq:
        inf.error(f"{rel}: maqueta.columna_dialogo debe ser [inicio, fin]")
    op = maq.get("opacidad_mosca")
    if isinstance(op, (int, float)) and not isinstance(op, bool) and op > 1:
        inf.error(f"{rel}: maqueta.opacidad_mosca debe estar entre 0 y 1")


def validar_archivos_fuente(raiz: Path, m, inf: Informe) -> None:
    arch = m.get("fuente", {}).get("archivos") if isinstance(m.get("fuente"), dict) else None
    if not isinstance(arch, dict):
        inf.error("marca.yml: fuente.archivos debe listar los TTF (regular, bold, italic, bolditalic)")
        return
    for estilo in ESTILOS_FUENTE:
        ruta = arch.get(estilo)
        if not isinstance(ruta, str):
            inf.error(f"marca.yml: fuente.archivos.{estilo} falta")
        elif not (raiz / ruta).is_file():
            inf.error(f"marca.yml: fuente.archivos.{estilo} apunta a {ruta}, que no existe")


def validar_horarios(h, inf: Informe) -> None:
    rel = "config/horarios.yml"
    if not _claves(h, rel, ["zona", "huecos_normales", "huecos_estreno"], inf):
        return
    try:
        ZoneInfo(str(h["zona"]))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        inf.error(f"{rel}: zona inválida '{h['zona']}'")
    for clave in ("huecos_normales", "huecos_estreno"):
        lista = h[clave]
        if not isinstance(lista, list):
            inf.error(f"{rel}: {clave} debe ser una lista")
            continue
        for x in lista:
            if not (isinstance(x, str) and RE_HORA.match(x)):
                inf.error(f"{rel}: {clave} con hora inválida {x!r} (formato HH:MM)")
    if isinstance(h["huecos_estreno"], list) and len(h["huecos_estreno"]) > 8:
        inf.error(f"{rel}: huecos_estreno admite 8 como máximo")


def validar_modo(m, inf: Informe) -> None:
    rel = "config/modo.yml"
    if not _claves(m, rel, ["modo", "plataformas_activas"], inf):
        return
    if m["modo"] not in MODOS:
        inf.error(f"{rel}: modo debe ser ensayo o vivo")
    pa = m["plataformas_activas"]
    if not (isinstance(pa, list) and set(pa) <= PLATAFORMAS):
        inf.error(f"{rel}: plataformas_activas debe ser una lista dentro de shorts, tiktok, reels")


def validar_canales(c, inf: Informe) -> None:
    rel = "config/canales.yml"
    if not isinstance(c, dict):
        inf.error(f"{rel}: debe ser un diccionario")
        return
    for plat, servicio in SERVICIOS.items():
        ficha = c.get(plat)
        if not isinstance(ficha, dict):
            inf.error(f"{rel}: falta el canal '{plat}'")
            continue
        if ficha.get("servicio") != servicio:
            inf.error(f"{rel}: {plat}.servicio debe ser {servicio}")
        if not ficha.get("nombre_esperado"):
            inf.error(f"{rel}: {plat}.nombre_esperado es obligatorio")
    for extra in set(c) - set(SERVICIOS):
        inf.error(f"{rel}: canal desconocido '{extra}'")


def validar_pausa(raiz: Path, inf: Informe) -> None:
    ruta = raiz / "PAUSA"
    if ruta.exists():
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        if not any(l.startswith("motivo:") and l.partition(":")[2].strip() for l in lineas):
            inf.error("PAUSA: debe tener una línea 'motivo:' con texto")


def validar_csv(raiz: Path, nombre: str, cabecera: list[str], inf: Informe) -> None:
    ruta = raiz / nombre
    if not ruta.exists():
        inf.error(f"{nombre}: no existe")
        return
    with open(ruta, newline="", encoding="utf-8") as f:
        primera = next(csv.reader(f), None)
    if primera != cabecera:
        inf.error(f"{nombre}: la cabecera no coincide con la esperada")


def _archivos(raiz: Path):
    for p in raiz.rglob("*"):
        if any(parte in IGNORAR_DIRS for parte in p.relative_to(raiz).parts):
            continue
        if p.is_file():
            yield p


def validar_sin_media(raiz: Path, inf: Informe) -> None:
    for p in _archivos(raiz):
        if p.suffix.lower() in EXT_MEDIA:
            inf.error(f"{p.relative_to(raiz)}: archivo de video o audio no permitido en el repo")


def validar_sin_claves(raiz: Path, inf: Informe) -> None:
    for p in _archivos(raiz):
        rel = p.relative_to(raiz)
        if p.suffix.lower() in EXT_BINARIAS:
            continue
        try:
            texto = p.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        en_pruebas = rel.parts[0] == "pruebas"
        for n, linea in enumerate(texto.splitlines(), 1):
            if en_pruebas and MARCA_FALSO in linea:
                continue
            if any(pat.search(linea) for pat in PATRONES_CLAVES):
                inf.error(f"{rel}:{n}: posible clave expuesta")


def validar(raiz=None) -> Informe:
    raiz = raiz_repo(raiz)
    inf = Informe()
    datos = {k: _cargar(raiz, rel, inf) for k, rel in ARCHIVOS.items()}
    if datos["temporada"] is not None:
        validar_temporada(datos["temporada"], inf)
    if datos["fuentes"] is not None:
        validar_fuentes(datos["fuentes"], inf)
    if datos["marca"] is not None:
        validar_marca(datos["marca"], inf)
        if isinstance(datos["marca"], dict):
            validar_archivos_fuente(raiz, datos["marca"], inf)
    if datos["horarios"] is not None:
        validar_horarios(datos["horarios"], inf)
    if datos["modo"] is not None:
        validar_modo(datos["modo"], inf)
    if datos["canales"] is not None:
        validar_canales(datos["canales"], inf)
    validar_pausa(raiz, inf)
    validar_csv(raiz, "marcador.csv", CABECERA_MARCADOR, inf)
    validar_csv(raiz, "envios-pendientes.csv", CABECERA_ENVIOS, inf)
    validar_sin_media(raiz, inf)
    validar_sin_claves(raiz, inf)
    return inf


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.validar",
                                 description="Valida la configuración del repo.")
    ap.add_argument("--raiz", default=None, help="Raíz del repo (por defecto, la del paquete)")
    args = ap.parse_args(argv)
    inf = validar(args.raiz)
    for a in inf.avisos:
        print(f"AVISO: {a}")
    for e in inf.errores:
        print(f"ERROR: {e}")
    if inf.errores:
        print(f"Puerta en ROJO: {len(inf.errores)} error(es), {len(inf.avisos)} aviso(s).")
        return 1
    print(f"Puerta en VERDE: 0 errores, {len(inf.avisos)} aviso(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .config import ARCHIVOS, raiz_repo
from .fuentes import _a_fecha, es_publicable
from .registro import CABECERA_ENVIOS, CABECERA_MARCADOR

TIPOS = {"A", "B", "C", "D", "T"}
ORIGENES = {"twitch", "youtube", "kick", "drive", "propio", "web"}
ESTADOS = {"vigente", "vencida", "pausada", "vetada"}
USOS = {"produccion", "muestras"}
PLATAFORMAS = {"shorts", "tiktok", "reels"}
SERVICIOS = {"shorts": "youtube", "tiktok": "tiktok", "reels": "instagram"}
MODOS = {"ensayo", "vivo"}
EXT_BINARIAS = {".ttf", ".otf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}
EXT_MEDIA = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".wav", ".mp3", ".ogg", ".flac", ".opus", ".aac"}
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
        self.pendientes: list[tuple[str, str]] = []   # (ficha, ruta) que `validar` comprueba contra la raíz

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


def _en(valor, conjunto) -> bool:
    """`valor in conjunto` sin TypeError cuando el YAML trae una lista o un diccionario."""
    return isinstance(valor, str) and valor in conjunto


def _fecha_hora(valor) -> datetime:
    """Fecha ISO a datetime con zona; sin zona se toma como hora de Guatemala. ValueError si no vale."""
    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, date):
        dt = datetime(valor.year, valor.month, valor.day)
    else:
        dt = datetime.fromisoformat(str(valor))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=ZoneInfo("America/Guatemala"))


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
            if _fecha_hora(v["inicio"]) >= _fecha_hora(v["fin"]):
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
        if not _en(tipo, TIPOS):
            inf.error(f"{et}: tipo debe ser A, B, C, D o T")
        if not _en(ficha.get("origen"), ORIGENES):
            inf.error(f"{et}: origen inválido (permitidos: {', '.join(sorted(ORIGENES))})")
        if not _en(ficha.get("estado"), ESTADOS):
            inf.error(f"{et}: estado inválido (permitidos: {', '.join(sorted(ESTADOS))})")
        if not _en(ficha.get("uso"), USOS):
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
        if tipo == "T":
            tl = ficha.get("twitch_login")
            if not (isinstance(tl, str) and tl):
                inf.error(f"{et}: tipo T exige twitch_login")
            cr = ficha.get("creador")
            if not (isinstance(cr, str) and cr.startswith("twitch.tv/")):
                inf.error(f"{et}: tipo T exige creador 'twitch.tv/<login>'")
        if tipo == "D":
            if ficha.get("pilares") != ["informativa"]:
                inf.error(f"{et}: tipo D sólo con pilar informativa")
            if ficha.get("campana") is not None:
                inf.error(f"{et}: tipo D exige campana null")
        if ficha.get("estado") in ESTADOS and ficha.get("estado") != "vigente" \
                and ficha.get("publicable") is True:
            _, motivo = es_publicable(ficha, _hoy(), None,
                                      "informativa" if ficha.get("tipo") == "D" else None)
            inf.aviso(f"{et}: marcada publicable pero no lo es ({motivo}); "
                      "la puerta de N3 exigirá estado vigente para descargar o publicar")
        _validar_campos_n3(ficha, et, inf)
        vence = ficha.get("vence")
        if vence is not None:
            f_vence = _a_fecha(vence)
            if f_vence is None:
                inf.error(f"{et}: vence ilegible ({vence!r}); use una fecha AAAA-MM-DD")
            elif ficha.get("estado") == "vigente" and f_vence < _hoy():
                inf.aviso(f"{et}: ficha vigente con vence {f_vence} ya pasado; debe pasar a vencida")


def _validar_campos_n3(ficha: dict, et: str, inf: Informe) -> None:
    """Campos opcionales de N3: twitch_login, material, reglas.permite_descarga, envio."""
    tl = ficha.get("twitch_login")
    if tl is not None and not (isinstance(tl, str) and re.fullmatch(r"[A-Za-z0-9_]{1,25}", tl)):
        inf.error(f"{et}: twitch_login debe ser texto (letras, números y guion bajo)")
    mat = ficha.get("material")
    if mat is not None:
        if not (isinstance(mat, list) and mat):
            inf.error(f"{et}: material debe ser una lista de URL https")
        else:
            for i, u in enumerate(mat):
                if isinstance(u, dict):
                    _validar_entrada_material(u, f"{et}: material[{i}]", ficha, inf)
                elif not _es_https(u):
                    inf.error(f"{et}: material debe ser una lista de URL https")
                    break
    reglas = ficha.get("reglas")
    if isinstance(reglas, dict) and "permite_descarga" in reglas \
            and not isinstance(reglas["permite_descarga"], bool):
        inf.error(f"{et}: reglas.permite_descarga debe ser booleano")
    if "envio" in ficha and ficha["envio"] not in ("manual", "api"):
        inf.error(f"{et}: envio debe ser manual o api")
    if "brief_url" in ficha and not _es_https(ficha["brief_url"]):
        inf.error(f"{et}: brief_url debe ser una URL https")
    if "juego" in ficha and not (isinstance(ficha["juego"], str) and ficha["juego"].strip()):
        inf.error(f"{et}: juego debe ser texto no vacío")
    if "marca_agua" in ficha:
        ma = ficha["marca_agua"]
        if not (isinstance(ma, str) and ma.endswith(".png") and not ma.startswith(("/", "..")) and ".." not in ma):
            inf.error(f"{et}: marca_agua debe ser la ruta relativa de un PNG dentro del repo")
        else:
            inf.pendientes.append((et, ma))
    if isinstance(reglas, dict):
        if "sin_musica" in reglas and not isinstance(reglas["sin_musica"], bool):
            inf.error(f"{et}: reglas.sin_musica debe ser booleano")
        ob = reglas.get("etiquetas_obligatorias")
        if ob is not None and not (isinstance(ob, list) and all(isinstance(x, str) and x.strip() for x in ob)):
            inf.error(f"{et}: reglas.etiquetas_obligatorias debe ser una lista de texto")
        hs = reglas.get("hashtags")
        if hs is not None and not (isinstance(hs, list) and all(isinstance(x, str) and x.startswith("#") for x in hs)):
            inf.error(f"{et}: reglas.hashtags debe ser una lista de hashtags que empiecen con #")


def _es_https(u) -> bool:
    return isinstance(u, str) and u.startswith("https://") and len(u) > 8


def _validar_entrada_material(m: dict, et: str, ficha: dict, inf: Informe) -> None:
    """Entrada con creador y etiquetas por red: {url, creador, etiquetas: {shorts, tiktok, reels}, archivo?}."""
    if not _es_https(m.get("url")):
        inf.error(f"{et}: url debe ser https")
    elif urlparse(m["url"]).hostname == "github.com":
        from .release import es_url_release
        if not es_url_release(m["url"]):
            inf.error(f"{et}: url de github.com debe ser un release (https://github.com/<dueño>/<repo>/releases/download/<tag>/<nombre>)")
    if "drive_url" in m and not (isinstance(m["drive_url"], str)
                                 and m["drive_url"].startswith("https://drive.usercontent.google.com/download?id=")):
        inf.error(f"{et}: drive_url debe empezar por https://drive.usercontent.google.com/download?id=")
    if not (isinstance(m.get("creador"), str) and m["creador"].strip()):
        inf.error(f"{et}: creador es obligatorio")
    etq = m.get("etiquetas")
    reglas = ficha.get("reglas") if isinstance(ficha.get("reglas"), dict) else {}
    plats = reglas.get("plataformas") if isinstance(reglas.get("plataformas"), list) else sorted(PLATAFORMAS)
    if not isinstance(etq, dict):
        inf.error(f"{et}: etiquetas debe ser un diccionario por red ({', '.join(sorted(PLATAFORMAS))})")
        return
    for k in etq:
        if k not in PLATAFORMAS:
            inf.error(f"{et}: etiquetas.{k}: red desconocida (permitidas: {', '.join(sorted(PLATAFORMAS))})")
    for p in plats:
        if p in PLATAFORMAS and not (isinstance(etq.get(p), str) and etq[p].strip()):
            inf.error(f"{et}: falta la etiqueta del creador para {p}")
    if "archivo" in m and not isinstance(m["archivo"], str):
        inf.error(f"{et}: archivo debe ser texto")


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
        mus = m.get("musica") if isinstance(m.get("musica"), dict) else {}
        if son.get("musica_de_biblioteca") is not True:
            inf.error(f"{rel}: sonido.musica_de_biblioteca debe ser true o false")
        elif not mus.get("manifiesto"):
            inf.error(f"{rel}: sonido.musica_de_biblioteca true exige musica.manifiesto")
        else:
            lufs, pico = mus.get("lufs"), mus.get("pico_db")
            if not (isinstance(lufs, (int, float)) and not isinstance(lufs, bool) and -20 <= lufs <= -12):
                inf.error(f"{rel}: musica.lufs debe estar entre -20 y -12")
            if not (isinstance(pico, (int, float)) and not isinstance(pico, bool) and pico <= -1):
                inf.error(f"{rel}: musica.pico_db debe ser -1 o menos")
            for k in ("entrada_s", "salida_s"):
                v = mus.get(k)
                if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 3):
                    inf.error(f"{rel}: musica.{k} debe ser un número de 0 a 3")
    e = m["encabezado_max_palabras"]
    if not (_es_int(e) and 1 <= e <= 6):
        inf.error(f"{rel}: encabezado_max_palabras debe ser entero de 1 a 6")
    s = m["subtitulo_max_lineas"]
    if not (_es_int(s) and 1 <= s <= 2):
        inf.error(f"{rel}: subtitulo_max_lineas debe ser entero de 1 a 2")
    validar_maqueta(m, inf)
    validar_marca_agua(m, inf)
    validar_pantalla_completa(m, inf)


RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
CAMPOS_LICENCIA = ("Fuente:", "Autor:", "Licencia:", "Enlace:")


def validar_musica(raiz: Path, datos, inf: Informe) -> None:
    """`marca/musica/pistas.yml`: sólo CC0, páginas https, assets sin rutas, y LICENCIA.md completa si está verificada."""
    rel = "marca/musica/pistas.yml"
    if not isinstance(datos, dict):
        inf.error(f"{rel}: debe ser un diccionario")
        return
    permitidas = datos.get("licencias_permitidas")
    if not (isinstance(permitidas, list) and permitidas and set(permitidas) <= {"CC0"}):
        inf.error(f"{rel}: licencias_permitidas debe ser una lista que sólo contenga CC0")
        permitidas = []
    pistas = datos.get("pistas")
    if not isinstance(pistas, list):
        inf.error(f"{rel}: pistas debe ser una lista")
        return
    vistos: set[str] = set()
    for i, p in enumerate(pistas):
        if not isinstance(p, dict):
            inf.error(f"{rel}: pistas[{i}] debe ser un diccionario")
            continue
        pid = p.get("id")
        et = f"{rel}: pista {pid if isinstance(pid, str) else i}"
        if not (isinstance(pid, str) and RE_ID.match(pid)):
            inf.error(f"{et}: id inválido (minúsculas, números y guiones)")
        elif pid in vistos:
            inf.error(f"{et}: id repetido")
        else:
            vistos.add(pid)
        if p.get("licencia") not in permitidas:
            inf.error(f"{et}: licencia {p.get('licencia')!r} fuera de licencias_permitidas (sólo CC0)")
        if not _es_https(p.get("pagina")):
            inf.error(f"{et}: pagina debe ser un enlace https")
        asset = p.get("asset")
        if not (isinstance(asset, str) and asset and "/" not in asset and "\\" not in asset
                and not asset.startswith(".")):
            inf.error(f"{et}: asset debe ser un nombre de archivo sin barras")
        if p.get("estado") not in ("candidata", "verificada", "descartada"):
            inf.error(f"{et}: estado debe ser candidata, verificada o descartada")
        if p.get("estado") == "verificada":
            if not (isinstance(p.get("sha256"), str) and RE_SHA256.match(p["sha256"])):
                inf.error(f"{et}: verificada exige sha256 hexadecimal de 64 caracteres")
            lic = raiz / "marca" / "musica" / str(pid) / "LICENCIA.md"
            if not lic.is_file():
                inf.error(f"{et}: verificada exige marca/musica/{pid}/LICENCIA.md")
            else:
                texto = lic.read_text(encoding="utf-8", errors="ignore")
                faltan = [c for c in CAMPOS_LICENCIA
                          if not re.search(rf"(?m)^\s*[-*]?\s*\**{re.escape(c[:-1])}(?::\**|\**:)[ \t]*\S", texto)]
                if faltan:
                    inf.error(f"{et}: LICENCIA.md sin las líneas {', '.join(faltan)}")
                elif not re.search(r"(?m)^\s*[-*]?\s*\**Licencia(?::\**|\**:)[^\n]*\bCC0\b", texto):
                    inf.error(f"{et}: la línea Licencia: de LICENCIA.md debe decir CC0")


def validar_marca_agua(m, inf: Informe) -> None:
    """`marca_agua` de marca.yml: lugar fijo, ancho, margen, opacidad y placa del logo de campaña."""
    rel = "marca.yml: marca_agua"
    ma = m.get("marca_agua")
    if not isinstance(ma, dict):
        inf.error("marca.yml: falta la sección marca_agua")
        return
    if ma.get("posicion") not in POSICIONES_AGUA:
        inf.error(f"{rel}.posicion debe ser una de {', '.join(POSICIONES_AGUA)}")
    for k in ("ancho", "margen", "opacidad", "placa"):
        v = ma.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            inf.error(f"{rel}.{k} debe ser un número positivo")
    if isinstance(ma.get("opacidad"), (int, float)) and not isinstance(ma.get("opacidad"), bool) \
            and ma["opacidad"] > 1:
        inf.error(f"{rel}.opacidad debe estar entre 0 y 1")
    if isinstance(ma.get("ancho"), (int, float)) and not isinstance(ma.get("ancho"), bool) and ma["ancho"] > 540:
        inf.error(f"{rel}.ancho {ma['ancho']}: el logo no puede pasar de la mitad del ancho (540)")


CLAVES_PANTALLA = ["zona_segura", "zona_accion", "tope_capas", "gancho_s", "relleno_placa", "ancho_max_encabezado",
                   "recorte", "fila", "cabecera", "mosca", "dialogo", "cierre", "agua", "lapiz"]


def validar_pantalla_completa(m, inf: Informe) -> None:
    """`pantalla_completa` de marca.yml: todas las medidas de la plantilla v2 (clip a cuadro completo con placas)."""
    rel = "marca.yml: pantalla_completa"
    if m.get("plantilla_video") != "pantalla_completa":
        inf.error("marca.yml: plantilla_video debe ser pantalla_completa")
    pc = m.get("pantalla_completa")
    if not isinstance(pc, dict):
        inf.error("marca.yml: falta la sección pantalla_completa")
        return
    for c in CLAVES_PANTALLA:
        if c not in pc:
            inf.error(f"{rel}: falta la clave '{c}'")
    _positivos(pc, "pantalla_completa", inf)
    zonas = {}
    for z in ("zona_segura", "zona_accion"):
        d = pc.get(z)
        if isinstance(d, dict) and all(isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool)
                                       for k in ("x0", "y0", "x1", "y1")):
            zonas[z] = d
            if not (d["x0"] < d["x1"] and d["y0"] < d["y1"]):
                inf.error(f"{rel}.{z}: x0 e y0 deben ser menores que x1 e y1")
    if len(zonas) == 2:
        s, a = zonas["zona_segura"], zonas["zona_accion"]
        if not (s["x0"] <= a["x0"] and s["y0"] <= a["y0"] and a["x1"] <= s["x1"] and a["y1"] <= s["y1"]):
            inf.error(f"{rel}: zona_accion debe quedar dentro de zona_segura")
    tope = pc.get("tope_capas")
    if isinstance(tope, (int, float)) and not isinstance(tope, bool) and tope > 1:
        inf.error(f"{rel}.tope_capas debe estar entre 0 y 1")


CLAVES_MAQUETA = [
    "margen", "columna_dialogo", "fila_superior_y", "tam_fila", "gancho_y", "tam_gancho",
    "alto_linea_gancho", "espacio_tras_gancho", "regla_grosor", "espacio_tras_regla", "tam_escena",
    "alto_escena", "espacio_tras_escena", "tam_nombre", "alto_nombre", "tam_acotacion",
    "alto_acotacion", "tam_frase", "alto_linea_frase", "espacio_video_dialogo",
    "alto_dialogo_sin_video", "margen_inferior", "tam_corte", "corte_y", "tam_credito", "credito_y", "cierre_s",
    "opacidad_mosca", "grano", "circulo", "subrayado",
]
SUBCLAVES_MAQUETA = {
    "grano": ["papel", "fibra", "bloque_fibra", "semilla"],
    "circulo": ["margen_x", "margen_y", "irregularidad", "sobrepaso_grados", "grosor",
                "giro_max_grados", "holguras_extra", "holgura_letra"],
    "subrayado": ["grosor", "sobresale", "bajo_palabra", "alto_extra", "inclinacion_max", "amplitud"],
}
ESQUINAS = {"superior_derecha", "superior_izquierda"}
POSICIONES_AGUA = ("cuerpo_inferior_derecha", "cuerpo_inferior_izquierda",
                   "cuerpo_superior_derecha", "cuerpo_superior_izquierda")
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
    for sub, claves in SUBCLAVES_MAQUETA.items():
        if sub in maq:
            _claves(maq[sub], f"{rel}: maqueta.{sub}", claves, inf)
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


def _entero(v, minimo: int, maximo: int | None = None) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= minimo and (maximo is None or v <= maximo)


def validar_horarios(h, inf: Informe) -> None:
    rel = "config/horarios.yml"
    if not _claves(h, rel, ["zona", "huecos"], inf):
        return
    try:
        ZoneInfo(str(h["zona"]))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        inf.error(f"{rel}: zona inválida '{h['zona']}'")
    huecos = h["huecos"]
    if not isinstance(huecos, list) or not huecos:
        inf.error(f"{rel}: huecos debe ser una lista no vacía")
    else:
        validos = True
        for x in huecos:
            if not (isinstance(x, str) and RE_HORA.match(x)):
                inf.error(f"{rel}: huecos con hora inválida {x!r} (formato HH:MM)")
                validos = False
        if validos and huecos != sorted(set(huecos)):
            inf.error(f"{rel}: huecos debe estar ordenada y sin repetidos")
        if len(huecos) > 8:
            inf.error(f"{rel}: huecos admite 8 como máximo")
    for clave in ("margen_min_minutos", "margen_max_minutos"):
        if clave in h and not _entero(h[clave], 1):
            inf.error(f"{rel}: {clave} debe ser un entero positivo")
    if _entero(h.get("margen_min_minutos", 15), 1) and _entero(h.get("margen_max_minutos", 75), 1) \
            and h.get("margen_min_minutos", 15) >= h.get("margen_max_minutos", 75):
        inf.error(f"{rel}: margen_min_minutos debe ser menor que margen_max_minutos")
    from .publicar import MINUTOS_MINIMOS_FUTURO
    if _entero(h.get("margen_min_minutos", 15), 1) and h.get("margen_min_minutos", 15) < MINUTOS_MINIMOS_FUTURO:
        inf.error(f"{rel}: margen_min_minutos no puede ser menor que {MINUTOS_MINIMOS_FUTURO} "
                  "(el mínimo de publicar.validar_futuro)")
    if "cola_buffer_max" in h and not _entero(h["cola_buffer_max"], 1, 10):
        inf.error(f"{rel}: cola_buffer_max debe ser un entero de 1 a 10")
    if "no_repetir_horas" in h and not _entero(h["no_repetir_horas"], 24):
        inf.error(f"{rel}: no_repetir_horas debe ser un entero de 24 o más")
    if "relleno" in h:
        r = h["relleno"]
        if not isinstance(r, dict):
            inf.error(f"{rel}: relleno debe ser un mapa")
        else:
            if "separacion_min_minutos" in r and not _entero(r["separacion_min_minutos"], 1):
                inf.error(f"{rel}: relleno.separacion_min_minutos debe ser un entero positivo")
            if "max_intentos_por_hueco" in r and not _entero(r["max_intentos_por_hueco"], 1):
                inf.error(f"{rel}: relleno.max_intentos_por_hueco debe ser un entero positivo")
            if "ultima_hora" in r and not (isinstance(r["ultima_hora"], str) and RE_HORA.match(r["ultima_hora"])):
                inf.error(f"{rel}: relleno.ultima_hora debe ser HH:MM")
    if "actions" in h:
        a = h["actions"]
        if not isinstance(a, dict):
            inf.error(f"{rel}: actions debe ser un mapa")
        elif "tope_minutos_dia" in a and not _entero(a["tope_minutos_dia"], 30, 600):
            inf.error(f"{rel}: actions.tope_minutos_dia debe ser un entero de 30 a 600")


def validar_cupos_contra_huecos(t, h, inf: Informe) -> None:
    """Los cupos de temporada.yml no pueden pasar del número de huecos de horarios.yml."""
    huecos = h.get("huecos") if isinstance(h, dict) else None
    cupos = t.get("cupos") if isinstance(t, dict) else None
    if not (isinstance(huecos, list) and huecos and isinstance(cupos, dict)):
        return
    for clave in ("por_dia_por_plataforma", "estreno_max_por_dia_por_plataforma"):
        v = cupos.get(clave)
        if isinstance(v, int) and not isinstance(v, bool) and v > len(huecos):
            inf.error(f"temporada.yml: cupos.{clave} ({v}) pasa del número de huecos de config/horarios.yml "
                      f"({len(huecos)})")


def validar_modo(m, inf: Informe) -> None:
    rel = "config/modo.yml"
    if not _claves(m, rel, ["modo", "plataformas_activas"], inf):
        return
    if m["modo"] not in MODOS:
        inf.error(f"{rel}: modo debe ser ensayo o vivo")
    pa = m["plataformas_activas"]
    if not (isinstance(pa, list) and set(pa) <= PLATAFORMAS):
        inf.error(f"{rel}: plataformas_activas debe ser una lista dentro de shorts, tiktok, reels")


def validar_ojos(o, inf: Informe) -> None:
    rel = "config/ojos.yml"
    if not _claves(o, rel, ["modelo", "modelos_respaldo", "max_mb_en_linea", "temperatura"], inf):
        return
    if not (isinstance(o["modelo"], str) and o["modelo"].strip()):
        inf.error(f"{rel}: modelo debe ser texto")
    r = o["modelos_respaldo"]
    if not (isinstance(r, list) and all(isinstance(x, str) and x.strip() for x in r)):
        inf.error(f"{rel}: modelos_respaldo debe ser una lista de nombres de modelo")
    elif o["modelo"] in r or len(set(r)) != len(r):
        inf.error(f"{rel}: modelos_respaldo no puede repetir modelos")
    mb = o["max_mb_en_linea"]
    if isinstance(mb, bool) or not isinstance(mb, (int, float)) or not 0 < mb <= 20:
        inf.error(f"{rel}: max_mb_en_linea debe ser un número mayor que 0 y de 20 como máximo")
    t = o["temperatura"]
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 2:
        inf.error(f"{rel}: temperatura debe ser un número de 0 a 2")
    for clave, minimo in (("tiempo_por_intento_s", 1), ("tope_ojos_s", 1)):     # opcionales
        if clave in o and (isinstance(o[clave], bool) or not isinstance(o[clave], (int, float)) or o[clave] < minimo):
            inf.error(f"{rel}: {clave} debe ser un número de {minimo} o más")
    if "respaldos_por_falla" in o and (isinstance(o["respaldos_por_falla"], bool)
                                       or not isinstance(o["respaldos_por_falla"], int) or o["respaldos_por_falla"] < 0):
        inf.error(f"{rel}: respaldos_por_falla debe ser un entero de 0 o más")
    if "listar_modelos" in o and not isinstance(o["listar_modelos"], bool):
        inf.error(f"{rel}: listar_modelos debe ser true o false")
    if "sin_ia" in o:
        s = o["sin_ia"]
        if not isinstance(s, dict) or not isinstance(s.get("activo"), bool):
            inf.error(f"{rel}: sin_ia debe ser un bloque con activo: true o false")
        else:
            m = s.get("max_por_ciclo", 1)
            if isinstance(m, bool) or not isinstance(m, int) or m < 0:
                inf.error(f"{rel}: sin_ia.max_por_ciclo debe ser un entero de 0 o más")
            d = s.get("duracion_objetivo_s", 20)
            if isinstance(d, bool) or not isinstance(d, (int, float)) or not 7 <= d <= 30:
                inf.error(f"{rel}: sin_ia.duracion_objetivo_s debe ser un número de 7 a 30")


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


def validar_almacen(a, inf: Informe) -> None:
    rel = "config/almacen.yml"
    if not _claves(a, rel, ["bucket", "max_mb", "prefijo"], inf):
        return
    if not (isinstance(a["bucket"], str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", a["bucket"])):
        inf.error(f"{rel}: bucket debe estar en minúsculas, números y guiones")
    if not (_es_int(a["max_mb"]) and 1 <= a["max_mb"] <= 50):
        inf.error(f"{rel}: max_mb debe ser entero de 1 a 50 (tope del plan gratis)")
    p = a["prefijo"]
    if not (isinstance(p, str) and re.fullmatch(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*", p)):
        inf.error(f"{rel}: prefijo debe ser una ruta sin espacios ni barras en los extremos")


def validar_pausa(raiz: Path, inf: Informe) -> None:
    ruta = raiz / "PAUSA"
    if ruta.exists():
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        if not any(l.startswith("motivo:") and l.partition(":")[2].strip() for l in lineas):
            inf.error("PAUSA: debe tener una línea 'motivo:' con texto")


def validar_pausa_t(raiz: Path, inf: Informe) -> None:
    ruta = raiz / "estado" / "pausa-t.yml"
    if ruta.exists():
        lineas = [l for l in ruta.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]
        if not any(l.startswith("motivo:") and l.partition(":")[2].strip() for l in lineas):
            inf.error("estado/pausa-t.yml: debe tener una línea 'motivo:' con texto")


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


def _sin_acentos_may(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(t)) if unicodedata.category(c) != "Mn").upper()


def _ganchos_genericos(marca) -> list[str]:
    """Los ganchos prohibidos que `marca.yml` lista en `prohibido` ("ganchos genéricos (A, B, C)")."""
    out: list[str] = []
    for item in (marca or {}).get("prohibido") or [] if isinstance(marca, dict) else []:
        m = re.search(r"ganchos gen[eé]ricos\s*\((.*)\)", str(item), re.I)
        if m:
            out += [x.strip() for x in m.group(1).split(",") if x.strip()]
    return out


def validar_informativas(datos, marca, inf: Informe) -> None:
    rel = "informativas.yml"
    lista = datos.get("informativas") if isinstance(datos, dict) else None
    if not isinstance(lista, list) or not lista:
        inf.error(f"{rel}: debe traer `informativas:` con al menos una entrada")
        return
    genericos = [_sin_acentos_may(g) for g in _ganchos_genericos(marca)]
    vistos: set[str] = set()
    for i, e in enumerate(lista, 1):
        if not isinstance(e, dict):
            inf.error(f"{rel}: la entrada {i} debe ser un diccionario")
            continue
        et = f"{rel}: entrada {e.get('id', i)}"
        faltan = [c for c in ("id", "gancho", "palabra_lapiz", "titular", "datos", "fuente_url",
                              "verificado_el", "vigente_desde", "vigente_hasta", "usada") if c not in e]
        if faltan:
            inf.error(f"{et}: faltan las claves {', '.join(faltan)}")
            continue
        if not (isinstance(e["id"], str) and RE_ID.match(e["id"])):
            inf.error(f"{et}: id inválido (minúsculas, números y guiones)")
        elif e["id"] in vistos:
            inf.error(f"{et}: id repetido")
        vistos.add(str(e["id"]))
        gancho = e["gancho"]
        if not (isinstance(gancho, str) and gancho.strip()):
            inf.error(f"{et}: gancho vacío")
        else:
            if len(gancho.split()) > 6:
                inf.error(f"{et}: el gancho pasa de 6 palabras")
            g = _sin_acentos_may(gancho)
            if any(x and x in g for x in genericos):
                inf.error(f"{et}: gancho genérico prohibido por marca.yml")
            lapiz = _sin_acentos_may(e["palabra_lapiz"]).strip(".,;:¿?¡!\"'()")
            if not lapiz or lapiz not in [w.strip(".,;:¿?¡!\"'()") for w in g.split()]:
                inf.error(f"{et}: palabra_lapiz debe ser una palabra del gancho")
        if not (isinstance(e["titular"], str) and e["titular"].strip()):
            inf.error(f"{et}: titular vacío")
        d = e["datos"]
        if not (isinstance(d, list) and 2 <= len(d) <= 3 and all(isinstance(x, str) and x.strip() for x in d)):
            inf.error(f"{et}: datos debe ser una lista de 2 o 3 viñetas de texto")
        elif any(len(x) > 70 for x in d):
            inf.error(f"{et}: cada viñeta de datos debe ser corta (70 caracteres como máximo)")
        u = e["fuente_url"]
        if not (isinstance(u, str) and u.startswith("https://") and len(u) > 8):
            inf.error(f"{et}: fuente_url debe ser https")
        if _a_fecha(e["verificado_el"]) is None:
            inf.error(f"{et}: verificado_el no es una fecha")
        ts = []
        for c in ("vigente_desde", "vigente_hasta"):
            try:
                dt = e[c] if isinstance(e[c], datetime) else datetime.fromisoformat(str(e[c]))
                if dt.tzinfo is None:
                    raise ValueError("sin zona")
                ts.append(dt)
            except ValueError:
                inf.error(f"{et}: {c} debe ser una fecha ISO con zona horaria")
        if len(ts) == 2 and ts[0] >= ts[1]:
            inf.error(f"{et}: vigente_desde debe ser anterior a vigente_hasta")
        if not isinstance(e["usada"], bool):
            inf.error(f"{et}: usada debe ser true o false")


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
        for et, ruta in inf.pendientes:
            if not (raiz / ruta).is_file():
                inf.error(f"{et}: marca_agua apunta a {ruta}, que no existe")
    if datos["marca"] is not None:
        validar_marca(datos["marca"], inf)
        if isinstance(datos["marca"], dict):
            validar_archivos_fuente(raiz, datos["marca"], inf)
    if datos["horarios"] is not None:
        validar_horarios(datos["horarios"], inf)
        if datos["temporada"] is not None:
            validar_cupos_contra_huecos(datos["temporada"], datos["horarios"], inf)
    if datos["modo"] is not None:
        validar_modo(datos["modo"], inf)
    if datos["canales"] is not None:
        validar_canales(datos["canales"], inf)
    ojos = _cargar(raiz, "config/ojos.yml", inf)
    if ojos is not None:
        validar_ojos(ojos, inf)
    almacen = _cargar(raiz, "config/almacen.yml", inf)
    if almacen is not None:
        validar_almacen(almacen, inf)
    informativas = _cargar(raiz, "informativas.yml", inf)
    if informativas is not None:
        validar_informativas(informativas, datos["marca"], inf)
    musica = _cargar(raiz, "marca/musica/pistas.yml", inf)
    if musica is not None:
        validar_musica(raiz, musica, inf)
    validar_pausa(raiz, inf)
    validar_pausa_t(raiz, inf)
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

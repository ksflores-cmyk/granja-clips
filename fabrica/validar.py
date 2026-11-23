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
    vistos_cr: dict = {}
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
        _validar_campana(ficha, et, inf, vistos_cr)
        vence = ficha.get("vence")
        if vence is not None:
            f_vence = _a_fecha(vence)
            if f_vence is None:
                inf.error(f"{et}: vence ilegible ({vence!r}); use una fecha AAAA-MM-DD")
            elif ficha.get("estado") == "vigente" and f_vence < _hoy():
                inf.aviso(f"{et}: ficha vigente con vence {f_vence} ya pasado; debe pasar a vencida")


RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _validar_campana(ficha: dict, et: str, inf: Informe, vistos_cr: dict) -> None:
    """Campos de las fichas de Content Rewards (#23): cr_campaign_id, motivo_pausa, *_por_red, presupuesto_total."""
    cr = ficha.get("cr_campaign_id")
    if cr is not None:
        if not (isinstance(cr, str) and RE_UUID.match(cr)):
            inf.error(f"{et}: cr_campaign_id debe ser el uuid de la campaña en minúsculas")
        else:
            if cr in vistos_cr:
                inf.error(f"{et}: cr_campaign_id repetido (ya está en {vistos_cr[cr]})")
            vistos_cr.setdefault(cr, ficha.get("id"))
            if not (isinstance(ficha.get("campana"), str) and ficha["campana"].strip()):
                inf.error(f"{et}: cr_campaign_id exige campana (nombre exacto de la campaña)")
            if isinstance(ficha.get("permiso_url"), str) and cr not in ficha["permiso_url"]:
                inf.error(f"{et}: permiso_url no es la página de la campaña {cr}")
    if ficha.get("estado") == "pausada" and ficha.get("tipo") == "A":
        mp = ficha.get("motivo_pausa")
        if not (isinstance(mp, str) and len(mp.strip()) >= 20):
            inf.error(f"{et}: ficha A pausada exige motivo_pausa (por qué y cómo se reanuda)")
    reglas = ficha.get("reglas") if isinstance(ficha.get("reglas"), dict) else {}
    plats = set(reglas.get("plataformas") or []) if isinstance(reglas.get("plataformas"), list) else set()
    epr = reglas.get("etiquetas_por_red")
    if epr is not None:
        if not (isinstance(epr, dict) and epr and all(
                isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v) for v in epr.values())):
            inf.error(f"{et}: reglas.etiquetas_por_red debe ser {{red: [etiquetas]}}")
        elif not set(epr) <= PLATAFORMAS or (plats and not set(epr) <= plats):
            inf.error(f"{et}: reglas.etiquetas_por_red con una red fuera de reglas.plataformas")
    tpr = ficha.get("tarifa_por_red")
    if tpr is not None:
        if not (isinstance(tpr, dict) and tpr and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                      and v > 0 for v in tpr.values())):
            inf.error(f"{et}: tarifa_por_red debe ser {{red: US$ por mil > 0}}")
        elif not set(tpr) <= PLATAFORMAS or (plats and set(tpr) != plats):
            inf.error(f"{et}: tarifa_por_red debe cubrir exactamente reglas.plataformas")
    pt, pr = ficha.get("presupuesto_total"), ficha.get("presupuesto_restante")
    if pt is not None and isinstance(pr, (int, float)) and isinstance(pt, (int, float)) and pr > pt:
        inf.error(f"{et}: presupuesto_restante mayor que presupuesto_total")


def _validar_campos_n3(ficha: dict, et: str, inf: Informe) -> None:
    """Campos opcionales de N3: twitch_login, material, reglas.permite_descarga, envio."""
    tl = ficha.get("twitch_login")
    if tl is not None and not (isinstance(tl, str) and re.fullmatch(r"[A-Za-z0-9_]{1,25}", tl)):
        inf.error(f"{et}: twitch_login debe ser texto (letras, números y guion bajo)")
    for campo in ("apodo", "nombre_visible"):      # nombre que se ve en el video: apodo (a mano) o el de Twitch (generado)
        v = ficha.get(campo)
        if v is not None and not (isinstance(v, str) and v.strip() and len(v.strip()) <= 40
                                  and not re.search(r"[<>#@/\\]", v)):
            inf.error(f"{et}: {campo} debe ser texto corto (hasta 40 caracteres, sin @, # ni enlaces)")
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
    validar_avisos(m, inf)
    validar_dividida(m, inf)
    validar_voces_marca(m, inf)


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


def validar_reglas_kevin(raiz: Path, inf: Informe) -> None:
    """`reglas-kevin.yml` con el mismo `cargar` de la puerta; avisa cuántas reglas quedan como notas."""
    from .editar import reglas_kevin
    try:
        reglas = reglas_kevin.cargar(raiz)
    except ValueError as e:
        inf.error(f"reglas-kevin.yml: {e}")
        return
    notas = [r for r in reglas if r["estado"] == "vigente" and not r["revision"]]
    if notas:
        inf.aviso(f"reglas-kevin.yml: {len(notas)} regla(s) vigente(s) sin revisión quedan como nota para los críticos")


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


AVISOS_S = (0.8, 1.5)             # segundos que se ve el aviso: lo bastante para leerlo, sin comerse el gancho
AVISOS_TIPOS = ("spoiler", "cinematica", "mods")
AVISO_MAX_CARACTERES = 24


def validar_avisos(m, inf: Informe) -> None:
    """`avisos` de marca.yml: duración de 0,8 a 1,5 s y un texto corto en mayúsculas por tipo (decisión de Kevin, 22 nov)."""
    rel = "marca.yml: avisos"
    a = m.get("avisos")
    if not _claves(a, rel, ["duracion_s", "textos"], inf):
        return
    d = a["duracion_s"]
    lo, hi = AVISOS_S
    if not (isinstance(d, (int, float)) and not isinstance(d, bool) and lo <= d <= hi):
        inf.error(f"{rel}: duracion_s debe ser un número de {lo:g} a {hi:g}")
    if not _claves(a["textos"], f"{rel}.textos", list(AVISOS_TIPOS), inf):
        return
    for k in AVISOS_TIPOS:
        t = a["textos"][k]
        if not (isinstance(t, str) and t.strip() and t == t.upper() and "\n" not in t
                and len(t) <= AVISO_MAX_CARACTERES):
            inf.error(f"{rel}.textos.{k}: debe ser una sola línea en mayúsculas de hasta {AVISO_MAX_CARACTERES} caracteres")


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


CLAVES_DIVIDIDA = ["activa", "alto_facecam", "tolerancia_alto", "cara_centro_y", "holgura_cara", "max_cara_panel",
                   "margen_cara", "max_escala_facecam", "zona_accion", "placas_alterna", "deteccion"]
CLAVES_DETECCION = ["fotogramas", "fotogramas_cara", "ancho_analisis", "haar", "min_fraccion_cara", "max_dispersion",
                    "max_area_cara", "centro_excluido", "expansion", "borde_persistente", "umbral_borde", "rejilla",
                    "banda", "banda_chat", "densidad_logo", "variacion_estatica", "densidad_chat", "min_celdas_chat",
                    "densidad_base_alerta", "salto_alerta", "min_celdas_alerta", "max_fraccion_alerta", "min_movimiento", "peso_bordes",
                    "min_alto_juego", "plazo_min_s", "plazo_min_cara_s", "min_lado_px", "tamanos_juego", "paso_juego", "peso_centro", "margen_facecam", "peso_estorbo", "peso_tamano"]


def validar_dividida(m, inf: Informe) -> None:
    """`dividida` de marca.yml (tarea #16): medidas de la pantalla dividida y de la detección de la facecam."""
    rel = "marca.yml: dividida"
    d = m.get("dividida")
    if not isinstance(d, dict):
        inf.error("marca.yml: falta la sección dividida")
        return
    for c in CLAVES_DIVIDIDA:
        if c not in d:
            inf.error(f"{rel}: falta la clave '{c}'")
    if not isinstance(d.get("activa"), bool):
        inf.error(f"{rel}.activa debe ser true o false")
    det = d.get("deteccion")
    if isinstance(det, dict):
        for c in CLAVES_DETECCION:
            if c not in det:
                inf.error(f"{rel}.deteccion: falta la clave '{c}'")
    elif "deteccion" in d:
        inf.error(f"{rel}.deteccion debe ser un diccionario")
    _positivos({k: v for k, v in d.items() if k != "activa"}, "dividida", inf)
    res = m.get("resolucion")
    alto_f = d.get("alto_facecam")
    if isinstance(res, list) and len(res) == 2 and isinstance(alto_f, (int, float)) and not isinstance(alto_f, bool):
        if not 0 < alto_f < res[1]:
            inf.error(f"{rel}.alto_facecam {alto_f}: debe ser menor que el alto del cuadro ({res[1]})")
        za = d.get("zona_accion")
        pc = m.get("pantalla_completa") or {}
        seg = pc.get("zona_segura") if isinstance(pc, dict) else None
        if isinstance(za, dict) and all(isinstance(za.get(k), (int, float)) for k in ("x0", "y0", "x1", "y1")):
            if not (za["x0"] < za["x1"] and za["y0"] < za["y1"]):
                inf.error(f"{rel}.zona_accion: x0 e y0 deben ser menores que x1 e y1")
            if za["y0"] < alto_f:
                inf.error(f"{rel}.zona_accion debe quedar dentro del panel del juego (y0 >= {alto_f})")
            if isinstance(seg, dict) and all(isinstance(seg.get(k), (int, float)) for k in ("x0", "y0", "x1", "y1")) \
                    and not (seg["x0"] <= za["x0"] and za["x1"] <= seg["x1"] and seg["y0"] <= za["y0"]
                             and za["y1"] <= seg["y1"]):
                inf.error(f"{rel}.zona_accion debe quedar dentro de la zona segura")
    for k in ("cara_centro_y", "max_cara_panel", "tolerancia_alto"):
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1:
            inf.error(f"{rel}.{k} debe estar entre 0 y 1")


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


def validar_prueba_retencion(p, inf: Informe) -> None:
    rel = "config/prueba-retencion.yml"
    if not _claves(p, rel, ["activa", "nombre", "inicio", "brazos", "asignacion", "min_tomas_por_brazo"], inf):
        return
    if not isinstance(p["activa"], bool):
        inf.error(f"{rel}: activa debe ser true o false")
    if not (isinstance(p["nombre"], str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", p["nombre"])):
        inf.error(f"{rel}: nombre debe ser texto en minúsculas, números y guiones")
    if not isinstance(p["inicio"], date) or isinstance(p["inicio"], datetime):
        inf.error(f"{rel}: inicio debe ser una fecha AAAA-MM-DD")
    if not (isinstance(p["asignacion"], str) and p["asignacion"].strip()):
        inf.error(f"{rel}: asignacion debe ser texto")
    m = p["min_tomas_por_brazo"]
    if not _es_int(m) or m < 1:
        inf.error(f"{rel}: min_tomas_por_brazo debe ser un entero de 1 o más")
    b = p["brazos"]
    if not isinstance(b, dict) or set(b) != {"corta", "control"}:
        inf.error(f"{rel}: brazos debe tener exactamente corta y control")
        return
    c = b["corta"]
    if not isinstance(c, dict):
        inf.error(f"{rel}: brazos.corta debe ser un bloque")
        return
    d = c.get("duracion_s")
    if not (isinstance(d, list) and len(d) == 2 and all(_numero(x, 7, 30) for x in d) and d[0] <= d[1]):
        inf.error(f"{rel}: brazos.corta.duracion_s debe ser [mínimo, máximo] entre 7 y 30")
    if not _numero(c.get("gancho_max_s"), 0.1, 5):
        inf.error(f"{rel}: brazos.corta.gancho_max_s debe ser un número de 0.1 a 5")
    if not isinstance(b["control"], dict):
        inf.error(f"{rel}: brazos.control debe ser un bloque (puede ir vacío)")


def validar_tecnica(t, inf: Informe) -> None:
    """`config/tecnica.yml`: umbrales de la puerta técnica (todos numéricos, con su rango)."""
    rel = "config/tecnica.yml"
    if not isinstance(t, dict):
        inf.error(f"{rel}: debe ser un diccionario")
        return

    def num(bloque, clave, minimo, maximo=None, entero=False, estricto=False):
        v = bloque.get(clave)
        tipo = int if entero else (int, float)
        bien = (isinstance(v, tipo) and not isinstance(v, bool)
                and (v > minimo if estricto else v >= minimo) and (maximo is None or v <= maximo))
        if not bien:
            rango = f"{'mayor que' if estricto else 'de'} {minimo:g}{' o más' if not estricto else ''}"
            if maximo is not None:
                rango = (f"mayor que {minimo:g} y de {maximo:g} como máximo" if estricto
                         else f"entre {minimo:g} y {maximo:g}")
            inf.error(f"{rel}: {bloque['_nombre']}.{clave} debe ser {'un entero' if entero else 'un número'} {rango}")

    def bloque(nombre, claves):
        b = t.get(nombre)
        if not isinstance(b, dict):
            inf.error(f"{rel}: falta el bloque {nombre}")
            return None
        for c in claves:
            if c not in b:
                inf.error(f"{rel}: {nombre} sin la clave {c}")
        return dict(b, _nombre=nombre) if all(c in b for c in claves) else None

    b = bloque("subtitulos", ["tolerancia_s", "ventana_s", "parecido_min", "exigir_transcripcion"])
    if b:
        num(b, "tolerancia_s", 0, 5, estricto=True)
        num(b, "ventana_s", 0, 10, estricto=True)
        num(b, "parecido_min", 0, 1)
        if not isinstance(b["exigir_transcripcion"], bool):
            inf.error(f"{rel}: subtitulos.exigir_transcripcion debe ser true o false")
    b = bloque("destellos", ["negro_max", "blanco_min", "fraccion_min", "salto_min", "cuadros_max"])
    if b:
        num(b, "negro_max", 0, 255, entero=True)
        num(b, "blanco_min", 0, 255, entero=True)
        num(b, "fraccion_min", 0, 1, estricto=True)
        num(b, "salto_min", 0, 1, estricto=True)
        num(b, "cuadros_max", 1, 10, entero=True)
        if isinstance(b["negro_max"], int) and isinstance(b["blanco_min"], int) and b["negro_max"] >= b["blanco_min"]:
            inf.error(f"{rel}: destellos.negro_max debe ser menor que blanco_min")
    b = bloque("congelados", ["max_s", "dif_percentil", "dif_max"])
    if b:
        num(b, "max_s", 0, 60, estricto=True)
        num(b, "dif_percentil", 50, 100)
        num(b, "dif_max", 0, 255)
    b = bloque("ducking", ["margen_db", "ventana_s", "voz_activa_db", "tolerancia_fraccion", "max_seguidas",
                           "musica_base_db", "sidechain"])
    if b:
        num(b, "margen_db", 0, 60)
        num(b, "ventana_s", 0.01, 2)
        num(b, "voz_activa_db", -100, 0)
        num(b, "tolerancia_fraccion", 0, 1)
        num(b, "max_seguidas", 1, 1000, entero=True)
        num(b, "musica_base_db", -60, 0)
        s = b["sidechain"]
        if not isinstance(s, dict):
            inf.error(f"{rel}: ducking.sidechain debe ser un bloque")
        else:
            for c in ("threshold", "ratio", "attack", "release", "makeup"):
                if c not in s:
                    inf.error(f"{rel}: ducking.sidechain sin la clave {c}")
            s = dict(s, _nombre="ducking.sidechain")
            if all(c in s for c in ("threshold", "ratio", "attack", "release", "makeup")):
                # los límites son los de `sidechaincompress` de ffmpeg
                num(s, "threshold", 0.000976563, 1)
                num(s, "ratio", 1, 20)
                num(s, "attack", 0.01, 2000)
                num(s, "release", 0.01, 9000)
                num(s, "makeup", 1, 64)
    b = bloque("clipping", ["umbral", "muestras_seguidas", "plano_max"])
    if b:
        num(b, "plano_max", 0, 1, estricto=True)
        num(b, "umbral", 0, 1, estricto=True)
        num(b, "muestras_seguidas", 1, 1000, entero=True)
    b = bloque("analisis", ["ancho", "alto"])
    if b:
        num(b, "ancho", 16, 1920, entero=True)
        num(b, "alto", 16, 1920, entero=True)


def _num(v, minimo=None, maximo=None) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and (minimo is None or v >= minimo) and (maximo is None or v <= maximo))


def validar_criticos(c, inf: Informe) -> None:
    rel = "config/criticos.yml"
    if not _claves(c, rel, ["activo", "modo", "tiempo_por_llamada_s", "tope_s", "juez", "cupos", "segundo", "sin_ia"], inf):
        return
    if not isinstance(c["activo"], bool):
        inf.error(f"{rel}: activo debe ser true o false")
    if c["modo"] not in ("cadena", "sin_ia"):
        inf.error(f"{rel}: modo debe ser cadena o sin_ia")
    for clave in ("tiempo_por_llamada_s", "tope_s"):
        if not _num(c[clave], 1):
            inf.error(f"{rel}: {clave} debe ser un número de 1 o más")
    for clave in ("reserva_publicar_s", "minimo_ronda2_s"):
        if not _num(c.get(clave), 0):
            inf.error(f"{rel}: {clave} debe ser un número de 0 o más")
    if "temperatura" in c and not _num(c["temperatura"], 0, 2):
        inf.error(f"{rel}: temperatura debe ser un número de 0 a 2")
    f = c.get("fotogramas") or {}
    if not (isinstance(f, dict) and _es_int(f.get("n")) and 2 <= f["n"] <= 12 and _es_int(f.get("lado_max"))
            and 64 <= f["lado_max"] <= 1024):
        inf.error(f"{rel}: fotogramas debe traer n (2 a 12) y lado_max (64 a 1024)")
    if not (isinstance(c["juez"], dict) and isinstance(c["juez"].get("con_modelo"), bool)):
        inf.error(f"{rel}: juez.con_modelo debe ser true o false")
    cu = c["cupos"]
    g = cu.get("gemini") if isinstance(cu, dict) else None
    if not (isinstance(g, dict) and all(_es_int(g.get(k)) and g[k] >= 0 for k in ("rpm", "rpd", "reserva_ojos", "pausa_429_s"))):
        inf.error(f"{rel}: cupos.gemini debe traer rpm, rpd, reserva_ojos y pausa_429_s (enteros de 0 o más)")
    elif g["reserva_ojos"] >= g["rpd"]:
        inf.error(f"{rel}: cupos.gemini.reserva_ojos debe ser menor que rpd (si no, los críticos nunca tienen cupo)")
    if not (isinstance(cu, dict) and _num(cu.get("espera_max_s"), 0)):
        inf.error(f"{rel}: cupos.espera_max_s debe ser un número de 0 o más")
    s = c["segundo"]
    if not (isinstance(s, dict) and isinstance(s.get("activo"), bool)):
        inf.error(f"{rel}: segundo.activo debe ser true o false")
    else:
        if s["activo"]:
            for k in ("url", "modelo", "secreto"):
                if not (isinstance(s.get(k), str) and s[k].strip()):
                    inf.error(f"{rel}: segundo activo necesita {k} (texto)")
            if isinstance(s.get("url"), str) and not s["url"].startswith("https://"):
                inf.error(f"{rel}: segundo.url debe empezar con https://")
        elif not (isinstance(s.get("motivo"), str) and s["motivo"].strip()):
            inf.error(f"{rel}: segundo apagado necesita un motivo escrito")
        if isinstance(s.get("secreto"), str) and not re.fullmatch(r"[A-Z][A-Z0-9_]*", s["secreto"]):
            inf.error(f"{rel}: segundo.secreto es el NOMBRE de una variable de entorno (MAYÚSCULAS), nunca la clave")
    u = c["sin_ia"]
    esperados = {"historia": ("audio_inicio_s", "silencio_db", "cambio_min", "min_pares_con_cambio"),
                 "subtitulos": ("max_cps", "tolerancia_papel", "max_papel_en_botones"),
                 "tecnica": ("negro_max", "blanco_min", "min_nitidez", "min_cuadros_nitidos", "vol_min_db", "vol_max_db")}
    if not isinstance(u, dict):
        inf.error(f"{rel}: sin_ia debe ser un bloque con historia, subtitulos y tecnica")
        return
    for crit, claves in esperados.items():
        bloque = u.get(crit)
        if not isinstance(bloque, dict):
            inf.error(f"{rel}: sin_ia.{crit} debe ser un bloque")
            continue
        for k in claves:
            if not _num(bloque.get(k)):
                inf.error(f"{rel}: sin_ia.{crit}.{k} debe ser un número")
    t = u.get("tecnica") or {}
    if _num(t.get("vol_min_db")) and _num(t.get("vol_max_db")) and t["vol_min_db"] >= t["vol_max_db"]:
        inf.error(f"{rel}: sin_ia.tecnica.vol_min_db debe ser menor que vol_max_db")
    p = (u.get("subtitulos") or {}).get("max_papel_en_botones")
    if _num(p) and not 0 <= p <= 1:
        inf.error(f"{rel}: sin_ia.subtitulos.max_papel_en_botones debe ser una fracción de 0 a 1")


def validar_voces_marca(m, inf: Informe) -> None:
    """`marca.yml` voces (opcional): cada valor es el nombre de un color de `colores`."""
    if "voces" not in m:
        return
    rel = "marca.yml"
    v, colores = m["voces"], m.get("colores")
    if not isinstance(v, dict):
        inf.error(f"{rel}: voces debe ser un diccionario (streamer, otra)")
        return
    for clave, valor in v.items():
        if clave not in ("streamer", "otra"):
            inf.error(f"{rel}: voces.{clave} no existe (sólo streamer y otra)")
        elif not (isinstance(valor, str) and isinstance(colores, dict) and valor in colores):
            inf.error(f"{rel}: voces.{clave} debe ser el nombre de un color de `colores`")


def _numero(v, minimo=None, maximo=None) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return (minimo is None or v >= minimo) and (maximo is None or v <= maximo)


def validar_voces(v, inf: Informe) -> None:
    rel = "config/voces.yml"
    if not _claves(v, rel, ["activo", "modo", "modelo", "tope_s", "silencio_min_s", "margen_s", "umbral_db"], inf):
        return
    if not isinstance(v["activo"], bool):
        inf.error(f"{rel}: activo debe ser true o false")
    if v["modo"] not in ("principal", "respaldo"):
        inf.error(f"{rel}: modo debe ser principal o respaldo")
    if not (isinstance(v["modelo"], str) and v["modelo"].strip()):
        inf.error(f"{rel}: modelo debe ser texto")
    mm = v.get("modelos_medir", [])
    if not (isinstance(mm, list) and all(isinstance(x, str) and x.strip() for x in mm)):
        inf.error(f"{rel}: modelos_medir debe ser una lista de nombres de modelo")
    if v.get("idioma") is not None and not (isinstance(v["idioma"], str) and re.fullmatch(r"[a-z]{2,3}", v["idioma"])):
        inf.error(f"{rel}: idioma debe ser un código de 2 o 3 letras (es) o null")
    if not _numero(v["tope_s"], 10, 600):
        inf.error(f"{rel}: tope_s debe ser un número de 10 a 600")
    for clave, lo, hi in (("reserva_s", 0, 600), ("idioma_prob_min", 0, 1), ("p_palabra_min", 0, 1)):     # opcionales
        if clave in v and not _numero(v[clave], lo, hi):
            inf.error(f"{rel}: {clave} debe ser un número de {lo} a {hi}")
    if not _numero(v["silencio_min_s"], 0.1, 5):
        inf.error(f"{rel}: silencio_min_s debe ser un número de 0.1 a 5")
    if not _numero(v["margen_s"], 0, 1):
        inf.error(f"{rel}: margen_s debe ser un número de 0 a 1")
    elif _numero(v["silencio_min_s"]) and v["silencio_min_s"] <= 2 * v["margen_s"]:
        inf.error(f"{rel}: silencio_min_s debe ser mayor que el doble de margen_s")
    if not _numero(v["umbral_db"], -90, -10):
        inf.error(f"{rel}: umbral_db debe ser un número de -90 a -10")
    bloques = {"golpes": {"sobre_mediana_db": (1, 60), "piso_db": (-90, 0), "separacion_s": (0.1, 10), "max": (0, 50)},
               "hablantes": {"max": (1, 2), "separacion_min": (0, 1), "minimo_s": (0, 60)},
               "lineas": {"pausa_s": (0.1, 5), "max_s": (1, 10)}}
    for nombre, campos in bloques.items():
        b = v.get(nombre)
        if not isinstance(b, dict):
            inf.error(f"{rel}: falta el bloque {nombre}")
            continue
        for campo, (lo, hi) in campos.items():
            if not _numero(b.get(campo), lo, hi):
                inf.error(f"{rel}: {nombre}.{campo} debe ser un número de {lo} a {hi}")
    al = v.get("alucinaciones", [])
    if not (isinstance(al, list) and all(isinstance(x, str) and x.strip() for x in al)):
        inf.error(f"{rel}: alucinaciones debe ser una lista de frases")
    if not (isinstance(v.get("nombre_otra"), str) and v["nombre_otra"].strip()):
        inf.error(f"{rel}: nombre_otra debe ser texto")


def validar_historia(h, inf: Informe) -> None:
    """config/historia.yml: límites de la historia (#20) y el bloque `segundo` (apagado: GitHub Models retirado)."""
    rel = "config/historia.yml"
    if h is None:
        return
    claves = ["activo", "apertura_min_s", "apertura_max_s", "apertura_final_pct", "momentos_min", "momentos_max",
              "momento_min_s", "camara_lenta_max", "camara_lenta_momento_max_s", "pausa_max", "pausa_s", "zoom_max_n",
              "zoom_max", "zoom_momento_max_s", "sfx_max", "sfx_en_campanas", "ganancia_sfx_db", "segundo"]
    if not _claves(h, rel, claves, inf):
        return

    def num(clave, minimo=None, maximo=None, entero=False, estricto_min=False):
        v = h[clave]
        if isinstance(v, bool) or not isinstance(v, int if entero else (int, float)):
            inf.error(f"{rel}: {clave} debe ser un {'entero' if entero else 'número'}")
            return None
        if minimo is not None and (v <= minimo if estricto_min else v < minimo):
            inf.error(f"{rel}: {clave} debe ser {'mayor que' if estricto_min else 'de'} {minimo}{'' if estricto_min else ' o más'}")
        if maximo is not None and v > maximo:
            inf.error(f"{rel}: {clave} debe ser de {maximo} o menos")
        return v
    for b in ("activo", "sfx_en_campanas"):
        if not isinstance(h[b], bool):
            inf.error(f"{rel}: {b} debe ser true o false")
    a0, a1 = num("apertura_min_s", 0, estricto_min=True), num("apertura_max_s", 0, 10, estricto_min=True)
    if a0 is not None and a1 is not None and a0 > a1:
        inf.error(f"{rel}: apertura_min_s no puede pasar de apertura_max_s")
    num("apertura_final_pct", 0, 100, estricto_min=True)
    m0, m1 = num("momentos_min", 1, entero=True), num("momentos_max", 1, 10, entero=True)
    if m0 is not None and m1 is not None and m0 > m1:
        inf.error(f"{rel}: momentos_min no puede pasar de momentos_max")
    num("momento_min_s", 0, estricto_min=True)
    num("camara_lenta_max", 0, entero=True)
    num("camara_lenta_momento_max_s", 0, 10, estricto_min=True)
    num("pausa_max", 0, entero=True)
    pausa = num("pausa_s", 0, estricto_min=True)
    if pausa is not None and pausa >= 0.5:
        inf.error(f"{rel}: pausa_s debe quedar por debajo del tope de congelado de 0.5 s")
    num("zoom_max_n", 0, entero=True)
    zm = num("zoom_max")
    if zm is not None and not 1.0 < zm <= 1.5:
        inf.error(f"{rel}: zoom_max debe estar entre 1 (sin incluir) y 1.5")
    num("zoom_momento_max_s", 0, 10, estricto_min=True)
    num("sfx_max", 0, entero=True)
    g = num("ganancia_sfx_db", -40, 0)
    s = h["segundo"]
    if not isinstance(s, dict):
        inf.error(f"{rel}: segundo debe ser un bloque")
        return
    if not _claves(s, f"{rel} (segundo)", ["activo", "nombre", "motivo", "url", "modelo", "secreto", "tiempo_s",
                                          "max_por_ciclo"], inf):
        return
    if not isinstance(s["activo"], bool):
        inf.error(f"{rel}: segundo.activo debe ser true o false")
    for k in ("nombre", "motivo", "url", "modelo", "secreto"):
        if not isinstance(s[k], str):
            inf.error(f"{rel}: segundo.{k} debe ser texto")
    if s["activo"] is False and s.get("motivo") != "GitHub Models retirado el 30 jul 2026":
        inf.error(f"{rel}: segundo.motivo debe decir «GitHub Models retirado el 30 jul 2026» mientras esté apagado")
    if s["activo"] is True:
        if not (isinstance(s["url"], str) and s["url"].startswith("https://")):
            inf.error(f"{rel}: segundo.url debe ser https://... cuando segundo está activo")
        for k in ("modelo", "secreto", "nombre"):
            if not (isinstance(s[k], str) and s[k].strip()):
                inf.error(f"{rel}: segundo.{k} no puede ir vacío cuando segundo está activo")
        if isinstance(s["secreto"], str) and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", s["secreto"]):
            inf.error(f"{rel}: segundo.secreto debe ser el NOMBRE de una variable de entorno (nunca la clave)")
    t = s["tiempo_s"]
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 1 <= t <= 300:
        inf.error(f"{rel}: segundo.tiempo_s debe ser un número de 1 a 300")
    mp = s["max_por_ciclo"]
    if isinstance(mp, bool) or not isinstance(mp, int) or mp < 0:
        inf.error(f"{rel}: segundo.max_por_ciclo debe ser un entero de 0 o más")


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
        for clave, tope, prefijo in (("credito", 40, "Fuente:"), ("acotacion", 40, ""), ("escena", 40, "")):
            if clave not in e:
                continue
            v = e[clave]
            if not (isinstance(v, str) and v.strip()):
                inf.error(f"{et}: {clave} debe ser texto no vacío")
            elif prefijo and not v.startswith(prefijo):
                inf.error(f"{et}: {clave} debe empezar con \"{prefijo}\"")
            elif len(v) > tope:
                inf.error(f"{et}: {clave} pasa de {tope} caracteres")


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
    prueba_ret = _cargar(raiz, "config/prueba-retencion.yml", inf)
    if prueba_ret is not None:
        validar_prueba_retencion(prueba_ret, inf)
    tecnica = _cargar(raiz, "config/tecnica.yml", inf)
    if tecnica is not None:
        validar_tecnica(tecnica, inf)
    criticos = _cargar(raiz, "config/criticos.yml", inf)
    if criticos is not None:
        validar_criticos(criticos, inf)
    if (raiz / "config/voces.yml").exists():
        voces = _cargar(raiz, "config/voces.yml", inf)
        if voces is not None:
            validar_voces(voces, inf)
    else:
        inf.aviso("config/voces.yml: no existe: la fábrica no transcribe ni corta silencios")
    validar_historia(_cargar(raiz, "config/historia.yml", inf), inf)
    almacen = _cargar(raiz, "config/almacen.yml", inf)
    if almacen is not None:
        validar_almacen(almacen, inf)
    informativas = _cargar(raiz, "informativas.yml", inf)
    if informativas is not None:
        validar_informativas(informativas, datos["marca"], inf)
    musica = _cargar(raiz, "marca/musica/pistas.yml", inf)
    if musica is not None:
        validar_musica(raiz, musica, inf)
    validar_reglas_kevin(raiz, inf)
    validar_sfx(raiz, inf)
    validar_pausa(raiz, inf)
    validar_pausa_t(raiz, inf)
    validar_csv(raiz, "marcador.csv", CABECERA_MARCADOR, inf)
    validar_csv(raiz, "envios-pendientes.csv", CABECERA_ENVIOS, inf)
    validar_sin_media(raiz, inf)
    validar_sin_claves(raiz, inf)
    return inf


def validar_sfx(raiz: Path, inf: Informe) -> None:
    """`marca/sfx/efectos.yml` (si existe): sólo CC0, duraciones coherentes y LICENCIA.md si está verificada."""
    rel = "marca/sfx/efectos.yml"
    ruta = Path(raiz) / rel
    if not ruta.is_file():
        return
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        inf.error(f"{rel}: YAML inválido: {e}")
        return
    if not isinstance(datos, dict):
        inf.error(f"{rel}: debe ser un diccionario")
        return
    permitidas = datos.get("licencias_permitidas")
    if not (isinstance(permitidas, list) and permitidas and set(permitidas) <= {"CC0"}):
        inf.error(f"{rel}: licencias_permitidas debe ser una lista que sólo contenga CC0")
        permitidas = []
    if not (isinstance(datos.get("release"), str) and datos["release"]):
        inf.error(f"{rel}: falta release")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    dmin, dmax = datos.get("duracion_min_s"), datos.get("duracion_max_s")
    if not (num(dmin) and dmin > 0):
        inf.error(f"{rel}: duracion_min_s debe ser un número positivo")
    if not (num(dmax) and dmax > 0 and (not num(dmin) or dmax > dmin)):
        inf.error(f"{rel}: duracion_max_s debe ser un número mayor que duracion_min_s")
    minimo = datos.get("minimo")
    if not (isinstance(minimo, int) and not isinstance(minimo, bool) and minimo >= 1):
        inf.error(f"{rel}: minimo debe ser un entero de 1 o más")
    efectos = datos.get("efectos")
    if not isinstance(efectos, list):
        inf.error(f"{rel}: efectos debe ser una lista")
        return
    vistos: set[str] = set()
    for i, e in enumerate(efectos):
        if not isinstance(e, dict):
            inf.error(f"{rel}: efectos[{i}] debe ser un diccionario")
            continue
        eid = e.get("id")
        et = f"{rel}: efecto {eid if isinstance(eid, str) else i}"
        if not (isinstance(eid, str) and RE_ID.match(eid)):
            inf.error(f"{et}: id inválido (minúsculas, números y guiones)")
        elif eid in vistos:
            inf.error(f"{et}: id repetido")
        else:
            vistos.add(eid)
        if e.get("licencia") not in permitidas:
            inf.error(f"{et}: licencia {e.get('licencia')!r} fuera de licencias_permitidas (sólo CC0)")
        if not _es_https(e.get("pagina")):
            inf.error(f"{et}: pagina debe ser un enlace https")
        if not (isinstance(e.get("tipo"), str) and e["tipo"]):
            inf.error(f"{et}: falta tipo")
        asset = e.get("asset")
        if not (isinstance(asset, str) and asset and "/" not in asset and "\\" not in asset
                and not asset.startswith(".")):
            inf.error(f"{et}: asset debe ser un nombre de archivo sin barras")
        if e.get("estado") not in ("candidata", "verificada", "descartada"):
            inf.error(f"{et}: estado debe ser candidata, verificada o descartada")
        if e.get("estado") == "verificada":
            if not (isinstance(e.get("sha256"), str) and RE_SHA256.match(e["sha256"])):
                inf.error(f"{et}: verificada exige sha256 hexadecimal de 64 caracteres")
            lic = Path(raiz) / "marca" / "sfx" / str(eid) / "LICENCIA.md"
            if not lic.is_file():
                inf.error(f"{et}: verificada exige marca/sfx/{eid}/LICENCIA.md")
            else:
                texto = lic.read_text(encoding="utf-8", errors="ignore")
                faltan = [c for c in CAMPOS_LICENCIA
                          if not re.search(rf"(?m)^\s*[-*]?\s*\**{re.escape(c[:-1])}(?::\**|\**:)[ \t]*\S", texto)]
                if faltan:
                    inf.error(f"{et}: LICENCIA.md sin las líneas {', '.join(faltan)}")
                elif not re.search(r"(?m)^\s*[-*]?\s*\**Licencia(?::\**|\**:)[^\n]*\bCC0\b", texto):
                    inf.error(f"{et}: la línea Licencia: de LICENCIA.md debe decir CC0")


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

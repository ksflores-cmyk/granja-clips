"""Formatos con valor añadido: «Mejores momentos» y «Mejores reacciones» (tarea #7).

Un compilado junta 2 o 3 clips del mismo día (fichas T o A vigentes), dura de 30 a 60 s, pasa de uno a otro con
corte seco y da crédito a cada streamer en pantalla y en el texto. Aquí vive la lógica sin video (elegir, combinar,
textos, fila del marcador y el orquestador `hacer_compilado`); el video lo arma `fabrica.editar.compilado`.

No se conecta al ciclo: nunca publica ni sube el contador de toma. Cualquier NO es un descarte con sus motivos.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from . import descargar as mod_descargar
from . import fuentes, ojos, puertas
from .config import cargar_yaml, raiz_repo
from .detectar import Candidato
from .editar.lienzo import Toma, _limpia
from .editar.marca import Marca
from .editar.toma import Resultado, armar, copia_para_ojos, lapiz_de
from . import textos as mod_textos   # la tarea #8: límites por red, título, hashtags y comprobaciones finales
from .publicar import Descartada, _comprobar_repetido, _limpio
from .registro import ZONA, contador_toma

TIPOS_APTOS = ("T", "A")
TIPOS_MUESTRA = ("T", "A", "B")
PLATAFORMAS = ("shorts", "tiktok", "reels")
SEP_CREDITOS = " · "
MARGEN_DURACION_S = 0.1     # el relleno del AAC alarga el MP4 unos 37 ms: la suma deja margen en los dos bordes


def hora_local(dt: datetime) -> datetime:
    """A hora de Guatemala. Una fecha sin zona se toma como hora de Guatemala (no la del sistema)."""
    return (dt.replace(tzinfo=ZONA) if dt.tzinfo is None else dt).astimezone(ZONA)


# ---------------------------------------------------------------- configuración

def _lista_num(cfg: dict, clave: str, rel: str, minimo: float, maximo: float, entero: bool = False) -> tuple:
    v = cfg.get(clave)
    tipo = int if entero else (int, float)
    if not (isinstance(v, list) and len(v) == 2
            and all(isinstance(x, tipo) and not isinstance(x, bool) for x in v)):
        raise ValueError(f"{rel}: {clave} debe ser una lista de 2 números")
    lo, hi = v
    if not (minimo <= lo <= hi <= maximo):
        raise ValueError(f"{rel}: {clave} debe cumplir {minimo:g} <= {lo} <= {hi} <= {maximo:g}")
    return lo, hi


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t).upper()) if unicodedata.category(c) != "Mn")


def cargar_config(raiz=None) -> dict:
    """Lee y valida `config/formatos.yml`. Lanza `ValueError` con el motivo."""
    raiz = raiz_repo(raiz)
    rel = "config/formatos.yml"
    try:
        cfg = cargar_yaml(rel, raiz)
    except OSError as e:
        raise ValueError(f"{rel}: no se pudo leer ({e})") from None
    if not isinstance(cfg, dict):
        raise ValueError(f"{rel}: debe ser un diccionario")
    temporada = cargar_yaml("temporada.yml", raiz)
    pilares = (temporada or {}).get("pilares") or {}
    marca = Marca.cargar(raiz)
    pilar = cfg.get("pilar")
    if not isinstance(pilar, str) or pilar not in pilares:
        raise ValueError(f"{rel}: el pilar {pilar!r} no está en temporada.yml")
    _lista_num(cfg, "duracion_s", rel, 1, 60)
    _lista_num(cfg, "segmentos", rel, 2, 3, entero=True)
    _lista_num(cfg, "segmento_s", rel, ojos.RECORTE_MIN_S, ojos.RECORTE_MAX_S)
    for clave in ("max_por_creador", "max_por_dia_por_plataforma"):
        v = cfg.get(clave)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ValueError(f"{rel}: {clave} debe ser un entero de 1 o más")
    formatos = cfg.get("formatos")
    if not isinstance(formatos, dict) or not formatos:
        raise ValueError(f"{rel}: formatos debe ser un diccionario no vacío")
    prohibidos = [_sin_acentos(p) for p in marca.ganchos_prohibidos()]
    for fid, f in formatos.items():
        et = f"{rel}: formatos.{fid}"
        if not isinstance(f, dict):
            raise ValueError(f"{et} debe ser un diccionario")
        for campo in ("nombre", "pilar_segmento", "gancho", "palabra_lapiz", "unidad"):
            if not isinstance(f.get(campo), str) or not f[campo].strip():
                raise ValueError(f"{et}: falta {campo} (texto)")
        if f["pilar_segmento"] not in pilares:
            raise ValueError(f"{et}: pilar_segmento {f['pilar_segmento']!r} no está en temporada.yml")
        palabras = f["gancho"].split()
        if len(palabras) > marca.max_palabras_gancho:
            raise ValueError(f"{et}: el gancho tiene {len(palabras)} palabras y el máximo es "
                             f"{marca.max_palabras_gancho}")
        if _limpia(f["palabra_lapiz"]) not in [_limpia(p) for p in palabras]:
            raise ValueError(f"{et}: palabra_lapiz {f['palabra_lapiz']!r} no está en el gancho")
        malos = [p for p in prohibidos if p in _sin_acentos(f["gancho"])]
        if malos:
            raise ValueError(f"{et}: el gancho usa un gancho genérico prohibido ({', '.join(malos)})")
        tags = f.get("hashtags")
        if not (isinstance(tags, list) and tags and all(isinstance(t, str) and t.startswith("#") and len(t) > 1
                                                         for t in tags)):
            raise ValueError(f"{et}: hashtags debe ser una lista de textos que empiezan con #")
    return cfg


def formato_con_limites(cfg: dict, formato_id: str) -> dict:
    """Los límites generales del archivo más los campos del formato, con su `id`: lo que recibe `compilar`."""
    if formato_id not in cfg["formatos"]:
        raise ValueError(f"formato desconocido: {formato_id!r}")
    base = {k: v for k, v in cfg.items() if k != "formatos"}
    return {**base, **cfg["formatos"][formato_id], "id": formato_id}


# ---------------------------------------------------------------- modelo

@dataclass
class Segmento:
    fuente: Path
    inicio: float
    duracion: float
    toma: Toma
    lineas: list
    ficha: dict
    creador: str
    fecha: date                  # día local de Guatemala del clip
    id_origen: str


@dataclass
class Compilado:
    formato: str
    segmentos: list[Segmento]

    @property
    def numero(self) -> int:
        return self.segmentos[0].toma.numero

    @property
    def fecha(self) -> date:
        return self.segmentos[0].fecha


# ---------------------------------------------------------------- elegir y combinar

def apto(ficha: dict | None, hoy: date, pilar_segmento: str, muestra: bool = False) -> tuple[bool, str]:
    """¿Esta ficha puede dar un segmento de compilado? Con `muestra=True` (sólo el comando de muestras) se aceptan
    fichas `uso: muestras` (tipo T, A o B); esas nunca se publican."""
    if not isinstance(ficha, dict):
        return False, "ficha ausente"
    tipo = ficha.get("tipo")
    if muestra:
        if tipo not in TIPOS_MUESTRA:
            return False, f"tipo {tipo!r}: las muestras de compilado son de tipo T, A o B"
        if ficha.get("uso") != "muestras":
            return False, f"uso {ficha.get('uso')!r}: debe ser muestras"
        if ficha.get("estado") != "vigente":
            return False, f"estado {ficha.get('estado')!r}: debe ser vigente"
        vence = fuentes._a_fecha(ficha["vence"]) if ficha.get("vence") is not None else None
        if ficha.get("vence") is not None and (vence is None or vence < fuentes._a_fecha(hoy)):
            return False, f"vencida o ilegible ({ficha.get('vence')!r})"
        # El pilar no se exige en las muestras: las fichas CC BY de muestra declaran un solo pilar cada una
        # (Sintel: primeras_veces; BBB: momentos) y las dos muestras mezclan sus metrajes.
        return True, "ok (muestra)"
    if tipo not in TIPOS_APTOS:
        return False, f"tipo {tipo!r}: los compilados sólo usan fichas T o A"
    ok, motivo = fuentes.es_publicable(ficha, hoy, None, pilar_segmento)
    if not ok:
        return False, motivo
    if tipo == "A" and (ficha.get("reglas") or {}).get("permite_compilados") is not True:
        return False, "ficha A sin reglas.permite_compilados: true"
    return True, "ok"


def _clave_creador(c) -> str:
    return re.sub(r"\s+", " ", str(c or "")).strip().lower()


def elegir_candidatos(candidatos: list, fichas: list[dict], formato: str, ahora: datetime, cfg: dict,
                      muestra: bool = False) -> list:
    """Candidatos aptos del mismo día local (Guatemala) que `ahora`, por vistas por hora de mayor a menor."""
    if formato not in cfg["formatos"]:
        raise ValueError(f"formato desconocido: {formato!r}")
    pilar = cfg["formatos"][formato]["pilar_segmento"]
    local = hora_local(ahora)
    hoy = local.date()
    por_id = {f.get("id"): f for f in fichas}
    aptos = []
    for c in candidatos:
        ficha = por_id.get(c.fuente_id)
        if not apto(ficha, hoy, pilar, muestra)[0]:
            continue
        if c.creado_en is None or hora_local(c.creado_en).date() != hoy:
            continue
        aptos.append(c)
    aptos.sort(key=lambda c: c.vistas_por_hora, reverse=True)
    elegidos, ids, por_creador = [], set(), {}
    tope = cfg["segmentos"][1]
    for c in aptos:
        k = _clave_creador(c.creador)
        if c.id_origen in ids or por_creador.get(k, 0) >= cfg["max_por_creador"]:
            continue
        ids.add(c.id_origen)
        por_creador[k] = por_creador.get(k, 0) + 1
        elegidos.append(c)
        if len(elegidos) >= tope:
            break
    return elegidos if len(elegidos) >= cfg["segmentos"][0] else []


def combinar(segmentos: list[Segmento], cfg: dict) -> list[Segmento] | None:
    """El primer prefijo (de 3, luego de 2) cuya suma de duraciones cae en `duracion_s`; None si no hay."""
    if len({s.fecha for s in segmentos}) > 1:
        return None
    lo_n, hi_n = cfg["segmentos"]
    lo, hi = cfg["duracion_s"]
    for n in range(min(len(segmentos), hi_n), lo_n - 1, -1):
        total = sum(s.duracion for s in segmentos[:n])
        if lo + MARGEN_DURACION_S <= total <= hi - MARGEN_DURACION_S:
            return list(segmentos[:n])
    return None


# ---------------------------------------------------------------- tomas y créditos

def _login(creador: str, ficha: dict | None = None) -> str:
    """El login real de Twitch: `twitch.tv/<login>` ya escrito en el creador, o `twitch_login` de la ficha (si el
    creador es el de la ficha); como último recurso, el nombre visible normalizado."""
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", str(creador or ""))
    if m:
        return m.group(1).lower()
    ficha = ficha or {}
    ficha_login = str(ficha.get("twitch_login") or "").strip().lower()
    if ficha_login and _clave_creador(creador) in (_clave_creador(ficha.get("creador")), ficha_login, ""):
        return ficha_login
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", str(ficha.get("creador") or ""))
    if m and _clave_creador(creador) == _clave_creador(ficha.get("creador")):
        return m.group(1).lower()
    return re.sub(r"[^a-z0-9_]", "", str(creador or "").lower())


def etiqueta_credito(ficha: dict, creador: str, plataforma: str | None = None) -> str:
    """Cómo se nombra al streamer: `twitch.tv/<login>` en ficha T; en una A, su etiqueta de la red si la hay."""
    creador = _limpio(creador)
    if not creador:
        return ""
    if ficha.get("tipo") == "T":
        return f"twitch.tv/{_login(creador, ficha) or creador}"
    if plataforma:
        e = fuentes.etiqueta_creador(ficha, creador, plataforma)
        if e:
            return e
    return creador


def toma_de_segmento(propia: Toma, formato: dict, numero: int, indice: int, creador: str, ficha: dict) -> Toma:
    """La toma de un segmento: todos con el mismo número, su escena y `Con: <creador>`. El primero lleva el
    gancho del formato y su palabra del lápiz; los demás, su propio gancho (el de Gemini)."""
    primero = indice == 0
    return Toma(numero=numero,
                gancho=formato["gancho"] if primero else propia.gancho,
                palabra_lapiz=formato["palabra_lapiz"] if primero else propia.palabra_lapiz,
                lapiz=lapiz_de(numero), escena=propia.escena,
                credito=f"Con: {etiqueta_credito(ficha, creador)}")


def _linea_credito(segmentos: list[Segmento], plataforma: str) -> str:
    vistos, etiquetas = set(), []
    for s in segmentos:
        e = etiqueta_credito(s.ficha, s.creador, plataforma)
        if e and e.lower() not in vistos:
            vistos.add(e.lower())
            etiquetas.append(e)
    return "🎥 " + SEP_CREDITOS.join(etiquetas)


def _hashtag_del_juego(nombre: str) -> str:
    solo = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", str(nombre or ""))
    return f"#{solo}" if solo else ""


def textos_compilado(compilado: Compilado, plataforma: str, temporada: dict, cfg: dict, raiz=None,
                     hoy: date | None = None) -> dict:
    """Texto de la publicación de un compilado: {texto, titulo, credito, etiquetas}; `Descartada` si no cumple.

        Toma 012. Mejores momentos del día en GTA 6
        <sólo Reels: «3 momentos de hoy, de 3 streamers.»>
        🎥 twitch.tv/a · twitch.tv/b · twitch.tv/c
        <divulgación de cada ficha, si la hay>
        #GTA6 #MejoresMomentos #Streamers
    """
    cfg_t = mod_textos.cargar_config()
    lim = cfg_t["limites"].get(plataforma)
    if lim is None:
        raise Descartada(f"plataforma desconocida: {plataforma}")
    formato = cfg["formatos"].get(compilado.formato)
    if formato is None:
        raise Descartada(f"formato desconocido: {compilado.formato}")
    segs = compilado.segmentos
    if not segs:
        raise Descartada("compilado sin segmentos")
    for s in segs:
        if not _limpio(s.creador):
            raise Descartada(f"el segmento {s.id_origen} no trae creador: no se publica sin crédito")
    juego = _limpio(temporada.get("nombre"))
    if not juego:
        raise Descartada("temporada sin nombre de juego")
    numero = compilado.numero
    num = f"{int(numero):03d}" if str(numero).isdigit() else str(numero)
    primera = f"Toma {num}. {_limpio(formato['gancho'])} en {juego}"
    credito = _linea_credito(segs, plataforma)
    for s in segs:           # cada streamer debe salir en la línea del crédito
        if etiqueta_credito(s.ficha, s.creador, plataforma).lower() not in credito.lower():
            raise Descartada(f"el creador {s.creador} no aparece en la línea del crédito")
    bloques = [primera]
    if plataforma == "reels":
        n_creadores = len({_clave_creador(s.creador) for s in segs})
        streamers = "streamer" if n_creadores == 1 else "streamers"
        bloques.append(f"{len(segs)} {formato['unidad']} de hoy, de {n_creadores} {streamers}.")
    cola = [credito]
    vistos_div: set[str] = set()
    for s in segs:
        div = str((s.ficha.get("reglas") or {}).get("divulgacion") or "").strip()
        if div.lower() not in ("", "ninguna") and div.lower() not in vistos_div:
            vistos_div.add(div.lower())
            cola.append(div)
    obligatorias: list[str] = []
    for s in segs:
        for e in (s.ficha.get("reglas") or {}).get("etiquetas_obligatorias") or []:
            e = str(e).strip()
            if e and e.lower() not in {x.lower() for x in obligatorias}:
                obligatorias.append(e)
    deseadas = [mod_textos.nfc(h).strip() for h in (temporada.get("hashtags") or [])]
    if not deseadas:
        deseadas = [_hashtag_del_juego(temporada.get("nombre"))]
    del_formato = [mod_textos.nfc(h).strip() for h in formato["hashtags"]]
    tope = lim["max_hashtags"]
    etiquetas = mod_textos._sin_repetir(mod_textos._validas(list(obligatorias)), set())
    visto = {e.lower() for e in etiquetas}
    n_tags = lambda: len([e for e in etiquetas if e.startswith("#")])  # noqa: E731
    # El hashtag propio del formato (#MejoresMomentos) siempre cabe: con 3 del juego en la temporada y tope 3
    # en Shorts/TikTok, el orden «temporada y luego formato» lo dejaría fuera (decisión al fusionar #7 con #8).
    propio = next((h for h in del_formato if mod_textos.es_hashtag(h) and h.lower() not in visto), None)
    reservado = 1 if propio else 0
    for h in deseadas:
        if mod_textos.es_hashtag(h) and h.lower() not in visto and n_tags() < tope - reservado:
            visto.add(h.lower())
            etiquetas.append(h)
    for h in ([propio] if propio else []) + del_formato:
        if mod_textos.es_hashtag(h) and h.lower() not in visto and n_tags() < tope:
            visto.add(h.lower())
            etiquetas.append(h)
    if n_tags() < lim["min_hashtags"]:
        raise Descartada(f"{plataforma} necesita de {lim['min_hashtags']} a {lim['max_hashtags']} "
                         "etiquetas y no hay suficientes")
    if etiquetas:
        cola.append(" ".join(etiquetas))
    texto = "\n\n".join(bloques) + "\n\n" + "\n".join(cola)
    for s in segs:   # un emoji, palabras a evitar de cada ficha y largo de la red, como en toda toma (textos.py)
        mod_textos._comprobar_final(texto, s.ficha, plataforma, cfg_t)
    titulo = mod_textos._titulo_de(primera, plataforma, cfg_t)
    if raiz is not None:
        # El gancho del formato se repite a propósito cada día: lo que no se repite es el compilado del mismo
        # día, así que para el control de «gancho repetido» el gancho lleva la fecha del compilado.
        _comprobar_repetido(raiz, texto, plataforma,
                            {"numero": numero, "gancho": f"{formato['gancho']} {compilado.fecha.isoformat()}"}, hoy)
    return {"texto": texto, "titulo": titulo, "credito": credito, "etiquetas": etiquetas}


def fila_marcador(compilado: Compilado, ahora: datetime, duracion: float | None = None, raiz=None) -> dict:
    """Fila futura del marcador (como `toma.fila_marcador`, con `pilar: compilados` y los datos unidos)."""
    local = hora_local(ahora)
    segs = compilado.segmentos
    campanas = []
    for s in segs:
        c = s.ficha.get("campana")
        if c and c not in campanas:
            campanas.append(str(c))
    total = duracion if duracion is not None else sum(s.duracion for s in segs)
    return {"toma": compilado.numero, "fecha": local.date().isoformat(), "hora": local.strftime("%H:%M"),
            "pilar": cargar_pilar(raiz), "fuente_id": "+".join(str(s.ficha.get("id")) for s in segs),
            "campana": "+".join(campanas) or None, "gancho": segs[0].toma.gancho,
            "duracion_s": round(total, 2), "credito": SEP_CREDITOS.join(s.toma.credito for s in segs)}


def cargar_pilar(raiz=None) -> str:
    return str(cargar_yaml("config/formatos.yml", raiz)["pilar"])


# ---------------------------------------------------------------- preparar un segmento

class ErrorSegmento(RuntimeError):
    """Un candidato no da segmento. El mensaje es el motivo."""


@dataclass
class Contexto:
    raiz: Path
    marca: Marca
    temporada: dict
    cfg: dict
    formato_id: str
    numero: int
    ahora: datetime
    pila: ExitStack            # las fuentes descargadas viven hasta que se compone el compilado
    abrir: object = None
    ejecutor: object = None
    transporte: object = None
    espera: object = time.sleep
    entorno: object = None


def _recorte_de(cfg: dict, ficha: dict) -> tuple[float, float]:
    lo_f, hi_f = ojos.rango_recorte(ficha)
    lo, hi = max(float(cfg["segmento_s"][0]), lo_f), min(float(cfg["segmento_s"][1]), hi_f)
    if lo > hi:
        raise ErrorSegmento(f"el recorte de la ficha ({lo_f:g}-{hi_f:g} s) no cruza el del formato "
                            f"({cfg['segmento_s'][0]:g}-{cfg['segmento_s'][1]:g} s)")
    return lo, hi


def preparar_segmento(candidato: Candidato, ficha: dict, ctx: Contexto) -> Segmento:
    """Descarga → ojos → validar_respuesta → armar. La fuente queda abierta en `ctx.pila`."""
    hoy = hora_local(ctx.ahora).date()
    try:
        d = ctx.pila.enter_context(mod_descargar.descargar(ctx.raiz, candidato, ficha, hoy, abrir=ctx.abrir,
                                                            ejecutor=ctx.ejecutor))
    except mod_descargar.ErrorDescarga as e:
        raise ErrorSegmento(f"descarga: {e}") from None
    temporada = ojos.temporada_de(ficha, ctx.temporada)
    juego = ojos.juego_de(ficha, temporada)
    recorte = _recorte_de(ctx.cfg, ficha)
    with tempfile.TemporaryDirectory(prefix="ojos-compilado-") as carpeta_ojos:
        video_ojos = copia_para_ojos(d.ruta, ctx.raiz, Path(carpeta_ojos))
        mirada = ojos.mirar(video_ojos, ctx.marca, temporada, raiz=ctx.raiz, duracion=d.duracion,
                            transporte=ctx.transporte, espera=ctx.espera, entorno=ctx.entorno, juego=juego,
                            nota=ojos.nota_de_ficha(ficha) or None, recorte=recorte)
    if mirada.datos is None:
        if mirada.pasajero:
            puertas.quitar_visto(ctx.raiz, candidato.id_origen)
            raise ErrorSegmento(f"ojos: {mirada.motivo} (pasajero: se reintenta)")
        raise ErrorSegmento(f"ojos: {mirada.motivo}")
    ok, motivos = ojos.validar_respuesta(mirada.datos, ctx.marca, temporada, duracion=d.duracion, juego=juego,
                                         recorte=recorte)
    if not ok:
        raise ErrorSegmento("; ".join(f"ojos: {m}" for m in motivos))
    entrada = fuentes.entrada_material(ficha, url=candidato.url)
    creador = (entrada or {}).get("creador") or candidato.creador or ficha.get("creador") or ""
    toma, lineas, inicio, dur = armar(mirada.datos, ctx.marca, ficha, ctx.numero, creador)
    return Segmento(fuente=d.ruta, inicio=inicio, duracion=dur, toma=toma, lineas=lineas, ficha=ficha,
                    creador=str(creador), fecha=hora_local(candidato.creado_en).date(),
                    id_origen=candidato.id_origen)


# ---------------------------------------------------------------- orquestador

def hacer_compilado(raiz, formato_id: str, candidatos: list, fichas: list[dict], ahora: datetime, *,
                    salida: Path | None = None, hacer_segmento_fn=None, abrir=None, ejecutor=None,
                    transporte=None, espera=time.sleep, entorno=None) -> Resultado:
    """Elegir → preparar cada segmento → combinar → compilar → puertas. Nunca publica.

    `salida`: carpeta del MP4. Si no se da, se crea una temporal que sólo se conserva si el compilado sale ok.
    En cualquier descarte o error no queda ningún MP4. `hacer_segmento_fn(candidato, ficha, contexto)` devuelve
    un `Segmento` (por defecto `preparar_segmento`); las pruebas pasan uno falso.
    """
    propia = salida is None
    carpeta: Path | None = None
    creados: list[Path] = []          # sólo los archivos de ESTE compilado: nunca se borra el de otro en la carpeta
    r = None
    try:
        carpeta = Path(salida) if salida is not None else Path(tempfile.mkdtemp(prefix="granja-compilado-"))
        r = _hacer_compilado(raiz, formato_id, candidatos, fichas, ahora, carpeta, creados,
                             hacer_segmento_fn or preparar_segmento,
                             dict(abrir=abrir, ejecutor=ejecutor, transporte=transporte, espera=espera,
                                  entorno=entorno))
    except Exception as e:  # noqa: BLE001 (nada debe subir sin control: es un descarte)
        r = Resultado(False, [f"error inesperado: {type(e).__name__}: {e}"])
    finally:
        if r is None or not r.ok:
            if carpeta is not None:
                for f in creados:
                    f.unlink(missing_ok=True)
                if propia:
                    shutil.rmtree(carpeta, ignore_errors=True)
    return r


def _hacer_compilado(raiz, formato_id, candidatos, fichas, ahora, carpeta: Path, creados: list, fn, extras) -> Resultado:
    from .editar import compilado as mod_compilado
    raiz = raiz_repo(raiz)
    cfg = cargar_config(raiz)
    if formato_id not in cfg["formatos"]:
        return Resultado(False, [f"formato desconocido: {formato_id}"])
    formato = formato_con_limites(cfg, formato_id)
    elegidos = elegir_candidatos(candidatos, fichas, formato_id, ahora, cfg)
    if not elegidos:
        return Resultado(False, [f"menos de {cfg['segmentos'][0]} candidatos aptos del día para "
                                 f"{cfg['formatos'][formato_id]['nombre']}"])
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = Marca.cargar(raiz)
    temporada = cargar_yaml("temporada.yml", raiz)
    numero = contador_toma(raiz)
    por_id = {f.get("id"): f for f in fichas}
    omitidos: list[str] = []
    segmentos: list[Segmento] = []
    with ExitStack() as pila:
        ctx = Contexto(raiz=raiz, marca=marca, temporada=temporada, cfg=cfg, formato_id=formato_id,
                       numero=numero, ahora=ahora, pila=pila, **extras)
        for c in elegidos:
            try:
                segmentos.append(fn(c, por_id[c.fuente_id], ctx))
            except (ErrorSegmento, ValueError, OSError, subprocess.SubprocessError) as e:
                omitidos.append(f"segmento {c.id_origen}: {e}")
        if len(segmentos) < cfg["segmentos"][0]:
            return Resultado(False, [*omitidos, f"quedan {len(segmentos)} segmentos y el mínimo es "
                                                f"{cfg['segmentos'][0]}"])
        total = combinar(segmentos, cfg)
        if total is None:
            if len({s.fecha for s in segmentos}) > 1:
                return Resultado(False, [*omitidos, "los segmentos no son del mismo día"])
            lo, hi = cfg["duracion_s"]
            suma = ", ".join(f"{sum(s.duracion for s in segmentos[:n]):.1f}"
                             for n in range(min(len(segmentos), cfg["segmentos"][1]), cfg["segmentos"][0] - 1, -1))
            return Resultado(False, [*omitidos, f"ninguna combinación suma de {lo:g} a {hi:g} s (suman {suma} s)"])
        segmentos = [replace(s, toma=toma_de_segmento(s.toma, formato, numero, i, s.creador, s.ficha))
                     for i, s in enumerate(total)]
        mp4 = carpeta / f"compilado-{numero:03d}-{formato_id}.mp4"
        plan_json = mp4.with_suffix(".plan.json")
        creados += [mp4, plan_json]
        try:
            with tempfile.TemporaryDirectory(prefix="trabajo-compilado-") as trabajo:
                plan, puntos = mod_compilado.compilar(marca, formato, segmentos, mp4, trabajo=Path(trabajo))
        except mod_compilado.SegmentoRojo as e:
            mp4.unlink(missing_ok=True)
            return Resultado(False, [*omitidos, *e.motivos], puntos=e.puntos)
        except (ValueError, subprocess.SubprocessError, OSError) as e:
            mp4.unlink(missing_ok=True)
            return Resultado(False, [*omitidos, f"editor: {type(e).__name__}: {e}"])
        rojos = [f"puerta del compilado: {n}" + (f" ({det})" if det else "") for n, ok_p, det in puntos if not ok_p]
        if rojos:
            mp4.unlink(missing_ok=True)
            plan_json.unlink(missing_ok=True)
            return Resultado(False, [*omitidos, *rojos], None, plan, None, None, puntos)
        comp = Compilado(formato_id, segmentos)
        plan["omitidos"] = omitidos
        plan_json.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return Resultado(True, [], mp4, plan, None, None, puntos,
                         fila_marcador(comp, ahora, plan["duracion"], raiz))

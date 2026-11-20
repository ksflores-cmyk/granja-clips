"""Texto de cada publicación (playbook, sección 10): título, descripción y hashtags por red.

Aquí vive la lógica pura (sin estado ni red). `publicar.textos` delega en `armar` y añade la
comprobación de «no se repite en 7 días», que necesita el estado del repo.

Límites y listas de palabras: `config/textos.yml`. Conteo en UTF-16 (un emoji cuenta 2); en Reels
cada salto de línea cuenta 2.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .config import cargar_yaml
from .fuentes import etiqueta_creador

PLATAFORMAS = ("shorts", "tiktok", "reels")
CREDITO_INFORMATIVA = "@RockstarGames"


class Descartada(Exception):
    """La toma no se publica; el mensaje es el motivo."""


# ---------------------------------------------------------------- utilidades de texto

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕⌀-⏿\U0001F1E6-\U0001F1FF]")
_RESTO_EMOJI = re.compile("[️‍⃣]")
_GTA = re.compile(r"(?<![#\w])GTA\s*(?:6|VI)\b|(?<![#\w])GTA6\b", re.IGNORECASE)
_HASHTAG_INCRUSTADO = re.compile(r"#\S*")
_HASHTAG_VALIDO = re.compile(r"#\w+")
_PALABRA = re.compile(r"\w+")


def contar_emojis(texto: str) -> int:
    return len(_EMOJI.findall(texto))


def utf16(s: str) -> int:
    """Largo como lo cuentan las redes: unidades UTF-16 (un emoji ocupa 2)."""
    return len(s.encode("utf-16-le")) // 2


def _sin_marcas(s) -> str:
    """Sin `<` ni `>`, sin emojis y con los espacios colapsados."""
    s = str(s or "").replace("<", "").replace(">", "")
    s = _RESTO_EMOJI.sub("", _EMOJI.sub("", s))
    return " ".join(s.split())


def _limpio(s) -> str:
    """Texto sin emojis ni `<>`, con «GTA 6» escrito siempre igual (el hashtag #GTA6 no se toca)."""
    return _GTA.sub("GTA 6", _sin_marcas(s))


def _sin_hashtags(t: str) -> str:
    return " ".join(_HASHTAG_INCRUSTADO.sub("", t).split())


def _n(s: str) -> str:
    """Sin acentos ni mayúsculas, para comparar."""
    d = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in d if not unicodedata.combining(c)).lower()


def _palabras(s: str) -> list[str]:
    return _PALABRA.findall(_n(s))


def es_de_campana(ficha: dict) -> bool:
    """Ficha de campaña con brief: tiene `campana` y su material trae el creador y sus etiquetas por red."""
    return bool(ficha.get("campana")) and any(isinstance(m, dict) for m in ficha.get("material") or [])


def es_hashtag(e: str) -> bool:
    return bool(_HASHTAG_VALIDO.fullmatch(e))


def _hashtags(etiquetas) -> list[str]:
    return [e for e in etiquetas if e.startswith("#")]


# ---------------------------------------------------------------- configuración

@lru_cache(maxsize=1)
def cargar_config() -> dict:
    """`config/textos.yml` más los ganchos genéricos de `marca.yml`. Siempre del repo del código."""
    from .editar.marca import Marca  # import tardío: evita ciclos y la carga de PIL al importar
    cfg = dict(cargar_yaml("config/textos.yml"))
    cfg["_genericos"] = [_n(f) for f in [*Marca.cargar().ganchos_prohibidos(), *cfg.get("genericos", [])]]
    cfg["_vacias"] = {_n(v) for v in cfg.get("vacias", [])}
    cfg["_ingles"] = {_n(v) for v in cfg.get("ingles", [])}
    cfg["_siglas"] = {_n(s): s for s in cfg.get("siglas", [])}
    return cfg


def largo(texto: str, plataforma: str, cfg: dict | None = None) -> int:
    """Largo del texto para esa red: UTF-16 y, en Reels, cada salto de línea cuenta 2."""
    cfg = cfg or cargar_config()
    extra = texto.count("\n") * (int(cfg["limites"][plataforma].get("salto_cuenta", 1)) - 1)
    return utf16(texto) + extra


# ---------------------------------------------------------------- gancho

def _oracion(g: str, siglas: dict) -> str:
    """Un gancho que llega todo en mayúsculas pasa a tipo oración, conservando las siglas."""
    if not any(c.isalpha() for c in g) or g != g.upper():
        return g
    g = _PALABRA.sub(lambda m: siglas.get(_n(m.group(0)), m.group(0).lower()), g)
    for i, c in enumerate(g):
        if c.isalpha():
            return g[:i] + c.upper() + g[i + 1:]
    return g


def _nombres_a_ignorar(toma: dict, ficha: dict) -> set[str]:
    """Palabras del creador, del login y del juego: no cuentan como contenido del gancho."""
    fuentes = [toma.get("creador"), ficha.get("creador"), ficha.get("twitch_login"), ficha.get("juego"),
               ficha.get("credito"), *(toma.get("creadores") or [])]
    palabras: set[str] = set()
    for f in fuentes:
        palabras.update(_palabras(str(f or "")))
    palabras.update({"twitch", "tv", "https", "www"} if palabras else set())
    return palabras


def comprobar_gancho(gancho: str, toma: dict, ficha: dict, cfg: dict) -> None:
    """Descarta (con motivo) un gancho genérico, vacío de contenido o con relleno en inglés."""
    plano = " ".join(_palabras(gancho))
    for frase in cfg["_genericos"]:
        if " ".join(_palabras(frase)) in plano and _palabras(frase):
            raise Descartada(f"gancho genérico: «{frase}»")
    ignorar = cfg["_vacias"] | _nombres_a_ignorar(toma, ficha)
    contenido = [p for p in _palabras(gancho) if p not in ignorar]
    if len(contenido) < 2:
        raise Descartada(f"gancho sin contenido: quedan {len(contenido)} palabra(s) con contenido (mínimo 2)")
    ingles = [p for p in _palabras(gancho) if p in cfg["_ingles"]]
    if len(ingles) >= 2:
        raise Descartada(f"gancho en inglés: {', '.join(ingles)}")


def _preparar_gancho(bruto, toma: dict, ficha: dict, cfg: dict) -> str:
    g = _limpio(_sin_hashtags(_sin_marcas(bruto)))
    g = _oracion(g, cfg["_siglas"])
    if not g:
        raise Descartada("sin gancho")
    comprobar_gancho(g, toma, ficha, cfg)
    return g


# ---------------------------------------------------------------- crédito

def _login_twitch(valor) -> str:
    v = _sin_marcas(valor)
    v = re.sub(r"^(?:https?://)?(?:www\.)?(?:twitch\.tv/)?@?", "", v, flags=re.IGNORECASE)
    return v.strip("/ ")


def _creditos(toma: dict, ficha: dict) -> list[str]:
    """Una o varias referencias de crédito, ya escritas (sin el 🎥)."""
    es_t = ficha.get("tipo") == "T"
    crudos = [c for c in (toma.get("creadores") or []) if str(c or "").strip()]
    if not crudos:
        if es_t:
            crudos = [next((v for v in (ficha.get("credito"), ficha.get("creador"), ficha.get("twitch_login"),
                                        toma.get("creador")) if _login_twitch(v)), "")]
        else:
            crudos = [toma.get("creador") or ficha.get("creador")]
    if es_t:
        logins = [_login_twitch(c) for c in crudos]
        return [f"twitch.tv/{x}" for x in logins if x] if all(logins) else []
    out = [_sin_marcas(c) for c in crudos]
    return out if all(out) else []


# ---------------------------------------------------------------- numeración y título

def _numero(toma: dict) -> str:
    n = toma.get("numero", "")
    return f"{int(n):03d}" if str(n).isdigit() else str(n)


def _titulo_youtube(linea: str, maximo: int = 100) -> str:
    """Título de YouTube: sin emoji, sin hashtags, sin `<>` y de `maximo` UTF-16 o menos."""
    linea = _sin_hashtags(_sin_marcas(linea))
    if utf16(linea) <= maximo:
        return linea
    corte = linea[:maximo - 1].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return corte + "…"


# ---------------------------------------------------------------- armado

def _validas(etiquetas: list[str]) -> list[str]:
    """Quita lo que empieza con # pero no es un hashtag válido (espacios, signos); deja las @ y demás."""
    return [e for e in etiquetas if not e.startswith("#") or es_hashtag(e)]


def _sin_repetir(etiquetas: list[str], vistos: set[str]) -> list[str]:
    out = []
    for e in etiquetas:
        if e.lower() not in vistos:
            vistos.add(e.lower())
            out.append(e)
    return out


def _comprobar_final(texto: str, ficha: dict, plataforma: str, cfg: dict) -> int:
    lim = cfg["limites"][plataforma]
    if contar_emojis(texto) > 1:
        raise Descartada("el texto lleva más de un emoji")
    plano = _n(texto)
    for p in (ficha.get("reglas") or {}).get("palabras_evitar") or []:
        if _n(p) and _n(p) in plano:
            raise Descartada(f"el texto contiene la palabra a evitar «{p}»")
    n = largo(texto, plataforma, cfg)
    if n > lim["max_texto"]:
        raise Descartada(f"el texto mide {n} y {plataforma} permite {lim['max_texto']}")
    return n


def _resultado(texto, titulo, credito, etiquetas, plataforma, cfg, ficha) -> dict:
    n = _comprobar_final(texto, ficha, plataforma, cfg)
    return {"texto": texto, "titulo": titulo, "credito": credito, "etiquetas": etiquetas,
            "descripcion": texto, "hashtags": [e for e in etiquetas if es_hashtag(e)], "largo": n}


def _titulo_de(primera: str, plataforma: str, cfg: dict) -> str | None:
    if plataforma != "shorts":
        return None
    return _titulo_youtube(primera, int(cfg["limites"]["shorts"]["max_titulo"]))


def _campana(toma: dict, ficha: dict, plataforma: str, cfg: dict) -> dict:
    """Texto de una campaña con brief (SEGA FM27). Orden fijo:

        Toma nnn. <gancho en español>
        <blanco>
        @footballmanager            (etiquetas_obligatorias, tal cual)
        <etiqueta del creador>      (la de su red; una línea por creador, en el orden de la toma)
        #Publicidad                 (reglas.divulgacion: su propia línea y primer hashtag)
        #uno #dos #tres             (hasta 3 hashtags relacionados: los de la toma y luego reglas.hashtags)
    """
    reglas = ficha.get("reglas") or {}
    lim = cfg["limites"][plataforma]
    creadores = [c for c in (toma.get("creadores") or []) if str(c or "").strip()] or [toma.get("creador")]
    etiquetas_creador = []
    for c in creadores:
        nombre = _limpio(c)
        e = etiqueta_creador(ficha, nombre, plataforma)
        if not e:
            raise Descartada(f"sin etiqueta del creador {nombre or '(vacío)'} para {plataforma}: no se publica")
        etiquetas_creador.append(e)
    gancho = _preparar_gancho(toma.get("gancho"), toma, ficha, cfg)
    obligatorias = [str(e).strip() for e in reglas.get("etiquetas_obligatorias") or [] if str(e).strip()]
    divulgacion = str(reglas.get("divulgacion") or "").strip()
    if not es_hashtag(divulgacion):
        raise Descartada("la campaña exige una divulgación en hashtag (reglas.divulgacion)")
    vistos = {divulgacion.lower(), *(o.lower() for o in obligatorias)}
    crudos = [str(x).strip() for x in (toma.get("etiquetas") or []) + (reglas.get("hashtags") or [])]
    candidatos = _sin_repetir([e for e in crudos if es_hashtag(e)], vistos)
    tope = int(cfg.get("max_relacionados_campana", 3))
    if plataforma == "reels":  # en Instagram #Publicidad y las del brief cuentan dentro del tope de 5
        tope = min(tope, lim["max_hashtags"] - 1 - len(_hashtags(obligatorias)))
    extras = candidatos[:max(tope, 0)]
    if 1 + len(_hashtags(obligatorias)) + len(extras) < lim["min_hashtags"]:
        raise Descartada(f"{plataforma} necesita de {lim['min_hashtags']} a {lim['max_hashtags']} "
                         "etiquetas y no hay suficientes")
    primera = f"Toma {_numero(toma)}. {gancho}"
    cola = [*obligatorias, *etiquetas_creador, divulgacion] + ([" ".join(extras)] if extras else [])
    texto = primera + "\n\n" + "\n".join(cola)
    if "#" in texto.split("\n" + divulgacion, 1)[0]:
        raise Descartada("hay un # antes de la divulgación: #Publicidad debe ser el primer hashtag")
    credito = " · ".join(etiquetas_creador)
    return _resultado(texto, _titulo_de(primera, plataforma, cfg), credito,
                      [*obligatorias, divulgacion, *extras], plataforma, cfg, ficha)


def _construir(primera: str, valor: list[str], datos: list[str], credito: str, aviso: list[str],
               etiquetas: list[str]) -> str:
    bloques = [primera]
    if datos:
        bloques.append("\n".join("- " + d for d in datos))
    if valor:
        bloques.append(" ".join(valor))
    cola = [credito, *aviso] + ([" ".join(etiquetas)] if etiquetas else [])
    return "\n\n".join(bloques) + "\n\n" + "\n".join(cola)


def armar(toma: dict, ficha: dict, plataforma: str, cfg: dict | None = None) -> dict:
    """Texto de la publicación. Devuelve texto, titulo, credito, etiquetas, descripcion, hashtags y largo;
    lanza Descartada con el motivo si no cumple. No toca el estado (la repetición la mira `publicar`)."""
    cfg = cfg or cargar_config()
    if plataforma not in cfg["limites"]:
        raise Descartada(f"plataforma desconocida: {plataforma}")
    if es_de_campana(ficha):
        return _campana(toma, ficha, plataforma, cfg)
    reglas = ficha.get("reglas") or {}
    lim = cfg["limites"][plataforma]
    informativa = toma.get("pilar") == "informativa"
    if informativa:
        refs = [CREDITO_INFORMATIVA]
    else:
        refs = _creditos(toma, ficha)
    if not refs:
        raise Descartada("sin crédito: no se publica")
    credito = "🎥 " + " · ".join(refs)
    divulgacion = str(reglas.get("divulgacion") or "").strip()
    aviso = [] if divulgacion.lower() in ("", "ninguna") else [_sin_marcas(divulgacion)]
    # Etiquetas: las del brief van siempre (los hashtags de más sobre el tope de la red se quitan);
    # después las de la toma hasta llenar el tope. Un aviso que es un solo hashtag cuenta en el tope.
    tope = lim["max_hashtags"] - sum(1 for a in aviso if es_hashtag(a))
    obligatorias = _validas([str(e).strip() for e in reglas.get("etiquetas_obligatorias") or [] if str(e).strip()])
    vistos: set[str] = set()
    obligatorias = _sin_repetir(obligatorias, vistos)
    etiquetas, n_hash = [], 0
    for e in obligatorias:
        if e.startswith("#"):
            if n_hash >= tope:
                continue
            n_hash += 1
        etiquetas.append(e)
    for e in _sin_repetir(_validas([str(e).strip() for e in toma.get("etiquetas") or [] if str(e).strip()]), vistos):
        if n_hash < tope and e.startswith("#"):
            etiquetas.append(e)
            n_hash += 1
    if n_hash + sum(1 for a in aviso if es_hashtag(a)) < lim["min_hashtags"]:
        raise Descartada(f"{plataforma} necesita de {lim['min_hashtags']} a {lim['max_hashtags']} "
                         "etiquetas y no hay suficientes")
    valor, datos = [], []
    if informativa:
        titular = _preparar_gancho(toma.get("titular") or toma.get("gancho"), toma, ficha, cfg) \
            if (toma.get("titular") or toma.get("gancho")) else ""
        if not titular:
            raise Descartada("la informativa necesita titular")
        primera = f"GTA 6: {titular}"
        datos = [d for d in (_limpio(_sin_hashtags(str(d))) for d in toma.get("datos") or []) if d]
    else:
        gancho = _preparar_gancho(toma.get("gancho"), toma, ficha, cfg)
        primera = f"Toma {_numero(toma)}. {gancho}"
        if plataforma == "reels":
            valor = [v for v in (_limpio(_sin_hashtags(str(v))) for v in toma.get("valor") or []) if v][:2]
    # Si no cabe: primero se recorta el valor (Reels) o los datos (informativa), frase por frase.
    while True:
        texto = _construir(primera, valor, datos, credito, aviso, etiquetas)
        if largo(texto, plataforma, cfg) <= lim["max_texto"] or not (valor or datos):
            break
        (datos if datos else valor).pop()
    return _resultado(texto, _titulo_de(primera, plataforma, cfg), credito, etiquetas, plataforma, cfg, ficha)

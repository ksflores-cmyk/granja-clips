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

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕⬛⬜⌀-⏿\U0001F1E6-\U0001F1FF]")
_TAGS = re.compile("[\U000E0020-\U000E007F]")   # banderas como la de Escocia: 🏴 + caracteres de etiqueta
_RESTO_EMOJI = re.compile("[️‍⃣]")
_GTA = re.compile(r"(?<![#\w])GTA\s*(?:6|VI)\b|(?<![#\w])GTA6\b", re.IGNORECASE)
_HASHTAG_INCRUSTADO = re.compile(r"#(?=[^\W\d_])\S*")   # sólo los que empiezan con letra: «#1» se queda
_HASHTAG_VALIDO = re.compile(r"#\w+")
_PALABRA = re.compile(r"\w+")


def nfc(s) -> str:
    """Todas las entradas de texto en NFC (un `#Cancún` escrito con tilde suelta sigue siendo un hashtag)."""
    return unicodedata.normalize("NFC", str(s or ""))


def contar_emojis(texto: str) -> int:
    return len(_EMOJI.findall(_TAGS.sub("", texto)))


def utf16(s: str) -> int:
    """Largo como lo cuentan las redes: unidades UTF-16 (un emoji ocupa 2)."""
    return len(s.encode("utf-16-le")) // 2


def _sin_marcas(s) -> str:
    """Sin `<` ni `>`, sin emojis y con los espacios colapsados."""
    s = nfc(str(s or "")).replace("<", "").replace(">", "")
    s = _RESTO_EMOJI.sub("", _TAGS.sub("", _EMOJI.sub("", s)))
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

def _nombres_del_creador(toma: dict, ficha: dict) -> dict[str, str]:
    """{palabra normalizada: forma original} del creador de la toma (nombre, login o etiqueta)."""
    crudos = [toma.get("creador"), *(toma.get("creadores") or []), ficha.get("twitch_login")]
    out: dict[str, str] = {}
    for c in crudos:
        c = _sin_marcas(c)
        if not c:
            continue
        if "twitch.tv/" in c.lower():
            c = _login_twitch(c, estricto=False)
        for m in _PALABRA.finditer(c):
            out.setdefault(_n(m.group(0)), m.group(0))
    return out


def _oracion(g: str, siglas: dict, nombres: dict | None = None) -> str:
    """Un gancho que llega todo en mayúsculas pasa a tipo oración: mayúscula al empezar y tras `. ! ?`
    (también tras `¡` y `¿`), con las siglas y el nombre del creador como se escriben de verdad."""
    if not any(c.isalpha() for c in g) or g != g.upper():
        return g
    nombres = nombres or {}
    g = _PALABRA.sub(lambda m: siglas.get(_n(m.group(0))) or nombres.get(_n(m.group(0)))
                     or m.group(0).lower(), g)
    out, cap = [], True
    for c in g:
        if c.isalpha() and cap:
            c, cap = c.upper(), False
        elif c in ".!?":
            cap = True
        elif c.isalnum():
            cap = False
        out.append(c)
    return "".join(out)


def _nombres_a_ignorar(toma: dict, ficha: dict, plataforma: str | None = None) -> set[str]:
    """Palabras que no cuentan como contenido: el creador de la toma (nombre, login o etiqueta),
    `twitch_login`, el `credito` de una ficha T y el juego. NO el texto libre de `ficha.creador`."""
    palabras = set(_nombres_del_creador(toma, ficha))
    fuentes = [ficha.get("juego")]
    if ficha.get("tipo") == "T":
        fuentes.append(_login_twitch(ficha.get("credito"), estricto=False))
    if es_de_campana(ficha):
        for c in [toma.get("creador"), *(toma.get("creadores") or [])]:
            for red in ([plataforma] if plataforma else PLATAFORMAS):
                fuentes.append(etiqueta_creador(ficha, _limpio(c), red))
    for f in fuentes:
        palabras.update(_palabras(str(f or "")))
    return palabras


def _contiene(palabras: list[str], frase: list[str]) -> bool:
    """La frase (lista de palabras) aparece completa y seguida dentro de las palabras."""
    n = len(frase)
    return n > 0 and any(palabras[i:i + n] == frase for i in range(len(palabras) - n + 1))


def comprobar_gancho(gancho: str, toma: dict, ficha: dict, cfg: dict, plataforma: str | None = None) -> None:
    """Descarta (con motivo) un gancho genérico, sin contenido o con relleno en inglés.

    Las frases genéricas se buscan por palabras completas, sin acentos ni mayúsculas. Sin contenido:
    0 palabras con contenido, o 1 sola y menos de 3 palabras en total.
    """
    todas = _palabras(gancho)
    for frase in cfg["_genericos"]:
        if _contiene(todas, _palabras(frase)):
            raise Descartada(f"gancho genérico: «{frase}»")
    ignorar = cfg["_vacias"] | _nombres_a_ignorar(toma, ficha, plataforma)
    contenido = [p for p in todas if p not in ignorar]
    if len(contenido) == 0 or (len(contenido) == 1 and len(todas) < 3):
        raise Descartada(f"gancho sin contenido: {len(contenido)} palabra(s) con contenido de {len(todas)}")
    ingles = [p for p in todas if p in cfg["_ingles"]]
    if len(ingles) >= 2:
        raise Descartada(f"gancho en inglés: {', '.join(ingles)}")


def limpiar_gancho(bruto, toma: dict, ficha: dict, cfg: dict) -> str:
    """El gancho tal como sale en el texto (sin emojis, `<>` ni hashtags; «GTA 6»; tipo oración)."""
    g = _limpio(_sin_hashtags(_sin_marcas(bruto)))
    return _oracion(g, cfg["_siglas"], _nombres_del_creador(toma, ficha))


def _preparar_gancho(bruto, toma: dict, ficha: dict, cfg: dict, plataforma: str | None = None) -> str:
    g = limpiar_gancho(bruto, toma, ficha, cfg)
    if not g:
        raise Descartada("sin gancho")
    comprobar_gancho(g, toma, ficha, cfg, plataforma)
    return g


# ---------------------------------------------------------------- crédito

_LOGIN_VALIDO = re.compile(r"[A-Za-z0-9_]{1,25}")


def _login_twitch(valor, estricto: bool = True) -> str:
    """Login de Twitch de un texto como `https://www.twitch.tv/ibai/clip/x?a=1`. Vacío si no hay.
    Con `estricto`, lanza Descartada si no cumple `[A-Za-z0-9_]{1,25}`."""
    v = _sin_marcas(valor)
    v = re.sub(r"^(?:https?://)?(?:www\.)?(?:twitch\.tv/)?@?", "", v, flags=re.IGNORECASE)
    v = re.split(r"[?#]", v, maxsplit=1)[0].split("/")[0].strip()
    if estricto and v and not _LOGIN_VALIDO.fullmatch(v):
        raise Descartada(f"sin crédito: login de Twitch inválido «{v}» (de «{_sin_marcas(valor)}»)")
    return v


def _creditos(toma: dict, ficha: dict) -> list[str]:
    """Una o varias referencias de crédito, ya escritas (sin el 🎥)."""
    es_t = ficha.get("tipo") == "T"
    crudos = [c for c in (toma.get("creadores") or []) if str(c or "").strip()]
    if not crudos:
        if es_t:
            crudos = [next((v for v in (ficha.get("credito"), ficha.get("creador"), ficha.get("twitch_login"),
                                        toma.get("creador")) if _login_twitch(v, estricto=False)), "")]
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
    corte = ""
    for c in linea:
        if utf16(corte + c) > maximo - 1:
            break
        corte += c
    corte = corte.rsplit(" ", 1)[0].rstrip(" ,.;:") if " " in corte else corte
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


def _raiz_evitar(p: str) -> str:
    """Raíz de una palabra a evitar: sin la vocal final si tiene más de 4 letras (pirata → pirat)."""
    return p[:-1] if len(p) > 4 and p[-1] in "aeiou" else p


def _evita(palabras: list[str], frase: list[str]) -> bool:
    """Una palabra suelta del brief atrapa sus variantes por el inicio (leak → leaks, filtración →
    filtraciones, pirata → piratería, crack → crackeado); una frase de varias palabras va completa."""
    if len(frase) == 1:
        r = _raiz_evitar(frase[0])
        return any(w.startswith(r) for w in palabras)
    return _contiene(palabras, frase)


def _comprobar_final(texto: str, ficha: dict, plataforma: str, cfg: dict) -> int:
    lim = cfg["limites"][plataforma]
    if contar_emojis(texto) > 1:
        raise Descartada("el texto lleva más de un emoji")
    plano = _palabras(texto)
    for p in (ficha.get("reglas") or {}).get("palabras_evitar") or []:
        if _evita(plano, _palabras(p)):
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
        e = nfc(e) if e else e
        if not e:
            raise Descartada(f"sin etiqueta del creador {nombre or '(vacío)'} para {plataforma}: no se publica")
        etiquetas_creador.append(e)
    gancho = _preparar_gancho(toma.get("gancho"), toma, ficha, cfg, plataforma)
    obligatorias = [nfc(e).strip() for e in reglas.get("etiquetas_obligatorias") or [] if nfc(e).strip()]
    divulgacion = nfc(reglas.get("divulgacion")).strip()
    if not es_hashtag(divulgacion):
        raise Descartada("la campaña exige una divulgación en hashtag (reglas.divulgacion)")
    vistos = {divulgacion.lower(), *(o.lower() for o in obligatorias)}
    crudos = [nfc(x).strip() for x in (reglas.get("hashtags") or []) + (toma.get("etiquetas") or [])]   # primero las del brief
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
    if any("#" in linea for linea in cola[:cola.index(divulgacion)]):
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
    divulgacion = nfc(reglas.get("divulgacion")).strip()
    aviso = [] if divulgacion.lower() in ("", "ninguna") else [_sin_marcas(divulgacion)]
    # Etiquetas: las del brief van siempre (los hashtags de más sobre el tope de la red se quitan);
    # después las de la toma hasta llenar el tope. Un aviso que es un solo hashtag cuenta en el tope.
    tope = lim["max_hashtags"] - sum(1 for a in aviso if es_hashtag(a))
    obligatorias = _validas([nfc(e).strip() for e in reglas.get("etiquetas_obligatorias") or [] if nfc(e).strip()])
    vistos: set[str] = set()
    obligatorias = _sin_repetir(obligatorias, vistos)
    etiquetas, n_hash = [], 0
    for e in obligatorias:
        if e.startswith("#"):
            if n_hash >= tope:
                continue
            n_hash += 1
        etiquetas.append(e)
    for e in _sin_repetir(_validas([nfc(e).strip() for e in toma.get("etiquetas") or [] if nfc(e).strip()]), vistos):
        if n_hash < tope and e.startswith("#"):
            etiquetas.append(e)
            n_hash += 1
    if n_hash + sum(1 for a in aviso if es_hashtag(a)) < lim["min_hashtags"]:
        raise Descartada(f"{plataforma} necesita de {lim['min_hashtags']} a {lim['max_hashtags']} "
                         "etiquetas y no hay suficientes")
    valor, datos = [], []
    if informativa:
        titular = _preparar_gancho(toma.get("titular") or toma.get("gancho"), toma, ficha, cfg, plataforma) \
            if (toma.get("titular") or toma.get("gancho")) else ""
        if not titular:
            raise Descartada("la informativa necesita titular")
        primera = f"GTA 6: {titular}"
        datos = [d for d in (_limpio(_sin_hashtags(str(d))) for d in toma.get("datos") or []) if d]
    else:
        gancho = _preparar_gancho(toma.get("gancho"), toma, ficha, cfg, plataforma)
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

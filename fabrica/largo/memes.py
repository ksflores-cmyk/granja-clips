"""Memes de la semana: los sonidos populares de Myinstants (Guatemala, México, España, EE. UU.), filtrados por Gemini.

Kevin (27 nov 2026): «memes famosos pero tampoco que den cringe o sean viejos (…) memes actuales y acorde a la
tendencia». Cada semana el workflow lee las páginas «Populares en …», Gemini descarta los viejos, de boomer, vulgares
u ofensivos y clasifica los que quedan (risa, fail, sorpresa, victoria, incómodo, susto). Los MP3 se bajan en el runner
al armar el episodio y se borran al terminar: nunca entran al repo (es público) ni al almacén. En `estado/largos.json`
sólo queda qué meme sonó y su página.

Ojo (avisado a Kevin): la mayoría de los memes famosos tienen derechos; un reclamo de Content ID puede quedarse con lo
que gane el episodio. Por eso: uno por clip como mucho, máximo 2,5 s, y separados.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .. import plazo
from ..config import cargar_yaml

BASE = "https://www.myinstants.com"
CATEGORIAS = ("risa", "fail", "sorpresa", "victoria", "incomodo", "susto")
URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
UA = "Mozilla/5.0 (X11; Linux x86_64) GranjaClips/1.0"


@dataclass
class Meme:
    nombre: str
    mp3: str                      # URL absoluta del MP3
    pagina: str = ""              # URL de la página del sonido (para el registro)
    paises: list[str] = field(default_factory=list)
    puesto: int = 0               # mejor puesto en las páginas de populares
    categoria: str = ""
    ruta: str = ""                # archivo local normalizado (sólo en el runner)
    duracion: float = 0.0

    def registro(self) -> dict:
        return {"nombre": self.nombre, "pagina": self.pagina, "categoria": self.categoria}


def url_populares(pais: str) -> str:
    idioma = "en" if pais in ("us", "gb", "ca", "au") else "es"
    return f"{BASE}/{idioma}/index/{pais}/"


_BLOQUE = re.compile(r'class="instant"(.*?)(?=class="instant"|\Z)', re.S)
_MP3 = re.compile(r"play\('(/media/sounds/[^']+?\.mp3)'")
_ENLACE = re.compile(r'<a href="(/[a-z]{2}/instant/[^"]+)"[^>]*>(.*?)</a>', re.S)


def leer_populares(texto_html: str, pais: str) -> list[Meme]:
    """Los botones de una página «Populares en …», en orden. Sin MP3 o sin nombre, el botón se salta."""
    salida, vistos = [], set()
    for bloque in _BLOQUE.findall(texto_html):
        m, e = _MP3.search(bloque), _ENLACE.search(bloque)
        if not m or not e:
            continue
        nombre = " ".join(html.unescape(re.sub(r"<[^>]+>", "", e.group(2))).split())
        mp3 = BASE + m.group(1)
        if not nombre or mp3 in vistos:
            continue
        vistos.add(mp3)
        salida.append(Meme(nombre=nombre[:80], mp3=mp3, pagina=BASE + e.group(1), paises=[pais],
                           puesto=len(salida) + 1))
    return salida


_MP3_PAGINA = re.compile(r"""(?:https://www\.myinstants\.com)?(/media/sounds/[^"'\s<>]+?\.mp3)""")


def leer_enlaces(texto_html: str) -> list[tuple[str, str]]:
    """Respaldo si la página cambia y los botones ya no traen el MP3: (nombre, ruta de la página) de cada sonido."""
    salida, vistos = [], set()
    for ruta, nombre in _ENLACE.findall(texto_html):
        nombre = " ".join(html.unescape(re.sub(r"<[^>]+>", "", nombre)).split())
        if nombre and ruta not in vistos:
            vistos.add(ruta)
            salida.append((nombre[:80], ruta))
    return salida


def mp3_de_pagina(texto_html: str) -> str | None:
    """El MP3 de la página de un sonido (el botón «Descargar MP3»)."""
    m = _MP3_PAGINA.search(texto_html)
    return BASE + m.group(1) if m else None


def _bajar_texto(url: str, abrir=urllib.request.urlopen, tiempo: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es,en;q=0.8"})
    with abrir(req, timeout=tiempo) as r:
        return r.read(3_000_000).decode("utf-8", "replace")


def populares(paises: list[str], abrir=urllib.request.urlopen, salida=print, max_paginas: int = 30) -> list[Meme]:
    """Une los populares de varios países (el mismo MP3 suma países y se queda con su mejor puesto)."""
    por_mp3: dict[str, Meme] = {}
    for pais in paises:
        try:
            texto = _bajar_texto(url_populares(pais), abrir)
            lista = leer_populares(texto, pais)
            if not lista:                      # la página cambió: se va a la página de cada sonido
                for nombre, ruta in leer_enlaces(texto)[:max_paginas]:
                    try:
                        mp3 = mp3_de_pagina(_bajar_texto(BASE + ruta, abrir))
                    except Exception:  # noqa: BLE001
                        mp3 = None
                    if mp3:
                        lista.append(Meme(nombre=nombre, mp3=mp3, pagina=BASE + ruta, paises=[pais],
                                          puesto=len(lista) + 1))
        except Exception as e:  # noqa: BLE001 — un país caído no frena la semana
            salida(f"memes: no se pudo leer {pais} ({type(e).__name__})")
            continue
        salida(f"memes: {len(lista)} populares en {pais}")
        for m in lista:
            if m.mp3 in por_mp3:
                previo = por_mp3[m.mp3]
                previo.paises = sorted(set(previo.paises + m.paises))
                previo.puesto = min(previo.puesto, m.puesto)
            else:
                por_mp3[m.mp3] = m
    return sorted(por_mp3.values(), key=lambda m: (-len(m.paises), m.puesto))


INSTRUCCION = """Eres editor de un canal de YouTube en español de clips de streamers jugando GTA 6 (público de 16 a 30
años, Latinoamérica y España). Te paso los sonidos más populares de Myinstants esta semana. Elige los que sirven como
efecto en el remate de un clip y clasifícalos. Reglas:
- SÍ: memes actuales y de tendencia en TikTok/YouTube (2025-2026) que la comunidad gamer en español o en inglés reconoce.
- NO: memes viejos o «de tío» (los que se compartían en WhatsApp hace años), cringe, groserías fuertes, sexuales,
  insultos, temas sensibles, pedos, gritos largos, canciones completas o fragmentos largos de música.
- Máximo {maximo}. Prefiere variedad de categorías.
Categorías: risa, fail, sorpresa, victoria, incomodo, susto.
Responde sólo JSON: {{"elegidos": [{{"i": <número de la lista>, "categoria": "<categoría>"}}]}}
LISTA:
{lista}"""


def _modelos(raiz=None) -> list[str]:
    cfg = cargar_yaml("config/ojos.yml", raiz) or {}
    return [m for m in [cfg.get("modelo"), *(cfg.get("modelos_respaldo") or [])] if isinstance(m, str) and m]


def gemini_json(texto: str, clave: str, modelos: list[str], abrir=urllib.request.urlopen,
                salida=print) -> dict | None:
    """Pregunta de sólo texto a Gemini; el primer modelo que devuelva JSON válido."""
    cuerpo = {"contents": [{"parts": [{"text": texto}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    for modelo in modelos:
        req = urllib.request.Request(URL_GEMINI.format(modelo=modelo), data=json.dumps(cuerpo).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": clave})
        try:
            with abrir(req, timeout=90) as r:
                datos = json.loads(r.read().decode("utf-8"))
            return json.loads(datos["candidates"][0]["content"]["parts"][0]["text"])
        except Exception as e:  # noqa: BLE001 — se prueba el siguiente modelo
            salida(f"memes: {modelo} no respondió ({type(e).__name__})")
    return None


def filtrar(candidatos: list[Meme], clave: str, modelos: list[str], maximo: int, abrir=urllib.request.urlopen,
            salida=print) -> list[Meme]:
    """Los que Gemini deja, con su categoría. Sin Gemini, ninguno (mejor sin memes que con uno que dé cringe)."""
    if not candidatos or not clave:
        return []
    lista = "\n".join(f"{i}. {m.nombre} (popular en {', '.join(m.paises)})" for i, m in enumerate(candidatos))
    d = gemini_json(INSTRUCCION.format(maximo=maximo, lista=lista), clave, modelos, abrir, salida)
    salida_l, usados = [], set()
    for e in (d or {}).get("elegidos") or []:
        try:
            i, cat = int(e["i"]), str(e.get("categoria", "")).strip().lower()
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(candidatos) and i not in usados and cat in CATEGORIAS:
            usados.add(i)
            candidatos[i].categoria = cat
            salida_l.append(candidatos[i])
    return salida_l[:maximo]


def orden_normalizar(entrada: Path, destino: Path, dur_max: float, lufs: float) -> list[str]:
    """MP3 → WAV estéreo 48 kHz: sin silencio al inicio, recortado a `dur_max` con fundido corto y a `lufs`."""
    fundido = min(0.25, dur_max / 4)
    filtro = (f"silenceremove=start_periods=1:start_threshold=-45dB,atrim=0:{dur_max:g},asetpts=PTS-STARTPTS,"
              f"afade=t=out:st={max(0.0, dur_max - fundido):g}:d={fundido:g},"
              f"loudnorm=I={lufs:g}:TP=-2:LRA=11,aresample=48000")
    return ["ffmpeg", "-y", "-v", "error", "-i", str(entrada), "-af", filtro, "-ac", "2", "-c:a", "pcm_s16le",
            str(destino)]


def duracion_audio(ruta: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                   str(ruta)], text=True, timeout=30)
    return float(out.strip() or 0)


def bajar(memes: list[Meme], carpeta: Path, cfg: dict, abrir=urllib.request.urlopen, salida=print) -> list[Meme]:
    """Baja y normaliza cada MP3 (tope 1 MB). Los que fallan se saltan."""
    carpeta.mkdir(parents=True, exist_ok=True)
    listos = []
    for i, m in enumerate(memes):
        crudo, final = carpeta / f"m{i:02d}.mp3", carpeta / f"m{i:02d}.wav"
        try:
            req = urllib.request.Request(m.mp3, headers={"User-Agent": UA})
            with abrir(req, timeout=30) as r:
                datos = r.read(1_000_001)
            if len(datos) > 1_000_000 or len(datos) < 500:
                raise ValueError(f"pesa {len(datos)} bytes")
            crudo.write_bytes(datos)
            subprocess.run(orden_normalizar(crudo, final, float(cfg.get("dur_max_s", 2.5)),
                                            float(cfg.get("lufs", -18))),
                           check=True, capture_output=True, timeout=plazo.tiempo_para(60))
            m.ruta, m.duracion = str(final), duracion_audio(final)
            if m.duracion < 0.15:
                raise ValueError("quedó vacío tras quitar el silencio")
            listos.append(m)
        except Exception as e:  # noqa: BLE001
            salida(f"memes: {m.nombre!r} no se pudo usar ({type(e).__name__}: {e})")
        finally:
            crudo.unlink(missing_ok=True)
    return listos


INSTRUCCION_ASIGNAR = """Asigna a cada clip el efecto de sonido que mejor remata lo que pasa, o "ninguno" si ninguno
encaja (mejor nada que un meme forzado). No repitas un efecto si hay otros que encajan.
EFECTOS: {efectos}
CLIPS:
{clips}
Responde sólo JSON: {{"asignados": [{{"clip": <número>, "efecto": <número de efecto o null>}}]}}"""


def asignar(clips: list[dict], memes: list[Meme], clave: str, modelos: list[str], abrir=urllib.request.urlopen,
            salida=print) -> dict[int, Meme]:
    """{índice de clip: meme}. `clips`: [{texto}] (gancho, caption y frase). Sin Gemini, por rotación de categorías."""
    if not memes or not clips:
        return {}
    efectos = "; ".join(f"{i}: {m.nombre} ({m.categoria})" for i, m in enumerate(memes))
    lista = "\n".join(f"{i}. {c['texto']}" for i, c in enumerate(clips))
    d = gemini_json(INSTRUCCION_ASIGNAR.format(efectos=efectos, clips=lista), clave, modelos, abrir, salida) \
        if clave else None
    salida_d: dict[int, Meme] = {}
    if d:
        for e in d.get("asignados") or []:
            try:
                c, ef = int(e["clip"]), e.get("efecto")
            except (KeyError, TypeError, ValueError):
                continue
            if ef is None or not (0 <= c < len(clips)):
                continue
            try:
                ef = int(ef)
            except (TypeError, ValueError):
                continue
            if 0 <= ef < len(memes):
                salida_d[c] = memes[ef]
        return salida_d
    return {i: memes[i % len(memes)] for i in range(len(clips))}


def espaciar(asignados: dict[int, Meme], inicios: list[float], separacion: float) -> dict[int, Meme]:
    """Quita los memes que caerían a menos de `separacion` segundos del anterior (por el inicio de cada clip)."""
    salida_d, ultimo = {}, None
    for i in sorted(asignados):
        if ultimo is not None and inicios[i] - ultimo < separacion:
            continue
        salida_d[i] = asignados[i]
        ultimo = inicios[i]
    return salida_d


def orden_mezclar(segmento: Path, meme: Path, t: float, ganancia_db: float, destino: Path) -> list[str]:
    """El meme entra en `t` sobre el audio del segmento (sin tocar el video)."""
    ms = int(round(t * 1000))
    filtro = (f"[1:a]volume={ganancia_db:g}dB,adelay={ms}|{ms}[m];"
              f"[0:a][m]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[a]")
    return ["ffmpeg", "-y", "-v", "error", "-i", str(segmento), "-i", str(meme), "-filter_complex", filtro,
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(destino)]

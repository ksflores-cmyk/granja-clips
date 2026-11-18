"""Los ojos: Gemini mira un clip y devuelve un JSON que nuestro código valida.

API REST de Gemini con `urllib.request` (sin SDK). La clave sólo se lee de `GEMINI_API_KEY`: nunca se
imprime, se guarda ni entra en un mensaje de error. Sin clave, `mirar` devuelve `datos=None` con el
motivo "sin GEMINI_API_KEY" y el ciclo descarta el clip. La instrucción vive en `ojos_prompt.md`.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import cargar_yaml
from .editar import dialogo as mod_dialogo
from .editar.lienzo import PUNTUACION, partir
from .editar.marca import Marca

URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
RUTA_PROMPT = Path(__file__).resolve().parent / "ojos_prompt.md"
ESPERA_REINTENTO_S = 8.0
TIEMPO_ESPERA_S = 180
TOPE_PETICION_BYTES = 20 * 1024 * 1024   # límite de la API para una petición en línea
RECORTE_MIN_S, RECORTE_MAX_S = 7.0, 30.0
TOL_S = 0.05
CAMPOS_BOOL = ("apto", "spoiler", "desnudez", "cinematica", "juego_correcto")
CAMPOS_TEXTO = ("idioma_original", "gancho", "palabra_lapiz", "escena", "acotacion")


class ErrorOjos(RuntimeError):
    pass


@dataclass
class Mirada:
    """Resultado de pedirle a Gemini. `datos` es None si no hubo respuesta utilizable."""
    datos: dict | None
    modelo: str | None = None          # el que respondió
    motivo: str | None = None          # por qué no hay datos
    intentos: list[str] = field(default_factory=list)   # un renglón por intento (sin claves)


def esquema_respuesta() -> dict:
    """`responseSchema` de Gemini (subconjunto de OpenAPI) con los campos que pide la especificación."""
    texto, booleano, numero = {"type": "STRING"}, {"type": "BOOLEAN"}, {"type": "NUMBER"}
    campos = {
        "apto": booleano,
        "motivos": {"type": "ARRAY", "items": texto},
        "spoiler": booleano, "desnudez": booleano, "cinematica": booleano, "juego_correcto": booleano,
        "idioma_original": texto,
        "lineas": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"t0": numero, "t1": numero, "hablante": texto, "original": texto, "es": texto},
            "required": ["t0", "t1", "hablante", "original", "es"]}},
        "gancho": texto, "palabra_lapiz": texto, "escena": texto, "acotacion": texto,
        "recorte": {"type": "OBJECT", "properties": {"inicio": numero, "fin": numero},
                    "required": ["inicio", "fin"]},
    }
    return {"type": "OBJECT", "properties": campos, "required": list(campos)}


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")


def instruccion(marca: Marca, temporada: dict, duracion: float | None, juego: str | None = None,
                nota: str | None = None) -> str:
    """Arma la instrucción desde `ojos_prompt.md` con la duración, el juego y las listas de marca y temporada."""
    texto = RUTA_PROMPT.read_text(encoding="utf-8")
    cambios = {
        "{{DURACION}}": f"{duracion:.1f}" if duracion else "desconocida",
        "{{JUEGO}}": str(juego or temporada.get("nombre", "")),
        "{{NOTA_ESPECIAL}}": nota or "",
        "{{GANCHOS_PROHIBIDOS}}": ", ".join(marca.ganchos_prohibidos()),
        "{{PALABRAS_EVITAR}}": ", ".join(str(p) for p in temporada.get("palabras_evitar", [])),
    }
    for k, v in cambios.items():
        texto = texto.replace(k, v)
    return texto


def cuerpo_peticion(instr: str, video: bytes, temperatura: float) -> bytes:
    cuerpo = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video).decode("ascii")}},
            {"text": instr}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": temperatura,
                             "responseSchema": esquema_respuesta()},
    }
    return json.dumps(cuerpo).encode("utf-8")


def transporte_urllib(url: str, encabezados: dict, cuerpo: bytes) -> tuple[int, str]:
    """Transporte real: POST. Devuelve (estado_http, texto). Nunca deja la clave en un error."""
    req = urllib.request.Request(url, data=cuerpo, headers=encabezados, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIEMPO_ESPERA_S) as r:  # noqa: S310 (URL fija https)
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErrorOjos(f"fallo de conexión con Gemini: {type(e).__name__}") from None


def _texto_de_respuesta(texto_http: str) -> str:
    """Extrae el texto del primer candidato. ErrorOjos con el motivo si no hay."""
    try:
        d = json.loads(texto_http)
    except json.JSONDecodeError:
        raise ErrorOjos("la respuesta de Gemini no es JSON") from None
    cands = d.get("candidates") if isinstance(d, dict) else None
    if not cands:
        bloqueo = (d.get("promptFeedback") or {}).get("blockReason") if isinstance(d, dict) else None
        raise ErrorOjos("Gemini no devolvió candidatos" + (f" (bloqueo: {bloqueo})" if bloqueo else ""))
    partes = ((cands[0].get("content") or {}).get("parts")) or []
    texto = "".join(p.get("text", "") for p in partes if isinstance(p, dict))
    if not texto.strip():
        raise ErrorOjos(f"Gemini devolvió una respuesta vacía (finishReason {cands[0].get('finishReason')})")
    return texto


def mirar(ruta_video: Path, marca: Marca, temporada: dict, *, raiz=None, duracion: float | None = None,
          transporte=None, espera=time.sleep, entorno=None, config: dict | None = None,
          juego: str | None = None, nota: str | None = None) -> Mirada:
    """Le pide a Gemini el análisis del clip. Reintenta una vez ante 429 o 5xx; ante 404 prueba el modelo siguiente."""
    env = os.environ if entorno is None else entorno
    clave = env.get("GEMINI_API_KEY", "")
    if not clave:
        return Mirada(None, motivo="sin GEMINI_API_KEY")
    cfg = config if config is not None else cargar_yaml("config/ojos.yml", raiz)
    ruta_video = Path(ruta_video)
    tam = ruta_video.stat().st_size
    tope = float(cfg["max_mb_en_linea"]) * 1024 * 1024
    if tam > tope:
        return Mirada(None, motivo=f"el video pesa {tam / 1048576:.1f} MB y el máximo en línea es "
                                   f"{cfg['max_mb_en_linea']} MB")
    cuerpo = cuerpo_peticion(instruccion(marca, temporada, duracion, juego, nota), ruta_video.read_bytes(),
                             float(cfg["temperatura"]))
    if len(cuerpo) > TOPE_PETICION_BYTES:     # el base64 infla un tercio: se mide lo que de verdad se envía
        return Mirada(None, motivo=f"la petición serializada pesa {len(cuerpo) / 1048576:.1f} MB y el tope es "
                                   f"{TOPE_PETICION_BYTES // 1048576} MB")
    transporte = transporte or transporte_urllib
    encabezados = {"Content-Type": "application/json", "x-goog-api-key": clave}
    mirada = Mirada(None)
    for modelo in [cfg["modelo"], *cfg.get("modelos_respaldo", [])]:
        url = URL_BASE.format(modelo=modelo)
        estado, texto = None, ""
        for intento in (1, 2):
            try:
                estado, texto = transporte(url, encabezados, cuerpo)
            except ErrorOjos as e:
                mirada.motivo = str(e)
                mirada.intentos.append(f"{modelo}: {e}")
                return mirada
            if estado == 429 or 500 <= estado < 600:
                mirada.intentos.append(f"{modelo}: HTTP {estado} (intento {intento})")
                if intento == 1:
                    espera(ESPERA_REINTENTO_S)
                    continue
            break
        if estado == 404:
            mirada.intentos.append(f"{modelo}: HTTP 404, no existe; se prueba el siguiente")
            continue
        if estado != 200:
            mirada.motivo = f"Gemini respondió HTTP {estado} con {modelo}"
            return mirada
        mirada.modelo = modelo
        mirada.intentos.append(f"{modelo}: HTTP 200")
        try:
            crudo = _texto_de_respuesta(texto)
        except ErrorOjos as e:
            mirada.motivo = str(e)
            return mirada
        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError:
            mirada.motivo = "la respuesta del modelo no es JSON válido"
            return mirada
        if not isinstance(datos, dict):
            mirada.motivo = "la respuesta del modelo no es un objeto JSON"
            return mirada
        mirada.datos = datos
        return mirada
    mirada.motivo = "ningún modelo respondió (todos dieron 404)"
    return mirada


# ------------------------------------------------------------------ validación (código nuestro, no del modelo)

def _numero(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _contiene(texto: str, palabra: str) -> bool:
    """La palabra empieza en frontera de palabra (así 'final' también atrapa 'finalmente'). Sin acentos."""
    p = _sin_acentos(palabra).strip()
    return bool(p) and re.search(r"(?<!\w)" + re.escape(p), _sin_acentos(texto)) is not None


def _cabe_en_lineas(marca: Marca, frase: str) -> bool:
    try:
        return len(mod_dialogo.lineas_frase(marca, frase)) <= marca.max_lineas_frase
    except ValueError:
        return False


def dividir_frase(marca: Marca, frase: str) -> list[str] | None:
    """La frase entera si cabe en 2 líneas; si no, dos mitades por oraciones que quepan; si no, None."""
    frase = " ".join(str(frase).split())
    if _cabe_en_lineas(marca, frase):
        return [frase]
    oraciones = re.split(r"(?<=[.!?…])\s+", frase)
    mejor = None
    for k in range(1, len(oraciones)):
        izq, der = " ".join(oraciones[:k]), " ".join(oraciones[k:])
        if _cabe_en_lineas(marca, izq) and _cabe_en_lineas(marca, der):
            dif = abs(len(izq) - len(der))
            if mejor is None or dif < mejor[0]:
                mejor = (dif, [izq, der])
    return mejor[1] if mejor else None


def validar_respuesta(datos, marca: Marca, temporada: dict, *, duracion: float | None = None,
                      juego: str | None = None) -> tuple[bool, list[str]]:
    """(ok, motivos). Tipos y campos; veto del modelo; recorte; líneas; gancho; palabras a evitar; cabida."""
    if not isinstance(datos, dict):
        return False, ["la respuesta no es un objeto JSON"]
    m: list[str] = []
    for c in CAMPOS_BOOL:
        if not isinstance(datos.get(c), bool):
            m.append(f"campo {c}: falta o no es booleano")
    for c in CAMPOS_TEXTO:
        if not isinstance(datos.get(c), str):
            m.append(f"campo {c}: falta o no es texto")
    if not (isinstance(datos.get("motivos"), list) and all(isinstance(x, str) for x in datos["motivos"])):
        m.append("campo motivos: falta o no es una lista de texto")
    rec = datos.get("recorte")
    if not (isinstance(rec, dict) and _numero(rec.get("inicio")) and _numero(rec.get("fin"))):
        m.append("campo recorte: falta o no trae inicio y fin numéricos")
        rec = None
    lineas = datos.get("lineas")
    if not isinstance(lineas, list):
        m.append("campo lineas: falta o no es una lista")
        lineas = None
    else:
        for i, ln in enumerate(lineas, 1):
            if not (isinstance(ln, dict) and _numero(ln.get("t0")) and _numero(ln.get("t1"))
                    and all(isinstance(ln.get(k), str) for k in ("hablante", "original", "es"))):
                m.append(f"línea {i}: faltan campos o tienen un tipo erróneo")
                lineas = None
                break
    if m:                         # sin estructura no se sigue
        return False, m

    # veto del modelo
    if datos["apto"] is False:
        m.append("Gemini marcó apto: false" + (": " + "; ".join(datos["motivos"]) if datos["motivos"] else ""))
    if datos["spoiler"]:
        m.append("spoiler")
    if datos["desnudez"]:
        m.append("desnudez")
    if datos["cinematica"]:
        m.append("cinemática aislada")
    if not datos["juego_correcto"]:
        m.append(f"no es el juego esperado ({juego or temporada.get('nombre', 'el de la temporada')})")

    # recorte
    ini, fin = float(rec["inicio"]), float(rec["fin"])
    if ini < 0 or (duracion is not None and fin > duracion + TOL_S):
        m.append(f"recorte {ini:g}-{fin:g} s fuera de la duración del archivo"
                 + (f" ({duracion:.1f} s)" if duracion is not None else ""))
    largo = fin - ini
    if not RECORTE_MIN_S - TOL_S <= largo <= RECORTE_MAX_S + TOL_S:
        m.append(f"recorte de {largo:.1f} s: debe durar de {RECORTE_MIN_S:g} a {RECORTE_MAX_S:g} s")

    # líneas
    previo = None
    for i, ln in enumerate(sorted(lineas, key=lambda x: x["t0"]), 1):
        t0, t1 = float(ln["t0"]), float(ln["t1"])
        if not t0 < t1:
            m.append(f"línea {i}: t0 {t0:g} no es menor que t1 {t1:g}")
        if t0 < ini - TOL_S or t1 > fin + TOL_S:
            m.append(f"línea {i}: {t0:g}-{t1:g} s queda fuera del recorte {ini:g}-{fin:g} s")
        if previo is not None and t0 < previo - TOL_S:
            m.append(f"línea {i}: se solapa con la anterior")
        previo = t1
        if not ln["es"].strip():
            m.append(f"línea {i}: sin traducción")
            continue
        if not ln["hablante"].strip():
            m.append(f"línea {i}: sin hablante")
        elif marca.fuente("regular", marca.m("tam_nombre")).getlength(ln["hablante"].upper()) \
                > marca.columna[1] - marca.columna[0]:
            m.append(f"línea {i}: el nombre del hablante no cabe en la columna")
        if dividir_frase(marca, ln["es"]) is None:
            m.append(f"línea {i}: la frase no cabe en {marca.max_lineas_frase} líneas ni partida por oraciones: "
                     f"{ln['es'][:60]!r}")

    # gancho
    gancho = datos["gancho"].strip()
    palabras = gancho.split()
    if not palabras:
        m.append("gancho vacío")
    elif len(palabras) > marca.max_palabras_gancho:
        m.append(f"gancho de {len(palabras)} palabras (máximo {marca.max_palabras_gancho}): {gancho!r}")
    else:
        try:
            if len(partir(gancho.upper(), marca.fuente("bold", marca.m("tam_gancho")),
                          marca.ancho - 2 * marca.m("margen"))) > 2:
                m.append(f"el gancho no cabe en 2 líneas: {gancho!r}")
        except ValueError as e:
            m.append(f"el gancho no cabe: {e}")
    for g in marca.ganchos_prohibidos():
        if _sin_acentos(g) in _sin_acentos(gancho):
            m.append(f"gancho genérico prohibido: {g}")
    lapiz = datos["palabra_lapiz"].strip(PUNTUACION + " ").upper()
    if not lapiz or lapiz not in [p.strip(PUNTUACION).upper() for p in palabras]:
        m.append(f"palabra_lapiz {datos['palabra_lapiz']!r} no está en el gancho")

    # escena y acotación
    escena = datos["escena"].strip()
    if not re.match(r"^(INT|EXT)\.\s+\S", escena):
        m.append(f"escena {escena!r}: debe empezar con INT. o EXT.")
    elif marca.fuente("regular", marca.m("tam_escena")).getlength(escena.upper()) > marca.ancho - 2 * marca.m("margen"):
        m.append("la escena no cabe en una línea")
    acot = datos["acotacion"].strip()
    if acot:
        try:
            mod_dialogo.validar_bloque(marca, None, None, acot)
        except ValueError as e:
            m.append(f"acotación: {e}")

    # palabras a evitar (spoilers)
    for p in temporada.get("palabras_evitar", []):
        for donde, texto in [("gancho", gancho), ("escena", escena), ("acotación", acot)] + \
                [(f"línea {i}", ln["es"]) for i, ln in enumerate(lineas, 1)]:
            if _contiene(texto, str(p)):
                m.append(f"palabra a evitar {p!r} en {donde}")
    return not m, m


def partir_lineas(datos: dict, marca: Marca) -> list[dict]:
    """Líneas listas para componer (t0, t1, hablante, original, es), con las frases largas partidas en dos tiempos.

    Supone que `validar_respuesta` ya dio ok. La división de tiempo es proporcional al texto de cada mitad.
    """
    salida: list[dict] = []
    for ln in sorted(datos["lineas"], key=lambda x: x["t0"]):
        trozos = dividir_frase(marca, ln["es"])
        if trozos is None:
            raise ValueError(f"la frase no cabe: {ln['es']!r}")
        if len(trozos) == 1:
            salida.append({**ln, "es": trozos[0]})
            continue
        t0, t1 = float(ln["t0"]), float(ln["t1"])
        medio = t0 + (t1 - t0) * len(trozos[0]) / (len(trozos[0]) + len(trozos[1]))
        salida.append({**ln, "t0": t0, "t1": medio, "es": trozos[0]})
        salida.append({**ln, "t0": medio, "t1": t1, "es": trozos[1]})
    return salida

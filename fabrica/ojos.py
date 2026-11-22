"""Los ojos: Gemini mira un clip y devuelve un JSON que nuestro código valida.

API REST de Gemini con `urllib.request` (sin SDK). La clave sólo se lee de `GEMINI_API_KEY`: nunca se
imprime, se guarda ni entra en un mensaje de error. Sin clave, `mirar` devuelve `datos=None` con el
motivo "sin GEMINI_API_KEY" y el ciclo descarta el clip. La instrucción vive en `ojos_prompt.md`.
"""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import cupos as mod_cupos
from . import plazo
from .config import cargar_yaml
from .editar import dialogo as mod_dialogo
from .editar import historia as mod_historia
from .editar.lienzo import PUNTUACION, partir
from .editar.marca import Marca

URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
URL_LISTA = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
TIEMPO_LISTA_S = 15                      # ListModels: una consulta corta, nunca bloquea
MAX_PAGINAS_LISTA = 10                   # ListModels: tope de páginas
MOTIVO_SIN_CLAVE = "sin GEMINI_API_KEY"
RESPALDOS_POR_FALLA = 1                  # por defecto, si la config no dice `respaldos_por_falla`
RUTA_PROMPT = Path(__file__).resolve().parent / "ojos_prompt.md"
ESPERA_REINTENTO_S = 8.0
TIEMPO_ESPERA_S = 120                    # por petición
TOPE_OJOS_S = 420                        # para todo `mirar` (intentos y esperas)
MINIMO_INTENTO_S = 15                    # con menos que esto no se envía otra petición
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
    pasajero: bool = False             # falló Gemini (429, 5xx, red), no el clip: la fuente no se gasta
    del_material: bool = False         # el fallo es de ESTE video (400, sin candidatos, muy pesado): la fuente se gasta
    clave_mala: bool = False           # 401/403/400 de clave: error de configuración, no hay corte sin IA ni se gasta la fuente

    @property
    def sin_gemini(self) -> bool:
        """Gemini no estuvo disponible (caída, sin tiempo, sin clave, todos los modelos 404): no hubo veredicto.
        Es lo único que habilita el corte sin IA; si Gemini respondió y vetó, o el fallo es del video, no."""
        return self.datos is None and not self.del_material


def _error_de_clave(texto) -> bool:
    """Un 400 de Gemini cuyo `error.status` o `details[].reason` habla de clave o permiso (no del video)."""
    try:
        err = (json.loads(texto) or {}).get("error") or {}
    except (ValueError, TypeError, AttributeError):
        return False
    if not isinstance(err, dict):
        return False
    marcas = [str(err.get("status") or "")]
    for d in err.get("details") or []:
        if isinstance(d, dict):
            marcas.append(str(d.get("reason") or ""))
    junto = " ".join(marcas).upper()
    return any(m in junto for m in ("API_KEY", "PERMISSION_DENIED", "FAILED_PRECONDITION", "UNAUTHENTICATED"))


def esquema_respuesta(historia: bool = False) -> dict:
    """`responseSchema` de Gemini (subconjunto de OpenAPI) con los campos que pide la especificación.

    `historia` añade el plan por momentos (#20); sin él el esquema es el de siempre, campo por campo."""
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
    if historia:        # opcional (no va en `required`): el plan de edición por momentos, sin texto propio (#20)
        return {"type": "OBJECT", "properties": {**campos, "historia": mod_historia.esquema_gemini()},
                "required": list(campos)}
    return {"type": "OBJECT", "properties": campos, "required": list(campos)}


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")


def juego_de(ficha: dict | None, temporada: dict) -> str:
    """El juego que se espera ver: el de la ficha si lo declara; si no, el de la temporada."""
    j = (ficha or {}).get("juego")
    return str(j).strip() if isinstance(j, str) and j.strip() else str(temporada.get("nombre", ""))


def temporada_de(ficha: dict | None, temporada: dict) -> dict:
    """La temporada que vale para esta ficha. Si la ficha declara su propio juego, los spoilers de la temporada
    (GTA 6) no le aplican: usa `reglas.palabras_evitar` de la ficha (vacío si no hay)."""
    if not (isinstance((ficha or {}).get("juego"), str) and ficha["juego"].strip()):
        return temporada
    return {**temporada, "nombre": ficha["juego"].strip(),
            "palabras_evitar": list(((ficha.get("reglas") or {}).get("palabras_evitar")) or [])}


def rango_recorte(ficha: dict | None) -> tuple[float, float]:
    """(mínimo, máximo) del recorte en segundos: `reglas.duracion_s` de la ficha dentro del formato (7 a 30 s)."""
    d = ((ficha or {}).get("reglas") or {}).get("duracion_s")
    if isinstance(d, (list, tuple)) and len(d) == 2 and all(isinstance(x, (int, float)) for x in d):
        return max(RECORTE_MIN_S, float(d[0])), min(RECORTE_MAX_S, float(d[1]))
    return RECORTE_MIN_S, RECORTE_MAX_S


def nota_de_ficha(ficha: dict | None) -> str:
    """Instrucciones extra que salen de las reglas de la campaña (sin música, sin datos inventados)."""
    r = (ficha or {}).get("reglas") or {}
    if not r.get("sin_musica"):
        return ""
    return ("Campaña con brief: el audio es sólo el del video; no añadas música, voz ni narración. No inventes "
            "datos (marcadores, fichajes, cifras) que no se vean u oigan. Si el video es un tráiler o una "
            "presentación, no lo llames gameplay. Ganchos sin exageraciones ni odio. El texto va en español.")


def instruccion(marca: Marca, temporada: dict, duracion: float | None, juego: str | None = None,
                nota: str | None = None, recorte: tuple[float, float] | None = None,
                historia: dict | None = None, gancho_inicio_s: float | None = None) -> str:
    """Arma la instrucción desde `ojos_prompt.md` con la duración, el juego y las listas de marca y temporada.

    `historia` ({cfg, sfx: [ids], prohibe_sfx}) activa la sección «9. historia»; sin él, el prompt sale sin ella.
    `gancho_inicio_s` (prueba de retención, brazo corta) pide que el momento fuerte caiga en el primer segundo del recorte."""
    texto = RUTA_PROMPT.read_text(encoding="utf-8")
    if historia:
        sec = mod_historia.instruccion(historia.get("cfg"), historia.get("sfx"), historia.get("prohibe_sfx"))
        texto = mod_historia.RE_SECCION.sub(lambda _m: sec, texto)
    else:
        texto = mod_historia.quitar_seccion(texto)
    lo, hi = recorte or (RECORTE_MIN_S, RECORTE_MAX_S)
    cambios = {
        "{{RECORTE_MIN}}": f"{lo:g}", "{{RECORTE_MAX}}": f"{hi:g}",
        "{{DURACION}}": f"{duracion:.1f}" if duracion else "desconocida",
        "{{JUEGO}}": str(juego or temporada.get("nombre", "")),
        "{{NOTA_ESPECIAL}}": nota or "",
        "{{GANCHO_INICIO}}": (f" El recorte debe empezar como mucho {gancho_inicio_s:g} s antes del momento más fuerte: "
                              "el gancho visual o sonoro ocurre en el primer segundo, sin preámbulo ni aire muerto."
                              if gancho_inicio_s else ""),
        "{{GANCHOS_PROHIBIDOS}}": ", ".join(marca.ganchos_prohibidos()),
        "{{PALABRAS_EVITAR}}": ", ".join(str(p) for p in temporada.get("palabras_evitar", [])),
    }
    for k, v in cambios.items():
        texto = texto.replace(k, v)
    return texto


def cuerpo_peticion(instr: str, video: bytes, temperatura: float, historia: bool = False) -> bytes:
    cuerpo = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video).decode("ascii")}},
            {"text": instr}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": temperatura,
                             "responseSchema": esquema_respuesta(historia)},
    }
    return json.dumps(cuerpo).encode("utf-8")


def transporte_urllib(url: str, encabezados: dict, cuerpo: bytes, tiempo: float | None = None) -> tuple[int, str]:
    """Transporte real: POST. Devuelve (estado_http, texto). Nunca deja la clave en un error."""
    req = urllib.request.Request(url, data=cuerpo, headers=encabezados, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=tiempo or TIEMPO_ESPERA_S) as r:  # noqa: S310 (URL fija https)
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


def transporte_get_urllib(url: str, encabezados: dict, tiempo: float | None = None) -> tuple[int, str]:
    """Transporte real de ListModels: GET. Devuelve (estado_http, texto). Nunca deja la clave en un error."""
    req = urllib.request.Request(url, headers=encabezados, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=tiempo or TIEMPO_LISTA_S) as r:  # noqa: S310 (URL fija https)
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErrorOjos(f"fallo de conexión con ListModels: {type(e).__name__}") from None


def modelos_disponibles(clave: str, transporte_get=None, tiempo: float = TIEMPO_LISTA_S) -> set[str]:
    """Nombres (sin el prefijo `models/`) de los modelos que soportan `generateContent`, según ListModels.

    Sigue `nextPageToken`. `transporte_get(url, encabezados, tiempo)` es inyectable. ErrorOjos si la consulta falla
    (red, HTTP distinto de 200, JSON raro); quien llama decide qué hacer (`mirar` nunca se bloquea por esto)."""
    get = transporte_get or transporte_get_urllib
    encabezados = {"x-goog-api-key": clave}
    nombres: set[str] = set()
    pagina, vistas = None, set()
    for _ in range(MAX_PAGINAS_LISTA):
        url = URL_LISTA + (f"&pageToken={urllib.parse.quote(pagina)}" if pagina else "")
        estado, texto = get(url, encabezados, tiempo)
        if estado != 200:
            raise ErrorOjos(f"ListModels respondió HTTP {estado}")
        try:
            d = json.loads(texto)
        except json.JSONDecodeError:
            raise ErrorOjos("ListModels no devolvió JSON") from None
        if not isinstance(d, dict) or not isinstance(d.get("models", []), list):
            raise ErrorOjos("ListModels devolvió un JSON inesperado")
        for m in d.get("models", []):
            if not isinstance(m, dict) or not isinstance(m.get("name"), str):
                continue
            metodos = m.get("supportedGenerationMethods")
            if isinstance(metodos, list) and "generateContent" in metodos:
                nombres.add(m["name"].removeprefix("models/"))
        pagina = d.get("nextPageToken")
        if not pagina:
            return nombres
        if pagina in vistas:
            raise ErrorOjos("ListModels repitió la página siguiente")
        vistas.add(pagina)
    raise ErrorOjos(f"ListModels pasó de {MAX_PAGINAS_LISTA} páginas")


_CACHE_MODELOS: dict[str, set[str] | ErrorOjos] = {}


def vaciar_cache_modelos() -> None:
    _CACHE_MODELOS.clear()


def modelos_disponibles_en_cache(clave: str, transporte_get=None, tiempo: float = TIEMPO_LISTA_S) -> set[str]:
    """Como `modelos_disponibles`, pero una sola consulta por proceso y clave (el ciclo mira varias tomas).
    También se guarda el fallo (no se repite una consulta que ya falló); la clave se guarda hasheada."""
    k = hashlib.sha256(clave.encode("utf-8")).hexdigest()
    if k not in _CACHE_MODELOS:
        try:
            _CACHE_MODELOS[k] = modelos_disponibles(clave, transporte_get, tiempo)
        except ErrorOjos as e:
            _CACHE_MODELOS[k] = e
    if isinstance(_CACHE_MODELOS[k], ErrorOjos):
        raise ErrorOjos(str(_CACHE_MODELOS[k]))
    return _CACHE_MODELOS[k]


def _acepta_tiempo(transporte) -> bool:
    """Los transportes inyectados antiguos reciben 3 argumentos; uno con `tiempo` (o 4 parámetros) recibe además el tope."""
    try:
        ps = inspect.signature(transporte).parameters
    except (TypeError, ValueError):
        return False
    return "tiempo" in ps or sum(1 for p in ps.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)) >= 4


def _numero_cfg(cfg: dict, clave: str, por_defecto: float, entero: bool = False) -> float:
    v = cfg.get(clave, por_defecto)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return por_defecto
    return int(v) if entero else float(v)


def _modelos_a_probar(cfg: dict, clave: str, mirada: Mirada, transporte, transporte_lista,
                      tiempo: float = TIEMPO_LISTA_S) -> list[str]:
    """La lista de la config, sin los modelos que ListModels no muestra. Nunca bloquea: si la consulta falla o
    el filtro la vacía, se usa la lista completa."""
    todos = [cfg["modelo"], *cfg.get("modelos_respaldo", [])]
    if not cfg.get("listar_modelos") or (transporte is not None and transporte_lista is None):
        return todos
    try:
        disponibles = modelos_disponibles_en_cache(clave, transporte_lista, tiempo)
    except ErrorOjos as e:
        mirada.intentos.append(f"ListModels: {e}; se usa la lista completa")
        return todos
    buenos = [m for m in todos if m in disponibles]
    if not buenos:
        mirada.intentos.append("ListModels: ningún modelo de la lista aparece; se usa la lista completa")
        return todos
    for m in todos:
        if m not in disponibles:
            mirada.intentos.append(f"{m}: no aparece en ListModels, se salta")
    return buenos


def mirar(ruta_video: Path, marca: Marca, temporada: dict, *, raiz=None, duracion: float | None = None,
          transporte=None, espera=time.sleep, entorno=None, config: dict | None = None,
          juego: str | None = None, nota: str | None = None,
          recorte: tuple[float, float] | None = None, transporte_lista=None,
          registrar_cupos: bool = False, historia: dict | None = None,
          gancho_inicio_s: float | None = None) -> Mirada:
    """Le pide a Gemini el análisis del clip. Reintenta una vez ante 429 o 5xx; ante 404 prueba el modelo siguiente.
    `historia` ({cfg, sfx, prohibe_sfx}) pide además el plan por momentos en la MISMA llamada (no gasta otra petición).

    Antes consulta ListModels (si `listar_modelos`) y salta los modelos que no existen. Con un `transporte`
    inyectado sólo se consulta si también se da `transporte_lista`."""
    env = os.environ if entorno is None else entorno
    clave = env.get("GEMINI_API_KEY", "")
    if not clave:
        return Mirada(None, motivo=MOTIVO_SIN_CLAVE)
    cfg = config if config is not None else cargar_yaml("config/ojos.yml", raiz)
    ruta_video = Path(ruta_video)
    tam = ruta_video.stat().st_size
    tope = float(cfg["max_mb_en_linea"]) * 1024 * 1024
    if tam > tope:
        return Mirada(None, motivo=f"el video pesa {tam / 1048576:.1f} MB y el máximo en línea es "
                                   f"{cfg['max_mb_en_linea']} MB", del_material=True)
    cuerpo = cuerpo_peticion(instruccion(marca, temporada, duracion, juego, nota, recorte, historia,
                                      gancho_inicio_s), ruta_video.read_bytes(),
                             float(cfg["temperatura"]), historia=bool(historia))
    if len(cuerpo) > TOPE_PETICION_BYTES:     # el base64 infla un tercio: se mide lo que de verdad se envía
        return Mirada(None, motivo=f"la petición serializada pesa {len(cuerpo) / 1048576:.1f} MB y el tope es "
                                   f"{TOPE_PETICION_BYTES // 1048576} MB", del_material=True)
    inicio_ojos = time.monotonic()
    tope_ojos = _numero_cfg(cfg, "tope_ojos_s", TOPE_OJOS_S)
    por_intento = _numero_cfg(cfg, "tiempo_por_intento_s", TIEMPO_ESPERA_S)

    def presupuesto() -> float:
        """Segundos que quedan: lo que resta del tope de ojos y lo que resta del plazo del ciclo."""
        return min(tope_ojos - (time.monotonic() - inicio_ojos), plazo.restante() - plazo.MARGEN_S)

    cuenta = mod_cupos.Cupos(raiz) if registrar_cupos else None    # #21: cada petición queda en la cuenta del día

    def llamar(url, encabezados, cuerpo, tiempo):
        if cuenta is not None:
            cuenta.registrar("gemini")
        if transporte is None:
            estado_t, texto_t = transporte_urllib(url, encabezados, cuerpo, tiempo=tiempo)
        elif _acepta_tiempo(transporte):
            estado_t, texto_t = transporte(url, encabezados, cuerpo, tiempo)
        else:
            estado_t, texto_t = transporte(url, encabezados, cuerpo)    # los transportes antiguos reciben 3 argumentos
        if cuenta is not None and estado_t == 200:
            cuenta.sumar_tokens("gemini", mod_cupos.tokens_de_gemini(texto_t))
        return estado_t, texto_t
    encabezados = {"Content-Type": "application/json", "x-goog-api-key": clave}
    mirada = Mirada(None)
    # ante 429/5xx/red persistentes se prueban hasta N modelos más (18 nov: 503 y timeouts)
    respaldos_por_falla = int(_numero_cfg(cfg, "respaldos_por_falla", RESPALDOS_POR_FALLA, entero=True))
    for modelo in _modelos_a_probar(cfg, clave, mirada, transporte, transporte_lista,
                              max(1.0, min(float(TIEMPO_LISTA_S), presupuesto()))):
        url = URL_BASE.format(modelo=modelo)
        estado, texto = None, ""
        caida = None
        for intento in (1, 2):
            quedan = presupuesto()
            if quedan < MINIMO_INTENTO_S:
                mirada.motivo = (f"sin tiempo para Gemini (quedan {max(quedan, 0):.0f} s del presupuesto de "
                                 f"{tope_ojos:g} s o del plazo del ciclo)")
                mirada.pasajero = True
                mirada.datos = None
                mirada.intentos.append(f"{modelo}: {mirada.motivo}")
                return mirada
            try:
                estado, texto = llamar(url, encabezados, cuerpo, min(por_intento, quedan))
            except ErrorOjos as e:
                mirada.intentos.append(f"{modelo}: {e} (intento {intento})")
                caida = str(e)
                if intento == 1:
                    espera(max(0.0, min(ESPERA_REINTENTO_S, presupuesto() - MINIMO_INTENTO_S)))
                    continue
                break
            caida = None
            if estado == 429 or 500 <= estado < 600:
                mirada.intentos.append(f"{modelo}: HTTP {estado} (intento {intento})")
                if intento == 1:
                    espera(max(0.0, min(ESPERA_REINTENTO_S, presupuesto() - MINIMO_INTENTO_S)))
                    continue
            break
        if caida is not None or estado == 429 or (estado is not None and 500 <= estado < 600):
            mirada.motivo = caida or f"Gemini respondió HTTP {estado} con {modelo}"
            mirada.pasajero = True
            if respaldos_por_falla > 0:
                respaldos_por_falla -= 1
                continue
            return mirada
        if estado == 404:
            mirada.intentos.append(f"{modelo}: HTTP 404, no existe; se prueba el siguiente")
            if mirada.pasajero and respaldos_por_falla <= 0:     # ya se gastaron los respaldos por caída
                return mirada
            continue
        if estado != 200:
            mirada.motivo = f"Gemini respondió HTTP {estado} con {modelo}"
            mirada.pasajero = False
            # 401/403 y los 400 de clave o permiso son de la máquina; sólo 400 INVALID_ARGUMENT y 413 son del video.
            mirada.clave_mala = estado in (401, 403) or (estado == 400 and _error_de_clave(texto))
            mirada.del_material = not mirada.clave_mala
            return mirada
        mirada.modelo = modelo
        mirada.pasajero = False
        mirada.intentos.append(f"{modelo}: HTTP 200")
        try:
            crudo = _texto_de_respuesta(texto)
        except ErrorOjos as e:
            mirada.motivo = str(e)
            mirada.del_material = True
            return mirada
        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError:
            mirada.motivo = "la respuesta del modelo no es JSON válido"
            mirada.del_material = True
            return mirada
        if not isinstance(datos, dict):
            mirada.motivo = "la respuesta del modelo no es un objeto JSON"
            mirada.del_material = True
            return mirada
        mirada.datos = datos
        return mirada
    if not mirada.pasajero:
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
                      juego: str | None = None,
                      recorte: tuple[float, float] | None = None) -> tuple[bool, list[str]]:
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
    r_min, r_max = recorte or (RECORTE_MIN_S, RECORTE_MAX_S)
    if not r_min - TOL_S <= largo <= r_max + TOL_S:
        m.append(f"recorte de {largo:.1f} s: debe durar de {r_min:g} a {r_max:g} s")

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

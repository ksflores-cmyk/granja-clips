"""Cadena de modelos para pedir JSON con imágenes: Gemini -> segundo proveedor -> (sin IA lo resuelve quien llama).

Q0: sólo Gemini (gratis) y un segundo proveedor genérico compatible con OpenAI que hoy está APAGADO
(`criticos.segundo.activo: false`; GitHub Models se retiró el 30 jul 2026). La clave de Gemini sólo se lee de
`GEMINI_API_KEY` y nunca se imprime ni entra en un mensaje. El transporte es inyectable, igual que en `ojos`:
`transporte(url, encabezados, cuerpo, tiempo) -> (estado, texto)`.

Reglas: 429, 5xx y red = pasajero (no es culpa del material); 404 = siguiente modelo; 401/403 = sin Gemini para el
resto del ciclo; una respuesta que no valida cuenta como fallo del eslabón y se pasa al siguiente.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field

from . import cupos as mod_cupos
from . import ojos, plazo
from .config import cargar_yaml

MINIMO_LLAMADA_S = 5.0
_estado = {"sin_gemini": None}          # motivo si 401/403: Gemini queda apagado para el resto del proceso


def reiniciar() -> None:
    _estado["sin_gemini"] = None


@dataclass
class Respuesta:
    datos: dict | None
    proveedor: str | None = None
    modelo: str | None = None
    intentos: list[str] = field(default_factory=list)     # un renglón por intento (sin claves)
    pasajero: bool = False            # falló la máquina o el proveedor (429, 5xx, red, cupo, tiempo), no el contenido
    peticiones: dict = field(default_factory=dict)         # {"gemini": n, "segundo": n}: peticiones enviadas
    motivo: str | None = None


def _imagenes_b64(imagenes) -> list[str]:
    return [base64.b64encode(i).decode("ascii") for i in imagenes]


def cuerpo_gemini(instruccion: str, imagenes: list[bytes], esquema: dict | None, temperatura: float) -> bytes:
    partes = [{"inline_data": {"mime_type": "image/jpeg", "data": b}} for b in _imagenes_b64(imagenes)]
    partes.append({"text": instruccion})
    gen = {"responseMimeType": "application/json", "temperature": temperatura}
    if esquema:
        gen["responseSchema"] = esquema
    return json.dumps({"contents": [{"parts": partes}], "generationConfig": gen}).encode("utf-8")


def cuerpo_chat(modelo: str, instruccion: str, imagenes: list[bytes], esquema: dict | None,
                temperatura: float) -> bytes:
    texto = instruccion
    if esquema:
        texto += "\n\nResponde SOLO con un objeto JSON con esta forma (tipos en mayúsculas):\n" + json.dumps(esquema)
    contenido = [{"type": "text", "text": texto}]
    contenido += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
                  for b in _imagenes_b64(imagenes)]
    return json.dumps({"model": modelo, "messages": [{"role": "user", "content": contenido}],
                       "response_format": {"type": "json_object"}, "temperature": temperatura}).encode("utf-8")


def _llamar(transporte, url, encabezados, cuerpo, tiempo):
    if transporte is None:
        return ojos.transporte_urllib(url, encabezados, cuerpo, tiempo=tiempo)
    if ojos._acepta_tiempo(transporte):
        return transporte(url, encabezados, cuerpo, tiempo)
    return transporte(url, encabezados, cuerpo)


def _texto_chat(texto_http: str) -> str:
    try:
        d = json.loads(texto_http)
        t = d["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ojos.ErrorOjos("la respuesta del segundo proveedor no tiene el formato de chat/completions") from None
    if not isinstance(t, str) or not t.strip():
        raise ojos.ErrorOjos("el segundo proveedor devolvió una respuesta vacía")
    return t


def _objeto(crudo: str) -> dict:
    try:
        d = json.loads(crudo)
    except ValueError:
        raise ojos.ErrorOjos("la respuesta no es JSON válido") from None
    if not isinstance(d, dict):
        raise ojos.ErrorOjos("la respuesta no es un objeto JSON")
    return d


def pedir_json(instruccion: str, imagenes: list[bytes], esquema: dict | None, *, raiz=None, entorno=None,
               transporte=None, plazo_s: float = 120.0, validar=None, espera=time.sleep,
               transporte_lista=None, config: dict | None = None, cfg_ojos: dict | None = None,
               ahora=None) -> Respuesta:
    """Pide un JSON a la cadena. `validar(datos) -> list[str]` (errores) la pone quien llama: si hay errores, el
    eslabón falla y se prueba el siguiente. `plazo_s` es el tope de TODA la petición (todos los intentos).

    Devuelve `Respuesta(datos=None, ...)` si ningún eslabón dio una respuesta válida; entonces quien llama usa
    su propia solución sin IA."""
    import os
    env = os.environ if entorno is None else entorno
    cfg = config if config is not None else cargar_yaml("config/criticos.yml", raiz)
    cfg_o = cfg_ojos if cfg_ojos is not None else cargar_yaml("config/ojos.yml", raiz)
    r = Respuesta(None)
    inicio = time.monotonic()
    cuenta = mod_cupos.Cupos(raiz, ahora)
    cup = cfg.get("cupos") or {}
    espera_max = float(cup.get("espera_max_s", 30))
    temp = float(cfg.get("temperatura", 0.2))
    por_llamada = float(cfg.get("tiempo_por_llamada_s", 40))

    def presupuesto() -> float:
        return min(plazo_s - (time.monotonic() - inicio), plazo.restante() - plazo.MARGEN_S)

    def veto_de(datos) -> list[str]:
        return list(validar(datos)) if validar is not None else []

    # ---------------------------------------------------------------- eslabón 1: Gemini
    clave = env.get("GEMINI_API_KEY", "")
    if not clave:
        r.intentos.append(f"gemini: {ojos.MOTIVO_SIN_CLAVE}")
        r.pasajero, r.motivo = True, ojos.MOTIVO_SIN_CLAVE
    elif _estado["sin_gemini"]:
        r.intentos.append(f"gemini: apagado el resto del ciclo ({_estado['sin_gemini']})")
        r.pasajero, r.motivo = True, _estado["sin_gemini"]
    else:
        previa = ojos.Mirada(None)
        modelos = ojos._modelos_a_probar(cfg_o, clave, previa, transporte, transporte_lista,
                                         max(1.0, min(float(ojos.TIEMPO_LISTA_S), presupuesto())))
        r.intentos += previa.intentos
        g = cup.get("gemini") or {}
        cuerpo = cuerpo_gemini(instruccion, imagenes, esquema, temp)
        encabezados = {"Content-Type": "application/json", "x-goog-api-key": clave}
        pausa = cuenta.pausado("gemini")
        if pausa > 0:                       # un 429 reciente: los críticos esperan (los ojos no se enteran)
            r.intentos.append(f"gemini: agotado por un 429 reciente; faltan {pausa:.0f} s")
            r.pasajero, r.motivo = True, "Gemini agotado (429 reciente)"
            modelos = []
        for modelo in modelos:
            quedan = presupuesto()
            if quedan < MINIMO_LLAMADA_S:
                r.intentos.append(f"{modelo}: sin tiempo ({max(quedan, 0):.0f} s)")
                r.pasajero, r.motivo = True, "sin tiempo para Gemini"
                break
            ok, motivo = cuenta.esperar_cupo("gemini", 1, rpm=g.get("rpm"), rpd=g.get("rpd"),
                                             reserva=int(g.get("reserva_ojos", 0)), espera=espera,
                                             espera_max_s=min(espera_max, max(0.0, quedan - MINIMO_LLAMADA_S)))
            if not ok:
                r.intentos.append(f"gemini: {motivo}")
                r.pasajero, r.motivo = True, motivo
                break
            cuenta.registrar("gemini")
            r.peticiones["gemini"] = r.peticiones.get("gemini", 0) + 1
            url = ojos.URL_BASE.format(modelo=modelo)
            try:
                estado, texto = _llamar(transporte, url, encabezados, cuerpo, min(por_llamada, presupuesto()))
            except ojos.ErrorOjos as e:
                r.intentos.append(f"{modelo}: {e}")
                r.pasajero, r.motivo = True, str(e)
                continue                                 # red: se prueba el siguiente modelo
            if estado == 404:
                r.intentos.append(f"{modelo}: HTTP 404, no existe; se prueba el siguiente")
                continue
            if estado == 429:
                r.intentos.append(f"{modelo}: HTTP 429")
                r.pasajero, r.motivo = True, "Gemini respondió HTTP 429"
                cuenta.pausar("gemini", float(g.get("pausa_429_s", 600)))
                break                                    # el cupo es del proyecto: otro modelo no ayuda
            if 500 <= estado < 600:
                r.intentos.append(f"{modelo}: HTTP {estado}")
                r.pasajero, r.motivo = True, f"Gemini respondió HTTP {estado}"
                continue
            if estado in (401, 403):
                _estado["sin_gemini"] = f"HTTP {estado}"
                r.intentos.append(f"{modelo}: HTTP {estado}; sin Gemini el resto del ciclo")
                r.pasajero, r.motivo = False, f"Gemini respondió HTTP {estado} (clave o permiso)"
                break
            if estado != 200:
                r.intentos.append(f"{modelo}: HTTP {estado}")
                r.motivo = f"Gemini respondió HTTP {estado}"
                break
            cuenta.sumar_tokens("gemini", mod_cupos.tokens_de_gemini(texto))
            try:
                datos = _objeto(ojos._texto_de_respuesta(texto))
            except ojos.ErrorOjos as e:
                r.intentos.append(f"{modelo}: HTTP 200 pero {e}")
                r.motivo, r.pasajero = str(e), False
                break
            errores = veto_de(datos)
            if errores:
                r.intentos.append(f"{modelo}: HTTP 200 pero la respuesta no valida ({'; '.join(errores)[:160]})")
                r.motivo, r.pasajero = "respuesta inválida", False
                break                                    # eslabón fallido: pasa al siguiente
            r.intentos.append(f"{modelo}: HTTP 200")
            r.datos, r.proveedor, r.modelo, r.pasajero, r.motivo = datos, "gemini", modelo, False, None
            return r
        else:
            if not r.pasajero and not r.motivo:
                r.motivo = "ningún modelo de Gemini respondió (todos dieron 404)"

    # ---------------------------------------------------------------- eslabón 2: segundo proveedor (genérico)
    seg = cfg.get("segundo") or {}
    if not seg.get("activo"):
        r.intentos.append(f"segundo: apagado ({seg.get('motivo') or 'sin motivo escrito'})")
        return r
    nombre_secreto = str(seg.get("secreto") or "")
    secreto = env.get(nombre_secreto, "") if nombre_secreto else ""
    if not secreto:
        r.intentos.append(f"segundo: sin {nombre_secreto or 'variable de secreto'}")
        return r
    quedan = presupuesto()
    if quedan < MINIMO_LLAMADA_S:
        r.intentos.append("segundo: sin tiempo")
        r.pasajero = True
        return r
    s = cup.get("segundo") or {}
    ok, motivo = cuenta.esperar_cupo("segundo", 1, rpm=s.get("rpm"), rpd=s.get("rpd"), espera=espera,
                                     espera_max_s=min(espera_max, max(0.0, quedan - MINIMO_LLAMADA_S)))
    if not ok:
        r.intentos.append(f"segundo: {motivo}")
        r.pasajero, r.motivo = True, motivo
        return r
    cuenta.registrar("segundo")
    r.peticiones["segundo"] = r.peticiones.get("segundo", 0) + 1
    cuerpo = cuerpo_chat(str(seg.get("modelo")), instruccion, imagenes, esquema, temp)
    enc = {"Content-Type": "application/json", "Authorization": f"Bearer {secreto}"}
    try:
        estado, texto = _llamar(transporte, str(seg.get("url")), enc, cuerpo, min(por_llamada, presupuesto()))
    except ojos.ErrorOjos as e:
        r.intentos.append(f"segundo: {str(e).replace('Gemini', 'el proveedor')}")
        r.pasajero, r.motivo = True, "falla de red con el segundo proveedor"
        return r
    if estado == 429 or 500 <= estado < 600:
        r.intentos.append(f"segundo: HTTP {estado}")
        r.pasajero, r.motivo = True, f"el segundo proveedor respondió HTTP {estado}"
        return r
    if estado != 200:
        r.intentos.append(f"segundo: HTTP {estado}")
        r.motivo = f"el segundo proveedor respondió HTTP {estado}"
        return r
    try:
        datos = _objeto(_texto_chat(texto))
    except ojos.ErrorOjos as e:
        r.intentos.append(f"segundo: HTTP 200 pero {e}")
        r.motivo = str(e)
        return r
    errores = veto_de(datos)
    if errores:
        r.intentos.append(f"segundo: HTTP 200 pero la respuesta no valida ({'; '.join(errores)[:160]})")
        r.motivo = "respuesta inválida"
        return r
    r.intentos.append("segundo: HTTP 200")
    r.datos, r.proveedor, r.modelo, r.pasajero, r.motivo = datos, "segundo", str(seg.get("modelo")), False, None
    return r

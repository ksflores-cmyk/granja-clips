"""El ciclo de la fábrica: detectar, tomar, subir y programar. Corre cada hora en GitHub Actions.

`python -m fabrica.ciclo [--decidir] [--ensayo-forzado] [--ahora ISO] [--salida CARPETA]`.
El orden de los pasos no se cambia: 1) PAUSA, 2) puerta de configuración, 3) cadencia, 4) tope de Actions,
5) hueco, 6) candidatos, 7) respaldo informativo, 8) publicar (vivo) o ensayo, 9) --ensayo-forzado, 10) alerta roja.

Calendario (config/horarios.yml): cada corrida programa como máximo un hueco, el normal que cae en su ventana o,
si no hay, el relleno de un hueco perdido de hoy a ahora+margen (sin amontonar, sin repetir clips en 24 h y sin
pasar la cola de Buffer gratis).

Cadencia: la corrida con un hueco por cubrir siempre sigue; la cadencia (cada hora del 18 al 20, desde el 21
sólo en horas múltiplo de 3) frena las corridas sin hueco. El número de toma avanza si al menos una plataforma
quedó programada (o incierta): las que fallaron pierden esa toma y ese hueco, nunca se reintenta la misma toma.

Todo lo de red entra por parámetros (transportes, `detectar_fn`, `hacer_toma_fn`) para probarlo sin red.
Nunca imprime claves: cada línea sale redactada y, si una clave aparecía, es alerta roja.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import detectar as mod_detectar
from . import informativa, plazo, publicar, puertas, red, registro, retiros, shorts
from . import validar as mod_validar
from .almacen import Almacen, ErrorAlmacen
from .config import cargar_yaml, raiz_repo
from .editar import toma as mod_toma
from .fuentes import cargar_fuentes, es_publicable
from .pausa import crear_pausa, esta_pausada
from .registro import ZONA

PLATAFORMAS = publicar.PLATAFORMAS
SECRETOS = ("BUFFER_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TWITCH_CLIENT_ID",
            "TWITCH_CLIENT_SECRET", "GEMINI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")
DIA_CADENCIA_LENTA = date(2026, 11, 21)      # desde este día (GT) sólo corre en horas múltiplo de 3
TOPE_MINUTOS_DIA = 60                            # valor por defecto: el de verdad es actions.tope_minutos_dia
MARGEN_MIN_S, MARGEN_MAX_S = 15 * 60, 75 * 60    # por defecto: el hueco cae de ahora+15 a ahora+75 min
MAX_INTENTOS = 3
TIEMPO_MIN_INTENTO_S = 240      # menos que esto del plazo: no se empieza otro intento de toma
FALLOS_CUARENTENA = 3           # fallos seguidos (red, tiempo o máquina) de una fuente antes de la cuarentena
HORAS_CUARENTENA = 24
TIEMPO_MIN_ARRANQUE_S = 60      # menos que esto al empezar (por una instalación lenta): no se hace nada
TIEMPO_MIN_RETIROS_S = 30       # menos que esto del plazo: no se lee Buffer en busca de reclamos
TIEMPO_MIN_PUBLICAR_S = 120     # menos que esto: no se publica (la fuente no se gasta)
RE_CUENTA = re.compile(r"suspend|restrict|bloquead|blocked|disabled|deshabilitad", re.IGNORECASE)
RE_HTTP_CUENTA = re.compile(r"código (401|403)\b")
ESTADOS_AVISO = ("incierto", "abandonado")
COD_OK, COD_ERROR, COD_NEGADO = 0, 1, 2


# ------------------------------------------------------------------ utilidades

class AlertaRojaCiclo(Exception):
    """Algo que obliga a parar: se crea PAUSA y estado/alerta.json."""


def imprimir_con_flush(texto) -> None:
    """print con flush: si la corrida se cancela, el log que ya se escribió no se pierde en el búfer."""
    print(texto, flush=True)


class Salida:
    """Imprime cada línea sin claves y guarda el texto crudo para buscar fugas."""

    def __init__(self, imprimir=None, env=None):
        self.imprimir = imprimir or imprimir_con_flush
        self.valores = {k: v for k in SECRETOS if len(v := (env or {}).get(k, "")) >= 8}
        self.secretos = list(self.valores.values())
        self.lineas: list[str] = []
        self.vigilar = False       # con el ciclo en marcha, una clave en cualquier texto lo corta

    def __call__(self, texto) -> None:
        texto = str(texto)
        self.lineas.append(texto)
        self.imprimir(red.ocultar(texto, self.secretos))
        if self.vigilar:
            for nombre, valor in self.valores.items():
                if valor in texto:
                    raise AlertaRojaCiclo(f"una clave apareció en el log ({nombre})")

    def fuga(self, env) -> str | None:
        """Nombre de la variable cuya clave apareció en lo que se imprimió, o None."""
        todo = "\n".join(self.lineas)
        for k in SECRETOS:
            v = (env or {}).get(k, "")
            if len(v) >= 8 and v in todo:
                return k
        return None


def _ahora(ahora) -> datetime:
    if ahora is None:
        return datetime.now(timezone.utc)
    return ahora if ahora.tzinfo else ahora.replace(tzinfo=ZONA)


def _escribir_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


def _leer_json(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


# ------------------------------------------------------------------ cuarentena de fuentes que fallan por no-material

def _ruta_reintentos(raiz) -> Path:
    return raiz_repo(raiz) / "estado" / "reintentos.json"


def _leer_reintentos(raiz) -> dict:
    datos = _leer_json(_ruta_reintentos(raiz), {})
    return datos if isinstance(datos, dict) else {}


def _hasta_cuarentena(raiz, id_origen, ahora) -> datetime | None:
    """Hasta cuándo está en cuarentena (None si no lo está o ya venció)."""
    e = _leer_reintentos(raiz).get(id_origen)
    try:
        hasta = datetime.fromisoformat(e["hasta"]) if isinstance(e, dict) and e.get("hasta") else None
    except (ValueError, TypeError):
        return None
    if hasta is None:
        return None
    if hasta.tzinfo is None:
        hasta = hasta.replace(tzinfo=timezone.utc)
    return hasta if hasta > ahora else None


def _sumar_fallo(raiz, id_origen, ahora, out) -> None:
    """Un intento falló sin que la fuente se gaste: 3 seguidos la ponen 24 h en cuarentena (NO se gasta)."""
    datos = _leer_reintentos(raiz)
    for k in [k for k, e in datos.items()                      # poda: cuarentenas vencidas sin fallos nuevos
              if not isinstance(e, dict) or (not e.get("fallos") and not _hasta_cuarentena(raiz, k, ahora))]:
        datos.pop(k, None)
    e = datos.get(id_origen) if isinstance(datos.get(id_origen), dict) else {"fallos": 0, "hasta": None}
    e["fallos"] = int(e.get("fallos") or 0) + 1
    if e["fallos"] >= FALLOS_CUARENTENA:
        hasta = ahora + timedelta(hours=HORAS_CUARENTENA)
        e = {"fallos": 0, "hasta": hasta.astimezone(timezone.utc).replace(microsecond=0).isoformat()}
        texto = (f"fuente en cuarentena {HORAS_CUARENTENA} h tras {FALLOS_CUARENTENA} fallos de "
                 f"red/tiempo/máquina: {id_origen}")
        out(texto)
        registro.anotar_registro(raiz, "ciclo: " + texto)
    datos[id_origen] = e
    _escribir_json(_ruta_reintentos(raiz), datos)


def _borrar_reintento(raiz, id_origen) -> None:
    datos = _leer_reintentos(raiz)
    if id_origen in datos:
        datos.pop(id_origen)
        _escribir_json(_ruta_reintentos(raiz), datos)


def escribir_salida_github(env, **pares) -> None:
    ruta = (env or {}).get("GITHUB_OUTPUT")
    if ruta:
        with open(ruta, "a", encoding="utf-8") as f:
            for k, v in pares.items():
                f.write(f"{k}={v}\n")


# ------------------------------------------------------------------ pasos 3 y 4: cadencia y tope

def en_cadencia(ahora: datetime) -> bool:
    """Del 18 al 20 de nov (GT), cada hora; desde el 21, sólo en las horas múltiplo de 3.

    Un hueco a cubrir siempre cuenta: la cadencia sólo frena las corridas sin hueco (el mantenimiento),
    así los huecos de horarios.yml se cubren aunque su corrida caiga en otra hora."""
    local = ahora.astimezone(ZONA)
    return local.date() < DIA_CADENCIA_LENTA or local.hour % 3 == 0


MINUTOS_PRODUCIR = 20          # lo que se cobra a un `producir` que no llegó a cerrar
CLAVE_EN_CURSO = "_en_curso"


def _ruta_minutos(raiz) -> Path:
    return raiz_repo(raiz) / "estado" / "actions-minutos.json"


def _leer_minutos(raiz) -> dict:
    datos = _leer_json(_ruta_minutos(raiz), {})
    return datos if isinstance(datos, dict) else {}


def _marca_ajena(datos: dict, run_id) -> dict | None:
    """La marca de inicio de un `producir` que no cerró (otra corrida distinta de la actual)."""
    m = datos.get(CLAVE_EN_CURSO)
    if isinstance(m, dict) and m.get("inicio") and str(m.get("run_id") or "") != str(run_id or "\0"):
        return m
    return None


def minutos_del_dia(raiz, ahora: datetime, run_id=None) -> float:
    """Minutos anotados hoy (GT), más los 20 de un `producir` anterior que no llegó a cerrar."""
    datos = _leer_minutos(raiz)
    dia = ahora.astimezone(ZONA).date().isoformat()
    total = float(datos.get(dia, 0))
    m = _marca_ajena(datos, run_id)
    if m and str(m["inicio"])[:10] == dia:
        total += MINUTOS_PRODUCIR
    return total


def minutos_de_decidir(ahora: datetime) -> int:
    """Cada hora corre un `decidir` de 1 minuto: se cuentan los del día hasta ahora (no dejan commit)."""
    return ahora.astimezone(ZONA).hour + 1


def minutos_usados(raiz, ahora: datetime, run_id=None) -> float:
    return minutos_del_dia(raiz, ahora, run_id) + minutos_de_decidir(ahora)


def tope_minutos(raiz) -> int:
    """actions.tope_minutos_dia de horarios.yml (60 si falta)."""
    return int(_cfg_horarios(raiz)["tope_minutos_dia"])


def admite_producir(raiz, ahora: datetime, run_id=None) -> bool:
    """`producir` sólo entra si quedan al menos 20 minutos del tope diario de Actions."""
    return minutos_usados(raiz, ahora, run_id) + MINUTOS_PRODUCIR <= tope_minutos(raiz)


def marcar_inicio(raiz, ahora: datetime, run_id) -> None:
    """Se escribe al empezar `producir`: si la corrida muere por tiempo, la siguiente le cobra sus 20 minutos."""
    datos = _leer_minutos(raiz)
    datos[CLAVE_EN_CURSO] = {"inicio": ahora.astimezone(ZONA).replace(microsecond=0).isoformat(),
                             "run_id": str(run_id or "")}
    _escribir_json(_ruta_minutos(raiz), datos)


def sumar_minutos(raiz, ahora: datetime, minutos: float, run_id=None) -> float:
    """Suma los minutos de una corrida al total del día (GT), cierra su marca de inicio y poda lo viejo.

    Si la marca es de otra corrida (que murió sin cerrar), le cobra sus 20 minutos en el día de esa marca."""
    datos = _leer_minutos(raiz)
    ajena = _marca_ajena(datos, run_id)
    datos.pop(CLAVE_EN_CURSO, None)
    if ajena:
        d = str(ajena["inicio"])[:10]
        datos[d] = round(float(datos.get(d, 0)) + MINUTOS_PRODUCIR, 2)
    dia = ahora.astimezone(ZONA).date()
    datos[dia.isoformat()] = round(float(datos.get(dia.isoformat(), 0)) + float(minutos), 2)
    corte = (dia - timedelta(days=14)).isoformat()
    datos = {k: v for k, v in datos.items() if k >= corte}
    _escribir_json(_ruta_minutos(raiz), datos)
    return datos[dia.isoformat()]


# ------------------------------------------------------------------ paso 5: hueco

def _ruta_cerrados(raiz) -> Path:
    return raiz_repo(raiz) / "estado" / "huecos-cerrados.json"


def huecos_cerrados(raiz) -> set[tuple]:
    datos = _leer_json(_ruta_cerrados(raiz), [])
    return {(d.get("fecha"), d.get("hora"), d.get("plataforma")) for d in datos if isinstance(d, dict)} \
        if isinstance(datos, list) else set()


def cerrar_hueco(raiz, hueco, plataforma: str) -> None:
    """Un hueco queda cerrado para una plataforma en cuanto se intentó (salió o falló): no se reintenta."""
    datos = _leer_json(_ruta_cerrados(raiz), [])
    datos = datos if isinstance(datos, list) else []
    nuevo = {"fecha": hueco.fecha, "hora": hueco.hora, "plataforma": plataforma}
    if nuevo not in datos:
        datos.append(nuevo)
        _escribir_json(_ruta_cerrados(raiz), datos[-200:])


@dataclass
class Hueco:
    cuando: datetime                  # hora local de Guatemala del hueco ORIGINAL (es la que se cierra y se anota)
    plataformas: list[str]
    publicar_en: datetime | None = None    # hora local real del post si es un relleno

    @property
    def fecha(self) -> str:
        return self.cuando.date().isoformat()

    @property
    def hora(self) -> str:
        return self.cuando.strftime("%H:%M")

    @property
    def programar_en(self) -> datetime:
        return self.publicar_en or self.cuando

    @property
    def relleno(self) -> bool:
        return self.publicar_en is not None and self.publicar_en != self.cuando

    def descripcion(self) -> str:
        extra = f" (relleno a las {self.publicar_en.strftime('%H:%M')})" if self.relleno else ""
        return f"hueco {self.fecha} {self.hora}{extra}"


def _cfg_horarios(raiz) -> dict:
    """horarios.yml con valores por defecto para lo opcional. Obligatorias: zona y huecos."""
    h = cargar_yaml("config/horarios.yml", raiz) or {}
    rel = h.get("relleno") or {}
    act = h.get("actions") or {}
    return {
        "zona": h.get("zona"),
        "huecos": [str(x) for x in h.get("huecos") or []],
        "margen_min_s": int(h.get("margen_min_minutos", MARGEN_MIN_S // 60)) * 60,
        "margen_max_s": int(h.get("margen_max_minutos", MARGEN_MAX_S // 60)) * 60,
        "separacion_s": int(rel.get("separacion_min_minutos", 60)) * 60,
        "ultima_hora": str(rel.get("ultima_hora", "23:00")),
        "max_intentos": int(rel.get("max_intentos_por_hueco", 2)),
        "cola_max": int(h.get("cola_buffer_max", 10)),
        "no_repetir_horas": int(h.get("no_repetir_horas", 24)),
        "tope_minutos_dia": int(act.get("tope_minutos_dia", TOPE_MINUTOS_DIA)),
    }


def _hhmm(texto: str) -> tuple[int, int]:
    h, m = (int(x) for x in str(texto).split(":"))
    return h, m


def _a_las(dia: date, texto: str) -> datetime:
    h, m = _hhmm(texto)
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=ZONA)


def _ruta_intentos(raiz) -> Path:
    return raiz_repo(raiz) / "estado" / "huecos-intentos.json"


def intentos_de_huecos(raiz) -> dict[tuple, int]:
    datos = _leer_json(_ruta_intentos(raiz), [])
    if not isinstance(datos, list):
        return {}
    return {(d.get("fecha"), d.get("hora")): int(d.get("intentos") or 0) for d in datos if isinstance(d, dict)}


def sumar_intento(raiz, hueco: Hueco) -> int:
    """Anota un intento más del hueco ORIGINAL (se poda a 100). Devuelve cuántos lleva."""
    datos = _leer_json(_ruta_intentos(raiz), [])
    datos = [d for d in datos if isinstance(d, dict)] if isinstance(datos, list) else []
    for d in datos:
        if d.get("fecha") == hueco.fecha and d.get("hora") == hueco.hora:
            d["intentos"] = int(d.get("intentos") or 0) + 1
            n = d["intentos"]
            break
    else:
        datos.append({"fecha": hueco.fecha, "hora": hueco.hora, "intentos": 1})
        n = 1
    _escribir_json(_ruta_intentos(raiz), datos[-100:])
    return n


def _redondear_5(t: datetime) -> datetime:
    """Hacia arriba al siguiente múltiplo de 5 minutos (sin segundos)."""
    base = t.replace(second=0, microsecond=0)
    if t != base:
        base += timedelta(minutes=1)
    return base + timedelta(minutes=(-base.minute) % 5)


def _horas_de_la_red(raiz, filas: list[dict], plataforma: str, dia: date) -> list[datetime]:
    """Horas (GT) de lo ya publicado o programado en esa red ese día: marcador y libro de envíos."""
    horas = []
    for f in filas:
        if f.get("plataforma") == plataforma and f.get("fecha") == dia.isoformat():
            try:
                horas.append(_a_las(dia, f.get("hora") or ""))
            except (ValueError, TypeError):
                continue
    for e in _libro(raiz):
        if e.get("plataforma") != plataforma or e.get("estado") in ("rechazado", "no_creado"):
            continue
        try:
            t = publicar._a_utc(e["dueAt"]).astimezone(ZONA)
        except (KeyError, ValueError, TypeError):
            continue
        if t.date() == dia:
            horas.append(t)
    return horas


def buscar_hueco(raiz, ahora: datetime, activas: list[str]) -> Hueco | None:
    """El hueco de esta corrida. Primero el normal de horarios.yml entre ahora+margen_min y ahora+margen_max con
    alguna red libre. Si no hay, el relleno: un hueco de hoy ya perdido, a ahora+margen_min+duración máxima del ciclo (redondeado a 5 min),
    sólo si no se amontona con lo demás del día. Sin nada, None."""
    if not activas:
        return None
    hz = _cfg_horarios(raiz)
    local = ahora.astimezone(ZONA)
    filas = puertas._filas_marcador(raiz)
    ocupados = {(f.get("fecha"), f.get("hora"), f.get("plataforma")) for f in filas} | huecos_cerrados(raiz)
    huecos = [(dia, hhmm) for dia in (local.date(), local.date() + timedelta(days=1)) for hhmm in hz["huecos"]]
    candidatos = sorted({_a_las(dia, hhmm) for dia, hhmm in huecos
                         if hz["margen_min_s"] <= (_a_las(dia, hhmm) - local).total_seconds() <= hz["margen_max_s"]})
    for cuando in candidatos:
        libres = [p for p in activas if (cuando.date().isoformat(), cuando.strftime("%H:%M"), p) not in ocupados]
        if libres:
            return Hueco(cuando, libres)
    # relleno
    hoy = local.date()
    # el relleno debe seguir siendo futuro (>= MINUTOS_MINIMOS_FUTURO) cuando termine el ciclo, que puede durar LIMITE_CICLO_S
    t = _redondear_5(local + timedelta(seconds=hz["margen_min_s"] + plazo.LIMITE_CICLO_S))
    if t.date() != hoy or t > _a_las(hoy, hz["ultima_hora"]):
        return None
    intentos = intentos_de_huecos(raiz)
    horas_red = {p: _horas_de_la_red(raiz, filas, p, hoy) for p in activas}
    perdidos = sorted((c for c in (_a_las(hoy, hhmm) for hhmm in hz["huecos"])
                       if (c - local).total_seconds() < hz["margen_min_s"]), reverse=True)
    for cuando in perdidos:
        if intentos.get((cuando.date().isoformat(), cuando.strftime("%H:%M")), 0) >= hz["max_intentos"]:
            continue
        otros = [_a_las(hoy, hhmm) for hhmm in hz["huecos"] if _a_las(hoy, hhmm) != cuando]
        aptas = [p for p in activas
                 if (cuando.date().isoformat(), cuando.strftime("%H:%M"), p) not in ocupados
                 and all(abs((t - x).total_seconds()) >= hz["separacion_s"] for x in otros + horas_red[p])]
        if aptas:
            return Hueco(cuando, aptas, publicar_en=t)
    return None


# ------------------------------------------------------------------ mantenimiento y avisos

def _libro(raiz) -> list[dict]:
    libro = _leer_json(raiz_repo(raiz) / "estado" / "buffer-envios.json", [])
    return [e for e in libro if isinstance(e, dict)] if isinstance(libro, list) else []


def hay_mantenimiento(raiz) -> bool:
    """Hay envíos sin cerrar o videos por limpiar: conviene un ciclo aunque no haya hueco."""
    return any(e.get("estado") in ("enviando", "incierto")
               or (e.get("estado") == "creado" and e.get("ruta") and not e.get("ruta_borrada"))
               for e in _libro(raiz))


def _avisados(raiz) -> list[str]:
    datos = _leer_json(raiz_repo(raiz) / "estado" / "envios-avisados.json", [])
    return [str(x) for x in datos] if isinstance(datos, list) else []


def avisos_pendientes(raiz) -> list[dict]:
    """Envíos `incierto` o `abandonado` que aún no tienen issue (uno por envío y estado)."""
    hechos = set(_avisados(raiz))
    nuevos = []
    for e in _libro(raiz):
        if e.get("estado") in ESTADOS_AVISO:
            clave = f"{e.get('id')}|{e['estado']}"
            if clave not in hechos:
                nuevos.append({"clave": clave, "estado": e["estado"], "plataforma": e.get("plataforma"),
                               "toma": e.get("toma"), "dueAt": e.get("dueAt")})
    return nuevos


def abrir_avisos(raiz, ejecutor=subprocess.run, env=None) -> list[str]:
    """Abre un issue por cada envío nuevo `incierto` o `abandonado` y lo apunta como avisado. Sin claves."""
    hechos = _avisados(raiz)
    abiertos = []
    for a in avisos_pendientes(raiz):
        titulo = f"Envío {a['estado']}: toma {a['toma']} en {a['plataforma']}"
        cuerpo = (f"El envío de la toma {a['toma']} a {a['plataforma']} (hora {a['dueAt']}) quedó "
                  f"{a['estado']} en estado/buffer-envios.json. No se repite a ciegas: revisa Buffer a mano.")
        try:
            r = ejecutor(["gh", "issue", "create", "--title", titulo, "--body", cuerpo],
                         capture_output=True, text=True, timeout=60)
            ok = getattr(r, "returncode", 1) == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            hechos.append(a["clave"])
            abiertos.append(a["clave"])
            _escribir_json(raiz_repo(raiz) / "estado" / "envios-avisados.json", hechos)
    return abiertos


# ------------------------------------------------------------------ alerta roja

def alerta_roja(raiz, motivo: str, ahora: datetime, secretos=()) -> None:
    """Crea PAUSA con el motivo y estado/alerta.json (sin claves). No publica nada más."""
    motivo = " ".join(red.ocultar(str(motivo), list(secretos)).split())[:300] or "alerta roja"
    with plazo.critico():            # la alarma dura no corta la creación de PAUSA ni de alerta.json
        if not esta_pausada(raiz)[0]:
            crear_pausa(raiz, motivo, "ciclo")
        _escribir_json(raiz_repo(raiz) / "estado" / "alerta.json",
                       {"motivo": motivo, "cuando": ahora.astimezone(ZONA).replace(microsecond=0).isoformat()})


def _revisar_retiros(raiz) -> str | None:
    """Un aviso de retiro o reclamo llega por la ficha de la fuente: `aviso_retiro` con texto."""
    for f in cargar_fuentes(raiz):
        if f.get("aviso_retiro"):
            return f"aviso de retiro o reclamo en la fuente {f.get('id')}"
    return None


def _motivo_cuenta(e: Exception) -> str | None:
    msg = str(e)
    if isinstance(e, publicar.AlertaRoja):
        return msg
    if isinstance(e, publicar.ErrorBuffer) and RE_HTTP_CUENTA.search(msg):
        return f"Buffer respondió {RE_HTTP_CUENTA.search(msg).group(1)} (clave o cuenta)"
    if isinstance(e, publicar.Descartada) and RE_CUENTA.search(msg):
        return "Buffer rechazó por la cuenta: " + msg[:200]
    return None


# ------------------------------------------------------------------ candidatos

def _peso(temporada: dict, pilar: str | None) -> float:
    return float((temporada.get("pilares") or {}).get(pilar or "", 0))


def _pilar_de(c, ficha: dict | None, temporada: dict) -> str | None:
    if c.pilar:
        return c.pilar
    pilares = [p for p in (ficha or {}).get("pilares") or [] if p != "informativa"]
    return max(pilares, key=lambda p: _peso(temporada, p), default=None)


def ordenar(candidatos: list, temporada: dict, pagadas=frozenset()) -> list:
    """Primero el material de una campaña pagada (`pagadas`: ids de ficha); luego los pilares con más peso;
    a igual pilar, más vistas por hora; luego, el más reciente."""
    sin_fecha = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(candidatos, key=lambda c: (not (c.fuente_id in pagadas and c.origen == "brief"),
                                             -_peso(temporada, c.pilar),
                                             -(c.vistas_por_hora or 0.0),
                                             -(c.creado_en or sin_fecha).timestamp()))


def elegir_candidatos(raiz, ahora, plataformas, out, detectar_fn=None, cliente=None) -> list:
    t = cargar_yaml("temporada.yml", raiz)
    try:
        cands, saltos = (detectar_fn or mod_detectar.detectar_con_motivos)(raiz, ahora, cliente)
    except plazo.SinTiempo:
        raise                # el plazo del ciclo no es un fallo del detector: lo maneja el ciclo
    except Exception as e:  # noqa: BLE001 (un fallo del detector no debe tumbar el ciclo)
        out(f"detectar: {type(e).__name__}: {e}")
        return []
    fichas = {f.get("id"): f for f in cargar_fuentes(raiz)}      # después de detectar: incluye las fichas T nuevas
    for s in saltos:
        out(f"detectar: {s}")
    for c in cands:
        c.pilar = _pilar_de(c, fichas.get(c.fuente_id), t)
    aprobados = None
    for plat in plataformas:
        ap, desc = puertas.filtrar(raiz, cands, ahora, plat, cliente)
        for c, motivo in desc:
            out(f"descartado {c.id_origen} en {plat}: {motivo}")
        ids = {c.id_origen for c in ap}
        aprobados = ap if aprobados is None else [c for c in aprobados if c.id_origen in ids]
    pagadas = {fid for fid, f in fichas.items()
               if f.get("tipo") == "A" and f.get("campana") and f.get("material")}
    return ordenar(aprobados or [], t, pagadas)


# ------------------------------------------------------------------ el ciclo

@dataclass
class Toma:
    resultado: mod_toma.Resultado
    toma: dict                    # lo que espera publicar.textos
    ficha: dict
    candidato: object | None = None
    entrada: dict | None = None   # entrada de informativas.yml, si es el respaldo


def _claves_de(c) -> list[str]:
    """Las claves de origen de un candidato (su id y, si trae varios, sus ids), ordenadas y sin repetir."""
    return sorted({str(x) for x in [c.id_origen, *(getattr(c, "ids_origen", None) or [])] if x})


def _frase_del_streamer(gemini) -> str:
    """La primera frase (en español) que dice el streamer en la respuesta de Gemini; vacía si no hay."""
    for ln in (gemini.get("lineas") if isinstance(gemini, dict) else None) or []:
        if isinstance(ln, dict) and str(ln.get("hablante", "")).strip().upper() == "STREAMER" \
                and isinstance(ln.get("es"), str) and ln["es"].strip():
            return ln["es"].strip()
    return ""


def _toma_dict(r, c, ficha: dict) -> dict:
    from .editar import nombres as mod_nombres
    f = r.fila
    caption = (getattr(r, "gemini", None) or {}).get("caption") if isinstance(getattr(r, "gemini", None), dict) else None
    creador = c.creador or ficha.get("creador")
    return {"numero": f["toma"], "gancho": f["gancho"], "pilar": f["pilar"], "fuente_id": ficha.get("id"),
            "caption": caption if isinstance(caption, str) else "",
            "frase": _frase_del_streamer(getattr(r, "gemini", None)),       # respaldo del caption (textos.respaldo_de_dialogo)
            "hablante": mod_nombres.nombre_para_mostrar(mod_nombres.creador_de(creador, ficha), ficha),
            "campana": ficha.get("campana") or "", "duracion_s": f["duracion_s"],
            "historia": f.get("historia") or "", "prueba": f.get("prueba") or "",
            "prueba_nota": ((getattr(r, "plan", None) or {}).get("prueba") or {}).get("nota") or "",
            "nota_edicion": f.get("notas") or "",          # avisos (spoiler, cinemática, mods) de la fila de edición
            "creador": creador, "id_origen": c.id_origen, "claves": _claves_de(c)}


def claves_recientes(raiz, plataforma: str, cuando: datetime, horas: int) -> set[str]:
    """Claves de los clips ya programados en esa red a menos de `horas` de `cuando` (lee el libro de envíos)."""
    cuando = cuando.astimezone(timezone.utc)
    vistas: set[str] = set()
    for e in _libro(raiz):
        if e.get("plataforma") != plataforma or e.get("estado") in ("rechazado", "no_creado"):
            continue
        try:
            due = publicar._a_utc(e["dueAt"])
        except (KeyError, ValueError, TypeError):
            continue
        if abs((due - cuando).total_seconds()) >= horas * 3600:
            continue
        toma = (e.get("datos") or {}).get("toma") or {}
        vistas.update(str(x) for x in toma.get("claves") or [])
        if toma.get("id_origen"):
            vistas.add(str(toma["id_origen"]))
    return vistas


def descarte_definitivo(r) -> bool:
    """Gemini miró el clip y su respuesta no pasó la validación (cinemática, spoiler, frase imposible...).
    Un fallo de red, de cuota o de clave (sin respuesta de Gemini) no lo es: se puede reintentar."""
    return (not r.ok and r.gemini is not None and bool(r.motivos)
            and all(str(m).startswith("ojos:") for m in r.motivos))


def _informe_puerta(carpeta: Path, r) -> Path:
    ruta = carpeta / "informe-puerta.txt"
    lineas = [f"{'OK ' if ok else 'ROJO'} {n}" + (f" ({det})" if det else "") for n, ok, det in r.puntos]
    verde = r.ok and all(ok for _, ok, _ in r.puntos)
    ruta.write_text(f"Puerta de estilo: {'VERDE' if verde else 'ROJO'}\n" + "\n".join(lineas) + "\n",
                    encoding="utf-8")
    return ruta


def _cliente_buffer(raiz, env, transporte, reloj):
    """(cliente, simulado). Sin BUFFER_API_KEY, el ensayo usa respuestas inventadas y una cuota de mentira."""
    from .simulacion import BufferSimulado
    if env.get("BUFFER_API_KEY"):
        return publicar.ClienteBuffer(env["BUFFER_API_KEY"], transporte, raiz, ahora=reloj), False
    return publicar.ClienteBuffer("simulada", BufferSimulado(), tempfile.mkdtemp(prefix="cuota-simulada-"),
                                  ahora=reloj), True


def ensayo_de_carga(raiz, env, toma: Toma, hueco: Hueco, out, transporte_buffer, reloj) -> bool:
    """Hasta `armar_carga` validada en cada plataforma, sin createPost. True si todas son válidas."""
    cfg = cargar_yaml("config/canales.yml", raiz)
    cliente, simulado = _cliente_buffer(raiz, env, transporte_buffer, reloj)
    if simulado:
        out("Buffer: ensayo simulado con respuestas inventadas, pendiente de Kevin (alta de la clave)")
    esquema = cliente.introspeccion()
    cuando = hueco.programar_en.astimezone(timezone.utc)
    todo_ok = True
    for plat in hueco.plataformas:
        try:
            tx = publicar.textos(toma.toma, toma.ficha, plat, raiz, hueco.programar_en.date())
            _, anot = publicar.armar_carga(plat, cfg[plat]["buffer_channel_id"], tx["texto"], tx["titulo"],
                                           "https://ejemplo.invalid/ensayo.mp4", cuando, esquema)
            out(f"ensayo {plat}: carga válida contra el esquema" + (f" ({'; '.join(anot)})" if anot else ""))
        except publicar.Descartada as e:
            out(f"ensayo {plat}: carga NO válida ({e})")
            todo_ok = False
    return todo_ok


def publicar_en_vivo(raiz, env, toma: Toma, hueco: Hueco, out, transporte_buffer, transporte_almacen,
                     reloj, al_resolver=None) -> tuple[list[str], list[str], list[str]]:
    """Sube el MP4, comprueba la URL y programa en cada plataforma, a la hora exacta del hueco (o del relleno).

    Devuelve (programadas, falladas, inciertas). Lanza AlertaRojaCiclo ante canal ajeno, 401/403 o cuenta restringida.

    `al_resolver(plataforma, estado)` se llama en cuanto se conoce el resultado de cada plataforma
    (`programada`, `incierta` o `fallada`), ANTES de intentar la siguiente: así lo ya confirmado se guarda
    aunque una alerta roja corte el resto."""
    falladas, programadas, inciertas = [], [], []

    def resolver(plat, estado):
        lista = {"programada": programadas, "incierta": inciertas, "fallada": falladas}[estado]
        if plat not in lista:
            lista.append(plat)
        if estado == "incierta" and plat not in falladas:
            falladas.append(plat)
        if al_resolver:
            al_resolver(plat, estado)

    def fallo(*plats):
        for p in plats:
            if p not in programadas and p not in falladas:
                resolver(p, "fallada")

    faltan = [s for s in publicar.SECRETOS_BUFFER + publicar.SECRETOS_ALMACEN if not env.get(s)]
    if faltan:
        out("sin " + ", ".join(faltan) + ": no se publica")
        fallo(*hueco.plataformas)
        return programadas, falladas, inciertas
    hoy = reloj().astimezone(ZONA).date()
    destinos = []
    for plat in hueco.plataformas:
        ok, motivo = es_publicable(toma.ficha, hoy, plat, toma.toma.get("pilar"))
        if ok:
            destinos.append(plat)
        else:
            out(f"{plat}: descartada, la ficha no es publicable ({motivo})")
            fallo(plat)
    if not destinos:
        return programadas, falladas, inciertas
    mp4 = toma.resultado.mp4
    ruta, pub = None, None
    try:
        cliente = publicar.ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=reloj)
        canales_api = cliente.todos_los_canales()
        ids = publicar.confirmar_canales(canales_api, cargar_yaml("config/canales.yml", raiz), raiz,
                                         plataformas=destinos, exigir_guardado=True)
        almacen = Almacen(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"],
                          cargar_yaml("config/almacen.yml", raiz), transporte_almacen)
        pub = publicar.Publicador(cliente, almacen, raiz, "vivo", reloj, ids,
                                  orgs=publicar.organizaciones_de(canales_api, ids))
        cuando = hueco.programar_en.astimezone(timezone.utc)
        hz = _cfg_horarios(raiz)
        listos = {}
        for plat in destinos:            # todo se valida ANTES de subir
            try:
                publicar.validar_futuro(cuando, reloj())
                if plat == "shorts":       # A-20261125-1: Buffer gratis, 2 Shorts por canal cada 24 h
                    ok, motivo = shorts.admite(_libro(raiz), toma.ficha, cuando, reloj(), shorts.config(raiz),
                                               lambda pid: pub.estado(pid, pub.orgs.get("shorts")))
                    if not ok:
                        out(f"shorts: no se crea el post ({motivo}); esta toma sale sólo en las otras redes")
                        fallo(plat)
                        continue
                    out(f"shorts: {motivo}")
                claves = set(toma.toma.get("claves") or []) | ({toma.toma["id_origen"]} if toma.toma.get("id_origen") else set())
                choque = claves & claves_recientes(raiz, plat, cuando, hz["no_repetir_horas"])
                if choque:
                    out(f"{plat}: descartada, repetido en {hz['no_repetir_horas']} h ({', '.join(sorted(choque))})")
                    fallo(plat)
                    continue
                pendientes, detalle = pub.cola_pendiente(plat)
                if pendientes >= hz["cola_max"]:
                    out(f"{plat}: cola de Buffer llena ({pendientes}/{hz['cola_max']}): se salta ({detalle})")
                    fallo(plat)
                    continue
                tx = publicar.textos(toma.toma, toma.ficha, plat, raiz, hoy)
                publicar.armar_carga(plat, ids[plat], tx["texto"], tx["titulo"],
                                     "https://ejemplo.invalid/previa.mp4", cuando, pub.esquema)
                listos[plat] = tx
            except publicar.Descartada as e:
                out(f"{plat}: descartada antes de subir ({e})")
                fallo(plat)
        if not listos:
            return programadas, falladas, inciertas
        try:
            with plazo.etapa("subida al almacén"):
                almacen.asegurar_bucket()
                ruta, url = almacen.subir(mp4, toma.toma["numero"], hueco.fecha)
                almacen.comprobar(url)
        except ErrorAlmacen as e:
            out(f"subida fallida: se salta el hueco ({e})")
            fallo(*listos)
            return programadas, falladas, inciertas
        for plat, tx in listos.items():
            try:
                with plazo.etapa(f"buffer {plat}"):
                    r = pub.programar(plat, toma.toma, url, tx, cuando, ruta, toma.ficha)
                out(f"{plat}: programada {r['id']} para {r['dueAt']}")
                resolver(plat, "programada")
            except publicar.ResultadoIncierto as e:
                out(f"{plat}: resultado INCIERTO, no se repite ({e})")
                resolver(plat, "incierta")
            except publicar.Descartada as e:
                if (motivo := _motivo_cuenta(e)):
                    raise AlertaRojaCiclo(motivo) from None
                out(f"{plat}: descartada ({e})")
                fallo(plat)
    except (publicar.AlertaRoja, publicar.ErrorBuffer) as e:
        motivo = _motivo_cuenta(e)
        if motivo:
            raise AlertaRojaCiclo(motivo) from None
        out(f"ERROR de Buffer: {e}")
        fallo(*destinos)
    except (ErrorAlmacen, red.ErrorRed, KeyError, TypeError, ValueError) as e:
        out(f"ERROR: {type(e).__name__}: {e}")
        fallo(*destinos)
    finally:
        if ruta and pub is not None:
            try:
                if pub.soltar_si_huerfano(ruta):
                    out("el video subido no tiene ningún envío vivo: se borró del almacén")
            except (ErrorAlmacen, publicar.ErrorBuffer, red.ErrorRed, OSError, plazo.SinTiempo) as e:
                out(f"AVISO: no se pudo borrar el video huérfano {ruta} ({e})")
    return programadas, falladas, inciertas


def mantenimiento(raiz, env, out, transporte_buffer, transporte_almacen, reloj) -> None:
    """`reconciliar` y `limpiar_enviados` (que ya reconcilia), con los ids de config/canales.yml."""
    faltan = [s for s in publicar.SECRETOS_BUFFER + publicar.SECRETOS_ALMACEN if not env.get(s)]
    if faltan:
        out("mantenimiento: sin " + ", ".join(faltan) + ", se salta")
        return
    cfg = cargar_yaml("config/canales.yml", raiz)
    ids = {p: cfg[p]["buffer_channel_id"] for p in PLATAFORMAS if (cfg.get(p) or {}).get("buffer_channel_id")}
    orgs = {p: cfg[p].get("buffer_org_id") for p in ids}
    try:
        cliente = publicar.ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=reloj)
        almacen = Almacen(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"],
                          cargar_yaml("config/almacen.yml", raiz), transporte_almacen)
        pub = publicar.Publicador(cliente, almacen, raiz, "vivo", reloj, ids, orgs=orgs)
        borradas = pub.limpiar_enviados()
        out(f"mantenimiento: {len(borradas)} video(s) borrado(s) del almacén")
    except (publicar.ErrorBuffer, ErrorAlmacen, red.ErrorRed, OSError) as e:
        motivo = _motivo_cuenta(e)
        if motivo:
            raise AlertaRojaCiclo(motivo) from None
        out(f"mantenimiento: {e}")


def revisar_retiros(raiz, env, out, transporte_buffer, ahora, reloj) -> None:
    """Reclamos y retiros: veta ya, antes de detectar. Sin clave sólo se evalúa la regla de 2 reclamos en 7 días.

    Un fallo de Buffer o de red se anota y el ciclo sigue (401/403 es alerta roja); `AlertaRoja` sube."""
    cliente, ids, org = None, {}, None
    if env.get("BUFFER_API_KEY"):
        cfg = cargar_yaml("config/canales.yml", raiz)
        ids = {p: cfg[p]["buffer_channel_id"] for p in PLATAFORMAS if (cfg.get(p) or {}).get("buffer_channel_id")}
        org = next((cfg[p].get("buffer_org_id") for p in ids if cfg[p].get("buffer_org_id")), None)
        cliente = publicar.ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=reloj)
    if cliente is not None and not plazo.queda(TIEMPO_MIN_RETIROS_S):
        out("retiros: sin tiempo para leer Buffer, sólo se evalúa la regla de 2 reclamos en 7 días")
        cliente = None
    # Plazo propio de la lectura (con tope por llamada) dentro del plazo del ciclo: nunca se come lo de producir.
    limite = max(1.0, min(retiros.PRESUPUESTO_S, plazo.restante() - plazo.MARGEN_S))
    try:
        with plazo.etapa("retiros"):
            retiros.revisar(raiz, cliente, ids, org, ahora, out, secretos=getattr(out, "secretos", ()),
                            limite_s=limite)
    except (publicar.ErrorBuffer, red.ErrorRed, OSError) as e:
        motivo = _motivo_cuenta(e)
        if motivo:
            raise AlertaRojaCiclo(motivo) from None
        out(f"retiros: {e}")


def ejecutar(raiz=None, *, decidir=False, ensayo_forzado=False, ahora=None, salida_dir=None, env=None,
             imprimir=None, detectar_fn=None, hacer_toma_fn=None, transporte_buffer=None,
             transporte_almacen=None, transporte_twitch=None, validar_fn=None, solo_ensayo=False,
             limite_s=None, alarma_s=None, inicio_epoch=None) -> int:
    """Un ciclo. `limite_s` (plazo cooperativo) y `alarma_s` (alarma dura) son opcionales: con None no hay plazo.

    Si se da `inicio_epoch` (la variable INICIO_S del workflow), ambos cuentan desde el inicio del job."""
    out = Salida(imprimir, dict(os.environ if env is None else env))
    plazo.registrar(out)
    plazo.iniciar(limite_s, inicio_epoch=inicio_epoch)
    resto = None
    if alarma_s is not None:
        resto = max(0.001, float(alarma_s) - plazo._ya_pasado(inicio_epoch))
    # Sólo con `inicio_epoch` (el job real): una instalación lenta pudo comerse el plazo antes de empezar.
    if inicio_epoch is not None and not decidir and (
            (resto is not None and resto < TIEMPO_MIN_ARRANQUE_S) or plazo.restante() < TIEMPO_MIN_ARRANQUE_S):
        out(f"sin tiempo al empezar (la instalación tardó {plazo._ya_pasado(inicio_epoch):.0f} s); "
            "no se hace nada")
        plazo.detener()
        plazo.registrar(None)
        return COD_ERROR if ensayo_forzado else COD_OK
    try:
        with plazo.alarma(resto):
            return _ejecutar(raiz, out, decidir=decidir, ensayo_forzado=ensayo_forzado, ahora=ahora,
                             salida_dir=salida_dir, env=env, detectar_fn=detectar_fn, hacer_toma_fn=hacer_toma_fn,
                             transporte_buffer=transporte_buffer, transporte_almacen=transporte_almacen,
                             transporte_twitch=transporte_twitch, validar_fn=validar_fn, solo_ensayo=solo_ensayo)
    finally:
        plazo.detener()
        plazo.registrar(None)


def _ejecutar(raiz, out, *, decidir, ensayo_forzado, ahora, salida_dir, env, detectar_fn, hacer_toma_fn,
              transporte_buffer, transporte_almacen, transporte_twitch, validar_fn, solo_ensayo) -> int:
    raiz = raiz_repo(raiz)
    env = dict(os.environ if env is None else env)
    fijo = ahora is not None
    ahora = _ahora(ahora)
    reloj = (lambda: ahora) if fijo else (lambda: datetime.now(timezone.utc))

    def terminar(codigo, trabajo="no"):
        if decidir:
            out(f"trabajo={trabajo}")
            escribir_salida_github(env, trabajo=trabajo)
        return codigo

    modo_cfg = cargar_yaml("config/modo.yml", raiz)
    modo = "ensayo" if solo_ensayo else modo_cfg.get("modo")      # fuera de main nunca se publica
    if ensayo_forzado and modo != "ensayo":
        out("--ensayo-forzado se niega: config/modo.yml dice "
            f"{modo!r}, no 'ensayo'. No se hace nada.")
        return COD_NEGADO
    # 1) PAUSA: ninguna llamada de red.
    pausada, motivo = esta_pausada(raiz)
    if pausada and not ensayo_forzado:
        out(f"PAUSA: {motivo or 'sin motivo'}")
        return terminar(COD_OK)
    # 2) puerta de configuración.
    informe = (validar_fn or mod_validar.validar)(raiz)
    if informe.errores:
        for e in informe.errores:
            out(f"ERROR de configuración: {e}")
        out("fabrica.validar en rojo: no se publica nada")
        return terminar(COD_ERROR)
    sim = ensayo_forzado
    # 4) tope de Actions (la cadencia, paso 3, se aplica abajo, sólo a las corridas sin hueco).
    if not sim:
        usados = minutos_usados(raiz, ahora, env.get("GITHUB_RUN_ID"))
        if not admite_producir(raiz, ahora, env.get("GITHUB_RUN_ID")):
            out(f"tope diario de Actions ({usados:g} min usados; hacen falta {MINUTOS_PRODUCIR} libres de {tope_minutos(raiz)})")
            return terminar(COD_OK)
    # 5) hueco.
    activas = list(modo_cfg.get("plataformas_activas") or [])
    if sim:
        hueco = Hueco(ahora.astimezone(ZONA) + timedelta(minutes=30), list(activas or PLATAFORMAS))
    else:
        hueco = buscar_hueco(raiz, ahora, activas)
    pendiente = modo == "vivo" and hay_mantenimiento(raiz)
    if hueco is None:
        if not sim and not en_cadencia(ahora):
            out("fuera de cadencia")
            return terminar(COD_OK)
        out("sin hueco")
        if decidir:
            return terminar(COD_OK, "si" if pendiente else "no")
        return _con_alerta(raiz, out, env, ahora, lambda: _solo_mantenimiento(
            raiz, env, out, pendiente, transporte_buffer, transporte_almacen, reloj))
    if decidir:
        out(f"{hueco.descripcion()} en {', '.join(hueco.plataformas)}")
        return terminar(COD_OK, "si")
    out(f"{hueco.descripcion()} en {', '.join(hueco.plataformas)}")
    return _con_alerta(raiz, out, env, ahora, lambda: _producir(
        raiz, env, out, modo, hueco, ahora, reloj, sim, salida_dir, detectar_fn, hacer_toma_fn,
        transporte_buffer, transporte_almacen, transporte_twitch))


def _solo_mantenimiento(raiz, env, out, pendiente, tb, ta, reloj) -> int:
    revisar_retiros(raiz, env, out, tb, reloj(), reloj)
    if pendiente:
        with plazo.etapa("mantenimiento"):
            mantenimiento(raiz, env, out, tb, ta, reloj)
    return COD_OK


def _con_alerta(raiz, out: Salida, env, ahora, fn) -> int:
    """Ejecuta el cuerpo del ciclo; toda alerta roja (y toda fuga de claves) crea PAUSA y alerta.json."""
    codigo = COD_OK
    out.vigilar = True
    motivo = _revisar_retiros(raiz)          # un aviso de retiro frena antes de tocar nada
    if motivo is None:
        try:
            codigo = fn()
        except plazo.SinTiempo as e:      # un SinTiempo residual (p. ej. del mantenimiento): no es alerta roja
            out(f"SIN TIEMPO: {e}. El ciclo termina solo; no se crea PAUSA.")
            codigo = COD_OK
        except AlertaRojaCiclo as e:
            motivo = str(e)
        except publicar.AlertaRoja as e:
            motivo = str(e)
    if motivo is None and (k := out.fuga(env)):
        motivo = f"una clave apareció en el log ({k})"
    if motivo is not None:
        try:
            alerta_roja(raiz, motivo, ahora, out.secretos)
        except plazo.SinTiempo:        # la alarma sonó durante la escritura: ya quedó escrita
            pass
        out(f"ALERTA ROJA: {motivo}. Se creó PAUSA y estado/alerta.json; no se publica nada más.")
        return COD_OK
    return codigo


def _producir(raiz, env, out, modo, hueco, ahora, reloj, sim, salida_dir, detectar_fn, hacer_toma_fn,
              tb, ta, tt) -> int:
    hacer = hacer_toma_fn or mod_toma.hacer_toma
    temporal = Path(tempfile.mkdtemp(prefix="fabrica-ciclo-"))
    carpeta = Path(salida_dir) if salida_dir else (Path(tempfile.gettempdir()) / "fabrica-ensayo" if sim else temporal)
    carpeta.mkdir(parents=True, exist_ok=True)
    hecho = {"gastada": False, "programadas": []}
    toma = None
    try:
        if not sim:
            sumar_intento(raiz, hueco)            # cuenta aunque no salga toma: el relleno tiene tope de intentos
            revisar_retiros(raiz, env, out, tb, ahora, reloj)   # el veto de un reclamo vale ya: antes de detectar
        cliente = mod_detectar.cliente_desde_entorno(tt) if not sim or env.get("TWITCH_CLIENT_ID") else None
        with plazo.etapa("detectar"):
            cands = elegir_candidatos(raiz, ahora, hueco.plataformas, out, detectar_fn, cliente)
        # Después de detectar: las fichas T nuevas ya están en estado/fichas-t.yml.
        fichas = {f.get("id"): f for f in cargar_fuentes(raiz)}
        if not sim:                   # cuarentena: no se llama a `hacer` con fuentes que fallan por red/tiempo/máquina
            libres = []
            for c in cands:
                hasta = _hasta_cuarentena(raiz, c.id_origen, ahora)
                if hasta is not None:
                    out(f"{c.id_origen}: en cuarentena hasta {hasta.isoformat()}")
                else:
                    libres.append(c)
            cands = libres
        horas = _cfg_horarios(raiz)["no_repetir_horas"]
        recientes = {p: claves_recientes(raiz, p, hueco.programar_en, horas) for p in hueco.plataformas}
        i = 0
        for c in cands:
            if i >= MAX_INTENTOS:
                break
            if set(_claves_de(c)) & set().union(*recientes.values()):
                out(f"{c.id_origen}: repetido en {horas} h: se salta")
                continue
            if not plazo.queda(TIEMPO_MIN_INTENTO_S):
                out("sin tiempo para otro intento")
                break
            i += 1
            ficha = fichas.get(c.fuente_id) or {}
            try:
                with plazo.etapa(f"toma, intento {i}"):
                    r = hacer(raiz, c, ficha, ahora, salida=carpeta / f"intento-{i}")
            except plazo.SinTiempo:
                if not sim:               # el intento ya había empezado: cuenta como fallo no-material
                    _sumar_fallo(raiz, c.id_origen, ahora, out)
                raise
            if r.ok:
                toma = Toma(r, _toma_dict(r, c, ficha), ficha, c)
                break
            out(f"intento {i}: {c.id_origen} descartado: " + "; ".join(r.motivos))
            # Regla de gasto: la fuente sólo se anota si el fallo es del MATERIAL; nunca por red, tiempo o máquina.
            if r.gastar or descarte_definitivo(r):
                out("la fuente se gasta (fallo del material)")
                if not sim:
                    puertas.anotar_visto(raiz, c.id_origen)       # no se gasta cuota repitiéndolo
                    _borrar_reintento(raiz, c.id_origen)
            else:
                out("la fuente no se gasta (red, tiempo o máquina)")
                if not sim:
                    _sumar_fallo(raiz, c.id_origen, ahora, out)
        if toma is None:
            if plazo.queda(TIEMPO_MIN_INTENTO_S):
                with plazo.etapa("respaldo"):
                    toma = _respaldo(raiz, ahora, carpeta, fichas, out)
            else:
                out("sin tiempo para el respaldo")
        if toma is None:
            if not sim:
                registro.anotar_registro(raiz, "ciclo: sin toma")
            out("sin toma")
            return COD_ERROR if sim else COD_OK         # el ensayo forzado existe para entregar una toma
        if sim:
            mp4 = toma.resultado.mp4
            if mp4:
                _informe_puerta(carpeta, toma.resultado)
            faltan = [n for n, ruta in (("MP4", mp4), ("plan", mp4 and Path(mp4).with_suffix(".plan.json")),
                                        ("informe de la puerta", carpeta / "informe-puerta.txt"))
                      if not ruta or not Path(ruta).exists()]
            if faltan:
                out("--ensayo-forzado no entregó: " + ", ".join(faltan))
                return COD_ERROR
            out(f"toma {toma.toma['numero']} lista en {mp4}; informe de la puerta en "
                f"{carpeta / 'informe-puerta.txt'}")
        if modo == "ensayo":
            ok = ensayo_de_carga(raiz, env, toma, hueco, out, tb, reloj)
            if not sim:      # el ensayo forzado es una prueba fuera del repo: no deja huella en registro.md
                registro.anotar_registro(raiz, f"ciclo: ensayo, toma {toma.toma['numero']} "
                                               f"({toma.toma['pilar']}) {'validada' if ok else 'con cargas no válidas'}"
                                               f" para {hueco.fecha} {hueco.programar_en.strftime('%H:%M')}; sin createPost")
            return COD_OK if ok else COD_ERROR
        if not plazo.queda(TIEMPO_MIN_PUBLICAR_S):
            out("sin tiempo para publicar: la fuente no se gasta")
            registro.anotar_registro(raiz, "ciclo: sin tiempo, no se publicó")
            return COD_OK

        def al_resolver(plat, estado):
            if estado == "programada":
                hecho["programadas"].append(plat)
            cerrar_hueco(raiz, hueco, plat)               # el hueco de esa plataforma se cierra, salga o falle
            # El número de toma se gasta en cuanto una plataforma quedó programada (o su resultado es incierto,
            # y entonces el post puede existir). Las que fallaron pierden esa toma y ese hueco: la misma toma
            # nunca se reintenta en otra corrida. Se guarda ya, por si una alerta roja corta el resto.
            if estado != "fallada" and not hecho["gastada"]:
                hecho["gastada"] = True
                if toma.candidato is not None:
                    puertas.anotar_visto(raiz, toma.candidato.id_origen)
                    _borrar_reintento(raiz, toma.candidato.id_origen)
                if toma.entrada is not None:
                    informativa.marcar_usada(raiz, toma.entrada["id"])
                registro.avanzar_toma(raiz)
            elif estado != "fallada" and toma.entrada is not None:
                informativa.marcar_usada(raiz, toma.entrada["id"])

        with plazo.etapa("publicar"):
            programadas, falladas, inciertas = publicar_en_vivo(raiz, env, toma, hueco, out, tb, ta, reloj,
                                                                al_resolver)
        registro.anotar_registro(raiz, f"ciclo: toma {toma.toma['numero']} ({toma.toma['pilar']}) programada en "
                                       f"{', '.join(programadas) or 'ninguna'}"
                                       + (f"; falló {', '.join(falladas)}" if falladas else ""))
        if modo == "vivo":
            try:
                with plazo.etapa("mantenimiento"):
                    mantenimiento(raiz, env, out, tb, ta, reloj)
            except plazo.SinTiempo as e:      # ya se publicó: el mantenimiento se retoma en el próximo ciclo
                out(f"mantenimiento: sin tiempo ({e}); se retoma en el próximo ciclo")
        return COD_OK
    except plazo.SinTiempo as e:
        if hecho["gastada"]:
            out(f"SIN TIEMPO: {e}. El ciclo termina solo; lo ya programado queda registrado.")
            if not sim:
                registro.anotar_registro(raiz, f"ciclo: sin tiempo; programada en "
                                               f"{', '.join(hecho['programadas']) or 'ninguna'}")
        else:
            out(f"SIN TIEMPO: {e}. El ciclo termina solo; la fuente no se gasta.")
            if not sim:
                registro.anotar_registro(raiz, "ciclo: sin tiempo, no se publicó")
        return COD_ERROR if sim else COD_OK
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def _respaldo(raiz, ahora, carpeta: Path, fichas, out) -> Toma | None:
    """La Informativa tipográfica: entrada vigente y sin usar, y sin pasar del 10 % de lo publicado en 7 días."""
    entrada = informativa.elegir(raiz, ahora)
    if entrada is None:
        out("respaldo: no hay informativa vigente y sin usar")
        return None
    cabe, motivo = informativa.cabe_en_el_tope(raiz, ahora)
    if not cabe:
        out(f"respaldo: {motivo}")
        return None
    r = informativa.armar(raiz, entrada, ahora, carpeta)
    if not r.ok:
        out("respaldo: la informativa no pasó: " + "; ".join(r.motivos))
        return None
    ficha = fichas.get(informativa.FICHA_ID) or {}
    toma = informativa.toma_dict(entrada, r.fila["toma"])
    toma["claves"] = [f"informativa:{entrada['id']}"]
    return Toma(r, toma, ficha, None, entrada)


def contar_minutos(raiz, ahora: datetime, repo: str | None = None, gh=None, imprimir=print) -> int:
    """Contador diario (fabrica.minutos). Nunca rompe el ciclo: ante cualquier fallo avisa y sale en verde."""
    from . import minutos as mod_minutos
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        imprimir("contar-minutos: falta GITHUB_REPOSITORY; nada que hacer")
        return COD_OK
    try:
        r = mod_minutos.contar_ayer(raiz, ahora, repo, gh or mod_minutos.gh_api)
    except Exception as e:      # noqa: BLE001 - red, permisos o JSON raro: el contador es accesorio
        imprimir(f"contar-minutos: no se pudo ({type(e).__name__}); se reintenta en el próximo ciclo")
        return COD_OK
    if r is None:
        imprimir("contar-minutos: el día anterior ya estaba contado")
        return COD_OK
    imprimir(f"contar-minutos: {r['dia']} = {r['total']} min; mes = {r['mes']} min")
    # Repo público (desde el 20 nov 2026): los minutos no se cobran y una PAUSA sólo frenaría la fábrica.
    # El tope sólo pausa si el workflow dice que el repo es privado (REPO_PRIVADO=true).
    if r["mes"] > mod_minutos.TOPE_MES and os.environ.get("REPO_PRIVADO", "").lower() != "true":
        imprimir(f"contar-minutos: el mes pasa de {mod_minutos.TOPE_MES} min, pero el repo es público: no se pausa")
    elif r["mes"] > mod_minutos.TOPE_MES:
        alerta_roja(raiz, f"Actions pasó de {mod_minutos.TOPE_MES} minutos en el mes ({r['mes']} al {r['dia']})", ahora)
    return COD_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.ciclo", description="Un ciclo de la fábrica.")
    ap.add_argument("--decidir", action="store_true", help="sólo decide si hay trabajo (escribe trabajo=si|no)")
    ap.add_argument("--ensayo-forzado", action="store_true",
                    help="corre el ciclo completo en ensayo aunque exista PAUSA (sólo si modo=ensayo)")
    ap.add_argument("--ahora", help="hora ISO (para pruebas)")
    ap.add_argument("--salida", help="carpeta del MP4, el plan y el informe (con --ensayo-forzado)")
    ap.add_argument("--raiz", default=None)
    ap.add_argument("--sumar-minutos", type=float, help="suma minutos de Actions al total del día y sale")
    ap.add_argument("--marcar-inicio", action="store_true", help="escribe la marca de inicio de `producir` y sale")
    ap.add_argument("--run-id", default=None, help="id de la corrida de Actions (para la marca de inicio)")
    ap.add_argument("--modo-ensayo", action="store_true",
                    help="trata config/modo.yml como ensayo (ramas que no son main: nunca publica)")
    ap.add_argument("--contar-minutos", action="store_true",
                    help="una vez al día cuenta los minutos de Actions de ayer (gh api) y pausa si el mes pasa de 2400")
    ap.add_argument("--abrir-avisos", action="store_true",
                    help="abre un issue por cada envío incierto o abandonado nuevo y sale")
    a = ap.parse_args(argv)
    ahora = datetime.fromisoformat(a.ahora) if a.ahora else None
    if a.sumar_minutos is not None:
        total = sumar_minutos(a.raiz, _ahora(ahora), math.ceil(a.sumar_minutos), a.run_id)
        print(f"minutos de Actions hoy: {total:g}")
        return COD_OK
    if a.marcar_inicio:
        marcar_inicio(a.raiz, _ahora(ahora), a.run_id)
        print("marca de inicio escrita")
        return COD_OK
    if a.contar_minutos:
        return contar_minutos(a.raiz, _ahora(ahora))
    if a.abrir_avisos:
        for clave in abrir_avisos(a.raiz):
            print(f"issue abierto: {clave}")
        return COD_OK
    plazos = {}
    if not a.decidir:          # el plazo sólo vale para `producir`; INICIO_S es el inicio del job (date +%s)
        plazos = {"limite_s": plazo.LIMITE_CICLO_S, "alarma_s": plazo.ALARMA_S}
        try:
            plazos["inicio_epoch"] = float(os.environ["INICIO_S"])
        except (KeyError, ValueError):
            pass
    return ejecutar(a.raiz, decidir=a.decidir, ensayo_forzado=a.ensayo_forzado, ahora=ahora,
                    salida_dir=a.salida, solo_ensayo=a.modo_ensayo, **plazos)


if __name__ == "__main__":
    sys.exit(main())

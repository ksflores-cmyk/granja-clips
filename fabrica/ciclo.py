"""El ciclo de la fábrica: detectar, tomar, subir y programar. Corre cada hora en GitHub Actions.

`python -m fabrica.ciclo [--decidir] [--ensayo-forzado] [--ahora ISO] [--salida CARPETA]`.
El orden de los pasos no se cambia: 1) PAUSA, 2) puerta de configuración, 3) cadencia, 4) tope de Actions,
5) hueco, 6) candidatos, 7) respaldo informativo, 8) publicar (vivo) o ensayo, 9) --ensayo-forzado, 10) alerta roja.

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
from . import informativa, publicar, puertas, red, registro
from . import validar as mod_validar
from .almacen import Almacen, ErrorAlmacen
from .config import cargar_yaml, raiz_repo
from .editar import toma as mod_toma
from .fuentes import cargar_fuentes, es_publicable
from .pausa import crear_pausa, esta_pausada
from .registro import ZONA

PLATAFORMAS = publicar.PLATAFORMAS
SECRETOS = ("BUFFER_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TWITCH_CLIENT_ID",
            "TWITCH_CLIENT_SECRET", "GEMINI_API_KEY")
DIA_CADENCIA_LENTA = date(2026, 11, 21)      # desde este día (GT) sólo corre en horas múltiplo de 3
TOPE_MINUTOS_DIA = 60
MARGEN_MIN_S, MARGEN_MAX_S = 15 * 60, 75 * 60    # el hueco debe caer de ahora+15 a ahora+75 min
MAX_INTENTOS = 3
RE_CUENTA = re.compile(r"suspend|restrict|bloquead|blocked|disabled|deshabilitad", re.IGNORECASE)
RE_HTTP_CUENTA = re.compile(r"código (401|403)\b")
ESTADOS_AVISO = ("incierto", "abandonado")
COD_OK, COD_ERROR, COD_NEGADO = 0, 1, 2


# ------------------------------------------------------------------ utilidades

class Salida:
    """Imprime cada línea sin claves y guarda el texto crudo para buscar fugas."""

    def __init__(self, imprimir=print, env=None):
        self.imprimir = imprimir
        self.secretos = [v for k in SECRETOS if len(v := (env or {}).get(k, "")) >= 8]
        self.lineas: list[str] = []

    def __call__(self, texto) -> None:
        texto = str(texto)
        self.lineas.append(texto)
        self.imprimir(red.ocultar(texto, self.secretos))

    def fuga(self, env) -> str | None:
        """Nombre de la variable cuya clave apareció en lo que se imprimió, o None."""
        todo = "\n".join(self.lineas)
        for k in SECRETOS:
            v = (env or {}).get(k, "")
            if len(v) >= 8 and v in todo:
                return k
        return None


class AlertaRojaCiclo(Exception):
    """Algo que obliga a parar: se crea PAUSA y estado/alerta.json."""


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


def escribir_salida_github(env, **pares) -> None:
    ruta = (env or {}).get("GITHUB_OUTPUT")
    if ruta:
        with open(ruta, "a", encoding="utf-8") as f:
            for k, v in pares.items():
                f.write(f"{k}={v}\n")


# ------------------------------------------------------------------ pasos 3 y 4: cadencia y tope

def en_cadencia(ahora: datetime) -> bool:
    """Del 18 al 20 de nov (GT), cada hora; desde el 21, sólo en las horas múltiplo de 3."""
    local = ahora.astimezone(ZONA)
    return local.date() < DIA_CADENCIA_LENTA or local.hour % 3 == 0


def _ruta_minutos(raiz) -> Path:
    return raiz_repo(raiz) / "estado" / "actions-minutos.json"


def minutos_del_dia(raiz, ahora: datetime) -> float:
    datos = _leer_json(_ruta_minutos(raiz), {})
    dia = ahora.astimezone(ZONA).date().isoformat()
    return float(datos.get(dia, 0)) if isinstance(datos, dict) else 0.0


def sumar_minutos(raiz, ahora: datetime, minutos: float) -> float:
    """Suma los minutos de una corrida al total del día (GT) y borra los días de más de 14 días."""
    datos = _leer_json(_ruta_minutos(raiz), {})
    datos = datos if isinstance(datos, dict) else {}
    dia = ahora.astimezone(ZONA).date()
    datos[dia.isoformat()] = round(float(datos.get(dia.isoformat(), 0)) + float(minutos), 2)
    corte = (dia - timedelta(days=14)).isoformat()
    datos = {k: v for k, v in datos.items() if k >= corte}
    _escribir_json(_ruta_minutos(raiz), datos)
    return datos[dia.isoformat()]


# ------------------------------------------------------------------ paso 5: hueco

@dataclass
class Hueco:
    cuando: datetime                  # hora local de Guatemala
    plataformas: list[str]

    @property
    def fecha(self) -> str:
        return self.cuando.date().isoformat()

    @property
    def hora(self) -> str:
        return self.cuando.strftime("%H:%M")


def _en_estreno(t: dict, cuando: datetime) -> bool:
    v = t["ventana_estreno"]
    return datetime.fromisoformat(str(v["inicio"])) <= cuando <= datetime.fromisoformat(str(v["fin"]))


def buscar_hueco(raiz, ahora: datetime, activas: list[str]) -> Hueco | None:
    """El primer hueco de horarios.yml entre ahora+15 y ahora+75 min con alguna plataforma sin fila."""
    if not activas:
        return None
    horarios = cargar_yaml("config/horarios.yml", raiz)
    t = cargar_yaml("temporada.yml", raiz)
    local = ahora.astimezone(ZONA)
    filas = puertas._filas_marcador(raiz)
    ocupados = {(f.get("fecha"), f.get("hora"), f.get("plataforma")) for f in filas}
    candidatos = []
    for dia in (local.date(), local.date() + timedelta(days=1)):
        for lista in ("huecos_normales", "huecos_estreno"):
            for hhmm in horarios.get(lista) or []:
                h, m = (int(x) for x in str(hhmm).split(":"))
                cuando = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=ZONA)
                if (lista == "huecos_estreno") != _en_estreno(t, cuando):
                    continue          # los de estreno sólo dentro de la ventana; los normales, fuera
                seg = (cuando - local).total_seconds()
                if MARGEN_MIN_S <= seg <= MARGEN_MAX_S:
                    candidatos.append(cuando)
    for cuando in sorted(set(candidatos)):
        libres = [p for p in activas if (cuando.date().isoformat(), cuando.strftime("%H:%M"), p) not in ocupados]
        if libres:
            return Hueco(cuando, libres)
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


def ordenar(candidatos: list, temporada: dict) -> list:
    """Primero los pilares con más peso; a igual pilar, el más reciente."""
    sin_fecha = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(candidatos, key=lambda c: (-_peso(temporada, c.pilar),
                                             -(c.creado_en or sin_fecha).timestamp()))


def elegir_candidatos(raiz, ahora, plataformas, out, detectar_fn=None, cliente=None) -> list:
    t = cargar_yaml("temporada.yml", raiz)
    fichas = {f.get("id"): f for f in cargar_fuentes(raiz)}
    try:
        cands, saltos = (detectar_fn or mod_detectar.detectar_con_motivos)(raiz, ahora, cliente)
    except Exception as e:  # noqa: BLE001 (un fallo del detector no debe tumbar el ciclo)
        out(f"detectar: {type(e).__name__}: {e}")
        return []
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
    return ordenar(aprobados or [], t)


# ------------------------------------------------------------------ el ciclo

@dataclass
class Toma:
    resultado: mod_toma.Resultado
    toma: dict                    # lo que espera publicar.textos
    ficha: dict
    candidato: object | None = None
    entrada: dict | None = None   # entrada de informativas.yml, si es el respaldo


def _toma_dict(r, c, ficha: dict) -> dict:
    f = r.fila
    return {"numero": f["toma"], "gancho": f["gancho"], "pilar": f["pilar"], "fuente_id": ficha.get("id"),
            "campana": ficha.get("campana") or "", "duracion_s": f["duracion_s"],
            "creador": c.creador or ficha.get("creador")}


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
    cuando = hueco.cuando.astimezone(timezone.utc)
    todo_ok = True
    for plat in hueco.plataformas:
        try:
            tx = publicar.textos(toma.toma, toma.ficha, plat, raiz, hueco.cuando.date())
            _, anot = publicar.armar_carga(plat, cfg[plat]["buffer_channel_id"], tx["texto"], tx["titulo"],
                                           "https://ejemplo.invalid/ensayo.mp4", cuando, esquema)
            out(f"ensayo {plat}: carga válida contra el esquema" + (f" ({'; '.join(anot)})" if anot else ""))
        except publicar.Descartada as e:
            out(f"ensayo {plat}: carga NO válida ({e})")
            todo_ok = False
    return todo_ok


def publicar_en_vivo(raiz, env, toma: Toma, hueco: Hueco, out, transporte_buffer, transporte_almacen,
                     reloj) -> tuple[list[str], list[str]]:
    """Sube el MP4, comprueba la URL y programa en cada plataforma, a la hora exacta del hueco.

    Devuelve (programadas, falladas). Lanza AlertaRojaCiclo ante canal ajeno, 401/403 o cuenta restringida."""
    faltan = [s for s in publicar.SECRETOS_BUFFER + publicar.SECRETOS_ALMACEN if not env.get(s)]
    if faltan:
        out("sin " + ", ".join(faltan) + ": no se publica")
        return [], list(hueco.plataformas)
    hoy = reloj().astimezone(ZONA).date()
    destinos, falladas = [], []
    for plat in hueco.plataformas:
        ok, motivo = es_publicable(toma.ficha, hoy, plat, toma.toma.get("pilar"))
        if ok:
            destinos.append(plat)
        else:
            out(f"{plat}: descartada, la ficha no es publicable ({motivo})")
            falladas.append(plat)
    if not destinos:
        return [], falladas
    mp4 = toma.resultado.mp4
    ruta, pub, programadas = None, None, []
    try:
        cliente = publicar.ClienteBuffer(env["BUFFER_API_KEY"], transporte_buffer, raiz, ahora=reloj)
        canales_api = cliente.todos_los_canales()
        ids = publicar.confirmar_canales(canales_api, cargar_yaml("config/canales.yml", raiz), raiz,
                                         plataformas=destinos, exigir_guardado=True)
        almacen = Almacen(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"],
                          cargar_yaml("config/almacen.yml", raiz), transporte_almacen)
        pub = publicar.Publicador(cliente, almacen, raiz, "vivo", reloj, ids,
                                  orgs=publicar.organizaciones_de(canales_api, ids))
        cuando = publicar.hueco_a_utc(date.fromisoformat(hueco.fecha), hueco.hora,
                                      cargar_yaml("config/horarios.yml", raiz)["zona"])
        listos = {}
        for plat in destinos:            # todo se valida ANTES de subir
            try:
                publicar.validar_futuro(cuando, reloj())
                tx = publicar.textos(toma.toma, toma.ficha, plat, raiz, hoy)
                publicar.armar_carga(plat, ids[plat], tx["texto"], tx["titulo"],
                                     "https://ejemplo.invalid/previa.mp4", cuando, pub.esquema)
                listos[plat] = tx
            except publicar.Descartada as e:
                out(f"{plat}: descartada antes de subir ({e})")
                falladas.append(plat)
        if not listos:
            return [], falladas
        almacen.asegurar_bucket()
        try:
            ruta, url = almacen.subir(mp4, toma.toma["numero"], hueco.fecha)
            almacen.comprobar(url)
        except ErrorAlmacen as e:
            out(f"subida fallida: se salta el hueco ({e})")
            return [], falladas + list(listos)
        for plat, tx in listos.items():
            try:
                r = pub.programar(plat, toma.toma, url, tx, cuando, ruta, toma.ficha)
                out(f"{plat}: programada {r['id']} para {r['dueAt']}")
                programadas.append(plat)
            except publicar.ResultadoIncierto as e:
                out(f"{plat}: resultado INCIERTO, no se repite ({e})")
                falladas.append(plat)
            except publicar.Descartada as e:
                if (motivo := _motivo_cuenta(e)):
                    raise AlertaRojaCiclo(motivo) from None
                out(f"{plat}: descartada ({e})")
                falladas.append(plat)
    except (publicar.AlertaRoja, publicar.ErrorBuffer) as e:
        motivo = _motivo_cuenta(e)
        if motivo:
            raise AlertaRojaCiclo(motivo) from None
        out(f"ERROR de Buffer: {e}")
        falladas += [p for p in destinos if p not in programadas and p not in falladas]
    except (ErrorAlmacen, red.ErrorRed, KeyError, TypeError, ValueError) as e:
        out(f"ERROR: {type(e).__name__}: {e}")
        falladas += [p for p in destinos if p not in programadas and p not in falladas]
    finally:
        if ruta and pub is not None:
            try:
                if pub.soltar_si_huerfano(ruta):
                    out("el video subido no tiene ningún envío vivo: se borró del almacén")
            except (ErrorAlmacen, publicar.ErrorBuffer, red.ErrorRed, OSError) as e:
                out(f"AVISO: no se pudo borrar el video huérfano {ruta} ({e})")
    return programadas, falladas


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


def ejecutar(raiz=None, *, decidir=False, ensayo_forzado=False, ahora=None, salida_dir=None, env=None,
             imprimir=print, detectar_fn=None, hacer_toma_fn=None, transporte_buffer=None,
             transporte_almacen=None, transporte_twitch=None, validar_fn=None) -> int:
    raiz = raiz_repo(raiz)
    env = dict(os.environ if env is None else env)
    out = Salida(imprimir, env)
    fijo = ahora is not None
    ahora = _ahora(ahora)
    reloj = (lambda: ahora) if fijo else (lambda: datetime.now(timezone.utc))

    def terminar(codigo, trabajo="no"):
        if decidir:
            out(f"trabajo={trabajo}")
            escribir_salida_github(env, trabajo=trabajo)
        return codigo

    modo_cfg = cargar_yaml("config/modo.yml", raiz)
    modo = modo_cfg.get("modo")
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
    # 3) cadencia y 4) tope de Actions (no aplican al ensayo forzado: es una prueba a mano).
    if not sim:
        if not en_cadencia(ahora):
            out("fuera de cadencia")
            return terminar(COD_OK)
        usados = minutos_del_dia(raiz, ahora)
        if usados >= TOPE_MINUTOS_DIA:
            out(f"tope diario de Actions ({usados:g} min)")
            return terminar(COD_OK)
    # 5) hueco.
    activas = list(modo_cfg.get("plataformas_activas") or [])
    if sim:
        hueco = Hueco(ahora.astimezone(ZONA) + timedelta(minutes=30), list(activas or PLATAFORMAS))
    else:
        hueco = buscar_hueco(raiz, ahora, activas)
    pendiente = modo == "vivo" and hay_mantenimiento(raiz)
    if hueco is None:
        out("sin hueco")
        if decidir:
            return terminar(COD_OK, "si" if pendiente else "no")
        return _con_alerta(raiz, out, env, ahora, lambda: _solo_mantenimiento(
            raiz, env, out, pendiente, transporte_buffer, transporte_almacen, reloj))
    if decidir:
        out(f"hueco {hueco.fecha} {hueco.hora} en {', '.join(hueco.plataformas)}")
        return terminar(COD_OK, "si")
    return _con_alerta(raiz, out, env, ahora, lambda: _producir(
        raiz, env, out, modo, hueco, ahora, reloj, sim, salida_dir, detectar_fn, hacer_toma_fn,
        transporte_buffer, transporte_almacen, transporte_twitch))


def _solo_mantenimiento(raiz, env, out, pendiente, tb, ta, reloj) -> int:
    if pendiente:
        mantenimiento(raiz, env, out, tb, ta, reloj)
    return COD_OK


def _con_alerta(raiz, out: Salida, env, ahora, fn) -> int:
    """Ejecuta el cuerpo del ciclo; toda alerta roja (y toda fuga de claves) crea PAUSA y alerta.json."""
    codigo = COD_OK
    motivo = _revisar_retiros(raiz)          # un aviso de retiro frena antes de tocar nada
    if motivo is None:
        try:
            codigo = fn()
        except AlertaRojaCiclo as e:
            motivo = str(e)
        except publicar.AlertaRoja as e:
            motivo = str(e)
    if motivo is None and (k := out.fuga(env)):
        motivo = f"una clave apareció en el log ({k})"
    if motivo is not None:
        alerta_roja(raiz, motivo, ahora, out.secretos)
        out(f"ALERTA ROJA: {motivo}. Se creó PAUSA y estado/alerta.json; no se publica nada más.")
        return COD_OK
    return codigo


def _producir(raiz, env, out, modo, hueco, ahora, reloj, sim, salida_dir, detectar_fn, hacer_toma_fn,
              tb, ta, tt) -> int:
    fichas = {f.get("id"): f for f in cargar_fuentes(raiz)}
    hacer = hacer_toma_fn or mod_toma.hacer_toma
    temporal = Path(tempfile.mkdtemp(prefix="fabrica-ciclo-"))
    carpeta = Path(salida_dir) if salida_dir else (Path(tempfile.gettempdir()) / "fabrica-ensayo" if sim else temporal)
    carpeta.mkdir(parents=True, exist_ok=True)
    try:
        cliente = mod_detectar.cliente_desde_entorno(tt) if not sim or env.get("TWITCH_CLIENT_ID") else None
        toma = None
        cands = elegir_candidatos(raiz, ahora, hueco.plataformas, out, detectar_fn, cliente)
        for i, c in enumerate(cands[:MAX_INTENTOS], 1):
            ficha = fichas.get(c.fuente_id) or {}
            r = hacer(raiz, c, ficha, ahora, salida=carpeta / f"intento-{i}")
            if r.ok:
                toma = Toma(r, _toma_dict(r, c, ficha), ficha, c)
                break
            out(f"intento {i}: {c.id_origen} descartado: " + "; ".join(r.motivos))
        if toma is None:
            toma = _respaldo(raiz, ahora, carpeta, fichas, out)
        if toma is None:
            if not sim:
                registro.anotar_registro(raiz, "ciclo: sin toma")
            out("sin toma")
            return COD_OK
        if sim and toma.resultado.mp4:
            _informe_puerta(carpeta, toma.resultado)
            out(f"toma {toma.toma['numero']} lista en {toma.resultado.mp4}; informe de la puerta en "
                f"{carpeta / 'informe-puerta.txt'}")
        if modo == "ensayo":
            ok = ensayo_de_carga(raiz, env, toma, hueco, out, tb, reloj)
            if not sim:      # el ensayo forzado es una prueba fuera del repo: no deja huella en registro.md
                registro.anotar_registro(raiz, f"ciclo: ensayo, toma {toma.toma['numero']} "
                                               f"({toma.toma['pilar']}) {'validada' if ok else 'con cargas no válidas'}"
                                               f" para {hueco.fecha} {hueco.hora}; sin createPost")
            return COD_OK if ok else COD_ERROR
        programadas, falladas = publicar_en_vivo(raiz, env, toma, hueco, out, tb, ta, reloj)
        if programadas:
            if toma.candidato is not None:
                puertas.anotar_visto(raiz, toma.candidato.id_origen)
            if toma.entrada is not None:
                informativa.marcar_usada(raiz, toma.entrada["id"])
        if programadas and not falladas:
            registro.avanzar_toma(raiz)
        registro.anotar_registro(raiz, f"ciclo: toma {toma.toma['numero']} ({toma.toma['pilar']}) programada en "
                                       f"{', '.join(programadas) or 'ninguna'}"
                                       + (f"; falló {', '.join(falladas)}" if falladas else ""))
        if modo == "vivo":
            mantenimiento(raiz, env, out, tb, ta, reloj)
        return COD_OK
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
    return Toma(r, informativa.toma_dict(entrada, r.fila["toma"]), ficha, None, entrada)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.ciclo", description="Un ciclo de la fábrica.")
    ap.add_argument("--decidir", action="store_true", help="sólo decide si hay trabajo (escribe trabajo=si|no)")
    ap.add_argument("--ensayo-forzado", action="store_true",
                    help="corre el ciclo completo en ensayo aunque exista PAUSA (sólo si modo=ensayo)")
    ap.add_argument("--ahora", help="hora ISO (para pruebas)")
    ap.add_argument("--salida", help="carpeta del MP4, el plan y el informe (con --ensayo-forzado)")
    ap.add_argument("--raiz", default=None)
    ap.add_argument("--sumar-minutos", type=float, help="suma minutos de Actions al total del día y sale")
    ap.add_argument("--abrir-avisos", action="store_true",
                    help="abre un issue por cada envío incierto o abandonado nuevo y sale")
    a = ap.parse_args(argv)
    ahora = datetime.fromisoformat(a.ahora) if a.ahora else None
    if a.sumar_minutos is not None:
        total = sumar_minutos(a.raiz, _ahora(ahora), math.ceil(a.sumar_minutos))
        print(f"minutos de Actions hoy: {total:g}")
        return COD_OK
    if a.abrir_avisos:
        for clave in abrir_avisos(a.raiz):
            print(f"issue abierto: {clave}")
        return COD_OK
    return ejecutar(a.raiz, decidir=a.decidir, ensayo_forzado=a.ensayo_forzado, ahora=ahora,
                    salida_dir=a.salida)


if __name__ == "__main__":
    sys.exit(main())

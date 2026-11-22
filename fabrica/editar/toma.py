"""Editor de punta a punta: `hacer_toma` descarga, mira, valida, compone y pasa la puerta técnica y la de estilo.

Nunca publica ni sube el contador de `marca.yml`: usa el número actual. Cualquier NO es un descarte con
todos sus motivos. La puerta de estilo corre mientras el video de origen sigue en la carpeta temporal
(compara el cuerpo con la fuente); el plan guarda su `sha256`.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .. import descargar as mod_descargar
from .. import fuentes, ojos, plazo, puertas, retencion
from .. import voces as mod_voces
from ..config import cargar_yaml, raiz_repo
from ..registro import ZONA, contador_toma
from . import criticos as mod_criticos, estilo, historia as mod_historia, musica as mod_musica, nombres, sfx as mod_sfx, sin_ia, tecnica
from .componer import Linea, componer, sondear_video
from .lienzo import Toma
from .marca import Marca

SIN_IA = "sin-ia"          # `Resultado.modelo` cuando el corte no pasó por Gemini
PLAZO_MIN_HISTORIA_S = 180   # la historia sólo se intenta si al ciclo le quedan al menos estos segundos
MIN_LINEA_S = 0.3        # una línea más corta que esto tras ajustarla al recorte se descarta
INICIO_ACOTACION_S = 0.8


@dataclass
class Resultado:
    ok: bool
    motivos: list[str] = field(default_factory=list)
    mp4: Path | None = None
    plan: dict | None = None
    gemini: dict | None = None          # la respuesta cruda de Gemini (para informes)
    modelo: str | None = None
    puntos: list = field(default_factory=list)   # (punto, ok, detalle) de la puerta de estilo
    fila: dict | None = None            # fila futura del marcador: N5 la pasa a registro.anotar_clip
    gastar: bool = False                # el fallo es del MATERIAL: la fuente se anota en estado/vistos.txt


def _nombre_seguro(id_origen: str) -> str:
    """El id del origen como parte de un nombre de archivo. Una URL (material del brief) no sirve tal cual."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,60}", str(id_origen)):
        return str(id_origen)
    return hashlib.sha256(str(id_origen).encode("utf-8")).hexdigest()[:10]


def _ffmpeg_ligero(ruta, destino: Path, escala: int, fps: int, crf: int, audio: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(ruta), "-vf",
                    f"scale=-2:{escala},fps={fps}", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio, "-ac", "1", "-movflags", "+faststart",
                    str(destino)],
                   check=True, capture_output=True, timeout=plazo.tiempo_para(180))


def copia_para_ojos(ruta: Path, raiz, carpeta: Path) -> Path:
    """Si el video pesa más de lo que Gemini acepta en línea (`max_mb_en_linea`), una copia ligera sólo para
    que Gemini lo mire (360p, 15 fps, audio de 48 kb/s). Si aun así pesa más del 90 % del tope, una segunda
    pasada más ligera (240p, 12 fps, crf 38, audio de 32 kb/s). Los tiempos son los mismos; el MP4 final sale
    de la fuente."""
    tope = float(cargar_yaml("config/ojos.yml", raiz)["max_mb_en_linea"]) * 1024 * 1024
    if Path(ruta).stat().st_size <= tope * 0.9:       # el base64 infla un tercio y ya hay un margen de prompt
        return Path(ruta)
    destino = Path(carpeta) / "para-ojos.mp4"
    _ffmpeg_ligero(ruta, destino, 360, 15, 34, "48k")
    if destino.stat().st_size > tope * 0.9:
        segunda = Path(carpeta) / "para-ojos-2.mp4"
        _ffmpeg_ligero(ruta, segunda, 240, 12, 38, "32k")
        segunda.replace(destino)
    return destino


def lapiz_de(numero: int) -> str:
    """Alterna: número impar, círculo; par, subrayado (como las muestras: 1 y 3 círculo, 2 subrayado)."""
    return "círculo" if numero % 2 else "subrayado"


def armar(datos: dict, marca: Marca, ficha: dict, numero: int,
          creador: str | None = None, temporada: dict | None = None, raiz=None) -> tuple[Toma, list[Linea], float, float]:
    """(Toma, líneas relativas al recorte, inicio, duración) a partir de una respuesta ya validada.

    `creador`: el creador recortado cuando la ficha lo trae por archivo (campañas); si no, el de la ficha.
    Los nombres salen de la ficha, no del modelo: `STREAMER` o el creador -> su nombre; otro hablante ->
    `OTRA VOZ`; una escena `INT. STREAM` o con un nombre vetado -> `sin_ia.escena_de`."""
    creador = nombres.creador_de(creador, ficha)
    ini, fin = float(datos["recorte"]["inicio"]), float(datos["recorte"]["fin"])
    dur = round(fin - ini, 3)
    toma = Toma(numero=numero, gancho=datos["gancho"].strip(), palabra_lapiz=datos["palabra_lapiz"].strip(),
                lapiz=lapiz_de(numero),
                escena=nombres.escena_segura(datos["escena"], creador, marca, temporada, raiz),
                credito=f"Con: {creador}")
    tope = dur - float(marca.m("cierre_s"))      # el último medio segundo es el cierre
    lineas: list[Linea] = []
    for ln in ojos.partir_lineas(datos, marca):
        t0, t1 = max(0.0, round(ln["t0"] - ini, 3)), min(tope, round(ln["t1"] - ini, 3))
        if t1 - t0 < MIN_LINEA_S:
            continue
        lineas.append(Linea(t0=t0, t1=t1, nombre=nombres.etiqueta_hablante(ln["hablante"], creador), frase=ln["es"].strip(),
                            original=ln["original"]))
    acot = datos["acotacion"].strip()
    if acot:
        if lineas:
            lineas[0].acotacion = acot
        else:
            lineas.append(Linea(t0=INICIO_ACOTACION_S, t1=max(INICIO_ACOTACION_S + MIN_LINEA_S, tope - 0.2),
                                acotacion=acot))
    return toma, lineas, ini, dur


def lineas_con_voces(res, lineas: list[Linea], modo: str) -> tuple[list[Linea], str]:
    """Líneas de la toma en el tiempo de la toma (ya sin los silencios cortados) y de dónde salen ("voces"|"gemini").

    Con la transcripción en español: reemplaza a las de Gemini (modo principal) o las rellena si no había
    (respaldo, o corte sin IA); la acotación de Gemini la pone `_etapa_voces` en la primera línea que sobreviva. Si no
    reemplaza, las de Gemini siguen, con su voz asignada por solape con los turnos y sus tiempos remapeados."""
    hay_frases = any(ln.frase for ln in lineas)
    if res.es_espanol and res.lineas and (modo == "principal" or not hay_frases):
        nuevas = [Linea(t0=ln["t0"], t1=ln["t1"], nombre=ln["hablante"], frase=ln["es"], original=ln["original"],
                        voz=ln["voz"]) for ln in res.lineas]
        return nuevas, "voces"
    return lineas_de_gemini(res, lineas), "gemini"


def lineas_de_gemini(res, lineas: list[Linea]) -> list[Linea]:
    """Las líneas de Gemini (tiempos relativos al recorte) con su voz por solape y los tiempos de la toma."""
    voz = mod_voces.asignar_voz(lineas, res.turnos)
    return [Linea(t0=round(mod_voces.a_toma(ln.t0, res.tramos), 3), t1=round(mod_voces.a_toma(ln.t1, res.tramos), 3),
                  nombre=ln.nombre, frase=ln.frase, acotacion=ln.acotacion, original=ln.original,
                  voz=v if ln.frase and res.turnos else None) for ln, v in zip(lineas, voz)]


def ajustar_a_toma(lineas: list[Linea], dur: float, marca: Marca) -> list[Linea]:
    """Recorta las líneas al tiempo útil de la toma (antes del cierre) y quita las que quedan demasiado cortas."""
    tope = dur - float(marca.m("cierre_s"))
    salida = []
    for ln in lineas:
        ln.t1 = min(ln.t1, round(tope, 3))
        if ln.t1 - ln.t0 >= MIN_LINEA_S:
            salida.append(ln)
    return salida


@dataclass
class EtapaVoces:
    """Lo que la etapa de voces decide para la toma."""
    lineas: list
    dur: float
    tramos: list | None = None
    info: dict | None = None             # va a plan["voces"]
    transcripcion: dict | None = None    # va a plan["transcripcion"]
    alterno: tuple | None = None         # (líneas de Gemini, duración, transcripción) sin cortes, por si compone mal


def _transcripcion_plan(res) -> dict | None:
    return ({"motor": "faster-whisper", "modelo": res.modelo, "idioma": res.idioma, "palabras": res.palabras}
            if res.modelo else None)


def _info_voces(res, cfg, fuente: str) -> dict:
    info = {"motor": "faster-whisper", "modelo": res.modelo, "idioma": res.idioma, "segundos": res.segundos,
            "modo": cfg.modo, "fuente_lineas": fuente, "hablantes": res.hablantes,
            "cortes": [[round(a, 3), round(b, 3)] for a, b in res.cortes], "golpes": res.golpes,
            "descartados": res.descartados, "hablantes_diagnostico": res.diagnostico}
    if res.motivo:
        info["motivo"] = res.motivo
    return info


def _etapa_voces(raiz, marca: Marca, video, inicio: float, dur: float, lineas: list[Linea], datos: dict,
                 nombre_streamer: str | None, minimo_s: float, transcriptor=None) -> EtapaVoces:
    """Analiza el audio (transcripción, voces, silencios, golpes) y decide líneas, duración y tramos de la toma.
    Nunca rompe la toma: sin voces, todo sigue igual y `info` trae el `motivo`."""
    cfg = mod_voces.Cfg.cargar(raiz)
    if cfg is None or not cfg.activo:
        return EtapaVoces(lineas, dur)
    if not plazo.queda(cfg.tope_s + cfg.reserva_s):          # además del tope, lo que hace falta para componer y la puerta
        plazo.log(f"voces: quedan menos de {cfg.tope_s + cfg.reserva_s:g} s del ciclo; la toma sigue sin voces")
        return EtapaVoces(lineas, dur, info={"motivo": "sin tiempo del ciclo para las voces"})
    try:
        with plazo.etapa("voces"):
            res = mod_voces.analizar(video, inicio, dur, marca, cfg, minimo_s=minimo_s,
                                     nombre_streamer=nombre_streamer, transcriptor=transcriptor)
    except plazo.SinTiempo:
        raise
    except Exception as e:  # noqa: BLE001 (las voces nunca rompen la toma)
        return EtapaVoces(lineas, dur, info={"motivo": f"voces: {type(e).__name__}: {str(e)[:160]}"})
    if res.hay_cortes and res.duracion_toma < minimo_s - 1e-6:
        res = res.sin_cortes()                           # nunca por debajo del mínimo de la ficha
    acot = next((ln.acotacion for ln in lineas if ln.acotacion), None)
    nuevas, fuente = lineas_con_voces(res, lineas, cfg.modo)
    dur_final = round(res.duracion_toma, 3)
    nuevas = ajustar_a_toma(nuevas, dur_final, marca)
    if fuente == "voces" and not nuevas:                 # ninguna línea de la transcripción cabe: las de Gemini
        fuente, nuevas = "gemini", ajustar_a_toma(lineas_de_gemini(res, lineas), dur_final, marca)
    if fuente == "voces" and acot and nuevas:            # la acotación de Gemini, en la primera línea que sobrevive
        nuevas[0].acotacion = acot
    alterno = None
    if fuente == "voces" or res.hay_cortes:
        liso = res.sin_cortes()
        alterno = (ajustar_a_toma(lineas_de_gemini(liso, lineas), dur, marca), dur, _transcripcion_plan(liso),
                   liso.golpes)
    return EtapaVoces(nuevas, dur_final, res.tramos if res.hay_cortes else None, _info_voces(res, cfg, fuente),
                      _transcripcion_plan(res), alterno)


def fila_marcador(candidato, ficha: dict, toma: Toma, dur: float, ahora: datetime, prueba: str = "") -> dict:
    """Fila futura del marcador (sin id_clip ni notas: `registro.anotar_clip` los pone).
    `prueba`: brazo de la prueba de retención (`corta-10-14:corta`); vacío si no aplica."""
    local = ahora.astimezone(ZONA)
    return {"prueba": prueba, "toma": toma.numero, "fecha": local.date().isoformat(), "hora": local.strftime("%H:%M"),
            "pilar": candidato.pilar or "", "fuente_id": ficha.get("id"), "campana": ficha.get("campana"),
            "gancho": toma.gancho, "duracion_s": round(dur, 2), "credito": toma.credito}


def hacer_toma(raiz, candidato, ficha: dict | None, ahora: datetime, *, salida: Path | None = None,
               abrir=None, ejecutor=None, transporte=None, espera=time.sleep, entorno=None,
               transporte_lista=None, transporte_criticos=None) -> Resultado:
    """Descarga → ojos → validar_respuesta → componer → puerta de estilo. Nunca publica.

    `salida`: carpeta del MP4. Si no se da, se crea una temporal que sólo se conserva cuando la toma sale ok
    (en cualquier descarte o error se borra). Cualquier excepción inesperada es un descarte con su motivo.

    Regla de gasto (`Resultado.gastar`): sólo es True cuando el fallo es del material (archivo que no sirve,
    respuesta de Gemini que no valida, puerta de estilo roja, error del editor). Red, tiempo, Gemini pasajero,
    sin clave o error inesperado dejan `gastar=False`: la fuente se puede volver a intentar.
    """
    propia = salida is None
    carpeta: Path | None = None
    r = None
    try:
        carpeta = Path(salida) if salida is not None else Path(tempfile.mkdtemp(prefix="granja-toma-"))
        r = _hacer_toma(raiz, candidato, ficha, ahora, carpeta, abrir, ejecutor, transporte, espera, entorno,
                        transporte_lista, transporte_criticos)
    except plazo.SinTiempo:
        raise                # se acabó el plazo del ciclo: lo maneja el ciclo (la fuente no se gasta)
    except Exception as e:  # noqa: BLE001 (nada debe subir sin control: es un descarte; gastar=False)
        r = Resultado(False, [f"error inesperado: {type(e).__name__}: {e}"])
    finally:
        if propia and carpeta is not None and not (r is not None and r.ok):
            shutil.rmtree(carpeta, ignore_errors=True)
    return r


def _planificar_historia(raiz, ficha, datos, inicio: float, dur: float, cfg_h: dict, sfx_d: dict, video_ojos,
                         lineas) -> "mod_historia.Planificacion":
    """El plan de historia de la cadena Gemini → segundo → sin IA. Nunca lanza (salvo el plazo): un fallo es «sin plan»."""
    try:
        with plazo.etapa("historia"):
            return mod_historia.planificar(
                raiz=raiz, ficha=ficha, recorte=(inicio, round(inicio + dur, 3)),
                rango_duracion=estilo.rango_duracion({}, ficha), sfx=sfx_d, datos_gemini=datos,
                ruta_video=video_ojos, lineas=lineas, cfg=cfg_h)
    except plazo.SinTiempo:
        raise
    except Exception as e:  # noqa: BLE001 (la historia nunca descarta una fuente)
        return mod_historia.Planificacion(None, None, [f"historia: error inesperado {type(e).__name__}"],
                                          f"error inesperado: {type(e).__name__}")


def _spoilers_en_lineas(lineas, temporada: dict) -> list[str]:
    """Las líneas que ponen las voces (#19, transcripción de Whisper) no pasaron por `ojos.validar_respuesta`:
    se les aplica el mismo filtro de palabras a evitar de la temporada (revisión de Codex, 22 nov)."""
    motivos = []
    for i, ln in enumerate(lineas or [], 1):
        for p in temporada.get("palabras_evitar", []) or []:
            for donde, texto in (("frase", getattr(ln, "frase", "") or ""), ("acotación", getattr(ln, "acotacion", "") or "")):
                if ojos._contiene(texto, str(p)):
                    motivos.append(f"línea {i}: palabra a evitar {p!r} en la {donde} (posible spoiler)")
    return motivos


def _hacer_toma(raiz, candidato, ficha, ahora, salida: Path, abrir, ejecutor, transporte, espera, entorno,
                transporte_lista=None, transporte_criticos=None, nota_juez: str | None = None, ronda: int = 1,
                descarga=None, llamadas_previas: dict | None = None) -> Resultado:
    raiz = raiz_repo(raiz)
    salida.mkdir(parents=True, exist_ok=True)
    marca = Marca.cargar(raiz)
    temporada = cargar_yaml("temporada.yml", raiz)
    hoy = ahora.astimezone(ZONA).date()
    try:
        with ExitStack() as pila:
            with plazo.etapa("descarga"):      # la descarga (y la medición) ocurre al entrar
                d = descarga or pila.enter_context(      # ronda 2 de los críticos: la misma descarga, sin repetirla
                    mod_descargar.descargar(raiz, candidato, ficha, hoy, abrir=abrir, ejecutor=ejecutor))
            temporada = ojos.temporada_de(ficha, temporada)
            juego = ojos.juego_de(ficha, temporada)          # el de la ficha si lo declara (FM27, no GTA 6)
            recorte = ojos.rango_recorte(ficha)              # reglas.duracion_s de la ficha
            recorte_ficha = recorte                          # contra éste se valida lo que devuelva Gemini
            # Prueba de retención: el brazo sale del número de toma que tocará (el contador sólo sube al programar).
            cfg_ret = retencion.cargar(raiz)
            el_brazo = retencion.brazo(contador_toma(raiz), cfg_ret, hoy)
            nota_prueba = None
            if el_brazo == retencion.CORTA:
                rango_c = retencion.rango_corta(cfg_ret, recorte_ficha)
                if rango_c is None:
                    el_brazo, nota_prueba = retencion.CONTROL, "pasó a control: la ficha pide una duración fuera de 10-14 s"
                elif d.duracion is not None and d.duracion < rango_c[0] - 1e-6:
                    el_brazo, nota_prueba = retencion.CONTROL, (f"pasó a control: la fuente dura {d.duracion:.1f} s, "
                                                               f"menos de {rango_c[0]:g} s")
                else:
                    recorte = rango_c
            etiqueta_prueba = retencion.etiqueta(cfg_ret, el_brazo) if el_brazo else ""
            gancho_ini = float(cfg_ret["brazos"]["corta"]["gancho_max_s"]) if el_brazo == retencion.CORTA else None
            cfg_sin = (cargar_yaml("config/ojos.yml", raiz).get("sin_ia") or {})
            cfg_h = mod_historia.cargar_config(raiz)
            # La historia añade la apertura (2-3 s) y sacaría la toma del 10-14: en el brazo corta va apagada.
            con_historia = cfg_h.get("activo") is True and el_brazo != retencion.CORTA
            sfx_d = mod_sfx.disponibles(raiz) if con_historia else {}
            prohibe_sfx = mod_historia.sfx_prohibido(ficha, cfg_h)
            pide_historia = ({"cfg": cfg_h, "sfx": [] if prohibe_sfx else sorted(sfx_d), "prohibe_sfx": prohibe_sfx}
                             if con_historia else None)
            intentos_gemini: list[str] = []
            por_sin_ia = None                  # por qué no hubo Gemini, si el corte es sin IA
            carpeta_ojos = pila.enter_context(tempfile.TemporaryDirectory(prefix="ojos-toma-"))
            try:
                with plazo.etapa("copia ligera"):
                    video_ojos = copia_para_ojos(d.ruta, raiz, Path(carpeta_ojos))
            except subprocess.CalledProcessError as e:     # ffmpeg rechazó el archivo: es del material
                return Resultado(False, [f"copia ligera: ffmpeg no pudo leer el archivo ({type(e).__name__})"],
                                 gastar=True)
            with plazo.etapa("ojos (Gemini)"):
                mirada = ojos.mirar(video_ojos, marca, temporada, raiz=raiz, duracion=d.duracion,
                                    transporte=transporte, espera=espera, entorno=entorno, juego=juego,
                                    nota=" ".join(x for x in (ojos.nota_de_ficha(ficha), nota_juez) if x) or None,
                                    recorte=recorte, transporte_lista=transporte_lista, registrar_cupos=True,
                                    gancho_inicio_s=gancho_ini,
                                    **({"historia": pide_historia} if pide_historia else {}))
            # Sin clave o con clave mala (401/403) es un error de configuración, no una caída de Gemini:
            # ahí no hay corte sin IA.
            if (mirada.datos is None and mirada.sin_gemini and not mirada.clave_mala
                    and cfg_sin.get("activo") is True
                    and mirada.motivo != ojos.MOTIVO_SIN_CLAVE):
                if not sin_ia.reservar(cfg_sin.get("max_por_ciclo", sin_ia.MAX_POR_CICLO)):
                    return Resultado(False, [f"ojos: {mirada.motivo}; tope de tomas sin IA del ciclo "
                                             "(pasajero: se reintenta)"])
                por_sin_ia = mirada.motivo
                intentos_gemini = list(mirada.intentos)
                with plazo.etapa("corte sin IA"):
                    obj = cfg_sin.get("duracion_objetivo_s", sin_ia.OBJETIVO_S)
                    if el_brazo == retencion.CORTA:          # el objetivo es la mitad del rango 10-14
                        obj = (recorte[0] + recorte[1]) / 2
                    c = sin_ia.construir(video_ojos, candidato, ficha, marca, temporada, duracion=d.duracion,
                                         rango=recorte, objetivo_s=obj if isinstance(obj, (int, float)) else sin_ia.OBJETIVO_S)
                if c.datos is None:
                    return Resultado(False, [f"ojos: {mirada.motivo}; {c.motivo}"], gastar=c.gastar)
                mirada = ojos.Mirada(c.datos, modelo=SIN_IA)
                plazo.log(f"ojos: {por_sin_ia}; corte sin IA (picos de volumen y cambios de escena)")
            if mirada.datos is None:
                if mirada.pasajero:          # Gemini caído o sin tiempo: la fuente no se gasta
                    return Resultado(False, [f"ojos: {mirada.motivo} (pasajero: se reintenta)"],
                                     modelo=mirada.modelo)
                return Resultado(False, [f"ojos: {mirada.motivo}"], modelo=mirada.modelo,
                                 gastar=bool(mirada.del_material))
            ok, motivos = ojos.validar_respuesta(mirada.datos, marca, temporada, duracion=d.duracion,
                                              juego=juego, recorte=recorte_ficha)
            if not ok:
                return Resultado(False, [f"ojos: {x}" for x in motivos], gemini=mirada.datos, modelo=mirada.modelo,
                                 gastar=True)
            if el_brazo == retencion.CORTA:
                # Gemini no siempre respeta 10-14: se recorta el final (nunca se estira); si no cabe, a control.
                rec = mirada.datos["recorte"]
                aj = retencion.ajustar_recorte(float(rec["inicio"]), float(rec["fin"]), d.duracion or float(rec["fin"]), recorte)
                if aj is None:
                    el_brazo, etiqueta_prueba = retencion.CONTROL, retencion.etiqueta(cfg_ret, retencion.CONTROL)
                    nota_prueba = "pasó a control: no cabe un recorte de 10-14 s en la fuente"
                    recorte = recorte_ficha
                elif aj != (float(rec["inicio"]), float(rec["fin"])):
                    nota_prueba = (f"recorte ajustado de {float(rec['fin']) - float(rec['inicio']):.1f} s a "
                                   f"{aj[1] - aj[0]:.1f} s (sin estirar)")
                    mirada = replace(mirada, datos={**mirada.datos, "recorte": {"inicio": aj[0], "fin": aj[1]}})
            numero = contador_toma(raiz)
            entrada = fuentes.entrada_material(ficha, url=candidato.url)
            creador = nombres.creador_de(entrada["creador"] if entrada else None, ficha)
            toma, lineas, inicio, dur = armar(mirada.datos, marca, ficha, numero, creador, temporada, raiz)
            motivos_nombres = nombres.revisar_nombres(toma, lineas, creador, raiz)
            if motivos_nombres:
                return Resultado(False, [f"puerta de nombres: {m}" for m in motivos_nombres],
                                 gemini=mirada.datos, modelo=mirada.modelo, gastar=True)
            mp4 = salida / f"toma-{numero:03d}-{_nombre_seguro(candidato.id_origen)}.mp4"
            plan_ruta = mp4.with_suffix(".plan.json")
            ev = _etapa_voces(raiz, marca, d.ruta, inicio, dur, lineas, mirada.datos, creador, recorte[0])
            # Puerta de nombres (#24) también para lo que ponen las voces (#19): el hablante sólo sale de la ficha
            # (el creador) o es OTRA VOZ, y ni las frases transcritas ni el rótulo llevan un nombre vetado. Si las
            # voces no pasan, se vuelve a las líneas de Gemini (ya limpias, sin cortes); si tampoco, se descarta.
            motivos_voces = (nombres.revisar_nombres(toma, ev.lineas, creador, raiz, marca)
                             + _spoilers_en_lineas(ev.lineas, temporada))
            if motivos_voces and ev.alterno is not None:
                ev.lineas, ev.dur, ev.transcripcion = ev.alterno[0], ev.alterno[1], None
                ev.tramos = None
                ev.info = {**(ev.info or {}), "fuente_lineas": "gemini", "cortes": [],
                           "reintento": "líneas de Gemini sin cortes por la puerta de nombres: " + "; ".join(motivos_voces)[:300]}
                motivos_voces = (nombres.revisar_nombres(toma, ev.lineas, creador, raiz, marca)
                                 + _spoilers_en_lineas(ev.lineas, temporada))
            if motivos_voces:
                return Resultado(False, [f"puerta de nombres (voces): {m}" for m in motivos_voces],
                                 gemini=mirada.datos, modelo=mirada.modelo, gastar=True)
            lineas, dur, tramos, info_voces, transcripcion = ev.lineas, ev.dur, ev.tramos, ev.info, ev.transcripcion
            pista, aviso_musica = None, None
            try:
                # Música CC0 sólo si la fuente no trae audio y la ficha no dice sin_musica. Sin pista disponible
                # sigue en silencio (la puerta de estilo lo marca en rojo y la toma se descarta con ese motivo).
                if mod_musica.lleva_musica(ficha, sondear_video(d.ruta)["audio"]):
                    pista = mod_musica.elegir(raiz, numero)
                    if pista is None:
                        aviso_musica = ("la fuente no trae audio y no hay pista de música CC0 disponible "
                                        f"(GRANJA_MUSICA_DIR={mod_musica.carpeta() or ''}): sigue en silencio")
                        print(f"AVISO: {aviso_musica}")
                def compuesta(lineas_, dur_, tramos_, h=None):
                    with tempfile.TemporaryDirectory(prefix="trabajo-toma-") as trabajo:
                        with plazo.etapa("componer"):
                            plan_ = componer(marca, toma, lineas_, mp4, duracion=dur_, trabajo=Path(trabajo),
                                             fuente_video=d.ruta, inicio=inicio, plan_salida=plan_ruta, ficha=ficha,
                                             musica=pista, tramos=tramos_, historia=h, sfx=sfx_d, config_historia=cfg_h)
                    # La puerta técnica (sin LLM) corre antes que la de estilo: si sale roja se ahorra la de estilo.
                    with plazo.etapa("puerta técnica"):
                        puntos_t = tecnica.revisar(mp4, tecnica.con_originales(plan_, lineas_), tecnica.cargar(raiz))
                    if any(not ok_t for _, ok_t, _ in puntos_t):
                        return plan_, puntos_t
                    with plazo.etapa("puerta de estilo"):
                        return plan_, puntos_t + estilo.revisar(mp4, plan_, marca, ficha=ficha)

                # La historia (#20) nunca descarta ni gasta una fuente: si no hay plan, o el render o la puerta fallan
                # con ella, la toma se renderiza como siempre y el plan dice por qué. Con silencios cortados (#19) la
                # línea de tiempo ya no es la del recorte: ahí manda el corte de voces y la historia se apaga.
                plan_h, plan, puntos = None, None, None
                if con_historia and tramos:
                    plan_h = mod_historia.Planificacion(
                        None, None, ["historia: hay silencios cortados (voces)"],
                        "hay silencios cortados (voces): historia apagada")
                elif con_historia and not plazo.queda(PLAZO_MIN_HISTORIA_S):
                    plan_h = mod_historia.Planificacion(
                        None, None, [f"historia: quedan menos de {PLAZO_MIN_HISTORIA_S} s del plazo"],
                        f"quedan menos de {PLAZO_MIN_HISTORIA_S} s del plazo del ciclo")
                elif con_historia:
                    plan_h = _planificar_historia(raiz, ficha, mirada.datos, inicio, dur, cfg_h, sfx_d,
                                                  video_ojos, lineas)
                if plan_h is not None and plan_h.plan is not None:
                    try:
                        plan, puntos = compuesta(lineas, dur, tramos, plan_h)
                        malos_h = [n for n, ok_p, _ in puntos if not ok_p]
                        if malos_h:
                            raise ValueError("la puerta de estilo salió en rojo con historia: " + "; ".join(malos_h[:3]))
                    except plazo.SinTiempo:
                        raise
                    except Exception as e:  # noqa: BLE001 (la historia nunca descarta una fuente; el plazo ya subió arriba)
                        mp4.unlink(missing_ok=True)
                        motivo_h = f"el render con historia falló ({type(e).__name__}): {str(e)[:mod_historia.TOPE_TEXTO]}"
                        plazo.log(f"historia: {motivo_h}; la toma se renderiza sin ella")
                        plan_h = mod_historia.Planificacion(None, None, [*plan_h.intentos, motivo_h], motivo_h)
                        plan, puntos = None, None
                if plan is None:
                    # Si algo de las voces (líneas de la transcripción o cortes) hace fallar el compositor o la
                    # puerta, se reintenta UNA vez con las líneas de Gemini y sin cortes antes de descartar la toma.
                    falla, error0 = None, None
                    try:
                        plan, puntos = compuesta(lineas, dur, tramos, plan_h)
                        malos = [n for n, ok_p, _ in puntos if not ok_p]
                        falla = ("la puerta de estilo salió en rojo: " + "; ".join(malos)) if malos else None
                    except (ValueError, subprocess.CalledProcessError) as e0:   # ffmpeg también: los cortes son de voces
                        error0, falla = e0, f"{type(e0).__name__}: {e0}"
                    if falla and ev.alterno is not None:
                        mp4.unlink(missing_ok=True)
                        plazo.log(f"voces: {falla[:200]}; se reintenta con las líneas de Gemini y sin cortes")
                        lineas, dur, transcripcion, golpes_sin_cortes = ev.alterno[0], ev.alterno[1], ev.alterno[2], ev.alterno[3]
                        tramos = None
                        info_voces = {**info_voces, "fuente_lineas": "gemini", "cortes": [], "golpes": golpes_sin_cortes,
                                      "reintento": f"líneas de Gemini sin cortes tras: {falla[:300]}"}
                        plan, puntos = compuesta(lineas, dur, None, plan_h)
                    elif error0 is not None:
                        raise error0
            except (ValueError, subprocess.SubprocessError, OSError) as e:
                mp4.unlink(missing_ok=True)
                # Sólo es del material si el editor corrió y falló (ffmpeg con código ≠ 0) o dio datos inválidos
                # (ValueError). Falta de ffmpeg, disco lleno (OSError) o tiempo agotado son de la máquina.
                return Resultado(False, [f"editor: {type(e).__name__}: {e}"], gemini=mirada.datos,
                                 modelo=mirada.modelo,
                                 gastar=isinstance(e, (subprocess.CalledProcessError, ValueError)))
            rojos = tecnica.rojos(puntos)
            if rojos:
                mp4.unlink(missing_ok=True)
                if aviso_musica:
                    rojos.append(f"música: {aviso_musica}")
                # Sin pista de música disponible (release sin bajar) el fallo es de la máquina: no gasta la fuente.
                return Resultado(False, rojos, None, plan, mirada.datos, mirada.modelo, puntos,
                                 gastar=not aviso_musica)
            if por_sin_ia is not None:
                plan["corte"] = "sin_ia"
                plan["gemini"] = {"modelo": None, "corte": "sin_ia", "motivo": por_sin_ia,
                                 "intentos": intentos_gemini}
            else:
                plan["gemini"] = {"modelo": mirada.modelo, "idioma_original": mirada.datos["idioma_original"]}
            # Tarea #21: los críticos (que no ven el plan) miran el MP4 final. Sin transporte propio y con ojos
            # falsos (pruebas) corren sin IA: nunca llaman a la red por su cuenta.
            dec = mod_criticos.decidir(
                raiz, mp4, plan, marca, ronda=ronda, con_corte_gemini=por_sin_ia is None, fuente=candidato.id_origen,
                entorno=entorno, transporte=transporte_criticos, espera=espera, transporte_lista=transporte_lista,
                modo="sin_ia" if transporte is not None and transporte_criticos is None else None,
                llamadas_previas=llamadas_previas)
            if dec.bloque is not None:
                plan["criticos"] = dec.bloque
            if dec.accion == "replantear":       # ronda 1 con corte de Gemini: una vuelta a los ojos con la nota del juez
                mp4.unlink(missing_ok=True)
                return _hacer_toma(raiz, candidato, ficha, ahora, salida, abrir, ejecutor, transporte, espera, entorno,
                                   transporte_lista, transporte_criticos, nota_juez=dec.nota, ronda=2, descarga=d,
                                   llamadas_previas=dec.bloque["llamadas"])
            if dec.accion == "sin_tiempo":       # no cabe la ronda 2 junto con publicar: la fuente no se gasta
                mp4.unlink(missing_ok=True)
                return Resultado(False, dec.motivos, None, plan, mirada.datos, mirada.modelo, puntos, gastar=False)
            if dec.accion == "descartar":        # el fallo es del material: la fuente se gasta
                mp4.unlink(missing_ok=True)
                return Resultado(False, dec.motivos, None, plan, mirada.datos, mirada.modelo, puntos, gastar=True)
            if info_voces is not None:
                plan["voces"] = info_voces
            if transcripcion is not None:
                plan["transcripcion"] = transcripcion          # contrato de la tarea #17: palabras en tiempo de la toma
            if el_brazo:
                plan["prueba"] = {"nombre": cfg_ret["nombre"], "brazo": el_brazo, "duracion_s": round(float(dur), 2),
                                  **({"nota": nota_prueba} if nota_prueba else {})}
                if nota_prueba:
                    plazo.log(f"prueba {cfg_ret['nombre']}: {nota_prueba}")
            # Después de anotar la prueba (revisión de Codex, 22 nov): el plan guardado lleva el brazo.
            plan_ruta.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            fila = fila_marcador(candidato, ficha, toma, dur, ahora, etiqueta_prueba)
            if isinstance(plan.get("historia"), dict):
                fila["historia"] = plan["historia"].get("proveedor") or ""
                if plan["historia"].get("plan") is not None:
                    fila["duracion_s"] = round(float(plan["duracion"]), 2)
            return Resultado(True, [], mp4, plan, mirada.datos, mirada.modelo, puntos, fila)
    except mod_descargar.ErrorDescarga as e:
        return Resultado(False, [f"descarga: {e}"], gastar=bool(getattr(e, "del_material", False)))

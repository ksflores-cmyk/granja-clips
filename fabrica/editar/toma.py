"""Editor de punta a punta: `hacer_toma` descarga, mira, valida, compone y pasa la puerta de estilo.

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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import descargar as mod_descargar
from .. import fuentes, ojos, plazo, puertas
from ..config import cargar_yaml, raiz_repo
from ..registro import ZONA, contador_toma
from . import estilo, sin_ia
from .componer import Linea, componer
from .lienzo import Toma
from .marca import Marca

SIN_IA = "sin-ia"          # `Resultado.modelo` cuando el corte no pasó por Gemini
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
          creador: str | None = None) -> tuple[Toma, list[Linea], float, float]:
    """(Toma, líneas relativas al recorte, inicio, duración) a partir de una respuesta ya validada.

    `creador`: el creador recortado cuando la ficha lo trae por archivo (campañas); si no, el de la ficha."""
    ini, fin = float(datos["recorte"]["inicio"]), float(datos["recorte"]["fin"])
    dur = round(fin - ini, 3)
    toma = Toma(numero=numero, gancho=datos["gancho"].strip(), palabra_lapiz=datos["palabra_lapiz"].strip(),
                lapiz=lapiz_de(numero), escena=datos["escena"].strip().upper(),
                credito=f"Con: {creador or ficha['creador']}")
    tope = dur - float(marca.m("cierre_s"))      # el último medio segundo es el cierre
    lineas: list[Linea] = []
    for ln in ojos.partir_lineas(datos, marca):
        t0, t1 = max(0.0, round(ln["t0"] - ini, 3)), min(tope, round(ln["t1"] - ini, 3))
        if t1 - t0 < MIN_LINEA_S:
            continue
        lineas.append(Linea(t0=t0, t1=t1, nombre=ln["hablante"].strip().upper(), frase=ln["es"].strip(),
                            original=ln["original"]))
    acot = datos["acotacion"].strip()
    if acot:
        if lineas:
            lineas[0].acotacion = acot
        else:
            lineas.append(Linea(t0=INICIO_ACOTACION_S, t1=max(INICIO_ACOTACION_S + MIN_LINEA_S, tope - 0.2),
                                acotacion=acot))
    return toma, lineas, ini, dur


def fila_marcador(candidato, ficha: dict, toma: Toma, dur: float, ahora: datetime) -> dict:
    """Fila futura del marcador (sin id_clip ni notas: `registro.anotar_clip` los pone)."""
    local = ahora.astimezone(ZONA)
    return {"toma": toma.numero, "fecha": local.date().isoformat(), "hora": local.strftime("%H:%M"),
            "pilar": candidato.pilar or "", "fuente_id": ficha.get("id"), "campana": ficha.get("campana"),
            "gancho": toma.gancho, "duracion_s": round(dur, 2), "credito": toma.credito}


def hacer_toma(raiz, candidato, ficha: dict | None, ahora: datetime, *, salida: Path | None = None,
               abrir=None, ejecutor=None, transporte=None, espera=time.sleep, entorno=None,
               transporte_lista=None) -> Resultado:
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
                        transporte_lista)
    except plazo.SinTiempo:
        raise                # se acabó el plazo del ciclo: lo maneja el ciclo (la fuente no se gasta)
    except Exception as e:  # noqa: BLE001 (nada debe subir sin control: es un descarte; gastar=False)
        r = Resultado(False, [f"error inesperado: {type(e).__name__}: {e}"])
    finally:
        if propia and carpeta is not None and not (r is not None and r.ok):
            shutil.rmtree(carpeta, ignore_errors=True)
    return r


def _hacer_toma(raiz, candidato, ficha, ahora, salida: Path, abrir, ejecutor, transporte, espera, entorno,
                transporte_lista=None) -> Resultado:
    raiz = raiz_repo(raiz)
    salida.mkdir(parents=True, exist_ok=True)
    marca = Marca.cargar(raiz)
    temporada = cargar_yaml("temporada.yml", raiz)
    hoy = ahora.astimezone(ZONA).date()
    try:
        with ExitStack() as pila:
            with plazo.etapa("descarga"):      # la descarga (y la medición) ocurre al entrar
                d = pila.enter_context(
                    mod_descargar.descargar(raiz, candidato, ficha, hoy, abrir=abrir, ejecutor=ejecutor))
            temporada = ojos.temporada_de(ficha, temporada)
            juego = ojos.juego_de(ficha, temporada)          # el de la ficha si lo declara (FM27, no GTA 6)
            recorte = ojos.rango_recorte(ficha)              # reglas.duracion_s de la ficha
            cfg_sin = (cargar_yaml("config/ojos.yml", raiz).get("sin_ia") or {})
            por_sin_ia = None                  # por qué no hubo Gemini, si el corte es sin IA
            with tempfile.TemporaryDirectory(prefix="ojos-toma-") as carpeta_ojos:
                with plazo.etapa("copia ligera"):
                    video_ojos = copia_para_ojos(d.ruta, raiz, Path(carpeta_ojos))
                with plazo.etapa("ojos (Gemini)"):
                    mirada = ojos.mirar(video_ojos, marca, temporada, raiz=raiz, duracion=d.duracion,
                                        transporte=transporte, espera=espera, entorno=entorno, juego=juego,
                                        nota=ojos.nota_de_ficha(ficha) or None, recorte=recorte,
                                        transporte_lista=transporte_lista)
                if mirada.datos is None and mirada.sin_gemini and cfg_sin.get("activo") is True:
                    por_sin_ia = mirada.motivo
                    with plazo.etapa("corte sin IA"):
                        obj = cfg_sin.get("duracion_objetivo_s", sin_ia.OBJETIVO_S)
                        c = sin_ia.construir(video_ojos, candidato, ficha, marca, temporada, duracion=d.duracion,
                                             rango=recorte, objetivo_s=obj if isinstance(obj, (int, float)) else sin_ia.OBJETIVO_S)
                    if c.datos is None:
                        return Resultado(False, [f"ojos: {mirada.motivo}; {c.motivo}"], modelo=SIN_IA, gastar=c.gastar)
                    mirada = ojos.Mirada(c.datos, modelo=SIN_IA)
                    plazo.log(f"ojos: {por_sin_ia}; corte sin IA (picos de volumen y cambios de escena)")
            if mirada.datos is None:
                if mirada.pasajero:          # Gemini caído o sin tiempo: la fuente no se gasta
                    return Resultado(False, [f"ojos: {mirada.motivo} (pasajero: se reintenta)"],
                                     modelo=mirada.modelo)
                return Resultado(False, [f"ojos: {mirada.motivo}"], modelo=mirada.modelo,
                                 gastar=bool(mirada.del_material))
            ok, motivos = ojos.validar_respuesta(mirada.datos, marca, temporada, duracion=d.duracion,
                                              juego=juego, recorte=recorte)
            if not ok:
                return Resultado(False, [f"ojos: {x}" for x in motivos], gemini=mirada.datos, modelo=mirada.modelo,
                                 gastar=True)
            numero = contador_toma(raiz)
            entrada = fuentes.entrada_material(ficha, url=candidato.url)
            toma, lineas, inicio, dur = armar(mirada.datos, marca, ficha, numero,
                                              entrada["creador"] if entrada else None)
            mp4 = salida / f"toma-{numero:03d}-{_nombre_seguro(candidato.id_origen)}.mp4"
            plan_ruta = mp4.with_suffix(".plan.json")
            try:
                with tempfile.TemporaryDirectory(prefix="trabajo-toma-") as trabajo:
                    with plazo.etapa("componer"):
                        plan = componer(marca, toma, lineas, mp4, duracion=dur, trabajo=Path(trabajo),
                                        fuente_video=d.ruta, inicio=inicio, plan_salida=plan_ruta, ficha=ficha)
                with plazo.etapa("puerta de estilo"):
                    puntos = estilo.revisar(mp4, plan, marca, ficha=ficha)
            except (ValueError, subprocess.SubprocessError, OSError) as e:
                mp4.unlink(missing_ok=True)
                # Un ffmpeg que se pasó de tiempo es de la máquina, no del material.
                return Resultado(False, [f"editor: {type(e).__name__}: {e}"], gemini=mirada.datos,
                                 modelo=mirada.modelo, gastar=not isinstance(e, subprocess.TimeoutExpired))
            rojos = [f"puerta de estilo: {n}" + (f" ({det})" if det else "") for n, ok_p, det in puntos if not ok_p]
            if rojos:
                mp4.unlink(missing_ok=True)
                return Resultado(False, rojos, None, plan, mirada.datos, mirada.modelo, puntos, gastar=True)
            if por_sin_ia is not None:
                plan["corte"] = "sin_ia"
                plan["gemini"] = {"modelo": None, "corte": "sin_ia", "motivo": por_sin_ia}
            else:
                plan["gemini"] = {"modelo": mirada.modelo, "idioma_original": mirada.datos["idioma_original"]}
            plan_ruta.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            return Resultado(True, [], mp4, plan, mirada.datos, mirada.modelo, puntos,
                             fila_marcador(candidato, ficha, toma, dur, ahora))
    except mod_descargar.ErrorDescarga as e:
        return Resultado(False, [f"descarga: {e}"], gastar=bool(getattr(e, "del_material", False)))

"""El video de un compilado: 2 o 3 segmentos pegados con corte seco, cada uno hecho con `componer()`.

`compilar` renderiza cada segmento con la plantilla vigente (`componer`) y lo pasa por la puerta de estilo
(`estilo.revisar`); después los une con UN solo ffmpeg (`concat` del filtro, re-codificado a la salida de siempre).
Sin fundidos, sin xfade, sin acrossfade. `revisar_compilado` es la puerta del compilado entero (sin LLM).

CLI de muestras: `python -m fabrica.editar.compilado --muestras --salida DIR`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

from . import estilo, muestras
from .componer import Linea, componer, sha256_archivo
from .config_ruta import RAIZ
from .lienzo import Toma
from .marca import Marca

FPS = 30
PLANTILLA_POR_DEFECTO = "v1-pagina"

# Corte seco: diferencia media en gris entre miniaturas de 64 px (0-255) del fotograma del compilado y el del
# segmento que debería ser. Calibrada (20 nov 2026) con Sintel y Big Buck Bunny y con video sintético
# (testsrc2, mandelbrot, smptebars), todo sobre MP4 de la plantilla vigente:
#   compilado correcto: 0.1 a 0.2 en todas las uniones y a la mitad (la recompresión H.264 de segunda generación);
#   xfade de 0.25, 0.5 y 1.0 s: el fotograma anterior a la unión da de 26.2 a 49.4 (el decisivo); los otros puntos
#     dan de 0.4 a 22.6, a veces menos que el umbral, así que ninguno basta solo;
#   fundido a negro de 0.3 s al final de un segmento: 182.9; fundido de entrada de 0.3 s: 163.2.
# Umbral 4.0: 20 veces sobre el compilado correcto y 6.5 veces bajo el menor de los fundidos.
UMBRAL_CORTE = 4.0
MINIATURA_ANCHO = 64
# Audio: correlación mínima entre el centro de un segmento en el compilado y en su MP4 (como `audio_original`).
# Calibrada con Sintel y BBB (ventana de 2 s, desfase de hasta ±100 ms): sin filtro, un pasaje de Sintel con mucho
# agudo da 0.86 tras la segunda compresión AAC; con pasa-bajos de 1500 Hz, todas las ventanas medidas dan de 0.986 a
# 1.00 (tono sintético 1.00). Con el audio de otro segmento: 0.06 a 0.08; con silencio: 0.0. Mínimo 0.9.
PASABAJOS_HZ = 1500
CORRELACION_AUDIO = 0.9
VENTANA_AUDIO_S = 2.0
RMS_AUDIO_MUDO = 1.0           # en unidades de int16: por debajo, el segmento no trae audio que comparar
TOL_DURACION_SEGMENTO_S = 0.15


class SegmentoRojo(ValueError):
    """Algún segmento no pasó la puerta de estilo. `motivos` los lista; `puntos` es [(segmento, punto, ok, detalle)]."""

    def __init__(self, motivos: list[str], puntos: list | None = None):
        super().__init__("; ".join(motivos))
        self.motivos = motivos
        self.puntos = puntos or []


# ---------------------------------------------------------------- utilidades de medida

def _cuadros(ruta: Path) -> int:
    """Cuántos fotogramas tiene el MP4 (de su cabecera; si no la trae, duración por 30)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                          "-of", "json", str(ruta)], capture_output=True, text=True)
    try:
        n = int(json.loads(out.stdout)["streams"][0]["nb_frames"])
        if n > 0:
            return n
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    return int(round(estilo.sondear(Path(ruta))["duracion"] * FPS))


def _miniatura(video: Path, cuadro: int, alto: int) -> np.ndarray | None:
    """Miniatura en gris del fotograma número `cuadro` (el primero es el 0)."""
    img = estilo._fotograma(Path(video), max(0.0, (cuadro - 0.5) / FPS))
    return None if img is None else estilo._miniatura_gris(img, MINIATURA_ANCHO, alto)


def _pcm(ruta: Path, desde: float, largo: float) -> np.ndarray:
    """Audio mono a 8 kHz y pasa-bajos de `PASABAJOS_HZ` (lo que el AAC conserva tras dos compresiones)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, desde):.3f}", "-t", f"{largo:.3f}", "-i", str(ruta),
                        "-vn", "-ac", "1", "-ar", "8000", "-af", f"lowpass=f={PASABAJOS_HZ}", "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)


def _correlacion(a: np.ndarray, b: np.ndarray) -> float:
    """Mejor correlación con un desfase de hasta ±100 ms (el AAC corre la señal unas decenas de milisegundos)."""
    n = min(len(a), len(b))
    if n < 800:
        return 0.0
    a, b = a[:n], b[:n]
    mejor = 0.0
    for lag in range(-800, 801):      # de a una muestra: con 40 muestras de paso el audio de banda ancha se pierde
        x, y = (a[lag:], b[:n - lag]) if lag >= 0 else (a[:n + lag], b[-lag:])
        den = float(np.linalg.norm(x) * np.linalg.norm(y))
        if den > 0:
            mejor = max(mejor, float(np.dot(x, y)) / den)
    return mejor


def _pistas_audio(ruta: Path) -> int:
    return estilo._pistas_de_audio(Path(ruta))


# ---------------------------------------------------------------- unir

def orden_concat(entradas: list[Path], salida: Path, marca: Marca) -> list[str]:
    """La orden de ffmpeg que une los segmentos con corte seco (función pura; no corre nada)."""
    n = len(entradas)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for e in entradas:
        cmd += ["-i", str(e)]
    filtros = []
    for i in range(n):
        filtros.append(f"[{i}:v]scale={marca.ancho}:{marca.alto},fps={FPS},setsar=1,format=yuv420p[v{i}]")
        filtros.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
    pares = "".join(f"[v{i}][a{i}]" for i in range(n))
    filtros.append(f"{pares}concat=n={n}:v=1:a=1[v][a]")
    return cmd + ["-filter_complex", ";".join(filtros), "-map", "[v]", "-map", "[a]",
                  "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-profile:v", "high", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                  "-movflags", "+faststart", str(salida)]


def rutas_segmentos(trabajo: Path, n: int) -> list[Path]:
    return [Path(trabajo) / f"seg-{i + 1}.mp4" for i in range(n)]


def compilar(marca: Marca, formato: dict, segmentos: list, salida: Path, *, trabajo: Path) -> tuple[dict, list]:
    """Renderiza los segmentos, los une con corte seco y corre la puerta del compilado.

    `formato`: el formato con sus límites (`formatos.formato_con_limites`). Los MP4 de cada segmento quedan en
    `trabajo/seg-N.mp4` (el que llama decide cuándo borrarlos). Lanza `SegmentoRojo` si algún segmento sale en
    rojo en la puerta de estilo y `ValueError` si un texto no cabe. Devuelve (plan, puntos del compilado) y escribe
    `plan_compilado.json` junto al MP4.
    """
    trabajo = Path(trabajo)
    salida = Path(salida)
    trabajo.mkdir(parents=True, exist_ok=True)
    mp4s = rutas_segmentos(trabajo, len(segmentos))
    planes, rojos, puntos_seg = [], [], []
    for i, (seg, mp4) in enumerate(zip(segmentos, mp4s), start=1):
        plan_seg = componer(marca, seg.toma, seg.lineas, mp4, duracion=seg.duracion, trabajo=trabajo / f"capas-{i}",
                            fuente_video=seg.fuente, inicio=seg.inicio, plan_salida=trabajo / f"seg-{i}.plan.json",
                            ficha=seg.ficha)
        puntos = estilo.revisar(mp4, plan_seg, marca, ficha=seg.ficha)
        planes.append((plan_seg, puntos))
        for nombre, ok, det in puntos:
            puntos_seg.append((i, nombre, ok, det))
            if not ok:
                rojos.append(f"segmento {i}: {nombre}" + (f" ({det})" if det else ""))
        if rojos:            # un segmento en rojo basta: no se gasta tiempo en renderizar los demás
            raise SegmentoRojo(rojos, puntos_seg)
    salida.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(orden_concat(mp4s, salida, marca), check=True)
    # posiciones medidas sobre los MP4 de cada segmento (fotogramas), no las pedidas
    entradas, t = [], 0.0
    cuadros = [_cuadros(m) for m in mp4s]
    for i, (seg, (plan_seg, puntos), mp4, n) in enumerate(zip(segmentos, planes, mp4s, cuadros), start=1):
        dur = n / FPS
        credito = seg.toma.credito
        entradas.append({"n": i, "t0": round(t, 3), "t1": round(t + dur, 3), "duracion": round(dur, 3),
                         "fuente_id": seg.ficha.get("id"), "creador": seg.creador, "credito": credito,
                         "id_origen": seg.id_origen, "fecha": seg.fecha.isoformat(),
                         "sha256_mp4": sha256_archivo(mp4), "plan": plan_seg,
                         "puerta_ok": all(ok for _, ok, _ in puntos),
                         "puntos": [[n_, ok, det] for n_, ok, det in puntos]})
        t += dur
    plan = {"tipo": "compilado", "formato": formato.get("id"), "pilar": formato.get("pilar"),
            "numero": segmentos[0].toma.numero, "fecha": segmentos[0].fecha.isoformat(),
            "duracion": round(estilo.sondear(salida)["duracion"], 3),
            "plantilla": entradas[0]["plan"].get("plantilla", PLANTILLA_POR_DEFECTO),
            "creditos": [e["credito"] for e in entradas], "segmentos": entradas}
    puntos = revisar_compilado(salida, plan, mp4s, formato, marca)
    (salida.parent / "plan_compilado.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
    return plan, puntos


# ---------------------------------------------------------------- puerta del compilado

def _norm(t) -> str:
    return re.sub(r"[^a-z0-9]", "", str(t or "").lower())


def revisar_compilado(mp4: Path, plan: dict, segmentos_mp4: list, cfg: dict, marca: Marca | None = None
                      ) -> list[tuple[str, bool, str]]:
    """Lista de (punto, ok, detalle) sobre el MP4 del compilado, su plan y los MP4 de cada segmento. Sin LLM.

    `cfg`: los límites de `config/formatos.yml` (vale el archivo entero o el formato con sus límites)."""
    marca = marca or Marca.cargar()
    mp4 = Path(mp4)
    res: list[tuple[str, bool, str]] = []

    def punto(base: str, nombre: str, ok: bool, detalle: str = "") -> None:
        res.append((f"[{base}] {nombre}", bool(ok), detalle))

    segs = list(plan.get("segmentos") or [])
    lo_n, hi_n = cfg["segmentos"]
    punto("compilado", f"{lo_n} o {hi_n} segmentos" if hi_n - lo_n == 1 else f"De {lo_n} a {hi_n} segmentos",
          lo_n <= len(segs) <= hi_n, f"{len(segs)} segmentos")
    info = estilo.sondear(mp4)
    v, a = info["video"], info["audio"]
    d_lo, d_hi = cfg["duracion_s"]
    punto("compilado", f"Duración de {d_lo:g} a {d_hi:g} s", d_lo <= info["duracion"] <= d_hi,
          f"{info['duracion']:.2f} s")
    suma = sum(float(s.get("duracion") or 0) for s in segs)
    tol = TOL_DURACION_SEGMENTO_S * max(1, len(segs))
    punto("compilado", "Duración igual a la suma de los segmentos", abs(info["duracion"] - suma) <= tol,
          f"MP4 {info['duracion']:.2f} s, segmentos {suma:.2f} s (tolerancia {tol:.2f})")
    punto("fotograma", f"Resolución {marca.ancho}x{marca.alto}",
          (v.get("width"), v.get("height")) == (marca.ancho, marca.alto), f"{v.get('width')}x{v.get('height')}")
    punto("fotograma", "Códec h264 + aac", v.get("codec_name") == "h264" and a.get("codec_name") == "aac",
          f"{v.get('codec_name')} + {a.get('codec_name')}")
    punto("fotograma", "30 fps", abs(info["fps"] - FPS) < 0.01, f"{info['fps']:.2f}")
    punto("fotograma", "faststart (moov antes de mdat)", estilo.faststart(mp4))
    fechas = {str(s.get("fecha") or plan.get("fecha")) for s in segs}
    punto("compilado", "Mismo día", len(fechas) == 1 and bool(plan.get("fecha")) and fechas == {str(plan["fecha"])},
          ", ".join(sorted(fechas)))
    creditos = [str(c) for c in plan.get("creditos") or []]
    malos = [str(s.get("n")) for s in segs
             if not (str(s.get("credito") or "").strip() and _norm(s.get("creador"))
                     and _norm(s.get("creador")) in _norm(s.get("credito"))
                     and str(s.get("credito")) in creditos)]
    punto("compilado", "Crédito de cada segmento", bool(segs) and not malos and len(creditos) == len(segs),
          ("segmentos sin crédito de su creador: " + ", ".join(malos)) if malos else
          f"{len(creditos)} créditos: " + " | ".join(creditos))
    rojos = [str(s.get("n")) for s in segs if s.get("puerta_ok") is not True]
    punto("compilado", "Puerta de estilo de cada segmento en verde", bool(segs) and not rojos,
          ("en rojo: " + ", ".join(rojos)) if rojos else "")
    ok_cortes, det_cortes = _cortes_secos(mp4, segmentos_mp4, marca)
    punto("fotograma", "Corte seco en cada unión", ok_cortes, det_cortes)
    ok_au, det_au = _audio_de_segmentos(mp4, segmentos_mp4)
    punto("audio", "Audio de cada segmento", ok_au, det_au)
    transiciones = marca.datos.get("movimiento", {}).get("transiciones")
    declaradas = plan.get("transiciones") or any(s.get("transicion") or s.get("transiciones") for s in segs)
    punto("compilado", "Nada de transiciones", not declaradas and transiciones == "ninguna",
          f"plan: {declaradas or 'ninguna'}; marca.yml: {transiciones}")
    return res


def _cortes_secos(mp4: Path, segmentos_mp4: list, marca: Marca) -> tuple[bool, str]:
    """En cada unión, el fotograma 2 antes y el 1 después coinciden con el último de su segmento y el 2.º del
    siguiente; a la mitad de cada segmento el compilado coincide con su segmento. Un fundido da diferencias altas."""
    if not segmentos_mp4:
        return False, "sin segmentos que comparar"
    alto = max(1, int(round(MINIATURA_ANCHO * marca.alto / marca.ancho)))
    cuadros = [_cuadros(Path(s)) for s in segmentos_mp4]
    difs: list[tuple[str, float | None]] = []

    def comparar(etiqueta: str, cuadro_c: int, seg: Path, cuadro_s: int) -> None:
        a, b = _miniatura(mp4, cuadro_c, alto), _miniatura(seg, cuadro_s, alto)
        difs.append((etiqueta, None if a is None or b is None else float(np.abs(a - b).mean())))

    inicio = 0
    for i, (seg, n) in enumerate(zip(segmentos_mp4, cuadros), start=1):
        comparar(f"mitad {i}", inicio + n // 2, Path(seg), n // 2)
        if i > 1:
            comparar(f"unión {i - 1}-{i} (después)", inicio + 1, Path(seg), 1)
        if i < len(cuadros):
            comparar(f"unión {i}-{i + 1} (antes)", inicio + n - 2, Path(seg), n - 2)
        inicio += n
    ilegibles = [e for e, d in difs if d is None]
    if ilegibles:
        return False, "no se pudo leer el fotograma: " + ", ".join(ilegibles)
    altos = [f"{e} {d:.1f}" for e, d in difs if d >= UMBRAL_CORTE]
    resumen = ", ".join(f"{e} {d:.1f}" for e, d in difs)
    if altos:
        return False, f"no es corte seco (máximo {UMBRAL_CORTE:g}): {', '.join(altos)}"
    return True, f"diferencias {resumen} (máximo {UMBRAL_CORTE:g})"


def _audio_de_segmentos(mp4: Path, segmentos_mp4: list) -> tuple[bool, str]:
    """Una sola pista AAC y, en el centro de cada segmento, su audio correlaciona con el del segmento."""
    if _pistas_audio(mp4) != 1:
        return False, f"el MP4 trae {_pistas_audio(mp4)} pistas de audio y debe traer exactamente una"
    inicio, partes, malas = 0.0, [], []
    for i, seg in enumerate(segmentos_mp4, start=1):
        dur = estilo.sondear(Path(seg))["duracion"]
        n = _cuadros(Path(seg)) / FPS
        largo = min(VENTANA_AUDIO_S, n / 2)
        centro = n / 2 - largo / 2
        b = _pcm(Path(seg), centro, largo)
        a = _pcm(mp4, inicio + centro, largo)
        inicio += n
        if len(b) < 800 or float(np.sqrt(np.mean(b ** 2))) < RMS_AUDIO_MUDO:
            partes.append(f"{i}: sin audio")
            continue
        c = _correlacion(a, b)
        partes.append(f"{i}: {c:.2f}")
        if c <= CORRELACION_AUDIO:
            malas.append(str(i))
    detalle = f"correlación por segmento {', '.join(partes)} (mínimo {CORRELACION_AUDIO:.2f})"
    return not malas, detalle if not malas else f"audio distinto en el segmento {', '.join(malas)}; {detalle}"


# ---------------------------------------------------------------- muestras

def _dentro_del_repo(ruta: Path) -> bool:
    return muestras._dentro_del_repo(ruta)


def fotogramas(mp4: Path, plan: dict, salida: Path, base: str) -> list[str]:
    """3 fotogramas PNG: en la primera unión, a la mitad y al final."""
    dur = float(plan["duracion"])
    union = float(plan["segmentos"][0]["t1"]) if plan["segmentos"] else dur / 3
    tiempos = [("union", union + 1 / FPS), ("mitad", dur / 2), ("fin", max(0.0, dur - muestras.FIN_S))]
    nombres = []
    for etiqueta, seg in tiempos:
        nombre = f"{base}-{etiqueta}.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seg:.3f}", "-i", str(mp4),
                        "-frames:v", "1", str(salida / nombre)], check=True)
        nombres.append(nombre)
    return nombres


def cargar_muestras(ruta: Path | None = None) -> dict:
    import yaml
    ruta = ruta or RAIZ / "marca" / "muestras" / "formatos.yml"
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))


def generar_muestras(salida: Path, marca: Marca, datos: dict, descargas: Path, *, fichas: list[dict] | None = None,
                     hoy=None, descargador=muestras.descargar, raiz=RAIZ) -> dict:
    """Renderiza los compilados de muestra, corre las puertas y escribe los informes en `salida`."""
    from datetime import date as _date

    from .. import formatos, fuentes
    from ..config import cargar_yaml
    from ..registro import ZONA
    cfg = formatos.cargar_config(raiz)
    temporada = cargar_yaml("temporada.yml", raiz)
    if fichas is None:
        fichas = fuentes.cargar_fuentes(raiz)
    hoy = hoy or datetime.now(ZONA).date()
    por_id = {f.get("id"): f for f in fichas}
    lista = datos["compilados"]
    for c in lista:                      # todo se autoriza antes de la primera conexión
        formato = cfg["formatos"][c["formato"]]
        for s in c["segmentos"]:
            ficha = por_id.get(s["fuente_id"])
            ok, motivo = formatos.apto(ficha, hoy, formato["pilar_segmento"], muestra=True)
            if not ok:
                raise RuntimeError(f"fuente no autorizada para {c['id']} ({s['fuente_id']}): {motivo}")
            ok, motivo = fuentes.autoriza_descarga(ficha, hoy, "muestras", s["video"]["url"])
            if not ok:
                raise RuntimeError(f"descarga no autorizada para {c['id']} ({s['fuente_id']}): {motivo}")
    informe = ["# Puerta de los compilados", "",
               "Revisión sin LLM (`fabrica.editar.compilado`) sobre cada compilado y sus segmentos.", ""]
    textos = ["# Textos de los compilados de muestra", ""]
    manifiesto = {"compilados": []}
    for c in lista:
        cid = c["id"]
        formato = formatos.formato_con_limites(cfg, c["formato"])
        carpeta = salida / cid
        carpeta.mkdir(parents=True, exist_ok=True)
        segmentos = []
        fecha = hoy if c.get("fecha", "corrida") == "corrida" else _date.fromisoformat(str(c["fecha"]))
        for i, s in enumerate(c["segmentos"]):
            ficha = por_id[s["fuente_id"]]
            ruta = descargador(s["video"]["url"], s["video"]["sha256"], descargas / Path(s["video"]["url"]).name)
            propia = Toma(numero=int(c["numero"]), gancho=s.get("gancho", ""), palabra_lapiz=s.get("palabra_lapiz", ""),
                          escena=s["escena"])
            creador = s.get("creador") or ficha["creador"]
            toma = formatos.toma_de_segmento(propia, formato, int(c["numero"]), i, creador, ficha)
            segmentos.append(formatos.Segmento(
                fuente=ruta, inicio=float(s["recorte"]["inicio"]), duracion=float(s["recorte"]["duracion"]),
                toma=toma, lineas=[Linea(**ln) for ln in s.get("lineas") or []], ficha=ficha,
                creador=creador, fecha=fecha, id_origen=s["fuente_id"]))
        mp4 = carpeta / f"{cid}.mp4"
        rojo_segmento = None
        plan, puntos = None, []
        with tempfile.TemporaryDirectory(prefix=f"trabajo-{cid}-") as tmp:
            try:
                plan, puntos = compilar(marca, formato, segmentos, mp4, trabajo=Path(tmp))
            except SegmentoRojo as e:
                rojo_segmento = e
        informe.append(f"## {cid}\n")
        if rojo_segmento is not None:
            informe.append("### Segmentos\n")
            informe += [f"- NO: segmento {m}" for m in rojo_segmento.motivos]
            manifiesto["compilados"].append({"id": cid, "en_verde": False, "motivos": rojo_segmento.motivos})
            continue
        informe.append(estilo.informe_md("Compilado", puntos))
        for e in plan["segmentos"]:
            informe.append(estilo.informe_md(f"Segmento {e['n']} ({e['fuente_id']}, {e['duracion']:.2f} s)",
                                             [tuple(p) for p in e["puntos"]]))
        ok = all(p[1] for p in puntos)
        cuadros = fotogramas(mp4, plan, carpeta, cid)
        compilado = formatos.Compilado(c["formato"], segmentos)
        textos.append(f"## {cid} ({formato['nombre']}, {plan['duracion']:.1f} s)\n")
        for plataforma in formatos.PLATAFORMAS:
            try:
                t = formatos.textos_compilado(compilado, plataforma, temporada, cfg, raiz=None)
                cuerpo = ("**Título:** " + t["titulo"] + "\n\n" if t["titulo"] else "") + "```\n" + t["texto"] + "\n```"
            except Exception as e:  # noqa: BLE001 (se informa; es una muestra)
                cuerpo = f"DESCARTADA: {e}"
                ok = False
            textos.append(f"### {plataforma}\n\n{cuerpo}\n")
        manifiesto["compilados"].append({
            "id": cid, "formato": c["formato"], "archivo": f"{cid}/{mp4.name}", "plan": f"{cid}/plan_compilado.json",
            "duracion_s": plan["duracion"], "segmentos": [{"fuente_id": e["fuente_id"], "t0": e["t0"], "t1": e["t1"]}
                                                          for e in plan["segmentos"]],
            "fotogramas": cuadros, "sha256_mp4": sha256_archivo(mp4), "en_verde": ok})
    todo = bool(manifiesto["compilados"]) and all(m["en_verde"] for m in manifiesto["compilados"])
    informe.append(f"## Resultado: {'EN VERDE' if todo else 'EN ROJO'}\n")
    manifiesto["en_verde"] = todo
    (salida / "puerta-formatos.md").write_text("\n".join(informe), encoding="utf-8")
    (salida / "textos.md").write_text("\n".join(textos), encoding="utf-8")
    (salida / "manifiesto.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifiesto


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.editar.compilado")
    ap.add_argument("--muestras", action="store_true", help="renderiza los compilados de marca/muestras/formatos.yml")
    ap.add_argument("--salida", default=str(RAIZ.parent / "salida-formatos"),
                    help="carpeta de salida, fuera del repo (por defecto ../salida-formatos)")
    ap.add_argument("--descargas", default=None, help="carpeta de caché de las descargas (por defecto temporal)")
    args = ap.parse_args(argv)
    if not args.muestras:
        ap.error("por ahora sólo existe --muestras")
    salida = Path(args.salida).resolve()
    if _dentro_del_repo(salida):
        print(f"ERROR: la salida {salida} está dentro del repo; no se escribe video en el árbol.", file=sys.stderr)
        return 2
    salida.mkdir(parents=True, exist_ok=True)
    marca = Marca.cargar()
    with tempfile.TemporaryDirectory(prefix="descargas-") as tmp:
        descargas = Path(args.descargas) if args.descargas else Path(tmp)
        if _dentro_del_repo(descargas):
            print("ERROR: las descargas no pueden estar dentro del repo.", file=sys.stderr)
            return 2
        descargas.mkdir(parents=True, exist_ok=True)
        try:
            manifiesto = generar_muestras(salida, marca, cargar_muestras(), descargas)
        except (RuntimeError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    for m in manifiesto["compilados"]:
        if "archivo" in m:
            print(f"{m['id']}: {m['archivo']} {m['duracion_s']} s, puerta {'EN VERDE' if m['en_verde'] else 'EN ROJO'}")
        else:
            print(f"{m['id']}: EN ROJO ({'; '.join(m['motivos'])})")
    return 0 if manifiesto["en_verde"] else 1


if __name__ == "__main__":
    sys.exit(main())

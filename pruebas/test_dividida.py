"""Tarea #16 `dividida`: pantalla dividida automática (facecam arriba, juego abajo).

La lógica de detección se prueba con un detector de caras falso inyectado y videos sintéticos de ffmpeg (sin red ni
modelos); las pruebas pesadas van con `@pytest.mark.slow` como las demás.
"""
import copy
import itertools
import json
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from conftest import RAIZ_REAL
from fabrica import plazo, validar
from fabrica.editar import componer, dividida, encuadre, estilo, lienzo, muestras, placas
from fabrica.editar.marca import Marca

hay_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
necesita_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")

TOMA = dict(numero=7, gancho="Ella busca a un dragón", palabra_lapiz="DRAGÓN", lapiz="círculo",
            escena="INT. CABAÑA — NOCHE", credito="Con: Blender Foundation (CC BY 3.0)")
LINEAS = [componer.Linea(0.5, 2.5, "CHAMÁN", "¿Qué te trae por aquí?"),
          componer.Linea(2.6, 3.4, None, None, "(silencio)")]
DUR = 4.0


@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


@pytest.fixture(scope="module")
def aj(marca):
    return dividida.ajustes_de(marca)


def malos(puntos):
    return [n for n, ok, _ in puntos if not ok and "Duración de 7 a 30" not in n]


# ---------------------------------------------------------------- fuentes sintéticas y detectores falsos

def generar_stream(destino, trabajo, *, facecam=True, estorbos=True, seg=6, audio=True):
    """«Stream» 1280x720 de `seg` s: un fondo en movimiento y, según se pida, una facecam con marco arriba a la derecha
    (cuadro de 900..1260 x 28..228), un logo fijo y denso arriba a la izquierda... (chat a la izquierda que se mueve,
    logo abajo a la derecha y una alerta de 1 s arriba). Con audio: un tono de 440 Hz."""
    trabajo.mkdir(parents=True, exist_ok=True)
    fuente = RAIZ_REAL / "marca" / "fuentes" / "CourierPrime-Bold.ttf"
    f, fg = ImageFont.truetype(str(fuente), 20), ImageFont.truetype(str(fuente), 34)
    logo = Image.new("RGBA", (170, 96), (15, 15, 25, 255))
    d = ImageDraw.Draw(logo)
    for i in range(0, 170, 17):
        d.rectangle((i, 0, i + 8, 95), fill=(240, 200, 40, 255))
    d.rectangle((20, 30, 150, 66), fill=(15, 15, 25, 255))
    d.text((85, 48), "GRANJA TV", font=f, fill="white", anchor="mm")
    logo.save(trabajo / "logo.png")
    chat = Image.new("RGBA", (240, 1500), (10, 10, 14, 220))
    d = ImageDraw.Draw(chat)
    for i in range(44):
        d.text((8, 6 + i * 32), f"ana{i}: jajaja que bien", font=f, fill=(245, 245, 245, 255))
    chat.save(trabajo / "chat.png")
    aviso = Image.new("RGBA", (480, 110), (20, 20, 30, 255))
    d = ImageDraw.Draw(aviso)
    d.rectangle((0, 0, 479, 109), outline=(240, 200, 40, 255), width=5)
    d.text((240, 40), "NUEVO SEGUIDOR", font=fg, fill="white", anchor="mm")
    d.text((240, 82), "gracias por pasar", font=f, fill="white", anchor="mm")
    aviso.save(trabajo / "alerta.png")
    marco = Image.new("RGBA", (376, 216), (0, 0, 0, 0))
    ImageDraw.Draw(marco).rectangle((0, 0, 375, 215), outline=(235, 235, 235, 255), width=8)
    marco.save(trabajo / "marco.png")
    entradas = ["-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seg}"]
    filt, ult, i = ["[0:v]gblur=sigma=10[f0]"], "f0", 1

    def capa(png, x, y, extra=""):
        nonlocal i, ult
        entradas.extend(["-loop", "1", "-framerate", "30", "-t", str(seg), "-i", str(png)])
        filt.append(f"[{ult}][{i}:v]overlay={x}:{y}{extra}[g{i}]")
        ult, i = f"g{i}", i + 1
    if facecam:
        entradas.extend(["-f", "lavfi", "-i", f"testsrc2=size=360x200:rate=30:duration={seg}"])
        filt.append(f"[{ult}][{i}:v]overlay=900:28[g{i}]")
        ult, i = f"g{i}", i + 1
        capa(trabajo / "marco.png", 892, 20)
    if estorbos:
        capa(trabajo / "logo.png", 1086, 600)
        capa(trabajo / "chat.png", 16, "'-mod(t*60,780)'", ":eval=frame")
        capa(trabajo / "alerta.png", 300, 40, ":enable='between(t,2,3)'")
    filt.append(f"[{ult}]format=yuv420p[out]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entradas]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seg}"]
    cmd += ["-filter_complex", ";".join(filt), "-map", "[out]"]
    cmd += ["-map", f"{i}:a", "-c:a", "aac"] if audio else ["-an"]
    subprocess.run([*cmd, "-t", str(seg), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(destino)], check=True)
    return destino


def cara_en(cx, cy, w):
    """Detector falso: una cara cuadrada de lado `w` (fracción del ancho) centrada en (cx, cy) (fracciones)."""
    def detector(gris):
        alto, ancho = gris.shape
        lado = int(w * ancho)
        return [(int(cx * ancho - lado / 2), int(cy * alto - lado / 2), lado, lado)]
    return detector


def sin_cara(gris):
    return []


def cara_que_salta():
    """Detector falso: la cara cambia de esquina en cada fotograma."""
    sitios = itertools.cycle([(0.1, 0.15), (0.9, 0.15), (0.1, 0.85), (0.9, 0.85), (0.5, 0.15), (0.5, 0.85)])

    def detector(gris):
        cx, cy = next(sitios)
        return cara_en(cx, cy, 0.06)(gris)
    return detector


def detectar(ruta, aj, detector, ancho=1280, alto=720, dur=6.0):
    return dividida.detectar(ruta, 0.0, dur, ancho, alto, ajustes=aj, detector=detector)


def toca(a, b):
    return (a["x"] < b["x"] + b["ancho"] and b["x"] < a["x"] + a["ancho"]
            and a["y"] < b["y"] + b["alto"] and b["y"] < a["y"] + a["alto"])


@pytest.fixture(scope="module")
def fondo(tmp_path_factory):
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    return generar_stream(tmp_path_factory.mktemp("fondo") / "fondo.mp4", tmp_path_factory.mktemp("w1"),
                          facecam=False, estorbos=False)


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    return generar_stream(tmp_path_factory.mktemp("stream") / "stream.mp4", tmp_path_factory.mktemp("w2"))


# la cara de la facecam del stream: centro (1080, 128) de 1280x720
CARA_STREAM = (1080 / 1280, 128 / 720, 0.05)


# ---------------------------------------------------------------- criterio 1: detectar

@necesita_ffmpeg
def test_sin_cara_es_completa(fondo, aj):
    det = detectar(fondo, aj, sin_cara)
    assert det["modo"] == "completa" and "cara" in det["motivo"]
    assert det["facecam"] is None and det["juego"] is None and det["estorbos"] == []


@necesita_ffmpeg
@pytest.mark.parametrize("cx,cy", [(0.1, 0.15), (0.9, 0.15), (0.1, 0.85), (0.9, 0.85)])
def test_cara_pequena_estable_en_una_esquina_es_dividida_con_la_facecam_ahi(fondo, aj, cx, cy):
    det = detectar(fondo, aj, cara_en(cx, cy, 0.06))
    assert det["modo"] == "dividida", det["motivo"]
    fc, cara, juego = det["facecam"], det["cara"], det["juego"]
    centro_x, centro_y = fc["x"] + fc["ancho"] / 2, fc["y"] + fc["alto"] / 2
    assert (centro_x > 640) == (cx > 0.5) and (centro_y > 360) == (cy > 0.5)       # la facecam está en esa esquina
    assert cara["x"] >= fc["x"] and cara["y"] >= fc["y"]
    assert cara["x"] + cara["ancho"] <= fc["x"] + fc["ancho"] and cara["y"] + cara["alto"] <= fc["y"] + fc["alto"]
    assert not toca(juego, fc)
    assert juego["alto"] >= aj["min_alto_juego"] * 720
    assert abs(juego["ancho"] / juego["alto"] - 1080 / 1248) < 0.01
    assert juego["x"] >= 0 and juego["y"] >= 0 and juego["x"] + juego["ancho"] <= 1280 and juego["y"] + juego["alto"] <= 720
    assert det == detectar(fondo, aj, cara_en(cx, cy, 0.06))                         # determinista


@necesita_ffmpeg
def test_cara_grande_en_el_centro_es_completa(fondo, aj):
    det = detectar(fondo, aj, cara_en(0.5, 0.5, 0.5))
    assert det["modo"] == "completa" and "grande" in det["motivo"]
    det = detectar(fondo, aj, cara_en(0.5, 0.5, 0.06))                  # pequeña pero en el centro: tampoco es facecam
    assert det["modo"] == "completa" and "centro" in det["motivo"]


@necesita_ffmpeg
def test_cara_que_salta_de_sitio_es_completa(fondo, aj):
    det = detectar(fondo, aj, cara_que_salta())
    assert det["modo"] == "completa" and "salta" in det["motivo"]


@necesita_ffmpeg
def test_cara_en_pocos_fotogramas_es_completa(fondo, aj):
    cuenta = itertools.count()

    def a_ratos(gris):
        return cara_en(0.9, 0.15, 0.06)(gris) if next(cuenta) % 4 == 0 else []
    det = detectar(fondo, aj, a_ratos)
    assert det["modo"] == "completa" and "fotogramas" in det["motivo"]


@necesita_ffmpeg
def test_sin_cv2_es_completa_con_motivo(fondo, aj, monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)                       # import cv2 -> ImportError
    det = dividida.detectar(fondo, 0.0, 6.0, 1280, 720, ajustes=aj)
    assert det["modo"] == "completa" and "cv2" in det["motivo"]


def test_cv2_sin_el_clasificador_haar_es_completa_con_motivo(aj, tmp_path, monkeypatch):
    class Falso:                                                         # un OpenCV 5 sin los XML
        __version__ = "5.0.0"
    monkeypatch.setitem(sys.modules, "cv2", Falso())
    det = dividida.detectar(tmp_path / "x.mp4", 0.0, 6.0, 1280, 720, ajustes=aj)
    assert det["modo"] == "completa" and "Haar" in det["motivo"]


def test_lectura_fallida_o_sin_plazo_es_completa_y_nunca_rompe(aj, tmp_path, monkeypatch):
    det = dividida.detectar(tmp_path / "no-existe.mp4", 0.0, 6.0, 1280, 720, ajustes=aj, detector=cara_en(0.9, 0.1, 0.05))
    assert det["modo"] == "completa" and det["motivo"]
    monkeypatch.setattr(plazo, "queda", lambda s: False)
    det = dividida.detectar(tmp_path / "no-existe.mp4", 0.0, 6.0, 1280, 720, ajustes=aj, detector=cara_en(0.9, 0.1, 0.05))
    assert det["modo"] == "completa" and "plazo" in det["motivo"]
    monkeypatch.undo()

    def revienta(gris):
        raise RuntimeError("detector roto")
    det = dividida.detectar(tmp_path / "x.mp4", 0.0, 6.0, 1280, 720, ajustes=aj, detector=revienta)
    assert det["modo"] == "completa"


def test_haar_del_paquete_carga_y_no_ve_cara_en_una_imagen_vacia(aj):
    pytest.importorskip("cv2")
    detector = dividida.detector_haar(aj)
    assert detector(np.full((360, 640), 128, dtype=np.uint8)) == []


@necesita_ffmpeg
@pytest.mark.slow
def test_la_deteccion_tarda_pocos_segundos(stream, aj):
    import time
    t0 = time.monotonic()
    det = detectar(stream, aj, cara_en(*CARA_STREAM))
    assert det["modo"] == "dividida"
    assert time.monotonic() - t0 < 8


# ---------------------------------------------------------------- criterio 2: estorbos

@necesita_ffmpeg
def test_logo_chat_y_alerta_salen_en_estorbos_y_el_juego_no_los_toca(stream, aj):
    det = detectar(stream, aj, cara_en(*CARA_STREAM))
    assert det["modo"] == "dividida", det["motivo"]
    tipos = {e["tipo"] for e in det["estorbos"]}
    assert {"estatico", "chat", "alerta"} <= tipos, det["estorbos"]
    logo = {"x": 1086, "y": 600, "ancho": 170, "alto": 96}
    chat = {"x": 16, "y": 0, "ancho": 240, "alto": 720}
    alerta = {"x": 300, "y": 40, "ancho": 480, "alto": 110}
    for tipo, real in (("estatico", logo), ("chat", chat), ("alerta", alerta)):
        assert any(e["tipo"] == tipo and toca(e, real) for e in det["estorbos"]), (tipo, det["estorbos"])
    for e in det["estorbos"]:
        assert not toca(det["juego"], e), (det["juego"], e)
    assert not toca(det["juego"], det["facecam"])
    assert not toca(det["facecam"], logo) and not toca(det["facecam"], chat)


@necesita_ffmpeg
def test_si_el_juego_casi_no_se_mueve_no_se_marcan_estorbos_fijos(tmp_path, aj):
    est = generar_stream(tmp_path / "e.mp4", tmp_path / "w", facecam=False, estorbos=True, seg=6, audio=False)
    quieto = tmp_path / "q.mp4"      # un solo fotograma fijo, repetido: ni juego en movimiento ni chat desplazándose
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(est), "-frames:v", "1",
                    str(tmp_path / "f.png")], check=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-t", "6",
                    "-i", str(tmp_path / "f.png"), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(quieto)], check=True)
    det = detectar(quieto, aj, cara_en(0.9, 0.12, 0.05))
    assert all(e["tipo"] != "estatico" for e in det["estorbos"])


# ---------------------------------------------------------------- geometría y orden de ffmpeg (puras)

def det_de_mano():
    return {"modo": "dividida", "motivo": "", "cara": dividida.caja(1000, 70, 80, 80),
            "facecam": dividida.caja(900, 28, 360, 200), "estorbos": [], "juego": dividida.caja(100, 0, 622, 720)}


def test_paneles_cubren_el_cuadro_y_la_cara_va_entera_arriba(marca):
    g = dividida.paneles(marca, det_de_mano(), 1280, 720)
    pf, pj = g["paneles"]
    assert pf["nombre"] == "facecam" and pj["nombre"] == "juego"
    assert pf["destino"] == {"x": 0, "y": 0, "ancho": 1080, "alto": 672}
    assert pj["destino"] == {"x": 0, "y": 672, "ancho": 1080, "alto": 1248}
    assert dividida.problemas_paneles({"paneles": g["paneles"]}, marca, 1280, 720) == []
    for p in g["paneles"]:
        r = p["recorte"]
        assert r["ancho"] % 2 == 0 and r["alto"] % 2 == 0 and r["x"] % 2 == 0
        assert r["escala"] >= max(p["destino"]["ancho"] / r["ancho"], p["destino"]["alto"] / r["alto"]) - 1e-3
    c = g["cara"]
    assert 0 <= c["x"] and c["x"] + c["ancho"] <= 1080 and 0 <= c["y"] and c["y"] + c["alto"] <= 672
    centro_y = (c["y"] + c["alto"] / 2) / 672
    assert abs(centro_y - marca.datos["dividida"]["cara_centro_y"]) < 0.08           # centrada según marca.yml


def test_cara_que_no_cabe_entera_en_el_panel_no_se_divide(marca):
    det = det_de_mano()
    det["cara"] = dividida.caja(900, 28, 360, 200)                   # la cara llena toda la facecam
    with pytest.raises(ValueError, match="cara"):
        dividida.paneles(marca, det, 1280, 720)


def test_orden_de_ffmpeg_con_paneles_vstack_y_overlay(marca):
    g = dividida.paneles(marca, det_de_mano(), 1280, 720)
    video = {"ruta": "v.mp4", "inicio": 3.0, "audio": True, "recorte": None,
             "paneles": [{"recorte": p["recorte"], "destino": p["destino"]} for p in g["paneles"]]}
    cmd = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=8.0, ancho=1080, alto=1920, video=video,
                                                  placas=[("a.png", 60, 150, 0.0, 8.0)])
    filtro = cmd[cmd.index("-filter_complex") + 1]
    rf, rj = g["paneles"][0]["recorte"], g["paneles"][1]["recorte"]
    assert filtro == (
        f"[0:v]fps=30,split=2[s0][s1];"
        f"[s0]crop={rf['ancho']}:{rf['alto']}:{rf['x']}:{rf['y']},scale=1080:672:flags=lanczos,setsar=1[p0];"
        f"[s1]crop={rj['ancho']}:{rj['alto']}:{rj['x']}:{rj['y']},scale=1080:1248:flags=lanczos,setsar=1[p1];"
        f"[p0][p1]vstack=inputs=2[c0];"
        f"[c0][1:v]overlay=60:150:enable='gte(t,0)*lt(t,8)'[c1];[c1]format=yuv420p[out]")
    assert cmd[cmd.index("-map") + 3] == "0:a:0"                         # el audio es el original, tal cual
    # paneles con hueco: no se apilan; van con overlay sobre un fondo
    sueltos = copy.deepcopy(video["paneles"])
    sueltos[1]["destino"]["y"] += 40
    f2 = componer._filtros_paneles(sueltos, 1080, 1920)
    assert not any("vstack" in x for x in f2) and any("overlay=0:712" in x for x in f2)
    with pytest.raises(ValueError):
        componer._filtros_paneles(sueltos[:1], 1080, 1920)


def test_sin_paneles_la_orden_es_identica_a_la_de_main():
    """No regresión: la orden de la pantalla completa de la #5, byte a byte."""
    video = {"ruta": "v.mp4", "inicio": 3.0, "audio": True, "recorte": {"x": 218, "y": 0, "ancho": 202, "alto": 360}}
    cmd = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=8.0, ancho=1080, alto=1920, video=video,
                                                  placas=[("a.png", 60, 150, 0.0, 8.0), ("b.png", 238, 1420, 7.5, 8.0)])
    assert cmd == [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "3", "-t", "8", "-i", "v.mp4",
        "-loop", "1", "-framerate", "30", "-t", "8", "-i", "a.png", "-loop", "1", "-framerate", "30", "-t", "8", "-i", "b.png",
        "-filter_complex",
        "[0:v]fps=30,crop=202:360:218:0,scale=1080:1920:flags=lanczos,setsar=1[c0];"
        "[c0][1:v]overlay=60:150:enable='gte(t,0)*lt(t,8)'[c1];"
        "[c1][2:v]overlay=238:1420:enable='gte(t,7.5)*lt(t,8)'[c2];[c2]format=yuv420p[out]",
        "-map", "[out]", "-map", "0:a:0", "-t", "8", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", "s.mp4"]


# ---------------------------------------------------------------- composición con facecam

def componer_con(marca, ruta, tmp, nombre, detector, *, ficha=None, inicio=0.0, mp):
    mp.setattr(dividida, "detector_haar", lambda aj: detector)
    mp4 = tmp / f"{nombre}.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=DUR, trabajo=tmp / f"w-{nombre}",
                             fuente_video=ruta, inicio=inicio, ficha=ficha)
    return mp4, plan


@pytest.fixture(scope="module")
def div(stream, marca, tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        tmp = tmp_path_factory.mktemp("div")
        mp4, plan = componer_con(marca, stream, tmp, "div", cara_en(*CARA_STREAM), mp=mp)
    return {"mp4": mp4, "plan": plan, "fuente": stream, "tmp": tmp}


@necesita_ffmpeg
@pytest.mark.slow
def test_con_facecam_el_mp4_es_1080x1920_con_facecam_arriba_y_juego_abajo(div, marca):
    plan = div["plan"]
    info = estilo.sondear(div["mp4"])
    assert (info["video"]["width"], info["video"]["height"]) == (1080, 1920)
    geo = plan["geometria"]
    assert plan["plantilla"] == "v2-pantalla-completa" and geo["disposicion"] == "dividida"
    assert geo["cuerpo"] == {"x": 0, "y": 0, "ancho": 1080, "alto": 1920} and "recorte" not in geo
    pf, pj = geo["paneles"]
    alto_f = marca.datos["dividida"]["alto_facecam"]
    assert pf["destino"] == {"x": 0, "y": 0, "ancho": 1080, "alto": alto_f}
    assert pj["destino"] == {"x": 0, "y": alto_f, "ancho": 1080, "alto": 1920 - alto_f}
    assert geo["cara"] and geo["deteccion"]["modo"] == "dividida" and geo["posicion_placas"] in ("estandar", "alterna")
    ok, det = estilo.pantalla_completa(plan, marca)
    assert ok, det
    ok, det = estilo.bordes_coinciden(div["mp4"], plan, marca)                  # sin franjas
    assert ok, det


@necesita_ffmpeg
@pytest.mark.slow
def test_el_panel_de_arriba_es_la_facecam_y_el_de_abajo_la_zona_del_juego(div, marca):
    """Diferencia baja en píxeles contra la fuente, fuera de las placas."""
    plan = div["plan"]
    geo = plan["geometria"]
    t = 1.5
    real = estilo._fotograma(div["mp4"], t)
    fuente = estilo._fotograma(div["fuente"], t)
    libre = estilo._mascara_placas(plan, t, marca)
    gris = lambda im: np.asarray(im.convert("L"), dtype=np.float32)
    for p in geo["paneles"]:
        r, d = p["recorte"], p["destino"]
        esperado = fuente.crop((r["x"], r["y"], r["x"] + r["ancho"], r["y"] + r["alto"])).resize(
            (d["ancho"], d["alto"]), Image.LANCZOS)
        zona = (slice(d["y"], d["y"] + d["alto"]), slice(0, d["ancho"]))
        a, b = gris(real)[zona], gris(esperado)
        dif = np.abs(a - b)[libre[zona]].mean()
        assert dif < 3.0, (p["nombre"], dif)
    # el de arriba es la facecam de la fuente (su caja detectada) y el de abajo no la toca
    rf, rj = geo["paneles"][0]["recorte"], geo["paneles"][1]["recorte"]
    fc = geo["deteccion"]["facecam"]
    assert fc["x"] - 4 <= rf["x"] and rf["x"] + rf["ancho"] <= fc["x"] + fc["ancho"] + 4
    assert not toca(rj, fc)


@necesita_ffmpeg
@pytest.mark.slow
def test_la_puerta_sale_en_verde_en_el_dividido_correcto(div, marca):
    puntos = estilo.revisar(div["mp4"], div["plan"], marca)
    assert malos(puntos) == [], [p for p in puntos if not p[1] and "Duración de 7 a 30" not in p[0]]
    nombres = [n for n, _, _ in puntos]
    for n in ("[plan] Pantalla dividida (facecam arriba, juego abajo)",
              "[plan] Pantalla completa (cuerpo 1080x1920, sin franjas)", "[fotograma] Cuerpo coincide con el origen",
              "[fotograma] Bordes coinciden con la fuente (sin franjas)", "[fotograma] Audio original del clip"):
        assert n in nombres


@necesita_ffmpeg
@pytest.mark.slow
def test_audio_original_intacto_en_las_dos_disposiciones(div, fondo, marca, tmp_path):
    for mp4, plan in ((div["mp4"], div["plan"]),):
        assert plan["audio"] == {"tipo": "original"}
        ok, det = estilo.audio_original(mp4, plan)
        assert ok, det
        assert float(re.search(r"correlación con el audio de la fuente ([0-9.]+)", det).group(1)) > 0.9
    with pytest.MonkeyPatch.context() as mp:
        src = generar_stream(tmp_path / "f.mp4", tmp_path / "wf", facecam=False, estorbos=False, seg=6)
        mp4, plan = componer_con(marca, src, tmp_path, "completa", sin_cara, mp=mp)
    assert plan["geometria"]["disposicion"] == "completa"
    ok, det = estilo.audio_original(mp4, plan)
    assert ok, det
    assert float(re.search(r"correlación con el audio de la fuente ([0-9.]+)", det).group(1)) > 0.9


# ---------------------------------------------------------------- criterio 4: sin facecam, idéntica a main

@necesita_ffmpeg
@pytest.mark.slow
def test_sin_facecam_el_plan_y_el_mp4_son_los_de_pantalla_completa(marca, tmp_path):
    datos = copy.deepcopy(marca.datos)
    datos["dividida"]["activa"] = False
    apagada = Marca(datos, marca.raiz)
    src = generar_stream(tmp_path / "f.mp4", tmp_path / "wf", facecam=False, estorbos=False, seg=6)
    with pytest.MonkeyPatch.context() as mp:
        mp4_a, plan_a = componer_con(marca, src, tmp_path, "a", sin_cara, mp=mp)          # activa, pero sin facecam
        mp4_b, plan_b = componer_con(apagada, src, tmp_path, "b", sin_cara, mp=mp)       # la de main
    geo_a = plan_a["geometria"]
    assert geo_a["disposicion"] == "completa" and geo_a["deteccion"]["modo"] == "completa"
    assert "disposicion" not in plan_b["geometria"] and "deteccion" not in plan_b["geometria"]
    quitar = lambda p: {**p, "geometria": {k: v for k, v in p["geometria"].items() if k not in ("disposicion", "deteccion")}}
    sin_a, sin_b = quitar(plan_a), quitar(plan_b)
    for p in (sin_a, sin_b):
        p["origen"] = {**p["origen"]}
    assert sin_a == sin_b
    assert list(plan_b["geometria"]) == ["cuerpo", "recorte", "capas"]            # mismas claves y orden que en main
    assert componer.sha256_archivo(mp4_a) == componer.sha256_archivo(mp4_b)         # el mismo MP4
    assert malos(estilo.revisar(mp4_a, plan_a, marca)) == []


@necesita_ffmpeg
@pytest.mark.slow
def test_la_ficha_con_dividida_false_apaga_la_division(stream, marca, tmp_path):
    with pytest.MonkeyPatch.context() as mp:
        _, plan = componer_con(marca, stream, tmp_path, "f", cara_en(*CARA_STREAM),
                               ficha={"id": "x", "reglas": {"dividida": False}}, mp=mp)
    assert "disposicion" not in plan["geometria"] and "recorte" in plan["geometria"]
    assert dividida.activa(marca, {"reglas": {"dividida": False}}) is False and dividida.activa(marca, None) is True


# ---------------------------------------------------------------- criterio 6: placas

@necesita_ffmpeg
@pytest.mark.slow
def test_ninguna_placa_toca_la_cara_ni_la_zona_de_accion_de_la_division(div, marca):
    geo = div["plan"]["geometria"]
    accion = marca.datos["dividida"]["zona_accion"]
    assert placas.problemas_capas(geo["capas"], marca, accion) == []              # incluye el tope de área de las capas
    assert dividida.problemas_placas(geo["capas"], geo["cara"], marca) == []
    assert accion["y0"] >= marca.datos["dividida"]["alto_facecam"]                # la zona está en el panel del juego
    for c in geo["capas"]:
        assert not (c["x"] < accion["x1"] and c["x"] + c["ancho"] > accion["x0"]
                    and c["y"] < accion["y1"] and c["y"] + c["alto"] > accion["y0"]), c["tipo"]
    # los textos de siempre
    assert geo["capas"][1]["tipo"] == "cabecera" and \
        any(c["tipo"] == "cierre" for c in geo["capas"]) == placas.cierre_con_placa(marca)   # estilo v3: destello
    assert div["plan"]["toma"]["gancho"] == TOMA["gancho"]


@necesita_ffmpeg
@pytest.mark.slow
def test_si_la_posicion_de_siempre_toca_la_cara_las_placas_van_a_la_alterna(div):
    # La cara de la fuente del stream queda arriba; en el cuadro la fila de siempre (y=150) la tocaría.
    assert div["plan"]["geometria"]["posicion_placas"] == "alterna"
    fila = next(c for c in div["plan"]["geometria"]["capas"] if c["tipo"] == "fila")
    assert fila["y"] == 690


@necesita_ffmpeg
@pytest.mark.slow
def test_sin_posicion_valida_para_las_placas_no_hay_division(stream, marca, tmp_path):
    datos = copy.deepcopy(marca.datos)
    datos["dividida"]["placas_alterna"]["y_fila"] = 150                   # la alterna es la misma que la de siempre
    with pytest.MonkeyPatch.context() as mp:
        _, plan = componer_con(Marca(datos, marca.raiz), stream, tmp_path, "s", cara_en(*CARA_STREAM), mp=mp)
    geo = plan["geometria"]
    assert geo["disposicion"] == "completa" and "recorte" in geo
    assert "ninguna posición" in geo["deteccion"]["motivo"] and "toca la cara" in geo["deteccion"]["motivo"]


# ---------------------------------------------------------------- criterio 7: la puerta en rojo

@necesita_ffmpeg
@pytest.mark.slow
def test_rojo_paneles_con_hueco_o_franja(div, marca, tmp_path):
    plan = copy.deepcopy(div["plan"])
    plan["geometria"]["paneles"][0]["destino"]["alto"] = 600               # hueco de 72 px entre los paneles
    ok, det = estilo.pantalla_completa(plan, marca)
    assert not ok and "hueco" in det
    assert "[plan] Pantalla completa (cuerpo 1080x1920, sin franjas)" in malos(estilo.revisar(div["mp4"], plan, marca))
    plan = copy.deepcopy(div["plan"])
    plan["geometria"]["paneles"][1]["destino"]["y"] = 600                  # solape
    assert "solapa" in estilo.pantalla_completa(plan, marca)[1]
    # una franja negra de verdad en el MP4
    franja = tmp_path / "franja.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(div["mp4"]), "-vf",
                    "drawbox=x=0:y=0:w=iw:h=60:color=black:t=fill", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", str(franja)], check=True)
    assert "[fotograma] Bordes coinciden con la fuente (sin franjas)" in malos(estilo.revisar(franja, div["plan"], marca))


@necesita_ffmpeg
@pytest.mark.slow
def test_rojo_panel_de_arriba_con_otra_zona_de_la_fuente(stream, marca, tmp_path, monkeypatch):
    original = dividida.paneles

    def torcido(m, det, ancho_v, alto_v):
        g = original(m, det, ancho_v, alto_v)
        r = g["paneles"][0]["recorte"]
        g["paneles"][0]["recorte"] = {**r, "x": 300, "y": 400}                # otra zona: el juego, no la facecam
        return g
    monkeypatch.setattr(dividida, "paneles", torcido)
    mp4, plan = componer_con(marca, stream, tmp_path, "torcido", cara_en(*CARA_STREAM), mp=monkeypatch)
    assert plan["geometria"]["disposicion"] == "dividida"
    puntos = estilo.revisar(mp4, plan, marca)
    assert "[plan] Pantalla dividida (facecam arriba, juego abajo)" in malos(puntos)
    detalle = next(d for n, ok, d in puntos if n.startswith("[plan] Pantalla dividida"))
    assert "no es la facecam" in detalle
    # el plan correcto contra ese MP4 (video distinto) tampoco pasa
    assert "[fotograma] Cuerpo coincide con el origen" in malos(
        estilo.revisar(mp4, json.loads(json.dumps({**plan, "geometria": {
            **plan["geometria"], "paneles": [{**plan["geometria"]["paneles"][0], "recorte": {
                **plan["geometria"]["paneles"][0]["recorte"], "x": 1000, "y": 28}}, plan["geometria"]["paneles"][1]]}})),
                       marca))


@necesita_ffmpeg
@pytest.mark.slow
def test_rojo_placa_sobre_la_cara(div, marca):
    plan = copy.deepcopy(div["plan"])
    cara = plan["geometria"]["cara"]
    fila = next(c for c in plan["geometria"]["capas"] if c["tipo"] == "fila")
    fila["x"], fila["y"] = cara["x"], cara["y"]
    puntos = estilo.revisar(div["mp4"], plan, marca)
    assert "[plan] Pantalla dividida (facecam arriba, juego abajo)" in malos(puntos)
    assert any("toca la cara" in d for n, ok, d in puntos if n.startswith("[plan] Pantalla dividida"))


@necesita_ffmpeg
@pytest.mark.slow
def test_rojo_casos_de_la_5_una_letra_y_audio_en_silencio(div, marca, tmp_path):
    plan = copy.deepcopy(div["plan"])
    plan["toma"]["gancho"] = "Ella busca a un dragon"                         # una letra menos (la tilde)
    plan["toma"]["gancho"] = plan["toma"]["gancho"].replace("dragon", "dragom")
    assert any("Cabecera" in n or "Capas" in n or "Cuerpo" in n for n in malos(estilo.revisar(div["mp4"], plan, marca)))
    plan = copy.deepcopy(div["plan"])
    plan["toma"]["numero"] = 8
    assert "[fotograma] Cabecera en el video" in malos(estilo.revisar(div["mp4"], plan, marca))
    mudo = tmp_path / "silencio.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(div["mp4"]), "-f", "lavfi", "-t", "4",
                    "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-movflags", "+faststart", str(mudo)], check=True)
    rojos = malos(estilo.revisar(mudo, div["plan"], marca))
    # además de la puerta de audio, la regla de Kevin «toda toma se mueve y se oye» (#17) lo marca
    assert rojos[0] == "[fotograma] Audio original del clip" and len(rojos) == 2 and rojos[1].startswith("[regla-kevin] muy-simple")


# ---------------------------------------------------------------- marca.yml, validar y muestras

def test_marca_yml_declara_el_bloque_dividida_y_validar_lo_exige(marca, repo, editar_yaml):
    d = marca.datos["dividida"]
    assert d["activa"] is True and d["alto_facecam"] == 672 and d["zona_accion"]["y0"] >= d["alto_facecam"]
    assert validar.validar(RAIZ_REAL).errores == []
    editar_yaml(repo, "marca.yml", lambda x: {k: v for k, v in x.items() if k != "dividida"})
    assert any("falta la sección dividida" in e for e in validar.validar(repo).errores)


def test_validar_zona_de_accion_de_la_division_en_el_panel_del_juego(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda x: x["dividida"]["zona_accion"].update(y0=300))
    assert any("panel del juego" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "marca.yml", lambda x: (x["dividida"]["deteccion"].pop("rejilla"), None)[1])
    assert any("falta la clave 'rejilla'" in e for e in validar.validar(repo).errores)


def test_el_codigo_de_la_division_no_lleva_medidas_ni_colores_a_mano():
    for nombre in ("dividida.py",):
        texto = (RAIZ_REAL / "fabrica" / "editar" / nombre).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", texto), nombre
        assert not re.search(r"\b(1080|1920|672|1248)\b", texto), nombre


def test_las_muestras_son_las_3_de_siempre_mas_3_divididas(marca):
    todas = muestras.cargar_muestras()
    assert [m["id"] for m in todas][:3] == ["dialogo-sintel", "momento-bbb", "pajaro-bbb"]
    divididas = [m for m in todas if m.get("disposicion") == "dividida"]
    assert len(todas) == 9 and len(divididas) == 3 and todas[-1]["id"] == "voces-sintel" and all(m["disposicion"] == "completa" for m in todas[:3])
    assert len({m["stream"]["esquina_facecam"] for m in divididas}) == 3          # una esquina distinta en cada una
    assert sum(1 for m in divididas if m["stream"]["alerta_s"] is not None) == 1  # y una sola con alerta
    for m in divididas:
        assert m["fuente_id"] == "sintel-cc-by" and m["juego"]["fuente_id"] == "bbb-cc-by"
        lineas = [componer.Linea(**ln) for ln in m["lineas"]]
        for posicion in ("estandar", "alterna"):
            todas_p = placas.construir(marca, lienzo.Toma(**m["toma"]), lineas, float(m["recorte"]["duracion"]), None, posicion)
            assert placas.problemas_capas([p.capa() for p in todas_p], marca) is not None


def test_las_muestras_divididas_se_autorizan_con_las_dos_fuentes(marca):
    from fabrica import fuentes as mod_fuentes
    from datetime import date
    fichas = mod_fuentes.cargar_fuentes()
    for m in muestras.cargar_muestras():
        muestras.autorizar(m, fichas, date(2026, 11, 20))
    m = next(x for x in muestras.cargar_muestras() if x.get("juego"))
    with pytest.raises(RuntimeError, match="bbb-cc-by"):
        muestras.autorizar({**m, "juego": {**m["juego"], "fuente_id": "bbb-cc-by"}}, [f for f in fichas if f["id"] != "bbb-cc-by"],
                           date(2026, 11, 20))


def test_requirements_trae_opencv_con_version_fija():
    texto = (RAIZ_REAL / "requirements.txt").read_text(encoding="utf-8")
    assert "opencv-python-headless==4.13.0.92" in texto.splitlines()
    assert "mediapipe" not in texto


@necesita_ffmpeg
def test_dibujar_la_deteccion_guarda_un_png_con_las_cajas(stream, aj, tmp_path):
    det = detectar(stream, aj, cara_en(*CARA_STREAM))
    cuadro = tmp_path / "c.png"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(stream), "-frames:v", "1",
                    str(cuadro)], check=True)
    dividida.dibujar(Image.open(cuadro), det, tmp_path / "deteccion.png")
    img = Image.open(tmp_path / "deteccion.png")
    assert img.size == (1280, 720)
    px = img.convert("RGB").getpixel((det["cara"]["x"], det["cara"]["y"] + det["cara"]["alto"] // 2))
    assert px == (255, 0, 0)                                                      # el borde rojo de la cara


# ---------------------------------------------------------------- ronda 1: tope de escala, plazo y borde

@necesita_ffmpeg
def test_facecam_muy_chica_es_completa_con_motivo(tmp_path, marca):
    src = generar_stream(tmp_path / "c.mp4", tmp_path / "w", facecam=True, estorbos=False, seg=6, audio=False)
    cfg = copy.deepcopy(marca.datos)
    cfg["dividida"]["max_escala_facecam"] = 2.0               # la facecam de 360 px sube a ~3x: pasa de 2
    det = dividida.detectar(src, 0.0, 6.0, 1280, 720, ajustes=dividida.ajustes_de(marca), detector=cara_en(*CARA_STREAM))
    with pytest.raises(ValueError, match="facecam muy chica: escala"):
        dividida.paneles(Marca(cfg, marca.raiz), det, 1280, 720)
    chica = det_de_mano()
    chica["facecam"] = dividida.caja(1000, 60, 100, 56)
    chica["cara"] = dividida.caja(1030, 70, 30, 30)
    with pytest.raises(ValueError, match="facecam muy chica: escala"):
        dividida.paneles(marca, chica, 1280, 720)


def test_la_puerta_pone_rojo_si_la_escala_de_la_facecam_pasa_el_tope(marca):
    g = dividida.paneles(marca, det_de_mano(), 1280, 720)
    plan = {"geometria": {"paneles": g["paneles"], "deteccion": det_de_mano(), "cara": g["cara"], "capas": []}}
    plan["geometria"]["paneles"][0]["recorte"]["escala"] = 6.0
    assert any("escala 6.00" in m for m in dividida.problemas_division(plan, marca))


def test_sin_tiempo_cooperativo_es_completa_y_la_alarma_dura_sube(aj, tmp_path, monkeypatch):
    def alarma(gris):
        raise plazo.SinTiempo("alarma dura")
    monkeypatch.setattr(encuadre, "_fotogramas_grises", lambda *a, **k: np.zeros((24, 36, 64), dtype=np.float32))
    with pytest.raises(plazo.SinTiempo):
        dividida.detectar(tmp_path / "x.mp4", 0.0, 6.0, 1280, 720, ajustes=aj, detector=alarma)
    monkeypatch.setattr(plazo, "queda", lambda s: False)
    det = dividida.detectar(tmp_path / "x.mp4", 0.0, 6.0, 1280, 720, ajustes=aj, detector=cara_en(0.9, 0.1, 0.05))
    assert det["modo"] == "completa" and "plazo" in det["motivo"]


@necesita_ffmpeg
def test_cara_pegada_al_borde_cae_a_completa_con_motivo(fondo, aj):
    det = detectar(fondo, aj, cara_en(0.97, 0.05, 0.06))              # la caja de la cara toca el borde del cuadro
    if det["modo"] == "completa":
        assert det["motivo"]
    else:                                                               # la holgura de la cara no cabe: no hay división
        pegada = {**det, "cara": dividida.caja(1200, 0, 80, 80), "facecam": dividida.caja(1100, 0, 180, 100)}
        with pytest.raises(ValueError, match="no queda entera"):
            dividida.paneles(Marca.cargar(RAIZ_REAL), pegada, 1280, 720)


def test_humo_detector_haar_real_lee_sus_ajustes(aj):
    """El detector de verdad (Haar) se arma con los ajustes de marca.yml y corre sobre una imagen sin cara.

    Las demás pruebas usan un detector falso: ésta atrapa una clave de ajustes mal leída en la puerta rápida.
    """
    pytest.importorskip("cv2")
    detectar_caras = dividida.detector_haar(aj)
    gris = np.full((360, 640), 128, dtype=np.uint8)
    assert detectar_caras(gris) == []

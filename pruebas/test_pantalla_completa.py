"""Tarea #5 `pantalla-completa`: plantilla v2 (el clip cubre 1080x1920 y la marca va en placas encima)."""
import copy
import json
import re
import shutil
import subprocess

import numpy as np
import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import validar
from fabrica.editar import componer, encuadre, estilo, lienzo, placas
from fabrica.editar.marca import Marca

hay_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
necesita_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")

TOMA = dict(numero=7, gancho="Ella busca a un dragón", palabra_lapiz="DRAGÓN", lapiz="círculo",
            escena="INT. CABAÑA — NOCHE", credito="Con: Blender Foundation (CC BY 3.0)")
LINEAS = [componer.Linea(0.5, 2.5, "CHAMÁN", "¿Qué te trae por aquí?"),
          componer.Linea(2.6, 3.4, None, None, "(silencio)")]


@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


def malos(puntos):
    return [n for n, ok, _ in puntos if not ok and "Duración de 7 a 30" not in n]


def lavfi(ruta, entrada, seg, audio=True, ffmpeg_extra=()):
    """Fuente sintética: `entrada` es un grafo lavfi de video (con -filter_complex si trae etiquetas)."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *ffmpeg_extra]
    cmd += ["-f", "lavfi", "-i", entrada]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seg}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    subprocess.run([*cmd, str(ruta)], check=True)
    return ruta


def fuente(ruta, ancho, alto, seg, audio=True, patron="testsrc"):
    return lavfi(ruta, f"{patron}=size={ancho}x{alto}:rate=30:duration={seg}", seg, audio)


@pytest.fixture(scope="module")
def ancha(tmp_path_factory, marca):
    """Fuente 16:9 de 4 s con tono, compuesta de 0 a 4 s."""
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    base = tmp_path_factory.mktemp("ancha")
    src = fuente(base / "a.mp4", 640, 360, 4)
    mp4 = base / "t.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=base / "w",
                             fuente_video=src, inicio=0.0)
    return {"mp4": mp4, "plan": plan, "fuente": src, "base": base}


# ---------------------------------------------------------------- criterio 1: cuadro completo

@necesita_ffmpeg
def test_16_9_y_9_16_salen_1080x1920_con_cuerpo_de_cuadro_completo(ancha, tmp_path, marca):
    vertical = fuente(tmp_path / "v.mp4", 1080, 1920, 4)
    mp4 = tmp_path / "v-t.mp4"
    plan_v = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=tmp_path / "w",
                               fuente_video=vertical, inicio=0.0)
    for salida, plan, ancho, alto in ((ancha["mp4"], ancha["plan"], 640, 360), (mp4, plan_v, 1080, 1920)):
        info = estilo.sondear(salida)
        assert (info["video"]["width"], info["video"]["height"]) == (1080, 1920)
        assert plan["plantilla"] == "v2-pantalla-completa"
        assert plan["geometria"]["cuerpo"] == {"x": 0, "y": 0, "ancho": 1080, "alto": 1920}
        r = plan["geometria"]["recorte"]
        assert r["escala"] >= max(1080 / ancho, 1920 / alto) - 1e-3
        assert 0 <= r["x"] and 0 <= r["y"] and r["x"] + r["ancho"] <= ancho and r["y"] + r["alto"] <= alto
        ok, det = estilo.pantalla_completa(plan, marca)
        assert ok, det


@necesita_ffmpeg
def test_la_puerta_nueva_sale_en_verde_con_el_video_correcto(ancha, marca):
    puntos = estilo.revisar(ancha["mp4"], ancha["plan"], marca)
    assert malos(puntos) == [], [p for p in puntos if not p[1]]
    nombres = [n for n, _, _ in puntos]
    for n in ("[plan] Pantalla completa (cuerpo 1080x1920, sin franjas)", "[plan] Capas fuera de la zona de acción",
              "[fotograma] Bordes coinciden con la fuente (sin franjas)", "[fotograma] Cuerpo coincide con el origen",
              "[fotograma] Audio original del clip", "[fotograma] Mosca en el video"):
        assert n in nombres


# ---------------------------------------------------------------- criterio 2: elegir_recorte

AJ = Marca.cargar(RAIZ_REAL).pc["recorte"]      # los ajustes de marca.yml, como los pasa la fábrica


def mover_en_el_tercio_derecho(ruta):
    """640x360 negro con un cuadrado blanco que se mueve (x de 470 a 560) dentro del tercio derecho."""
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=6",
                    "-f", "lavfi", "-i", "color=c=white:s=40x40:r=30:d=6",
                    "-filter_complex", "[0:v][1:v]overlay=x='515+45*sin(2*PI*t)':y=160",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(ruta)], check=True)
    return ruta


@necesita_ffmpeg
def test_elegir_recorte_sigue_el_movimiento_del_tercio_derecho(tmp_path):
    src = mover_en_el_tercio_derecho(tmp_path / "mov.mp4")
    r = encuadre.elegir_recorte(src, 0.0, 6.0, 640, 360, 1080, 1920, ajustes=AJ)
    assert r["modo"] == "accion"
    assert r["alto"] == 360 and 200 <= r["ancho"] <= 204
    assert r["x"] <= 470 and r["x"] + r["ancho"] >= 600                 # la ventana contiene el cuadrado en movimiento
    assert r["x"] > (640 - r["ancho"]) // 2                               # y no es la centrada
    assert r == encuadre.elegir_recorte(src, 0.0, 6.0, 640, 360, 1080, 1920, ajustes=AJ)      # determinista


@necesita_ffmpeg
def test_elegir_recorte_estatica_o_uniforme_queda_centrada(tmp_path):
    from PIL import Image
    uniforme = lavfi(tmp_path / "u.mp4", "color=c=gray:s=640x360:r=30:d=5", 5, audio=False)
    Image.effect_noise((640, 360), 40).convert("RGB").save(tmp_path / "ruido.png")
    estatica = tmp_path / "e.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-t", "5",
                    "-i", str(tmp_path / "ruido.png"), "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p",
                    str(estatica)], check=True)
    for src in (uniforme, estatica):
        r = encuadre.elegir_recorte(src, 0.0, 5.0, 640, 360, 1080, 1920, ajustes=AJ)
        assert r["modo"] == "centro"
        assert r["x"] == (640 - r["ancho"]) // 2 // 2 * 2 and r["y"] == 0


def test_elegir_recorte_sin_lectura_o_ya_en_9_16_queda_centrada(tmp_path):
    r = encuadre.elegir_recorte(tmp_path / "no-existe.mp4", 0.0, 5.0, 640, 360, 1080, 1920, ajustes=AJ)
    assert r["modo"] == "centro" and r["alto"] == 360 and r["x"] + r["ancho"] <= 640
    r = encuadre.elegir_recorte(tmp_path / "no-existe.mp4", 0.0, 5.0, 1080, 1920, 1080, 1920, ajustes=AJ)
    assert (r["x"], r["y"], r["ancho"], r["alto"], r["escala"], r["modo"]) == (0, 0, 1080, 1920, 1.0, "centro")
    r = encuadre.elegir_recorte(tmp_path / "no-existe.mp4", 0.0, 5.0, 1000, 1000, 1080, 1920, ajustes=AJ)   # 1:1: sobra ancho
    assert r["alto"] == 1000 and r["ancho"] < 1000 and r["escala"] == 1.92


# ---------------------------------------------------------------- criterio 3: audio original

@necesita_ffmpeg
def test_el_audio_del_mp4_es_el_de_la_fuente(ancha, marca):
    assert ancha["plan"]["audio"] == "original"
    ok, det = estilo.audio_original(ancha["mp4"], ancha["plan"])
    assert ok, det
    assert "correlación" in det and float(re.search(r"correlación con el audio de la fuente ([0-9.]+)", det).group(1)) > 0.9


@necesita_ffmpeg
def test_sin_audio_de_origen_el_mp4_va_en_silencio(tmp_path, marca):
    src = fuente(tmp_path / "mudo.mp4", 640, 360, 4, audio=False)
    mp4 = tmp_path / "m.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=tmp_path / "w",
                             fuente_video=src, inicio=0.0)
    assert plan["audio"] == "silencio"
    ok, det = estilo.audio_original(mp4, plan)
    assert ok, det
    assert malos(estilo.revisar(mp4, plan, marca)) == []
    # un plan que diga "original" con una fuente muda no pasa
    assert not estilo.audio_original(mp4, {**plan, "audio": "original"})[0]


def test_la_orden_de_ffmpeg_no_añade_audio_ni_efectos_prohibidos():
    video = {"ruta": "v.mp4", "inicio": 3.0, "audio": True, "recorte": {"x": 218, "y": 0, "ancho": 202, "alto": 360}}
    cmd = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=8.0, ancho=1080, alto=1920, video=video,
                                                  placas=[("a.png", 60, 150, 0.0, 8.0), ("b.png", 238, 1420, 7.5, 8.0)])
    txt = " ".join(cmd)
    assert "-ss 3 -t 8 -i v.mp4" in txt and "crop=202:360:218:0,scale=1080:1920:flags=lanczos" in txt
    assert "overlay=60:150:enable='gte(t,0)*lt(t,8)'" in txt and "overlay=238:1420:enable='gte(t,7.5)*lt(t,8)'" in txt
    assert "-map [out] -map 0:a" in txt and "anullsrc" not in txt and "amix" not in txt
    for prohibido in ("zoompan", "xfade", "fade", "gradient", "drawtext", "subtitles"):
        assert prohibido not in txt
    mudo = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=8.0, ancho=1080, alto=1920,
                                                   video={**video, "audio": False}, placas=[("a.png", 60, 150, 0.0, 8.0)])
    assert "anullsrc=r=48000" in " ".join(mudo) and "-map [out] -map 2:a" in " ".join(mudo)


# ---------------------------------------------------------------- criterio 4: zonas y tope de capas

def toma_cargada(marca):
    t = lienzo.Toma(numero=3, gancho="Ella busca un dragón de fuego", palabra_lapiz="DRAGÓN", lapiz="subrayado",
                    escena="INT. TU CONSOLA — 18 DE NOVIEMBRE, NOCHE", credito="Con: Blender Foundation (CC BY 3.0)")
    lineas = [componer.Linea(0.5, 3.0, "CHAMÁN", "¿Alguien muy querido? ¿Un alma gemela?", "(en voz baja)"),
              componer.Linea(3.2, 5.0, "SINTEL", "Busco a alguien que ya no está aquí para siempre.")]
    return t, lineas


def test_ninguna_placa_toca_la_zona_de_accion_ni_pasa_el_tope(marca):
    t, lineas = toma_cargada(marca)
    todas = placas.construir(marca, t, lineas, 8.0, RAIZ_REAL / "marca/campanas/fm27/logo.png")
    capas = [p.capa() for p in todas]
    assert {c["tipo"] for c in capas} >= {"fila", "cabecera", "mosca", "dialogo_1", "dialogo_2", "cierre", "marca_agua"}
    acc, seg = marca.pc["zona_accion"], marca.pc["zona_segura"]
    for c in capas:
        assert c["x"] >= seg["x0"] and c["y"] >= seg["y0"] and c["x"] + c["ancho"] <= seg["x1"] \
            and c["y"] + c["alto"] <= seg["y1"], c
        fuera = (c["x"] + c["ancho"] <= acc["x0"] or c["x"] >= acc["x1"]
                 or c["y"] + c["alto"] <= acc["y0"] or c["y"] >= acc["y1"])
        assert fuera, c
        assert c["x"] % 2 == 0 and c["y"] % 2 == 0 and c["ancho"] % 2 == 0 and c["alto"] % 2 == 0
    for t0 in sorted({c["t0"] for c in capas}):
        area = sum(c["ancho"] * c["alto"] for c in capas if c["t0"] <= t0 < c["t1"])
        assert area <= marca.pc["tope_capas"] * 1080 * 1920
    assert placas.problemas_capas(capas, marca) == []
    # dialogo: su borde de abajo queda sobre la franja de las apps; el cierre va en el tercio inferior
    assert all(c["y"] + c["alto"] <= 1920 - marca.m("margen_inferior") for c in capas if c["tipo"].startswith("dialogo"))
    cierre = next(c for c in capas if c["tipo"] == "cierre")
    assert cierre["y"] >= acc["y1"] and (cierre["t0"], cierre["t1"]) == (7.5, 8.0)


def test_tiempos_de_las_placas(marca):
    t, lineas = toma_cargada(marca)
    c = {p.tipo: p.capa() for p in placas.construir(marca, t, lineas, 8.0)}
    assert (c["cabecera"]["t0"], c["cabecera"]["t1"]) == (0.0, marca.pc["gancho_s"])        # el gancho se ve ~3 s
    assert (c["fila"]["t0"], c["fila"]["t1"]) == (0.0, 8.0)                                  # fila y crédito, siempre
    assert (c["mosca"]["t0"], c["mosca"]["t1"]) == (0.0, 8.0)                                # mosca fija toda la toma
    assert c["mosca"]["x"] + c["mosca"]["ancho"] == marca.pc["zona_segura"]["x1"]            # arriba a la derecha


def test_una_placa_que_toca_la_accion_no_se_compone(marca, tmp_path):
    datos = copy.deepcopy(marca.datos)
    datos["pantalla_completa"]["zona_accion"]["y0"] = 300           # ahora el encabezado invade la acción
    m2 = Marca(datos, marca.raiz)
    t, lineas = toma_cargada(m2)
    capas = [p.capa() for p in placas.construir(m2, t, lineas, 8.0)]
    assert any("zona de acción" in x for x in placas.problemas_capas(capas, m2))
    if hay_ffmpeg:
        src = fuente(tmp_path / "a.mp4", 640, 360, 8)
        with pytest.raises(ValueError, match="zona de acción"):
            componer.componer(m2, t, lineas, tmp_path / "x.mp4", duracion=8.0, trabajo=tmp_path / "w",
                              fuente_video=src, inicio=0.0)
        assert not (tmp_path / "x.mp4").exists()


# ---------------------------------------------------------------- criterio 5: la puerta en rojo

@necesita_ffmpeg
def test_video_con_franjas_negras_o_de_papel_sale_en_rojo(ancha, marca, tmp_path):
    """El diseño de caja (franjas arriba y abajo) con el plan v2 correcto: los bordes ya no coinciden con la fuente."""
    for nombre, color in (("negras", "black"), ("papel", "0xF2EDE3")):
        franjas = tmp_path / f"{nombre}.mp4"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(ancha["mp4"]),
                        "-vf", f"scale=1080:608,pad=1080:1920:0:656:color={color}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "copy", str(franjas)],
                       check=True)
        puntos = malos(estilo.revisar(franjas, ancha["plan"], marca))
        assert "[fotograma] Bordes coinciden con la fuente (sin franjas)" in puntos, (nombre, puntos)
        assert "[fotograma] Cuerpo coincide con el origen" in puntos


@necesita_ffmpeg
def test_una_escena_oscura_de_verdad_no_sale_en_rojo(marca, tmp_path):
    oscura = lavfi(tmp_path / "oscura.mp4", "color=c=0x080808:s=640x360:r=30:d=4", 4)
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=tmp_path / "w",
                             fuente_video=oscura, inicio=0.0)
    ok, det = estilo.bordes_coinciden(mp4, plan, marca)
    assert ok, det


@necesita_ffmpeg
def test_una_placa_que_invade_la_accion_en_el_plan_sale_en_rojo(ancha, marca):
    plan = json.loads(json.dumps(ancha["plan"]))
    cab = next(c for c in plan["geometria"]["capas"] if c["tipo"] == "cabecera")
    cab["y"] = 700
    puntos = malos(estilo.revisar(ancha["mp4"], plan, marca))
    assert "[plan] Capas fuera de la zona de acción" in puntos
    assert "[plan] Capas del plan coinciden con las placas de marca.yml" in puntos


@necesita_ffmpeg
def test_audio_reemplazado_por_silencio_sale_en_rojo(ancha, marca, tmp_path):
    mudo = tmp_path / "silencio.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(ancha["mp4"]),
                    "-f", "lavfi", "-t", "4", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(mudo)], check=True)
    puntos = malos(estilo.revisar(mudo, ancha["plan"], marca))
    assert puntos == ["[fotograma] Audio original del clip"], puntos


@necesita_ffmpeg
def test_otro_audio_distinto_al_de_la_fuente_sale_en_rojo(ancha, marca, tmp_path):
    otro = tmp_path / "otro-audio.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(ancha["mp4"]),
                    "-f", "lavfi", "-t", "4", "-i", "anoisesrc=d=4:c=pink:a=0.3", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(otro)], check=True)
    assert "[fotograma] Audio original del clip" in malos(estilo.revisar(otro, ancha["plan"], marca))


@necesita_ffmpeg
def test_un_plan_v2_sin_plantilla_con_origen_sale_en_rojo(ancha, marca):
    plan = {k: v for k, v in ancha["plan"].items() if k not in ("plantilla", "audio")}
    ok, det = estilo.pantalla_completa(plan, marca)
    assert not ok and "v2" in det
    assert "[plan] Pantalla completa (cuerpo 1080x1920, sin franjas)" in malos(estilo.revisar(ancha["mp4"], plan, marca))


@necesita_ffmpeg
def test_mosca_y_cabecera_cambiadas_salen_en_rojo(ancha, marca, tmp_path):
    plan = json.loads(json.dumps(ancha["plan"]))
    plan["toma"]["numero"] = 8                              # TOMA 008 en la fila: el MP4 trae TOMA 007
    assert "[fotograma] Cabecera en el video" in malos(estilo.revisar(ancha["mp4"], plan, marca))
    datos = copy.deepcopy(marca.datos)
    datos["mosca"]["texto"] = "OTRA MOSCA"
    assert "[fotograma] Mosca en el video" in malos(estilo.revisar(ancha["mp4"], ancha["plan"], Marca(datos, marca.raiz)))


# ---------------------------------------------------------------- marca de agua y logos extra en v2

@necesita_ffmpeg
def test_marca_de_agua_en_v2_va_bajo_la_mosca_y_la_puerta_la_ve(marca, tmp_path):
    src = fuente(tmp_path / "a.mp4", 640, 360, 12)
    ficha = {"id": "fm27", "marca_agua": "marca/campanas/fm27/logo.png",
             "reglas": {"sin_musica": True, "duracion_s": [10, 30]}}
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=12.0, trabajo=tmp_path / "w",
                             fuente_video=src, inicio=0.0, ficha=ficha)
    caja = plan["campana"]["marca_agua"]["caja"]
    mosca = next(c for c in plan["geometria"]["capas"] if c["tipo"] == "mosca")
    assert caja["x"] + caja["ancho"] == marca.pc["zona_segura"]["x1"] and caja["y"] >= mosca["y"] + mosca["alto"]
    assert caja["y"] + caja["alto"] <= marca.pc["zona_accion"]["y0"]
    assert any(c["tipo"] == "marca_agua" for c in plan["geometria"]["capas"])
    puntos = estilo.revisar(mp4, plan, marca, ficha=ficha)
    assert [n for n, ok, _ in puntos if not ok] == []
    assert any(n.endswith("Marca de agua de la campaña") for n, _, _ in puntos)
    # sin que la ficha la declare, la puerta la rechaza
    assert not next(ok for n, ok, _ in estilo.revisar(mp4, plan, marca, ficha={**ficha, "marca_agua": None})
                    if n.endswith("Marca de agua de la campaña"))


@necesita_ffmpeg
def test_un_logo_pegado_sin_declarar_sigue_en_rojo(ancha, marca, tmp_path):
    from fabrica.editar import agua
    placa = agua.logo(RAIZ_REAL / "marca/campanas/fm27/logo.png", marca)
    c = agua.caja_pantalla(marca, placa.size)
    con = tmp_path / "con-logo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(ancha["mp4"]),
                    "-i", str(RAIZ_REAL / "marca/campanas/fm27/logo.png"),
                    "-filter_complex", f"[1:v]scale={marca.marca_agua['ancho']}:-1[l];[0:v][l]overlay={c['x']}:{c['y']}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "copy", str(con)], check=True)
    assert "[fotograma] Sin logos extra no declarados" in malos(estilo.revisar(con, ancha["plan"], marca))


# ---------------------------------------------------------------- tomas sin video, marca.yml, validador

@necesita_ffmpeg
def test_una_toma_sin_video_no_cambia(marca, tmp_path):
    t = lienzo.Toma(numero=1, gancho="GTA 6 llega esta medianoche", palabra_lapiz="MEDIANOCHE", lapiz="círculo",
                    escena="INT. TU CONSOLA", credito="Fuente: Rockstar Games Support")
    ln = [componer.Linea(0.0, 4.0, "EL DATO", "Medianoche local del 19 de noviembre.")]
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, t, ln, mp4, duracion=8.0, trabajo=tmp_path / "w")
    assert "plantilla" not in plan and plan["origen"] is None and plan["geometria"]["cuerpo"] is None
    assert "y_dialogo" in plan["geometria"] and "capas" not in plan["geometria"]
    assert all(ok for _, ok, _ in estilo.revisar(mp4, plan, marca))
    assert not any("zona de acción" in n or "Pantalla completa" in n for n, _, _ in estilo.revisar(mp4, plan, marca))


def test_el_codigo_nuevo_no_lleva_medidas_ni_colores_escritos_a_mano():
    for nombre in ("placas.py", "encuadre.py", "agua.py"):
        texto = (RAIZ_REAL / "fabrica" / "editar" / nombre).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", texto), nombre
        assert not re.search(r"\b(1080|1920)\b", texto), nombre


def test_marca_yml_declara_la_plantilla_y_todas_las_medidas(marca):
    assert marca.datos["plantilla_video"] == "pantalla_completa"
    pc = marca.pc
    for clave in ("zona_segura", "zona_accion", "tope_capas", "gancho_s", "relleno_placa", "fila", "cabecera", "mosca",
                  "dialogo", "cierre", "agua", "lapiz", "recorte"):
        assert clave in pc, clave
    assert marca.datos["movimiento"]["zoom_automatico"] is False and marca.datos["movimiento"]["transiciones"] == "ninguna"
    assert pc["dialogo"]["borde_inferior"] <= marca.alto - marca.m("margen_inferior")
    assert validar.validar(RAIZ_REAL).errores == []


def test_validar_exige_la_seccion_pantalla_completa(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda d: {k: v for k, v in d.items() if k != "pantalla_completa"})
    assert any("falta la sección pantalla_completa" in e for e in validar.validar(repo).errores)


def test_validar_zona_de_accion_dentro_de_la_segura(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda d: d["pantalla_completa"]["zona_accion"].update(y1=1900))
    assert any("zona_accion debe quedar dentro" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "marca.yml", lambda d: (d["pantalla_completa"].pop("gancho_s"), None)[1])
    assert any("falta la clave 'gancho_s'" in e for e in validar.validar(repo).errores)


def test_las_3_muestras_son_de_video_y_caben_en_la_plantilla(marca):
    from fabrica.editar import muestras
    todas = muestras.cargar_muestras()
    assert [m["id"] for m in todas] == ["dialogo-sintel", "momento-bbb", "pajaro-bbb"]
    for m in todas:
        assert m["video"]["sha256"] and m["fuente_id"] in ("sintel-cc-by", "bbb-cc-by")
        lineas = [componer.Linea(**ln) for ln in m["lineas"]]
        todas_p = placas.construir(marca, lienzo.Toma(**m["toma"]), lineas, float(m["recorte"]["duracion"]))
        assert placas.problemas_capas([p.capa() for p in todas_p], marca) == []


# ---------------------------------------------------------------- ronda 1: rotación, solapes, fila y gancho

@necesita_ffmpeg
def test_una_fuente_con_rotacion_90_se_trata_con_ancho_y_alto_intercambiados(ancha, marca, tmp_path):
    girada = tmp_path / "girada.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-display_rotation", "90",
                        "-i", str(ancha["fuente"]), "-c", "copy", str(girada)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("este ffmpeg no soporta -display_rotation")
    info = componer.sondear_video(girada)
    assert (info["ancho"], info["alto"]) == (360, 640)
    sin = componer.sondear_video(ancha["fuente"])
    assert (sin["ancho"], sin["alto"]) == (640, 360)
    mp4 = tmp_path / "g.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=tmp_path / "w",
                             fuente_video=girada, inicio=0.0)
    r = plan["geometria"]["recorte"]
    assert r["x"] + r["ancho"] <= 360 and r["y"] + r["alto"] <= 640
    assert malos(estilo.revisar(mp4, plan, marca)) == []


def test_rotacion_lee_el_tag_y_el_side_data():
    assert componer.rotacion({"tags": {"rotate": "90"}}) == 90
    assert componer.rotacion({"side_data_list": [{"rotation": -90}]}) == 270
    assert componer.rotacion({}) == 0


def test_un_dialogo_se_recorta_antes_del_cierre_o_se_rechaza(marca):
    t = lienzo.Toma(**TOMA)
    todas = placas.construir(marca, t, [componer.Linea(0.5, 8.0, "ANA", "Hola.")], 8.0)
    d = next(p for p in todas if p.tipo == "dialogo_1")
    assert d.t1 == 8.0 - marca.m("cierre_s")
    with pytest.raises(ValueError, match="dentro del cierre"):
        placas.construir(marca, t, [componer.Linea(7.6, 8.0, "ANA", "Hola.")], 8.0)


def test_placas_activas_a_la_vez_no_se_solapan(marca):
    t, lineas = toma_cargada(marca)
    capas = [p.capa() for p in placas.construir(marca, t, lineas, 8.0)]
    assert placas.problemas_capas(capas, marca) == []
    dos = [dict(c) for c in capas]
    d1 = next(c for c in dos if c["tipo"] == "dialogo_1")
    d2 = next(c for c in dos if c["tipo"] == "dialogo_2")
    d2.update(t0=d1["t0"], t1=d1["t1"], x=d1["x"], y=d1["y"])        # dos diálogos al mismo tiempo y en el mismo sitio
    assert any("se solapan" in x for x in placas.problemas_capas(dos, marca))
    d2.update(t0=d1["t1"], t1=d1["t1"] + 1)                          # en tiempos distintos sí conviven
    assert not any("dialogo_1 y dialogo_2" in x for x in placas.problemas_capas(dos, marca))


@necesita_ffmpeg
def test_la_fila_debe_seguir_en_pantalla_a_30_y_70_por_ciento(ancha, marca, tmp_path):
    fila = next(c for c in ancha["plan"]["geometria"]["capas"] if c["tipo"] == "fila")
    sin = tmp_path / "sin-fila.mp4"
    caja = f"drawbox=x={fila['x']}:y={fila['y']}:w={fila['ancho']}:h={fila['alto']}:color=black:t=fill:enable='gte(t,2.5)'"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(ancha["mp4"]), "-vf", caja,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "copy", str(sin)], check=True)
    assert "[fotograma] Cabecera en el video" in malos(estilo.revisar(sin, ancha["plan"], marca))


@necesita_ffmpeg
def test_el_gancho_ya_no_esta_pasado_gancho_s(marca, tmp_path):
    datos = copy.deepcopy(marca.datos)
    datos["pantalla_completa"]["gancho_s"] = 6.0
    largo = Marca(datos, marca.raiz)
    src = fuente(tmp_path / "a.mp4", 640, 360, 8)
    mp4 = tmp_path / "t.mp4"
    componer.componer(largo, lienzo.Toma(**TOMA), [], mp4, duracion=8.0, trabajo=tmp_path / "w",
                      fuente_video=src, inicio=0.0)
    plan = componer.componer(marca, lienzo.Toma(**TOMA), [], tmp_path / "ok.mp4", duracion=8.0,
                             trabajo=tmp_path / "w2", fuente_video=src, inicio=0.0)
    ok, det = estilo.cabecera_visible(tmp_path / "ok.mp4", plan, marca)
    assert ok, det
    ok, det = estilo.cabecera_visible(mp4, plan, marca)              # este MP4 aún trae el gancho a los 3.5 s
    assert not ok and "gancho fuera" in det

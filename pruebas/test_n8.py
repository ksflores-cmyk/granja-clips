"""N8: primera campaña real (SEGA Football Manager 27, Clipping Culture). Sin red."""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import fuentes, validar

CREADORES = {
    "SEBASBERON": ("@sebasberon", "@sebasclips", "@sebasberon"),
    "DARO": ("@darizenz", "Credit to Dario Zenz", "Credit to Dario Zenz"),
    "ERIC ZZETA": ("@ericzzeta", "@ericzzeta", "@ericzzeta"),
    "HABEMUS GAMER": ("@habemusfutbol", "@habemusfutbol", "@habemusfutbol"),
    "TIO PEDRO": ("@eltiopedro_fut", "@eltiopedro_fut", "@ElTioPedro"),
}
IDS = {
    "SEBASBERON": "1Z8QGSsOLahIn15ludN-ItQ56aSJ_Q-hV", "DARO": "1ZzpMQ5LzOem94pys0KAgmLYSf92YkmEP",
    "ERIC ZZETA": "1pvvTm9uKcrd_BjN1SMF7048FGn1Ni2vH", "HABEMUS GAMER": "1xo_SFCmZabeXR3afEl3SZkH43R-boMkf",
    "TIO PEDRO": "1ZU5eMBd_yl0HuPH_YEyGQ8XqTmbtEHOS",
}
PREFIJO_RELEASE = "https://github.com/ksflores-cmyk/granja-clips/releases/download/fuentes-fm27/"
ASSETS = {"SEBASBERON": "fm27-sebasberon.mp4", "DARO": "fm27-daro.mp4", "ERIC ZZETA": "fm27-eric-zzeta.mp4",
          "HABEMUS GAMER": "fm27-habemus-gamer.mov", "TIO PEDRO": "fm27-tio-pedro.mp4"}
RED = {"reels": 0, "tiktok": 1, "shorts": 2}


def ficha_real() -> dict:
    return next(f for f in fuentes.cargar_fuentes(RAIZ_REAL) if f["id"] == "sega-fm27")


# ---------------------------------------------------------------- criterio 1: la ficha

def test_ficha_sega_fm27_trae_lo_del_brief():
    f = ficha_real()
    assert (f["tipo"], f["estado"], f["uso"], f["publicable"]) == ("A", "vigente", "produccion", True)
    assert f["campana"] == "SEGA FM27 (Clipping Culture)"
    assert (f["tarifa_usd_por_mil"], f["minimo_por_clip_usd"], f["tope_por_clip_usd"],
            f["presupuesto_restante"]) == (2.0, 2, 600, 5000)
    r = f["reglas"]
    assert r["plataformas"] == ["shorts", "tiktok", "reels"] and r["duracion_s"] == [10, 30]
    assert r["divulgacion"] == "#Publicidad" and r["etiquetas_obligatorias"] == ["@footballmanager"]
    assert r["sin_musica"] is True
    assert f["juego"] == "Football Manager 27"
    assert f["marca_agua"] == "marca/campanas/fm27/logo.png" and (RAIZ_REAL / f["marca_agua"]).is_file()
    assert f["pilares"] in (["momentos"], ["primeras_veces"])
    assert fuentes.es_publicable(f, date(2026, 11, 18))[0]


def test_ficha_sega_fm27_cinco_urls_directas_con_creador_y_etiquetas():
    f = ficha_real()
    urls = fuentes.urls_material(f)
    assert len(urls) == 5
    for u in urls:
        assert u.startswith(PREFIJO_RELEASE)
    for creador, tags in CREADORES.items():
        m = fuentes.entrada_material(f, creador=creador)
        assert m is not None and m["url"] == PREFIJO_RELEASE + ASSETS[creador]
        assert m["drive_url"].startswith("https://drive.usercontent.google.com/download?id=")
        assert m["drive_url"].split("id=")[1].split("&")[0] == IDS[creador]
        for red, i in RED.items():
            assert fuentes.etiqueta_creador(f, creador, red) == tags[i]
    assert fuentes.puede_descargar(f)[0]
    assert fuentes.autoriza_descarga(f, date(2026, 11, 18), "produccion", urls[0], pilar="momentos")[0]
    assert not fuentes.autoriza_descarga(f, date(2026, 11, 18), "produccion", "https://otro.org/x.mp4",
                                         pilar="momentos")[0]


def test_validar_en_verde_con_la_ficha(repo):
    assert validar.validar(repo).errores == []


def _con_ficha(repo, editar_yaml, fn):
    def cambio(datos):
        fn(next(f for f in datos if f["id"] == "sega-fm27"))
    editar_yaml(repo, "fuentes.yml", cambio)
    return validar.validar(repo).errores


def test_validar_rechaza_material_sin_creador(repo, editar_yaml):
    e = _con_ficha(repo, editar_yaml, lambda f: f["material"][0].pop("creador"))
    assert any("material[0]: creador es obligatorio" in x for x in e)


def test_validar_rechaza_etiqueta_faltante_o_red_desconocida(repo, editar_yaml):
    e = _con_ficha(repo, editar_yaml, lambda f: f["material"][1]["etiquetas"].pop("tiktok"))
    assert any("falta la etiqueta del creador para tiktok" in x for x in e)
    e = _con_ficha(repo, editar_yaml, lambda f: f["material"][2]["etiquetas"].update({"facebook": "@x"}))
    assert any("red desconocida" in x for x in e)


def test_validar_rechaza_url_no_https_en_entrada(repo, editar_yaml):
    e = _con_ficha(repo, editar_yaml, lambda f: f["material"][0].update(url="http://x.org/a.mp4"))
    assert any("material[0]: url debe ser https" in x for x in e)


def test_validar_rechaza_marca_agua_inexistente_o_fuera_del_repo(repo, editar_yaml):
    e = _con_ficha(repo, editar_yaml, lambda f: f.update(marca_agua="marca/campanas/fm27/nada.png"))
    assert any("que no existe" in x for x in e)
    e = _con_ficha(repo, editar_yaml, lambda f: f.update(marca_agua="../logo.png"))
    assert any("ruta relativa" in x for x in e)


def test_validar_rechaza_sin_musica_no_booleano_y_juego_vacio(repo, editar_yaml):
    e = _con_ficha(repo, editar_yaml, lambda f: f["reglas"].update(sin_musica="si"))
    assert any("sin_musica" in x for x in e)
    e = _con_ficha(repo, editar_yaml, lambda f: f.update(juego=""))
    assert any("juego debe ser texto" in x for x in e)


# ---------------------------------------------------------------- utilidades de render (video sintético)

import json
import shutil
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

from fabrica import ojos
from fabrica.detectar import Candidato
from fabrica.editar import componer as mod_componer
from fabrica.editar import estilo, toma as mod_toma
from fabrica.editar.componer import Linea
from fabrica.editar.lienzo import Toma
from fabrica.editar.marca import Marca

AHORA = datetime(2026, 11, 18, 22, 0, tzinfo=ZoneInfo("America/Guatemala"))
DATOS = RAIZ_REAL / "pruebas" / "datos" / "gemini"


@pytest.fixture(scope="session")
def marca():
    return Marca.cargar(RAIZ_REAL)


@pytest.fixture(scope="session")
def temporada():
    return yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    """Video sintético de 20 s, 1280x720, con audio (el audio es un tono: sirve para comparar)."""
    ruta = tmp_path_factory.mktemp("sintetico-n8") / "fuente.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=20", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(ruta)], check=True)
    return ruta


def toma_de_prueba() -> tuple[Toma, list[Linea]]:
    t = Toma(numero=1, gancho="Sebas ficha al delantero", palabra_lapiz="DELANTERO", lapiz="círculo",
             escena="INT. STREAM DE SEBASBERON — MINUTO 4", credito="Con: SEBASBERON")
    return t, [Linea(t0=0.5, t1=4.0, nombre="SEBASBERON", frase="Por fin ficho al delantero.")]


def render(marca, tmp, video, ficha, nombre):
    t, lineas = toma_de_prueba()
    carpeta = tmp / nombre
    carpeta.mkdir()
    mp4 = carpeta / "t.mp4"
    plan = mod_componer.componer(marca, t, lineas, mp4, duracion=12.0, trabajo=carpeta / "w", fuente_video=video,
                                 inicio=2.0, plan_salida=carpeta / "t.plan.json", ficha=ficha)
    return mp4, plan


def sin_agua(ficha):
    f = copy.deepcopy(ficha)
    f.pop("marca_agua")
    return f


@pytest.fixture(scope="module")
def con_logo(marca, video, tmp_path_factory):
    return render(marca, tmp_path_factory.mktemp("logo"), video, ficha_real(), "a")


@pytest.fixture(scope="module")
def sin_logo(marca, video, tmp_path_factory):
    return render(marca, tmp_path_factory.mktemp("limpio"), video, sin_agua(ficha_real()), "a")


def punto(puntos, nombre):
    return next((ok, det) for n, ok, det in puntos if n.endswith(nombre))


# ---------------------------------------------------------------- criterio 2: juego por ficha

def test_juego_de_usa_la_ficha_y_si_no_la_temporada(temporada):
    assert ojos.juego_de(ficha_real(), temporada) == "Football Manager 27"
    assert ojos.juego_de({"id": "x"}, temporada) == temporada["nombre"] == "GTA 6"
    assert ojos.juego_de(None, temporada) == "GTA 6"


def test_la_instruccion_pide_el_juego_de_la_ficha_y_el_recorte_de_la_ficha(marca, temporada):
    t = ojos.temporada_de(ficha_real(), temporada)
    ins = ojos.instruccion(marca, t, 20.0, ojos.juego_de(ficha_real(), temporada),
                           ojos.nota_de_ficha(ficha_real()), ojos.rango_recorte(ficha_real()))
    assert "Football Manager 27" in ins and "GTA" not in ins
    assert "de 10 a 30 segundos" in ins and "{{" not in ins
    assert "no añadas música" in ins and "gameplay" in ins


def test_un_clip_de_fm27_no_se_descarta_por_no_ser_gta6(marca, temporada):
    datos = json.loads((DATOS / "fm27_valida.json").read_text(encoding="utf-8"))
    f = ficha_real()
    ok, motivos = ojos.validar_respuesta(datos, marca, ojos.temporada_de(f, temporada), duracion=20.0,
                                         juego=ojos.juego_de(f, temporada), recorte=ojos.rango_recorte(f))
    assert ok, motivos
    datos["juego_correcto"] = False
    ok, motivos = ojos.validar_respuesta(datos, marca, ojos.temporada_de(f, temporada), duracion=20.0,
                                         juego=ojos.juego_de(f, temporada), recorte=ojos.rango_recorte(f))
    assert not ok and any("Football Manager 27" in m and "GTA" not in m for m in motivos)


def test_sin_juego_en_la_ficha_se_sigue_usando_la_temporada(marca, temporada):
    datos = json.loads((DATOS / "valida.json").read_text(encoding="utf-8"))
    datos["juego_correcto"] = False
    ok, motivos = ojos.validar_respuesta(datos, marca, temporada, duracion=20.0, juego=ojos.juego_de({}, temporada))
    assert not ok and any("GTA 6" in m for m in motivos)


def test_los_spoilers_de_gta_no_aplican_a_una_ficha_con_otro_juego(marca, temporada):
    f = ficha_real()
    assert ojos.temporada_de(f, temporada)["palabras_evitar"] == ["filtración", "filtrado", "leak", "pirata", "crack", "gratis"]
    datos = json.loads((DATOS / "fm27_valida.json").read_text(encoding="utf-8"))
    datos["gancho"] = "Final de copa para Sebas"
    datos["palabra_lapiz"] = "COPA"
    assert ojos.validar_respuesta(datos, marca, ojos.temporada_de(f, temporada), duracion=20.0)[0]
    assert not ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)[0]   # con la temporada, sí es spoiler


# ---------------------------------------------------------------- criterio 4: duración por ficha

def test_recorte_de_8_s_se_rechaza_con_la_ficha_de_10_a_30(marca, temporada):
    f = ficha_real()
    datos = json.loads((DATOS / "fm27_valida.json").read_text(encoding="utf-8"))
    datos["recorte"] = {"inicio": 3.0, "fin": 11.0}
    datos["lineas"] = datos["lineas"][:1]
    t = ojos.temporada_de(f, temporada)
    ok, m = ojos.validar_respuesta(datos, marca, t, duracion=20.0, recorte=ojos.rango_recorte(f))
    assert not ok and any("de 10 a 30 s" in x for x in m)
    assert ojos.validar_respuesta(datos, marca, t, duracion=20.0)[0]      # sin ficha, 8 s vale (mínimo 7)


def test_rango_de_recorte_nunca_sale_del_formato():
    assert ojos.rango_recorte({"reglas": {"duracion_s": [10, 30]}}) == (10.0, 30.0)
    assert ojos.rango_recorte({"reglas": {"duracion_s": [3, 90]}}) == (7.0, 30.0)
    assert ojos.rango_recorte(None) == (7.0, 30.0)


def test_la_puerta_de_estilo_usa_la_duracion_de_la_ficha(marca, sin_logo):
    mp4, plan = sin_logo                              # dura 12 s
    f = sin_agua(ficha_real())
    ok, _ = punto(estilo.revisar(mp4, plan, marca, ficha=f), "Duración de 10 a 30 s")
    assert ok
    f["reglas"]["duracion_s"] = [15, 30]
    ok, det = punto(estilo.revisar(mp4, plan, marca, ficha=f), "Duración de 15 a 30 s")
    assert not ok and "12.0" in det


# ---------------------------------------------------------------- criterio 3: marca de agua por ficha

def test_el_plan_declara_la_capa_y_el_logo_esta_en_el_lugar_de_marca_yml(marca, con_logo):
    mp4, plan = con_logo
    m = plan["campana"]["marca_agua"]
    seg, acc = marca.pc["zona_segura"], marca.pc["zona_accion"]
    assert m["archivo"] == "marca/campanas/fm27/logo.png"
    # v2: esquina superior derecha de la zona segura, bajo la mosca, y sin tocar la zona de acción
    assert m["caja"]["x"] + m["caja"]["ancho"] == seg["x1"]
    assert m["caja"]["y"] == marca.pc["agua"]["y"] and m["caja"]["y"] + m["caja"]["alto"] <= acc["y0"]
    capas = {c["tipo"]: c for c in plan["geometria"]["capas"]}
    assert (capas["marca_agua"]["x"], capas["marca_agua"]["y"]) == (m["caja"]["x"], m["caja"]["y"])
    # nunca sobre la cabecera, el diálogo ni la mosca
    assert m["caja"]["y"] >= capas["mosca"]["y"] + capas["mosca"]["alto"]
    for tipo in ("fila", "cabecera", "dialogo_1"):
        c = capas[tipo]
        assert (c["x"] + c["ancho"] <= m["caja"]["x"] or m["caja"]["x"] + m["caja"]["ancho"] <= c["x"]
                or c["y"] + c["alto"] <= m["caja"]["y"] or m["caja"]["y"] + m["caja"]["alto"] <= c["y"]), tipo


def test_la_puerta_acepta_el_logo_si_la_ficha_lo_declara(marca, con_logo):
    mp4, plan = con_logo
    puntos = estilo.revisar(mp4, plan, marca, ficha=ficha_real())
    ok, det = punto(puntos, "Marca de agua de la campaña")
    assert ok, det
    malos = [n for n, o, d in puntos if not o]
    assert malos == [], malos


def test_la_puerta_rechaza_la_capa_si_la_ficha_no_la_declara(marca, con_logo):
    mp4, plan = con_logo
    puntos = estilo.revisar(mp4, plan, marca, ficha=sin_agua(ficha_real()))
    ok, det = punto(puntos, "Marca de agua de la campaña")
    assert not ok and "no declara" in det
    assert not punto(estilo.revisar(mp4, plan, marca, ficha=None), "Marca de agua de la campaña")[0]


def test_un_logo_extra_sin_declarar_sigue_en_rojo(marca, con_logo):
    mp4, plan = con_logo
    plan_sin = {k: v for k, v in plan.items() if k != "campana"}      # el MP4 lleva el logo; el plan no lo dice
    puntos = estilo.revisar(mp4, plan_sin, marca, ficha=sin_agua(ficha_real()))
    rojos = [n for n, ok, _ in puntos if not ok]
    assert "[fotograma] Sin logos extra no declarados" in rojos, rojos


def test_sin_logo_y_sin_declaracion_la_puerta_sigue_en_verde(marca, sin_logo):
    mp4, plan = sin_logo
    puntos = estilo.revisar(mp4, plan, marca, ficha=sin_agua(ficha_real()))
    assert [n for n, o, d in puntos if not o] == []
    assert punto(puntos, "Sin logos extra no declarados")[0]


def test_la_ficha_declara_el_logo_pero_no_aparece_en_el_video(marca, con_logo, sin_logo):
    mp4_limpio, _ = sin_logo
    _, plan = con_logo
    ok, det = punto(estilo.revisar(mp4_limpio, plan, marca, ficha=ficha_real()), "Marca de agua de la campaña")
    assert not ok and "no aparece" in det


def test_la_ficha_declara_el_logo_y_el_plan_no_lo_trae(marca, sin_logo):
    mp4, plan = sin_logo
    ok, det = punto(estilo.revisar(mp4, plan, marca, ficha=ficha_real()), "Marca de agua de la campaña")
    assert not ok and "el plan no la trae" in det


def test_componer_sin_ficha_no_pone_capa_ni_campana(marca, sin_logo):
    _, plan = sin_logo
    assert plan["campana"]["marca_agua"] is None


def test_marca_yml_valida_la_seccion_marca_agua(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda m: m["marca_agua"].update(posicion="centro"))
    assert any("marca_agua.posicion" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "marca.yml", lambda m: {k: v for k, v in m.items() if k != "marca_agua"})
    assert any("falta la sección marca_agua" in e for e in validar.validar(repo).errores)


# ---------------------------------------------------------------- criterio 5: textos por campaña

from fabrica import publicar
from fabrica.publicar import Descartada, textos

TOMA_FM = {"numero": 7, "gancho": "Sebas ficha al delantero", "pilar": "momentos", "campana": "SEGA FM27 (Clipping Culture)"}
REDES = ("reels", "tiktok", "shorts")


@pytest.mark.parametrize("creador", list(CREADORES))
@pytest.mark.parametrize("red", REDES)
def test_texto_de_campana_por_red_y_por_creador(creador, red):
    f = ficha_real()
    tx = textos({**TOMA_FM, "creador": creador}, f, red)
    etiqueta = CREADORES[creador][RED[red]]
    lineas = tx["texto"].split("\n")
    assert lineas[0] == "Toma 007. Sebas ficha al delantero" and lineas[1] == ""
    # @footballmanager, la etiqueta del creador de esa red y #Publicidad: en ese orden, sin líneas en medio
    assert lineas[2:5] == ["@footballmanager", etiqueta, "#Publicidad"]
    assert lineas[5].split()[0].startswith("#") and 1 <= len(lineas[5].split()) <= 3
    assert len(lineas) == 6
    assert "🎥" not in tx["texto"] and tx["credito"] == etiqueta
    assert tx["etiquetas"][:2] == ["@footballmanager", "#Publicidad"]
    hashtags = [w for w in tx["texto"].split() if w.startswith("#")]
    assert hashtags[0] == "#Publicidad" and len(hashtags) <= 4
    assert (tx["titulo"] is not None) == (red == "shorts")


def test_daro_en_tiktok_y_shorts_es_el_texto_literal_y_en_reels_su_usuario():
    for red, esperado in (("tiktok", "Credit to Dario Zenz"), ("shorts", "Credit to Dario Zenz"),
                          ("reels", "@darizenz")):
        assert esperado in textos({**TOMA_FM, "creador": "DARO"}, ficha_real(), red)["texto"].split("\n")


def test_tio_pedro_usa_otro_usuario_en_youtube():
    assert "@ElTioPedro" in textos({**TOMA_FM, "creador": "TIO PEDRO"}, ficha_real(), "shorts")["texto"]
    assert "@eltiopedro_fut" in textos({**TOMA_FM, "creador": "TIO PEDRO"}, ficha_real(), "tiktok")["texto"]


def test_sebasberon_en_tiktok_es_sebasclips():
    assert "@sebasclips" in textos({**TOMA_FM, "creador": "SEBASBERON"}, ficha_real(), "tiktok")["texto"]


def test_creador_sin_etiqueta_no_se_publica():
    with pytest.raises(Descartada, match="sin etiqueta del creador"):
        textos({**TOMA_FM, "creador": "NADIE"}, ficha_real(), "tiktok")
    with pytest.raises(Descartada, match="sin etiqueta del creador"):
        textos({**TOMA_FM, "creador": ""}, ficha_real(), "tiktok")


def test_los_hashtags_del_brief_van_primero_sin_pasar_de_tres_ni_repetir_publicidad():
    tx = textos({**TOMA_FM, "creador": "DARO", "etiquetas": ["#publicidad", "#Barça", "#Liga", "#Otro", "#Mas"]},
                ficha_real(), "tiktok")
    ultima = tx["texto"].split("\n")[-1]
    # Tarea #8 (ronda 2): los hashtags del brief (reglas.hashtags) van antes que los de la toma.
    assert ultima == "#FootballManager #FM27 #FootballManager27"
    assert tx["texto"].lower().count("#publicidad") == 1


def test_una_ficha_sin_campana_sigue_con_el_formato_de_siempre():
    f = {"id": "propio", "creador": "@guionvivo", "reglas": {"etiquetas_obligatorias": [], "divulgacion": "ninguna"}}
    tx = textos({"numero": 1, "gancho": "Algo pasa", "pilar": "momentos"}, f, "tiktok")
    assert "🎥 @guionvivo" in tx["texto"]


def test_la_divulgacion_debe_ser_un_hashtag():
    f = copy.deepcopy(ficha_real())
    f["reglas"]["divulgacion"] = "colaboración pagada"
    with pytest.raises(Descartada, match="divulgación"):
        textos({**TOMA_FM, "creador": "DARO"}, f, "reels")


# ---------------------------------------------------------------- criterio 6: sin música

def test_con_sin_musica_el_audio_es_el_original(marca, con_logo):
    mp4, plan = con_logo
    assert plan["campana"]["sin_musica"] is True and plan["campana"]["audio"] == "original"
    ok, det = estilo.audio_original(mp4, plan)
    assert ok, det
    assert punto(estilo.revisar(mp4, plan, marca, ficha=ficha_real()), "Audio original del clip")[0]


def test_la_orden_de_ffmpeg_no_mezcla_audio_nuevo(marca, video, tmp_path, monkeypatch):
    t, lineas = toma_de_prueba()
    cmds = []
    real = subprocess.run

    def espia(cmd, *a, **k):
        cmds.append(list(cmd))
        return real(cmd, *a, **k)
    monkeypatch.setattr(mod_componer.subprocess, "run", espia)
    mod_componer.componer(marca, t, lineas, tmp_path / "t.mp4", duracion=12.0, trabajo=tmp_path / "w",
                          fuente_video=video, inicio=2.0, ficha=ficha_real())
    orden = next(c for c in cmds if "-filter_complex" in c)
    maps = [orden[i + 1] for i, x in enumerate(orden) if x == "-map"]
    assert maps == ["[out]", "0:a:0"]                                 # el audio es el de la fuente
    assert "amix" not in orden[orden.index("-filter_complex") + 1] and not any("anullsrc" in x for x in orden)


def test_el_audio_de_otro_origen_se_detecta(marca, video, tmp_path, con_logo):
    mp4, plan = con_logo
    otro = tmp_path / "otro.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=20", "-f", "lavfi", "-i",
                    "anoisesrc=d=20:c=pink:a=0.3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(otro)], check=True)
    plan2 = copy.deepcopy(plan)
    plan2["origen"]["archivo"] = str(otro)          # la fuente "declarada" no es de la que salió el audio
    ok, det = estilo.audio_original(mp4, plan2)
    assert not ok, det


def test_fuente_sin_audio_deja_silencio_y_nada_mas(marca, tmp_path):
    mudo = tmp_path / "mudo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(mudo)], check=True)
    mp4, plan = render(marca, tmp_path, mudo, sin_agua(ficha_real()), "m")
    assert plan["campana"]["audio"] == "silencio"
    assert estilo.audio_original(mp4, plan)[0]


# ---------------------------------------------------------------- criterio 7: registro del envío manual

from datetime import timedelta, timezone
from fabrica import envios_campana
from fabrica.publicar import ClienteBuffer, Publicador, hueco_a_utc
from fabrica.simulacion import BufferSimulado, _json

AHORA_UTC = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


class BufferConUrl(BufferSimulado):
    """Como el simulado, pero `Post` expone `externalLink` y el post enviado la trae (o no)."""

    def __init__(self, url="https://www.tiktok.com/@guionvivo/video/123", expone=True, status="sent", **kw):
        super().__init__(**kw)
        self.url, self.expone, self.status = url, expone, status

    def __call__(self, metodo, u, encabezados, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode("utf-8"))["query"]
        if '__type(name: "Post")' in q:
            campos = ["id", "status", "dueAt", "text"] + (["externalLink"] if self.expone else [])
            return _json(200, {"data": {"__type": {"fields": [{"name": n} for n in campos]}}})
        if "post(" in q and "posts(" not in q:
            self.consultas.append(q)
            nodo = {"id": "post-ejemplo-001", "status": self.status, "dueAt": "2026-11-18T17:00:00.000Z"}
            if self.url and "externalLink" in q:
                nodo["externalLink"] = self.url
            return _json(200, {"data": {"post": nodo}})
        return super().__call__(metodo, u, encabezados, cuerpo, tiempo)


def publicador(tmp_path, sim):
    c = ClienteBuffer("bfr_clave-inventada-n8", sim, tmp_path, dormir=lambda s: None, ahora=lambda: AHORA_UTC)
    ids = {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"}
    return Publicador(c, None, tmp_path, "vivo", lambda: AHORA_UTC, ids)


def programar_de_campana(tmp_path, sim, red="tiktok"):
    shutil.copy(RAIZ_REAL / "marcador.csv", tmp_path / "marcador.csv")
    pub = publicador(tmp_path, sim)
    toma = {**TOMA_FM, "creador": "DARO", "fuente_id": "sega-fm27", "duracion_s": 12.0}
    tx = textos(toma, ficha_real(), red)
    pub.programar(red, toma, "https://x.invalid/v.mp4", tx, hueco_a_utc(date(2026, 11, 18), "11:00"),
                  None, ficha_real())
    return pub


def test_al_programar_un_clip_de_campana_se_anota_para_el_envio_manual(tmp_path):
    programar_de_campana(tmp_path, BufferConUrl())
    filas = json.loads((tmp_path / "estado" / "envios-campana.json").read_text(encoding="utf-8"))
    assert len(filas) == 1
    f = filas[0]
    assert (f["plataforma"], f["post_id"], f["campana"]) == ("tiktok", "post-ejemplo-001", "SEGA FM27 (Clipping Culture)")
    assert f["hora"] == "2026-11-18T11:00:00-06:00"
    assert f["estado"] == "programado" and f["url"] is None


def test_un_clip_sin_campana_no_se_anota(tmp_path):
    pub = publicador(tmp_path, BufferConUrl())
    toma = {"numero": 3, "gancho": "Algo pasa", "pilar": "momentos", "fuente_id": "propio"}
    f = {"id": "propio", "creador": "@guionvivo", "reglas": {"etiquetas_obligatorias": [], "divulgacion": "ninguna"}}
    pub.programar("tiktok", toma, "https://x.invalid/v.mp4", textos(toma, f, "tiktok"),
                  hueco_a_utc(date(2026, 11, 18), "11:00"), None, f)
    assert not (tmp_path / "estado" / "envios-campana.json").exists()


def test_con_post_sent_y_url_de_buffer_se_escribe_la_url(tmp_path):
    pub = programar_de_campana(tmp_path, BufferConUrl())
    assert envios_campana.completar(tmp_path, pub) == 1
    f = envios_campana.leer(tmp_path)[0]
    assert f["estado"] == "enviado" and f["url"] == "https://www.tiktok.com/@guionvivo/video/123"
    assert f["nota"] == envios_campana.PEGAR
    assert envios_campana.completar(tmp_path, pub) == 0            # no se vuelve a consultar lo cerrado


def test_si_buffer_no_da_la_url_queda_pendiente_de_copiar_desde_la_app(tmp_path):
    pub = programar_de_campana(tmp_path, BufferConUrl(expone=False))
    envios_campana.completar(tmp_path, pub)
    f = envios_campana.leer(tmp_path)[0]
    assert f["estado"] == "enviado" and f["url"] is None
    assert f["nota"] == "pendiente: copiar el enlace desde la app"


def test_con_el_post_aun_programado_no_se_escribe_nada(tmp_path):
    pub = programar_de_campana(tmp_path, BufferConUrl(status="scheduled"))
    assert envios_campana.completar(tmp_path, pub) == 0
    assert envios_campana.leer(tmp_path)[0]["estado"] == "programado"


def test_limpiar_enviados_completa_los_enlaces_en_cada_ciclo(tmp_path):
    pub = programar_de_campana(tmp_path, BufferConUrl())
    pub.limpiar_enviados()
    assert envios_campana.leer(tmp_path)[0]["url"].startswith("https://www.tiktok.com/")


# ---------------------------------------------------------------- criterio 8: ensayo local de punta a punta

import io

from fabrica import detectar


class Resp:
    def __init__(self, datos: bytes):
        self._b = io.BytesIO(datos)
        self.headers = {"Content-Type": "video/mp4"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._b.read(n)


class Transporte:
    def __init__(self, texto_modelo: str):
        self.respuesta = (200, json.dumps({"candidates": [{"content": {"parts": [{"text": texto_modelo}]},
                                                            "finishReason": "STOP"}]}))
        self.cuerpos: list[dict] = []

    def __call__(self, url, encabezados, cuerpo):
        self.cuerpos.append(json.loads(cuerpo))
        return self.respuesta


def candidatos_de_fm27(repo):
    return {c.creador: c for c in detectar.detectar(repo, AHORA, None) if c.fuente_id == "sega-fm27"}


def test_el_detector_trae_los_cinco_videos_con_su_creador(repo):
    cands = candidatos_de_fm27(repo)
    assert sorted(cands) == sorted(CREADORES)
    assert all(c.origen == "brief" and c.pilar is None and c.url == PREFIJO_RELEASE + ASSETS[c.creador] for c in cands.values())
    assert cands["HABEMUS GAMER"].titulo == "HABEMUS GAMER.mov"


@pytest.mark.slow
def test_ensayo_de_punta_a_punta_con_la_ficha_sega_fm27(repo, tmp_path, video):
    cand = candidatos_de_fm27(repo)["DARO"]
    cand.pilar = "momentos"
    ficha = next(f for f in fuentes.cargar_fuentes(repo) if f["id"] == "sega-fm27")
    datos = (DATOS / "fm27_valida.json").read_text(encoding="utf-8")
    tr = Transporte(datos)
    antes = (repo / "marca.yml").read_text(encoding="utf-8")
    r = mod_toma.hacer_toma(repo, cand, ficha, AHORA, salida=tmp_path / "salida", abrir=lambda u: Resp(video.read_bytes()),
                            transporte=tr, entorno={"GEMINI_API_KEY": "clave-de-prueba"}, espera=lambda s: None)
    assert r.ok, r.motivos
    # la puerta de estilo en verde, con la marca de agua y el audio original demostrados
    assert all(ok for _, ok, _ in r.puntos), [n for n, ok, _ in r.puntos if not ok]
    assert punto(r.puntos, "Marca de agua de la campaña")[0]
    assert punto(r.puntos, "Audio original del clip")[0]
    assert punto(r.puntos, "Duración de 10 a 30 s")[0]
    assert (repo / "marca.yml").read_text(encoding="utf-8") == antes
    # Gemini recibió el juego de la ficha y el recorte de la ficha, no el de la temporada
    instruccion = tr.cuerpos[0]["contents"][0]["parts"][1]["text"]
    assert "Football Manager 27" in instruccion and "GTA" not in instruccion and "de 10 a 30 segundos" in instruccion
    # la toma sale con el crédito del creador recortado y declara la capa
    assert r.plan["toma"]["credito"] == "Con: DARO"
    assert r.plan["campana"]["marca_agua"]["archivo"] == "marca/campanas/fm27/logo.png"
    assert r.plan["campana"]["duracion_s"] == [10, 30]
    assert r.fila["campana"] == "SEGA FM27 (Clipping Culture)" and r.fila["fuente_id"] == "sega-fm27"
    # los textos de las tres redes cumplen el brief
    toma_dict = {"numero": r.fila["toma"], "gancho": r.fila["gancho"], "pilar": r.fila["pilar"],
                 "campana": r.fila["campana"], "creador": cand.creador}
    esperado = {"reels": "@darizenz", "tiktok": "Credit to Dario Zenz", "shorts": "Credit to Dario Zenz"}
    for red in REDES:
        tx = textos(toma_dict, ficha, red)
        lineas = tx["texto"].split("\n")
        assert lineas[0].endswith("Sebas ficha al delantero") and lineas[2:5] == ["@footballmanager", esperado[red], "#Publicidad"]
        assert len([w for w in tx["texto"].split() if w.startswith("#")]) <= 4
    assert (tmp_path / "salida" / r.mp4.name).is_file()


@pytest.mark.slow
def test_ensayo_gemini_dice_que_no_es_fm27_y_se_descarta_sin_hablar_de_gta(repo, tmp_path, video):
    cand = candidatos_de_fm27(repo)["ERIC ZZETA"]
    cand.pilar = "momentos"
    ficha = next(f for f in fuentes.cargar_fuentes(repo) if f["id"] == "sega-fm27")
    d = json.loads((DATOS / "fm27_valida.json").read_text(encoding="utf-8"))
    d["juego_correcto"] = False
    r = mod_toma.hacer_toma(repo, cand, ficha, AHORA, salida=tmp_path / "s", abrir=lambda u: Resp(video.read_bytes()),
                            transporte=Transporte(json.dumps(d)), entorno={"GEMINI_API_KEY": "clave-de-prueba"},
                            espera=lambda s: None)
    assert not r.ok and any("Football Manager 27" in m for m in r.motivos) and not any("GTA" in m for m in r.motivos)


def test_el_nombre_del_mp4_no_lleva_la_url_del_drive():
    url = fuentes.urls_material(ficha_real())[0]
    n = mod_toma._nombre_seguro(url)
    assert "/" not in n and "?" not in n and len(n) == 10 and n == mod_toma._nombre_seguro(url)
    assert mod_toma._nombre_seguro("AbcDef-12") == "AbcDef-12"


def test_un_video_grande_se_mira_en_una_copia_ligera_y_uno_chico_tal_cual(video, tmp_path):
    assert video.stat().st_size > 14 * 1024 * 1024 * 0.9            # el sintético de 20 s pesa 18 MB
    c = mod_toma.copia_para_ojos(video, RAIZ_REAL, tmp_path)
    assert c != video and c.stat().st_size < 2 * 1024 * 1024
    assert abs(mod_componer.sondear_video(c)["duracion"] - 20.0) < 0.5      # mismos tiempos
    chico = tmp_path / "chico.mp4"
    chico.write_bytes(b"\x00" * 1024)
    assert mod_toma.copia_para_ojos(chico, RAIZ_REAL, tmp_path) == chico


# ---------------------------------------------------------------- ronda 1

def test_silencio_se_mide_de_verdad_un_tono_anadido_sale_en_rojo(marca, tmp_path):
    mudo = tmp_path / "mudo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(mudo)], check=True)
    mp4, plan = render(marca, tmp_path, mudo, sin_agua(ficha_real()), "m")
    ok, det = estilo.audio_original(mp4, plan)
    assert ok, det                                                    # fuente muda y salida silenciosa: verde
    con_tono = tmp_path / "cancion.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4), "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=12", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    str(con_tono)], check=True)
    ok, det = estilo.audio_original(con_tono, plan)
    assert not ok and "dB" in det                                     # fuente muda y salida con tono: rojo


def test_la_ficha_con_presupuesto_cero_se_cierra():
    f = copy.deepcopy(ficha_real())
    f["presupuesto_restante"] = 0
    assert fuentes.es_publicable(f, date(2026, 11, 18)) == (False, "presupuesto agotado")


def test_ficha_con_permiso_y_brief_reales():
    f = ficha_real()
    assert f["permiso_url"] == "https://contentrewards.com/c/campaigns/affff4f9-30b5-4dea-841f-69ef3f8f9c50"
    assert f["brief_url"].startswith("https://docs.google.com/document/d/1oBJA-xEJs1HrH7thU8QYcSLt5oki1f7qlsfd-fo9_ts")
    assert str(f["permiso_leido"]) == "2026-11-18"


def test_validar_rechaza_brief_url_no_https(repo, editar_yaml):
    e = _con_ficha(repo, editar_yaml, lambda f: f.update(brief_url="http://x.org/b"))
    assert any("brief_url" in x for x in e)


def _anotar_en_proceso(args):
    raiz, i = args
    envios_campana.anotar(Path(raiz), "tiktok", f"post-{i}", datetime(2026, 11, 18, 17, tzinfo=timezone.utc),
                          {"campana": "c", "numero": i})


def test_dos_procesos_anotando_a_la_vez_no_pierden_filas(tmp_path):
    import multiprocessing as mp
    with mp.get_context("spawn").Pool(4) as pool:
        pool.map(_anotar_en_proceso, [(str(tmp_path), i) for i in range(24)])
    ids = sorted(e["post_id"] for e in envios_campana.leer(tmp_path))
    assert ids == sorted(f"post-{i}" for i in range(24))


def test_completar_fusiona_sobre_la_version_vigente(tmp_path):
    pub = programar_de_campana(tmp_path, BufferConUrl())

    class Intruso:
        """Mientras `completar` consulta Buffer, otra corrida añade su fila."""
        def estado_y_url(self, post_id):
            envios_campana.anotar(tmp_path, "reels", "post-otro", AHORA_UTC, {"campana": "c", "numero": 9})
            return pub.estado_y_url(post_id)
    assert envios_campana.completar(tmp_path, Intruso()) == 1
    filas = {e["post_id"]: e for e in envios_campana.leer(tmp_path)}
    assert filas["post-ejemplo-001"]["estado"] == "enviado" and "post-otro" in filas


@pytest.mark.parametrize("red", REDES)
def test_hashtag_en_el_gancho_no_antecede_a_publicidad(red):
    tx = textos({**TOMA_FM, "creador": "DARO", "gancho": "#Fichaje Sebas ficha #FM27 al delantero"}, ficha_real(), red)
    antes = tx["texto"].split("\n#Publicidad")[0]
    assert "#" not in antes and "Sebas ficha al delantero" in antes
    lineas = tx["texto"].split("\n")
    i = lineas.index("#Publicidad")
    assert not any("#" in ln for ln in lineas[:i])                    # ningún # antes de la divulgación
    assert [w for w in tx["texto"].split() if w.startswith("#")][0] == "#Publicidad"


def test_el_valor_de_la_toma_ya_no_entra_al_texto_de_campana():
    tx = textos({**TOMA_FM, "creador": "DARO", "valor": ["Frase uno #Ad."]}, ficha_real(), "reels")
    assert "Frase uno" not in tx["texto"] and "#" not in tx["texto"].split("\n#Publicidad")[0]


def test_palabras_de_filtracion_en_el_gancho_se_descartan(marca, temporada):
    f = ficha_real()
    datos = json.loads((DATOS / "fm27_valida.json").read_text(encoding="utf-8"))
    datos["gancho"] = "Leak del fichaje de Sebas"
    datos["palabra_lapiz"] = "FICHAJE"
    ok, m = ojos.validar_respuesta(datos, marca, ojos.temporada_de(f, temporada), duracion=20.0)
    assert not ok and any("palabra a evitar 'leak'" in x for x in m)


def test_el_material_de_la_campana_va_primero_y_luego_twitch_por_vistas(repo, editar_yaml):
    sys_path = str(Path(__file__).parent)
    import sys
    sys.path.insert(0, sys_path)
    import test_n3 as N3
    editar_yaml(repo, "fuentes.yml", lambda d: d + [N3.base()])
    cands = detectar.detectar(repo, N3.AHORA, N3.cliente())
    ids = [c.fuente_id for c in cands]
    primeros = ids[:5]
    assert primeros == ["sega-fm27"] * 5 and "canal-ejemplo" in ids[5:]
    resto = [c.vistas_por_hora for c in cands[5:]]
    assert resto == sorted(resto, reverse=True)


def test_completar_sigue_con_los_demas_si_una_consulta_falla(tmp_path):
    pub = programar_de_campana(tmp_path, BufferConUrl())
    envios_campana.anotar(tmp_path, "reels", "post-malo", AHORA_UTC, {"campana": "c", "numero": 9})

    class Mixto:
        def estado_y_url(self, post_id):
            if post_id == "post-ejemplo-001":
                raise RuntimeError("caída de red")
            return "sent", "https://www.instagram.com/reel/abc/"
    assert envios_campana.completar(tmp_path, Mixto()) == 1
    filas = {e["post_id"]: e for e in envios_campana.leer(tmp_path)}
    assert filas["post-malo"]["estado"] == "enviado" and filas["post-malo"]["url"].startswith("https://www.instagram")
    assert filas["post-ejemplo-001"]["estado"] == "programado" and "caída de red" in filas["post-ejemplo-001"]["error"]


@pytest.mark.parametrize("creador", list(CREADORES))
@pytest.mark.parametrize("red", REDES)
def test_candidato_fm27_por_toma_dict_y_textos_da_la_etiqueta_de_cada_red(repo, creador, red):
    from fabrica import ciclo
    from fabrica.editar.toma import Resultado
    cand = candidatos_de_fm27(repo)[creador]
    ficha = next(f for f in fuentes.cargar_fuentes(repo) if f["id"] == "sega-fm27")
    r = Resultado(True, fila={"toma": 5, "gancho": "Sebas ficha al delantero", "pilar": "momentos",
                              "duracion_s": 12.0})
    td = ciclo._toma_dict(r, cand, ficha)
    tx = textos(td, ficha, red)
    assert tx["texto"].split("\n")[2:5] == ["@footballmanager", CREADORES[creador][RED[red]], "#Publicidad"]
    with pytest.raises(Descartada, match="sin gancho"):
        textos({**TOMA_FM, "creador": "DARO", "gancho": "#Solo #Hashtags"}, ficha_real(), "tiktok")


def _mp4_con_audio(origen: Path, destino: Path, audio_lavfi: list[str]):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(origen), *audio_lavfi,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(destino)], check=True)


def test_un_segundo_de_tono_entre_silencios_sale_en_rojo(marca, tmp_path):
    mudo = tmp_path / "mudo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(mudo)], check=True)
    mp4, plan = render(marca, tmp_path, mudo, sin_agua(ficha_real()), "m")
    corto = tmp_path / "corto.mp4"
    _mp4_con_audio(mp4, corto, ["-f", "lavfi", "-i",
                                "aevalsrc=if(between(t\\,5\\,6)\\,0.5*sin(2*PI*440*t)\\,0):s=48000:d=12"])
    ok, det = estilo.audio_original(corto, plan)
    assert not ok and "pico" in det
    assert estilo.audio_original(mp4, plan)[0]


def test_dos_pistas_de_audio_o_ffmpeg_con_error_son_rojo(marca, tmp_path):
    mudo = tmp_path / "mudo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(mudo)], check=True)
    mp4, plan = render(marca, tmp_path, mudo, sin_agua(ficha_real()), "m")
    dos = tmp_path / "dos.mkv"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4), "-i", str(mp4),
                    "-map", "0:v", "-map", "0:a", "-map", "1:a", "-c", "copy", str(dos)], check=True)
    ok, det = estilo.audio_original(dos, plan)
    assert not ok and "2 pistas" in det
    roto = tmp_path / "roto.mp4"
    roto.write_bytes(b"no es un mp4")
    ok, det = estilo.audio_original(roto, plan)
    assert not ok

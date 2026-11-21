"""Tarea #20 «historia»: plan por momentos, mapa, cadena Gemini → segundo → sin IA, orden de ffmpeg y puerta.

Todo sin red: transportes falsos. Las pruebas con ffmpeg real (testsrc + seno) van marcadas `slow` cuando renderizan.
"""
import copy
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml

from conftest import LICENCIA_FALSA, RAIZ_REAL
from fabrica import validar
from fabrica.editar import componer, estilo, historia as h, lienzo, sfx as mod_sfx, toma
from fabrica.editar.componer import Linea
from fabrica.editar.marca import Marca
from test_n4 import (AHORA, Transporte, abrir_con, candidato, ficha_propia, respuesta_grabada, sobre,
                     video20)  # noqa: F401 (video20 es un fixture de sesión)

RECORTE, RANGO = (0.0, 18.0), (7.0, 30.0)
CFG = h.cargar_config(RAIZ_REAL)
CFG_ACTIVA = {**CFG, "activo": True}


def plan_base(**cambios):
    """Plan válido: apertura en el último 40 % de 0-18 s; zoom, pausa y cámara lenta (18 + 2 + 1.2 + 0.4 = 21.6 s)."""
    p = {"apertura": {"t0": 12.0, "t1": 14.0},
         "momentos": [{"t0": 2.0, "t1": 4.0, "sentimiento": "tension", "tratamiento": "zoom_suave", "sfx": None},
                      {"t0": 5.0, "t1": 6.0, "sentimiento": "tension", "tratamiento": "pausa", "sfx": None},
                      {"t0": 8.0, "t1": 9.2, "sentimiento": "asombro", "tratamiento": "camara_lenta", "sfx": None}]}
    p.update(cambios)
    return p


def validar_(plan, recorte=RECORTE, rango=RANGO, sfx=(), ficha=None, cfg=None):
    return h.validar_plan(plan, recorte, rango, list(sfx), ficha, cfg)


def momento(t0, t1, tratamiento="ninguno", sentimiento="tension", sfx=None):
    return {"t0": t0, "t1": t1, "sentimiento": sentimiento, "tratamiento": tratamiento, "sfx": sfx}


# ---------------------------------------------------------------- A. config y esquema

def test_la_config_del_repo_valida_y_coincide_con_los_valores_por_defecto():
    assert validar.validar(RAIZ_REAL).errores == []
    cfg = yaml.safe_load((RAIZ_REAL / "config" / "historia.yml").read_text(encoding="utf-8"))
    assert {k: v for k, v in cfg.items() if k not in ("segundo", "activo")} \
        == {k: v for k, v in h.DEFAULTS.items() if k not in ("segundo", "activo")}
    assert cfg["segundo"]["activo"] is False and cfg["segundo"]["motivo"] == "GitHub Models retirado el 30 jul 2026"
    assert h.MIN_LINEA_S == toma.MIN_LINEA_S


@pytest.mark.parametrize("cambio,texto", [
    (lambda d: d.update(activo="si"), "activo"),
    (lambda d: d.update(zoom_max=1.0), "zoom_max"),
    (lambda d: d.update(pausa_s=0.5), "pausa_s"),
    (lambda d: d.update(apertura_min_s=4.0), "apertura_min_s"),
    (lambda d: d.update(momentos_min=5), "momentos_min"),
    (lambda d: (d.pop("sfx_en_campanas"), None)[1], "sfx_en_campanas"),
    (lambda d: d["segundo"].update(motivo="otra cosa"), "segundo.motivo"),
    (lambda d: d["segundo"].update(activo=True), "segundo.url"),
    (lambda d: d["segundo"].update(activo=True, url="https://x/v1/chat/completions", modelo="m", secreto="clave-en-minusculas"),
     "variable de entorno"),
    (lambda d: d["segundo"].update(tiempo_s=0), "segundo.tiempo_s"),
    (lambda d: (d.pop("segundo"), None)[1], "segundo"),
])
def test_validar_rechaza_una_config_de_historia_mala(repo, editar_yaml, cambio, texto):
    editar_yaml(repo, "config/historia.yml", cambio)
    assert any("historia.yml" in e and texto in e for e in validar.validar(repo).errores)


def test_validar_acepta_el_segundo_proveedor_bien_configurado(repo, editar_yaml):
    editar_yaml(repo, "config/historia.yml", lambda d: d["segundo"].update(
        activo=True, url="https://api.ejemplo.test/v1/chat/completions", modelo="modelo-x", secreto="SEGUNDO_CLAVE"))
    assert validar.validar(repo).errores == []


def test_el_plan_valido_pasa_y_su_forma_es_la_del_esquema():
    ok, motivos = validar_(plan_base())
    assert ok and motivos == []
    assert h.sin_texto_propio(plan_base())[0]


@pytest.mark.parametrize("cambio,texto", [
    (lambda p: p.update(titulo="Un texto mío"), "claves fuera del esquema"),
    (lambda p: p["momentos"][0].update(texto="hola"), "claves fuera del esquema"),
    (lambda p: p["apertura"].update(nota="x"), "claves fuera del esquema"),
    (lambda p: p["momentos"][0].update(sentimiento="Me gusta mucho"), "lista cerrada"),
    (lambda p: p["momentos"][0].update(tratamiento="explosion"), "lista cerrada"),
    (lambda p: p["momentos"][0].update(sfx="Pon un sonido de explosión!"), "no es un id de efecto"),
    (lambda p: p["momentos"][0].pop("sfx"), "falta sfx"),
    (lambda p: p.pop("apertura"), "faltan claves"),
    (lambda p: p["apertura"].update(t0="12"), "no es un número"),
])
def test_sin_texto_inventado_el_plan_se_rechaza_si_trae_algo_fuera_del_esquema(cambio, texto):
    p = plan_base()
    cambio(p)
    ok, motivos = validar_(p)
    assert not ok and any(texto in m for m in motivos), motivos
    assert not h.sin_texto_propio(p)[0]


def test_un_plan_que_no_es_objeto_se_rechaza():
    assert validar_([1, 2])[0] is False and validar_(None)[0] is False


# ---------------------------------------------------------------- A. validar_plan: cada regla con su caso rojo

def rojo(cambio, texto, **kw):
    p = plan_base()
    cambio(p)
    ok, motivos = validar_(p, **kw)
    assert not ok and any(texto in m for m in motivos), motivos


def test_apertura_de_1_a_3_segundos():
    rojo(lambda p: p["apertura"].update(t0=12.0, t1=12.5), "apertura de 0.50 s")
    rojo(lambda p: p["apertura"].update(t0=12.0, t1=15.5), "apertura de 3.50 s")
    assert validar_(plan_base(apertura={"t0": 12.0, "t1": 13.0}))[0]
    assert validar_(plan_base(apertura={"t0": 12.0, "t1": 15.0}))[0]


def test_apertura_dentro_del_recorte_y_en_su_ultimo_40_por_ciento():
    rojo(lambda p: p["apertura"].update(t0=17.0, t1=19.0), "fuera del recorte")
    rojo(lambda p: p["apertura"].update(t0=5.0, t1=7.0), "último 40 %")
    rojo(lambda p: p["apertura"].update(t0=10.0, t1=12.0), "último 40 %")
    assert validar_(plan_base(apertura={"t0": 10.8, "t1": 12.8}))[0]


def test_de_2_a_4_momentos():
    rojo(lambda p: p.update(momentos=p["momentos"][:1]), "1 momentos")
    p = plan_base()
    p["momentos"] = [momento(1 + 3 * i, 3 + 3 * i, "zoom_suave" if i == 0 else "ninguno") for i in range(5)]
    ok, motivos = validar_(p)
    assert not ok and any("5 momentos" in m for m in motivos)


def test_momentos_ordenados_sin_solaparse_dentro_del_recorte_y_de_08_s_o_mas():
    rojo(lambda p: p["momentos"].reverse(), "no está ordenado")
    rojo(lambda p: p["momentos"][1].update(t0=3.5, t1=4.5), "se solapa")
    rojo(lambda p: p["momentos"][0].update(t0=-1.0), "fuera del recorte")
    rojo(lambda p: p["momentos"][2].update(t0=17.5, t1=19.0), "fuera del recorte")
    rojo(lambda p: p["momentos"][1].update(t0=5.0, t1=5.7), "mínimo 0.8 s")
    assert validar_(plan_base(momentos=[momento(2, 2.8, "zoom_suave"), momento(4, 4.8)]))[0]


def test_al_menos_un_momento_con_tratamiento():
    rojo(lambda p: p.update(momentos=[momento(2, 4), momento(6, 8)]), "al menos uno")


def test_camara_lenta_maximo_una_y_de_15_s_o_menos_de_fuente():
    p = plan_base()
    p["momentos"].append(momento(10, 11, "camara_lenta"))
    ok, motivos = validar_(p)
    assert not ok and any("2 cámaras lentas" in m for m in motivos)
    rojo(lambda p: p["momentos"][2].update(t0=8.0, t1=9.6), "cámara lenta de 1.60 s")
    assert validar_(plan_base(momentos=[momento(2, 4, "zoom_suave"), momento(8, 9.5, "camara_lenta")]))[0]


def test_pausa_maximo_una():
    p = plan_base()
    p["momentos"].append(momento(10, 11, "pausa"))
    ok, motivos = validar_(p)
    assert not ok and any("2 pausas" in m for m in motivos)


def test_zoom_suave_maximo_dos():
    p = plan_base()
    p["momentos"][1]["tratamiento"] = "zoom_suave"
    p["momentos"][2]["tratamiento"] = "zoom_suave"
    ok, motivos = validar_(p)
    assert not ok and any("3 zooms suaves" in m for m in motivos)
    p["momentos"][2]["tratamiento"] = "ninguno"
    assert validar_(p)[0]


def test_sfx_maximo_dos_con_id_verificado_y_disponible():
    p = plan_base(momentos=[momento(2, 3, "sfx", sfx="golpe-a"), momento(5, 6, "sfx", sfx="golpe-a"),
                            momento(8, 9, "sfx", sfx="golpe-a")])
    ok, motivos = validar_(p, sfx=["golpe-a"])
    assert not ok and any("3 efectos" in m for m in motivos)
    p["momentos"][2] = momento(8, 9, "ninguno")
    assert validar_(p, sfx=["golpe-a"])[0]
    ok, motivos = validar_(p, sfx=[])
    assert not ok and any("no está verificado y disponible" in m for m in motivos)
    rojo(lambda q: q["momentos"][0].update(tratamiento="sfx", sfx=None), "pide el id de un efecto", sfx=["golpe-a"])
    rojo(lambda q: q["momentos"][0].update(sfx="golpe-a"), "sólo va con tratamiento sfx", sfx=["golpe-a"])


def test_sfx_prohibido_con_sin_musica_y_en_campanas_mientras_la_config_no_diga_otra_cosa():
    p = plan_base(momentos=[momento(2, 3, "sfx", sfx="golpe-a"), momento(5, 6, "zoom_suave")])
    sin_musica = {"id": "f", "tipo": "B", "reglas": {"sin_musica": True}}
    campana_cr = {"id": "c", "tipo": "A", "cr_campaign_id": "abc", "permiso_url": "https://x"}
    campana_url = {"id": "c2", "tipo": "A", "permiso_url": "https://contentrewards.com/c/campaigns/1"}
    for ficha in (sin_musica, campana_cr, campana_url):
        ok, motivos = validar_(p, sfx=["golpe-a"], ficha=ficha)
        assert not ok and any("prohibidos" in m for m in motivos), ficha
    permitida = {"id": "b", "tipo": "B", "permiso_url": "https://creativecommons.org/licenses/by/3.0/"}
    assert validar_(p, sfx=["golpe-a"], ficha=permitida)[0]
    assert validar_(p, sfx=["golpe-a"], ficha=campana_cr, cfg={"sfx_en_campanas": True})[0]


def test_duracion_de_salida_dentro_del_rango_de_la_ficha():
    p = plan_base()          # 18 + 2 + 1.2 + 0.4 = 21.6 s
    assert h.duracion_esperada(p, RECORTE) == pytest.approx(21.6)
    ok, motivos = validar_(p, rango=(7.0, 20.0))
    assert not ok and any("21.60 s" in m and "7 y 20" in m for m in motivos)
    assert not validar_(p, rango=(25.0, 30.0))[0]
    assert validar_(p, rango=(21.6, 21.6))[0]


def test_debe_quedar_un_tramo_plano_de_2_s():
    p = plan_base(apertura={"t0": 12.0, "t1": 13.0},
                  momentos=[momento(0, 6, "zoom_suave"), momento(6, 12, "zoom_suave"),
                            momento(12, 15, "sfx", sfx="golpe-a"), momento(15, 16.5, "camara_lenta")])
    ok, motivos = validar_(p, sfx=["golpe-a"])
    assert not ok and any("tramo plano" in m for m in motivos)
    p["momentos"][3]["t1"] = 15.8                 # queda un tramo plano de 2.2 s al final
    p["momentos"][3]["t0"] = 15.0
    p["momentos"][3]["t1"] = 15.8
    assert validar_(p, sfx=["golpe-a"])[0]


# ---------------------------------------------------------------- A. mapa, fuente_en, remapear_lineas

def test_mapa_tramos_de_salida():
    t = h.mapa(plan_base(), 0.0, 18.0)
    resumen = [(x.s0, x.s1, x.f0, x.f1, x.velocidad, x.zoom) for x in t]
    assert resumen[0] == (0.0, 2.0, 12.0, 14.0, 1.0, None)                       # la apertura va primero
    assert resumen[1] == (2.0, 4.0, 0.0, 2.0, 1.0, None)                         # lo que va antes del primer momento
    assert resumen[2] == (4.0, 6.0, 2.0, 4.0, 1.0, (1.0, 1.12))                  # zoom suave
    assert resumen[3] == (6.0, 7.0, 4.0, 5.0, 1.0, None)
    assert resumen[4] == (7.0, 8.0, 5.0, 6.0, 1.0, None)                         # el momento de la pausa
    assert resumen[5] == (8.0, 8.4, 6.0, 6.0, 0.0, None)                         # congelado de 0.4 s con el último cuadro
    assert resumen[6] == (8.4, 10.4, 6.0, 8.0, 1.0, None)
    assert resumen[7] == (10.4, 12.8, 8.0, 9.2, 0.5, None)                       # cámara lenta: el doble
    assert resumen[-1][1] == h.duracion_salida(t) == pytest.approx(21.6)
    assert resumen[-1][2:4] == (9.2, 18.0) and len(t) == 9
    assert all(a.s1 == b.s0 for a, b in zip(t, t[1:]))                           # sin huecos ni solapes
    assert all(round(x.s0 * 30) / 30 == pytest.approx(x.s0) for x in t)          # bordes en cuadros de 30 fps


def test_mapa_es_pura_y_marca_el_sfx_del_tramo():
    p = plan_base(momentos=[momento(2, 4, "sfx", sfx="golpe-a"), momento(8, 9, "zoom_suave")])
    a, b = h.mapa(p, 0.0, 18.0), h.mapa(copy.deepcopy(p), 0.0, 18.0)
    assert a == b and [x.sfx for x in a if x.sfx] == ["golpe-a"]
    assert [x for x in a if x.sfx][0].plano is False
    assert h.mapa(p, 0.0, 18.0, zoom_max=1.1)[-2].zoom == (1.0, 1.1)


def test_fuente_en_traduce_el_tiempo_de_salida_a_la_fuente():
    t = h.mapa(plan_base(), 0.0, 18.0)
    assert h.fuente_en(t, 0.5) == (12.5, 1.0)                    # apertura
    assert h.fuente_en(t, 3.0) == (1.0, 1.0)                     # tramo plano
    f, z = h.fuente_en(t, 5.0)                                   # a mitad del zoom
    assert f == pytest.approx(3.0) and z == pytest.approx(1.06)
    assert h.fuente_en(t, 8.2)[0] == 6.0                         # congelado: el cuadro del final del momento
    assert h.fuente_en(t, 11.4)[0] == pytest.approx(8.0 + 1.0 * 0.5)   # cámara lenta a mitad de velocidad
    assert h.fuente_en(t, 20.0)[0] == pytest.approx(9.2 + (20.0 - 12.8))


def test_instante_plano_y_tramo_plano_mas_largo():
    t = h.mapa(plan_base(), 0.0, 18.0)
    assert h.tramo_plano_largo(t).dur == pytest.approx(21.6 - 12.8)
    c = h.instante_plano(t, 5.0)                                  # 5.0 cae en el zoom: se corre al tramo plano
    assert c is not None and next(x for x in t if x.s0 <= c < x.s1).plano
    assert h.instante_plano(t, 5.0, desde=12.0) >= 12.0
    assert h.instante_plano(t, 5.0, tope=0.1) is None


def lineas_ejemplo():
    return [Linea(0.5, 1.5, "A", "uno"), Linea(2.5, 3.5, "B", "dos"), Linea(10.0, 12.0, "C", "tres")]


def test_remapear_lineas_pasa_las_lineas_al_tiempo_de_salida_y_repite_en_la_apertura():
    t = h.mapa(plan_base(), 0.0, 18.0)
    out = h.remapear_lineas([Linea(0.5, 1.5, "A", "uno"), Linea(12.5, 13.5, "Z", "final")], t)
    assert [(ln.nombre, ln.t0, ln.t1) for ln in out] == [
        ("Z", pytest.approx(0.5), pytest.approx(1.5)),      # la repetición recortada a la apertura (mismo texto)
        ("A", pytest.approx(2.5), pytest.approx(3.5)),      # el tramo principal, desplazado por la apertura
        ("Z", pytest.approx(16.1), pytest.approx(17.1))]    # y la misma línea en su sitio del flujo principal
    assert [ln.frase for ln in out] == ["final", "uno", "final"]
    assert all(a.t1 <= b.t0 + 1e-9 for a, b in zip(out, out[1:]))


def test_remapear_lineas_con_camara_lenta_y_pausa_dentro_de_la_linea():
    t = h.mapa(plan_base(), 0.0, 18.0)
    ln = h.remapear_lineas([Linea(4.5, 9.0, "A", "larga")], t)[0]
    assert ln.t0 == pytest.approx(6.5) and ln.t1 == pytest.approx(12.4)
    assert ln.t1 - ln.t0 > 9.0 - 4.5                            # dura más: lleva la pausa y la cámara lenta dentro


def test_remapear_lineas_descarta_las_cortas_respeta_el_tope_y_no_inventa_texto():
    t = h.mapa(plan_base(), 0.0, 18.0)
    assert h.remapear_lineas([Linea(0.5, 0.7, "A", "cortita")], t) == []         # 0.2 s < MIN_LINEA_S
    out = h.remapear_lineas([Linea(16.0, 17.9, "A", "al final")], t, tope=21.1)
    assert out and out[0].t1 == pytest.approx(21.1) and out[0].frase == "al final"
    assert h.remapear_lineas([], t) == []
    assert h.remapear_lineas([{"t0": 0.5, "t1": 1.5, "nombre": "A"}], t)[0]["nombre"] == "A"      # también diccionarios
    solapadas = h.remapear_lineas([Linea(1.0, 3.0, "A", "x"), Linea(2.0, 4.0, "B", "y")], t)
    assert [(ln.t0, ln.t1) for ln in solapadas] == [(pytest.approx(3.0), pytest.approx(5.0)),
                                                    (pytest.approx(5.0), pytest.approx(6.0))]


# ---------------------------------------------------------------- B. sin IA

def senales_sinteticas(n=36, picos=((9, 8.0), (30, 12.0)), escenas=(4.0,)):
    rms = [100.0] * n
    for i, v in picos:
        rms[i] = v * 100
    return {"inicio": 0.0, "paso": 0.5, "rms": rms, "escenas": list(escenas), "detector": "prueba"}


def test_sin_ia_es_determinista_y_el_plan_valida():
    s = senales_sinteticas()
    a = h.plan_sin_ia(s, RECORTE, RANGO, sfx_golpe="golpe-a", sfx_ids=["golpe-a"])
    b = h.plan_sin_ia(copy.deepcopy(s), RECORTE, RANGO, sfx_golpe="golpe-a", sfx_ids=["golpe-a"])
    assert a is not None and a == b
    assert validar_(a, sfx=["golpe-a"])[0]
    assert h.sin_texto_propio(a)[0]


def test_sin_ia_la_apertura_es_2_s_alrededor_del_pico_mas_alto_del_ultimo_40_por_ciento():
    plan = h.plan_sin_ia(senales_sinteticas(), RECORTE, RANGO, sfx_golpe=None)
    ap = plan["apertura"]
    assert ap["t1"] - ap["t0"] == pytest.approx(2.0)
    assert ap["t0"] <= 15.25 <= ap["t1"]                       # el pico de la ventana 30 (15.25 s)
    assert ap["t0"] >= 18.0 * 0.6 - 1e-6


def test_sin_ia_reglas_de_tratamiento():
    con = h.plan_sin_ia(senales_sinteticas(), RECORTE, RANGO, sfx_golpe="golpe-a", sfx_ids=["golpe-a"])
    trat = [m["tratamiento"] for m in con["momentos"]]
    assert "sfx" in trat and "camara_lenta" in trat and "pausa" in trat
    lenta = next(m for m in con["momentos"] if m["tratamiento"] == "camara_lenta")
    assert lenta["t1"] - lenta["t0"] <= 1.5 and lenta["t0"] >= 0.6 * 18.0 - 1.0          # el pico corto cerca del final
    golpe = next(m for m in con["momentos"] if m["tratamiento"] == "sfx")
    assert golpe["sfx"] == "golpe-a" and golpe["t0"] <= 4.75 <= golpe["t1"]
    sin = h.plan_sin_ia(senales_sinteticas(), RECORTE, RANGO, sfx_golpe=None)
    assert "sfx" not in [m["tratamiento"] for m in sin["momentos"]] and "zoom_suave" in \
        [m["tratamiento"] for m in sin["momentos"]]
    pausa = next(m for m in con["momentos"] if m["tratamiento"] == "pausa")
    assert pausa["t1"] <= con["apertura"]["t1"] and pausa["sfx"] is None


def test_sin_ia_sin_senales_sigue_dando_un_plan_valido():
    plan = h.plan_sin_ia({"inicio": 0.0, "paso": 0.5, "rms": [], "escenas": []}, RECORTE, RANGO)
    assert plan is not None and validar_(plan)[0]
    assert len(plan["momentos"]) >= 2


def test_sin_ia_sin_musica_y_campana_nunca_llevan_efectos():
    for ficha in ({"tipo": "B", "reglas": {"sin_musica": True}}, {"tipo": "A", "cr_campaign_id": "x"}):
        plan = h.plan_sin_ia(senales_sinteticas(), RECORTE, RANGO, sfx_golpe="golpe-a", ficha=ficha,
                             sfx_ids=["golpe-a"])
        assert plan is not None and all(m["sfx"] is None and m["tratamiento"] != "sfx" for m in plan["momentos"])


def test_sin_ia_si_no_cabe_la_pausa_o_la_camara_lenta_se_prueba_sin_ellas():
    s = senales_sinteticas()
    # el rango deja sólo 21.0 s: 18 + 2 + 1.4 + 0.4 no cabe, sin la pausa (20 + 1.4 = 21.4) tampoco; sin cámara lenta sí
    plan = h.plan_sin_ia(s, RECORTE, (7.0, 21.0), sfx_golpe=None)
    assert plan is not None
    assert "camara_lenta" not in [m["tratamiento"] for m in plan["momentos"]]
    assert h.duracion_esperada(plan, RECORTE) <= 21.0 + 1e-6
    assert h.plan_sin_ia(s, RECORTE, (7.0, 19.0), sfx_golpe=None) is None


def test_senales_usan_ffmpeg_si_scenedetect_no_esta_y_scenedetect_si_esta(monkeypatch, video20):
    monkeypatch.setitem(__import__("sys").modules, "scenedetect", None)       # ImportError al importarlo
    s = h.senales(video20, 2.0, 14.0)
    assert s["detector"] == "ffmpeg" and len(s["rms"]) == 24 and s["inicio"] == 2.0

    class TC:
        def __init__(self, s):
            self.s = s

        def get_seconds(self):
            return self.s

    falso = type("M", (), {"ContentDetector": staticmethod(lambda: object()),
                           "detect": staticmethod(lambda ruta, det, start_time, end_time:
                                                  [(TC(2.0), TC(6.0)), (TC(6.0), TC(9.5)), (TC(9.5), TC(14.0))])})
    monkeypatch.setitem(__import__("sys").modules, "scenedetect", falso)
    s2 = h.senales(video20, 2.0, 14.0)
    assert s2["detector"] == "scenedetect" and s2["escenas"] == [6.0, 9.5]

    def rota(*a, **k):
        raise RuntimeError("opencv no pudo abrir")
    monkeypatch.setattr(falso, "detect", staticmethod(rota))
    assert h.senales(video20, 2.0, 14.0)["detector"] == "ffmpeg"                 # respaldo, no falla


# ---------------------------------------------------------------- B. la cadena (transportes falsos)

SEG_ACTIVO = {**h.DEFAULTS["segundo"], "activo": True, "url": "https://api.ejemplo.test/v1/chat/completions",
              "modelo": "modelo-x", "secreto": "SEGUNDO_CLAVE", "nombre": "segundo-x"}
CLAVE_SEGUNDO = "esta-es-una-clave-de-prueba-12345"          # EJEMPLO-FALSO


def planificar_(cfg=None, **kw):
    base = dict(raiz=RAIZ_REAL, ficha=None, recorte=RECORTE, rango_duracion=RANGO, sfx={},
                sen=senales_sinteticas(), cfg=cfg or CFG_ACTIVA)
    base.update(kw)
    return h.planificar(**base)


def test_gemini_valido_gana_y_viaja_en_la_misma_llamada_de_los_ojos():
    r = planificar_(datos_gemini={"historia": plan_base()})
    assert r.proveedor == "gemini" and r.plan == plan_base() and r.intentos == ["gemini: plan válido"]


def test_gemini_invalido_cae_al_segundo_apagado_y_luego_al_sin_ia():
    malo = plan_base(titulo="un texto")
    r = planificar_(datos_gemini={"historia": malo})
    assert r.proveedor == "sin-ia" and r.plan is not None and r.detector == "prueba"
    assert r.intentos[0].startswith("gemini: plan no válido")
    assert "segundo: apagado (GitHub Models retirado el 30 jul 2026)" in r.intentos
    r2 = planificar_(datos_gemini={"motivos": []})
    assert r2.intentos[0] == "gemini: sin plan de historia en la respuesta" and r2.proveedor == "sin-ia"


def test_si_ningun_eslabon_valida_no_hay_plan_y_el_motivo_lo_dice():
    r = planificar_(sen=senales_sinteticas(), rango_duracion=(7.0, 12.0))
    assert r.plan is None and r.proveedor is None and r.motivo
    assert r.resumen() == {"proveedor": None, "motivo": r.motivo, "intentos": r.intentos}
    assert planificar_(cfg={**CFG, "activo": False}).plan is None
    sin_senales = h.planificar(raiz=RAIZ_REAL, ficha=None, recorte=RECORTE, rango_duracion=RANGO, cfg=CFG_ACTIVA)
    assert sin_senales.plan is None and "sin señales" in sin_senales.motivo


class FalsoHTTP:
    def __init__(self, *respuestas):
        self.cola, self.llamadas = list(respuestas), []

    def __call__(self, url, encabezados, cuerpo, tiempo):
        self.llamadas.append((url, encabezados, json.loads(cuerpo), tiempo))
        r = self.cola.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def sobre_chat(plan):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": json.dumps(plan)}}]})


CFG_SEGUNDO = {**CFG_ACTIVA, "segundo": SEG_ACTIVO}
FOTOS = [b"\xff\xd8\xff-jpeg-" + bytes([i]) for i in range(8)]


def con_segundo(transporte, **kw):
    return planificar_(cfg=CFG_SEGUNDO, transporte=transporte, entorno={"SEGUNDO_CLAVE": CLAVE_SEGUNDO},
                       fotogramas=FOTOS, lineas=[Linea(1.0, 2.0, "A", "x")], **kw)


def test_segundo_proveedor_200_valido_gana():
    h.reiniciar_contador()
    t = FalsoHTTP((200, sobre_chat(plan_base())))
    r = con_segundo(t)
    assert r.proveedor == "segundo" and r.plan == plan_base()
    url, enc, cuerpo, tiempo = t.llamadas[0]
    assert url == SEG_ACTIVO["url"] and enc["Authorization"] == f"Bearer {CLAVE_SEGUNDO}" and 0 < tiempo <= 60
    assert cuerpo["response_format"] == {"type": "json_object"} and cuerpo["model"] == "modelo-x"
    imagenes = [p for p in cuerpo["messages"][1]["content"] if p["type"] == "image_url"]
    assert len(imagenes) == 6 and all(p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in imagenes)
    texto = next(p["text"] for p in cuerpo["messages"][1]["content"] if p["type"] == "text")
    assert '"lineas"' in texto and '"rms"' in texto and '"escenas"' in texto            # líneas y señales de audio/escena
    assert "historia" in cuerpo["messages"][0]["content"]                              # la sección 9 del prompt


@pytest.mark.slow
def test_segundo_proveedor_recibe_las_senales_del_video_si_no_se_las_dieron(video20):
    h.reiniciar_contador()
    t = FalsoHTTP((200, sobre_chat(plan_base())))
    r = h.planificar(raiz=RAIZ_REAL, ficha=None, recorte=(2.0, 14.0), rango_duracion=RANGO, cfg=CFG_SEGUNDO,
                     ruta_video=video20, transporte=t, entorno={"SEGUNDO_CLAVE": CLAVE_SEGUNDO}, fotogramas=FOTOS)
    texto = next(p["text"] for p in t.llamadas[0][2]["messages"][1]["content"] if p["type"] == "text")
    assert '"rms"' in texto and len(json.loads(texto.split(": ", 1)[1])["senales"]["rms"]) == 24
    assert r.proveedor in ("segundo", "sin-ia")


def test_segundo_proveedor_200_invalido_cae_al_sin_ia():
    h.reiniciar_contador()
    r = con_segundo(FalsoHTTP((200, sobre_chat(plan_base(extra="x")))))
    assert r.proveedor == "sin-ia" and any("segundo-x: plan no válido" in i for i in r.intentos)
    h.reiniciar_contador()
    r = con_segundo(FalsoHTTP((200, "esto no es json")))
    assert r.proveedor == "sin-ia" and any("sin un plan JSON legible" in i for i in r.intentos)


def test_segundo_proveedor_429_5xx_y_red_son_pasajeros():
    for resp in ((429, "{}"), (503, "{}"), ErrorRed()):
        h.reiniciar_contador()
        r = con_segundo(FalsoHTTP(resp))
        assert r.proveedor == "sin-ia" and any("pasajero" in i for i in r.intentos), resp
    h.reiniciar_contador()
    assert any("HTTP 400" in i for i in con_segundo(FalsoHTTP((400, "{}"))).intentos)


def ErrorRed():
    return h.ErrorHistoria("fallo de conexión con el segundo proveedor: TimeoutError")


def test_segundo_proveedor_sin_secreto_no_llama_y_nunca_imprime_el_secreto(capsys):
    h.reiniciar_contador()

    def no_llamar(*a, **k):
        raise AssertionError("se llamó sin secreto")
    r = planificar_(cfg=CFG_SEGUNDO, transporte=no_llamar, entorno={}, fotogramas=FOTOS)
    assert r.proveedor == "sin-ia" and any("sin secreto" in i for i in r.intentos)
    h.reiniciar_contador()
    r = con_segundo(FalsoHTTP((401, "{}"), ))
    salida = capsys.readouterr()
    assert CLAVE_SEGUNDO not in salida.out + salida.err + json.dumps(r.intentos) + json.dumps(r.resumen())


def test_segundo_proveedor_respeta_el_tope_por_ciclo_y_el_plazo(monkeypatch):
    h.reiniciar_contador()
    t = FalsoHTTP((200, sobre_chat(plan_base(extra="x"))), (200, sobre_chat(plan_base())))
    con_segundo(t)
    r2 = con_segundo(t)                              # max_por_ciclo: 1
    assert len(t.llamadas) == 1 and any("tope de llamadas" in i for i in r2.intentos)
    h.reiniciar_contador()
    monkeypatch.setattr(h.plazo, "restante", lambda: 8.0)           # quedan 8 s: menos de 5 s útiles tras el margen
    t2 = FalsoHTTP((200, sobre_chat(plan_base())))
    r3 = con_segundo(t2)
    assert not t2.llamadas and any("sin tiempo" in i for i in r3.intentos)
    monkeypatch.setattr(h.plazo, "restante", lambda: 30.0)
    h.reiniciar_contador()
    t3 = FalsoHTTP((200, sobre_chat(plan_base())))
    con_segundo(t3)
    assert t3.llamadas[0][3] == pytest.approx(25.0)                  # el tiempo de la config recortado al plazo


@pytest.mark.slow
def test_fotogramas_jpeg_son_6_y_de_512_px_o_menos(video20):
    from io import BytesIO
    from PIL import Image
    fotos = h.fotogramas_jpeg(video20, 2.0, 14.0)
    assert len(fotos) == 6
    for f in fotos:
        im = Image.open(BytesIO(f))
        assert im.format == "JPEG" and max(im.size) <= 512


# ---------------------------------------------------------------- F. las muestras

def test_las_muestras_de_historia_son_las_dos_pedidas_y_estan_autorizadas():
    from datetime import date
    from fabrica import fuentes
    from fabrica.editar import muestras
    ms = muestras.cargar_muestras_historia()
    assert [m["id"] for m in ms] == ["historia-bbb", "historia-sintel"]
    assert [m["historia"]["eslabon"] for m in ms] == ["sin-ia", "fijo"]
    for m in ms:
        muestras.autorizar(m, fuentes.cargar_fuentes(), date(2026, 11, 21))      # lista blanca de fuentes.yml
        assert m["fuente_id"] in ("sintel-cc-by", "bbb-cc-by") and m["video"]["sha256"]
    assert [m["id"] for m in muestras.cargar_muestras()] == ["dialogo-sintel", "momento-bbb", "pajaro-bbb"]


def test_la_muestra_sintel_lleva_camara_lenta_pausa_zoom_suave_y_un_efecto():
    from fabrica.editar import muestras
    m = muestras.cargar_muestras_historia()[1]
    ini = m["recorte"]["inicio"]
    rec = (ini, ini + m["recorte"]["duracion"])
    con, avisos = muestras.resolver_sfx(m["historia"]["plan"], {"golpe-a": {"id": "golpe-a", "tipo": "golpe"}})
    assert avisos == [] and sorted(x["tratamiento"] for x in con["momentos"]) == ["camara_lenta", "pausa", "sfx", "zoom_suave"]
    assert validar_(con, recorte=rec, sfx=["golpe-a"])[0]
    sin, avisos = muestras.resolver_sfx(m["historia"]["plan"], {})          # sin efectos del release: zoom_suave y el aviso
    assert len(avisos) == 1 and "zoom_suave" in avisos[0] and "sfx" not in [x["tratamiento"] for x in sin["momentos"]]
    assert validar_(sin, recorte=rec)[0]


# ---------------------------------------------------------------- el prompt y el esquema de Gemini

def test_el_prompt_lleva_la_seccion_9_solo_con_historia_activa():
    from fabrica import ojos
    marca = Marca.cargar(RAIZ_REAL)
    temporada = yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))
    sin = ojos.instruccion(marca, temporada, 20.0)
    assert "9. `historia`" not in sin and "<!--" not in sin and "{{" not in sin
    con = ojos.instruccion(marca, temporada, 20.0, historia={"cfg": CFG_ACTIVA, "sfx": ["golpe-a", "whoosh-b"]})
    assert "9. `historia`" in con and "{{" not in con and "<!--" not in con
    assert "golpe-a, whoosh-b" in con and "último 40 %" in con
    assert "tension" in con and "camara_lenta" in con and "1.12" in con
    prohibido = ojos.instruccion(marca, temporada, 20.0, historia={"cfg": CFG_ACTIVA, "sfx": ["x"],
                                                                   "prohibe_sfx": "la ficha tiene reglas.sin_musica"})
    assert "Sin efectos en esta pieza" in prohibido and "Efectos disponibles" not in prohibido
    assert "No hay efectos de sonido disponibles" in ojos.instruccion(marca, temporada, 20.0,
                                                                      historia={"cfg": CFG_ACTIVA, "sfx": []})


def test_esquema_de_gemini_trae_historia_opcional_con_listas_cerradas():
    from fabrica import ojos
    e = ojos.esquema_respuesta()
    assert "historia" in e["properties"] and "historia" not in e["required"]
    mo = e["properties"]["historia"]["properties"]["momentos"]["items"]["properties"]
    assert mo["tratamiento"]["enum"] == list(h.TRATAMIENTOS) and mo["sentimiento"]["enum"] == list(h.SENTIMIENTOS)


def test_validar_respuesta_no_rechaza_la_toma_por_la_historia():
    from fabrica import ojos
    marca = Marca.cargar(RAIZ_REAL)
    temporada = yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))
    datos = json.loads(respuesta_grabada("valida"))
    base = ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)
    datos["historia"] = {"basura": [1, 2, 3]}
    assert ojos.validar_respuesta(datos, marca, temporada, duracion=20.0) == base == (True, [])


# ---------------------------------------------------------------- C. la orden de ffmpeg (pura)

def orden(plan=None, sfx=None, placas_=None, recorte=RECORTE):
    plan = plan or plan_base()
    tramos = h.mapa(plan, recorte[0], recorte[1])
    video = {"ruta": "v.mp4", "inicio": recorte[0], "recorte": {"x": 218, "y": 0, "ancho": 202, "alto": 360}}
    return h.orden_ffmpeg(salida="s.mp4", ancho=1080, alto=1920, video=video, tramos=tramos,
                          placas=placas_ if placas_ is not None else [("a.png", 60, 150, 0.0, 21.6)], sfx=sfx)


def test_la_orden_es_una_sola_llamada_de_ffmpeg_con_la_apertura_aparte():
    cmd = orden()
    assert cmd[0] == "ffmpeg" and cmd.count("ffmpeg") == 1 and cmd.count("-filter_complex") == 1
    txt = " ".join(cmd)
    assert "-ss 12 -t 2 -i v.mp4 -ss 0 -t 18 -i v.mp4" in txt         # entrada 0: la apertura; entrada 1: el recorte
    assert cmd[-1] == "s.mp4" and "-t 21.6" in txt and "-r 30" in txt and "libx264" in txt and "-movflags +faststart" in txt
    assert "-map [out] -map [ac]" in txt


def test_la_orden_corta_cada_tramo_y_une_con_concat_sin_fundidos():
    cmd = orden()
    txt = " ".join(cmd)
    n = len(h.mapa(plan_base(), 0.0, 18.0))
    assert f"concat=n={n}:v=1:a=1[vc][ac]" in txt
    assert txt.count("trim=start=") >= n - 2 and txt.count("atrim=start=") >= n - 3
    for prohibido in ("xfade", "fade", "acrossfade", "drawtext", "subtitles", "gradient"):
        assert prohibido not in txt
    assert "overlay=60:150:enable='gte(t,0)*lt(t,21.6)'" in txt
    assert "crop=202:360:218:0,scale=1080:1920:flags=lanczos,setsar=1" in txt


def test_camara_lenta_es_setpts_x2_y_atempo_antes_de_fps_30():
    filtros = next(c for c in orden() if c.startswith("[0:v]"))
    lenta = next(f for f in filtros.split(";") if "2*(PTS-STARTPTS)" in f)
    assert lenta.index("setpts=2*(PTS-STARTPTS)") < lenta.index("fps=30") < lenta.index("crop=")
    assert "atempo=0.5" in next(f for f in filtros.split(";") if f.startswith("[1:a]") and "atempo" in f)
    assert filtros.count("atempo=0.5") == 1 and filtros.count("2*(PTS-STARTPTS)") == 1


def test_la_pausa_clona_el_ultimo_cuadro_en_silencio_y_el_zoom_es_zoompan_centrado():
    filtros = next(c for c in orden() if c.startswith("[0:v]")).split(";")
    pausa = next(f for f in filtros if "tpad=stop_mode=clone:stop=11" in f and "end_frame=1" in f)      # 12 cuadros = 0.4 s
    assert pausa.startswith("[1:v]trim=start=")
    assert any(f.startswith("anullsrc=r=48000:cl=stereo,atrim=duration=0.4") for f in filtros)
    zoom = next(f for f in filtros if "zoompan" in f)
    assert "x='iw/2-(iw/zoom/2)'" in zoom and "y='ih/2-(ih/zoom/2)'" in zoom and "s=1080x1920" in zoom and "fps=30" in zoom
    assert "z='1+" in zoom and zoom.count("zoompan") == 1


def test_cada_tramo_tiene_un_numero_exacto_de_cuadros_y_de_muestras():
    filtros = next(c for c in orden() if c.startswith("[0:v]")).split(";")
    tramos = h.mapa(plan_base(), 0.0, 18.0)
    for i, tr in enumerate(tramos):
        n = round(tr.s1 * 30) - round(tr.s0 * 30)
        v = next(f for f in filtros if f.endswith(f"[v{i}]"))
        a = next(f for f in filtros if f.endswith(f"[a{i}]"))
        assert f"trim=end_frame={n}," in v and f"apad=whole_len={n * 1600},atrim=end_sample={n * 1600}" in a


def test_los_efectos_entran_con_adelay_y_amix_sin_normalizar():
    p = plan_base(momentos=[momento(2, 4, "sfx", sfx="golpe-a"), momento(8, 9.2, "camara_lenta")])
    cmd = orden(p, sfx={"golpe-a": "e.wav"})
    txt = " ".join(cmd)
    assert "-i e.wav" in txt and "adelay=4000|4000" in txt and "volume=-6dB" in txt
    assert "amix=inputs=2:normalize=0:duration=first[aud]" in txt and "-map [out] -map [aud]" in txt
    assert "atrim=duration=2" in txt                                     # el efecto no pasa del tramo
    with pytest.raises(ValueError, match="no tiene archivo"):
        orden(p, sfx={})


def test_una_fuente_mono_se_duplica_a_estereo_sin_bajarle_volumen():
    tramos = h.mapa(plan_base(), 0.0, 18.0)
    video = {"ruta": "v.mp4", "inicio": 0.0, "recorte": {"x": 0, "y": 0, "ancho": 202, "alto": 360}, "canales": 1}
    mono = " ".join(h.orden_ffmpeg(salida="s.mp4", ancho=1080, alto=1920, video=video, tramos=tramos, placas=[]))
    est = " ".join(h.orden_ffmpeg(salida="s.mp4", ancho=1080, alto=1920, video={**video, "canales": 2}, tramos=tramos,
                                  placas=[]))
    assert "pan=stereo|c0=c0|c1=c0,aformat" in mono and "pan=stereo" not in est


def test_sin_historia_la_orden_vieja_queda_igual():
    video = {"ruta": "v.mp4", "inicio": 3.0, "audio": True, "recorte": {"x": 218, "y": 0, "ancho": 202, "alto": 360}}
    cmd = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=8.0, ancho=1080, alto=1920, video=video,
                                                  placas=[("a.png", 60, 150, 0.0, 8.0)])
    txt = " ".join(cmd)
    assert "zoompan" not in txt and "concat" not in txt and "-map [out] -map 0:a" in txt


# ---------------------------------------------------------------- C/D. render real corto con la puerta

@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


@pytest.fixture(scope="module")
def corta(tmp_path_factory):
    """Fuente de 12 s con testsrc y un seno (el cuadro cambia con el tiempo, el audio es el mismo tono)."""
    ruta = tmp_path_factory.mktemp("corta") / "f.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=30:duration=12", "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(ruta)], check=True)
    return ruta


TOMA = dict(numero=7, gancho="Ella busca a un dragón", palabra_lapiz="DRAGÓN", lapiz="círculo",
            escena="INT. CABAÑA — NOCHE", credito="Con: Blender Foundation (CC BY 3.0)")
LINEAS = [Linea(0.5, 1.5, "CHAMÁN", "¿Qué te trae por aquí?"), Linea(3.0, 4.0, None, None, "(silencio)")]
PLAN_CORTO = {"apertura": {"t0": 7.0, "t1": 9.0},
              "momentos": [momento(2.0, 4.0, "zoom_suave"), momento(4.5, 5.5, "pausa"),
                           momento(6.0, 7.2, "camara_lenta")]}                 # recorte 0-10: 10 + 2 + 1.2 + 0.4 = 13.6 s
REC_CORTO = (0.0, 10.0)


def planificacion(plan, proveedor="gemini"):
    return h.Planificacion(plan, proveedor, ["prueba"])


@pytest.fixture(scope="module")
def render_con_historia(corta, marca, tmp_path_factory):
    base = tmp_path_factory.mktemp("con")
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, base / "t.mp4", duracion=10.0, trabajo=base / "w",
                             fuente_video=corta, inicio=0.0, historia=planificacion(PLAN_CORTO), sfx={},
                             config_historia=CFG_ACTIVA)
    return {"mp4": base / "t.mp4", "plan": plan, "base": base}


@pytest.fixture(scope="module")
def render_sin_historia(corta, marca, tmp_path_factory):
    base = tmp_path_factory.mktemp("sin")
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, base / "t.mp4", duracion=10.0, trabajo=base / "w",
                             fuente_video=corta, inicio=0.0)
    return {"mp4": base / "t.mp4", "plan": plan, "base": base}


def rojos(puntos):
    return [(n, d) for n, ok, d in puntos if not ok]


@pytest.mark.slow
def test_render_corto_con_historia_pasa_la_puerta_de_estilo(render_con_historia, marca):
    p = render_con_historia["plan"]
    assert p["duracion"] == pytest.approx(13.6) and p["origen"]["duracion"] == 10.0 and p["origen"]["inicio"] == 0.0
    assert p["historia"]["proveedor"] == "gemini" and p["historia"]["plan"] == PLAN_CORTO
    assert [t["s1"] for t in p["historia"]["mapa"]][-1] == pytest.approx(13.6)
    puntos = estilo.revisar(render_con_historia["mp4"], p, marca)
    assert rojos(puntos) == []
    nombres = [n for n, _, _ in puntos]
    for esperado in ("[plan] Historia válida", "[plan] Historia sin texto propio",
                     "[fotograma] Apertura en frío = desenlace", "[fotograma] Duración igual al mapa"):
        assert esperado in nombres
    dur = estilo.sondear(render_con_historia["mp4"])["duracion"]
    assert dur == pytest.approx(13.6, abs=0.05)


@pytest.mark.slow
def test_las_placas_y_los_textos_son_los_mismos_con_y_sin_historia(render_con_historia, render_sin_historia):
    a, b = render_con_historia["plan"], render_sin_historia["plan"]
    assert a["toma"] == b["toma"]
    texto = lambda p: [(ln.get("nombre"), ln.get("frase"), ln.get("acotacion")) for ln in p["lineas"]]
    assert texto(a) == texto(b)
    assert [c["tipo"] for c in a["geometria"]["capas"]] == [c["tipo"] for c in b["geometria"]["capas"]]
    assert {c["tipo"]: (c["x"], c["y"], c["ancho"], c["alto"]) for c in a["geometria"]["capas"]} \
        == {c["tipo"]: (c["x"], c["y"], c["ancho"], c["alto"]) for c in b["geometria"]["capas"]}
    assert "historia" not in b                       # sin historia, el plan no cambia
    assert a["historia"]["plan"] == PLAN_CORTO and set(a["historia"]["plan"]) == {"apertura", "momentos"}


@pytest.mark.slow
def test_las_lineas_pasan_al_tiempo_de_salida(render_con_historia):
    lineas = render_con_historia["plan"]["lineas"]
    assert lineas[0]["t0"] == pytest.approx(2.5) and lineas[0]["t1"] == pytest.approx(3.5)        # + los 2 s de la apertura
    assert lineas[1]["t0"] > 3.5 + 1.0 and lineas[1]["acotacion"] == "(silencio)"


@pytest.mark.slow
def test_la_puerta_ve_si_la_apertura_no_es_el_desenlace(render_con_historia, marca):
    p = copy.deepcopy(render_con_historia["plan"])
    p["historia"]["plan"]["apertura"] = {"t0": 0.0, "t1": 2.0}          # el plan guardado dice otra cosa que el MP4
    puntos = {n: ok for n, ok, _ in estilo.revisar(render_con_historia["mp4"], p, marca)}
    assert puntos["[plan] Historia válida"] is False                      # la apertura no cae en el último 40 %
    p2 = copy.deepcopy(render_con_historia["plan"])
    p2["historia"]["plan"]["momentos"][0]["titulo"] = "algo mío"
    puntos2 = {n: ok for n, ok, _ in estilo.revisar(render_con_historia["mp4"], p2, marca)}
    assert puntos2["[plan] Historia sin texto propio"] is False and puntos2["[plan] Historia válida"] is False


@pytest.mark.slow
def test_la_puerta_rechaza_un_mp4_de_otra_duracion_y_un_mapa_distinto(render_con_historia, marca):
    p = copy.deepcopy(render_con_historia["plan"])
    p["duracion"] = 13.0
    puntos = {n: ok for n, ok, _ in estilo.revisar(render_con_historia["mp4"], p, marca)}
    assert puntos["[fotograma] Duración igual al mapa"] is False
    p2 = copy.deepcopy(render_con_historia["plan"])
    p2["historia"]["mapa"][2]["f0"] = 3.0
    puntos2 = {n: ok for n, ok, _ in estilo.revisar(render_con_historia["mp4"], p2, marca)}
    assert puntos2["[plan] Historia válida"] is False


@pytest.mark.slow
def test_sin_historia_la_puerta_no_trae_los_puntos_nuevos(render_sin_historia, marca):
    puntos = estilo.revisar(render_sin_historia["mp4"], render_sin_historia["plan"], marca)
    assert rojos(puntos) == [] and not any("Historia" in n or "Apertura en frío" in n or "al mapa" in n for n, _, _ in puntos)


@pytest.mark.slow
def test_historia_rota_deja_la_toma_como_hoy_y_el_plan_dice_por_que(corta, marca, render_sin_historia, tmp_path):
    for hist, texto in ((h.Planificacion(None, None, ["gemini: x"], "ningún eslabón dio un plan válido"), "ningún eslabón"),
                        (planificacion(plan_base(apertura={"t0": 0.0, "t1": 2.0})), "el plan no valida")):
        plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, tmp_path / "r.mp4", duracion=10.0,
                                 trabajo=tmp_path / "w", fuente_video=corta, inicio=0.0, historia=hist, sfx={},
                                 config_historia=CFG_ACTIVA)
        h_plan = plan.pop("historia")
        assert h_plan["proveedor"] is None and texto in h_plan["motivo"]
        assert plan == render_sin_historia["plan"]                  # el mismo plan que hoy, sin la clave historia
        assert estilo.sondear(tmp_path / "r.mp4")["duracion"] == pytest.approx(10.0, abs=0.05)
        assert rojos(estilo.revisar(tmp_path / "r.mp4", plan, marca)) == []


@pytest.mark.slow
def test_fuente_sin_audio_apaga_la_historia_con_motivo(tmp_path, marca):
    muda = tmp_path / "muda.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=30:duration=12", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-an", str(muda)], check=True)
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, tmp_path / "m.mp4", duracion=10.0,
                             trabajo=tmp_path / "w", fuente_video=muda, inicio=0.0, historia=planificacion(PLAN_CORTO),
                             sfx={}, config_historia=CFG_ACTIVA, musica=None)
    assert plan["historia"]["proveedor"] is None and "no trae audio" in plan["historia"]["motivo"]
    assert plan["duracion"] == 10.0


def test_historia_en_una_toma_sin_video_no_cambia_nada(marca, tmp_path):
    plan = componer.componer(marca, lienzo.Toma(**TOMA), [Linea(1.0, 3.0, "A", "hola")], tmp_path / "s.mp4",
                             duracion=6.0, trabajo=tmp_path / "w", historia=planificacion(plan_base()))
    assert plan["historia"]["proveedor"] is None and "sin video" in plan["historia"]["motivo"] and plan["duracion"] == 6.0


# ---------------------------------------------------------------- E (interfaz). efectos de sonido en el render

@pytest.fixture
def repo_con_efecto(repo, tmp_path, monkeypatch):
    """Una copia del repo con un efecto sintético verificado y GRANJA_SFX_DIR apuntando a su archivo."""
    carpeta = tmp_path / "sfx"
    carpeta.mkdir()
    wav = carpeta / "golpe.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=180:duration=0.8", str(wav)], check=True)
    sha = componer.sha256_archivo(wav)
    (repo / "marca" / "sfx" / "golpe-prueba").mkdir(parents=True, exist_ok=True)
    (repo / "marca" / "sfx" / "golpe-prueba" / "LICENCIA.md").write_text(LICENCIA_FALSA, encoding="utf-8")
    (repo / "marca" / "sfx" / "efectos.yml").write_text(yaml.safe_dump({"efectos": [
        {"id": "golpe-prueba", "tipo": "golpe", "estado": "verificada", "asset": "golpe.wav", "sha256": sha}]}),
        encoding="utf-8")
    monkeypatch.setenv("GRANJA_SFX_DIR", str(carpeta))
    return repo


def test_sfx_disponibles_solo_verificados_con_sha_bueno(repo_con_efecto, tmp_path, monkeypatch):
    d = mod_sfx.disponibles(repo_con_efecto)
    assert list(d) == ["golpe-prueba"] and d["golpe-prueba"]["tipo"] == "golpe" and Path(d["golpe-prueba"]["ruta"]).is_file()
    assert mod_sfx.de_tipo(repo_con_efecto, "golpe")["id"] == "golpe-prueba" and mod_sfx.de_tipo(repo_con_efecto, "boom") is None
    Path(d["golpe-prueba"]["ruta"]).write_bytes(b"cambiado")                         # sha distinto: ya no está
    assert mod_sfx.disponibles(repo_con_efecto) == {}
    monkeypatch.delenv("GRANJA_SFX_DIR")
    assert mod_sfx.disponibles(repo_con_efecto) == {} and mod_sfx.carpeta() is None


@pytest.mark.slow
def test_render_con_efecto_pasa_la_puerta_y_declara_el_efecto_en_el_plan(repo_con_efecto, corta, tmp_path):
    marca = Marca.cargar(repo_con_efecto)
    sfx_d = mod_sfx.disponibles(repo_con_efecto)
    plan_h = {"apertura": {"t0": 7.0, "t1": 9.0},
              "momentos": [momento(2.0, 4.0, "sfx", sentimiento="sorpresa", sfx="golpe-prueba"),
                           momento(5.0, 6.2, "camara_lenta")]}
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, tmp_path / "e.mp4", duracion=10.0,
                             trabajo=tmp_path / "w", fuente_video=corta, inicio=0.0, historia=planificacion(plan_h),
                             sfx=sfx_d, config_historia=CFG_ACTIVA)
    assert plan["audio"]["tipo"] == "original" and plan["audio"]["efectos"][0]["id"] == "golpe-prueba"
    assert plan["audio"]["efectos"][0]["t"] == pytest.approx(4.0)
    puntos = estilo.revisar(tmp_path / "e.mp4", plan, marca)
    assert rojos(puntos) == []
    # el efecto se oye: el tramo con efecto tiene más energía que el mismo tramo de la fuente (un seno de 440 Hz)
    def energia(ruta, ss, t):
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(ss), "-t", str(t), "-i", str(ruta), "-vn", "-ac", "1",
                              "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
        return float(np.sqrt(np.mean(np.frombuffer(out, dtype=np.int16).astype(np.float64) ** 2)))
    base = energia(corta, 2.0, 1.5)
    assert energia(tmp_path / "e.mp4", 0.2, 1.5) == pytest.approx(base, rel=0.05)      # fuera del efecto: el audio tal cual
    assert energia(tmp_path / "e.mp4", 4.0, 1.5) > base * 1.02
    # un efecto declarado que el catálogo no tiene en verificada: la puerta lo ve
    p2 = copy.deepcopy(plan)
    p2["audio"]["efectos"][0]["sha256"] = "0" * 64
    assert not dict((n, ok) for n, ok, _ in estilo.revisar(tmp_path / "e.mp4", p2, marca))["[plan] Historia válida"]


# ---------------------------------------------------------------- toma de punta a punta (hacer_toma)

def respuesta_con_historia(plan_h):
    datos = json.loads(respuesta_grabada("valida"))
    datos["historia"] = plan_h
    return Transporte((200, sobre(json.dumps(datos))))


PLAN_TOMA = {"apertura": {"t0": 11.0, "t1": 13.0},              # recorte de la respuesta grabada: 2.0-14.0
             "momentos": [momento(4.0, 6.5, "zoom_suave"), momento(8.0, 9.2, "camara_lenta")]}


@pytest.fixture
def repo_historia(repo, editar_yaml):
    editar_yaml(repo, "config/historia.yml", lambda d: d.update(activo=True))
    return repo


def hacer(repo, tmp_path, video, transporte, nombre="s"):
    return toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, salida=tmp_path / nombre, abrir=abrir_con(video),
                           transporte=transporte, entorno={"GEMINI_API_KEY": "clave-de-prueba"},
                           espera=lambda s: None)


@pytest.mark.slow
def test_hacer_toma_con_el_plan_de_gemini_renderiza_la_historia(repo_historia, tmp_path, video20):
    r = hacer(repo_historia, tmp_path, video20, respuesta_con_historia(PLAN_TOMA))
    assert r.ok, r.motivos
    assert r.plan["historia"]["proveedor"] == "gemini" and r.fila["historia"] == "gemini"
    assert r.plan["origen"]["inicio"] == 2.0 and r.plan["origen"]["duracion"] == 12.0
    assert r.plan["duracion"] == pytest.approx(12.0 + 2.0 + 1.2) and r.fila["duracion_s"] == pytest.approx(15.2)
    assert all(ok for _, ok, _ in r.puntos) and any("Historia válida" in n for n, _, _ in r.puntos)
    assert r.gastar is False


@pytest.mark.slow
def test_hacer_toma_sin_plan_de_gemini_usa_el_eslabon_sin_ia(repo_historia, tmp_path, video20):
    t = Transporte((200, sobre(respuesta_grabada("valida"))))
    r = hacer(repo_historia, tmp_path, video20, t)
    assert r.ok, r.motivos
    assert r.plan["historia"]["proveedor"] == "sin-ia" and r.fila["historia"] == "sin-ia"
    assert "segundo: apagado (GitHub Models retirado el 30 jul 2026)" in r.plan["historia"]["intentos"]
    assert "9. `historia`" in json.loads(t.llamadas[0][2])["contents"][0]["parts"][1]["text"]


@pytest.mark.slow
def test_hacer_toma_con_historia_apagada_sale_como_hoy(repo, tmp_path, video20):
    r = hacer(repo, tmp_path, video20, respuesta_con_historia(PLAN_TOMA))
    assert r.ok and "historia" not in r.plan and r.fila.get("historia") is None
    assert r.plan["duracion"] == 12.0 and r.fila["duracion_s"] == 12.0


@pytest.mark.slow
def test_si_el_render_con_historia_falla_la_toma_sale_como_hoy_sin_gastar_la_fuente(repo_historia, tmp_path, video20,
                                                                                  monkeypatch):
    monkeypatch.setattr(h, "orden_ffmpeg", lambda **k: ["ffmpeg", "-y", "-i", "no-existe.mp4", "x.mp4"])
    r = hacer(repo_historia, tmp_path, video20, respuesta_con_historia(PLAN_TOMA))
    assert r.ok, r.motivos
    assert r.plan["historia"]["proveedor"] is None and "el render con historia falló" in r.plan["historia"]["motivo"]
    assert r.plan["duracion"] == 12.0 and r.fila["historia"] == "" and r.gastar is False
    assert all(ok for _, ok, _ in r.puntos)


@pytest.mark.slow
def test_si_la_puerta_sale_en_rojo_con_historia_se_renderiza_sin_ella(repo_historia, tmp_path, video20, monkeypatch):
    reales = estilo.revisar
    llamadas = []

    def revisar(video, plan, marca, ficha=None):
        llamadas.append(plan.get("historia", {}).get("proveedor"))
        if plan.get("historia", {}).get("proveedor"):          # la corrida con historia se descarta: no hace falta medirla
            return [("[fotograma] prueba roja", False, "forzado")]
        return reales(video, plan, marca, ficha=ficha)
    monkeypatch.setattr(toma.estilo, "revisar", revisar)
    r = hacer(repo_historia, tmp_path, video20, respuesta_con_historia(PLAN_TOMA))
    assert r.ok and llamadas == ["gemini", None]
    assert "puerta de estilo salió en rojo con historia" in r.plan["historia"]["motivo"] and r.plan["duracion"] == 12.0

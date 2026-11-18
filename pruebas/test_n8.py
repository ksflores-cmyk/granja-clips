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
        assert u.startswith("https://drive.usercontent.google.com/download?id=") \
            and u.endswith("&export=download&confirm=t")
    for creador, tags in CREADORES.items():
        m = fuentes.entrada_material(f, creador=creador)
        assert m is not None and m["url"].split("id=")[1].split("&")[0] == IDS[creador]
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

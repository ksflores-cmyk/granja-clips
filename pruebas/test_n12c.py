"""Ronda de corrección N1N2-c: lista blanca antes de descargar, puerta que mira el video, etc."""
from datetime import date

import pytest

from conftest import RAIZ_REAL
from fabrica import fuentes
from fabrica.editar import muestras
from fabrica.editar.marca import Marca

HOY = date(2026, 11, 17)
U_VIDEO = "https://raw.githubusercontent.com/x/y/video.mp4"
U_SUBS = "https://raw.githubusercontent.com/x/y/subs.vtt"


def ficha(**cambios):
    f = {"id": "fx", "tipo": "B", "estado": "vigente", "uso": "muestras", "publicable": False,
         "vence": None, "material": [U_VIDEO, U_SUBS], "reglas": {}}
    f.update(cambios)
    return f


# ---------------------------------------------------------------- A3

@pytest.mark.parametrize("f,motivo", [
    (None, "ficha ausente"),
    (ficha(estado="vetada"), "estado"),
    (ficha(vence="2026-01-01"), "vencida"),
    (ficha(tipo="D"), "tipo D"),
    (ficha(material=["https://otro.com/a.mp4"]), "fuera de material"),
])
def test_autoriza_descarga_rechaza_y_nombra_la_condicion(f, motivo):
    ok, m = fuentes.autoriza_descarga(f, HOY, "muestras", U_VIDEO)
    assert not ok and motivo in m


def test_autoriza_descarga_acepta_la_ficha_correcta_y_exige_uso():
    assert fuentes.autoriza_descarga(ficha(), HOY, "muestras", U_VIDEO)[0]
    ok, m = fuentes.autoriza_descarga(ficha(), HOY, "produccion", U_VIDEO)
    assert not ok and "produccion" in m
    assert not fuentes.autoriza_descarga(ficha(), HOY, "muestras", None)[0]
    assert not fuentes.autoriza_descarga(ficha(), HOY, "otro", U_VIDEO)[0]


def muestra(fuente_id="fx", con_subs=True):
    m = {"id": "m1", "fuente_id": fuente_id, "toma": {"numero": 1, "gancho": "Hola mundo", "palabra_lapiz": "MUNDO"},
         "lineas": [], "recorte": {"inicio": 0, "duracion": 8}, "video": {"url": U_VIDEO, "sha256": "0" * 64}}
    if con_subs:
        m["subtitulos"] = {"url": U_SUBS, "sha256": "0" * 64}
    return m


@pytest.mark.parametrize("fichas", [
    [],
    [ficha(estado="vetada")],
    [ficha(vence="2026-01-01")],
    [ficha(material=[U_VIDEO])],            # los subtítulos no están en material
])
def test_generar_no_llama_al_descargador_si_la_lista_blanca_dice_no(tmp_path, fichas):
    llamadas = []

    def descargador(*a, **k):
        llamadas.append(a)
        raise AssertionError("no debe descargar")
    with pytest.raises(RuntimeError, match="no autorizada"):
        muestras.generar(tmp_path, Marca.cargar(RAIZ_REAL), [muestra()], tmp_path,
                         fichas=fichas, hoy=HOY, descargador=descargador)
    assert llamadas == []


def test_generar_con_ficha_correcta_si_llama_al_descargador(tmp_path):
    llamadas = []

    def descargador(url, esperado, destino):
        llamadas.append(url)
        raise KeyError("parada controlada")      # basta con ver que se llamó
    with pytest.raises(KeyError):
        muestras.generar(tmp_path, Marca.cargar(RAIZ_REAL), [muestra()], tmp_path,
                         fichas=[ficha()], hoy=HOY, descargador=descargador)
    assert llamadas == [U_VIDEO]


def test_fichas_reales_de_muestras_autorizan_exactamente_sus_urls():
    fichas = fuentes.cargar_fuentes(RAIZ_REAL)
    for m in muestras.cargar_muestras():
        if m.get("video"):
            muestras.autorizar(m, fichas, HOY)

"""El nombre de quien habla y el del stream salen de la ficha, no de Gemini (casos reales: tomas 002 y 003)."""
from pathlib import Path

from fabrica.editar import nombres, toma as mod_toma
from fabrica.editar.componer import Linea
from fabrica.editar.marca import Marca

RAIZ = Path(__file__).resolve().parent.parent


def _datos(hablante="X", escena="EXT. CALLE — NOCHE", frase="Vamos con todo.", hablante2=None):
    lineas = [{"t0": 1.0, "t1": 4.0, "hablante": hablante, "original": "go", "es": frase}]
    if hablante2:
        lineas.append({"t0": 5.0, "t1": 8.0, "hablante": hablante2, "original": "ok", "es": "Está bien."})
    return {"recorte": {"inicio": 0, "fin": 12}, "gancho": "Gol de último minuto", "palabra_lapiz": "Gol",
            "escena": escena, "acotacion": "", "lineas": lineas}


def _armar(datos, ficha, creador=None):
    marca = Marca.cargar(RAIZ)
    toma, lineas, _, _ = mod_toma.armar(datos, marca, ficha, 2, creador)
    return toma, lineas


def test_toma_002_westcol_nunca_llega_al_video():
    ficha = {"creador": "campaña FM27"}
    # El modelo ya sólo debe decir STREAMER; si aun así inventa WESTCOL, es OTRA VOZ (jamás su nombre).
    toma, lineas = _armar(_datos("STREAMER", "INT. STREAM DE WESTCOL — MINUTO 42"), ficha, "sebasberon")
    assert [ln.nombre for ln in lineas] == ["SEBASBERON"]
    assert toma.escena == "INT. STREAM DE SEBASBERON"
    assert nombres.revisar_nombres(toma, lineas, "sebasberon", RAIZ) == []
    toma, lineas = _armar(_datos("WESTCOL", "INT. STREAM DE WESTCOL — MINUTO 42"), ficha, "sebasberon")
    assert [ln.nombre for ln in lineas] == ["OTRA VOZ"]
    assert toma.escena == "INT. STREAM DE SEBASBERON"
    assert nombres.revisar_nombres(toma, lineas, "sebasberon", RAIZ) == []


def test_toma_003_baity_se_vuelve_taisontv():
    ficha = {"creador": "taisontv", "twitch_login": "taisontv"}
    toma, lineas = _armar(_datos("STREAMER", "INT. STREAM DE BAITY — MINUTO 3"), ficha)
    assert [ln.nombre for ln in lineas] == ["TAISONTV"]
    assert [ln.nombre for ln in _armar(_datos("BAITY"), ficha)[1]] == ["OTRA VOZ"]
    assert toma.escena == "INT. STREAM DE TAISONTV"
    assert nombres.revisar_nombres(toma, lineas, "taisontv", RAIZ) == []


def test_streamer_y_el_propio_creador_con_formato_distinto_llevan_el_nombre():
    ficha = {"creador": "sebas_beron"}
    _, lineas = _armar(_datos("Streamer", hablante2="@Sebas Beron"), ficha)
    assert [ln.nombre for ln in lineas] == ["SEBAS_BERON", "SEBAS_BERON"]


def test_otro_hablante_es_otra_voz():
    _, lineas = _armar(_datos("JUGADOR"), {"creador": "taisontv"})
    assert [ln.nombre for ln in lineas] == ["OTRA VOZ"]


def test_escena_ext_con_vetado_se_reemplaza_y_sin_vetado_se_conserva():
    ficha = {"creador": "taisontv"}
    assert _armar(_datos(escena="EXT. CASA DE WESTCOL — NOCHE"), ficha)[0].escena == "INT. STREAM DE TAISONTV"
    assert _armar(_datos(escena="EXT. CALLE — NOCHE"), ficha)[0].escena == "EXT. CALLE — NOCHE"


def test_la_puerta_rechaza_una_frase_con_westcol():
    toma, lineas = _armar(_datos("STREAMER", frase="Dice Westcol que es fácil."), {"creador": "sebasberon"})
    motivos = nombres.revisar_nombres(toma, lineas, "sebasberon", RAIZ)
    assert any("westcol" in m for m in motivos)


def test_la_puerta_rechaza_un_hablante_ajeno_y_acepta_acotacion_sin_nombre():
    marca = Marca.cargar(RAIZ)
    toma, _ = _armar(_datos(), {"creador": "taisontv"})
    malas = [Linea(t0=1, t1=2, nombre="BAITY", frase="Hola.")]
    assert nombres.revisar_nombres(toma, malas, "taisontv", RAIZ)
    solo_acotacion = [Linea(t0=1, t1=2, acotacion="(silencio)")]
    assert nombres.revisar_nombres(toma, solo_acotacion, "taisontv", RAIZ) == []
    assert marca is not None

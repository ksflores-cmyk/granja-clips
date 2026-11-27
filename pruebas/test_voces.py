"""Tarea #19 `voces`: transcripción propia, voces por color, silencios cortados y golpes. Sin red ni modelos:
el transcriptor es falso y el audio, sintético (dos "voces" de tono y timbre distintos, silencios y un golpe)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import wave
from dataclasses import replace

import numpy as np
import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import validar, voces
from fabrica.editar import componer, estilo, lienzo, placas, toma
from fabrica.editar import dialogo as mod_dialogo
from fabrica.editar.componer import Linea
from fabrica.editar.marca import Marca
from fabrica.voces import Cfg, Transcripcion, Voces

SR = voces.SR
hay_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
necesita_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")


@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


# ---------------------------------------------------------------- audio sintético

def voz(f0, formantes, dur, rng, amp=0.2):
    """Una vocal sintética: armónicos de `f0` con una envolvente espectral de dos formantes y algo de vibrato."""
    t = np.arange(int(dur * SR)) / SR
    vib = 1 + 0.01 * np.sin(2 * np.pi * 5 * t + rng.uniform(0, 6))
    fase = 2 * np.pi * np.cumsum(f0 * vib) / SR
    x = np.zeros_like(t)
    for k in range(1, int(4000 / f0)):
        g = sum(np.exp(-0.5 * ((k * f0 - c) / bw) ** 2) for c, bw in formantes) + 0.02
        x += g * np.sin(k * fase)
    x /= np.abs(x).max()
    env = np.minimum(1, np.minimum(t, dur - t) / 0.03)
    return (amp * x * env + 0.002 * rng.standard_normal(len(t))).astype(np.float32)


def voz_a(dur, rng):
    return voz(110, [(500, 150), (1500, 250)], dur, rng)


def voz_b(dur, rng):
    return voz(230, [(800, 200), (2500, 300)], dur, rng)


def silencio(dur, rng):
    return (0.0005 * rng.standard_normal(int(dur * SR))).astype(np.float32)


def golpe(dur, rng):
    return (0.9 * rng.standard_normal(int(dur * SR))).astype(np.float32).clip(-1, 1)


def escena(solo_a=False):
    """(muestras, turnos [(t0, t1, 'A'|'B')], tiempo del golpe). Silencios de 0,3 s y uno de 1,2 s (de 3,4 a 4,6)."""
    rng = np.random.default_rng(1)
    guion = [("A", 1.6, 0.3), ("B", 1.5, 1.2), ("A", 1.8, 0.3), ("A", 1.4, 0.3), ("B", 1.5, 0.3), ("A", 1.7, 0.3)]
    partes, turnos, t = [], [], 0.0
    for quien, dur, pausa in guion:
        quien = "A" if solo_a else quien
        partes.append((voz_a if quien == "A" else voz_b)(dur, rng))
        turnos.append((t, t + dur, quien))
        t += dur
        partes.append(silencio(pausa, rng))
        t += pausa
    partes.append(golpe(0.06, rng))
    t_golpe = t + 0.03
    partes.append(silencio(0.3, rng))
    return np.concatenate(partes), turnos, t_golpe


def guardar_wav(ruta, x, sr=SR):
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return ruta


def palabras_de(turnos, n=4):
    """Palabras falsas repartidas dentro de cada turno."""
    texto = ["hola", "amigos", "cómo", "estáis", "hoy", "vamos", "a", "jugar"]
    segs, k = [], 0
    for t0, t1, _ in turnos:
        w = (t1 - t0) / n
        pal = [{"texto": texto[(k + i) % len(texto)], "t0": t0 + i * w + 0.02, "t1": t0 + (i + 1) * w - 0.02, "p": 0.9}
               for i in range(n)]
        k += n
        segs.append({"t0": t0, "t1": t1, "texto": " ".join(p["texto"] for p in pal), "no_speech_prob": 0.01,
                     "avg_logprob": -0.2, "palabras": pal})
    return segs


def falso(segmentos, idioma="es", modelo="base"):
    def transcriptor(wav, cfg, nombre):
        return Transcripcion(idioma=idioma, prob_idioma=0.99, segmentos=segmentos, modelo=nombre, segundos=0.1)
    return transcriptor


# ---------------------------------------------------------------- silencios y tramos

def test_corta_el_silencio_de_1_2_s_y_no_el_de_0_3_s():
    x, turnos, _ = escena()
    sil = voces.silencios(x, SR, voces._palabras_planas(palabras_de(turnos)), Cfg())
    assert len(sil) == 1
    a, b = sil[0]
    assert abs(a - 3.4) < 0.1 and abs(b - 4.6) < 0.1


def test_nunca_corta_encima_de_una_palabra():
    x, turnos, _ = escena()
    palabra = {"texto": "susurro", "t0": 3.8, "t1": 4.1, "p": 0.5}           # una palabra bajita dentro del silencio
    sil = voces.silencios(x, SR, [palabra], Cfg())
    for a, b in sil:
        assert b <= palabra["t0"] or a >= palabra["t1"]
    tramos = voces.tramos_a_conservar(12.6, sil, 0.12, 7.0)
    for a, b in voces._quitados(12.6, tramos):
        assert b <= palabra["t0"] or a >= palabra["t1"]


def test_los_tramos_dejan_el_margen_y_respetan_el_minimo():
    tramos = voces.tramos_a_conservar(12.0, [(3.4, 4.6)], 0.12, 7.0)
    assert [(a, b) for a, b in tramos] == [(0.0, 3.533333), (4.466667, 12.0)]       # bordes en múltiplos de 1/30
    assert all(abs(t * 30 - round(t * 30)) < 1e-4 for tr in tramos for t in tr)
    assert voces._quitados(12.0, tramos) == [(3.533333, 4.466667)]
    # si lo que queda no llega al mínimo, no se corta nada
    assert voces.tramos_a_conservar(12.0, [(1.0, 11.0)], 0.12, 7.0) == [(0.0, 12.0)]
    assert voces.tramos_a_conservar(12.0, [], 0.12, 7.0) == [(0.0, 12.0)]
    # un silencio que no pasa del doble del margen no se toca
    assert voces.tramos_a_conservar(12.0, [(3.0, 3.2)], 0.12, 7.0) == [(0.0, 12.0)]


def test_un_silencio_que_toca_el_inicio_o_el_final_se_quita_entero():
    tramos = voces.tramos_a_conservar(12.0, [(0.0, 1.0), (11.0, 12.0)], 0.12, 7.0)
    assert tramos == [(0.866667, 11.133333)]       # se quita desde 0 y hasta el final; el margen queda sólo del lado de la voz
    assert voces.tramos_a_conservar(12.0, [(0.0, 0.5)], 0.12, 7.0) == [(0.366667, 12.0)]
    sin_borde = voces.tramos_a_conservar(12.0, [(5.0, 6.0)], 0.12, 7.0)
    assert sin_borde[0][0] == 0.0 and sin_borde[-1][1] == 12.0 and len(sin_borde) == 2


def test_a_toma_y_a_fuente_son_inversos_dentro_de_los_tramos():
    tramos = [(0.0, 3.52), (4.48, 9.0), (10.0, 12.0)]
    for t in (0.0, 1.0, 3.5, 4.5, 6.0, 8.99, 10.0, 11.5):
        assert voces.a_fuente(voces.a_toma(t, tramos), tramos) == pytest.approx(t)
    assert voces.a_toma(4.0, tramos) == pytest.approx(3.52)             # dentro de lo quitado: el borde
    assert voces.a_toma(9.5, tramos) == pytest.approx(3.52 + 4.52)
    assert voces.a_toma(12.0, tramos) == pytest.approx(3.52 + 4.52 + 2.0)
    assert voces.a_fuente(3.52, tramos) == pytest.approx(4.48)
    assert voces.a_fuente(99.0, tramos) == 12.0


# ---------------------------------------------------------------- golpes

def test_encuentra_el_golpe_de_ruido():
    x, _, t_golpe = escena()
    g = voces.golpes(x, SR, Cfg())
    assert len(g) >= 1
    mejor = max(g, key=lambda e: e["db"])
    assert abs(mejor["t"] - t_golpe) < 0.08 and mejor["db"] > -10
    assert [e["t"] for e in g] == sorted(e["t"] for e in g)


def test_los_golpes_no_bajan_del_piso_absoluto():
    rng = np.random.default_rng(5)
    quedito = np.concatenate([silencio(1.0, rng), 0.03 * golpe(0.05, rng), silencio(1.0, rng)])      # ~ -36 dBFS, sobre la mediana
    assert voces.golpes(quedito, SR, replace(Cfg(), golpes={**Cfg().golpes, "piso_db": -100})) != []
    assert voces.golpes(quedito, SR, Cfg()) == []                                                       # piso de -30 dBFS


def test_los_golpes_respetan_separacion_y_maximo():
    rng = np.random.default_rng(3)
    partes = []
    for _ in range(12):
        partes += [silencio(0.4, rng), golpe(0.05, rng)]
    x = np.concatenate(partes)
    g = voces.golpes(x, SR, replace(Cfg(), golpes={"sobre_mediana_db": 12, "separacion_s": 1.0, "max": 3}))
    assert len(g) == 3
    assert all(b["t"] - a["t"] >= 1.0 for a, b in zip(g, g[1:]))
    assert voces.golpes(silencio(2.0, rng), SR, Cfg()) == []


# ---------------------------------------------------------------- hablantes

def test_separa_dos_voces_y_la_que_mas_habla_es_el_streamer():
    x, turnos, _ = escena()
    etiquetas = voces.hablantes(x, SR, [(a, b) for a, b, _ in turnos], Cfg())
    esperado = ["streamer" if q == "A" else "otra" for _, _, q in turnos]
    assert etiquetas == esperado
    assert voces.hablantes(x, SR, [(a, b) for a, b, _ in turnos], Cfg()) == etiquetas        # determinista


def test_una_sola_voz_es_toda_del_streamer():
    x, turnos, _ = escena(solo_a=True)
    assert set(voces.hablantes(x, SR, [(a, b) for a, b, _ in turnos], Cfg())) == {"streamer"}


def test_la_voz_minoritaria_con_poco_tiempo_no_cuenta():
    x, turnos, _ = escena()
    cfg = replace(Cfg(), hablantes={"max": 2, "separacion_min": 0.25, "minimo_s": 10.0})
    assert set(voces.hablantes(x, SR, [(a, b) for a, b, _ in turnos], cfg)) == {"streamer"}
    assert set(voces.hablantes(x, SR, [(a, b) for a, b, _ in turnos], replace(Cfg(), hablantes={
        "max": 1, "separacion_min": 0.25, "minimo_s": 1.5}))) == {"streamer"}


# ---------------------------------------------------------------- transcripción

def test_descarta_alucinaciones_y_cuenta_lo_descartado(tmp_path):
    base = {"t0": 0, "t1": 2, "no_speech_prob": 0.0, "avg_logprob": -0.2, "palabras": []}
    segs = [{**base, "texto": "Hola, ¿qué tal?"},
            {**base, "texto": "Subtítulos realizados por la comunidad de Amara.org"},
            {**base, "texto": "¡Gracias por ver!"},                                       # igual a una frase de la lista
            {**base, "texto": "Gracias por ver el stream de hoy, nos vemos mañana a las ocho"},   # con texto real: se queda
            {**base, "texto": "♪"}, {**base, "texto": "[Música]"}, {**base, "texto": "(música)"},
            {**base, "texto": "mmm", "no_speech_prob": 0.9, "avg_logprob": -1.5},
            {**base, "texto": "dudoso pero sonó", "no_speech_prob": 0.9, "avg_logprob": -0.3},     # sólo una de las dos
            {**base, "texto": "   "}]
    tr = voces.transcribir(tmp_path / "x.wav", Cfg(), transcriptor=falso(segs))
    assert [x["texto"] for x in tr.segmentos] == ["Hola, ¿qué tal?",
                                                  "Gracias por ver el stream de hoy, nos vemos mañana a las ocho",
                                                  "dudoso pero sonó"]
    assert tr.descartados == {"segmentos": 7, "palabras": 0}


def test_descarta_palabras_dudosas_y_simbolos_sueltos(tmp_path):
    def w(t, texto, p=0.9):
        return {"texto": texto, "t0": t, "t1": t + 0.3, "p": p}
    seg = {"t0": 0, "t1": 3, "texto": "x", "no_speech_prob": 0.0, "avg_logprob": -0.2,
           "palabras": [w(0.0, "♪"), w(0.4, "hola"), w(0.8, "[Música]"), w(1.2, "mundo", 0.2), w(1.6, "bien"),
                        w(2.0, "(risas)")]}
    solo_basura = {"t0": 4, "t1": 5, "texto": "♪ ♪", "no_speech_prob": 0.0, "avg_logprob": -0.2,
                   "palabras": [w(4.0, "♪"), w(4.4, "♪♪")]}
    tr = voces.transcribir(tmp_path / "x.wav", Cfg(), transcriptor=falso([seg, solo_basura]))
    assert [p["texto"] for p in tr.segmentos[0]["palabras"]] == ["hola", "bien"] and tr.segmentos[0]["texto"] == "hola bien"
    assert len(tr.segmentos) == 1 and tr.descartados == {"segmentos": 1, "palabras": 6}


def test_nunca_se_le_da_el_idioma_a_whisper_y_hay_que_pasar_la_probabilidad(tmp_path, marca):
    """Un transcriptor que devuelve el idioma que le piden no debe convertir audio "desconocido" en español."""
    pedidos = []

    def eco(wav, cfg, nombre):
        pedidos.append(cfg.idioma)
        return Transcripcion(cfg.idioma or "zz", 1.0 if cfg.idioma else 0.2, [], nombre, 0.1)
    tr = voces.transcribir(tmp_path / "x.wav", replace(Cfg(), idioma="es"), transcriptor=eco)
    assert pedidos == [None] and tr.idioma == "zz"
    mudo = Voces(duracion=5, duracion_toma=5, idioma="es", prob_idioma=0.3)
    assert not mudo.es_espanol                                   # "es" con probabilidad baja no vale
    assert replace(mudo, prob_idioma=0.8).es_espanol and not replace(mudo, idioma="en", prob_idioma=0.99).es_espanol


def test_sin_faster_whisper_devuelve_none_con_motivo(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)             # el import falla como si no estuviera
    monkeypatch.setattr(voces, "_modelos", {})
    monkeypatch.setattr(voces, "leer_wav", lambda ruta: (np.zeros(SR, dtype=np.float32), SR))
    assert voces.transcribir(tmp_path / "x.wav", Cfg()) is None
    assert "faster-whisper" in voces.motivo_de_fallo()


def test_whisper_se_llama_con_los_ajustes_de_la_tarea_y_el_modelo_queda_en_cache(monkeypatch, tmp_path):
    from types import ModuleType, SimpleNamespace as NS
    cargas, llamadas = [], []

    class ModeloFalso:
        def __init__(self, nombre, **kw):
            cargas.append((nombre, kw))

        def transcribe(self, audio, **kw):
            llamadas.append((audio, kw))
            pal = [NS(word=" Hola", start=0.5, end=0.9, probability=0.9), NS(word=" mundo", start=1.0, end=1.4, probability=0.8)]
            seg = NS(start=0.5, end=1.4, text=" Hola mundo", no_speech_prob=0.02, avg_logprob=-0.3, words=pal)
            return iter([seg]), NS(language="es", language_probability=0.97)

    falso_mod = ModuleType("faster_whisper")
    falso_mod.WhisperModel = ModeloFalso
    monkeypatch.setitem(sys.modules, "faster_whisper", falso_mod)
    monkeypatch.setattr(voces, "_modelos", {})
    wav = guardar_wav(tmp_path / "a.wav", np.zeros(SR, dtype=np.float32))
    tr = voces.transcribir(wav, replace(Cfg(), idioma="es"))            # aunque la config diga "es": se autodetecta
    assert cargas == [("base", {"device": "cpu", "compute_type": "int8"})]
    audio, kw = llamadas[0]
    assert audio.dtype == np.float32 and len(audio) == SR
    assert kw == {"language": None, "word_timestamps": True, "vad_filter": True, "beam_size": 1,
                  "condition_on_previous_text": False}
    assert tr.idioma == "es" and tr.modelo == "base" and tr.prob_idioma == pytest.approx(0.97)
    assert tr.segmentos[0]["palabras"][0] == {"texto": "Hola", "t0": 0.5, "t1": 0.9, "p": 0.9}
    tr2 = voces.transcribir(wav, replace(Cfg(), idioma=None), modelo="small")
    assert [c[0] for c in cargas] == ["base", "small"] and llamadas[1][1]["language"] is None and llamadas[0][1]["language"] is None
    voces.transcribir(wav, Cfg())                                          # el modelo ya cargado no se vuelve a cargar
    assert len(cargas) == 2 and tr2.segundos_carga >= 0


def test_un_error_del_modelo_no_lanza(tmp_path):
    def roto(wav, cfg, nombre):
        raise OSError("sin red: no se pudo bajar el modelo")
    assert voces.transcribir(tmp_path / "x.wav", Cfg(), transcriptor=roto) is None
    assert "OSError" in voces.motivo_de_fallo()


def test_el_sin_tiempo_del_ciclo_si_sube(tmp_path):
    from fabrica import plazo

    def sin_tiempo(wav, cfg, nombre):
        raise plazo.SinTiempo("se acabó")
    with pytest.raises(plazo.SinTiempo):
        voces.transcribir(tmp_path / "x.wav", Cfg(), transcriptor=sin_tiempo)


# ---------------------------------------------------------------- líneas

def test_lineas_caben_y_cortan_en_cambio_de_voz_pausa_y_duracion(marca):
    def p(t, texto, dur=0.3):
        return {"texto": texto, "t0": t, "t1": t + dur, "p": 0.9}
    palabras = [p(0.0, "uno"), p(0.4, "dos"), p(0.8, "tres"),              # misma voz, sin pausas: una línea
                p(1.2, "cuatro"), p(1.6, "cinco"),                          # cambio de voz: otra línea
                p(3.0, "seis"),                                             # pausa de más de 0,5 s: otra línea
                p(3.4, "siete"), p(3.8, "ocho"), p(4.2, "nueve"), p(4.6, "diez"), p(5.0, "once"), p(5.4, "doce"),
                p(5.8, "trece"), p(6.2, "catorce"), p(6.6, "quince"), p(7.0, "dieciséis"), p(7.4, "diecisiete")]
    tr = Transcripcion("es", 1.0, [{"t0": 0, "t1": 8, "texto": "", "no_speech_prob": 0, "avg_logprob": 0,
                                    "palabras": palabras}], "base", 0.1)
    turnos = [{"t0": 0.0, "t1": 1.2, "voz": "streamer"}, {"t0": 1.2, "t1": 2.0, "voz": "otra"},
              {"t0": 2.9, "t1": 8.0, "voz": "otra"}]
    lineas = voces.lineas_desde_transcripcion(tr, turnos, marca, "twitch.tv/ibai", Cfg())
    assert [ln["es"] for ln in lineas][:2] == ["uno dos tres", "cuatro cinco"] and lineas[2]["es"].startswith("seis")
    assert lineas[0]["voz"] == "streamer" and lineas[0]["hablante"] == "IBAI"
    assert lineas[1]["voz"] == "otra" and lineas[1]["hablante"] == "OTRA VOZ"
    assert lineas[2]["t0"] - lineas[1]["t1"] >= 0.0
    for ln in lineas:                                       # ninguna pasa de 4,5 s, ni de las líneas de la placa
        assert ln["original"] == ln["es"]
        assert len(mod_dialogo.lineas_frase(marca, ln["es"], marca.pc["dialogo"])) <= marca.max_lineas_frase
        placas.placa_dialogo(marca, 1, {"nombre": ln["hablante"], "frase": ln["es"], "t0": ln["t0"], "t1": ln["t1"],
                                        "voz": ln["voz"]})                # la placa la dibuja sin error
    assert all(a["t1"] <= b["t0"] + 1e-9 for a, b in zip(lineas, lineas[1:]))       # no se solapan
    assert max(ln["t1"] - ln["t0"] for ln in lineas) <= 4.5 + 0.3 + voces.HOLGURA_S


def test_el_nombre_del_streamer_se_recorta_a_la_columna(marca):
    assert voces.nombre_visible("twitch.tv/ibai", marca) == "IBAI"
    assert voces.nombre_visible("@guionvivo", marca) == "GUIONVIVO"
    largo = voces.nombre_visible("twitch.tv/" + "x" * 80, marca)
    cfg = marca.pc["dialogo"]
    assert marca.fuente("regular", cfg["tam_nombre"]).getlength(largo) <= 840 - 2 * cfg["relleno_x"]
    assert voces.nombre_visible(None, marca) == "STREAMER"


def test_asignar_voz_por_solape():
    turnos = [{"t0": 0.0, "t1": 3.0, "voz": "streamer"}, {"t0": 3.0, "t1": 6.0, "voz": "otra"}]
    lineas = [Linea(0.5, 2.0, "A", "x"), Linea(2.5, 5.0, "B", "y"), {"t0": 7.0, "t1": 8.0}]
    assert voces.asignar_voz(lineas, turnos) == ["streamer", "otra", "otra"]       # sin solape: el turno más cercano
    assert voces.asignar_voz(lineas, []) == ["streamer"] * 3


# ---------------------------------------------------------------- analizar de punta a punta (el "video" es un WAV)

@necesita_ffmpeg
def test_analizar_remapea_todo_al_tiempo_de_la_toma(tmp_path, marca):
    x, turnos, t_golpe = escena()
    wav = guardar_wav(tmp_path / "escena.wav", x)
    segs = palabras_de(turnos)
    v = voces.analizar(wav, 0.0, 12.6, marca, Cfg(), minimo_s=7.0, nombre_streamer="twitch.tv/ibai",
                       transcriptor=falso(segs))
    assert v.motivo is None and v.idioma == "es" and v.modelo == "base" and v.hablantes == 2
    assert len(v.cortes) == 1 and abs(v.cortes[0][0] - 3.52) < 0.1 and abs(v.cortes[0][1] - 4.48) < 0.1
    quitado = v.cortes[0][1] - v.cortes[0][0]
    assert v.duracion_toma == pytest.approx(12.6 - quitado, abs=0.01) and v.hay_cortes
    # una palabra de después del corte llega más temprano, la de antes queda igual
    assert v.palabras[0]["t0"] == pytest.approx(segs[0]["palabras"][0]["t0"])
    despues = next(p for p in v.palabras if p["texto"] == segs[2]["palabras"][0]["texto"] and p["t0"] > 3.0)
    assert despues["t0"] == pytest.approx(segs[2]["palabras"][0]["t0"] - quitado, abs=0.01)
    assert abs(max(v.golpes, key=lambda g: g["db"])["t"] - (t_golpe - quitado)) < 0.08
    assert [ln["voz"] for ln in v.lineas][:3] == ["streamer", "otra", "streamer"]
    assert v.lineas[0]["hablante"] == "IBAI" and v.lineas[1]["hablante"] == "OTRA VOZ"
    assert all(0 <= ln["t0"] < ln["t1"] <= v.duracion_toma + 1e-6 for ln in v.lineas)
    assert all(a["t1"] <= b["t0"] + 1e-6 for a, b in zip(v.lineas, v.lineas[1:]))
    assert v.sin_cortes().duracion_toma == 12.6 and v.sin_cortes().cortes == [] and v.sin_cortes().tramos == [(0.0, 12.6)]


@necesita_ffmpeg
def test_analizar_con_el_minimo_alto_no_corta(tmp_path, marca):
    x, turnos, _ = escena()
    wav = guardar_wav(tmp_path / "escena.wav", x)
    v = voces.analizar(wav, 0.0, 12.6, marca, Cfg(), minimo_s=12.0, nombre_streamer="x", transcriptor=falso(palabras_de(turnos)))
    assert v.cortes == [] and v.duracion_toma == 12.6 and not v.hay_cortes


@necesita_ffmpeg
def test_analizar_idioma_no_espanol_no_trae_lineas(tmp_path, marca):
    x, turnos, _ = escena()
    wav = guardar_wav(tmp_path / "escena.wav", x)
    v = voces.analizar(wav, 0.0, 12.6, marca, Cfg(), minimo_s=7.0, nombre_streamer="x",
                       transcriptor=falso(palabras_de(turnos), idioma="en"))
    assert v.idioma == "en" and not v.es_espanol and v.lineas == [] and len(v.palabras) > 0 and v.hablantes == 2


@necesita_ffmpeg
def test_analizar_sin_whisper_sigue_con_silencios_y_golpes(tmp_path, marca, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr(voces, "_modelos", {})
    x, _, _ = escena()
    wav = guardar_wav(tmp_path / "escena.wav", x)
    v = voces.analizar(wav, 0.0, 12.6, marca, Cfg(), minimo_s=7.0, nombre_streamer="x")
    assert "faster-whisper" in v.motivo and v.lineas == [] and v.palabras == [] and v.modelo is None
    assert len(v.cortes) == 1 and len(v.golpes) >= 1


@necesita_ffmpeg
def test_analizar_sin_audio_dice_el_motivo(tmp_path, marca):
    mudo = tmp_path / "mudo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=160x90:rate=10:duration=2", "-an", str(mudo)], check=True)
    v = voces.analizar(mudo, 0.0, 2.0, marca, Cfg(), minimo_s=1.0, nombre_streamer="x", transcriptor=falso([]))
    assert v.motivo and "audio" in v.motivo and v.tramos == [(0.0, 2.0)] and v.duracion_toma == 2.0


@necesita_ffmpeg
def test_la_medicion_trae_carga_y_transcripcion_por_modelo(tmp_path):
    x, turnos, _ = escena()
    wav = guardar_wav(tmp_path / "escena.wav", x)

    def tr(w, cfg, nombre):
        return Transcripcion("es", 0.99, palabras_de(turnos), nombre, 2.5 if nombre == "small" else 1.0, 0.7)
    res = voces.medir(wav, 0.0, 12.6, Cfg(), ["base", "small", "roto"],
                      transcriptor=lambda w, c, n: (_ for _ in ()).throw(OSError("sin modelo")) if n == "roto" else tr(w, c, n))
    assert [m["modelo"] for m in res["modelos"]] == ["base", "small", "roto"]
    assert res["modelos"][1]["segundos"] == 2.5 and res["modelos"][1]["segundos_carga"] == 0.7
    assert "error" in res["modelos"][2] and res["hablantes"]["n"] == 2 and len(res["cortes"]) == 1
    json.dumps(res)                                                     # se puede escribir como JSON


# ---------------------------------------------------------------- la orden de ffmpeg

VIDEO = {"ruta": "/tmp/f.mp4", "inicio": 2.0, "recorte": {"x": 10, "y": 0, "ancho": 202, "alto": 360}, "audio": True}


def test_la_orden_sin_tramos_es_la_de_siempre():
    sin = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=4.0, ancho=1080, alto=1920, video=VIDEO,
                                                  placas=[("p.png", 60, 150, 0.0, 3.5)])
    con_none = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=4.0, ancho=1080, alto=1920, video=VIDEO,
                                                       placas=[("p.png", 60, 150, 0.0, 3.5)], tramos=None)
    assert sin == con_none
    assert sin[sin.index("-filter_complex") + 1] == (
        "[0:v]fps=30,crop=202:360:10:0,scale=1080:1920:flags=lanczos,setsar=1[c0];"
        "[c0][1:v]overlay=60:150:enable='gte(t,0)*lt(t,3.5)'[c1];[c1]format=yuv420p[out]")
    assert sin[sin.index("-map", sin.index("[out]")) + 1] == "0:a:0"
    assert sin[:8] == ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "2", "-t"] and sin[8] == "4"


def test_la_orden_con_tramos_usa_select_y_atrim_concat():
    tramos = [(0.0, 1.5), (2.5, 4.0)]
    o = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=3.0, ancho=1080, alto=1920, video=VIDEO,
                                                placas=[("p.png", 60, 150, 0.0, 2.5)], tramos=tramos)
    filtro = o[o.index("-filter_complex") + 1]
    assert filtro.startswith("[0:v]fps=30,select='gte(t,-0.016)*lt(t,1.484)+gte(t,2.484)*lt(t,3.984)',"
                             "setpts=N/FRAME_RATE/TB,crop=")
    assert ("[0:a]asplit=2[s0][s1];[s0]atrim=start=0:end=1.5,asetpts=PTS-STARTPTS[x0];"
            "[s1]atrim=start=2.5:end=4,asetpts=PTS-STARTPTS[x1];[x0][x1]concat=n=2:v=0:a=1[a0]") in filtro
    assert o[o.index("[out]") + 1:o.index("[out]") + 3] == ["-map", "[a0]"]
    assert o[o.index("-i") - 1] == "4"                    # se lee hasta el final del último tramo
    assert o[o.index("-t", o.index("-map")) + 1] == "3"   # y la salida dura la suma de los tramos
    mudo = dict(VIDEO, audio=False)
    o2 = componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=3.0, ancho=1080, alto=1920, video=mudo,
                                                 placas=[], tramos=tramos)
    assert "atrim" not in o2[o2.index("-filter_complex") + 1]


def test_tramos_invalidos_o_que_no_suman_la_duracion_son_un_error():
    assert componer._tramos_validos(None, 5.0) is None
    assert componer._tramos_validos([(0.0, 5.0)], 5.0) is None            # un solo tramo desde 0: no hay cortes
    assert componer._tramos_validos([(0.0, 1.5), (2.5, 4.0)], 3.0) == [(0.0, 1.5), (2.5, 4.0)]
    for malos in ([(0.0, 1.5), (2.5, 4.0)], [(2.0, 1.0)], [(0.0, 2.0), (1.0, 3.0)]):
        with pytest.raises(ValueError):
            componer._tramos_validos(malos, 2.0 if malos[0] == (2.0, 1.0) else 9.0)


# ---------------------------------------------------------------- placa con color de voz y estilo

def pixeles_de(img, color, tol=40):
    a = np.asarray(img.convert("RGB")).astype(int)
    return int((np.abs(a - np.array(color)).sum(axis=2) < tol).sum())


def test_la_placa_de_la_otra_voz_pinta_en_lapiz_rojo_y_sin_voz_sale_igual_que_antes(marca):
    base = {"nombre": "CHAMÁN", "frase": "¿Qué te trae por aquí?", "t0": 0.5, "t1": 2.5}
    sin = placas.placa_dialogo(marca, 1, base)
    objeto = placas.placa_dialogo(marca, 1, Linea(0.5, 2.5, "CHAMÁN", "¿Qué te trae por aquí?"))
    streamer = placas.placa_dialogo(marca, 1, {**base, "voz": "streamer"})
    otra = placas.placa_dialogo(marca, 1, {**base, "voz": "otra"})
    assert sin.imagen.tobytes() == objeto.imagen.tobytes() == streamer.imagen.tobytes()
    assert pixeles_de(sin.imagen, marca.color("lapiz_rojo")) == 0
    assert pixeles_de(otra.imagen, marca.color("lapiz_rojo")) > 200
    # el texto principal: tinta sobre papel (placa) o papel con contorno de tinta (v3, contorno)
    principal = "papel" if marca.pc["dialogo"].get("estilo") == "contorno" else "tinta"
    assert pixeles_de(otra.imagen, marca.color(principal)) < pixeles_de(sin.imagen, marca.color(principal)) // 4
    assert otra.caja() == sin.caja()                                       # misma caja y mismos tiempos
    con_objeto = placas.placa_dialogo(marca, 1, Linea(0.5, 2.5, "CHAMÁN", "¿Qué te trae por aquí?", voz="otra"))
    assert con_objeto.imagen.tobytes() == otra.imagen.tobytes()


def test_color_voz_lee_marca_yml_y_sin_voz_es_tinta(marca):
    assert marca.color_voz(None) == marca.color("tinta") == marca.color_voz("streamer")
    assert marca.color_voz("otra") == marca.color("lapiz_rojo")
    sin_seccion = Marca({k: v for k, v in marca.datos.items() if k != "voces"}, marca.raiz)
    assert sin_seccion.color_voz("otra") == sin_seccion.color("tinta")


def test_la_voz_va_en_el_plan_solo_si_existe(marca):
    t = lienzo.Toma(numero=1, gancho="Ella busca a un dragón", palabra_lapiz="DRAGÓN", lapiz="círculo", escena="X",
                    credito="Con: a")
    plan = componer.plan_json(marca, t, [Linea(0.5, 2.0, "A", "hola", voz="otra"), Linea(2.0, 3.0, "B", "adiós")], 4.0)
    assert plan["lineas"][0]["voz"] == "otra" and "voz" not in plan["lineas"][1]


def test_t_fuente_con_y_sin_tramos():
    assert estilo.t_fuente({"inicio": 2.0}, 1.5) == 3.5
    assert estilo.t_fuente({"inicio": 2.0, "tramos": None}, 1.5) == 3.5
    origen = {"inicio": 2.0, "tramos": [[0.0, 1.0], [2.0, 4.0]]}
    assert estilo.t_fuente(origen, 0.5) == pytest.approx(2.5)
    assert estilo.t_fuente(origen, 1.5) == pytest.approx(2.0 + 2.5)


# ---------------------------------------------------------------- validación de la configuración

def test_validar_voces_valido_e_invalido(repo, editar_yaml):
    assert validar.validar(repo).errores == []
    cambios = [
        (lambda d: d.update(modo="mixto"), "modo"),
        (lambda d: d.update(activo="si"), "activo"),
        (lambda d: d.update(tope_s=1), "tope_s"),
        (lambda d: d.update(silencio_min_s=0.2, margen_s=0.2), "silencio_min_s"),
        (lambda d: d.update(umbral_db=5), "umbral_db"),
        (lambda d: d["golpes"].update(max=-1), "golpes.max"),
        (lambda d: d["hablantes"].update(max=3), "hablantes.max"),
        (lambda d: d["lineas"].pop("pausa_s"), "lineas.pausa_s"),
        (lambda d: d.update(alucinaciones="amara"), "alucinaciones"),
        (lambda d: d.update(nombre_otra=""), "nombre_otra"),
        (lambda d: d.update(idioma="español"), "idioma"),
        (lambda d: d.pop("modelo"), "modelo"),
    ]
    for cambiar, texto in cambios:
        copia = yaml.safe_load((RAIZ_REAL / "config" / "voces.yml").read_text(encoding="utf-8"))
        cambiar(copia)
        (repo / "config" / "voces.yml").write_text(yaml.safe_dump(copia, allow_unicode=True), encoding="utf-8")
        errores = validar.validar(repo).errores
        assert any(texto in e for e in errores), (texto, errores)
    (repo / "config" / "voces.yml").unlink()                              # sin el archivo: aviso, no error
    inf = validar.validar(repo)
    assert inf.errores == [] and any("voces.yml" in a for a in inf.avisos)


def test_validar_marca_voces(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda d: d["voces"].update(otra="verde_fosforito"))
    assert any("voces.otra" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "marca.yml", lambda d: d["voces"].update(otra="lapiz_rojo", extra="tinta"))
    assert any("voces.extra" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "marca.yml", lambda d: d.pop("voces") and None)              # la sección es opcional
    assert validar.validar(repo).errores == []


# ---------------------------------------------------------------- render real corto con tramos

def lavfi_con_chirrido(ruta, seg=4):
    """Fuente 16:9 con un chirrido que sube (cada instante suena distinto: un mapa de tiempo mal puesto se nota)."""
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size=640x360:rate=30:duration={seg}", "-f", "lavfi", "-i",
                    f"aevalsrc=0.5*sin(2*PI*(300+30*t)*t):s=44100:d={seg}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(ruta)], check=True)
    return ruta


def en_rojo(puntos):
    return [n for n, ok, _ in puntos if not ok and "Duración de 7 a 30" not in n]


@pytest.mark.slow
@necesita_ffmpeg
def test_render_real_con_tramos_dura_la_suma_y_la_puerta_de_origen_y_audio_pasa(tmp_path, marca):
    fuente = lavfi_con_chirrido(tmp_path / "f.mp4")
    t = lienzo.Toma(numero=7, gancho="Ella busca a un dragón", palabra_lapiz="DRAGÓN", lapiz="círculo",
                    escena="INT. CABAÑA — NOCHE", credito="Con: Blender Foundation (CC BY 3.0)")
    lineas = [Linea(0.3, 1.3, "IBAI", "Hola a todos.", voz="streamer"),
              Linea(1.5, 2.4, "OTRA VOZ", "¿Qué tal?", voz="otra")]
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, t, lineas, mp4, duracion=3.0, trabajo=tmp_path / "w", fuente_video=fuente,
                             inicio=0.0, tramos=[(0.0, 1.5), (2.5, 4.0)])
    assert plan["origen"]["tramos"] == [[0.0, 1.5], [2.5, 4.0]] and plan["origen"]["duracion"] == 3.0
    assert plan["duracion"] == 3.0 and plan["lineas"][1]["voz"] == "otra"
    info = estilo.sondear(mp4)
    assert info["duracion"] == pytest.approx(3.0, abs=0.1)
    puntos = estilo.revisar(mp4, plan, marca)
    assert en_rojo(puntos) == [], en_rojo(puntos)
    assert any("udio" in n and ok for n, ok, _ in puntos)
    # sin el mapa de tramos, la fuente esperada es otra y la puerta lo ve (frames y audio)
    sin_mapa = json.loads(json.dumps(plan))
    sin_mapa["origen"].pop("tramos")
    assert estilo.audio_original(mp4, sin_mapa)[0] is False
    assert estilo.audio_original(mp4, plan)[0] is True
    # y un plan sin cortes no escribe `tramos`
    plan2 = componer.componer(marca, t, lineas, tmp_path / "t2.mp4", duracion=4.0, trabajo=tmp_path / "w2",
                              fuente_video=fuente, inicio=0.0, tramos=[(0.0, 4.0)])
    assert "tramos" not in plan2["origen"]


# ---------------------------------------------------------------- integración con _hacer_toma (voces falsas)

def _voces_falsas(tramos, *, idioma="es", motivo=None):
    """Voces de un recorte de 12 s con un silencio quitado de 5 a 6 s (relativo al recorte)."""
    crudo = {"lineas": [
        {"t0": 0.6, "t1": 2.0, "hablante": "PROBADOR", "original": "Hola a todos.", "es": "Hola a todos.", "voz": "streamer"},
        {"t0": 2.4, "t1": 4.6, "hablante": "OTRA VOZ", "original": "¿Qué tal?", "es": "¿Qué tal?", "voz": "otra"},
        {"t0": 6.5, "t1": 9.0, "hablante": "PROBADOR", "original": "Mira esto.", "es": "Mira esto.", "voz": "streamer"}],
        "palabras": [{"texto": "Hola", "t0": 0.6, "t1": 1.0}, {"texto": "esto", "t0": 8.0, "t1": 8.5}],
        "golpes": [{"t": 7.0, "db": -3.0}]}
    v = Voces(duracion=12.0, duracion_toma=12.0, idioma=idioma, prob_idioma=0.99, modelo="base", segundos=1.5,
              hablantes=2, motivo=motivo,
              turnos=[{"t0": 0.5, "t1": 2.2, "voz": "streamer"}, {"t0": 2.3, "t1": 5.0, "voz": "otra"},
                      {"t0": 6.0, "t1": 9.5, "voz": "streamer"}], crudo=crudo)
    return v.con_tramos(tramos)


@pytest.fixture
def entorno_toma(repo, editar_yaml, monkeypatch, tmp_path_factory):
    import test_n4 as n4
    editar_yaml(repo, "config/voces.yml", lambda d: d.update(activo=True))
    video = tmp_path_factory.mktemp("v20") / "fuente.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=1280x720:rate=30:duration=20", "-f", "lavfi", "-i",
                    "aevalsrc=0.5*sin(2*PI*(300+30*t)*t):s=44100:d=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)

    def correr(tmp_path, voces_falsas, nombre="valida"):
        llamadas = []

        def analizar(ruta, inicio, duracion, marca_, cfg, **kw):
            llamadas.append((inicio, duracion, kw))
            return voces_falsas
        monkeypatch.setattr(toma.mod_voces, "analizar", analizar)
        r = toma.hacer_toma(repo, n4.candidato(), n4.ficha_propia(), n4.AHORA, salida=tmp_path / "salida",
                            abrir=n4.abrir_con(video), transporte=n4.Transporte((200, n4.sobre(n4.respuesta_grabada(nombre)))),
                            entorno={"GEMINI_API_KEY": "clave-de-prueba"}, espera=lambda s: None)
        return r, llamadas
    return correr


@pytest.mark.slow
@necesita_ffmpeg
def test_hacer_toma_modo_principal_reemplaza_las_lineas_y_corta_el_silencio(entorno_toma, tmp_path):
    r, llamadas = entorno_toma(tmp_path, _voces_falsas([(0.0, 5.0), (6.0, 12.0)]))
    assert r.ok, r.motivos
    assert llamadas[0][0] == 2.0 and llamadas[0][1] == 12.0 and llamadas[0][2]["minimo_s"] == 7.0
    assert llamadas[0][2]["nombre_streamer"] == "PROBADOR"     # nombre para mostrar (#30), no el login
    p = r.plan
    assert p["duracion"] == 11.0 and p["origen"]["duracion"] == 11.0 and p["origen"]["tramos"] == [[0.0, 5.0], [6.0, 12.0]]
    assert r.fila["duracion_s"] == 11.0
    assert estilo.sondear(r.mp4)["duracion"] == pytest.approx(11.0, abs=0.1)
    assert [ln["frase"] for ln in p["lineas"]] == ["Hola a todos.", "¿Qué tal?", "Mira esto."]
    assert [ln.get("voz") for ln in p["lineas"]] == ["streamer", "otra", "streamer"]
    assert p["lineas"][0]["acotacion"] == "(se ríe)"                       # la acotación de Gemini se conserva
    assert p["lineas"][2]["t0"] == pytest.approx(5.5) and p["lineas"][2]["t1"] == pytest.approx(8.0)
    assert p["voces"]["fuente_lineas"] == "voces" and p["voces"]["modo"] == "principal"
    assert p["voces"]["hablantes"] == 2 and p["voces"]["cortes"] == [[5.0, 6.0]] and p["voces"]["modelo"] == "base"
    assert p["voces"]["golpes"] == [{"t": 6.0, "db": -3.0}]
    assert p["transcripcion"] == {"motor": "faster-whisper", "modelo": "base", "idioma": "es",
                                  "palabras": [{"texto": "Hola", "t0": 0.6, "t1": 1.0},
                                               {"texto": "esto", "t0": 7.0, "t1": 7.5}]}
    en_disco = json.loads(r.mp4.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert en_disco["voces"] == p["voces"] and en_disco["transcripcion"] == p["transcripcion"]
    assert all(ok for _, ok, _ in r.puntos)


@pytest.mark.slow
@necesita_ffmpeg
def test_hacer_toma_modo_respaldo_conserva_las_de_gemini_con_voz_y_tiempos_remapeados(entorno_toma, tmp_path, repo, editar_yaml):
    editar_yaml(repo, "config/voces.yml", lambda d: d.update(modo="respaldo"))
    r, _ = entorno_toma(tmp_path, _voces_falsas([(0.0, 5.0), (6.0, 12.0)]))
    assert r.ok, r.motivos
    p = r.plan
    assert [ln["frase"] for ln in p["lineas"]] == ["Por fin estoy en la ciudad.", "Mira esta vista."]
    assert p["voces"]["fuente_lineas"] == "gemini" and p["voces"]["modo"] == "respaldo"
    assert all("voz" in ln for ln in p["lineas"])
    assert p["lineas"][0]["t0"] == pytest.approx(0.5) and p["lineas"][1]["t0"] == pytest.approx(5.0)
    assert p["lineas"][1]["t1"] == pytest.approx(8.0)                      # 9.0 s del recorte, menos el silencio de 1 s
    assert p["origen"]["tramos"] == [[0.0, 5.0], [6.0, 12.0]] and "transcripcion" in p


@pytest.mark.slow
@necesita_ffmpeg
def test_hacer_toma_idioma_no_espanol_nunca_usa_las_lineas_de_voces(entorno_toma, tmp_path):
    r, _ = entorno_toma(tmp_path, _voces_falsas([(0.0, 5.0), (6.0, 12.0)], idioma="en"))
    assert r.ok, r.motivos
    assert r.plan["voces"]["fuente_lineas"] == "gemini" and r.plan["voces"]["idioma"] == "en"
    assert [ln["frase"] for ln in r.plan["lineas"]] == ["Por fin estoy en la ciudad.", "Mira esta vista."]


@pytest.mark.slow
@necesita_ffmpeg
def test_hacer_toma_si_las_voces_fallan_sigue_como_hoy(entorno_toma, tmp_path):
    sin = Voces(duracion=12.0, duracion_toma=12.0, tramos=[(0.0, 12.0)], motivo="sin faster-whisper (ImportError)",
                crudo={"lineas": [], "palabras": [], "golpes": []}).con_tramos([(0.0, 12.0)])
    r, _ = entorno_toma(tmp_path, sin)
    assert r.ok, r.motivos
    p = r.plan
    assert p["duracion"] == 12.0 and "tramos" not in p["origen"] and "transcripcion" not in p
    assert p["voces"]["motivo"].startswith("sin faster-whisper")
    assert [ln["frase"] for ln in p["lineas"]] == ["Por fin estoy en la ciudad.", "Mira esta vista."]


def test_sin_lineas_de_gemini_la_transcripcion_las_rellena_aunque_el_modo_sea_respaldo():
    voces_falsas = _voces_falsas([(0.0, 12.0)])
    nuevas, fuente = toma.lineas_con_voces(voces_falsas, [], "respaldo")
    assert fuente == "voces" and [ln.frase for ln in nuevas] == ["Hola a todos.", "¿Qué tal?", "Mira esto."]
    assert nuevas[1].voz == "otra" and nuevas[0].nombre == "PROBADOR"
    # una acotación suelta de Gemini (sin frases) tampoco impide rellenar
    nuevas, fuente = toma.lineas_con_voces(voces_falsas, [Linea(0.8, 5.0, acotacion="(se ríe)")], "respaldo")
    assert fuente == "voces"
    # con líneas de Gemini, el modo respaldo no las reemplaza; el principal sí
    previas = [Linea(0.5, 2.0, "X", "de Gemini")]
    mantenidas, fuente = toma.lineas_con_voces(voces_falsas, previas, "respaldo")
    assert fuente == "gemini" and mantenidas[0].frase == "de Gemini" and mantenidas[0].voz == "streamer"
    assert toma.lineas_con_voces(voces_falsas, previas, "principal")[1] == "voces"


def test_la_etapa_de_voces_se_salta_si_no_queda_tiempo(repo, editar_yaml, marca, monkeypatch):
    from fabrica import plazo
    editar_yaml(repo, "config/voces.yml", lambda d: d.update(activo=True))
    monkeypatch.setattr(toma.mod_voces, "analizar", lambda *a, **k: pytest.fail("no debía analizar"))
    registro = []
    plazo.registrar(registro.append)
    plazo.iniciar(200)                                    # quedan menos de tope_s + reserva_s (300 s)
    try:
        lineas = [Linea(0.5, 2.0, "A", "x")]
        res = toma._etapa_voces(repo, marca, "video.mp4", 2.0, 12.0, lineas, {"idioma_original": "inglés"}, "x", 7.0)
    finally:
        plazo.detener()
        plazo.registrar()
    assert res.lineas is lineas and res.dur == 12.0 and res.tramos is None and "sin tiempo" in res.info["motivo"]
    assert any("sin voces" in r for r in registro)


def test_las_pruebas_parten_con_las_voces_apagadas(repo):
    assert Cfg.cargar(repo).activo is False and Cfg.cargar(RAIZ_REAL).activo is True
    assert Cfg.cargar(RAIZ_REAL / "pruebas") is None                       # sin config/voces.yml: no hay voces


# ---------------------------------------------------------------- muestra con voces

@pytest.mark.slow
@necesita_ffmpeg
def test_la_muestra_con_voces_renderiza_sin_whisper_y_escribe_voces_json(tmp_path, marca, monkeypatch):
    from datetime import date
    from fabrica.editar import muestras
    monkeypatch.setitem(sys.modules, "faster_whisper", None)             # como en local: sin biblioteca ni modelo
    monkeypatch.setattr(voces, "_modelos", {})
    todas = {m["id"]: m for m in muestras.cargar_muestras()}
    m = todas["voces-sintel"]
    assert m["voces"] is True and m["recorte"] == todas["dialogo-sintel"]["recorte"]
    assert m["video"] == todas["dialogo-sintel"]["video"] and m["licencia"] == todas["dialogo-sintel"]["licencia"]
    fuente = lavfi_con_chirrido(tmp_path / "sintel.mp4", seg=20)
    ficha = {"id": m["fuente_id"], "tipo": "B", "estado": "vigente", "uso": "muestras", "publicable": False,
             "vence": None, "material": [m["video"]["url"], m["subtitulos"]["url"]], "reglas": {}}
    salida = tmp_path / "salida"
    salida.mkdir()
    m = {**m, "recorte": {"inicio": 0.0, "duracion": 17.0}}
    man = muestras.generar(salida, marca, [m], tmp_path, fichas=[ficha], hoy=date(2026, 11, 20),
                           descargador=lambda url, sha, destino: fuente)
    vj = json.loads((salida / "voces.json").read_text(encoding="utf-8"))
    assert vj["muestra"] == "voces-sintel" and "faster-whisper" in vj["motivo"] and vj["modelo"] is None
    assert [x["modelo"] for x in vj["medicion"]["modelos"]] == ["base", "small"]
    assert all("error" in x for x in vj["medicion"]["modelos"])
    hecha = next(x for x in man["muestras"] if x["id"] == "voces-sintel")
    assert hecha["puerta_estilo"]["en_verde"], [p for p in hecha["puerta_estilo"]["puntos"] if not p["ok"]]
    assert [ln["frase"] for ln in hecha["lineas"]][:2] == ["¿Qué te trae a la tierra de los guardianes?", "Busco a alguien."]
    plan = json.loads((salida / "voces-sintel.plan.json").read_text(encoding="utf-8"))
    assert plan["voces"]["motivo"] and plan["origen"]["duracion"] == pytest.approx(hecha["duracion_s"], abs=0.1)


@pytest.mark.slow
@necesita_ffmpeg
def test_la_muestra_con_voces_y_transcriptor_falso_colorea_las_voces_y_corta_el_silencio(tmp_path, marca):
    from datetime import date
    from fabrica.editar import muestras
    x, turnos, _ = escena()
    wav = guardar_wav(tmp_path / "audio.wav", x)
    video = tmp_path / "fuente.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=30:duration=13", "-i", str(wav), "-af", "apad=whole_dur=13", "-t", "13",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    m = {"id": "voces-prueba", "voces": True, "pilar": "primeras_veces", "fuente_id": "fx",
         "video": {"url": "https://raw.githubusercontent.com/x/y/v.mp4", "sha256": "0" * 64},
         "licencia": "CC BY 3.0", "enlace_licencia": "https://creativecommons.org/licenses/by/3.0/",
         "recorte": {"inicio": 0.0, "duracion": 12.6},
         "toma": {"numero": 4, "gancho": "Ella busca a un dragón", "palabra_lapiz": "DRAGÓN", "lapiz": "subrayado",
                  "escena": "INT. CABAÑA — NOCHE", "credito": "Con: prueba"},
         "lineas": [{"t0": 0.2, "t1": 1.4, "nombre": "CHAMÁN", "frase": "Hola.", "original": "Hello."},
                    {"t0": 2.0, "t1": 3.2, "nombre": "SINTEL", "frase": "Adiós.", "original": "Bye."},
                    {"t0": 5.0, "t1": 6.0, "nombre": "CHAMÁN", "frase": "Otra vez.", "original": "Again."}]}
    ficha = {"id": "fx", "tipo": "B", "estado": "vigente", "uso": "muestras", "publicable": False, "vence": None,
             "material": [m["video"]["url"]], "reglas": {}}
    salida = tmp_path / "salida"
    salida.mkdir()
    man = muestras.generar(salida, marca, [m], tmp_path, fichas=[ficha], hoy=date(2026, 11, 20),
                           descargador=lambda url, sha, destino: video,
                           transcriptor_voces=falso(palabras_de(turnos), idioma="en"))
    hecha = man["muestras"][0]
    assert hecha["puerta_estilo"]["en_verde"], [p for p in hecha["puerta_estilo"]["puntos"] if not p["ok"]]
    assert [ln.get("voz") for ln in hecha["lineas"]] == ["streamer", "otra", "streamer"]
    vj = json.loads((salida / "voces.json").read_text(encoding="utf-8"))
    assert vj["hablantes"] == 2 and len(vj["cortes"]) == 1 and vj["idioma"] == "en"
    assert vj["duracion_toma_s"] < vj["duracion_recorte_s"] and hecha["duracion_s"] == pytest.approx(vj["duracion_toma_s"], abs=0.1)
    assert {x["modelo"] for x in vj["medicion"]["modelos"]} == {"base", "small"}
    assert all("segundos" in x and "segundos_carga" in x for x in vj["medicion"]["modelos"])


# ---------------------------------------------------------------- ronda 1: reintento, 8 cortes, medición

@pytest.mark.slow
@necesita_ffmpeg
def test_si_las_lineas_de_voces_no_componen_se_reintenta_con_las_de_gemini_sin_cortes(entorno_toma, tmp_path):
    v = _voces_falsas([(0.0, 5.0), (6.0, 12.0)])
    v.crudo["lineas"][1]["es"] = "palabra " * 120                       # no cabe en la columna: ValueError al componer
    v = v.con_tramos([(0.0, 5.0), (6.0, 12.0)])
    r, _ = entorno_toma(tmp_path, v)
    assert r.ok, r.motivos
    p = r.plan
    assert p["duracion"] == 12.0 and "tramos" not in p["origen"]
    assert p["voces"]["fuente_lineas"] == "gemini" and "reintento" in p["voces"] and p["voces"]["cortes"] == []
    assert [ln["frase"] for ln in p["lineas"]] == ["Por fin estoy en la ciudad.", "Mira esta vista."]
    assert p["transcripcion"]["palabras"][1]["t0"] == pytest.approx(8.0)       # tiempos de la toma sin cortes
    assert estilo.sondear(r.mp4)["duracion"] == pytest.approx(12.0, abs=0.1)


def test_la_acotacion_de_gemini_va_en_la_primera_linea_que_sobrevive(repo, editar_yaml, marca, monkeypatch):
    editar_yaml(repo, "config/voces.yml", lambda d: d.update(activo=True))
    v = _voces_falsas([(0.0, 12.0)])
    v.crudo["lineas"][0].update(t0=0.0, t1=0.1)                           # la primera es demasiado corta y se descarta
    v = v.con_tramos([(0.0, 12.0)])
    monkeypatch.setattr(toma.mod_voces, "analizar", lambda *a, **k: v)
    ev = toma._etapa_voces(repo, marca, "v.mp4", 2.0, 12.0, [Linea(0.5, 4.0, "G", "x", acotacion="(se ríe)")],
                           {"idioma_original": "español"}, "probador", 7.0)
    assert ev.info["fuente_lineas"] == "voces" and ev.lineas[0].frase == "¿Qué tal?" and ev.lineas[0].acotacion == "(se ríe)"
    assert ev.info["descartados"] == {} and ev.alterno is not None


def cuadros_y_audio(mp4):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(mp4)], capture_output=True, text=True).stdout
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    return int(out.strip()), len(pcm) // 2 / 48000


@pytest.mark.slow
@necesita_ffmpeg
def test_8_cortes_renderizados_no_derivan_entre_audio_y_video(tmp_path, marca):
    fuente = lavfi_con_chirrido(tmp_path / "f.mp4", seg=20)
    sil = [(1.0 + 2.3 * i, 1.0 + 2.3 * i + 0.83) for i in range(8)]
    tramos = voces.tramos_a_conservar(20.0, sil, 0.12, 7.0)
    assert len(tramos) == 9
    dur = round(sum(b - a for a, b in tramos), 3)
    t = lienzo.Toma(numero=7, gancho="Ella busca a un dragón", palabra_lapiz="DRAGÓN", lapiz="círculo",
                    escena="INT. CABAÑA — NOCHE", credito="Con: prueba")
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, t, [Linea(0.5, 2.0, "A", "Hola.")], mp4, duracion=dur, trabajo=tmp_path / "w",
                             fuente_video=fuente, inicio=0.0, tramos=tramos)
    cuadros, audio_s = cuadros_y_audio(mp4)
    assert cuadros / 30 == pytest.approx(audio_s, abs=0.001), (cuadros, audio_s)
    assert cuadros / 30 == pytest.approx(sum(b - a for a, b in tramos), abs=0.035)
    assert en_rojo(estilo.revisar(mp4, plan, marca)) == []


def test_medir_vacia_la_cache_y_guarda_las_transcripciones(tmp_path, monkeypatch):
    x, turnos, _ = escena()
    wav = guardar_wav(tmp_path / "escena.wav", x)
    voces._modelos["base"] = object()                                     # un modelo "ya cargado" de antes
    vistos = []

    def tr(w, cfg, nombre):
        vistos.append(dict(voces._modelos))
        return Transcripcion("es", 0.99, palabras_de(turnos), nombre, 1.0, 0.5)
    guardadas = {}
    res = voces.medir(wav, 0.0, 12.6, Cfg(), ["base", "small"], transcriptor=tr, guardar=guardadas)
    assert vistos == [{}, {}] and set(guardadas) == {"base", "small"}
    assert all("voz" in sg for sg in res["modelos"][0]["segmentos"])
    md = voces.resumen_md(res, "prueba")
    assert "| base | 0.5 | 1.0 | es | 0.99 |" in md and "[streamer]" in md and "[otra]" in md and "Hablantes: 2" in md
    assert "cortes de silencio: 1" in md and "golpes:" in md


# ---------------------------------------------------------------- ronda 2: hablantes calibrados con audio real

TURNOS_SINTEL = [(0.75, 3.09, "C"), (6.33, 8.11, "S"), (9.05, 10.37, "C"), (11.61, 12.47, "C"), (15.12, 15.70, "S")]


def sintel_wav(tmp_path):
    import os
    from pathlib import Path
    ruta = Path(os.environ.get("GRANJA_SINTEL", "/tmp/claude-0/sintel.mp4"))
    if not ruta.is_file():
        pytest.skip("no está el video de Sintel (GRANJA_SINTEL o /tmp/claude-0/sintel.mp4)")
    import hashlib
    if hashlib.sha256(ruta.read_bytes()).hexdigest()[:8] != "358447f3":
        pytest.skip("el archivo no es el sintel-short.mp4 esperado")
    return voces.leer_wav(voces.extraer_audio(ruta, 39.6, 17.0, tmp_path / "sintel.wav"))


@necesita_ffmpeg
def test_sintel_real_chaman_y_sintel_salen_en_dos_voces(tmp_path):
    x, sr = sintel_wav(tmp_path)
    etiquetas, dg = voces.clasificar_hablantes(x, sr, [(a, b) for a, b, _ in TURNOS_SINTEL], Cfg())
    assert etiquetas == ["streamer", "otra", "streamer", "streamer", "otra"], dg      # CHAMÁN = streamer (más tiempo)
    assert dg["separadas"] and dg["f0_hz"]["streamer"] < 120 < dg["f0_hz"]["otra"] and dg["salto_f0"] > 0.5
    assert dg["silueta"] >= 0.25 and dg["distancia"] >= voces.SEPARACION_CENTROS_MIN
    assert dg["tiempo_s"]["otra"] >= 1.5


@necesita_ffmpeg
def test_sintel_real_solo_los_turnos_del_chaman_dan_una_voz(tmp_path):
    x, sr = sintel_wav(tmp_path)
    solo = [(a, b) for a, b, q in TURNOS_SINTEL if q == "C"]
    assert set(voces.hablantes(x, sr, solo, Cfg())) == {"streamer"}                   # tres turnos: no alcanzan
    partidos = []
    for a, b in solo:                                                                  # y partidos en seis: sigue siendo uno
        partidos += [(a, (a + b) / 2), ((a + b) / 2, b)]
    etiquetas, dg = voces.clasificar_hablantes(x, sr, partidos, Cfg())
    assert set(etiquetas) == {"streamer"} and not dg["separadas"] and "tonos" in dg["motivo"]


def musica_de_fondo(n, rng, amp=0.05):
    t = np.arange(n) / SR
    acorde = sum(np.sin(2 * np.pi * f * t) for f in (196.0, 247.0, 392.0, 494.0)) / 4
    return (amp * acorde * (0.7 + 0.3 * np.sin(2 * np.pi * 0.4 * t)) + 0.004 * rng.standard_normal(n)).astype(np.float32)


def test_voz_grave_larga_y_voz_aguda_corta_con_musica_de_fondo():
    rng = np.random.default_rng(11)
    guion = [("A", 2.3), ("B", 1.8), ("A", 1.3), ("A", 0.9), ("B", 0.6), ("A", 1.2)]     # B: 2,4 s en dos turnos
    partes, turnos, t = [], [], 0.0
    for q, d in guion:
        partes += [(voz_a if q == "A" else voz_b)(d, rng), silencio(0.3, rng)]
        turnos.append((t, t + d, q))
        t += d + 0.3
    x = np.concatenate(partes)
    x = x + musica_de_fondo(len(x), rng)
    etiquetas, dg = voces.clasificar_hablantes(x, SR, [(a, b) for a, b, _ in turnos], Cfg())
    assert etiquetas == ["streamer" if q == "A" else "otra" for _, _, q in turnos], dg
    assert dg["f0_hz"]["streamer"] < 130 and dg["f0_hz"]["otra"] > 200


def test_una_voz_con_entonacion_variable_y_musica_sigue_siendo_una():
    rng = np.random.default_rng(12)
    partes, turnos, t = [], [], 0.0
    for f0, d in [(100, 1.6), (125, 1.2), (105, 1.8), (118, 1.0), (95, 1.5), (122, 1.4)]:
        partes += [voz(f0, [(500, 150), (1500, 250)], d, rng), silencio(0.3, rng)]
        turnos.append((t, t + d))
        t += d + 0.3
    x = np.concatenate(partes)
    x = x + musica_de_fondo(len(x), rng)
    etiquetas, dg = voces.clasificar_hablantes(x, SR, turnos, Cfg())
    assert set(etiquetas) == {"streamer"} and not dg["separadas"], dg


def test_el_diagnostico_va_en_el_resumen():
    dg = {"separadas": True, "silueta": 0.383, "distancia": 2.01, "f0_hz": {"streamer": 87.0, "otra": 196.3},
          "salto_f0": 0.81, "tiempo_s": {"streamer": 4.5, "otra": 2.4}}
    md = voces.resumen_md({"video": "x.mp4", "inicio_s": 0, "duracion_s": 17, "modelos": [],
                           "hablantes": {"n": 2, "turnos": [], "diagnostico": dg}, "cortes": [], "golpes": []}, "t")
    assert "Diagnóstico de voces: separadas True, silueta 0.383, distancia 2.01" in md and "'otra': 196.3" in md

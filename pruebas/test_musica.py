"""Tarea #6 «musica»: pistas CC0 en piezas sin audio propio, volumen normalizado y movimiento de entrada.

Las pistas son tonos `sine` de ffmpeg en una carpeta temporal, con un pistas.yml y LICENCIA.md falsos en una
copia del repo: nada de red y ningún audio entra al repo.
"""
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import informativa, validar
from fabrica.editar import componer, estilo, muestras
from fabrica.editar import musica as mod_musica
from fabrica.editar.lienzo import Toma
from fabrica.editar.marca import Marca
from fabrica.registro import ZONA

LIC = ("# Licencia\n\n- Fuente: https://opengameart.org/content/falsa\n- Autor: Prueba\n"
       "- Licencia: CC0\n- Enlace: https://creativecommons.org/publicdomain/zero/1.0/\n")


def tono(ruta: Path, seg: float, hz: int = 440) -> str:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={hz}:duration={seg}", str(ruta)], check=True)
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def pista(pid, sha, estado="verificada", licencia="CC0", asset=None):
    return {"id": pid, "titulo": pid, "autor": "Prueba", "pagina": f"https://opengameart.org/content/{pid}",
            "archivo": "x.ogg", "licencia": licencia, "estilo": "tono", "inicio_s": 0,
            "asset": asset or f"{pid}.wav", "sha256": sha, "estado": estado}


def escribir_pistas(raiz: Path, pistas: list[dict], con_licencia=True):
    d = raiz / "marca" / "musica"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pistas.yml").write_text(yaml.safe_dump({"licencias_permitidas": ["CC0"], "release": "musica",
                                                  "pistas": pistas}, allow_unicode=True), encoding="utf-8")
    for p in pistas:
        if con_licencia:
            (d / p["id"]).mkdir(exist_ok=True)
            (d / p["id"] / "LICENCIA.md").write_text(LIC, encoding="utf-8")


@pytest.fixture
def banco(repo, tmp_path):
    """(repo, carpeta de audio) con 3 pistas verificadas y buenas."""
    audio = tmp_path / "audio"
    audio.mkdir()
    pistas = [pista(f"tono-{i}", tono(audio / f"tono-{i}.wav", 14, 300 + 100 * i)) for i in range(3)]
    escribir_pistas(repo, pistas)
    return repo, audio


# ---------------------------------------------------------------- módulo musica

def test_cargar_filtra_no_verificadas_sin_sha_licencia_ajena_y_sin_licencia_md(repo):
    pistas = [pista("buena", "a" * 64), pista("candidata", "b" * 64, estado="candidata"),
              pista("sin-sha", None), pista("ajena", "c" * 64, licencia="CC-BY"),
              pista("sin-md", "d" * 64)]
    escribir_pistas(repo, pistas)
    (repo / "marca/musica/sin-md/LICENCIA.md").unlink()
    assert [p["id"] for p in mod_musica.cargar(repo)] == ["buena"]


def test_cargar_con_el_repo_real_es_vacio_mientras_las_pistas_son_candidatas():
    assert mod_musica.cargar(RAIZ_REAL) == []


def test_carpeta_lee_la_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("GRANJA_MUSICA_DIR", raising=False)
    assert mod_musica.carpeta() is None
    monkeypatch.setenv("GRANJA_MUSICA_DIR", str(tmp_path))
    assert mod_musica.carpeta() == tmp_path


def test_elegir_rota_por_numero_de_toma_y_descarta_sha_distinto(banco):
    repo, audio = banco
    ids = [mod_musica.elegir(repo, n, audio)["id"] for n in range(6)]
    assert ids == ["tono-0", "tono-1", "tono-2", "tono-0", "tono-1", "tono-2"]
    assert all(a != b for a, b in zip(ids, ids[1:]))
    assert Path(mod_musica.elegir(repo, 1, audio)["ruta"]) == audio / "tono-1.wav"
    # un archivo alterado ya no cuenta, y la rotación sigue entre las que quedan
    tono(audio / "tono-1.wav", 14, 999)
    assert [p["id"] for p in mod_musica.disponibles(repo, audio)] == ["tono-0", "tono-2"]
    assert mod_musica.elegir(repo, 1, audio)["id"] == "tono-2"
    (audio / "tono-0.wav").unlink()
    (audio / "tono-2.wav").unlink()
    assert mod_musica.elegir(repo, 3, audio) is None


def test_disponibles_sin_carpeta_es_vacio(banco, monkeypatch):
    repo, _ = banco
    monkeypatch.delenv("GRANJA_MUSICA_DIR", raising=False)
    assert mod_musica.disponibles(repo) == []


def test_disponibles_cachea_el_sha_por_ruta_y_fecha(banco, monkeypatch):
    repo, audio = banco
    mod_musica.disponibles(repo, audio)
    llamadas = []
    monkeypatch.setattr(mod_musica.hashlib, "sha256", lambda *a, **k: llamadas.append(1) or hashlib.sha256())
    assert len(mod_musica.disponibles(repo, audio)) == 3
    assert llamadas == []


@pytest.mark.parametrize("ficha,audio,esperado", [
    ({"reglas": {"sin_musica": True}}, False, False),
    ({"reglas": {"sin_musica": True}}, True, False),
    ({"reglas": {}}, True, False),
    (None, True, False),
    ({"reglas": {}}, False, True),
    (None, False, True),
])
def test_lleva_musica(ficha, audio, esperado):
    assert mod_musica.lleva_musica(ficha, audio) is esperado


# ---------------------------------------------------------------- orden de ffmpeg

ARGS = dict(salida="s.mp4", duracion=11.0, ancho=1080, cierre_s=0.5, pagina="p.png", cierre="c.png",
            capas=[("d0.png", 0.0, 3.6), ("d1.png", 3.6, 7.0)])
MUSICA = {"ruta": "pista.ogg", "inicio_s": 2, "entrada_s": 0.4, "salida_s": 0.8, "lufs": -16, "pico_db": -1.5,
          "bucle": False}
TEXTO = {"duracion_s": 0.35, "subida_px": 14}


def test_orden_con_musica_mapea_aud_con_loudnorm_y_sin_silencio():
    cmd = componer.orden_ffmpeg(**ARGS, video=None, musica=MUSICA)
    txt = " ".join(cmd)
    assert "-map [out] -map [aud]" in txt and "anullsrc" not in txt
    assert "-ss 2 -t 11 -i pista.ogg" in txt and "-stream_loop" not in txt
    assert ("aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=0.4,afade=t=out:st=10.2:d=0.8,"
            "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aud]") in txt
    assert "[4:a]aformat" in txt          # página, 2 capas y cierre son las entradas 0 a 3; la pista es la 4


def test_orden_con_musica_corta_hace_bucle_antes_de_la_entrada():
    cmd = componer.orden_ffmpeg(**ARGS, video=None, musica={**MUSICA, "bucle": True})
    i = cmd.index("-stream_loop")
    assert cmd[i:i + 6] == ["-stream_loop", "-1", "-ss", "2", "-t", "11"] and cmd[i + 6:i + 8] == ["-i", "pista.ogg"]


def test_orden_con_musica_y_video_con_audio_no_se_mezcla():
    with pytest.raises(ValueError, match="audio propio"):
        componer.orden_ffmpeg(**ARGS, video={"ruta": "v.mp4", "inicio": 0, "alto": 600, "y": 500, "audio": True},
                              musica=MUSICA)


def test_orden_con_musica_y_video_mudo_si_vale():
    txt = " ".join(componer.orden_ffmpeg(**ARGS, video={"ruta": "v.mp4", "inicio": 0, "alto": 600, "y": 500,
                                                         "audio": False}, musica=MUSICA))
    assert "-map [aud]" in txt and "anullsrc" not in txt and "1:a" not in txt.replace("[1:a]", "")


def test_orden_sin_musica_no_toca_el_audio_original_ni_el_silencio():
    con_audio = " ".join(componer.orden_ffmpeg(
        **ARGS, video={"ruta": "v.mp4", "inicio": 0, "alto": 600, "y": 500, "audio": True}))
    assert "-map [out] -map 1:a" in con_audio and "loudnorm" not in con_audio and "[aud]" not in con_audio
    mudo = " ".join(componer.orden_ffmpeg(**ARGS, video=None))
    assert "anullsrc" in mudo and "loudnorm" not in mudo


def test_orden_con_entrada_de_texto_funde_y_sube_cada_capa():
    txt = " ".join(componer.orden_ffmpeg(**ARGS, video=None, entrada_texto=TEXTO))
    assert "[1:v]format=rgba,fade=t=in:st=0:d=0.35:alpha=1[l0]" in txt
    assert "[2:v]format=rgba,fade=t=in:st=3.6:d=0.35:alpha=1[l1]" in txt
    assert "y='if(lt(t,3.6+0.35),14*(1-(t-3.6)/0.35),0)'" in txt
    assert "eval=frame:enable='between(t,3.6,7)'" in txt
    sin = " ".join(componer.orden_ffmpeg(**ARGS, video=None))
    assert "fade=" not in sin and "eval=frame" not in sin


# ---------------------------------------------------------------- componer y puerta de estilo (ffmpeg real)

TOMA = Toma(numero=5, gancho="Prueba de música y movimiento", palabra_lapiz="MOVIMIENTO", lapiz="subrayado",
            escena="INT. TU CONSOLA — NOCHE", credito="Fuente: prueba de estilo")
LINEAS = [componer.Linea(0.0, 3.6, "EL DATO", "Pieza de prueba sin datos reales.", "según la prueba"),
          componer.Linea(3.6, 7.0, "EL DATO", "La música es CC0 y viene del release."),
          componer.Linea(7.0, 10.5, "EL DATO", "El movimiento es sobrio.")]


@pytest.mark.slow
def test_informativa_con_musica_y_movimiento_pasa_toda_la_puerta(banco, tmp_path):
    repo, audio = banco
    marca = Marca.cargar(repo)
    p = mod_musica.elegir(repo, TOMA.numero, audio)
    plan = componer.componer(marca, TOMA, LINEAS, tmp_path / "t.mp4", duracion=11.0, trabajo=tmp_path / "w",
                             musica=p, movimiento=True)
    assert plan["audio"] == {"tipo": "musica", "pista": p["id"], "sha256": p["sha256"],
                             "licencia": f"marca/musica/{p['id']}/LICENCIA.md", "lufs": -16, "inicio_s": 0.0}
    assert plan["movimiento"] == "entrada_texto"
    puntos = estilo.revisar(tmp_path / "t.mp4", plan, marca)
    rojos = [x for x in puntos if not x[1]]
    assert rojos == [], rojos
    nombres = [x[0] for x in puntos]
    for n in ("[plan] Música CC0 en pieza sin audio propio", "[fotograma] Volumen normalizado",
              "[fotograma] Movimiento en pieza sin video", "[fotograma] Diálogo en el video"):
        assert n in nombres


@pytest.mark.slow
def test_pista_mas_corta_que_la_toma_hace_bucle_y_pasa_la_puerta(repo, tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    escribir_pistas(repo, [pista("corta", tono(audio / "corta.wav", 4))])
    marca = Marca.cargar(repo)
    p = mod_musica.elegir(repo, 0, audio)
    plan = componer.componer(marca, TOMA, LINEAS, tmp_path / "t.mp4", duracion=11.0, trabajo=tmp_path / "w",
                             musica=p, movimiento=True)
    assert estilo.sondear(tmp_path / "t.mp4")["duracion"] > 10.9
    assert [x for x in estilo.revisar(tmp_path / "t.mp4", plan, marca) if not x[1]] == []


@pytest.mark.slow
def test_informativa_sin_musica_sale_en_rojo_en_la_puerta(repo, tmp_path):
    marca = Marca.cargar(repo)
    plan = componer.componer(marca, TOMA, LINEAS, tmp_path / "t.mp4", duracion=11.0, trabajo=tmp_path / "w",
                             movimiento=True)
    assert plan["audio"] == {"tipo": "silencio"}
    rojos = [x[0] for x in estilo.revisar(tmp_path / "t.mp4", plan, marca) if not x[1]]
    assert rojos == ["[plan] Música CC0 en pieza sin audio propio"]


@pytest.mark.slow
def test_informativa_con_musica_pero_sin_movimiento_sale_en_rojo(banco, tmp_path):
    repo, audio = banco
    marca = Marca.cargar(repo)
    plan = componer.componer(marca, TOMA, LINEAS, tmp_path / "t.mp4", duracion=11.0, trabajo=tmp_path / "w",
                             musica=mod_musica.elegir(repo, 1, audio))
    rojos = [x[0] for x in estilo.revisar(tmp_path / "t.mp4", plan, marca) if not x[1]]
    assert rojos == ["[fotograma] Movimiento en pieza sin video"]


@pytest.mark.slow
def test_plan_con_pista_ajena_o_sha_distinto_sale_en_rojo(banco, tmp_path):
    repo, audio = banco
    marca = Marca.cargar(repo)
    plan = componer.componer(marca, TOMA, LINEAS, tmp_path / "t.mp4", duracion=11.0, trabajo=tmp_path / "w",
                             musica=mod_musica.elegir(repo, 1, audio), movimiento=True)
    malo = {**plan, "audio": {**plan["audio"], "sha256": "0" * 64}}
    assert not estilo.musica_en_plan(malo, marca, None)[0]
    inexistente = {**plan, "audio": {**plan["audio"], "pista": "no-existe"}}
    assert not estilo.musica_en_plan(inexistente, marca, None)[0]
    (repo / "marca/musica/tono-1/LICENCIA.md").unlink()
    assert not estilo.musica_en_plan(plan, marca, None)[0]


def test_puntos_de_plan_sin_audio_de_origen_con_sin_musica_y_con_origen():
    marca = Marca.cargar(RAIZ_REAL)
    ficha_sm = {"reglas": {"sin_musica": True}}
    base = {"origen": None, "campana": None}
    assert estilo.musica_en_plan({**base, "audio": {"tipo": "silencio"}}, marca, ficha_sm)[0]
    assert estilo.sin_musica_en_plan({**base, "audio": {"tipo": "silencio"}}, ficha_sm)[0]
    assert not estilo.sin_musica_en_plan({**base, "audio": {"tipo": "musica"}}, ficha_sm)[0]
    con_origen = {"origen": {"archivo": "v.mp4"}, "campana": None}
    assert estilo.musica_en_plan({**con_origen, "audio": {"tipo": "original"}}, marca, None)[0]
    assert estilo.musica_en_plan(con_origen, marca, None)[0]                  # plan viejo, sin clave audio
    assert not estilo.musica_en_plan({**con_origen, "audio": {"tipo": "musica", "pista": "x"}}, marca, None)[0]
    muda = {"origen": {"archivo": "v.mp4"}, "campana": {"audio": "silencio"}}
    assert not estilo.musica_en_plan({**muda, "audio": {"tipo": "silencio"}}, marca, None)[0]


SALIDA_EBUR = """[Parsed_ebur128_0 @ 0x1] t: 3.1  TARGET:-23 LUFS  M: -17.0 S: -16.9  I: -16.5 LUFS  LRA: 0.0 LU  FTPK: -3.0 -3.0 dBFS  TPK: -2.9 -2.9 dBTP
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -15.8 LUFS
    Threshold: -25.8 LUFS

  Loudness range:
    LRA:         0.0 LU

  True peak:
    Peak:       -2.1 dBFS
"""


def test_medir_sonoridad_lee_el_resumen_y_no_las_lineas_de_progreso(monkeypatch):
    class R:
        returncode, stderr = 0, SALIDA_EBUR
    monkeypatch.setattr(estilo.subprocess, "run", lambda *a, **k: R)
    assert estilo.medir_sonoridad(Path("x.mp4")) == ({"I": -15.8, "pico": -2.1}, None)
    R.stderr = "nada útil"
    assert estilo.medir_sonoridad(Path("x.mp4"))[0] is None


@pytest.mark.slow
def test_volumen_fuera_de_rango_sale_en_rojo(repo, tmp_path):
    """Un tono a casi plena escala, sin loudnorm, declara música pero rompe el volumen."""
    marca = Marca.cargar(repo)
    mp4 = tmp_path / "fuerte.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=64x64:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-af", "volume=0.98", "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", str(mp4)], check=True)
    ok, det = estilo.volumen_normalizado(mp4, {}, marca)
    assert not ok and "LUFS" in det


def test_componer_rechaza_sin_musica_con_pista_y_video_con_audio(banco, tmp_path):
    repo, audio = banco
    marca = Marca.cargar(repo)
    p = mod_musica.elegir(repo, 0, audio)
    with pytest.raises(ValueError, match="sin_musica"):
        componer.componer(marca, TOMA, LINEAS, tmp_path / "t.mp4", duracion=11.0, trabajo=tmp_path / "w",
                          ficha={"id": "f", "reglas": {"sin_musica": True}}, musica=p, movimiento=True)
    assert not (tmp_path / "t.mp4").exists()
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=12",
                    "-f", "lavfi", "-i", "sine=duration=12", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(video)], check=True)
    with pytest.raises(ValueError, match="audio propio"):
        componer.componer(marca, TOMA, [], tmp_path / "t2.mp4", duracion=11.0, trabajo=tmp_path / "w2",
                          fuente_video=video, musica=p)
    with pytest.raises(ValueError, match="sin video"):
        componer.componer(marca, TOMA, [], tmp_path / "t3.mp4", duracion=11.0, trabajo=tmp_path / "w3",
                          fuente_video=video, movimiento=True)


@pytest.mark.slow
def test_video_con_audio_sigue_con_su_audio_y_plan_original(repo, tmp_path):
    marca = Marca.cargar(repo)
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=12",
                    "-f", "lavfi", "-i", "sine=duration=12", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(video)], check=True)
    plan = componer.componer(marca, TOMA, [], tmp_path / "t.mp4", duracion=11.0, trabajo=tmp_path / "w",
                             fuente_video=video, plan_salida=tmp_path / "t.plan.json")
    assert plan["audio"] == {"tipo": "original"} and plan["movimiento"] is None
    assert [x for x in estilo.revisar(tmp_path / "t.mp4", plan, marca) if not x[1]] == []


# ---------------------------------------------------------------- informativa.armar

AHORA = datetime(2026, 11, 20, 9, 0, tzinfo=ZONA)
ENTRADA = {"id": "dato-prueba", "gancho": "Prueba de música y movimiento", "palabra_lapiz": "MOVIMIENTO",
           "titular": "Prueba", "datos": ["Pieza de prueba sin datos reales.", "El movimiento es sobrio."],
           "fuente_url": "https://example.com", "verificado_el": "2026-11-20",
           "vigente_desde": "2026-11-20T00:00:00-06:00", "vigente_hasta": "2026-11-30T00:00:00-06:00", "usada": False}


def test_informativa_sin_pista_disponible_no_compone(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GRANJA_MUSICA_DIR", str(tmp_path / "vacia"))
    compuso = []
    monkeypatch.setattr(informativa, "componer", lambda *a, **k: compuso.append(1))
    r = informativa.armar(repo, ENTRADA, AHORA, tmp_path / "sal")
    assert r.ok is False and compuso == []
    assert r.motivos == [f"informativa: sin pista de música CC0 disponible (GRANJA_MUSICA_DIR={tmp_path / 'vacia'})"]


def test_informativa_sin_variable_tambien_lo_dice(repo, tmp_path, monkeypatch):
    monkeypatch.delenv("GRANJA_MUSICA_DIR", raising=False)
    r = informativa.armar(repo, ENTRADA, AHORA, tmp_path / "sal")
    assert not r.ok and "sin pista de música CC0 disponible" in r.motivos[0]


@pytest.mark.slow
def test_informativa_armar_con_pista_sale_en_verde_con_musica_y_movimiento(banco, tmp_path, monkeypatch):
    repo, audio = banco
    monkeypatch.setenv("GRANJA_MUSICA_DIR", str(audio))
    r = informativa.armar(repo, ENTRADA, AHORA, tmp_path / "sal")
    assert r.ok, r.motivos
    assert r.plan["audio"]["tipo"] == "musica" and r.plan["movimiento"] == "entrada_texto"
    assert r.mp4.is_file() and all(x[1] for x in r.puntos)


# ---------------------------------------------------------------- muestras

def test_muestra_con_musica_se_omite_sin_pista_y_no_falla(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GRANJA_MUSICA_DIR", str(tmp_path / "vacia"))
    omitidas, hechas = [], []
    muestras._muestra_con_musica(tmp_path, Marca.cargar(repo), lambda *a: hechas.append(a),
                                 lambda mid, motivo: omitidas.append((mid, motivo)), None)
    assert hechas == [] and omitidas[0][0] == "informativa-con-musica" and "sin pista" in omitidas[0][1]


@pytest.mark.slow
def test_muestra_con_musica_renderiza_con_su_puerta_en_verde(banco, tmp_path, monkeypatch):
    repo, audio = banco
    monkeypatch.setenv("GRANJA_MUSICA_DIR", str(audio))
    hechas = []
    salida = tmp_path / "sal"
    salida.mkdir()
    muestras._muestra_con_musica(salida, Marca.cargar(repo), lambda *a: hechas.append(a),
                                 lambda *a: pytest.fail("no debía omitirse"), None)
    mid, mp4, _, _, puntos, _, datos, plan = hechas[0]
    assert mid == "informativa-con-musica" and mp4.is_file() and all(x[1] for x in puntos)
    assert plan["audio"]["tipo"] == "musica" and datos["pilar"] == "informativa"


# ---------------------------------------------------------------- validar

def errs(repo):
    return validar.validar(repo).errores


def test_validar_acepta_pistas_verificadas_buenas(banco):
    repo, _ = banco
    assert errs(repo) == []


def test_validar_rechaza_licencia_no_cc0(repo):
    escribir_pistas(repo, [pista("ajena", "a" * 64, estado="candidata", licencia="CC-BY")])
    assert any("licencia 'CC-BY' fuera de licencias_permitidas" in e for e in errs(repo))


def test_validar_rechaza_licencias_permitidas_distintas_de_cc0(repo):
    ruta = repo / "marca/musica/pistas.yml"
    ruta.write_text(ruta.read_text(encoding="utf-8").replace("licencias_permitidas: [CC0]",
                                                            "licencias_permitidas: [CC0, CC-BY]"), encoding="utf-8")
    assert any("sólo contenga CC0" in e for e in errs(repo))


def test_validar_rechaza_verificada_sin_sha(repo):
    escribir_pistas(repo, [pista("sin-sha", None)])
    assert any("sha256 hexadecimal de 64" in e for e in errs(repo))


def test_validar_rechaza_licencia_md_sin_campos(repo):
    escribir_pistas(repo, [pista("a-medias", "a" * 64)])
    (repo / "marca/musica/a-medias/LICENCIA.md").write_text("- Fuente: x\n- Autor:\n- Licencia: CC0\n", encoding="utf-8")
    es = errs(repo)
    assert any("a-medias" in e and "Autor:" in e and "Enlace:" in e and "Fuente:" not in e for e in es)


def test_validar_rechaza_verificada_sin_licencia_md(repo):
    escribir_pistas(repo, [pista("sin-md", "a" * 64)], con_licencia=False)
    assert any("exige marca/musica/sin-md/LICENCIA.md" in e for e in errs(repo))


@pytest.mark.parametrize("cambio,msg", [
    (dict(id="Mala_Id"), "id inválido"),
    (dict(pagina="http://x.com"), "pagina debe ser un enlace https"),
    (dict(asset="a/b.ogg"), "asset debe ser un nombre"),
])
def test_validar_rechaza_id_pagina_y_asset_malos(repo, cambio, msg):
    escribir_pistas(repo, [{**pista("buena", None, estado="candidata"), **cambio}], con_licencia=False)
    assert any(msg in e for e in errs(repo))


def test_validar_exige_manifiesto_y_rangos_si_la_musica_esta_activa(repo, editar_yaml):
    def quitar(d):
        d["musica"].pop("manifiesto")
    editar_yaml(repo, "marca.yml", quitar)
    assert any("exige musica.manifiesto" in e for e in errs(repo))


@pytest.mark.parametrize("clave,valor,msg", [("lufs", -10, "musica.lufs"), ("lufs", -25, "musica.lufs"),
                                             ("pico_db", -0.5, "musica.pico_db")])
def test_validar_rangos_de_volumen(repo, editar_yaml, clave, valor, msg):
    editar_yaml(repo, "marca.yml", lambda d: d["musica"].update({clave: valor}))
    assert any(msg in e for e in errs(repo))


@pytest.mark.parametrize("ext", [".ogg", ".flac", ".opus", ".aac"])
def test_validar_rechaza_audio_en_el_repo(repo, ext):
    (repo / f"cancion{ext}").write_bytes(b"x")
    assert any("audio no permitido" in e for e in errs(repo))


def test_gitignore_cubre_los_audios():
    lineas = (RAIZ_REAL / ".gitignore").read_text(encoding="utf-8").split()
    for ext in ("*.ogg", "*.flac", "*.opus", "*.aac", "*.mp3"):
        assert ext in lineas

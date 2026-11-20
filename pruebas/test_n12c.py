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


# ---------------------------------------------------------------- video: A1, A2, A4, M1
import json
import shutil
import subprocess

from fabrica.editar import componer, estilo, lienzo

hay_ffmpeg = shutil.which("ffmpeg") is not None
necesita_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")


@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


def fuente_sintetica(ruta, ancho, alto, seg, patron="testsrc"):
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"{patron}=size={ancho}x{alto}:rate=30:duration={seg}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seg}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(ruta)], check=True)
    return ruta


TOMA = dict(numero=7, gancho="Ella busca a un dragón", palabra_lapiz="DRAGÓN", lapiz="círculo",
            escena="INT. CABAÑA — NOCHE", credito="Con: Blender Foundation (CC BY 3.0)")
LINEAS = [componer.Linea(0.5, 2.5, "CHAMÁN", "¿Qué te trae por aquí?"),
          componer.Linea(2.6, 3.4, None, None, "(silencio)")]


@pytest.fixture(scope="module")
def vertical(tmp_path_factory, marca):
    """Fuente 9:16 de 4 s compuesta con recorte de 0 a 4 s (la toma dura 4 s, cierre incluido)."""
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    base = tmp_path_factory.mktemp("vert")
    fuente = fuente_sintetica(base / "v.mp4", 1080, 1920, 4)
    mp4 = base / "t.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=base / "w",
                             fuente_video=fuente, inicio=0.0)
    return {"mp4": mp4, "plan": plan, "fuente": fuente, "base": base}


def malos(puntos):
    """Puntos en rojo, sin el de duración (las fuentes sintéticas de prueba duran 4 s, menos de 7)."""
    return [n for n, ok, _ in puntos if not ok and "Duración de 7 a 30" not in n]


@necesita_ffmpeg
def test_fuente_9_16_cubre_el_cuadro_completo(vertical, marca):
    plan = vertical["plan"]
    assert plan["plantilla"] == "v2-pantalla-completa"
    assert plan["geometria"]["cuerpo"] == {"x": 0, "y": 0, "ancho": 1080, "alto": 1920}
    r = plan["geometria"]["recorte"]
    assert (r["x"], r["y"], r["ancho"], r["alto"], r["modo"]) == (0, 0, 1080, 1920, "centro") and r["escala"] == 1.0
    info = estilo.sondear(vertical["mp4"])
    assert (info["video"]["width"], info["video"]["height"]) == (1080, 1920)
    assert abs(info["duracion"] - 4.0) <= 0.1
    ok, det = estilo.cierre_visible(vertical["mp4"], plan, marca)
    assert ok, det
    assert plan["origen"]["sha256"] == componer.sha256_archivo(vertical["fuente"])


@necesita_ffmpeg
def test_fuente_16_9_cubre_el_cuadro_con_recorte_fijo(tmp_path, marca):
    fuente = fuente_sintetica(tmp_path / "ancha.mp4", 640, 360, 4)
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=tmp_path / "w",
                             fuente_video=fuente, inicio=0.0)
    info = estilo.sondear(mp4)
    assert (info["video"]["width"], info["video"]["height"]) == (1080, 1920)
    assert plan["geometria"]["cuerpo"] == {"x": 0, "y": 0, "ancho": 1080, "alto": 1920}
    r = plan["geometria"]["recorte"]
    assert r["escala"] >= max(1080 / 640, 1920 / 360) - 1e-3             # cubre, no encaja
    assert r["alto"] == 360 and abs(r["ancho"] * r["escala"] - 1080) <= 2 * r["escala"]
    assert 0 <= r["x"] and r["x"] + r["ancho"] <= 640 and r["x"] % 2 == 0 and r["ancho"] % 2 == 0
    assert estilo.pantalla_completa(plan, marca)[0]


def test_fuente_corta_da_error_antes_de_ffmpeg(tmp_path, marca, monkeypatch):
    monkeypatch.setattr(componer, "sondear_video",
                        lambda r: {"ancho": 1080, "alto": 1920, "audio": True, "duracion": 2.0})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no debe llamar a ffmpeg"))
    with pytest.raises(ValueError, match="terminaría antes del cierre"):
        componer.componer(marca, lienzo.Toma(**TOMA), [], tmp_path / "x.mp4", duracion=5.0,
                          trabajo=tmp_path / "w", fuente_video=tmp_path / "f.mp4", inicio=0.0)


@necesita_ffmpeg
def test_puerta_en_verde_con_video_de_fuente(vertical, marca):
    puntos = estilo.revisar(vertical["mp4"], vertical["plan"], marca)
    assert malos(puntos) == [], [p for p in puntos if not p[1]]
    assert all(n.startswith(("[fotograma] ", "[plan] ")) for n, _, _ in puntos)
    assert any("sha256 del MP4" in n and len(d) == 64 for n, _, d in puntos)
    for n in ("Cabecera en el video", "Diálogo en el video", "Cuerpo coincide con el origen", "Mosca en el video",
              "Bordes coinciden con la fuente (sin franjas)", "Audio original del clip"):
        assert any(x == f"[fotograma] {n}" for x, _, _ in puntos)
    assert any(x == "[plan] Textos caben en sus márgenes" for x, _, _ in puntos)
    for n in ("Pantalla completa (cuerpo 1080x1920, sin franjas)", "Capas fuera de la zona de acción"):
        assert any(x == f"[plan] {n}" for x, _, _ in puntos)


@pytest.mark.slow
@necesita_ffmpeg
def test_cabecera_con_otro_gancho_sale_en_rojo(vertical, marca):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["toma"]["gancho"] = "Él busca a un lobo"
    plan["toma"]["palabra_lapiz"] = "LOBO"
    assert "[fotograma] Cabecera en el video" in malos(estilo.revisar(vertical["mp4"], plan, marca))


@pytest.mark.slow
@necesita_ffmpeg
def test_cuerpo_de_otra_fuente_con_cierre_valido_sale_en_rojo(vertical, marca):
    otra = fuente_sintetica(vertical["base"] / "otra.mp4", 1080, 1920, 4, patron="smptebars")
    mp4 = vertical["base"] / "otra-t.mp4"
    componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=vertical["base"] / "w2",
                      fuente_video=otra, inicio=0.0)
    plan = json.loads(json.dumps(vertical["plan"]))        # el plan dice que el origen es testsrc
    m = malos(estilo.revisar(mp4, plan, marca))
    assert "[fotograma] Cuerpo coincide con el origen" in m
    # lo demás (cabecera, diálogo, cierre, audio) sigue en verde: sólo la imagen de fondo es de otra fuente
    assert set(m) <= {"[fotograma] Cuerpo coincide con el origen", "[fotograma] Bordes coinciden con la fuente (sin franjas)"}, m


@necesita_ffmpeg
def test_origen_que_falta_o_cambia_no_se_salta(vertical, marca, tmp_path):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["origen"]["archivo"] = str(tmp_path / "no-existe.mp4")
    ok, det = estilo.cuerpo_coincide(vertical["mp4"], plan, marca)
    assert not ok and "no está" in det
    plan["origen"]["archivo"] = str(vertical["fuente"])
    plan["origen"]["sha256"] = "0" * 64
    assert not estilo.cuerpo_coincide(vertical["mp4"], plan, marca)[0]


@pytest.mark.slow
@necesita_ffmpeg
def test_dialogo_distinto_sale_en_rojo(vertical, marca):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["lineas"][0]["frase"] = "Una frase que no es la del video."
    assert "[fotograma] Diálogo en el video" in malos(estilo.revisar(vertical["mp4"], plan, marca))


@pytest.mark.slow
def test_la_fuente_del_plan_debe_coincidir_con_marca(marca, vertical):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["fuente"]["archivos"]["bold"] = "marca/fuentes/otra.ttf"
    assert "[plan] Fuente igual a marca.yml (familia y archivos presentes)" in malos(
        estilo.revisar(vertical["mp4"], plan, marca))


@pytest.mark.slow
@necesita_ffmpeg
def test_cierre_correcto_pasa_corte_sin_credito_falla_y_credito_distinto_falla(vertical, marca, tmp_path):
    plan = vertical["plan"]
    assert estilo.cierre_visible(vertical["mp4"], plan, marca)[0]
    # CORTE. sin crédito: se compone el mismo video con crédito vacío
    sin = tmp_path / "sin-credito.mp4"
    componer.componer(marca, lienzo.Toma(**{**TOMA, "credito": ""}), LINEAS, sin, duracion=4.0,
                      trabajo=tmp_path / "w", fuente_video=vertical["fuente"], inicio=0.0)
    ok, det = estilo.cierre_visible(sin, plan, marca)        # la placa del cierre ya no es la del plan
    assert not ok and "falla: " in det
    # crédito distinto
    otro = dict(plan, toma=dict(plan["toma"], credito="Con: @otro_creador_distinto"))
    ok, det = estilo.cierre_visible(vertical["mp4"], otro, marca)
    assert not ok and "crédito" in det.split("falla:")[1]


@necesita_ffmpeg
def test_sin_corte_falla(marca, tmp_path):
    sin = fuente_sintetica(tmp_path / "s.mp4", 1080, 1920, 8)
    plan = {"toma": TOMA, "duracion": 8.0}
    ok, det = estilo.cierre_visible(sin, plan, marca)
    assert not ok and "CORTE." in det.split("falla:")[1]


# ---------------------------------------------------------------- M1

def test_palabra_sola_que_no_cabe_da_error_claro(marca):
    f = marca.fuente("bold", marca.m("tam_gancho"))
    with pytest.raises(ValueError, match="no cabe"):
        lienzo.partir("ANTICONSTITUCIONALISMO" * 3, f, 400)
    assert lienzo.partir("UNO DOS", f, 2000) == ["UNO DOS"]


def test_la_puerta_avisa_si_un_texto_no_cabe(marca):
    plan = {"toma": dict(TOMA, escena="INT. " + "X" * 80), "lineas": [{"t0": 0, "t1": 1, "nombre": "A", "frase": "B" * 60}]}
    ok, det = estilo.textos_caben(plan, marca)
    assert not ok and "escena" in det and "frase 1" in det
    assert estilo.textos_caben({"toma": TOMA, "lineas": []}, marca)[0]


# ---------------------------------------------------------------- M2: el validador no lanza TypeError
from fabrica import validar


def _hay(repo, fragmento):
    return any(fragmento in e for e in validar.validar(repo).errores)


def _tocar_ficha(repo, editar_yaml, **cambios):
    def f(datos):
        datos[0].update(cambios)
        return datos
    editar_yaml(repo, "fuentes.yml", f)


def test_validar_tipo_como_lista_da_error_claro(repo, editar_yaml):
    _tocar_ficha(repo, editar_yaml, tipo=["A", "B"])
    assert _hay(repo, "tipo debe ser A, B, C o D")


def test_validar_ventana_con_zonas_mezcladas_se_normaliza(repo, editar_yaml):
    def f(t):
        t["ventana_estreno"] = {"inicio": "2026-11-18T05:00:00", "fin": "2026-11-20T23:59:00-06:00"}
        return t
    editar_yaml(repo, "temporada.yml", f)
    assert not any("ventana_estreno" in e for e in validar.validar(repo).errores)

    def g(t):
        t["ventana_estreno"] = {"inicio": "2026-11-20T23:59:00-06:00", "fin": "2026-11-18T05:00:00"}
        return t
    editar_yaml(repo, "temporada.yml", g)
    assert _hay(repo, "inicio debe ser anterior a fin")


def test_validar_vence_entre_comillas_se_lee_y_avisa_si_pasó(repo, editar_yaml):
    _tocar_ficha(repo, editar_yaml, vence="2020-01-01")
    inf = validar.validar(repo)
    assert any("vence 2020-01-01 ya pasado" in a for a in inf.avisos), inf.avisos
    assert not any("vence" in e for e in inf.errores)


def test_validar_vence_ilegible_da_error(repo, editar_yaml):
    _tocar_ficha(repo, editar_yaml, vence="pronto")
    assert _hay(repo, "vence ilegible")


def test_validar_grano_vacio_da_error_de_claves(repo, editar_yaml):
    def f(m):
        m["maqueta"]["grano"] = {}
        return m
    editar_yaml(repo, "marca.yml", f)
    assert _hay(repo, "maqueta.grano: falta la clave 'papel'")


# ---------------------------------------------------------------- M3: el registro con dos corridas
import multiprocessing

from fabrica import registro


def _avanzar_diez(raiz, cola):
    cola.put([registro.avanzar_toma(raiz) for _ in range(10)])


def test_dos_procesos_avanzan_toma_sin_repetir_numeros(repo):
    cola = multiprocessing.Queue()
    ps = [multiprocessing.Process(target=_avanzar_diez, args=(repo, cola)) for _ in range(2)]
    for p in ps:
        p.start()
    numeros = cola.get(timeout=60) + cola.get(timeout=60)
    for p in ps:
        p.join(30)
    assert len(numeros) == 20 and len(set(numeros)) == 20
    assert registro.contador_toma(repo) == max(numeros)


def test_anotar_registro_anexa_sin_reescribir(repo):
    (repo / "registro.md").write_text("# Registro\n- vieja\n", encoding="utf-8")
    registro.anotar_registro(repo, "nueva")
    texto = (repo / "registro.md").read_text(encoding="utf-8")
    assert texto.startswith("# Registro\n- vieja\n") and texto.rstrip().endswith("nueva")


# ---------------------------------------------------------------- ronda 2
import importlib
import sys


def test_registro_importa_sin_fcntl(monkeypatch):
    """En Windows no existe fcntl: el módulo debe cargar igual (el candado se importa al usarlo)."""
    monkeypatch.setitem(sys.modules, "fcntl", None)       # import fcntl -> ImportError
    mod = importlib.reload(registro)
    assert hasattr(mod, "avanzar_toma") and hasattr(mod, "_bloquear")
    monkeypatch.undo()
    importlib.reload(registro)


@necesita_ffmpeg
def test_plan_con_cuerpo_sin_origen_sale_en_rojo(vertical, marca):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["origen"] = None
    ok, det = estilo.cuerpo_coincide(vertical["mp4"], plan, marca)
    assert not ok and "origen" in det
    for k in ("archivo", "sha256", "inicio", "duracion"):
        p2 = json.loads(json.dumps(vertical["plan"]))
        del p2["origen"][k]
        assert not estilo.cuerpo_coincide(vertical["mp4"], p2, marca)[0], k
    p3 = json.loads(json.dumps(vertical["plan"]))
    p3["origen"]["sha256"] = "0" * 64
    assert not estilo.cuerpo_coincide(vertical["mp4"], p3, marca)[0]
    # sin cuerpo y sin origen (tipográfica) sí es "no aplica"
    p4 = {"duracion": 4.0, "geometria": {"cuerpo": None, "y_dialogo": 790}, "origen": None}
    assert estilo.cuerpo_coincide(vertical["mp4"], p4, marca)[0]


@pytest.mark.slow
@necesita_ffmpeg
def test_mp4_alargado_despues_del_cierre_sale_en_rojo(vertical, marca, tmp_path):
    largo = tmp_path / "largo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(vertical["mp4"]),
                    "-vf", "tpad=stop_mode=clone:stop_duration=3", "-af", "apad=pad_dur=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
                    str(largo)], check=True)
    assert estilo.sondear(largo)["duracion"] > 6.5
    ok, det = estilo.cierre_visible(largo, vertical["plan"], marca)
    assert not ok and "plan" in det
    puntos = estilo.revisar(largo, vertical["plan"], marca)
    assert "[fotograma] Duración igual a la del plan" in malos(puntos)
    assert "[fotograma] Duración igual a la del plan" not in malos(estilo.revisar(vertical["mp4"], vertical["plan"], marca))


@necesita_ffmpeg
def test_fuente_con_video_corto_y_audio_largo_no_acepta_recorte_largo(tmp_path, marca):
    f = tmp_path / "va.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(f)], check=True)
    info = componer.sondear_video(f)
    assert 4.9 <= info["duracion"] <= 5.2, info
    with pytest.raises(ValueError, match="terminaría antes del cierre"):
        componer.componer(marca, lienzo.Toma(**TOMA), [], tmp_path / "x.mp4", duracion=8.0,
                          trabajo=tmp_path / "w", fuente_video=f, inicio=0.0)
    assert not (tmp_path / "x.mp4").exists()


def test_dif_tinta_rechaza_cajas_vacias_o_fuera_del_cuadro(marca):
    from PIL import Image
    papel = lienzo.papel(marca).convert("RGB")
    for caja in [(0, 100, 0, 200), (0, 200, 1080, 200), (0, 1900, 1080, 2100), (-5, 0, 100, 100), (0, 0, 2000, 100)]:
        with pytest.raises(ValueError, match="vacía o fuera"):
            estilo.discrepancia_local(papel, papel, papel, caja)
    with pytest.raises(ValueError):
        estilo.discrepancia_local(Image.new("RGB", (10, 10)), papel, papel, (0, 0, 10, 10))


@necesita_ffmpeg
def test_dialogo_con_capa_fuera_del_cuadro_sale_en_rojo(vertical, marca):
    plan = json.loads(json.dumps(vertical["plan"]))
    capa = next(c for c in plan["geometria"]["capas"] if c["tipo"] == "dialogo_1")
    capa["y"] = 5000
    ok, det = estilo.capas_fuera_de_la_accion(plan, marca)
    assert not ok and "zona segura" in det
    assert not estilo.capas_coinciden(plan, marca)[0]


def test_tinta_extra_en_el_fotograma_se_detecta(marca):
    papel = lienzo.papel(marca).convert("RGB")
    real = papel.copy()
    real.paste((0, 0, 0), (100, 940, 400, 980))          # texto añadido que el plan no tiene
    d = estilo.discrepancia_local(real, papel, papel, (90, 930, 990, 1014))
    assert d > estilo.K_BLOQUE


@pytest.mark.slow
@necesita_ffmpeg
def test_credito_con_texto_extra_sale_en_rojo(vertical, marca, tmp_path):
    mp4 = tmp_path / "extra.mp4"
    componer.componer(marca, lienzo.Toma(**{**TOMA, "credito": "Con: Blender Foundation (CC BY 3.0)"}), LINEAS,
                      mp4, duracion=4.0, trabajo=tmp_path / "w", fuente_video=vertical["fuente"], inicio=0.0)
    corto = json.loads(json.dumps(vertical["plan"]))
    corto["toma"]["credito"] = "Con: Blender"
    ok, det = estilo.cierre_visible(mp4, corto, marca)
    assert not ok and "crédito" in det.split("falla:")[1]


# ---------------------------------------------------------------- una sola letra (ronda 2, punto 8)

def _con_cambio(vertical, marca, tmp_path, toma=None, frase=None):
    t = {**TOMA, **(toma or {})}
    lineas = [componer.Linea(0.5, 2.5, "CHAMÁN", frase or "¿Qué te trae por aquí?"), LINEAS[1]]
    mp4 = tmp_path / "var.mp4"
    componer.componer(marca, lienzo.Toma(**t), lineas, mp4, duracion=4.0, trabajo=tmp_path / "w",
                      fuente_video=vertical["fuente"], inicio=0.0)
    return mp4


@necesita_ffmpeg
@pytest.mark.parametrize("cambio,punto", [
    ({"toma": {"credito": "Con: Blender Foundation (CC BY 4.0)"}}, "[fotograma] Cierre visible en el video (CORTE. y crédito)"),
    ({"toma": {"escena": "INT. CABAÑA — NOCHF"}}, "[fotograma] Cabecera en el video"),
    ({"toma": {"gancho": "Ella buscs a un dragón"}}, "[fotograma] Cabecera en el video"),
    ({"frase": "¿Qué te trae por aquí!"}, "[fotograma] Diálogo en el video"),
])
@pytest.mark.slow
def test_un_cambio_de_una_letra_sale_en_rojo(vertical, marca, tmp_path, cambio, punto):
    mp4 = _con_cambio(vertical, marca, tmp_path, **cambio)
    assert punto in malos(estilo.revisar(mp4, vertical["plan"], marca))


@pytest.mark.slow
@necesita_ffmpeg
def test_el_video_correcto_no_da_discrepancias(vertical, marca):
    puntos = estilo.revisar(vertical["mp4"], vertical["plan"], marca)
    assert malos(puntos) == []

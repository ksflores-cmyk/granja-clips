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
def test_fuente_9_16_cabe_y_el_dialogo_queda_dentro_del_cuadro(vertical, marca):
    plan = vertical["plan"]
    c = plan["geometria"]["cuerpo"]
    assert c["alto"] <= componer.alto_caja_cuerpo(marca, plan["geometria"]["y_contenido"])
    assert c["ancho"] < marca.ancho and abs(c["ancho"] / c["alto"] - 9 / 16) < 0.01
    assert c["x"] == (marca.ancho - c["ancho"]) // 2
    assert plan["geometria"]["y_dialogo"] + componer.alto_zona_dialogo(marca) <= marca.alto
    info = estilo.sondear(vertical["mp4"])
    assert (info["video"]["width"], info["video"]["height"]) == (1080, 1920)
    assert abs(info["duracion"] - 4.0) <= 0.1
    ok, det = estilo.cierre_visible(vertical["mp4"], plan, marca)
    assert ok, det
    assert plan["origen"]["sha256"] == componer.sha256_archivo(vertical["fuente"])


def test_fuente_16_9_conserva_el_ancho_completo(marca):
    c = componer.ajustar_cuerpo(marca, 530, 640, 360)
    assert (c["x"], c["ancho"], c["alto"]) == (0, 1080, 608)


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
    for n in ("Cabecera en el video", "Diálogo en el video", "Cuerpo coincide con el origen"):
        assert any(x == f"[fotograma] {n}" for x, _, _ in puntos)
    assert any(x == "[plan] Textos caben en sus márgenes" for x, _, _ in puntos)


@necesita_ffmpeg
def test_cabecera_con_otro_gancho_sale_en_rojo(vertical, marca):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["toma"]["gancho"] = "Él busca a un lobo"
    plan["toma"]["palabra_lapiz"] = "LOBO"
    assert "[fotograma] Cabecera en el video" in malos(estilo.revisar(vertical["mp4"], plan, marca))


@necesita_ffmpeg
def test_cuerpo_de_otra_fuente_con_cierre_valido_sale_en_rojo(vertical, marca):
    otra = fuente_sintetica(vertical["base"] / "otra.mp4", 1080, 1920, 4, patron="smptebars")
    mp4 = vertical["base"] / "otra-t.mp4"
    componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=4.0, trabajo=vertical["base"] / "w2",
                      fuente_video=otra, inicio=0.0)
    plan = json.loads(json.dumps(vertical["plan"]))        # el plan dice que el origen es testsrc
    m = malos(estilo.revisar(mp4, plan, marca))
    assert m == ["[fotograma] Cuerpo coincide con el origen"], m


@necesita_ffmpeg
def test_origen_que_falta_o_cambia_no_se_salta(vertical, marca, tmp_path):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["origen"]["archivo"] = str(tmp_path / "no-existe.mp4")
    ok, det = estilo.cuerpo_coincide(vertical["mp4"], plan, marca)
    assert not ok and "no está" in det
    plan["origen"]["archivo"] = str(vertical["fuente"])
    plan["origen"]["sha256"] = "0" * 64
    assert not estilo.cuerpo_coincide(vertical["mp4"], plan, marca)[0]


@necesita_ffmpeg
def test_dialogo_distinto_sale_en_rojo(vertical, marca):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["lineas"][0]["frase"] = "Una frase que no es la del video."
    assert "[fotograma] Diálogo en el video" in malos(estilo.revisar(vertical["mp4"], plan, marca))


def test_la_fuente_del_plan_debe_coincidir_con_marca(marca, vertical):
    plan = json.loads(json.dumps(vertical["plan"]))
    plan["fuente"]["archivos"]["bold"] = "marca/fuentes/otra.ttf"
    assert "[plan] Fuente igual a marca.yml (familia y archivos presentes)" in malos(
        estilo.revisar(vertical["mp4"], plan, marca))


@necesita_ffmpeg
def test_cierre_correcto_pasa_corte_sin_credito_falla_y_credito_distinto_falla(vertical, marca, tmp_path):
    plan = vertical["plan"]
    assert estilo.cierre_visible(vertical["mp4"], plan, marca)[0]
    # CORTE. sin crédito: se compone el mismo video con crédito vacío
    sin = tmp_path / "sin-credito.mp4"
    componer.componer(marca, lienzo.Toma(**{**TOMA, "credito": ""}), LINEAS, sin, duracion=4.0,
                      trabajo=tmp_path / "w", fuente_video=vertical["fuente"], inicio=0.0)
    ok, det = estilo.cierre_visible(sin, plan, marca)
    assert not ok and "falla: crédito" in det and "CORTE." not in det.split("falla:")[1]
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

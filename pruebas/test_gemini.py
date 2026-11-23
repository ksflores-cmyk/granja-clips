"""Tarea #2 «gemini»: lista de modelos desde la config, ListModels, tiempo por intento y corte sin IA.

Todo sin red: transportes falsos. Las pruebas con ffmpeg real van marcadas `slow`.
"""
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from fabrica import ojos
from fabrica.editar import sin_ia, toma
from fabrica.editar.marca import Marca
from test_n4 import (AHORA, RAIZ_REAL, Transporte, abrir_con, candidato, ficha_propia, mirar, respuesta_grabada,
                     sobre, video20)  # noqa: F401 (video20 es un fixture de sesión)

CFG_REAL = yaml.safe_load((RAIZ_REAL / "config" / "ojos.yml").read_text(encoding="utf-8"))
OK = (200, sobre(respuesta_grabada("valida")))


@pytest.fixture(autouse=True)
def cache_limpia(monkeypatch):
    ojos.vaciar_cache_modelos()
    sin_ia.reiniciar_contador()

    def sin_red(*a, **k):
        raise AssertionError("se intentó ListModels por la red real")
    monkeypatch.setattr(ojos, "transporte_get_urllib", sin_red)
    yield
    ojos.vaciar_cache_modelos()


def cfg(**cambios):
    return {**CFG_REAL, "listar_modelos": False, **cambios}


# Un 503 sostenido recorre el principal y todos los respaldos permitidos, con 2 intentos por modelo.
N503 = 2 * (1 + min(CFG_REAL["respaldos_por_falla"], len(CFG_REAL["modelos_respaldo"])))


def lista(*nombres, metodos=("generateContent",)):
    return json.dumps({"models": [{"name": f"models/{n}", "supportedGenerationMethods": list(metodos)}
                                  for n in nombres]})


class Lista:
    """Transporte GET falso: respuestas en orden."""

    def __init__(self, *respuestas):
        self.cola, self.llamadas = list(respuestas), []

    def __call__(self, url, encabezados, tiempo):
        self.llamadas.append((url, encabezados, tiempo))
        r = self.cola.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# ---------------------------------------------------------------- 1. config

def test_la_config_real_pone_primero_el_modelo_que_respondio_en_4_s():
    assert CFG_REAL["modelo"] == "gemini-3-flash-preview"
    assert CFG_REAL["modelos_respaldo"] == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash"]
    assert (CFG_REAL["tiempo_por_intento_s"], CFG_REAL["tope_ojos_s"], CFG_REAL["respaldos_por_falla"]) == (90, 300, 4)
    assert CFG_REAL["listar_modelos"] is True and CFG_REAL["sin_ia"] == {"activo": True, "max_por_ciclo": 1, "duracion_objetivo_s": 20}


def test_el_orden_de_los_modelos_sale_de_la_config(tmp_path):
    t = Transporte((404, "{}"), (404, "{}"), OK)
    m = mirar(tmp_path, t, config=cfg(modelo="uno", modelos_respaldo=["dos", "tres"]))
    assert t.modelos() == ["uno", "dos", "tres"] and m.modelo == "tres"


def test_validar_acepta_las_claves_nuevas_y_rechaza_las_malas(repo):
    from fabrica import validar
    ruta = repo / "config" / "ojos.yml"
    assert validar.validar(repo).errores == []
    malo = {**CFG_REAL, "respaldos_por_falla": -1, "listar_modelos": "si", "sin_ia": {"activo": "x"}, "tope_ojos_s": 0}
    ruta.write_text(yaml.safe_dump(malo, allow_unicode=True), encoding="utf-8")
    texto = "\n".join(validar.validar(repo).errores)
    for clave in ("respaldos_por_falla", "listar_modelos", "sin_ia", "tope_ojos_s"):
        assert clave in texto


# ---------------------------------------------------------------- 2. ListModels

def test_modelos_disponibles_quita_el_prefijo_filtra_por_generate_content_y_sigue_la_paginacion():
    g = Lista((200, json.dumps({"models": [
                  {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
                  {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]}],
                  "nextPageToken": "SIG 2"})),
              (200, lista("gemini-b")))
    assert ojos.modelos_disponibles("clave-xyz", g, tiempo=7) == {"gemini-a", "gemini-b"}
    assert len(g.llamadas) == 2
    assert g.llamadas[0][0] == "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
    assert g.llamadas[1][0].endswith("&pageToken=SIG%202")
    assert all(e == {"x-goog-api-key": "clave-xyz"} and "clave" not in u for u, e, _ in g.llamadas)
    assert g.llamadas[0][2] == 7


@pytest.mark.parametrize("respuesta", [(500, "x"), (200, "no es json"), (200, "[1]"), (200, '{"models": 3}')])
def test_modelos_disponibles_levanta_ErrorOjos_si_la_respuesta_es_rara(respuesta):
    with pytest.raises(ojos.ErrorOjos):
        ojos.modelos_disponibles("k", Lista(respuesta))


def test_listmodels_salta_los_que_no_aparecen_y_lo_anota(tmp_path):
    t = Transporte(OK)
    m = mirar(tmp_path, t, config=cfg(listar_modelos=True, modelo="a", modelos_respaldo=["b", "c"]),
              transporte_lista=Lista((200, lista("b", "c", "otro"))))
    assert t.modelos() == ["b"] and m.modelo == "b"
    assert "a: no aparece en ListModels, se salta" in m.intentos


@pytest.mark.parametrize("respuesta", [ojos.ErrorOjos("fallo de conexión con ListModels: TimeoutError"), (503, "x"),
                                       (200, "basura")])
def test_si_listmodels_falla_se_usa_la_lista_completa_sin_bloquear(tmp_path, respuesta):
    t = Transporte(OK)
    m = mirar(tmp_path, t, config=cfg(listar_modelos=True, modelo="a", modelos_respaldo=["b"]),
              transporte_lista=Lista(respuesta))
    assert m.datos is not None and t.modelos() == ["a"]
    assert any(i.startswith("ListModels:") and "lista completa" in i for i in m.intentos)


def test_si_el_filtro_vacia_la_lista_se_usa_la_completa(tmp_path):
    t = Transporte(OK)
    m = mirar(tmp_path, t, config=cfg(listar_modelos=True, modelo="a", modelos_respaldo=["b"]),
              transporte_lista=Lista((200, lista("zzz"))))
    assert t.modelos() == ["a"] and m.datos is not None
    assert any("ningún modelo de la lista aparece" in i for i in m.intentos)


def test_con_transporte_inyectado_y_sin_transporte_lista_no_se_llama_a_listmodels(tmp_path):
    t = Transporte(OK)
    m = mirar(tmp_path, t, config=cfg(listar_modelos=True))     # `transporte_get_urllib` revienta si se usa
    assert m.datos is not None and not any("ListModels" in i for i in m.intentos)


def test_listmodels_se_consulta_una_sola_vez_por_proceso(tmp_path):
    g = Lista((200, lista(CFG_REAL["modelo"])))
    for _ in range(2):
        mirar(tmp_path, Transporte(OK), config=cfg(listar_modelos=True), transporte_lista=g)
    assert len(g.llamadas) == 1
    ojos.vaciar_cache_modelos()
    g2 = Lista((200, lista(CFG_REAL["modelo"])))
    mirar(tmp_path, Transporte(OK), config=cfg(listar_modelos=True), transporte_lista=g2)
    assert len(g2.llamadas) == 1


# ---------------------------------------------------------------- 3. tiempo por intento y respaldos por falla

class ConTiempo:
    """Transporte de 4 argumentos: apunta el tiempo que recibe cada intento."""

    def __init__(self, *respuestas):
        self.cola, self.tiempos, self.urls = list(respuestas), [], []

    def __call__(self, url, encabezados, cuerpo, tiempo=None):
        self.urls.append(url)
        self.tiempos.append(tiempo)
        r = self.cola.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_un_timeout_en_el_primer_modelo_prueba_el_siguiente_y_ningun_intento_pasa_de_tiempo_por_intento(tmp_path):
    lento = ojos.ErrorOjos("fallo de conexión con Gemini: TimeoutError")
    t = ConTiempo(lento, lento, OK)
    m = mirar(tmp_path, t, config=cfg(modelo="a", modelos_respaldo=["b"], tiempo_por_intento_s=25))
    assert m.modelo == "b" and m.datos is not None and not m.pasajero
    assert len(t.tiempos) == 3 and all(0 < x <= 25 for x in t.tiempos)


def test_el_tiempo_de_cada_intento_se_recorta_al_presupuesto_restante(tmp_path):
    t = ConTiempo(OK)
    mirar(tmp_path, t, config=cfg(tiempo_por_intento_s=90, tope_ojos_s=40))
    assert 30 < t.tiempos[0] <= 40


def test_sin_las_claves_nuevas_valen_los_valores_del_blindaje(tmp_path):
    viejo = {k: CFG_REAL[k] for k in ("modelo", "modelos_respaldo", "max_mb_en_linea", "temperatura")}
    t = ConTiempo(OK)
    mirar(tmp_path, t, config=viejo)
    assert t.tiempos[0] == ojos.TIEMPO_ESPERA_S
    caidas = [(503, "x")] * 4
    t2 = Transporte(*caidas)
    assert mirar(tmp_path, t2, config=viejo).pasajero and len(t2.llamadas) == 4      # 1 respaldo por falla


def test_respaldos_por_falla_sale_de_la_config(tmp_path):
    e = (503, "x")
    t = Transporte(*[e] * 6)
    m = mirar(tmp_path, t, config=cfg(respaldos_por_falla=2))
    assert m.pasajero and len(t.llamadas) == 6 and len(set(t.modelos())) == 3
    t0 = Transporte(e, e)
    m0 = mirar(tmp_path, t0, config=cfg(respaldos_por_falla=0))
    assert m0.pasajero and len(t0.llamadas) == 2 and len(set(t0.modelos())) == 1


def test_sin_gemini_se_distingue_de_un_veto(tmp_path):
    assert mirar(tmp_path, Transporte(), clave=None).sin_gemini
    assert mirar(tmp_path, Transporte(*[(503, "x")] * N503), config=cfg()).sin_gemini
    assert mirar(tmp_path, Transporte(*[(404, "{}")] * (N503 // 2)), config=cfg()).sin_gemini
    assert not mirar(tmp_path, Transporte((400, "mal")), config=cfg()).sin_gemini
    assert not mirar(tmp_path, Transporte(OK), config=cfg()).sin_gemini


# ---------------------------------------------------------------- 4. corte sin IA: puntaje (sin ffmpeg)

def test_elegir_inicio_prefiere_la_ventana_con_el_pico_de_volumen():
    rms = [10.0] * 60                      # 30 s de volumen parejo
    rms[40:46] = [900.0] * 6               # fuerte entre 20 y 23 s
    ini = sin_ia.elegir_inicio(rms, [], 30.0, 12.0)
    assert ini <= 20 - 1 and ini + 12 >= 23 and ini + 12 - 2 >= 20.5
    assert ini == sin_ia.elegir_inicio(rms, [], 30.0, 12.0)      # determinista


def test_el_inicio_se_ajusta_al_cambio_de_escena_mas_cercano_dentro_de_1_5_s():
    rms = [10.0] * 60
    rms[40:46] = [900.0] * 6
    assert sin_ia.elegir_inicio(rms, [], 30.0, 12.0) == 11.0                 # sin escenas: la más temprana que sirve
    assert sin_ia.elegir_inicio(rms, [12.0], 30.0, 12.0) == 12.0             # 1 s de distancia: se ajusta
    assert sin_ia.elegir_inicio(rms, [13.0], 30.0, 12.0) == 11.0             # 2 s: demasiado lejos
    assert sin_ia.elegir_inicio(rms, [3.0], 30.0, 12.0) == 11.0


def test_los_cambios_de_escena_suman_al_puntaje():
    rms = [10.0] * 40                       # sin audio que destaque
    assert sin_ia.elegir_inicio(rms, [12.0, 13.0, 14.0], 20.0, 8.0) == 6.5      # la más temprana que incluye las 3
    assert sin_ia.elegir_inicio(rms, [18.0, 18.5, 19.0], 20.0, 8.0) == 11.5


def test_el_pico_mas_alto_no_queda_en_el_primer_segundo_ni_en_los_ultimos_dos():
    rms = [10.0] * 60
    rms[20] = 1000.0                        # un único pico a los 10 s
    for _ in range(1):
        ini = sin_ia.elegir_inicio(rms, [], 30.0, 12.0)
        off = 10.0 - ini
        assert 1.0 <= off and off + 0.5 <= 12.0 - 2.0


def test_sin_audio_ni_escenas_da_el_primer_inicio_y_nunca_falla():
    assert sin_ia.elegir_inicio([], [], 25.0, 20.0) == 0.0
    assert sin_ia.elegir_inicio([5.0] * 50, [], 25.0, 20.0) == 0.0


def test_elegir_recorte_acota_el_largo_y_descarta_el_video_corto():
    analisis = lambda ruta: {"rms": [1.0] * 40, "escenas": []}
    assert sin_ia.elegir_recorte(Path("x"), (7.0, 30.0), 20, 20.0, analisis) == (0.0, 20.0)
    assert sin_ia.elegir_recorte(Path("x"), (7.0, 15.0), 20, 20.0, analisis) == (0.0, 15.0)       # tope del rango
    assert sin_ia.elegir_recorte(Path("x"), (7.0, 30.0), 20, 12.0, analisis) == (0.0, 12.0)       # tope de la duración
    assert sin_ia.elegir_recorte(Path("x"), (10.0, 30.0), 5, 20.0, analisis) == (0.0, 10.0)       # mínimo del rango
    assert sin_ia.elegir_recorte(Path("x"), (10.0, 30.0), 20, 9.0, analisis) is None


# ---------------------------------------------------------------- 4. corte sin IA: textos

@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


TEMPORADA = {"nombre": "GTA 6", "palabras_evitar": ["final", "traición", "muere"]}


def test_limpiar_titulo_quita_urls_hashtags_menciones_emojis_extension_y_guiones_bajos():
    t = "🔥 Clutch_increíble 😱 #gta6 @eric https://x.com/a?b=1 www.ejemplo.com/z jugada.mp4 ™ ★"
    assert sin_ia.limpiar_titulo(t) == "Clutch increíble jugada"
    assert sin_ia.limpiar_titulo("¡Qué locura, no puede ser!") == "¡Qué locura, no puede ser!"


def test_gancho_de_un_titulo_sano_y_palabra_lapiz_la_mas_larga(marca):
    g, motivo, _ = sin_ia.gancho_de("Clutch increíble en directo #gta6", None, marca, TEMPORADA)
    assert (g, motivo) == ("Clutch increíble en directo", None)
    assert sin_ia.palabra_lapiz_de("Clutch increíble en directo!") == "increíble"
    assert sin_ia.palabra_lapiz_de("¡Qué locura!") == "locura"


def test_el_gancho_se_recorta_al_maximo_de_palabras_de_la_marca(marca):
    g, _, _ = sin_ia.gancho_de("uno dos tres cuatro cinco seis siete ocho", None, marca, TEMPORADA)
    assert len(g.split()) == marca.max_palabras_gancho == 6


@pytest.mark.parametrize("titulo", ["Clutch", "clip 2024 jugada", "🔥🔥 #gta6", "https://x.com/a.mp4"])
def test_titulo_flojo_sin_respaldo_es_descarte_con_motivo_y_sin_gastar_la_fuente(marca, titulo):
    g, motivo, definitivo = sin_ia.gancho_de(titulo, None, marca, TEMPORADA)
    assert g is None and "gancho_sin_ia" in motivo and definitivo is False


def test_titulo_flojo_usa_el_gancho_de_respaldo_de_la_ficha(marca):
    ficha = ficha_propia(reglas={"duracion_s": [7, 30], "gancho_sin_ia": "Esto pasó en directo"})
    assert sin_ia.gancho_de("Clutch", ficha, marca, TEMPORADA)[0] == "Esto pasó en directo"


def test_gancho_prohibido_de_marca_cae_al_respaldo(marca, monkeypatch):
    monkeypatch.setattr(Marca, "ganchos_prohibidos", lambda self: ["no vas a creer"])
    assert sin_ia.gancho_de("No vas a creer esto", None, marca, TEMPORADA)[0] is None
    ficha = ficha_propia(reglas={"gancho_sin_ia": "Mira esta jugada"})
    assert sin_ia.gancho_de("No vas a creer esto", ficha, marca, TEMPORADA)[0] == "Mira esta jugada"


def test_palabra_a_evitar_en_el_titulo_es_descarte_directo_aunque_haya_respaldo(marca):
    ficha = ficha_propia(reglas={"gancho_sin_ia": "Mira esta jugada"})
    g, motivo, definitivo = sin_ia.gancho_de("El final de la historia", ficha, marca, TEMPORADA)
    assert g is None and "final" in motivo and definitivo is True


def test_un_gancho_que_no_cabe_en_dos_lineas_cae_al_respaldo(marca):
    largo = "Supercalifragilisticoespialidosamente extraordinariamente inconstitucionalmente"
    assert sin_ia.gancho_de(largo, None, marca, TEMPORADA)[0] is None


def test_escena_con_el_creador_en_mayusculas_sin_arroba(marca):
    assert sin_ia.escena_de("@eric_zzeta", marca) == "INT. STREAM DE ERIC ZZETA"
    assert sin_ia.escena_de(None, marca) == "INT. STREAM"


def test_escena_larga_cae_a_int_stream(marca):
    assert sin_ia.escena_de("@" + "W" * 60, marca) == "INT. STREAM"


def test_escena_con_palabra_a_evitar_cae_a_int_stream(marca):
    assert sin_ia.escena_de("@final", marca, TEMPORADA) == "INT. STREAM"


def test_construir_arma_un_dict_que_valida_con_la_misma_forma_que_gemini(marca):
    analisis = lambda ruta: {"rms": [1.0] * 40, "escenas": [8.0]}
    c = sin_ia.construir(Path("x"), candidato(titulo="Clutch increíble en directo", creador="@probador"), ficha_propia(),
                         marca, TEMPORADA, duracion=20.0, rango=(7.0, 30.0), analizar=analisis)
    assert c.datos is not None and c.motivo is None
    d = c.datos
    assert d["lineas"] == [] and d["acotacion"] == "" and d["escena"] == "INT. STREAM DE PROBADOR"
    assert d["apto"] is True and d["juego_correcto"] is True and not (d["spoiler"] or d["desnudez"] or d["cinematica"])
    assert ojos.validar_respuesta(d, marca, TEMPORADA, duracion=20.0, juego="GTA 6") == (True, [])
    corto = sin_ia.construir(Path("x"), candidato(titulo="Clutch increíble en directo"), ficha_propia(), marca, TEMPORADA,
                             duracion=5.0, rango=(7.0, 30.0), analizar=analisis)
    assert corto.datos is None and corto.motivo == "sin IA: el video dura menos que el mínimo" and corto.gastar


def test_construir_con_ffmpeg_roto_no_gasta_la_fuente(marca):
    def roto(ruta):
        raise subprocess.CalledProcessError(1, "ffmpeg")
    c = sin_ia.construir(Path("x"), candidato(titulo="Clutch increíble en directo"), ficha_propia(), marca, TEMPORADA,
                         duracion=20.0, rango=(7.0, 30.0), analizar=roto)
    assert c.datos is None and "falló el análisis" in c.motivo and c.gastar is False


# ---------------------------------------------------------------- 4. corte sin IA: en hacer_toma (componer simulado)

class ComponerFalso:
    """Reemplaza `componer`: apunta lo recibido y falla a propósito, para no renderizar."""

    def __init__(self):
        self.llamadas = []

    def __call__(self, marca, t, lineas, salida, **kw):
        self.llamadas.append((t, lineas, kw))
        raise ValueError("render omitido")


@pytest.fixture
def sin_render(monkeypatch):
    falso = ComponerFalso()
    monkeypatch.setattr(toma, "componer", falso)
    monkeypatch.setattr(sin_ia, "analizar_video", lambda ruta: {"rms": [1.0] * 40, "escenas": []})
    return falso


def hacer(repo, tmp_path, video, transporte, entorno, cand=None, ficha=None):
    return toma.hacer_toma(repo, cand or candidato(titulo="Clutch increíble en directo"), ficha or ficha_propia(), AHORA,
                           salida=tmp_path / "s", abrir=abrir_con(video), transporte=transporte, entorno=entorno,
                           espera=lambda s: None)


CLAVE = {"GEMINI_API_KEY": "clave-de-prueba"}


def caida():
    """Gemini caído en los 3 modelos (503 sostenido)."""
    return Transporte(*[(503, "x")] * N503)


@pytest.mark.parametrize("caso", ["pasajero", "404"])
def test_sin_ia_se_activa_si_gemini_no_esta_disponible(repo, tmp_path, video20, sin_render, caso):
    t, env = {"pasajero": (Transporte(*[(503, "x")] * N503), CLAVE),
              "404": (Transporte(*[(404, "{}")] * (N503 // 2)), CLAVE),
              "sin_clave": (caida(), CLAVE)}[caso]
    r = hacer(repo, tmp_path, video20, t, env)
    assert sin_render.llamadas, r.motivos                 # llegó a componer: no se descartó antes
    assert r.modelo == "sin-ia" and r.gemini["lineas"] == [] and r.gemini["gancho"] == "Clutch increíble en directo"
    assert r.motivos[0].startswith("editor: ValueError")
    toma_, lineas, kw = sin_render.llamadas[0]
    assert lineas == [] and toma_.escena == "INT. STREAM DE PROBADOR" and kw["inicio"] == 0.0


def test_sin_ia_no_se_activa_si_gemini_vetó(repo, tmp_path, video20, sin_render, monkeypatch):
    monkeypatch.setattr(sin_ia, "construir", lambda *a, **k: pytest.fail("no debe entrar sin IA"))
    r = hacer(repo, tmp_path, video20, Transporte((200, sobre(respuesta_grabada("spoiler")))), CLAVE)
    assert not r.ok and any("spoiler" in m for m in r.motivos) and r.modelo != "sin-ia" and r.gastar is True
    r = hacer(repo, tmp_path, video20, Transporte((200, sobre(respuesta_grabada("gancho_generico")))), CLAVE)
    assert not r.ok and r.modelo != "sin-ia" and r.gastar is True and not sin_render.llamadas


def test_sin_ia_no_se_activa_con_un_400_del_video(repo, tmp_path, video20, sin_render, monkeypatch):
    monkeypatch.setattr(sin_ia, "construir", lambda *a, **k: pytest.fail("no debe entrar sin IA"))
    r = hacer(repo, tmp_path, video20, Transporte((400, "mal")), CLAVE)
    assert not r.ok and r.gastar is True and "400" in r.motivos[0]


def test_sin_ia_desactivada_es_el_descarte_pasajero_de_antes(repo, tmp_path, video20, sin_render):
    ruta = repo / "config" / "ojos.yml"
    c = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    c["sin_ia"]["activo"] = False
    ruta.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    r = hacer(repo, tmp_path, video20, Transporte(*[(503, "x")] * N503), CLAVE)
    assert not r.ok and "pasajero" in r.motivos[0] and r.gastar is False and not sin_render.llamadas


def test_sin_ia_con_titulo_flojo_descarta_sin_gastar_la_fuente(repo, tmp_path, video20, sin_render):
    r = hacer(repo, tmp_path, video20, caida(), CLAVE, cand=candidato(titulo="clip"))
    assert not r.ok and r.gastar is False and r.motivos[0].startswith("ojos: Gemini respondió HTTP 503") and "; sin IA:" in r.motivos[0]
    assert not sin_render.llamadas


def test_sin_ia_con_palabra_a_evitar_en_el_titulo_gasta_la_fuente(repo, tmp_path, video20, sin_render):
    r = hacer(repo, tmp_path, video20, caida(), CLAVE, cand=candidato(titulo="El final de la historia"))
    assert not r.ok and r.gastar is True and "final" in r.motivos[0]


def test_sin_ia_con_un_dict_que_no_valida_descarta_y_gasta(repo, tmp_path, video20, sin_render, monkeypatch):
    malo = {"apto": True, "motivos": [], "spoiler": False, "desnudez": False, "cinematica": False,
            "juego_correcto": True, "idioma_original": "en", "lineas": [], "gancho": "", "palabra_lapiz": "x",
            "escena": "INT. STREAM", "acotacion": "", "recorte": {"inicio": 0.0, "fin": 12.0}}
    monkeypatch.setattr(sin_ia, "construir", lambda *a, **k: sin_ia.Construccion(malo))
    r = hacer(repo, tmp_path, video20, caida(), CLAVE)
    assert not r.ok and r.gastar is True and "ojos: gancho vacío" in r.motivos and not sin_render.llamadas
    assert r.modelo == "sin-ia"


def test_sin_ia_usa_el_objetivo_de_la_config(repo, tmp_path, video20, sin_render):
    ruta = repo / "config" / "ojos.yml"
    c = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    c["sin_ia"]["duracion_objetivo_s"] = 10
    ruta.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    hacer(repo, tmp_path, video20, caida(), CLAVE)
    assert sin_render.llamadas[0][2]["duracion"] == 10.0


# ---------------------------------------------------------------- 4. corte sin IA: ffmpeg de verdad

@pytest.fixture(scope="module")
def video_con_pico(tmp_path_factory):
    """30 s: corte de escena a los 12 s y un tramo fuerte de volumen de 20 a 23 s (el resto, casi mudo)."""
    ruta = tmp_path_factory.mktemp("sinia") / "pico.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=12",
                    "-f", "lavfi", "-i", "color=c=white:size=320x240:rate=15:duration=18",
                    "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*440*t)*if(between(t,20,23),0.9,0.02)':s=8000:d=30",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(ruta)],
                   check=True)
    return ruta


@pytest.mark.slow
def test_analizar_video_mide_el_volumen_y_el_cambio_de_escena(video_con_pico):
    a = sin_ia.analizar_video(video_con_pico)
    assert len(a["rms"]) == pytest.approx(60, abs=2)
    fuerte = [i * 0.5 for i, v in enumerate(a["rms"]) if v > 5 * min(a["rms"][:20])]
    assert fuerte and min(fuerte) >= 19.5 and max(fuerte) <= 23.5
    assert any(abs(e - 12.0) < 0.3 for e in a["escenas"]), a["escenas"]


@pytest.mark.slow
def test_el_recorte_elegido_con_ffmpeg_contiene_el_pico_y_es_determinista(video_con_pico):
    r1 = sin_ia.elegir_recorte(video_con_pico, (7.0, 30.0), 12, duracion=30.0)
    r2 = sin_ia.elegir_recorte(video_con_pico, (7.0, 30.0), 12, duracion=30.0)
    assert r1 == r2
    ini, fin = r1
    assert fin - ini == pytest.approx(12.0) and ini <= 20 and fin >= 23
    assert ini == pytest.approx(12.0, abs=0.35)            # ajustado al corte de escena (sin ajuste sería 11)
    assert (20.0 - ini) >= 1.0 and fin - 23.0 >= 0       # el pico no queda al principio


@pytest.mark.slow
def test_hacer_toma_sin_ia_de_punta_a_punta_pasa_la_puerta_de_estilo(repo, tmp_path, video20):
    r = hacer(repo, tmp_path, video20, caida(), CLAVE)
    assert r.ok, r.motivos
    assert r.mp4.is_file() and all(ok for _, ok, _ in r.puntos)
    assert r.modelo == "sin-ia"
    assert r.plan["corte"] == "sin_ia"
    g = r.plan["gemini"]
    assert (g["modelo"], g["corte"]) == (None, "sin_ia") and "503" in g["motivo"]
    assert g["intentos"] and all("503" in i for i in g["intentos"])
    assert json.loads(r.mp4.with_suffix(".plan.json").read_text(encoding="utf-8"))["corte"] == "sin_ia"


# ---------------------------------------------------------------- 5. copia ligera y ojos-prueba

class FfmpegFalso:
    """Reemplaza `subprocess.run` de toma: crea el destino con el peso que toque."""

    def __init__(self, *pesos_mb):
        self.pesos, self.ordenes = list(pesos_mb), []

    def __call__(self, orden, **kw):
        self.ordenes.append(orden)
        with open(orden[-1], "wb") as f:
            f.truncate(int(self.pesos.pop(0) * 1048576))
        return subprocess.CompletedProcess(orden, 0)


def video_pesado(tmp_path, mb=20):
    v = tmp_path / "grande.mp4"
    with open(v, "wb") as f:
        f.truncate(mb * 1048576)
    return v


def test_la_copia_ligera_hace_una_segunda_pasada_si_sigue_pesando_mas_del_90_por_ciento(tmp_path, monkeypatch):
    falso = FfmpegFalso(13.0, 5.0)           # 13 MB > 0.9 * 14
    monkeypatch.setattr(toma.subprocess, "run", falso)
    c = toma.copia_para_ojos(video_pesado(tmp_path), RAIZ_REAL, tmp_path)
    assert len(falso.ordenes) == 2 and c.name == "para-ojos.mp4" and c.stat().st_size == 5 * 1048576
    primera, segunda = (" ".join(o) for o in falso.ordenes)
    assert "scale=-2:360,fps=15" in primera and "-crf 34" in primera and "48k" in primera
    assert "scale=-2:240,fps=12" in segunda and "-crf 38" in segunda and "32k" in segunda
    assert not (tmp_path / "para-ojos-2.mp4").exists()


def test_la_copia_ligera_no_hace_segunda_pasada_si_ya_cabe(tmp_path, monkeypatch):
    falso = FfmpegFalso(12.0)
    monkeypatch.setattr(toma.subprocess, "run", falso)
    c = toma.copia_para_ojos(video_pesado(tmp_path), RAIZ_REAL, tmp_path)
    assert len(falso.ordenes) == 1 and c.stat().st_size == 12 * 1048576


def test_prueba_ojos_manda_la_copia_ligera_y_anota_el_tamano_y_los_modelos(tmp_path, monkeypatch):
    from fabrica.editar import prueba_ojos, muestras
    original = video_pesado(tmp_path, 21)
    vistos = {}

    monkeypatch.setattr(muestras, "autorizar", lambda *a, **k: None)
    monkeypatch.setattr(muestras, "descargar", lambda *a, **k: original)
    monkeypatch.setattr(prueba_ojos, "sondear_video", lambda r: {"duracion": 20.0})
    monkeypatch.setattr(prueba_ojos.toma, "copia_para_ojos",
                        lambda ruta, raiz, carpeta: vistos.setdefault("copia", _copia(carpeta)))

    def _copia(carpeta):
        p = Path(carpeta) / "para-ojos.mp4"
        p.write_bytes(b"\x00" * int(2.5 * 1048576))
        return p

    def transporte(url, enc, cuerpo):
        vistos["peso_enviado"] = len(cuerpo)
        return 400, "fin"                    # la prueba termina aquí: sólo importa lo que se envió
    salida = tmp_path / "out"
    prueba_ojos.correr(salida, entorno=CLAVE, transporte=transporte,
                       transporte_lista=Lista((200, lista("gemini-3-flash-preview", "gemini-3.8-flash", "embed"))))
    informe = (salida / "informe.md").read_text(encoding="utf-8")
    assert "copia ligera: 2.5 MB" in informe
    assert "gemini-3-flash-preview" in informe and "gemini-3.8-flash" in informe and "embed" not in informe
    assert vistos["peso_enviado"] < 5 * 1048576          # nunca el original de 21 MB
    assert "clave-de-prueba" not in informe


# ---------------------------------------------------------------- ronda 1 de corrección

@pytest.mark.parametrize("titulo,esperado", [
    ("❤️ Qué jugada increíble", "Qué jugada increíble"),
    ("⚠️ Cuidado ⚠️ con esto", "Cuidado con esto"),
    ("✔️ Listo el clutch ✔️", "Listo el clutch"),
    ("Top 1️⃣ momento", "Top 1 momento"),
    ("Qué canción: café ñandú", "Qué canción: café ñandú"),
])
def test_limpiar_titulo_quita_selectores_de_variante_y_keycaps_pero_no_acentos(titulo, esperado):
    out = sin_ia.limpiar_titulo(titulo)
    assert out == esperado
    assert not any(c in out for c in "\ufe0f\u20e3\ufe0e")


def test_un_titulo_con_keycap_numerico_cae_al_respaldo_por_ser_solo_numeros(marca):
    assert sin_ia.gancho_de("Top 1️⃣ momento", None, marca, TEMPORADA)[0] is None
    ficha = ficha_propia(reglas={"gancho_sin_ia": "Mira esta jugada"})
    assert sin_ia.gancho_de("Top 1️⃣ momento", ficha, marca, TEMPORADA)[0] == "Mira esta jugada"


def test_un_titulo_de_pura_puntuacion_no_lanza_y_se_descarta(marca):
    assert sin_ia.limpiar_titulo("¿¡ ... !?") == ""
    g, motivo, _ = sin_ia.gancho_de("¿¡ ... !?", None, marca, TEMPORADA)
    assert g is None and "menos de 2 palabras" in motivo
    assert sin_ia.gancho_de("¿¡ ... !? sí", None, marca, TEMPORADA)[0] is None     # una sola palabra con letras
    assert sin_ia.palabra_lapiz_de("¿¡ !?") == ""


def test_tras_un_404_con_respaldos_por_falla_pendientes_se_prueba_el_siguiente(tmp_path):
    t = Transporte((503, "x"), (503, "x"), (404, "{}"), OK)
    m = mirar(tmp_path, t, config=cfg(modelo="preview", modelos_respaldo=["m38", "m37"], respaldos_por_falla=2))
    assert t.modelos() == ["preview", "preview", "m38", "m37"] and m.modelo == "m37" and m.datos is not None


def test_tras_un_404_sin_respaldos_por_falla_se_rinde_como_antes(tmp_path):
    t = Transporte((503, "x"), (503, "x"), (404, "{}"))
    m = mirar(tmp_path, t, config=cfg(modelo="a", modelos_respaldo=["b", "c"], respaldos_por_falla=1))
    assert m.pasajero and m.datos is None and t.modelos() == ["a", "a", "b"]


def test_con_503_en_el_principal_y_en_3_8_flash_responde_el_primer_lite_de_la_config_real(tmp_path):
    """Caso de producción (22 nov): el 503 ya no agota los respaldos antes de llegar a los -lite."""
    e = (503, "x")
    t = Transporte(e, e, e, e, OK)                      # principal x2, gemini-3.8-flash x2, gemini-3.5-flash-lite 200
    esperas = []
    cfg_real = {**CFG_REAL, "listar_modelos": False}    # la config real, sin tocar el orden ni el tope
    m = mirar(tmp_path, t, config=cfg_real, espera=esperas.append)
    assert t.modelos() == [CFG_REAL["modelo"]] * 2 + ["gemini-3.8-flash"] * 2 + ["gemini-3.5-flash-lite"]
    assert m.modelo == "gemini-3.5-flash-lite" and m.datos is not None and not m.pasajero
    assert len(esperas) == 2 and all(0 <= x <= CFG_REAL["tope_ojos_s"] for x in esperas)     # 1 espera por modelo caído


def test_el_get_de_listmodels_usa_el_presupuesto_restante(tmp_path):
    g = Lista((200, lista(CFG_REAL["modelo"])))
    mirar(tmp_path, Transporte(OK), config=cfg(listar_modelos=True, tope_ojos_s=6), transporte_lista=g)
    assert 1 <= g.llamadas[0][2] <= 6
    ojos.vaciar_cache_modelos()
    g2 = Lista((200, lista(CFG_REAL["modelo"])))
    mirar(tmp_path, Transporte(OK), config=cfg(listar_modelos=True), transporte_lista=g2)
    assert g2.llamadas[0][2] == 15


def test_un_listmodels_fallido_tambien_se_guarda_en_la_cache(tmp_path):
    g = Lista((503, "x"))
    for _ in range(3):
        m = mirar(tmp_path, Transporte(OK), config=cfg(listar_modelos=True), transporte_lista=g)
        assert m.datos is not None and any("ListModels" in i and "503" in i for i in m.intentos)
    assert len(g.llamadas) == 1
    ojos.vaciar_cache_modelos()
    g2 = Lista((200, lista(CFG_REAL["modelo"])))
    mirar(tmp_path, Transporte(OK), config=cfg(listar_modelos=True), transporte_lista=g2)
    assert len(g2.llamadas) == 1


def test_listmodels_tiene_un_tope_de_10_paginas():
    pagina = (200, json.dumps({"models": [], "nextPageToken": None}))
    paginas = [(200, json.dumps({"models": [], "nextPageToken": f"p{i}"})) for i in range(30)]
    g = Lista(*paginas)
    with pytest.raises(ojos.ErrorOjos, match="10 páginas"):
        ojos.modelos_disponibles("k", g)
    assert len(g.llamadas) == 10
    assert pagina and True


def test_un_listmodels_interminable_no_bloquea_mirar(tmp_path):
    g = Lista(*[(200, json.dumps({"models": [], "nextPageToken": f"p{i}"})) for i in range(30)])
    m = mirar(tmp_path, Transporte(OK), config=cfg(listar_modelos=True), transporte_lista=g)
    assert m.datos is not None and any("páginas" in i for i in m.intentos)


def test_sin_clave_no_hay_corte_sin_ia_y_queda_el_descarte_de_antes(repo, tmp_path, video20, sin_render, monkeypatch):
    monkeypatch.setattr(sin_ia, "construir", lambda *a, **k: pytest.fail("no debe entrar sin IA"))
    r = hacer(repo, tmp_path, video20, Transporte(), {})
    assert not r.ok and r.motivos == ["ojos: sin GEMINI_API_KEY"] and r.gastar is False
    assert sin_ia.tomas_usadas() == 0


@pytest.mark.parametrize("estado,cuerpo", [(403, "forbidden"), (401, "unauthorized"),
                                           (400, json.dumps({"error": {"status": "API_KEY_INVALID"}}))])
def test_clave_mala_no_hace_corte_sin_ia_ni_gasta_la_fuente(repo, tmp_path, video20, sin_render, monkeypatch, estado, cuerpo):
    monkeypatch.setattr(sin_ia, "construir", lambda *a, **k: pytest.fail("no debe entrar sin IA"))
    r = hacer(repo, tmp_path, video20, Transporte((estado, cuerpo)), CLAVE)
    assert not r.ok and r.gastar is False and str(estado) in r.motivos[0]
    assert not sin_render.llamadas and sin_ia.tomas_usadas() == 0


def test_solo_una_toma_sin_ia_por_ciclo_las_demas_son_pasajeras(repo, tmp_path, video20, sin_render):
    r1 = hacer(repo, tmp_path, video20, caida(), CLAVE)
    assert sin_render.llamadas and r1.modelo == "sin-ia"
    r2 = hacer(repo, tmp_path, video20, caida(), CLAVE)
    assert not r2.ok and r2.gastar is False and r2.modelo is None
    assert "tope de tomas sin IA del ciclo" in r2.motivos[0] and "pasajero" in r2.motivos[0]
    assert len(sin_render.llamadas) == 1
    sin_ia.reiniciar_contador()
    hacer(repo, tmp_path, video20, caida(), CLAVE)
    assert len(sin_render.llamadas) == 2


def test_max_por_ciclo_sale_de_la_config(repo, tmp_path, video20, sin_render):
    ruta = repo / "config" / "ojos.yml"
    c = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    c["sin_ia"]["max_por_ciclo"] = 2
    ruta.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    for _ in range(3):
        hacer(repo, tmp_path, video20, caida(), CLAVE)
    assert len(sin_render.llamadas) == 2
    assert CFG_REAL["sin_ia"]["max_por_ciclo"] == 1


def test_un_descarte_sin_ia_antes_de_cortar_deja_el_modelo_en_none(repo, tmp_path, video20, sin_render):
    r = hacer(repo, tmp_path, video20, caida(), CLAVE, cand=candidato(titulo="clip"))
    assert not r.ok and r.modelo is None


def test_validar_revisa_max_por_ciclo(repo):
    from fabrica import validar
    ruta = repo / "config" / "ojos.yml"
    c = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    c["sin_ia"]["max_por_ciclo"] = -1
    ruta.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    assert "max_por_ciclo" in "\n".join(validar.validar(repo).errores)


def test_el_diagnostico_no_imprime_http_000000():
    texto = (RAIZ_REAL / ".github" / "workflows" / "diagnostico.yml").read_text(encoding="utf-8")
    assert '|| echo "000"' not in texto and texto.count("|| true)") >= 2


def test_con_10_caidas_lentas_no_se_pasa_del_tope_de_ojos(tmp_path, monkeypatch):
    """Revisión de Codex (PR #31): con 4 respaldos caben hasta 10 peticiones, pero el tope acumulado (`tope_ojos_s`)
    manda. Reloj simulado; ListModels activo; cada petición consume todo el tiempo que se le da (caso peor de un
    timeout) y luego da 503. Ni peticiones ni esperas pasan del tope."""
    reloj = [1000.0]
    monkeypatch.setattr(ojos.time, "monotonic", lambda: reloj[0])
    tope = CFG_REAL["tope_ojos_s"]
    dados = []

    class Lento(Transporte):
        def __call__(self, url, encabezados, cuerpo, tiempo):
            usado = reloj[0] - 1000.0
            dados.append((usado, tiempo))
            reloj[0] += tiempo                                   # consume el timeout completo
            return super().__call__(url, encabezados, cuerpo)

    class ListaLenta(Lista):
        def __call__(self, url, encabezados, tiempo):
            dados.append((reloj[0] - 1000.0, tiempo))
            reloj[0] += tiempo                                   # ListModels también consume su timeout completo
            return super().__call__(url, encabezados, tiempo)

    todos = [CFG_REAL["modelo"], *CFG_REAL["modelos_respaldo"]]
    g = ListaLenta((200, lista(*todos)))
    t = Lento(*[(503, "x")] * 10)
    m = mirar(tmp_path, t, config={**CFG_REAL, "listar_modelos": True}, espera=lambda s: reloj.__setitem__(0, reloj[0] + s),
              transporte_lista=g)
    assert m.datos is None and m.pasajero
    assert len(dados) >= 2 and g.llamadas                                           # ListModels y peticiones
    assert all(usado + tiempo <= tope + 1e-6 for usado, tiempo in dados)   # cada timeout cabe en lo que queda
    assert reloj[0] - 1000.0 <= tope + 1e-6                                          # el total no pasa del tope

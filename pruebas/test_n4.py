"""N4: ojos (Gemini), descarga autorizada, hacer_toma de punta a punta y detalles 5b. Sin red."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import descargar as mod_descargar
from fabrica import fuentes, ojos, puertas, registro, validar
from fabrica.detectar import Candidato
from fabrica.editar import componer as mod_componer
from fabrica.editar import estilo, toma
from fabrica.editar.lienzo import Toma
from fabrica.editar.marca import Marca

DATOS = RAIZ_REAL / "pruebas" / "datos" / "gemini"
AHORA = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("America/Guatemala"))
HOY = AHORA.date()
URL = "https://ejemplo.invalid/clip.mp4"


# ---------------------------------------------------------------- utilidades

def respuesta_grabada(nombre: str) -> str:
    return (DATOS / f"{nombre}.json").read_text(encoding="utf-8")


def sobre(texto_modelo: str) -> str:
    """Lo que devuelve la API: el texto del modelo dentro de candidates[0].content.parts[0].text."""
    return json.dumps({"candidates": [{"content": {"parts": [{"text": texto_modelo}]}, "finishReason": "STOP"}]})


class Transporte:
    """Transporte falso: entrega las respuestas de la cola en orden y apunta cada llamada."""

    def __init__(self, *respuestas):
        self.cola = list(respuestas)
        self.llamadas: list[tuple[str, dict, bytes]] = []

    def __call__(self, url, encabezados, cuerpo):
        self.llamadas.append((url, encabezados, cuerpo))
        r = self.cola.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def modelos(self):
        return [u.split("/models/")[1].split(":")[0] for u, _, _ in self.llamadas]


class Resp:
    def __init__(self, datos: bytes, tipo="video/mp4", largo=None):
        self._b = io.BytesIO(datos)
        self.headers = {"Content-Type": tipo}
        if largo is not None:
            self.headers["Content-Length"] = str(largo)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._b.read(n)


@pytest.fixture(scope="session")
def video20(tmp_path_factory):
    """Video sintético de 20 s, 1280x720, con audio (ffmpeg testsrc)."""
    ruta = tmp_path_factory.mktemp("sintetico") / "fuente.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=1280x720:rate=30:duration=20", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(ruta)], check=True)
    return ruta


@pytest.fixture(scope="session")
def marca():
    return Marca.cargar(RAIZ_REAL)


@pytest.fixture(scope="session")
def temporada():
    return yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))


def ficha_propia(**cambios):
    f = {"id": "propio-prueba", "tipo": "C", "creador": "@probador", "origen": "propio",
         "estado": "vigente", "uso": "produccion", "publicable": True, "vence": None,
         "pilares": ["primeras_veces"], "campana": None, "presupuesto_restante": None,
         "reglas": {"duracion_s": [7, 30], "plataformas": ["shorts", "tiktok", "reels"]}}
    f.update(cambios)
    return f


def candidato(**cambios):
    c = dict(fuente_id="propio-prueba", origen="brief", id_origen="brief-n4-001", url=URL, titulo="prueba",
             creador="@probador", idioma="en", juego_id=None, duracion_s=None, vistas=0, vistas_por_hora=0.0,
             creado_en=None, vod_offset=None, pilar="primeras_veces", medir_al_descargar=True)
    c.update(cambios)
    return Candidato(**c)


def abrir_con(ruta: Path, **kw):
    datos = ruta.read_bytes()
    return lambda url: Resp(datos, **kw)


def no_conectar(*a, **k):
    raise AssertionError("se abrió una conexión sin autorización")


@pytest.fixture
def carpetas(monkeypatch):
    """Apunta las carpetas temporales que crea la descarga para comprobar que se borran."""
    creadas: list[Path] = []
    real = mod_descargar.tempfile.mkdtemp

    def mk(*a, **k):
        p = Path(real(*a, **k))
        creadas.append(p)
        return str(p)
    monkeypatch.setattr(mod_descargar.tempfile, "mkdtemp", mk)
    return creadas


# ---------------------------------------------------------------- ojos: transporte y respaldo

def mirar(tmp_path, transporte, clave="clave-de-prueba", video_bytes=b"\x00" * 2048, **kw):
    v = tmp_path / "v.mp4"
    v.write_bytes(video_bytes)
    espera = kw.pop("espera", lambda s: None)
    entorno = {"GEMINI_API_KEY": clave} if clave is not None else {}
    return ojos.mirar(v, Marca.cargar(RAIZ_REAL), yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8")),
                      raiz=RAIZ_REAL, duracion=20.0, transporte=transporte, espera=espera, entorno=entorno, **kw)


def test_sin_clave_devuelve_none_con_motivo_y_no_conecta(tmp_path):
    t = Transporte()
    m = mirar(tmp_path, t, clave=None)
    assert m.datos is None and m.motivo == "sin GEMINI_API_KEY" and t.llamadas == []


def test_clave_vacia_cuenta_como_sin_clave(tmp_path):
    assert mirar(tmp_path, Transporte(), clave="").motivo == "sin GEMINI_API_KEY"


def test_video_demasiado_grande_se_descarta_con_motivo(tmp_path):
    t = Transporte()
    m = mirar(tmp_path, t, config={"modelo": "a", "modelos_respaldo": [], "max_mb_en_linea": 0.001, "temperatura": 0.2},
              video_bytes=b"\x00" * 5000)
    assert m.datos is None and "máximo en línea" in m.motivo and t.llamadas == []


def test_respaldo_de_modelo_ante_404_y_anota_cual_respondio(tmp_path):
    t = Transporte((404, "{}"), (404, "{}"), (200, sobre(respuesta_grabada("valida"))))
    m = mirar(tmp_path, t)
    cfg = yaml.safe_load((RAIZ_REAL / "config" / "ojos.yml").read_text(encoding="utf-8"))
    assert m.datos is not None
    assert t.modelos() == [cfg["modelo"], cfg["modelos_respaldo"][0], cfg["modelos_respaldo"][1]]
    assert m.modelo == cfg["modelos_respaldo"][1]
    assert any("404" in i for i in m.intentos)


def test_todos_404_da_motivo(tmp_path):
    cfg = yaml.safe_load((RAIZ_REAL / "config" / "ojos.yml").read_text(encoding="utf-8"))
    n = 1 + len(cfg["modelos_respaldo"])
    m = mirar(tmp_path, Transporte(*[(404, "{}")] * n))
    assert m.datos is None and "todos dieron 404" in m.motivo


def test_reintento_unico_ante_429_con_espera(tmp_path):
    esperas = []
    t = Transporte((429, "{}"), (200, sobre(respuesta_grabada("valida"))))
    m = mirar(tmp_path, t, espera=esperas.append)
    assert m.datos is not None and len(t.llamadas) == 2 and len(esperas) == 1 and esperas[0] > 0
    assert t.modelos()[0] == t.modelos()[1]


def test_dos_429_seguidos_prueban_un_solo_modelo_mas_y_se_rinden(tmp_path):
    t = Transporte((429, "{}"), (429, "{}"), (429, "{}"), (429, "{}"))
    m = mirar(tmp_path, t)
    assert m.datos is None and "429" in m.motivo and len(t.llamadas) == 4 and m.pasajero
    assert t.modelos()[0] == t.modelos()[1] != t.modelos()[2] == t.modelos()[3]


def test_503_persistente_pasa_al_modelo_de_respaldo(tmp_path):
    t = Transporte((503, "x"), (503, "x"), (200, sobre(respuesta_grabada("valida"))))
    m = mirar(tmp_path, t)
    assert m.datos is not None and not m.pasajero and m.modelo == t.modelos()[2] != t.modelos()[0]


def test_reintento_ante_5xx(tmp_path):
    t = Transporte((503, "x"), (200, sobre(respuesta_grabada("valida"))))
    assert mirar(tmp_path, t).datos is not None and len(t.llamadas) == 2


def test_error_400_no_se_reintenta(tmp_path):
    t = Transporte((400, "mala petición"))
    m = mirar(tmp_path, t)
    assert m.datos is None and "400" in m.motivo and len(t.llamadas) == 1


def test_la_clave_va_en_el_encabezado_y_nunca_en_la_url_ni_en_los_informes(tmp_path):
    t = Transporte((200, sobre(respuesta_grabada("valida"))))
    m = mirar(tmp_path, t, clave="CLAVE-SECRETA-XYZ")
    url, enc, _ = t.llamadas[0]
    assert enc["x-goog-api-key"] == "CLAVE-SECRETA-XYZ" and "CLAVE-SECRETA" not in url
    assert "CLAVE-SECRETA" not in repr(m) and "CLAVE-SECRETA" not in " ".join(m.intentos)


def test_error_de_conexion_da_motivo_sin_clave_y_es_pasajero(tmp_path):
    e = ojos.ErrorOjos("fallo de conexión con Gemini: TimeoutError")
    t = Transporte(e, e, e, e)
    m = mirar(tmp_path, t, clave="CLAVE-SECRETA-XYZ")
    assert m.datos is None and "conexión" in m.motivo and "CLAVE-SECRETA" not in m.motivo
    assert m.pasajero and len(t.llamadas) == 4


def test_un_timeout_y_luego_respuesta_valida(tmp_path):
    t = Transporte(ojos.ErrorOjos("fallo de conexión con Gemini: TimeoutError"),
                   (200, sobre(respuesta_grabada("valida"))))
    m = mirar(tmp_path, t)
    assert m.datos is not None and not m.pasajero and len(t.llamadas) == 2


def test_quitar_visto_deshace_solo_la_ultima_marca(repo):
    from fabrica import puertas as p
    p.anotar_visto(repo, "a"); p.anotar_visto(repo, "b"); p.anotar_visto(repo, "a")
    p.quitar_visto(repo, "a"); p.quitar_visto(repo, "zzz")
    assert (repo / "estado" / "vistos.txt").read_text(encoding="utf-8").split() == ["a", "b"]


def test_tras_una_caida_un_404_del_respaldo_no_abre_un_tercer_modelo(tmp_path):
    t = Transporte((503, "x"), (503, "x"), (404, "{}"), (200, sobre(respuesta_grabada("valida"))))
    m = mirar(tmp_path, t)
    assert m.datos is None and m.pasajero and len(t.llamadas) == 3


def test_json_roto_se_descarta_con_motivo(tmp_path):
    m = mirar(tmp_path, Transporte((200, sobre(respuesta_grabada("rota")))))
    assert m.datos is None and "no es JSON válido" in m.motivo


def test_sin_candidatos_se_descarta(tmp_path):
    m = mirar(tmp_path, Transporte((200, json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}))))
    assert m.datos is None and "SAFETY" in m.motivo


def test_la_peticion_lleva_video_en_linea_esquema_temperatura_y_la_instruccion(tmp_path):
    t = Transporte((200, sobre(respuesta_grabada("valida"))))
    mirar(tmp_path, t, video_bytes=b"VIDEO-FALSO")
    cuerpo = json.loads(t.llamadas[0][2])
    partes = cuerpo["contents"][0]["parts"]
    assert partes[0]["inline_data"]["mime_type"] == "video/mp4" and partes[0]["inline_data"]["data"]
    gc = cuerpo["generationConfig"]
    assert gc["responseMimeType"] == "application/json" and gc["temperature"] == 0.2
    campos = set(gc["responseSchema"]["properties"])
    assert campos == {"apto", "motivos", "spoiler", "desnudez", "cinematica", "juego_correcto", "idioma_original",
                      "lineas", "gancho", "palabra_lapiz", "escena", "acotacion", "recorte"}
    assert set(gc["responseSchema"]["properties"]["lineas"]["items"]["properties"]) == \
        {"t0", "t1", "hablante", "original", "es"}
    texto = partes[1]["text"]
    assert "final" in texto and "epílogo" in texto          # palabras_evitar de temporada.yml
    assert "NO VAS A CREER" in texto                          # ganchos genéricos de marca.yml
    assert "{{" not in texto                                  # sin marcadores sin sustituir


# ---------------------------------------------------------------- validar_respuesta

def cargar_dato(nombre):
    return json.loads(respuesta_grabada(nombre))


def test_respuesta_valida_pasa(marca, temporada):
    assert ojos.validar_respuesta(cargar_dato("valida"), marca, temporada, duracion=20.0) == (True, [])


@pytest.mark.parametrize("nombre,fragmento", [
    ("campo_faltante", "campo gancho"),
    ("gancho_8_palabras", "8 palabras"),
    ("gancho_generico", "genérico"),
    ("spoiler", "spoiler"),
    ("desnudez", "desnudez"),
    ("cinematica", "cinemática"),
    ("juego_incorrecto", "juego esperado"),
    ("recorte_fuera", "recorte"),
    ("frase_imposible", "no cabe"),
])
def test_respuestas_grabadas_que_se_descartan(nombre, fragmento, marca, temporada):
    ok, motivos = ojos.validar_respuesta(cargar_dato(nombre), marca, temporada, duracion=20.0)
    assert not ok and any(fragmento in m for m in motivos), motivos


def test_frase_larga_se_parte_en_dos_lineas_de_tiempo_consecutivas(marca, temporada):
    datos = cargar_dato("frase_larga_partible")
    assert ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)[0]
    lineas = ojos.partir_lineas(datos, marca)
    assert len(lineas) == 3                                   # 2 de la primera + la segunda intacta
    a, b = lineas[0], lineas[1]
    assert a["t1"] == b["t0"] and a["t0"] == 2.5 and b["t1"] == 6.8
    assert a["es"].endswith("nunca.") and b["es"].startswith("Todavía")
    assert a["original"] == b["original"]


def test_tipos_erroneos_se_descartan(marca, temporada):
    d = cargar_dato("valida")
    d["apto"] = "sí"
    d["lineas"][0]["t0"] = "2.5"
    ok, m = ojos.validar_respuesta(d, marca, temporada)
    assert not ok and any("apto" in x for x in m)


def test_no_es_un_objeto(marca, temporada):
    assert ojos.validar_respuesta([1, 2], marca, temporada)[0] is False


def test_recorte_nan_o_invertido(marca, temporada):
    d = cargar_dato("valida")
    d["recorte"] = {"inicio": float("nan"), "fin": 14}
    assert not ojos.validar_respuesta(d, marca, temporada)[0]
    d["recorte"] = {"inicio": 14, "fin": 2}
    assert not ojos.validar_respuesta(d, marca, temporada)[0]


def test_recorte_demasiado_corto(marca, temporada):
    d = cargar_dato("valida")
    d["recorte"] = {"inicio": 2, "fin": 6}
    d["lineas"] = []
    ok, m = ojos.validar_respuesta(d, marca, temporada, duracion=20.0)
    assert not ok and any("de 7 a 30" in x for x in m)


def test_linea_fuera_del_recorte(marca, temporada):
    d = cargar_dato("valida")
    d["lineas"][1]["t1"] = 16.0
    ok, m = ojos.validar_respuesta(d, marca, temporada, duracion=20.0)
    assert not ok and any("fuera del recorte" in x for x in m)


def test_palabra_del_lapiz_fuera_del_gancho(marca, temporada):
    d = cargar_dato("valida")
    d["palabra_lapiz"] = "DRAGÓN"
    assert any("palabra_lapiz" in x for x in ojos.validar_respuesta(d, marca, temporada)[1])


@pytest.mark.parametrize("campo", ["gancho", "escena", "linea"])
def test_palabras_a_evitar_en_gancho_escena_y_lineas(campo, marca, temporada):
    d = cargar_dato("valida")
    if campo == "gancho":
        d["gancho"] = "Kai ve el final"; d["palabra_lapiz"] = "FINAL"
    elif campo == "escena":
        d["escena"] = "INT. STREAM DE KAI — ÚLTIMA MISIÓN"
    else:
        d["lineas"][0]["es"] = "Ella muere aquí, la traición era cierta."
    ok, m = ojos.validar_respuesta(d, marca, temporada, duracion=20.0)
    assert not ok and any("palabra a evitar" in x for x in m), m


def test_escena_sin_formato_de_guion(marca, temporada):
    d = cargar_dato("valida")
    d["escena"] = "Una ciudad cualquiera"
    assert any("INT. o EXT." in x for x in ojos.validar_respuesta(d, marca, temporada)[1])


def test_instruccion_nombra_el_juego_y_la_duracion(marca, temporada):
    texto = ojos.instruccion(marca, temporada, 12.5)
    assert "GTA 6" in texto and "12.5" in texto


# ---------------------------------------------------------------- config/ojos.yml en fabrica.validar

def test_config_ojos_valida_y_se_rechaza_si_esta_mal(repo, editar_yaml):
    assert validar.validar(repo).errores == []
    editar_yaml(repo, "config/ojos.yml", lambda d: d.update(max_mb_en_linea=50))
    assert any("max_mb_en_linea" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "config/ojos.yml", lambda d: d.update(max_mb_en_linea=18, temperatura=9))
    assert any("temperatura" in e for e in validar.validar(repo).errores)
    (repo / "config" / "ojos.yml").unlink()
    assert any("config/ojos.yml: no existe" in e for e in validar.validar(repo).errores)


# ---------------------------------------------------------------- descarga

def test_descarga_negada_sin_permite_descarga_no_abre_conexion(repo):
    ficha = ficha_propia(tipo="A", origen="twitch", permiso_url="https://x.org/p", permiso_leido=True,
                         tarifa_usd_por_mil=1, vence="2027-01-01")
    c = candidato(origen="twitch", url="https://clips.twitch.tv/AbcDef")
    with pytest.raises(mod_descargar.ErrorDescarga, match="no descargable|permite_descarga"):
        with mod_descargar.descargar(repo, c, ficha, HOY, abrir=no_conectar, ejecutor=no_conectar):
            pass
    assert not (repo / "estado" / "vistos.txt").exists() or "AbcDef" not in (repo / "estado" / "vistos.txt").read_text()


@pytest.mark.parametrize("ficha,motivo", [
    (None, "ficha ausente"),
    (ficha_propia(estado="vetada"), "estado"),
    (ficha_propia(vence="2020-01-01"), "vencida"),
    (ficha_propia(publicable=False), "publicable"),
    (ficha_propia(tipo="D", pilares=["informativa"]), "tipo D"),
])
def test_autorizacion_antes_de_conectar(repo, ficha, motivo):
    with pytest.raises(mod_descargar.ErrorDescarga, match=motivo):
        with mod_descargar.descargar(repo, candidato(pilar="informativa"), ficha, HOY, abrir=no_conectar):
            pass


def test_url_fuera_del_material_se_niega(repo):
    ficha = ficha_propia(tipo="B", permiso_url="https://x.org/p", permiso_leido=True,
                         material=["https://ejemplo.invalid/otro.mp4"])
    with pytest.raises(mod_descargar.ErrorDescarga, match="fuera de material"):
        with mod_descargar.descargar(repo, candidato(), ficha, HOY, abrir=no_conectar):
            pass


def test_material_con_tipo_que_no_es_video_se_rechaza_y_borra_la_carpeta(repo, carpetas):
    abrir = lambda url: Resp(b"<html>hola</html>", tipo="text/html")
    with pytest.raises(mod_descargar.ErrorDescarga, match="no es video"):
        with mod_descargar.descargar(repo, candidato(), ficha_propia(), HOY, abrir=abrir):
            pass
    assert carpetas and not any(c.exists() for c in carpetas)


def test_url_http_no_se_descarga(repo):
    with pytest.raises(mod_descargar.ErrorDescarga, match="https"):
        with mod_descargar.descargar(repo, candidato(url="http://ejemplo.invalid/a.mp4"), ficha_propia(), HOY,
                                     abrir=no_conectar):
            pass


def test_descarga_buena_mide_con_ffprobe_y_anota_visto(repo, carpetas, video20):
    with mod_descargar.descargar(repo, candidato(), ficha_propia(), HOY, abrir=abrir_con(video20)) as d:
        assert d.ruta.is_file() and abs(d.duracion - 20.0) < 0.2
        ruta = d.ruta
    assert not ruta.exists() and not any(c.exists() for c in carpetas)
    assert "brief-n4-001" in (repo / "estado" / "vistos.txt").read_text(encoding="utf-8").split()


def test_la_carpeta_temporal_se_borra_aunque_el_cuerpo_falle(repo, carpetas, video20):
    with pytest.raises(RuntimeError, match="boom"):
        with mod_descargar.descargar(repo, candidato(), ficha_propia(), HOY, abrir=abrir_con(video20)):
            raise RuntimeError("boom")
    assert carpetas and not any(c.exists() for c in carpetas)


def test_fuente_mas_corta_que_el_minimo_se_descarta_y_queda_como_visto(repo, carpetas, video20):
    ficha = ficha_propia(reglas={"duracion_s": [25, 30], "plataformas": ["shorts"]})
    with pytest.raises(mod_descargar.ErrorDescarga, match="dura 20"):
        with mod_descargar.descargar(repo, candidato(), ficha, HOY, abrir=abrir_con(video20)):
            pass
    assert not any(c.exists() for c in carpetas)
    assert "brief-n4-001" in (repo / "estado" / "vistos.txt").read_text(encoding="utf-8").split()


def test_fuente_mas_larga_que_el_clip_pasa_porque_el_recorte_lo_elige_gemini(repo, carpetas, video20):
    # FM27 (18 nov): videos de 66 y 118 s con clips de 10 a 30 s se descartaban por error.
    ficha = ficha_propia(reglas={"duracion_s": [7, 15], "plataformas": ["shorts"]})
    with mod_descargar.descargar(repo, candidato(), ficha, HOY, abrir=abrir_con(video20)) as d:
        assert d.duracion == pytest.approx(20, abs=0.5)


def test_duracion_nan_se_descarta(repo, carpetas, video20, monkeypatch):
    monkeypatch.setattr(mod_descargar, "sondear_video", lambda ruta: {"duracion": "nan"})
    with pytest.raises(mod_descargar.ErrorDescarga, match="no válida"):
        with mod_descargar.descargar(repo, candidato(), ficha_propia(), HOY, abrir=abrir_con(video20)):
            pass


def test_fuente_mas_larga_que_el_tope_se_descarta(repo, carpetas, video20, monkeypatch):
    monkeypatch.setattr(mod_descargar, "TOPE_FUENTE_S", 15.0)
    with pytest.raises(mod_descargar.ErrorDescarga, match="tope de una fuente"):
        with mod_descargar.descargar(repo, candidato(), ficha_propia(), HOY, abrir=abrir_con(video20)):
            pass


def test_tope_de_800_mb_por_cabecera_y_por_streaming(tmp_path):
    with pytest.raises(mod_descargar.ErrorDescarga, match="tope"):
        mod_descargar.bajar_https(URL, tmp_path / "a.mp4", abrir=lambda u: Resp(b"x" * 10, largo=999), tope=100)
    with pytest.raises(mod_descargar.ErrorDescarga, match="tope"):
        mod_descargar.bajar_https(URL, tmp_path / "b.mp4", abrir=lambda u: Resp(b"x" * 500), tope=100)
    assert not (tmp_path / "b.mp4").exists()
    assert mod_descargar.TOPE_BYTES == 800 * 1024 * 1024


def ficha_twitch(**cambios):
    f = ficha_propia(tipo="A", origen="twitch", permiso_url="https://x.org/p", permiso_leido=True,
                     tarifa_usd_por_mil=1.0, vence="2027-01-01", pilares=["primeras_veces"])
    f["reglas"] = {"duracion_s": [7, 30], "plataformas": ["shorts"], "permite_descarga": True}
    f.update(cambios)
    return f


def test_clip_de_twitch_usa_ytdlp_sin_cookies_ni_listas(repo, carpetas, video20):
    ordenes = []

    def ejecutor(orden, **kw):
        ordenes.append(orden)
        carpeta = Path(orden[orden.index("-o") + 1]).parent
        shutil.copy(video20, carpeta / "clip.mp4")
        return subprocess.CompletedProcess(orden, 0, "", "")

    c = candidato(origen="twitch", url="https://clips.twitch.tv/AbcDef", id_origen="AbcDef")
    with mod_descargar.descargar(repo, c, ficha_twitch(), HOY, abrir=no_conectar, ejecutor=ejecutor) as d:
        assert d.ruta.name == "clip.mp4"
    o = ordenes[0]
    assert o[0] == "yt-dlp" and "--no-playlist" in o and "--no-cookies" in o and "--ignore-config" in o
    assert "--cookies" not in o and "--cookies-from-browser" not in o
    assert "duration<=120" in o
    alternativas = o[o.index("-f") + 1].split("/")
    assert len(alternativas) >= 2 and all("height<=1080" in a for a in alternativas)
    assert o[-2:] == ["--", "https://clips.twitch.tv/AbcDef"]
    assert not any(c.exists() for c in carpetas)


def test_twitch_sin_permite_descarga_aunque_traiga_material_no_baja(repo):
    ficha = ficha_twitch(material=["https://clips.twitch.tv/AbcDef"])
    ficha["reglas"]["permite_descarga"] = False
    c = candidato(origen="twitch", url="https://clips.twitch.tv/AbcDef")
    with pytest.raises(mod_descargar.ErrorDescarga, match="permite_descarga"):
        with mod_descargar.descargar(repo, c, ficha, HOY, abrir=no_conectar, ejecutor=no_conectar):
            pass


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abc", "https://youtu.be/abc"])
def test_nunca_se_descarga_de_youtube(repo, url):
    c = candidato(origen="twitch", url=url)
    with pytest.raises(mod_descargar.ErrorDescarga):
        with mod_descargar.descargar(repo, c, ficha_twitch(), HOY, abrir=no_conectar, ejecutor=no_conectar):
            pass
    c = candidato(origen="brief", url=url)
    with pytest.raises(mod_descargar.ErrorDescarga, match="YouTube"):
        with mod_descargar.descargar(repo, c, ficha_propia(), HOY, abrir=no_conectar, ejecutor=no_conectar):
            pass


def test_twitch_con_url_que_no_es_clip(repo):
    c = candidato(origen="twitch", url="https://www.twitch.tv/videos/123")
    with pytest.raises(mod_descargar.ErrorDescarga, match="clip de Twitch"):
        with mod_descargar.descargar(repo, c, ficha_twitch(), HOY, ejecutor=no_conectar):
            pass


# ---------------------------------------------------------------- hacer_toma de punta a punta

def transporte_con(nombre):
    return Transporte((200, sobre(respuesta_grabada(nombre))))


def correr(repo, nombre, tmp_path, video, **kw):
    return toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, salida=tmp_path / "salida",
                           abrir=abrir_con(video), transporte=transporte_con(nombre),
                           entorno={"GEMINI_API_KEY": "clave-de-prueba"}, espera=lambda s: None, **kw)


@pytest.mark.slow
def test_hacer_toma_de_punta_a_punta_pasa_la_puerta_de_estilo(repo, tmp_path, video20, carpetas):
    antes = registro.contador_toma(repo)
    r = correr(repo, "valida", tmp_path, video20)
    assert r.ok, r.motivos
    assert r.mp4.is_file() and all(ok for _, ok, _ in r.puntos)
    assert any(n.endswith("Cuerpo coincide con el origen") and ok for n, ok, _ in r.puntos)
    assert registro.contador_toma(repo) == antes                # no sube el contador
    p = r.plan
    assert p["toma"]["numero"] == antes and p["toma"]["credito"] == "Con: @probador"
    assert p["toma"]["lapiz"] == ("círculo" if antes % 2 else "subrayado")
    assert p["origen"]["inicio"] == 2.0 and p["duracion"] == 12.0 and len(p["origen"]["sha256"]) == 64
    assert p["lineas"][0]["t0"] == 0.5 and p["lineas"][0]["acotacion"] == "(se ríe)"
    assert r.fila["fuente_id"] == "propio-prueba" and r.fila["pilar"] == "primeras_veces" and r.fila["fecha"] == "2026-11-18"
    assert not any(c.exists() for c in carpetas)                # la descarga se borró; el MP4 queda en salida
    assert r.mp4.with_suffix(".plan.json").is_file()


@pytest.mark.slow
def test_hacer_toma_con_spoiler_es_descarte_con_su_motivo(repo, tmp_path, video20, carpetas):
    r = correr(repo, "spoiler", tmp_path, video20)
    assert not r.ok and r.mp4 is None
    assert any("spoiler" in m for m in r.motivos) and any("apto: false" in m for m in r.motivos)
    assert not list((tmp_path / "salida").glob("*.mp4"))
    assert not any(c.exists() for c in carpetas)


def test_hacer_toma_sin_clave_descarta(repo, tmp_path, video20):
    r = toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, salida=tmp_path / "s", abrir=abrir_con(video20),
                        transporte=Transporte(), entorno={})
    assert not r.ok and r.motivos == ["ojos: sin GEMINI_API_KEY"]


def test_hacer_toma_con_descarga_negada(repo, tmp_path):
    r = toma.hacer_toma(repo, candidato(), ficha_propia(estado="vetada"), AHORA, salida=tmp_path / "s",
                        abrir=no_conectar, entorno={"GEMINI_API_KEY": "x"})
    assert not r.ok and "descarga" in r.motivos[0] and "estado" in r.motivos[0]


@pytest.mark.slow
def test_hacer_toma_con_frase_larga_partida_pasa_la_puerta(repo, tmp_path, video20):
    r = correr(repo, "frase_larga_partible", tmp_path, video20)
    assert r.ok, r.motivos
    frases = [ln.get("frase") for ln in r.plan["lineas"]]
    assert any(f and f.endswith("nunca.") for f in frases) and len(r.plan["lineas"]) == 3


# ---------------------------------------------------------------- anotar_clip

def test_anotar_clip_escribe_fila_y_visto_en_el_mismo_paso(repo):
    c = candidato(id_origen="clip-xyz-7")
    fila = registro.anotar_clip(repo, c, {"toma": 4, "fuente_id": "propio-prueba", "notas": "ensayo"})
    assert fila["id_clip"] == "clip-xyz-7" and fila["notas"] == "origen:clip-xyz-7; ensayo"
    assert "clip-xyz-7" in (repo / "estado" / "vistos.txt").read_text(encoding="utf-8").split()
    assert "clip-xyz-7" in puertas.ids_en_marcador(repo)
    ok, _ = puertas.puerta_no_repetido(repo, c)
    assert not ok


def test_anotar_clip_con_fila_invalida_no_anota_nada(repo):
    c = candidato(id_origen="clip-malo")
    with pytest.raises(ValueError, match="desconocidas"):
        registro.anotar_clip(repo, c, {"columna_inventada": 1})
    assert puertas.puerta_no_repetido(repo, c)[0]
    with pytest.raises(ValueError):
        registro.anotar_clip(repo, candidato(id_origen=" "), {})


# ---------------------------------------------------------------- 5b

def _con_desfase(video20, destino, desfase=1.0):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video20), "-an", "-c:v", "libx264",
                    "-preset", "ultrafast", "-t", "6", "-output_ts_offset", str(desfase), str(destino)], check=True)
    return destino


def test_duracion_por_paquetes_sin_duration_en_la_pista(tmp_path, video20):
    ruta = _con_desfase(video20, tmp_path / "desfase.mp4")        # el primer PTS es 1 s, no 0
    d = mod_componer._duracion_pista_video(ruta, {"r_frame_rate": "30/1"}, {})
    assert abs(d - 6.0) < 0.1, d                                   # antes daba ~7 (PTS mayor + duración)


def test_duracion_por_paquetes_sin_nb_frames_pero_con_tasa_rota(tmp_path, video20):
    ruta = _con_desfase(video20, tmp_path / "d2.mp4", 2.5)
    for v in ({"r_frame_rate": "0/0"}, {"r_frame_rate": "x", "nb_frames": "N/A"}, {"duration": "N/A"}):
        assert abs(mod_componer._duracion_pista_video(ruta, v, {}) - 6.0) < 0.1


def test_duracion_con_nb_frames_y_con_duration(video20):
    assert mod_componer._duracion_pista_video(video20, {"nb_frames": "600", "r_frame_rate": "30/1"}, {}) == 20.0
    assert mod_componer._duracion_pista_video(video20, {"duration": "19.5"}, {}) == 19.5


@pytest.fixture(scope="module")
def render_tipografico(tmp_path_factory, marca):
    """Una toma tipográfica de 8 s renderizada con el compositor."""
    d = tmp_path_factory.mktemp("render")
    t = Toma(numero=5, gancho="Kai llega a la ciudad", palabra_lapiz="CIUDAD", lapiz="círculo",
             escena="INT. STREAM DE KAI — MINUTO 12", credito="Con: @probador")
    mp4 = d / "t.mp4"
    plan = mod_componer.componer(marca, t, [mod_componer.Linea(0.5, 3.0, "KAI", "Hola.")], mp4,
                                 duracion=8.0, trabajo=d / "w")
    return mp4, plan, d


@pytest.mark.slow
def test_cierre_con_los_ultimos_tres_fotogramas_en_negro_da_rojo(render_tipografico, marca, tmp_path):
    mp4, plan, _ = render_tipografico
    assert estilo.cierre_visible(mp4, plan, marca)[0]
    negro = tmp_path / "negro.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4), "-vf",
                    "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='gte(t,7.9)'",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
                    "-c:a", "copy", "-movflags", "+faststart", str(negro)], check=True)
    assert abs(estilo.sondear(negro)["duracion"] - 8.0) < 0.1       # misma duración
    ok, detalle = estilo.cierre_visible(negro, plan, marca)
    assert not ok and "último fotograma" in detalle


@pytest.fixture(scope="module")
def render_con_cuerpo(tmp_path_factory, marca, video20):
    d = tmp_path_factory.mktemp("render-cuerpo")
    t = Toma(numero=5, gancho="Kai llega a la ciudad", palabra_lapiz="CIUDAD", escena="INT. STREAM DE KAI — MINUTO 12",
             credito="Con: @probador")
    mp4 = d / "c.mp4"
    plan = mod_componer.componer(marca, t, [mod_componer.Linea(0.5, 3.0, "KAI", "Hola.")], mp4, duracion=8.0,
                                 trabajo=d / "w", fuente_video=video20, inicio=2.0)
    return mp4, plan


@pytest.mark.slow
@pytest.mark.parametrize("campo,valor,fragmento", [
    ("duracion", float("nan"), "finito"),
    ("duracion", float("inf"), "finito"),
    ("duracion", -3.0, "positiva"),
    ("duracion", 0, "positiva"),
    ("duracion", "8", "finito"),
    ("duracion", True, "finito"),
    ("duracion", 5.0, "no coincide"),
    ("inicio", -1.0, "no negativo"),
    ("inicio", float("nan"), "finito"),
])
def test_cuerpo_coincide_rechaza_origen_duracion_erronea(render_con_cuerpo, marca, campo, valor, fragmento):
    mp4, plan = render_con_cuerpo
    assert estilo.cuerpo_coincide(mp4, plan, marca)[0]
    malo = json.loads(json.dumps(plan))
    malo["origen"][campo] = valor
    ok, det = estilo.cuerpo_coincide(mp4, malo, marca)
    assert not ok and fragmento in det, det


@pytest.mark.slow
def test_render_correcto_con_fuente_sintetica_de_60_fps_y_mucho_movimiento_pasa(marca, tmp_path):
    src = tmp_path / "f60.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=60:duration=14,noise=alls=30:allf=t,hue=h=t*120",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=14", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    t = Toma(numero=6, gancho="Kai llega a la ciudad", palabra_lapiz="CIUDAD", escena="INT. STREAM DE KAI — MINUTO 12",
             credito="Con: @probador")
    mp4 = tmp_path / "o.mp4"
    plan = mod_componer.componer(marca, t, [mod_componer.Linea(0.5, 3.0, "KAI", "Hola.")], mp4, duracion=8.0,
                                 trabajo=tmp_path / "w", fuente_video=src, inicio=1.0)
    ok, det = estilo.cuerpo_coincide(mp4, plan, marca)
    assert ok, det
    assert all(p[1] for p in estilo.revisar(mp4, plan, marca))


# ---------------------------------------------------------------- prueba de los ojos (la del workflow)

def test_prueba_ojos_sin_clave_sale_en_verde_sin_hacer_nada(tmp_path, capsys):
    from fabrica.editar import prueba_ojos
    assert prueba_ojos.correr(tmp_path / "o", entorno={}) == 0
    assert "sin GEMINI_API_KEY" in capsys.readouterr().out and not (tmp_path / "o").exists()


def test_prueba_ojos_rechaza_salida_dentro_del_repo():
    from fabrica.editar import prueba_ojos
    assert prueba_ojos.main(["--salida", str(RAIZ_REAL / "salida-x")]) == 2


@pytest.mark.slow
def test_prueba_ojos_con_respuesta_grabada_deja_mp4_png_json_e_informe(tmp_path, video20, monkeypatch):
    from fabrica.editar import muestras, prueba_ojos
    monkeypatch.setattr(muestras, "descargar", lambda url, esperado, destino: shutil.copy(video20, destino) and destino)
    rc = prueba_ojos.correr(tmp_path / "o", entorno={"GEMINI_API_KEY": "clave-de-prueba"},
                            transporte=transporte_con("valida"))
    nombres = {p.name for p in (tmp_path / "o").iterdir()}
    assert rc == 0, nombres
    assert {"ojos-sintel.mp4", "gemini.json", "informe.md", "ojos-sintel.plan.json"} <= nombres
    assert any(n.endswith(".png") for n in nombres)
    assert "clave-de-prueba" not in "".join((tmp_path / "o" / n).read_text(encoding="utf-8", errors="ignore")
                                            for n in ("gemini.json", "informe.md"))


# ---------------------------------------------------------------- ronda 1 de corrección

def test_el_opener_no_sigue_redirecciones():
    import urllib.request as ur
    h = mod_descargar._SinRedirecciones()
    req = ur.Request("https://ejemplo.invalid/a.mp4")
    assert h.redirect_request(req, None, 302, "Found", {}, "http://otro.invalid/b.mp4") is None


@pytest.mark.parametrize("destino", ["http://ejemplo.invalid/b.mp4", "https://otro-host.invalid/b.mp4"])
def test_redireccion_a_otro_host_o_a_http_se_rechaza_sin_descargar(repo, carpetas, monkeypatch, destino):
    import urllib.request as ur
    import urllib.response as resp

    class Falso(ur.HTTPSHandler):
        visitas: list[str] = []

        def https_open(self, req):
            Falso.visitas.append(req.full_url)
            r = resp.addinfourl(io.BytesIO(b""), {"Location": destino}, req.full_url, 302)
            r.msg = "Found"
            return r

    monkeypatch.setattr(mod_descargar, "_opener", lambda: ur.build_opener(mod_descargar._SinRedirecciones, Falso))
    with pytest.raises(mod_descargar.ErrorDescarga, match="redirección"):
        with mod_descargar.descargar(repo, candidato(), ficha_propia(), HOY):
            pass
    assert Falso.visitas == [URL]                       # sólo se abrió la URL autorizada
    assert not any(c.exists() for c in carpetas)


def test_instruccion_de_la_muestra_pide_el_juego_esperado_y_no_gta(marca, temporada):
    from fabrica.editar import prueba_ojos
    texto = ojos.instruccion(marca, temporada, 52.0, prueba_ojos.JUEGO_MUESTRA)
    assert "Sintel" in texto and "GTA 6" not in texto


def test_validar_respuesta_nombra_el_juego_esperado(marca, temporada):
    d = cargar_dato("juego_incorrecto")
    ok, m = ojos.validar_respuesta(d, marca, temporada, duracion=20.0, juego="Sintel, cortometraje de Blender")
    assert not ok and any("Sintel" in x for x in m)


def test_prueba_ojos_manda_a_gemini_la_instruccion_de_la_muestra(tmp_path, video20, monkeypatch):
    from fabrica.editar import muestras, prueba_ojos
    monkeypatch.setattr(muestras, "descargar", lambda url, esperado, destino: shutil.copy(video20, destino) and destino)
    t = transporte_con("juego_incorrecto")
    prueba_ojos.correr(tmp_path / "o", entorno={"GEMINI_API_KEY": "clave-de-prueba"}, transporte=t)
    texto = json.loads(t.llamadas[0][2])["contents"][0]["parts"][1]["text"]
    assert "Sintel" in texto and "GTA 6" not in texto


def test_el_tope_se_mide_sobre_el_json_serializado(tmp_path, monkeypatch):
    monkeypatch.setattr(ojos, "TOPE_PETICION_BYTES", 3000)
    t = Transporte()
    m = mirar(tmp_path, t, video_bytes=b"\x00" * 2500)          # 2.5 KB crudos, más de 3 KB en base64
    assert m.datos is None and "serializada" in m.motivo and t.llamadas == []


def test_config_ojos_baja_a_14_mb():
    cfg = yaml.safe_load((RAIZ_REAL / "config" / "ojos.yml").read_text(encoding="utf-8"))
    assert cfg["max_mb_en_linea"] == 14
    assert cfg["max_mb_en_linea"] * 1024 * 1024 * 4 / 3 < ojos.TOPE_PETICION_BYTES


def test_anotar_clip_es_idempotente_tras_un_fallo_entre_las_dos_escrituras(repo, monkeypatch):
    c = candidato(id_origen="clip-reintento")
    original = puertas.anotar_visto
    monkeypatch.setattr(puertas, "anotar_visto", lambda *a: (_ for _ in ()).throw(OSError("disco lleno")))
    with pytest.raises(OSError):
        registro.anotar_clip(repo, c, {"toma": 9, "plataforma": "shorts"})
    monkeypatch.setattr(puertas, "anotar_visto", original)
    registro.anotar_clip(repo, c, {"toma": 9, "plataforma": "shorts"})
    filas = [f for f in puertas._filas_marcador(repo) if f["id_clip"] == "clip-reintento"]
    assert len(filas) == 1
    assert "clip-reintento" in (repo / "estado" / "vistos.txt").read_text(encoding="utf-8").split()
    registro.anotar_clip(repo, c, {"toma": 9, "plataforma": "tiktok"})     # otra plataforma, otra fila
    assert len([f for f in puertas._filas_marcador(repo) if f["id_clip"] == "clip-reintento"]) == 2


# ---------------------------------------------------------------- ronda 1: hallazgos de Haiku

def test_excepcion_inesperada_es_descarte_y_la_carpeta_propia_se_borra(repo, monkeypatch):
    import tempfile as tf
    creadas = []
    real = toma.tempfile.mkdtemp
    monkeypatch.setattr(toma.tempfile, "mkdtemp", lambda *a, **k: creadas.append(real(*a, **k)) or creadas[-1])

    def rota(*a, **k):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "inválido")
    monkeypatch.setattr(toma.mod_descargar, "descargar", rota)
    r = toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA)
    assert not r.ok and "error inesperado: UnicodeDecodeError" in r.motivos[0]
    assert creadas and not any(Path(c).exists() for c in creadas)


def test_incomplete_read_durante_la_descarga_no_sube(repo):
    import http.client

    class Cortada(Resp):
        def read(self, n=-1):
            raise http.client.IncompleteRead(b"abc")
    r = toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, abrir=lambda u: Cortada(b"x"),
                        entorno={"GEMINI_API_KEY": "x"})
    assert not r.ok and r.motivos


def test_redireccion_real_de_un_servidor_local_se_rechaza(tmp_path, monkeypatch):
    import http.server
    import threading
    visitas = []

    class Destino(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            visitas.append(self.path)
            self.send_response(200); self.send_header("Content-Type", "video/mp4"); self.end_headers()
            self.wfile.write(b"x" * 100)

        def log_message(self, *a):
            pass

    destino = http.server.HTTPServer(("127.0.0.1", 0), Destino)

    class Origen(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destino.server_address[1]}/robado.mp4")
            self.end_headers()

        def log_message(self, *a):
            pass

    origen = http.server.HTTPServer(("127.0.0.1", 0), Origen)
    hilos = [threading.Thread(target=s.serve_forever, daemon=True) for s in (destino, origen)]
    for h in hilos:
        h.start()
    try:
        monkeypatch.setattr(mod_descargar, "_es_https", lambda u: True)     # el servidor local no habla TLS
        with pytest.raises(mod_descargar.ErrorDescarga, match="redirección"):
            mod_descargar.bajar_https(f"http://127.0.0.1:{origen.server_address[1]}/a.mp4", tmp_path / "a.mp4")
    finally:
        for s in (destino, origen):
            s.shutdown(); s.server_close()
    assert visitas == [] and not (tmp_path / "a.mp4").exists()


def test_respuesta_de_sintel_con_juego_incorrecto_se_descarta_y_la_propia_pasa(marca, temporada):
    from fabrica.editar import prueba_ojos
    juego = prueba_ojos.JUEGO_MUESTRA
    ok, m = ojos.validar_respuesta(cargar_dato("sintel_juego_incorrecto"), marca, temporada, duracion=60.0, juego=juego)
    assert not ok and any("Sintel" in x for x in m)
    assert ojos.validar_respuesta(cargar_dato("sintel_valida"), marca, temporada, duracion=60.0, juego=juego) == (True, [])


# ---------------------------------------------------------------- ronda 2

def test_prompt_de_la_muestra_aclara_cinematica_y_el_de_produccion_conserva_el_veto(marca, temporada):
    from fabrica.editar import prueba_ojos
    muestra = ojos.instruccion(marca, temporada, 52.0, prueba_ojos.JUEGO_MUESTRA, prueba_ojos.NOTA_MUESTRA)
    prod = ojos.instruccion(marca, temporada, 52.0)
    assert "escena sin transformación editorial" in muestra and "NO cuenta como cinemática aislada" in muestra
    assert "CC BY" in muestra and "cinemática aislada" in muestra
    assert "sin transformación editorial" not in prod and "{{" not in prod and "{{" not in muestra
    assert "una cinemática aislada" in prod and "GTA 6" in prod


def test_prueba_ojos_envia_la_nota_de_la_muestra(tmp_path, video20, monkeypatch):
    from fabrica.editar import muestras, prueba_ojos
    monkeypatch.setattr(muestras, "descargar", lambda url, esperado, destino: shutil.copy(video20, destino) and destino)
    t = transporte_con("juego_incorrecto")
    prueba_ojos.correr(tmp_path / "o", entorno={"GEMINI_API_KEY": "clave-de-prueba"}, transporte=t)
    assert "escena sin transformación editorial" in json.loads(t.llamadas[0][2])["contents"][0]["parts"][1]["text"]


def test_mkdtemp_que_falla_da_resultado_no_ok(repo, monkeypatch):
    def falla(*a, **k):
        raise OSError("sin espacio")
    monkeypatch.setattr(toma.tempfile, "mkdtemp", falla)
    r = toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA)
    assert not r.ok and "error inesperado: OSError" in r.motivos[0]


def test_anotar_clip_devuelve_la_fila_persistida_en_un_reintento_con_otros_datos(repo):
    c = candidato(id_origen="clip-persistida")
    registro.anotar_clip(repo, c, {"toma": 9, "plataforma": "shorts"})
    devuelta = registro.anotar_clip(repo, c, {"toma": 10, "plataforma": "shorts"})
    filas = [f for f in puertas._filas_marcador(repo) if f["id_clip"] == "clip-persistida"]
    assert len(filas) == 1 and filas[0]["toma"] == "9" and str(devuelta["toma"]) == "9"
    assert devuelta["id_clip"] == "clip-persistida" and devuelta["notas"] == "origen:clip-persistida"


def test_anotar_clip_acepta_la_forma_corta_de_publicar(repo):
    registro.anotar_clip(repo, {"toma": 3, "plataforma": "shorts"})                       # sin id: sólo la fila
    registro.anotar_clip(repo, {"toma": 4, "plataforma": "shorts", "id_clip": "clip-corto"})
    registro.anotar_clip(repo, {"toma": 4, "plataforma": "shorts", "id_clip": "clip-corto"})
    filas = puertas._filas_marcador(repo)
    assert [f["toma"] for f in filas] == ["3", "4"] and filas[1]["notas"] == "origen:clip-corto"
    assert "clip-corto" in (repo / "estado" / "vistos.txt").read_text(encoding="utf-8").split()


def test_gemini_caido_no_gasta_la_fuente(repo, tmp_path, video20):
    e = ojos.ErrorOjos("fallo de conexión con Gemini: TimeoutError")
    r = toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, salida=tmp_path / "s", abrir=abrir_con(video20),
                        transporte=Transporte(e, e, e, e), entorno={"GEMINI_API_KEY": "k"}, espera=lambda s: None)
    assert not r.ok and "pasajero" in r.motivos[0]
    vistos = repo / "estado" / "vistos.txt"
    assert candidato().id_origen not in (vistos.read_text(encoding="utf-8").split() if vistos.exists() else [])


def test_un_descarte_por_contenido_si_gasta_la_fuente(repo, tmp_path, video20):
    r = toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, salida=tmp_path / "s", abrir=abrir_con(video20),
                        transporte=Transporte((400, "mala")), entorno={"GEMINI_API_KEY": "k"}, espera=lambda s: None)
    assert not r.ok
    assert candidato().id_origen in (repo / "estado" / "vistos.txt").read_text(encoding="utf-8").split()

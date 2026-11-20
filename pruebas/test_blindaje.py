"""Blindaje del ciclo: plazo, log por etapas, regla de gasto de la fuente y contrato del workflow.

Origen: la corrida 44609108976 se colgó 20 min sin una línea de log y dejó la fuente ERIC ZZETA gastada sin
publicar. Todo aquí es sin red ni claves reales y rápido (los cuelgues se simulan con `time.sleep`).
"""
import functools
import io
import re
import subprocess
import time
import urllib.error
from pathlib import Path

import pytest
import yaml

import falsos_n5 as F
from fabrica import ciclo, descargar as mod_descargar, ojos, plazo, publicar, red, registro
from fabrica.editar import toma
from fabrica.editar.marca import Marca
from fabrica.simulacion import BufferSimulado, SupabaseSimulado
from test_n4 import (AHORA as AHORA_N4, Resp, Transporte, candidato, cfg_antigua_de_ojos, config_antigua,
                     ficha_propia, sobre)
from test_n6_ciclo import (AHORA, ENV, FICHA, VERDE, Hacer, armar_falso, cambiar_modo, cand, correr, filas,
                           vistos)

RAIZ_REAL = Path(__file__).resolve().parent.parent
ID_ERIC = "1pvvTm9uKcrd_BjN1SMF7048FGn1Ni2vH"


@pytest.fixture(autouse=True)
def sin_plazo():
    """Que ningún plazo ni sumidero de log se filtre de una prueba a otra."""
    plazo.detener()
    plazo.registrar(None)
    yield
    plazo.detener()
    plazo.registrar(None)


@pytest.fixture
def rb(repo):
    """Como el fixture `r` de test_n6 (vivo, shorts activo, canales confirmados), con la ficha con `material`."""
    (repo / "PAUSA").unlink()
    (repo / "config" / "canales.yml").write_text(
        "".join(f"{p}: {{servicio: {s}, nombre_esperado: guionvivo, buffer_channel_id: null, buffer_org_id: null}}\n"
                for p, s in (("shorts", "youtube"), ("tiktok", "tiktok"), ("reels", "instagram"))),
        encoding="utf-8")
    publicar.guardar_ids_de_canales(
        repo, {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"},
        dict.fromkeys(("shorts", "tiktok", "reels"), "org-ejemplo-001"))
    cambiar_modo(repo, "vivo", ["shorts"])
    ficha = FICHA.replace("  publicable: true", "  publicable: true\n  material:\n"
                          "    - url: https://ejemplo.invalid/clip-1\n      creador: \"@ejemplo\"")
    with open(repo / "fuentes.yml", "a", encoding="utf-8") as f:
        f.write(ficha)
    return repo


@pytest.fixture(scope="module")
def video8(tmp_path_factory):
    """MP4 pequeño de 8 s (más que el mínimo de 7 s de la ficha de prueba) con audio."""
    ruta = tmp_path_factory.mktemp("blindaje") / "pequeno.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=15:duration=8", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(ruta)], check=True)
    return ruta


class RespColgada:
    """Una respuesta que anuncia video y luego no entrega ni un byte: `read()` duerme."""

    def __init__(self, segundos=60):
        self.headers = {"Content-Type": "video/mp4"}
        self.segundos = segundos

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        time.sleep(self.segundos)
        return b""


def abrir_colgado(url):
    return RespColgada()


def correr_colgado(rb, monkeypatch, *, hacer, limite_s, alarma_s, cands=None):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    sal = []
    t0 = time.monotonic()
    cod = ciclo.ejecutar(rb, ahora=AHORA, env=ENV, imprimir=sal.append, validar_fn=VERDE,
                         detectar_fn=lambda raiz, a, c: (list(cands or [cand("clip-1")]), []),
                         hacer_toma_fn=hacer, transporte_buffer=(buf := BufferSimulado()),
                         transporte_almacen=SupabaseSimulado(), limite_s=limite_s, alarma_s=alarma_s)
    return cod, "\n".join(sal), time.monotonic() - t0, buf


# ---------------------------------------------------------------- 1. el plazo

def test_sin_plazo_tiempo_para_devuelve_el_tope():
    assert not plazo.activo() and plazo.restante() == float("inf")
    assert plazo.tiempo_para(60) == 60 and plazo.queda(10 ** 9)


def test_tiempo_para_recorta_al_restante_y_lanza_al_agotarse():
    plazo.iniciar(30)
    t = plazo.tiempo_para(60)
    assert 24 < t <= 25                              # 30 s menos el margen de 5 s
    assert plazo.tiempo_para(10) == 10               # un tope menor no se toca
    plazo.iniciar(6)
    with pytest.raises(plazo.SinTiempo):             # quedan 1 s libres y hacen falta 5
        plazo.tiempo_para(60)
    with pytest.raises(plazo.SinTiempo):
        plazo.tiempo_para(60, minimo=2)
    plazo.iniciar(0)
    with pytest.raises(plazo.SinTiempo):
        plazo.tiempo_para(1, minimo=0)


def test_inicio_epoch_descuenta_lo_ya_pasado():
    plazo.iniciar(100, inicio_epoch=time.time() - 90)
    assert 9 < plazo.restante() <= 10 and plazo.transcurrido() >= 90
    assert not plazo.queda(60)
    with pytest.raises(plazo.SinTiempo):
        plazo.tiempo_para(60)
    plazo.iniciar(100, inicio_epoch=time.time() + 50)         # un reloj adelantado no da tiempo de más
    assert plazo.restante() <= 100


def test_iniciar_sin_limite_apaga_el_plazo():
    plazo.iniciar(5)
    plazo.iniciar(None)
    assert not plazo.activo()


def test_las_constantes_del_plazo_son_coherentes():
    assert plazo.LIMITE_CICLO_S < plazo.ALARMA_S < 15 * 60


# ---------------------------------------------------------------- 2. la alarma dura

def test_la_alarma_dura_corta_un_cuelgue():
    t0 = time.monotonic()
    with pytest.raises(plazo.SinTiempo, match="alarma dura"):
        with plazo.alarma(0.5):
            time.sleep(10)
    assert time.monotonic() - t0 < 3


def test_la_alarma_se_apaga_al_salir():
    with plazo.alarma(5):
        pass
    time.sleep(0.05)                                  # si quedara armada, aquí no pasaría nada visible
    import signal
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


# ---------------------------------------------------------------- 3. el log por etapas

def test_etapa_imprime_inicio_y_fin_con_segundos():
    log = []
    plazo.registrar(log.append)
    with plazo.etapa("prueba"):
        pass
    assert re.fullmatch(r"\[etapa\] prueba: inicio \(ciclo \d+\.\d s\)", log[0])
    assert re.fullmatch(r"\[etapa\] prueba: fin en \d+\.\d s \(ok\)", log[1])


def test_etapa_imprime_el_fin_tambien_con_excepcion_y_no_se_la_traga():
    log = []
    plazo.registrar(log.append)
    with pytest.raises(ValueError):
        with plazo.etapa("rota"):
            raise ValueError("x")
    assert re.fullmatch(r"\[etapa\] rota: fin en \d+\.\d s \(error: ValueError\)", log[-1])


def test_la_salida_del_ciclo_imprime_con_flush(capsys):
    ciclo.Salida()("una línea")
    assert capsys.readouterr().out == "una línea\n"
    import inspect
    assert "flush=True" in inspect.getsource(ciclo.imprimir_con_flush)


# ---------------------------------------------------------------- 4. el cuelgue simulado (criterio F)

def _comprobar_sin_gasto(rb, sal, buf):
    assert "ERIC" not in sal
    assert "clip-1" not in vistos(rb)                              # la fuente NO se gastó
    assert filas(rb) == [] and not buf.hubo("createPost")          # nada se programó
    assert not (rb / "PAUSA").exists()                             # ni alerta roja ni pausa
    assert "[etapa] descarga: inicio" in sal
    assert re.search(r"\[etapa\] descarga: fin en \d+\.\d s", sal)


def test_cuelgue_en_la_descarga_la_alarma_dura_corta_y_la_fuente_no_se_gasta(rb, monkeypatch):
    hacer = functools.partial(toma.hacer_toma, abrir=abrir_colgado)
    cod, sal, seg, buf = correr_colgado(rb, monkeypatch, hacer=hacer, limite_s=600, alarma_s=2)
    assert cod == 0 and seg < 8, (seg, sal)
    assert "SIN TIEMPO" in sal and "alarma dura" in sal and "la fuente no se gasta" in sal
    _comprobar_sin_gasto(rb, sal, buf)
    assert "sin tiempo, no se publicó" in (rb / "registro.md").read_text(encoding="utf-8")


def test_cuelgue_en_la_descarga_el_plazo_cooperativo_la_corta_y_la_fuente_no_se_gasta(rb, monkeypatch):
    monkeypatch.setattr(ciclo, "TIEMPO_MIN_INTENTO_S", 0)          # con plazo de 2 s no cabría el intento de 240 s
    hacer = functools.partial(toma.hacer_toma, abrir=abrir_colgado)
    cod, sal, seg, buf = correr_colgado(rb, monkeypatch, hacer=hacer, limite_s=2, alarma_s=4)
    assert cod == 0 and seg < 8, (seg, sal)
    assert "la fuente no se gasta (red, tiempo o máquina)" in sal or "SIN TIEMPO" in sal
    _comprobar_sin_gasto(rb, sal, buf)


def test_cuelgue_en_gemini_la_alarma_dura_corta_y_la_fuente_no_se_gasta(rb, monkeypatch, video8):
    def gemini_colgado(url, encabezados, cuerpo):
        time.sleep(60)

    hacer = functools.partial(toma.hacer_toma, abrir=lambda url: Resp(video8.read_bytes()),
                              transporte=gemini_colgado, entorno={"GEMINI_API_KEY": "clave-de-prueba"})
    cod, sal, seg, buf = correr_colgado(rb, monkeypatch, hacer=hacer, limite_s=600, alarma_s=3)
    assert cod == 0 and seg < 10, (seg, sal)
    assert "[etapa] ojos (Gemini): inicio" in sal and re.search(r"\[etapa\] ojos \(Gemini\): fin en", sal)
    assert "SIN TIEMPO" in sal
    _comprobar_sin_gasto(rb, sal, buf)


def test_sin_tiempo_para_otro_intento_corta_el_bucle_sin_tocar_la_fuente(rb, monkeypatch):
    hacer = Hacer(rb)
    cod, sal, seg, buf = correr_colgado(rb, monkeypatch, hacer=hacer, limite_s=100, alarma_s=200)
    assert cod == 0 and "sin tiempo para otro intento" in sal and hacer.llamadas == []
    assert "clip-1" not in vistos(rb)


def test_sin_tiempo_para_publicar_no_publica_ni_gasta_la_fuente(rb, monkeypatch):
    monkeypatch.setattr(ciclo, "TIEMPO_MIN_INTENTO_S", 0)
    monkeypatch.setattr(ciclo, "TIEMPO_MIN_PUBLICAR_S", 10 ** 6)
    hacer = Hacer(rb)
    cod, sal, seg, buf = correr_colgado(rb, monkeypatch, hacer=hacer, limite_s=100, alarma_s=200)
    assert cod == 0 and "sin tiempo para publicar: la fuente no se gasta" in sal
    assert hacer.llamadas == ["clip-1"] and "clip-1" not in vistos(rb)
    assert filas(rb) == [] and not buf.hubo("createPost")
    assert "sin tiempo, no se publicó" in (rb / "registro.md").read_text(encoding="utf-8")


def test_un_sin_tiempo_residual_no_crea_pausa(rb, monkeypatch):
    def mantenimiento_colgado(*a, **k):
        raise plazo.SinTiempo("se acabó")
    monkeypatch.setattr(ciclo, "mantenimiento", mantenimiento_colgado)
    # sin candidatos y con un envío abierto en el libro: sólo corre el mantenimiento
    monkeypatch.setattr(ciclo, "hay_mantenimiento", lambda raiz: True)
    monkeypatch.setattr(ciclo, "buscar_hueco", lambda raiz, ahora, activas: None)
    cod, sal = correr(rb, cands=[])
    assert cod == 0 and "SIN TIEMPO" in sal and not (rb / "PAUSA").exists()


def test_un_sin_tiempo_en_el_detector_sube_y_no_se_traga(rb):
    def detector(raiz, ahora, cliente):
        raise plazo.SinTiempo("se acabó")
    with pytest.raises(plazo.SinTiempo):
        ciclo.elegir_candidatos(rb, AHORA, ["shorts"], lambda t: None, detector)


def test_ejecutar_deja_el_plazo_apagado_al_terminar(rb, monkeypatch):
    correr_colgado(rb, monkeypatch, hacer=Hacer(rb), limite_s=100, alarma_s=200)
    assert not plazo.activo()
    import signal
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


# ---------------------------------------------------------------- 4b. `mirar` bajo plazo corto

def _mirar(tmp_path, transporte, espera=lambda s: None):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"\x00" * 2048)
    temporada = yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))
    return ojos.mirar(v, Marca.cargar(RAIZ_REAL), temporada, raiz=RAIZ_REAL, duracion=20.0, transporte=transporte,
                      espera=espera, entorno={"GEMINI_API_KEY": "clave-de-prueba"}, config=cfg_antigua_de_ojos())


def test_mirar_sin_tiempo_termina_pasajero_sin_llamar_a_gemini(tmp_path):
    t = Transporte()
    plazo.iniciar(10)
    m = _mirar(tmp_path, t)
    assert m.datos is None and m.pasajero and "sin tiempo para Gemini" in m.motivo and t.llamadas == []


def test_mirar_con_transporte_que_duerme_termina_pasajero_y_rapido(tmp_path):
    llamadas = []

    def lento(url, encabezados, cuerpo):
        llamadas.append(1)
        time.sleep(1.5)
        raise ojos.ErrorOjos("fallo de conexión con Gemini: TimeoutError")

    plazo.iniciar(21)                    # 16 s libres: cabe el primer intento y el siguiente ya no (menos de 15 s)
    t0 = time.monotonic()
    m = _mirar(tmp_path, lento)
    assert time.monotonic() - t0 < 5 and len(llamadas) == 1
    assert m.datos is None and m.pasajero and not m.del_material and "sin tiempo para Gemini" in m.motivo


def test_mirar_acota_la_espera_entre_reintentos(tmp_path):
    esperas = []
    plazo.iniciar(25)                    # 20 s libres: tras el primer intento sobran pocos segundos
    t = Transporte((503, ""), (503, ""), (503, ""), (503, ""))
    m = _mirar(tmp_path, t, espera=esperas.append)
    assert m.pasajero and all(e <= ojos.ESPERA_REINTENTO_S for e in esperas)
    assert esperas and esperas[0] <= 5.0 + 1e-6


def test_mirar_pasa_el_tiempo_calculado_al_transporte_por_defecto(tmp_path, monkeypatch):
    vistos_t = []

    def falso(url, encabezados, cuerpo, tiempo=None):
        vistos_t.append(tiempo)
        return 200, sobre("{}")
    monkeypatch.setattr(ojos, "transporte_urllib", falso)
    plazo.iniciar(40)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"\x00" * 2048)
    temporada = yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))
    ojos.mirar(v, Marca.cargar(RAIZ_REAL), temporada, raiz=RAIZ_REAL, duracion=20.0, transporte=None,
               espera=lambda s: None, entorno={"GEMINI_API_KEY": "clave-de-prueba"})
    assert vistos_t and 30 < vistos_t[0] <= 35      # 40 s del plazo menos el margen; menor que los 120 s por petición


def test_mirar_clasifica_si_el_fallo_es_del_material(tmp_path):
    assert _mirar(tmp_path, Transporte((400, "mala"))).del_material is True
    assert _mirar(tmp_path, Transporte((200, "no es json"))).del_material is True
    assert _mirar(tmp_path, Transporte((200, "{}"))).del_material is True            # sin candidatos
    pas = _mirar(tmp_path, Transporte((503, ""), (503, ""), (503, ""), (503, "")))
    assert pas.pasajero and pas.del_material is False
    assert _mirar(tmp_path, Transporte((404, ""), (404, ""), (404, ""), (404, ""))).del_material is False


# ---------------------------------------------------------------- 5. regla de gasto

def _vistos_de(repo):
    ruta = repo / "estado" / "vistos.txt"
    return ruta.read_text(encoding="utf-8").split() if ruta.exists() else []


def _toma(repo, tmp_path, ficha=None, **kw):
    return toma.hacer_toma(repo, candidato(), ficha or ficha_propia(), AHORA_N4, salida=tmp_path / "s",
                           entorno={"GEMINI_API_KEY": "k"}, espera=lambda s: None, **kw)


def test_fallo_de_conexion_no_gasta_la_fuente(repo, tmp_path):
    def sin_red(url):
        raise urllib.error.URLError("sin red")
    r = _toma(repo, tmp_path, abrir=sin_red)
    assert not r.ok and r.gastar is False and "fallo de conexión" in r.motivos[0]


def test_http_404_es_del_material_y_http_500_no(repo, tmp_path):
    def con_codigo(c):
        def abrir(url):
            raise urllib.error.HTTPError(url, c, "x", {}, io.BytesIO(b""))
        return abrir
    assert _toma(repo, tmp_path, abrir=con_codigo(404)).gastar is True
    assert _toma(repo, tmp_path, abrir=con_codigo(500)).gastar is False


def test_duracion_menor_que_el_minimo_gasta_la_fuente(repo, tmp_path, video8):
    ficha = ficha_propia(reglas={"duracion_s": [25, 30], "plataformas": ["shorts"]})
    r = _toma(repo, tmp_path, ficha=ficha, abrir=lambda url: Resp(video8.read_bytes()))
    assert not r.ok and r.gastar is True and "menos que el mínimo" in r.motivos[0]


def test_la_descarga_que_no_es_video_gasta_y_la_pagina_de_cuota_de_drive_no(repo, tmp_path):
    assert _toma(repo, tmp_path, abrir=lambda url: Resp(b"<html>x</html>", tipo="text/html")).gastar is True
    assert _toma(repo, tmp_path, abrir=lambda url: Resp(b"", tipo="video/mp4")).gastar is True     # vacía
    ficha = ficha_propia(tipo="C")
    c = candidato(url="https://drive.usercontent.google.com/download?id=abc&export=download")
    pagina = b"<html><title>Cuota excedida</title></html>"
    with pytest.raises(mod_descargar.ErrorDescarga) as e:
        with mod_descargar.descargar(repo, c, ficha, AHORA_N4.date(),
                                     abrir=lambda url: Resp(pagina, tipo="text/html")):
            pass
    assert e.value.del_material is False


def test_gemini_pasajero_no_gasta_y_sin_clave_tampoco(repo, tmp_path, video8):
    config_antigua(repo)
    err = ojos.ErrorOjos("fallo de conexión con Gemini: TimeoutError")
    r = _toma(repo, tmp_path, abrir=lambda url: Resp(video8.read_bytes()), transporte=Transporte(err, err, err, err))
    assert not r.ok and r.gastar is False and "pasajero" in r.motivos[0]
    r = toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA_N4, salida=tmp_path / "s2",
                        abrir=lambda url: Resp(video8.read_bytes()), entorno={}, espera=lambda s: None)
    assert not r.ok and r.gastar is False and "sin GEMINI_API_KEY" in r.motivos[0]


def test_gemini_400_y_respuesta_invalida_gastan_la_fuente(repo, tmp_path, video8):
    abrir = lambda url: Resp(video8.read_bytes())          # noqa: E731
    assert _toma(repo, tmp_path, abrir=abrir, transporte=Transporte((400, "mala"))).gastar is True
    r = _toma(repo, tmp_path, abrir=abrir, transporte=Transporte((200, sobre('{"apto": true}'))))
    assert not r.ok and r.gastar is True


def test_un_error_inesperado_no_gasta_la_fuente(repo, tmp_path):
    def raro(url):
        raise RuntimeError("algo que no esperábamos")
    r = _toma(repo, tmp_path, abrir=raro)
    assert not r.ok and r.gastar is False and "error inesperado" in r.motivos[0]


def test_hacer_toma_deja_pasar_el_sin_tiempo(repo, tmp_path):
    def sin_tiempo(url):
        raise plazo.SinTiempo("se acabó")
    with pytest.raises(plazo.SinTiempo):
        _toma(repo, tmp_path, abrir=sin_tiempo)


def test_el_ciclo_no_gasta_la_fuente_por_un_fallo_de_conexion_real(rb, monkeypatch):
    def sin_red(url):
        raise urllib.error.URLError("sin red")
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    cod, sal = correr(rb, cands=[cand("clip-1")], hacer=functools.partial(toma.hacer_toma, abrir=sin_red))
    assert "la fuente no se gasta (red, tiempo o máquina)" in sal and "clip-1" not in vistos(rb)


def test_el_ciclo_gasta_la_fuente_por_un_fallo_del_material(rb, monkeypatch, video8):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    cod, sal = correr(rb, cands=[cand("clip-1")], hacer=Hacer(rb, oks=(False,), motivo="descarga: Content-Type",
                                                              gastar=True))
    assert "la fuente se gasta (fallo del material)" in sal and "clip-1" in vistos(rb)


def test_toma_programada_gasta_la_fuente(rb):
    cod, sal = correr(rb, cands=[cand("c1")])
    assert "shorts: programada" in sal and "c1" in vistos(rb)


def test_descargar_ya_no_reserva_la_fuente_y_la_descarga_lenta_no_es_del_material(repo, monkeypatch):
    monkeypatch.setattr(mod_descargar, "TOPE_DESCARGA_S", 0.2)

    class Lenta(RespColgada):
        def read(self, n=-1):
            time.sleep(0.3)
            return b"x" * 10

    with pytest.raises(mod_descargar.ErrorDescarga, match="tiempo límite") as e:
        with mod_descargar.descargar(repo, candidato(), ficha_propia(), AHORA_N4.date(), abrir=lambda url: Lenta()):
            pass
    assert e.value.del_material is False and "brief-n4-001" not in _vistos_de(repo)


def test_la_orden_de_ytdlp_lleva_socket_timeout():
    orden = mod_descargar.orden_ytdlp("https://clips.twitch.tv/AbcDef", Path("/tmp/x"))
    assert orden[orden.index("--socket-timeout") + 1] == "30"


def test_yt_dlp_con_tiempo_agotado_no_es_del_material(tmp_path):
    def ejecutor(orden, **kw):
        assert kw["timeout"] <= mod_descargar.TOPE_YTDLP_S
        raise subprocess.TimeoutExpired(orden, kw["timeout"])
    with pytest.raises(mod_descargar.ErrorDescarga) as e:
        mod_descargar.bajar_twitch("https://clips.twitch.tv/AbcDef", tmp_path, ejecutor=ejecutor)
    assert e.value.del_material is False


# ---------------------------------------------------------------- 6. red.solicitar bajo plazo

def test_solicitar_pasa_al_transporte_un_tiempo_dentro_del_plazo():
    vistos_t = []

    def transporte(metodo, url, cab, cuerpo, tiempo):
        vistos_t.append(tiempo)
        return 200, {}, b"{}"
    plazo.iniciar(30)
    red.solicitar(transporte, "GET", "https://ejemplo.invalid/x", {}, tiempo=60)
    assert 20 < vistos_t[0] <= 25


def test_solicitar_lanza_sin_tiempo_antes_de_enviar():
    llamadas = []
    plazo.iniciar(3)
    with pytest.raises(plazo.SinTiempo):
        red.solicitar(lambda *a: llamadas.append(a) or (200, {}, b""), "POST", "https://ejemplo.invalid/x", {})
    assert llamadas == []


def test_solicitar_sin_plazo_no_cambia_el_tiempo():
    vistos_t = []
    red.solicitar(lambda m, u, c, b, t: vistos_t.append(t) or (200, {}, b""), "GET", "https://ejemplo.invalid/x", {},
                  tiempo=42)
    assert vistos_t == [42]


def test_solicitar_acota_la_espera_del_retry_after():
    esperas = []
    respuestas = [(503, {"retry-after": "30"}, b""), (200, {}, b"")]
    plazo.iniciar(20)
    red.solicitar(lambda *a: respuestas.pop(0), "GET", "https://ejemplo.invalid/x", {}, dormir=esperas.append)
    assert esperas and esperas[0] <= 15


# ---------------------------------------------------------------- 7. contrato del workflow

def _workflow():
    return yaml.safe_load((RAIZ_REAL / ".github" / "workflows" / "fabrica.yml").read_text(encoding="utf-8"))


def test_workflow_sin_bufer_y_con_tope_en_el_paso_del_ciclo():
    wf = _workflow()
    for job in ("decidir", "producir"):
        assert wf["jobs"][job]["env"]["PYTHONUNBUFFERED"] == "1"
    paso = next(p for p in wf["jobs"]["producir"]["steps"] if p.get("name") == "Ciclo")
    assert paso["timeout-minutes"] <= 15
    assert wf["jobs"]["producir"]["timeout-minutes"] > paso["timeout-minutes"]      # margen para «Cerrar el ciclo»


def test_el_ciclo_usa_el_inicio_del_job_y_no_pone_plazo_a_las_otras_ordenes(monkeypatch):
    llamadas = []
    monkeypatch.setattr(ciclo, "ejecutar", lambda *a, **k: llamadas.append(k) or 0)
    monkeypatch.setenv("INICIO_S", "1234.5")
    ciclo.main([])
    assert llamadas[-1]["limite_s"] == plazo.LIMITE_CICLO_S and llamadas[-1]["alarma_s"] == plazo.ALARMA_S
    assert llamadas[-1]["inicio_epoch"] == 1234.5
    ciclo.main(["--decidir"])
    assert "limite_s" not in llamadas[-1] and "alarma_s" not in llamadas[-1]


# ---------------------------------------------------------------- 8. el estado real

def test_eric_zzeta_ya_no_esta_gastada_en_el_repo_real():
    texto = (RAIZ_REAL / "estado" / "vistos.txt").read_text(encoding="utf-8")
    assert ID_ERIC not in texto

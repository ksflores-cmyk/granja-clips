"""N3: lista blanca (es_publicable / puede_descargar), detector de Twitch y puertas baratas. Sin red."""
import dataclasses
import datetime as dt
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

import pytest
import yaml

from fabrica import detectar, puertas, validar
from fabrica.detectar import ClienteTwitch, ErrorTwitch
from fabrica.fuentes import cargar_fuentes, es_publicable, puede_descargar
from fabrica.registro import anotar_fila

DATOS = Path(__file__).parent / "datos" / "twitch"
UTC = dt.timezone.utc
AHORA = dt.datetime(2026, 11, 19, 22, 0, tzinfo=UTC)     # 16:00 en Guatemala, dentro del estreno
HOY = AHORA.astimezone(ZoneInfo("America/Guatemala")).date()
CLAVE_FALSA = "secreto-inventado"                          # EJEMPLO-FALSO


@pytest.fixture(autouse=True)
def _sin_campanas_reales(request):
    """El repo real ya trae la ficha de la campaña SEGA FM27 (publicable, con 5 videos de material). Estas pruebas
    cuentan los candidatos de sus propias fichas, así que parten del repo sin esa ficha."""
    if "repo" in request.fixturenames:
        repo = request.getfixturevalue("repo")
        ruta = repo / "fuentes.yml"
        fichas = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        ruta.write_text(yaml.safe_dump([f for f in fichas if f.get("id") != "sega-fm27"], allow_unicode=True),
                        encoding="utf-8")


def leer(nombre):
    return (DATOS / nombre).read_text(encoding="utf-8")


def base(**cambios):
    f = {"id": "canal-ejemplo", "tipo": "A", "creador": "@ejemplo", "origen": "twitch",
         "twitch_login": "canal_ejemplo", "permiso_url": "https://example.invalid/brief",
         "permiso_leido": dt.date(2026, 11, 17), "tarifa_usd_por_mil": 1.0,
         "reglas": {"duracion_s": [10, 60], "plataformas": ["tiktok", "shorts"]},
         "pilares": ["primeras_veces", "momentos"], "campana": "demo",
         "presupuesto_restante": 100, "vence": dt.date(2026, 12, 31),
         "estado": "vigente", "uso": "produccion", "publicable": True}
    f.update(cambios)
    return f


class Falso:
    """Transporte falso: sirve JSON grabados y cuenta llamadas."""
    def __init__(self, respuestas=None):
        self.llamadas = []
        self.respuestas = respuestas or {}

    def __call__(self, metodo, url, encabezados, cuerpo=None):
        self.llamadas.append((metodo, url, dict(encabezados), cuerpo))
        ruta = urlparse(url).path
        r = self.respuestas.get(ruta)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if r is not None:
            return r
        return {"/oauth2/token": (200, leer("oauth.json")),
                "/helix/users": (200, leer("users.json")),
                "/helix/games": (200, leer("games.json")),
                "/helix/clips": (200, leer("clips.json"))}[ruta]


def cliente(falso=None, **kw):
    esperas = []
    c = ClienteTwitch("id-falso", CLAVE_FALSA, falso or Falso(), espera=esperas.append, **kw)
    c.esperas = esperas
    return c


# ---- es_publicable ----

def test_publicable_ok():
    assert es_publicable(base(), HOY, "tiktok", "momentos") == (True, "ok")


@pytest.mark.parametrize("cambios,fragmento", [
    ({"estado": "vencida"}, "estado"),
    ({"uso": "muestras"}, "uso"),
    ({"publicable": False}, "publicable"),
    ({"vence": dt.date(2026, 11, 1)}, "vencida el"),
    ({"presupuesto_restante": 0}, "presupuesto"),
    ({"presupuesto_restante": "agotado"}, "presupuesto"),
])
def test_no_publicable_motivo(cambios, fragmento):
    ok, motivo = es_publicable(base(**cambios), HOY)
    assert not ok and fragmento in motivo


def test_vence_hoy_aun_vale_y_nulo_vale():
    assert es_publicable(base(vence=HOY), HOY)[0]
    assert es_publicable(base(vence=None), HOY)[0]


def test_presupuesto_por_leer_no_bloquea():
    assert es_publicable(base(presupuesto_restante="por leer"), HOY)[0]


def test_plataforma_fuera_de_reglas():
    ok, m = es_publicable(base(), HOY, "reels")
    assert not ok and "reels" in m


def test_pilar_fuera_de_la_ficha():
    ok, m = es_publicable(base(), HOY, "tiktok", "dato_util")
    assert not ok and "pilar" in m


def test_tipo_d_solo_informativa():
    d = base(tipo="D", pilares=["informativa"])
    assert es_publicable(d, HOY, None, "informativa")[0]
    ok, m = es_publicable(d, HOY, None, "momentos")
    assert not ok and "tipo D" in m


# ---- puede_descargar ----

def test_descarga_tipo_c_si():
    assert puede_descargar(base(tipo="C"))[0]


def test_descarga_tipo_d_no():
    ok, m = puede_descargar(base(tipo="D"))
    assert not ok and "tipo D" in m


def test_descarga_a_sin_permiso_no():
    ok, m = puede_descargar(base())
    assert not ok and "permite_descarga" in m


def test_descarga_a_con_permite_descarga():
    f = base()
    f["reglas"]["permite_descarga"] = True
    assert puede_descargar(f)[0]


def test_descarga_b_con_material_https():
    assert puede_descargar(base(tipo="B", material=["https://example.invalid/v.mp4"]))[0]


def test_descarga_material_http_no_cuenta():
    assert not puede_descargar(base(tipo="B", material=["http://example.invalid/v.mp4"]))[0]


# ---- cliente de Twitch ----

def test_token_usa_post_y_no_imprime_claves(capsys):
    f = Falso()
    c = cliente(f)
    assert c.token_aplicacion("id-falso", CLAVE_FALSA) == "token-inventado-para-pruebas"
    assert f.llamadas[0][0] == "POST" and f.llamadas[0][1] == "https://id.twitch.tv/oauth2/token"
    assert b"client_credentials" in f.llamadas[0][3]
    assert CLAVE_FALSA not in repr(c)
    out = capsys.readouterr()
    assert CLAVE_FALSA not in out.out + out.err


def test_encabezados_client_id_y_bearer():
    f = Falso()
    cliente(f).usuarios(["canal_ejemplo"])
    h = f.llamadas[-1][2]
    assert h["Client-Id"] == "id-falso" and h["Authorization"] == "Bearer token-inventado-para-pruebas"


def test_usuarios_devuelve_id():
    assert cliente().usuarios(["Canal_Ejemplo"]) == {"canal_ejemplo": "900100"}


def test_juego_id_y_url():
    f = Falso()
    assert cliente(f).juego_id("Grand Theft Auto VI") == "777001"
    q = parse_qs(urlparse(f.llamadas[-1][1]).query)
    assert q["name"] == ["Grand Theft Auto VI"]


def test_juego_id_desconocido_es_none():
    f = Falso({"/helix/games": (200, '{"data": []}')})
    assert cliente(f).juego_id("No existe") is None


def test_reintento_unico_ante_429():
    f = Falso({"/helix/users": [(429, "{}"), (200, leer("users.json"))]})
    c = cliente(f)
    assert c.usuarios(["canal_ejemplo"]) == {"canal_ejemplo": "900100"}
    assert len(c.esperas) == 1


def test_reintento_ante_5xx_y_luego_falla():
    f = Falso({"/helix/users": (503, "{}")})
    c = cliente(f)
    with pytest.raises(ErrorTwitch):
        c.usuarios(["canal_ejemplo"])
    assert len([x for x in f.llamadas if x[1].startswith("https://api.twitch.tv/helix/users")]) == 2


def test_error_401_no_reintenta():
    f = Falso({"/helix/users": (401, "{}")})
    with pytest.raises(ErrorTwitch):
        cliente(f).usuarios(["x"])
    assert len([x for x in f.llamadas if "helix/users" in x[1]]) == 1


def test_clips_pide_ventana_y_primeros():
    f = Falso()
    cliente(f).clips("900100", AHORA - dt.timedelta(hours=24), AHORA)
    q = parse_qs(urlparse(f.llamadas[-1][1]).query)
    assert q["broadcaster_id"] == ["900100"] and q["first"] == ["20"]
    assert q["started_at"] == ["2026-11-18T22:00:00Z"] and q["ended_at"] == ["2026-11-19T22:00:00Z"]


# ---- detectar ----

@pytest.fixture
def repo_twitch(repo, editar_yaml):
    def f(d):
        d.append(base())
        return d
    editar_yaml(repo, "fuentes.yml", f)
    return repo


def test_detectar_ordena_por_vistas_por_hora(repo_twitch):
    cands = detectar.detectar(repo_twitch, AHORA, cliente())
    ids = [c.id_origen for c in cands]
    assert ids[0] == "ClipOtroJuego" and ids.index("ClipRapidoDos") < ids.index("ClipLentoUno")
    assert [c.vistas_por_hora for c in cands] == sorted([c.vistas_por_hora for c in cands], reverse=True)


def test_vistas_por_hora_minimo_una_hora(repo_twitch):
    cands = {c.id_origen: c for c in detectar.detectar(repo_twitch, AHORA, cliente())}
    assert cands["ClipRapidoDos"].vistas_por_hora == 900 / 2       # 2 h
    assert cands["ClipCorto"].vistas_por_hora == 50 / 1             # 30 min cuenta como 1 h
    assert cands["ClipCorto"].duracion_s == 3.0 and cands["ClipLentoUno"].vod_offset == 3600


def test_detectar_sin_claves_salta_twitch_y_sigue(repo_twitch, editar_yaml, monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)

    def f(d):
        d.append(base(id="con-brief", origen="web", twitch_login=None, tipo="B",
                      material=["https://example.invalid/a.mp4"]))
        return d
    editar_yaml(repo_twitch, "fuentes.yml", f)
    cands, saltos = detectar.detectar_con_motivos(repo_twitch, AHORA)
    assert [c.origen for c in cands] == ["brief"]
    assert any("sin claves de Twitch" in s for s in saltos)


def test_detectar_brief_un_candidato_por_url(repo, editar_yaml):
    editar_yaml(repo, "fuentes.yml", lambda d: d + [base(
        id="brief", origen="web", twitch_login=None, tipo="B",
        material=["https://example.invalid/a.mp4", "https://example.invalid/b.mp4"])])
    cands = detectar.detectar(repo, AHORA)
    assert [c.url for c in cands] == ["https://example.invalid/a.mp4", "https://example.invalid/b.mp4"]
    assert all(c.origen == "brief" and c.id_origen == c.url for c in cands)


def test_detectar_ignora_fichas_no_publicables(repo_twitch, editar_yaml):
    editar_yaml(repo_twitch, "fuentes.yml", lambda d: [dict(x, estado="pausada") if x["id"] == "canal-ejemplo" else x for x in d])
    assert detectar.detectar(repo_twitch, AHORA, cliente()) == []


def test_detectar_error_de_twitch_se_anota_y_sigue(repo_twitch):
    f = Falso({"/helix/users": (500, "{}")})
    cands, saltos = detectar.detectar_con_motivos(repo_twitch, AHORA, cliente(f))
    assert cands == [] and any("500" in s for s in saltos)


# ---- puertas ----

def cand(**kw):
    d = dict(fuente_id="canal-ejemplo", origen="twitch", id_origen="ClipX", url="https://clips.twitch.tv/ClipX",
             titulo="t", creador="c", idioma="es", juego_id="777001", duracion_s=20.0, vistas=10,
             vistas_por_hora=1.0, creado_en=AHORA, vod_offset=None)
    d.update(kw)
    return detectar.Candidato(**d)


@pytest.fixture
def repo_n3(repo, editar_yaml):
    editar_yaml(repo, "fuentes.yml", lambda d: d + [base()])
    editar_yaml(repo, "temporada.yml", lambda d: dict(d, twitch_game_id="777001"))
    return repo


def test_puerta_ficha_pasa_y_falla(repo_n3):
    assert puertas.puerta_ficha(repo_n3, cand(), AHORA, "tiktok")[0]
    ok, m = puertas.puerta_ficha(repo_n3, cand(), AHORA, "reels")
    assert not ok and "reels" in m
    assert not puertas.puerta_ficha(repo_n3, cand(fuente_id="no-existe"), AHORA, "tiktok")[0]


def test_puerta_juego_pasa_falla_y_brief(repo_n3):
    assert puertas.puerta_juego(cand(), "777001")[0]
    assert not puertas.puerta_juego(cand(juego_id="555"), "777001")[0]
    assert puertas.puerta_juego(cand(origen="brief", juego_id=None), "777001")[0]


def test_id_juego_resuelto_por_nombre_cuando_no_hay_id_guardado(repo):
    # El repo real ya guarda el id; esta prueba lo quita para probar la consulta por nombre.
    tmp = repo / "temporada.yml"
    tmp.write_text("\n".join(l for l in tmp.read_text(encoding="utf-8").splitlines()
                             if not l.startswith("twitch_game_id")) + "\n", encoding="utf-8")
    assert puertas.id_juego_temporada(repo, None) is None
    assert puertas.id_juego_temporada(repo, cliente()) == "777001"


def test_id_juego_guardado_gana(repo_n3):
    assert puertas.id_juego_temporada(repo_n3, None) == "777001"


def test_puerta_duracion(repo_n3):
    assert puertas.puerta_duracion(repo_n3, cand(duracion_s=10))[0]
    ok, m = puertas.puerta_duracion(repo_n3, cand(duracion_s=9.9))
    assert not ok and "10" in m


def test_puerta_no_repetido_y_vistos_se_crea(repo_n3):
    vistos = repo_n3 / "estado" / "vistos.txt"
    assert not vistos.exists()
    assert puertas.puerta_no_repetido(repo_n3, cand())[0]
    assert vistos.exists()
    puertas.anotar_visto(repo_n3, "ClipX")
    ok, m = puertas.puerta_no_repetido(repo_n3, cand())
    assert not ok and "vistos" in m


def test_puerta_no_repetido_por_marcador(repo_n3):
    anotar_fila(repo_n3, {"id_clip": "c1", "fecha": "2026-11-18", "plataforma": "tiktok",
                          "notas": "origen:ClipX"})
    ok, m = puertas.puerta_no_repetido(repo_n3, cand())
    assert not ok and "marcador" in m


def test_no_repetido_compara_id_completo_no_subcadena(repo_n3):
    anotar_fila(repo_n3, {"id_clip": "ClipXyz", "notas": "origen:ClipXyz otro", "url": "https://clips.twitch.tv/ClipXyz"})
    assert puertas.puerta_no_repetido(repo_n3, cand(id_origen="ClipX"))[0]
    assert not puertas.puerta_no_repetido(repo_n3, cand(id_origen="ClipXyz"))[0]


@pytest.mark.parametrize("fila", [
    {"id_clip": "ClipX"},
    {"url": "https://clips.twitch.tv/ClipX"},
    {"url": "https://clips.twitch.tv/ClipX/"},
    {"notas": "bueno; origen:ClipX; ok"},
])
def test_no_repetido_formas_del_marcador(repo_n3, fila):
    anotar_fila(repo_n3, dict(fila, fecha="2026-11-18", plataforma="tiktok"))
    assert not puertas.puerta_no_repetido(repo_n3, cand())[0]


def llenar(repo, n, plataforma="tiktok", fecha="2026-11-19"):
    for i in range(n):
        anotar_fila(repo, {"id_clip": f"r{i}", "fecha": fecha, "plataforma": plataforma})


def test_cupo_de_estreno_es_6(repo_n3):
    assert puertas.cupo_del_dia(repo_n3, AHORA, "tiktok") == 6
    llenar(repo_n3, 5)
    assert puertas.puerta_cupo(repo_n3, AHORA, "tiktok")[0]
    llenar(repo_n3, 1)
    assert not puertas.puerta_cupo(repo_n3, AHORA, "tiktok")[0]


def test_cupo_normal_es_6_fuera_de_ventana(repo_n3):
    despues = dt.datetime(2026, 12, 15, 18, 0, tzinfo=UTC)
    assert puertas.cupo_del_dia(repo_n3, despues, "tiktok") == 6
    llenar(repo_n3, 6, fecha="2026-12-15")
    assert not puertas.puerta_cupo(repo_n3, despues, "tiktok")[0]


def test_cupo_cuenta_solo_hoy_y_esa_plataforma(repo_n3):
    llenar(repo_n3, 8, plataforma="shorts")
    llenar(repo_n3, 8, fecha="2026-11-18")
    assert puertas.puerta_cupo(repo_n3, AHORA, "tiktok")[0]


def test_cupo_usa_fecha_de_guatemala(repo_n3):
    # 03:00 UTC del 20 nov es todavía 19 nov (21:00) en Guatemala
    tarde = dt.datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
    llenar(repo_n3, 8, fecha="2026-11-19")
    assert not puertas.puerta_cupo(repo_n3, tarde, "tiktok")[0]


def test_filtrar_aprueba_y_descarta_con_motivo(repo_n3):
    puertas.anotar_visto(repo_n3, "ClipVisto")
    cs = [cand(id_origen="Bueno"), cand(id_origen="Otro", juego_id="555"),
          cand(id_origen="Corto", duracion_s=2), cand(id_origen="ClipVisto")]
    ok, no = puertas.filtrar(repo_n3, cs, AHORA, "tiktok")
    assert [c.id_origen for c in ok] == ["Bueno"]
    motivos = {c.id_origen: m for c, m in no}
    assert "juego" in motivos["Otro"] and "mínimo" in motivos["Corto"] and "vistos" in motivos["ClipVisto"]


def test_filtrar_cuenta_el_cupo_con_las_filas_del_dia_y_los_demas_aprobados_son_suplentes(repo_n3):
    llenar(repo_n3, 5)                      # cupo 6: falta una publicación; cada corrida publica una sola toma
    ok, no = puertas.filtrar(repo_n3, [cand(id_origen="a"), cand(id_origen="b")], AHORA, "tiktok")
    assert [c.id_origen for c in ok] == ["a", "b"] and no == []
    llenar(repo_n3, 1)                      # con el cupo lleno no pasa ninguno
    ok, no = puertas.filtrar(repo_n3, [cand(id_origen="a"), cand(id_origen="b")], AHORA, "tiktok")
    assert ok == [] and all("cupo" in m for _, m in no) and len(no) == 2


# ---- validador ----




@pytest.mark.parametrize("campo,valor,fragmento", [
    ("twitch_login", "tiene espacios", "twitch_login"),
    ("material", ["http://x.invalid/a.mp4"], "material"),
    ("envio", "paloma", "envio"),
])
def test_validador_campos_nuevos(repo, editar_yaml, campo, valor, fragmento):
    editar_yaml(repo, "fuentes.yml", lambda d: [dict(d[0], **{campo: valor})] + d[1:])
    assert any(fragmento in e for e in validar.validar(repo).errores)


def test_validador_permite_descarga_debe_ser_booleano(repo, editar_yaml):
    def f(d):
        d[0]["reglas"]["permite_descarga"] = "si"
        return d
    editar_yaml(repo, "fuentes.yml", f)
    assert any("permite_descarga" in e for e in validar.validar(repo).errores)


def test_validador_acepta_campos_nuevos_validos(repo, editar_yaml):
    def f(d):
        d[0].update(twitch_login="canal_ejemplo", material=["https://x.invalid/a.mp4"], envio="api")
        d[0]["reglas"]["permite_descarga"] = True
        return d
    editar_yaml(repo, "fuentes.yml", f)
    assert validar.validar(repo).errores == []


def test_cargar_fuentes_real():
    assert any(f["id"] == "propio-tipografico" for f in cargar_fuentes())





# ---- ronda 1: correcciones de Codex y Haiku ----

def ficha_d(**kw):
    return base(id="ficha-d", tipo="D", pilares=["informativa"], campana=None, origen="web",
                twitch_login=None, material=["https://example.invalid/d.mp4"], **kw)


def test_tipo_d_sin_pilar_no_es_publicable():
    ok, m = es_publicable(ficha_d(), HOY)
    assert not ok and "informativa" in m
    assert es_publicable(ficha_d(), HOY, None, "informativa")[0]


def test_tipo_d_de_extremo_a_extremo_nadie_pasa_sin_pilar_informativa(repo, editar_yaml):
    editar_yaml(repo, "fuentes.yml", lambda d: d + [ficha_d()])
    editar_yaml(repo, "temporada.yml", lambda d: dict(d, twitch_game_id="777001"))
    cands = detectar.detectar(repo, AHORA)
    assert [c.fuente_id for c in cands] == ["ficha-d"] and cands[0].pilar == "informativa"
    ok, _ = puertas.filtrar(repo, cands, AHORA, "tiktok")
    assert [c.fuente_id for c in ok] == ["ficha-d"]
    for pilar_malo in (None, "momentos"):
        sin = [dataclasses.replace(c, pilar=pilar_malo) for c in cands]
        ok, no = puertas.filtrar(repo, sin, AHORA, "tiktok", pilar=pilar_malo)
        assert ok == [] and "informativa" in no[0][1]
    assert all(c.pilar == "informativa" for c in ok) or ok == []


def test_validador_no_da_aviso_falso_para_tipo_d(repo, editar_yaml):
    editar_yaml(repo, "fuentes.yml", lambda d: d + [ficha_d(permiso_url="https://example.invalid/p")])
    inf = validar.validar(repo)
    assert inf.errores == [] and inf.avisos == []


def test_filtrar_no_aprueba_dos_veces_el_mismo_id(repo, editar_yaml):
    url = "https://example.invalid/a.mp4"
    editar_yaml(repo, "fuentes.yml", lambda d: d + [
        base(id="b1", origen="web", twitch_login=None, tipo="B", material=[url]),
        base(id="b2", origen="web", twitch_login=None, tipo="B", material=[url])])
    ok, no = puertas.filtrar(repo, detectar.detectar(repo, AHORA), AHORA, "tiktok")
    assert [c.fuente_id for c in ok] == ["b1"] and "repetido" in no[0][1]


def test_transporte_convierte_fallos_de_conexion(monkeypatch):
    import urllib.error
    def falla(req, timeout=None):
        raise urllib.error.URLError("sin ruta")
    monkeypatch.setattr(detectar.urllib.request, "urlopen", falla)
    with pytest.raises(ErrorTwitch) as e:
        detectar.transporte_urllib("GET", "https://api.twitch.tv/helix/users?login=secreto-q", {})
    assert "secreto-q" not in str(e.value) and "URLError" in str(e.value)
    monkeypatch.setattr(detectar.urllib.request, "urlopen", lambda r, timeout=None: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(ErrorTwitch):
        detectar.transporte_urllib("GET", "https://api.twitch.tv/helix/users", {})


def test_detector_salta_twitch_por_fallo_de_red_y_conserva_brief(repo_twitch, editar_yaml):
    editar_yaml(repo_twitch, "fuentes.yml", lambda d: d + [base(
        id="brief", origen="web", twitch_login=None, tipo="B", material=["https://example.invalid/a.mp4"])])

    def roto(metodo, url, enc, cuerpo=None):
        raise ErrorTwitch("fallo de conexión con api.twitch.tv/helix/users: URLError")
    cands, saltos = detectar.detectar_con_motivos(repo_twitch, AHORA, cliente(roto))
    assert [c.origen for c in cands] == ["brief"] and any("fallo de conexión" in s for s in saltos)


def test_fallo_de_helix_no_aborta_lote_de_brief(repo_twitch, editar_yaml):
    repo = repo_twitch
    editar_yaml(repo, "temporada.yml", lambda d: d.pop("twitch_game_id", None) and None)
    editar_yaml(repo, "fuentes.yml", lambda d: d + [base(
        id="brief", origen="web", twitch_login=None, tipo="B", material=["https://example.invalid/a.mp4"])])
    c = cliente(Falso({"/helix/games": (500, "{}")}))
    mezcla = [cand(id_origen="ClipA"), detectar.detectar(repo, AHORA)[0]]
    ok, no = puertas.filtrar(repo, mezcla, AHORA, "tiktok", cliente=c)
    assert [x.origen for x in ok] == ["brief"]
    assert "no se pudo resolver" in no[0][1]


def test_lote_solo_de_brief_no_llama_a_helix(repo, editar_yaml):
    editar_yaml(repo, "fuentes.yml", lambda d: d + [base(
        id="brief", origen="web", twitch_login=None, tipo="B", material=["https://example.invalid/a.mp4"])])
    f = Falso()
    ok, _ = puertas.filtrar(repo, detectar.detectar(repo, AHORA), AHORA, "tiktok", cliente=cliente(f))
    assert len(ok) == 1 and f.llamadas == []


def test_fecha_de_guatemala_en_el_detector(repo, editar_yaml):
    # 03:00 UTC del 20 nov es 19 nov 21:00 en Guatemala: una ficha que vence el 19 sigue vigente
    editar_yaml(repo, "fuentes.yml", lambda d: d + [base(
        id="brief", origen="web", twitch_login=None, tipo="B", vence=dt.date(2026, 11, 19),
        material=["https://example.invalid/a.mp4"])])
    tarde = dt.datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
    assert len(detectar.detectar(repo, tarde)) == 1
    assert detectar.detectar(repo, tarde + dt.timedelta(hours=7)) == []   # ya es 20 nov en Guatemala


def test_brief_sin_duracion_queda_marcado_para_medir(repo_n3):
    c = cand(origen="brief", duracion_s=None)
    assert puertas.puerta_duracion(repo_n3, c)[0] and c.medir_al_descargar is True
    assert cand().medir_al_descargar is False


def test_cupo_con_filas_reales_de_marcador_csv(repo_n3):
    from fabrica.registro import CABECERA_MARCADOR
    fila = {c: "" for c in CABECERA_MARCADOR}
    lineas = [",".join(CABECERA_MARCADOR)]
    for i in range(8):
        f = dict(fila, id_clip=f"r{i}", fecha="2026-11-19", hora="10:00", plataforma="tiktok")
        lineas.append(",".join(f[c] for c in CABECERA_MARCADOR))
    (repo_n3 / "marcador.csv").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    assert not puertas.puerta_cupo(repo_n3, AHORA, "tiktok")[0]
    assert puertas.puerta_cupo(repo_n3, AHORA, "shorts")[0]


def test_validador_llama_a_es_publicable(repo, editar_yaml, monkeypatch):
    visto = []
    real = validar.es_publicable
    monkeypatch.setattr(validar, "es_publicable", lambda *a, **k: (visto.append(a[0]["id"]), real(*a, **k))[1])
    editar_yaml(repo, "fuentes.yml", lambda d: [dict(d[0], estado="vetada")] + d[1:])
    inf = validar.validar(repo)
    assert visto == [d["id"] for d in cargar_fuentes(repo)][:1] and any("estado 'vetada'" in a for a in inf.avisos)


SECRETOS = ["GEMINI_API_KEY", "BUFFER_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
            "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"]


def test_diagnostico_ningun_paso_imprime_un_secreto():
    ruta = Path(__file__).parent.parent / ".github" / "workflows" / "diagnostico.yml"
    wf = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    assert wf[True] == {"workflow_dispatch": None} and wf["permissions"] == {"contents": "read"}
    pasos = [p for p in wf["jobs"]["diagnostico"]["steps"] if "run" in p]   # los `uses:` no llevan shell
    assert pasos and all(p.get("shell") == "bash" for p in pasos)
    for p in pasos:
        logicas = p["run"].replace("\\\n", " ").splitlines()
        for linea in logicas:
            toca = [n for n in SECRETOS if re.search(r"\$\{?" + n + r"\b", linea)]
            if not toca:
                continue
            assert not re.match(r"\s*(echo|print)\b", linea), linea
            if "printf" in linea:                 # sólo viaja por tubería a curl, nunca a la pantalla
                assert "| curl" in linea or linea.rstrip().endswith("|"), linea
            if "TWITCH_CLIENT_SECRET" in toca and "printf" not in linea:
                assert "curl" not in linea, linea   # el secreto nunca va en la línea de órdenes de curl
    texto = ruta.read_text(encoding="utf-8")
    assert "-d \"client_secret=" not in texto and "--data @-" in texto


def test_pruebas_datos_no_esta_ignorado_por_gitignore():
    import shutil, subprocess
    raiz = Path(__file__).parent.parent
    if shutil.which("git") is None or not (raiz / ".git").exists():
        pytest.skip("sin git")
    archivos = [str(p.relative_to(raiz)) for p in (raiz / "pruebas" / "datos").rglob("*") if p.is_file()]
    assert archivos
    r = subprocess.run(["git", "check-ignore", "--", *archivos], cwd=raiz, capture_output=True, text=True)
    assert r.returncode == 1 and r.stdout == "", f"ignorados por .gitignore: {r.stdout}"


# ---- ronda 2 ----

def test_cero_llamadas_a_helix_si_todas_las_fichas_de_twitch_se_rechazan(repo, editar_yaml):
    editar_yaml(repo, "fuentes.yml", lambda d: d + [base(estado="pausada")])
    f = Falso()
    ok, no = puertas.filtrar(repo, [cand(id_origen="a"), cand(id_origen="b")], AHORA, "tiktok", cliente=cliente(f))
    assert ok == [] and len(no) == 2 and f.llamadas == []


@pytest.mark.parametrize("notas", ["origen:ClipX", "origen ClipX", "ok; origen ClipX; fin"])
def test_notas_con_origen_en_ambas_formas(repo_n3, notas):
    anotar_fila(repo_n3, {"notas": notas})
    assert not puertas.puerta_no_repetido(repo_n3, cand())[0]
    assert puertas.puerta_no_repetido(repo_n3, cand(id_origen="Clip"))[0]


@pytest.mark.parametrize("url", ["https://clips.twitch.tv/ClipX", "https://www.twitch.tv/canal_ejemplo/clip/ClipX"])
def test_url_de_clip_de_twitch_aporta_su_id(repo_n3, url):
    anotar_fila(repo_n3, {"url": url})
    assert not puertas.puerta_no_repetido(repo_n3, cand())[0]


def test_url_de_tiktok_no_bloquea_un_id_igual_al_ultimo_tramo(repo_n3):
    anotar_fila(repo_n3, {"url": "https://www.tiktok.com/@canal/video/123"})
    assert puertas.puerta_no_repetido(repo_n3, cand(id_origen="123"))[0]
    # la URL completa sí cuenta (brief)
    u = "https://www.tiktok.com/@canal/video/123"
    assert not puertas.puerta_no_repetido(repo_n3, cand(id_origen=u))[0]


def test_workflows_no_interpolan_secretos_en_run():
    carpeta = Path(__file__).parent.parent / ".github" / "workflows"
    archivos = sorted(carpeta.glob("*.yml"))
    assert archivos
    for ruta in archivos:
        wf = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        for nombre, job in wf["jobs"].items():
            for paso in job.get("steps", []):
                run = paso.get("run", "")
                assert "${{ secrets." not in run and "${{secrets." not in run, \
                    f"{ruta.name}/{nombre}: un run interpola secretos; pásalos por env"


def test_diagnostico_no_escribe_el_token_en_disco():
    texto = (Path(__file__).parent.parent / ".github" / "workflows" / "diagnostico.yml").read_text(encoding="utf-8")
    assert "-o token" not in texto and "-o juego" not in texto and "> token" not in texto

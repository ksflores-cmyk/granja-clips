"""Buscador de la ficha T (tarea #4): sin red, con un Twitch falso que enruta por ruta de URL."""
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from fabrica import ciclo, detectar, fuentes, publicar, puertas, vetados
from fabrica.detectar import ClienteTwitch, ErrorTwitch
from fabrica.editar import toma as mod_toma
from fabrica.pausa import ZONA

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_n6_ciclo as n6  # noqa: E402

AHORA = datetime(2026, 11, 20, 16, 0, tzinfo=timezone.utc)      # 10:00 en Guatemala
CLAVE_ID = "cid-inventado-t"        # EJEMPLO-FALSO
CLAVE_SECRETO = "secreto-inventado-t"   # EJEMPLO-FALSO
GAME = "1867279146"


def clip(cid, bid, nombre="Streamer", vistas=100, lang="es", game=GAME, dur=20.0, titulo="Un clip"):
    return {"id": cid, "url": f"https://clips.twitch.tv/{cid}", "broadcaster_id": bid,
            "broadcaster_name": nombre, "language": lang, "game_id": game, "view_count": vistas,
            "created_at": (AHORA - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration": dur, "title": titulo, "vod_offset": 10}


class Twitch:
    """Transporte falso. users: id -> login; followers: id -> int | None (error); teams: nombre -> logins | None."""

    def __init__(self, clips=(), users=None, followers=None, teams=None, streams=(), por_streamer=None,
                 paginas=None):
        self.clips = list(clips)
        self.users = users or {}
        self.followers = followers or {}
        self.teams = teams if teams is not None else {"youplanet": ["miembro_youplanet"]}
        self.streams = list(streams)
        self.por_streamer = por_streamer or {}
        self.paginas = paginas
        self.llamadas = []

    def __call__(self, metodo, url, enc, cuerpo=None):
        u = urlparse(url)
        q = parse_qs(u.query)
        self.llamadas.append((u.path, q))
        if u.path == "/oauth2/token":
            return 200, json.dumps({"access_token": "token-inventado"})
        if u.path == "/helix/streams":
            return 200, json.dumps({"data": self.streams})
        if u.path == "/helix/clips" and "game_id" in q:
            lista = [c for c in self.clips if c["game_id"] == q["game_id"][0]]
            if self.paginas:
                i = int(q.get("after", ["0"])[0])
                trozo = lista[i * self.paginas:(i + 1) * self.paginas]
                sig = {"cursor": str(i + 1)} if (i + 1) * self.paginas < len(lista) else {}
                return 200, json.dumps({"data": trozo, "pagination": sig})
            return 200, json.dumps({"data": lista, "pagination": {}})
        if u.path == "/helix/clips":
            return 200, json.dumps({"data": self.por_streamer.get(q["broadcaster_id"][0], [])})
        if u.path == "/helix/users":
            return 200, json.dumps({"data": [{"id": i, "login": self.users[i]} for i in q.get("id", [])
                                             if i in self.users]})
        if u.path == "/helix/channels/followers":
            n = self.followers.get(q["broadcaster_id"][0])
            return (200, json.dumps({"total": n, "data": []})) if n is not None else (500, "{}")
        if u.path == "/helix/teams":
            t = self.teams.get(q["name"][0])
            if t is None:
                return 404, "{}"
            return 200, json.dumps({"data": [{"users": [{"user_login": x} for x in t]}]})
        return 404, "{}"

    def rutas(self):
        return [p for p, _ in self.llamadas]


def cliente(t):
    return ClienteTwitch(CLAVE_ID, CLAVE_SECRETO, t, espera=lambda s: None)


@pytest.fixture
def r(repo):
    return repo


def correr_t(repo, t, ahora=AHORA):
    return detectar.detectar_t(repo, ahora, cliente(t))


def mundo(**kw):
    """Tres streamers normales: ana (5k), beto (50k), caro (1k)."""
    clips = [clip("c1", "1", "Ana", 300), clip("c2", "2", "Beto", 200), clip("c3", "3", "Caro", 100)]
    return Twitch(clips=clips, users={"1": "ana", "2": "beto", "3": "caro"},
                  followers={"1": 5000, "2": 50000, "3": 1000}, **kw)


def ids(cands):
    return [c.id_origen for c in cands]


# ------------------------------------------------------------ configuración y vetados

def test_config_por_defecto_sin_archivo_esta_apagada(r):
    (r / "config" / "buscador-t.yml").unlink()
    assert detectar.config_t(r)["activo"] is False
    t = mundo()
    assert correr_t(r, t) == ([], [], [])
    assert t.llamadas == []


def test_config_real_activa_y_con_los_valores_de_kevin(r):
    c = detectar.config_t(r)
    assert c["activo"] is True and c["seguidores_min"] == 1000 and c["seguidores_max"] == 300000
    assert c["max_clips_por_streamer_dia"] == 2 and c["duracion_s"] == [10, 45] and c["vigencia_dias"] == 2


def test_lista_negra_fija(r):
    assert vetados.logins_vetados(r) >= {"rubius", "auronplay", "ibai", "spreen", "westcol", "juanguarnizo"}
    assert vetados.equipos_vetados(r) == ["youplanet"]


def test_vetados_lee_retiros_por_url_y_por_id_de_ficha(r):
    (r / "retiros.md").write_text(
        "# Retiros\n\n| Fecha | Fuente | Publicación | Quién pidió | Motivo | Acción |\n|---|---|---|---|---|---|\n"
        "| 2026-11-20 | t-mi-streamer | https://www.twitch.tv/OtroCanal_9/clip/xx | él | pidió | vetada |\n",
        encoding="utf-8")
    v = vetados.logins_vetados(r)
    assert "mi_streamer" in v and "otrocanal_9" in v
    assert "fecha" not in v and "---" not in v


def test_retiros_vacio_no_veta_nada_extra(r):
    assert vetados.logins_vetados(r) == {"rubius", "auronplay", "ibai", "spreen", "westcol", "juanguarnizo"}


# ------------------------------------------------------------ cliente nuevo

def test_cliente_pagina_clips_del_juego_con_after(r):
    t = Twitch(clips=[clip(f"c{i}", "1") for i in range(5)], paginas=2)
    out = cliente(t).clips_juego(GAME, AHORA - timedelta(hours=24), AHORA, 100, 3)
    assert len(out) == 5
    pedidos = [q for p, q in t.llamadas if p == "/helix/clips"]
    assert len(pedidos) == 3 and "after" not in pedidos[0] and pedidos[1]["after"] == ["1"]
    assert pedidos[0]["first"] == ["100"] and pedidos[0]["game_id"] == [GAME]


def test_usuarios_por_id_en_lotes_de_100():
    users = {str(i): f"u{i}" for i in range(250)}
    t = Twitch(users=users)
    assert cliente(t).usuarios_por_id(list(users)) == users
    assert t.rutas().count("/helix/users") == 3


def test_seguidores_sin_total_es_error():
    class T(Twitch):
        def __call__(self, m, url, e, c=None):
            if "followers" in url:
                return 200, json.dumps({"data": []})
            return super().__call__(m, url, e, c)
    with pytest.raises(ErrorTwitch):
        cliente(T()).seguidores("1")


def test_equipo_inexistente_es_error_y_existente_da_logins_en_minusculas():
    t = Twitch(teams={"youplanet": ["Uno", "dos"]})
    assert cliente(t).miembros_equipo("youplanet") == {"uno", "dos"}
    with pytest.raises(ErrorTwitch):
        cliente(t).miembros_equipo("noexiste")


def test_streams_pide_juego_idioma_y_cantidad():
    t = Twitch()
    cliente(t).streams(GAME, "es", 20)
    q = [q for p, q in t.llamadas if p == "/helix/streams"][0]
    assert q == {"game_id": [GAME], "language": ["es"], "first": ["20"]}


# ------------------------------------------------------------ detección

def test_solo_pasan_clips_en_el_idioma(r):
    t = mundo()
    t.clips.append(clip("en1", "4", "Dan", 999, lang="en"))
    t.users["4"] = "dan"
    t.followers["4"] = 5000
    cands, _, fichas = correr_t(r, t)
    assert "en1" not in ids(cands) and {f["twitch_login"] for f in fichas} == {"ana", "beto", "caro"}


@pytest.mark.parametrize("n,entra", [(999, False), (1000, True), (300000, True), (300001, False)])
def test_limites_de_seguidores(r, n, entra):
    t = Twitch(clips=[clip("c1", "1", "Ana")], users={"1": "ana"}, followers={"1": n})
    cands, _, fichas = correr_t(r, t)
    assert (ids(cands) == ["c1"]) is entra and bool(fichas) is entra


def test_vetado_por_vetados_yml_login_y_nombre(r):
    t = mundo()
    t.clips += [clip("v1", "7", "Ibai", 9000), clip("v2", "8", "Auron Play", 9000)]
    t.users.update({"7": "ibai", "8": "auronplay"})
    t.followers.update({"7": 5000, "8": 5000})
    cands, _, _ = correr_t(r, t)
    assert not {"v1", "v2"} & set(ids(cands))
    # también por nombre visible aunque el login sea otro
    t.users["7"] = "otro_login_raro"
    cands, _, _ = correr_t(r, t)
    assert "v1" not in ids(cands) and "v2" not in ids(cands)


def test_vetado_por_equipo(r):
    t = mundo(teams={"youplanet": ["beto"]})
    cands, _, fichas = correr_t(r, t)
    assert "c2" not in ids(cands) and "beto" not in {f["twitch_login"] for f in fichas}
    assert "c1" in ids(cands)


def test_equipo_que_falla_apaga_la_ficha_t(r):
    t = mundo(teams={})        # el equipo no se puede leer
    cands, saltos, fichas = correr_t(r, t)
    assert cands == [] and fichas == [] and any("youplanet" in s for s in saltos)
    assert "/helix/clips" not in t.rutas()


def test_seguidores_con_error_deja_fuera_a_ese_streamer(r):
    t = mundo()
    t.followers["2"] = None
    cands, saltos, fichas = correr_t(r, t)
    assert "c2" not in ids(cands) and "c1" in ids(cands) and any("beto" in s for s in saltos)


def test_vetado_por_retiros_en_la_deteccion(r):
    (r / "retiros.md").write_text(
        "| Fecha | Fuente | Publicación | Quién pidió | Motivo | Acción |\n|---|---|---|---|---|---|\n"
        "| 2026-11-20 | t-ana | x | ella | pidió | vetada |\n", encoding="utf-8")
    cands, _, _ = correr_t(r, mundo())
    assert "c1" not in ids(cands) and "c2" in ids(cands)


def test_ficha_ya_guardada_de_un_retirado_sale_no_publicable(r):
    cands, _, fichas = correr_t(r, mundo())
    detectar.guardar_fichas_t(r, fichas)
    assert next(f for f in fuentes.cargar_fuentes(r) if f["id"] == "t-ana")["publicable"] is True
    (r / "retiros.md").write_text(
        "| Fecha | Fuente | Publicación | Quién pidió | Motivo | Acción |\n|---|---|---|---|---|---|\n"
        "| 2026-11-20 | t-ana | x | ella | pidió | vetada |\n", encoding="utf-8")
    f = next(f for f in fuentes.cargar_fuentes(r) if f["id"] == "t-ana")
    assert f["publicable"] is False and f["estado"] == "vetada"
    ok, _ = fuentes.es_publicable(f, AHORA.date(), "shorts")
    assert not ok


def test_maximo_dos_clips_por_streamer_y_orden_por_vistas(r):
    t = Twitch(clips=[clip("a", "1", "Ana", 10), clip("b", "1", "Ana", 500), clip("c", "1", "Ana", 300),
                      clip("d", "2", "Beto", 400)],
               users={"1": "ana", "2": "beto"}, followers={"1": 5000, "2": 5000})
    cands, _, _ = correr_t(r, t)
    assert ids(cands) == ["b", "d", "c"]


def test_streams_en_vivo_aportan_sus_clips(r):
    t = Twitch(clips=[], users={"9": "vivo"}, followers={"9": 3000},
               streams=[{"user_id": "9", "user_login": "vivo", "language": "es"}],
               por_streamer={"9": [clip("z", "9", "Vivo", 50), clip("otro", "9", "Vivo", 50, game="1")]})
    cands, _, fichas = correr_t(r, t)
    assert ids(cands) == ["z"] and fichas[0]["twitch_login"] == "vivo"
    q = [q for p, q in t.llamadas if p == "/helix/streams"][0]
    assert q["language"] == ["es"]


def test_candidato_y_ficha_t_con_todos_los_campos(r):
    cands, saltos, fichas = correr_t(r, mundo())
    c = next(x for x in cands if x.id_origen == "c1")
    assert (c.fuente_id, c.origen, c.creador, c.url, c.juego_id, c.vistas) == (
        "t-ana", "twitch", "twitch.tv/ana", "https://clips.twitch.tv/c1", GAME, 300)
    f = next(x for x in fichas if x["id"] == "t-ana")
    assert f["tipo"] == "T" and f["creador"] == f["credito"] == "twitch.tv/ana" and f["origen"] == "twitch"
    assert f["twitch_login"] == "ana" and f["broadcaster_id"] == "1" and f["seguidores"] == 5000
    assert f["seguidores_leidos"] == "2026-11-20" and f["juego"] == "GTA 6"
    assert f["reglas"] == {"duracion_s": [10, 45], "plataformas": ["shorts", "tiktok", "reels"],
                           "permite_descarga": True, "solo_clip": True,
                           "etiquetas_obligatorias": ["#GTA6", "#GTAVI", "#GrandTheftAutoVI"],
                           "divulgacion": "ninguna", "audiencia": "sin restricción"}
    assert f["pilares"] == ["primeras_veces", "momentos"] and f["campana"] is None
    assert f["vence"] == "2026-11-22" and f["estado"] == "vigente" and f["uso"] == "produccion"
    assert f["publicable"] is True and f["presupuesto_restante"] is None


def test_id_con_guion_bajo_pasa_a_guion(r):
    t = Twitch(clips=[clip("a", "1", "X")], users={"1": "mi_canal"}, followers={"1": 2000})
    cands, _, fichas = correr_t(r, t)
    assert cands[0].fuente_id == "t-mi-canal" and fichas[0]["twitch_login"] == "mi_canal"


def test_el_juego_sale_de_temporada_yml(r):
    t = mundo()
    correr_t(r, t)
    assert all(q["game_id"] == [GAME] for p, q in t.llamadas if p == "/helix/streams")
    cfg = yaml.safe_load((r / "temporada.yml").read_text(encoding="utf-8"))
    (r / "temporada.yml").write_text(
        (r / "temporada.yml").read_text(encoding="utf-8").replace(GAME, "424242"), encoding="utf-8")
    t2 = mundo()
    correr_t(r, t2)
    juegos = {q["game_id"][0] for p, q in t2.llamadas if "game_id" in q}
    assert juegos == {"424242"} and cfg["twitch_game_id"] == GAME


def test_sin_game_id_lo_resuelve_por_nombre(r):
    txt = (r / "temporada.yml").read_text(encoding="utf-8")
    (r / "temporada.yml").write_text(txt.replace(f'twitch_game_id: "{GAME}"', ""), encoding="utf-8")

    class T(Twitch):
        def __call__(self, m, url, e, c=None):
            if "/helix/games" in url:
                return 200, json.dumps({"data": [{"id": "555"}]})
            return super().__call__(m, url, e, c)
    t = T()
    correr_t(r, t)
    assert {q["game_id"][0] for p, q in t.llamadas if "game_id" in q} == {"555"}


def test_activo_false_no_consulta_nada(r):
    (r / "config" / "buscador-t.yml").write_text("activo: false\n", encoding="utf-8")
    t = mundo()
    assert correr_t(r, t) == ([], [], [])
    assert t.llamadas == []


def test_sin_cliente_da_un_salto_explicativo(r):
    cands, saltos, fichas = detectar.detectar_t(r, AHORA, None)
    assert cands == [] and fichas == [] and saltos and "claves" in saltos[0]


def test_tope_de_streamers_por_corrida(r):
    (r / "config" / "buscador-t.yml").write_text("activo: true\nmax_streamers: 2\n", encoding="utf-8")
    t = mundo()
    cands, _, _ = correr_t(r, t)
    assert t.rutas().count("/helix/channels/followers") == 2 and len(cands) == 2


# ------------------------------------------------------------ persistencia y fusión

def test_guardar_actualiza_por_id_y_cargar_fuentes_fusiona(r):
    _, _, fichas = correr_t(r, mundo())
    detectar.guardar_fichas_t(r, fichas)
    detectar.guardar_fichas_t(r, [dict(fichas[0], seguidores=777)])
    texto = (r / "estado" / "fichas-t.yml").read_text(encoding="utf-8")
    assert texto.startswith("#") and "no editar a mano" in texto.splitlines()[0]
    todas = fuentes.cargar_fuentes(r)
    t = [f for f in todas if f.get("tipo") == "T"]
    assert len(t) == 3 and any(f["seguidores"] == 777 for f in t)
    assert any(f["id"] == "fuente-prueba" or f.get("tipo") in ("A", "C", "D") for f in todas)


def test_id_que_choca_con_fuentes_yml_se_ignora(r):
    with open(r / "fuentes.yml", "a", encoding="utf-8") as f:
        f.write(n6.FICHA.replace("fuente-prueba", "t-ana"))
    detectar.guardar_fichas_t(r, [dict(correr_t(r, mundo())[2][0], id="t-ana", tipo="T")])
    asi = [f for f in fuentes.cargar_fuentes(r) if f["id"] == "t-ana"]
    assert len(asi) == 1 and asi[0]["tipo"] == "A"


def test_archivo_ilegible_o_ausente_solo_fuentes_yml(r):
    base = fuentes.cargar_fuentes(r)
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "fichas-t.yml").write_text("{{{ no es yaml: [", encoding="utf-8")
    assert fuentes.cargar_fuentes(r) == base


def test_detectar_con_motivos_guarda_las_fichas_y_suma_candidatos(r):
    cands, saltos = detectar.detectar_con_motivos(r, AHORA, cliente(mundo()))
    assert {"c1", "c2", "c3"} <= set(ids(cands))
    assert (r / "estado" / "fichas-t.yml").exists()
    assert {f["id"] for f in fuentes.cargar_fuentes(r)} >= {"t-ana", "t-beto", "t-caro"}
    # una segunda corrida no vuelve a pedir los clips de las fichas T por canal
    t = mundo()
    detectar.detectar_con_motivos(r, AHORA, cliente(t))
    assert "broadcaster_id" not in {k for p, q in t.llamadas if p == "/helix/clips" for k in q}


# ------------------------------------------------------------ descarga

def ficha_t(login="ana"):
    return correr_t_ficha(login)


def correr_t_ficha(login):
    return detectar._ficha_t(login, "1", 5000, "GTA 6", detectar.CONFIG_T_DEFECTO, AHORA.date())


@pytest.mark.parametrize("url,ok", [
    ("https://clips.twitch.tv/AbcDef", True),
    ("https://www.twitch.tv/ana/clip/AbcDef", True),
    ("https://www.twitch.tv/otro/clip/AbcDef", False),
    ("https://www.twitch.tv/videos/12345", False),
    ("https://www.twitch.tv/ana", False),
    ("http://clips.twitch.tv/AbcDef", False),
    ("https://youtube.com/watch?v=1", False),
])
def test_autoriza_descarga_t_solo_clips(url, ok):
    f = ficha_t()
    f["estado"], f["publicable"] = "vigente", True
    r_ok, motivo = fuentes.autoriza_descarga(f, AHORA.date(), "produccion", url, "momentos")
    assert r_ok is ok
    if not ok:
        assert "sólo autoriza el clip de Twitch" in motivo


def test_puede_descargar_y_es_publicable_de_la_ficha_t():
    f = ficha_t()
    assert fuentes.puede_descargar(f) == (True, "ficha T: sólo el clip de Twitch")
    assert fuentes.es_publicable(f, AHORA.date(), "shorts", "momentos")[0] is True
    assert fuentes.es_publicable(dict(f, creador="@ana"), AHORA.date())[0] is False
    assert fuentes.es_publicable({k: v for k, v in f.items() if k != "twitch_login"}, AHORA.date())[0] is False


# ------------------------------------------------------------ puertas

def cand_t(idc, fuente="t-ana", **kw):
    c = detectar.Candidato(fuente_id=fuente, origen="twitch", id_origen=idc, url=f"https://clips.twitch.tv/{idc}",
                           titulo="t", creador="twitch.tv/ana", idioma="es", juego_id=GAME, duracion_s=20.0,
                           vistas=10, vistas_por_hora=3.0, creado_en=AHORA - timedelta(hours=2), vod_offset=1,
                           pilar="momentos")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def con_fichas(repo, logins=("ana",)):
    detectar.guardar_fichas_t(repo, [detectar._ficha_t(l, str(i), 5000, "GTA 6", detectar.CONFIG_T_DEFECTO,
                                                       AHORA.date()) for i, l in enumerate(logins, 1)])


def test_puerta_vetado_para_ficha_t_con_login_en_la_lista(r):
    con_fichas(r, ("ana", "rubius"))
    assert puertas.puerta_vetado(r, cand_t("a")) == (True, "ok")
    assert puertas.puerta_vetado(r, cand_t("b", "t-rubius")) == (False, "streamer vetado")


def test_filtrar_veta_por_retiros_aunque_la_ficha_exista(r):
    con_fichas(r)
    (r / "retiros.md").write_text(
        "| Fecha | Fuente | Publicación | Quién pidió | Motivo | Acción |\n|---|---|---|---|---|---|\n"
        "| 2026-11-20 | t-ana | x | ella | pidió | vetada |\n", encoding="utf-8")
    ap, desc = puertas.filtrar(r, [cand_t("a")], AHORA, "shorts")
    assert ap == [] and desc


def test_cupo_de_dos_por_streamer_dentro_de_la_corrida(r):
    con_fichas(r)
    ap, desc = puertas.filtrar(r, [cand_t("a"), cand_t("b"), cand_t("c")], AHORA, "shorts")
    assert ids(ap) == ["a", "b"] and "cupo del streamer lleno (2/2)" in desc[0][1]


def test_cupo_de_dos_por_streamer_con_el_marcador_de_hoy(r):
    con_fichas(r)
    hoy = AHORA.astimezone(ZONA).date().isoformat()
    n6.anotar(r, fecha=hoy, fuente_id="t-ana", plataforma="shorts")
    ap, _ = puertas.filtrar(r, [cand_t("a"), cand_t("b")], AHORA, "shorts")
    assert ids(ap) == ["a"]
    # otra plataforma u otro día no cuentan
    n6.anotar(r, fecha="2026-11-19", fuente_id="t-ana", plataforma="tiktok")
    ap, _ = puertas.filtrar(r, [cand_t("a"), cand_t("b")], AHORA, "tiktok")
    assert ids(ap) == ["a", "b"]


def test_el_cupo_no_afecta_a_fichas_que_no_son_t(r):
    with open(r / "fuentes.yml", "a", encoding="utf-8") as f:
        f.write(n6.FICHA)
    c = [n6.cand(f"x{i}") for i in range(3)]
    assert puertas.puerta_cupo_streamer(r, c[0], AHORA, "shorts") == (True, "ok")


# ------------------------------------------------------------ crédito

def test_textos_y_credito_del_video_con_twitch_tv():
    f = ficha_t("ana")
    toma = {"numero": 5, "gancho": "Primera vez", "pilar": "momentos", "creador": "twitch.tv/ana",
            "valor": ["Uno.", "Dos.", "Tres."], "etiquetas": ["#GTA6", "#Gaming", "#Clips"]}
    for plat in ("shorts", "tiktok", "reels"):
        t = publicar.textos(toma, f, plat)
        assert "🎥 twitch.tv/ana" in t["texto"] or t["credito"] == "🎥 twitch.tv/ana"
    toma.pop("creador")
    assert publicar.textos(toma, f, "shorts")["credito"] == "🎥 twitch.tv/ana"
    datos = {"recorte": {"inicio": 0, "fin": 12}, "gancho": "Hola", "palabra_lapiz": "Hola", "escena": "x",
             "acotacion": "", "lineas": []}
    from fabrica.editar.marca import Marca
    marca = Marca.cargar(Path(__file__).resolve().parent.parent)
    assert mod_toma.armar(datos, marca, f, 3)[0].credito == "Con: twitch.tv/ana"


# ------------------------------------------------------------ validar

def test_validar_acepta_t_y_exige_login_y_creador(r):
    from fabrica import validar
    f = ficha_t()
    inf = validar.Informe()
    validar.validar_fuentes([f], inf)
    assert not inf.errores
    for malo, texto in ((dict(f, twitch_login=None), "twitch_login"), (dict(f, creador="@ana"), "twitch.tv/")):
        inf = validar.Informe()
        validar.validar_fuentes([malo], inf)
        assert any(texto in e for e in inf.errores)
    inf = validar.Informe()
    validar.validar_fuentes([dict(f, tipo="Z")], inf)
    assert any("tipo debe ser A, B, C, D o T" in e for e in inf.errores)


# ------------------------------------------------------------ ciclo

@pytest.fixture
def rc(repo):
    """Repo listo para el ciclo en vivo (igual que la fixture `r` de test_n6_ciclo)."""
    (repo / "PAUSA").unlink()
    (repo / "config" / "canales.yml").write_text(
        "".join(f"{p}: {{servicio: {s}, nombre_esperado: guionvivo, buffer_channel_id: null, buffer_org_id: null}}\n"
                for p, s in (("shorts", "youtube"), ("tiktok", "tiktok"), ("reels", "instagram"))),
        encoding="utf-8")
    publicar.guardar_ids_de_canales(
        repo, {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"},
        dict.fromkeys(("shorts", "tiktok", "reels"), "org-ejemplo-001"))
    n6.cambiar_modo(repo, "vivo", ["shorts"])
    return repo


def test_producir_ve_la_ficha_t_nueva(rc):
    vistas = []

    def detectar_fn(raiz, ahora, c):
        f = ficha_t("ana")
        f["estado"], f["publicable"] = "vigente", True
        detectar.guardar_fichas_t(raiz, [f])
        return [cand_t("clip-t")], []

    def hacer(raiz, c, ficha, ahora, salida=None, **kw):
        vistas.append((ficha.get("id"), ficha.get("tipo")))
        return n6.Hacer(rc)(raiz, c, ficha, ahora, salida=salida)

    sal = []
    ciclo.ejecutar(rc, ahora=n6.AHORA, env=n6.ENV, imprimir=sal.append, validar_fn=n6.VERDE,
                   detectar_fn=detectar_fn, hacer_toma_fn=hacer,
                   transporte_buffer=n6.BufferSimulado(), transporte_almacen=n6.SupabaseSimulado())
    assert vistas[:1] == [("t-ana", "T")], "\n".join(sal)


def test_ordenar_desempata_por_vistas_por_hora():
    t = yaml.safe_load((Path(__file__).resolve().parent.parent / "temporada.yml").read_text(encoding="utf-8"))
    a = cand_t("a", vistas_por_hora=1.0)
    b = cand_t("b", vistas_por_hora=9.0)
    assert ids(ciclo.ordenar([a, b], t)) == ["b", "a"]


# ------------------------------------------------------------ diagnóstico

def test_repr_del_cliente_no_revela_claves():
    c = cliente(mundo())
    assert CLAVE_ID not in repr(c) and CLAVE_SECRETO not in repr(c)


def test_diagnostico_imprime_tabla_sin_claves_y_no_escribe(r):
    antes = sorted(p.relative_to(r).as_posix() for p in r.rglob("*") if p.is_file())
    sal = []
    cod = detectar.diagnostico_t(r, 2, cliente(mundo()), AHORA, sal.append)
    texto = "\n".join(sal)
    assert cod == 0 and "Grand Theft Auto VI" in texto and "idioma: es" in texto and "clips vistos: 3" in texto
    assert "ana |" in texto and "https://clips.twitch.tv/c1" in texto and "candidatos: 3 de 3" in texto
    assert texto.count("https://clips.twitch.tv/") == 2          # --n 2
    for secreto in (CLAVE_ID, CLAVE_SECRETO, "token-inventado"):
        assert secreto not in texto
    assert sorted(p.relative_to(r).as_posix() for p in r.rglob("*") if p.is_file()) == antes


def test_diagnostico_codigos_de_salida(r, monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    sal = []
    assert detectar.diagnostico_t(r, 10, None, AHORA, sal.append) == 1
    assert detectar.diagnostico_t(r, 10, cliente(Twitch()), AHORA, sal.append) == 2

    class Roto(Twitch):
        def __call__(self, *a, **k):
            raise RuntimeError(f"fallo con {CLAVE_SECRETO}")
    sal = []
    cod = detectar.diagnostico_t(r, 10, cliente(Roto()), AHORA, sal.append)
    assert cod == 1 and CLAVE_SECRETO not in "\n".join(sal)


def test_el_flujo_de_diagnostico_corre_el_diagnostico_t():
    wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "diagnostico.yml").read_text(
        encoding="utf-8")
    assert "python -m fabrica.detectar --diagnostico-t --n 10" in wf and "timeout-minutes: 10" in wf
    assert "GEMINI_API_KEY" in wf and "actions/setup-python@v5" in wf


# ------------------------------------------------------------ ronda 1 de revisión

def test_equipo_sin_miembros_apaga_la_ficha_t(r):
    t = mundo(teams={"youplanet": []})       # Twitch responde el equipo vacío: falla cerrada
    cands, saltos, fichas = correr_t(r, t)
    assert cands == [] and fichas == [] and any("sin miembros" in s for s in saltos)


def test_si_no_se_guardan_las_fichas_no_hay_candidatos_t_y_el_resto_sigue(r, monkeypatch):
    def rompe(*a, **k):
        raise OSError("disco lleno")
    monkeypatch.setattr(detectar, "guardar_fichas_t", rompe)
    cands, saltos = detectar.detectar_con_motivos(r, AHORA, cliente(mundo()))
    assert not any(c.fuente_id.startswith("t-") for c in cands)
    assert any("no se pudo guardar" in s for s in saltos)


def test_la_campana_pagada_va_antes_que_la_ficha_t():
    t = yaml.safe_load((Path(__file__).resolve().parent.parent / "temporada.yml").read_text(encoding="utf-8"))
    pagada = cand_t("fm", fuente="sega-fm27", origen="brief", pilar="momentos", vistas_por_hora=0.0,
                    creado_en=None)
    tt = cand_t("tt", pilar="primeras_veces", vistas_por_hora=500.0)
    assert ids(ciclo.ordenar([tt, pagada], t, {"sega-fm27"})) == ["fm", "tt"]
    assert ids(ciclo.ordenar([tt, pagada], t)) == ["tt", "fm"]     # sin campaña pagada, manda el pilar


def test_elegir_candidatos_pone_primero_la_pagada(repo_fm27):
    r = repo_fm27                                   # FM27 está pausada en el repo real: copia vigente
    con_fichas(r)
    pagada = cand_t("fm", fuente="sega-fm27", origen="brief", pilar="momentos", vistas_por_hora=0.0,
                    creado_en=None, juego_id=None, duracion_s=None)
    tt = cand_t("tt", pilar="primeras_veces", vistas_por_hora=500.0)
    out = []
    cands = ciclo.elegir_candidatos(r, AHORA, ["shorts"], out.append,
                                    detectar_fn=lambda *a: ([tt, pagada], []), cliente=None)
    assert ids(cands) == ["fm", "tt"], out


def test_ficha_t_lleva_las_etiquetas_de_la_temporada_y_sale_en_reels(r):
    cands, _, fichas = correr_t(r, mundo())
    f = next(x for x in fichas if x["twitch_login"] == "ana")
    temporada = yaml.safe_load((r / "temporada.yml").read_text(encoding="utf-8"))
    assert f["reglas"]["etiquetas_obligatorias"] == temporada["hashtags"][:3]
    toma = {"numero": 7, "gancho": "Primera persecución", "pilar": "primeras_veces",
            "creador": "twitch.tv/ana"}           # como la arma el ciclo: sin etiquetas
    for plat in ("shorts", "tiktok", "reels"):
        t = publicar.textos(toma, f, plat)
        assert "🎥 twitch.tv/ana" in t["texto"]


def test_el_cupo_cuenta_tambien_lo_ya_programado_para_manana(r):
    con_fichas(r)
    noche = AHORA.astimezone(ZONA).replace(hour=23)
    manana = (noche.date() + timedelta(days=1)).isoformat()
    n6.anotar(r, fecha=manana, fuente_id="t-ana", plataforma="shorts")
    n6.anotar(r, fecha=manana, fuente_id="t-ana", plataforma="shorts")
    ap, desc = puertas.filtrar(r, [cand_t("a")], noche, "shorts")
    assert ap == [] and "cupo del streamer lleno" in desc[0][1]

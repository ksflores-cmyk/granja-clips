"""N5 secciones 2 y 3: publicador de Buffer, textos, cuota, canales y modos. Sin red ni claves reales."""
import csv
import json
import shutil
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

import falsos_n5 as F
from fabrica import publicar, red, registro, validar
from fabrica.almacen import Almacen
from fabrica.publicar import (AlertaRoja, ClienteBuffer, Cuota, Descartada, ErrorBuffer, ErrorCuota,
                              Publicador, armar_carga, confirmar_canales, hueco_a_utc, textos)
from fabrica.simulacion import BufferSimulado, SupabaseSimulado

CLAVE = "bfr_clave-inventada-n5"       # EJEMPLO-FALSO
SUPA_URL = "https://proyecto-falso.supabase.invalid"
SUPA_CLAVE = "sb_secret_inventada-n5"  # EJEMPLO-FALSO
AHORA = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)     # 06:00 en Guatemala
HOY = date(2026, 11, 18)
CANALES_BASE = (
    "shorts: {servicio: youtube, nombre_esperado: guionvivo, buffer_channel_id: null, buffer_org_id: null}\n"
    "tiktok: {servicio: tiktok, nombre_esperado: guionvivo, buffer_channel_id: null, buffer_org_id: null}\n"
    "reels: {servicio: instagram, nombre_esperado: guionvivo, buffer_channel_id: null, buffer_org_id: null}\n")


@pytest.fixture(autouse=True)
def _canales_de_prueba(request):
    """El repo real ya guarda los canales confirmados de Kevin; estas pruebas parten de canales sin confirmar."""
    if "repo" in request.fixturenames:
        repo = request.getfixturevalue("repo")
        (repo / "config" / "canales.yml").write_text(CANALES_BASE, encoding="utf-8")


CANALES_CFG = {"shorts": {"servicio": "youtube", "nombre_esperado": "guionvivo"},
               "tiktok": {"servicio": "tiktok", "nombre_esperado": "guionvivo"},
               "reels": {"servicio": "instagram", "nombre_esperado": "guionvivo"}}
FICHA = {"id": "canal-ejemplo", "creador": "@ejemplo",
         "reglas": {"etiquetas_obligatorias": ["@marcaejemplo", "#Ejemplo"],
                    "divulgacion": "#ad colaboración pagada"}}
FICHA_LIMPIA = {"id": "propio", "creador": "@guionvivo", "reglas": {"etiquetas_obligatorias": [],
                                                                  "divulgacion": "ninguna"}}
TOMA = {"numero": 12, "gancho": "Primera vez en el mapa", "pilar": "momentos",
        "valor": ["Frase uno.", "Frase dos.", "Frase tres."],
        "etiquetas": ["#GTA6", "#Gaming", "#Clips", "#Mas", "#Otra"]}
CONFIG_ALMACEN = {"bucket": "clips", "max_mb": 50, "prefijo": "tomas"}


def cliente(tmp_path, simulado=None, **kw):
    return ClienteBuffer(CLAVE, simulado or BufferSimulado(**kw), tmp_path, dormir=lambda s: None,
                         ahora=lambda: AHORA)


def esquema(tmp_path, archivo="esquema.json"):
    return cliente(tmp_path, esquema=archivo).introspeccion()


def vivo(tmp_path, simulado=None, **kw):
    c = cliente(tmp_path, simulado, **kw)
    ids = {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"}
    return Publicador(c, None, tmp_path, "vivo", lambda: AHORA, ids), c


def sin_pausa(repo):
    (repo / "PAUSA").unlink()
    return repo


# ---------------- organización, canales y confirmación ----------------

def test_organizacion(tmp_path):
    assert cliente(tmp_path).organizaciones() == [{"id": "org-ejemplo-001", "name": "Guion Vivo"}]


def test_canales_traen_los_tres_nuestros_y_uno_ajeno(tmp_path):
    canales = cliente(tmp_path).todos_los_canales()
    assert [c["service"] for c in canales] == ["youtube", "tiktok", "instagram", "instagram"]
    assert canales[3]["name"] == "cocina_de_ana"


def test_confirmar_canales_devuelve_los_ids_ignorando_el_ajeno(tmp_path):
    ids = confirmar_canales(cliente(tmp_path).todos_los_canales(), CANALES_CFG, tmp_path, escribir=False)
    assert ids == {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"}


def test_confirmar_acepta_arroba_y_mayusculas_en_name():
    api = [{"id": "x1", "name": "GuionVivo", "displayName": "Otro Nombre", "service": "youtube",
            "isQueuePaused": False}]
    cfg = {"shorts": {**CANALES_CFG["shorts"], "nombre_esperado": "@guionVIVO"}}
    assert confirmar_canales(api, cfg, None, ["shorts"], escribir=False) == {"shorts": "x1"}


def test_name_ajeno_con_displayname_igual_es_alerta_roja(repo):
    sin_pausa(repo)
    api = [{"id": "x1", "name": "cuenta_de_otro", "displayName": "guionvivo", "service": "youtube",
            "isQueuePaused": False}]
    with pytest.raises(AlertaRoja, match="no confirmado"):
        confirmar_canales(api, {"shorts": CANALES_CFG["shorts"]}, repo, ["shorts"])
    assert (repo / "PAUSA").exists()


def test_id_guardado_distinto_al_de_la_api_es_alerta_roja():
    api = [{"id": "x1", "name": "guionvivo", "service": "youtube", "isQueuePaused": False, "_org": "o1"}]
    cfg = {"shorts": {**CANALES_CFG["shorts"], "buffer_channel_id": "otro-id", "buffer_org_id": "o1"}}
    with pytest.raises(AlertaRoja, match="no es el confirmado"):
        confirmar_canales(api, cfg, None, ["shorts"], escribir=False)


def test_organizacion_distinta_a_la_guardada_es_alerta_roja():
    api = [{"id": "x1", "name": "guionvivo", "service": "youtube", "isQueuePaused": False, "_org": "o2"}]
    cfg = {"shorts": {**CANALES_CFG["shorts"], "buffer_channel_id": "x1", "buffer_org_id": "o1"}}
    with pytest.raises(AlertaRoja, match="otra organización"):
        confirmar_canales(api, cfg, None, ["shorts"], escribir=False)


def test_exigir_guardado_sin_ids_lanza_sin_confirmar_y_con_ids_pasa():
    api = [{"id": "x1", "name": "guionvivo", "service": "youtube", "isQueuePaused": False, "_org": "o1"}]
    with pytest.raises(publicar.SinConfirmar, match="ensayo con claves"):
        confirmar_canales(api, {"shorts": CANALES_CFG["shorts"]}, None, ["shorts"], escribir=False,
                          exigir_guardado=True)
    cfg = {"shorts": {**CANALES_CFG["shorts"], "buffer_channel_id": "x1", "buffer_org_id": "o1"}}
    assert confirmar_canales(api, cfg, None, ["shorts"], escribir=False, exigir_guardado=True) == {"shorts": "x1"}


def test_canal_ajeno_crea_pausa_con_su_motivo_y_la_alerta_para_n6(repo):
    sin_pausa(repo)
    api = F.cargar("buffer", "canales_ajeno.json")["data"]["channels"]
    with pytest.raises(AlertaRoja) as e:
        confirmar_canales(api, CANALES_CFG, repo)
    assert "shorts no confirmado" in str(e.value)
    pausa = (repo / "PAUSA").read_text(encoding="utf-8")
    assert "motivo: canal de shorts no confirmado" in pausa
    alerta = json.loads((repo / "estado" / "alerta-roja.json").read_text(encoding="utf-8"))
    assert "shorts no confirmado" in alerta["motivo"]


def test_cola_pausada_es_alerta_roja(repo):
    sin_pausa(repo)
    api = F.cargar("buffer", "canales_pausada.json")["data"]["channels"]
    with pytest.raises(AlertaRoja, match="pausada"):
        confirmar_canales(api, CANALES_CFG, repo)
    assert "pausada" in (repo / "PAUSA").read_text(encoding="utf-8")


def test_dos_canales_iguales_no_se_adivinan(tmp_path):
    api = F.cargar("buffer", "canales.json")["data"]["channels"]
    api.append({**api[0], "id": "canal-yt-002"})
    with pytest.raises(AlertaRoja, match="hay 2"):
        confirmar_canales(api, CANALES_CFG, tmp_path, escribir=False)


def test_guardar_ids_cambia_solo_buffer_channel_id(repo):
    publicar.guardar_ids_de_canales(repo, {"shorts": "canal-yt-001", "reels": "canal-ig-001"},
                                    {"shorts": "org-ejemplo-001", "reels": "org-ejemplo-001"})
    datos = yaml.safe_load((repo / "config" / "canales.yml").read_text(encoding="utf-8"))
    assert datos["shorts"]["buffer_channel_id"] == "canal-yt-001"
    assert datos["shorts"]["buffer_org_id"] == "org-ejemplo-001" and datos["tiktok"]["buffer_org_id"] is None
    assert datos["reels"]["buffer_channel_id"] == "canal-ig-001"
    assert datos["tiktok"]["buffer_channel_id"] is None
    assert datos["shorts"]["nombre_esperado"] == "guionvivo"
    assert validar.validar(repo).errores == []


# ---------------- createPost, errores y reintento ----------------

def test_createpost_correcto_con_la_forma_documentada(tmp_path):
    sim = BufferSimulado()
    pub, _ = vivo(tmp_path, sim)
    tx = textos(TOMA, FICHA_LIMPIA, "shorts")
    cuando = hueco_a_utc(HOY, "11:00")
    r = pub.programar("shorts", {**TOMA, "fuente_id": "propio"}, "https://x.invalid/v.mp4", tx, cuando)
    assert r["id"] == "post-ejemplo-001" and r["dueAt"] == "2026-11-18T17:00:00.000Z"
    i = [k for k, q in enumerate(sim.consultas) if "createPost" in q][0]
    carga = sim.variables[i]["input"]
    assert carga["assets"] == [{"video": {"url": "https://x.invalid/v.mp4"}}]
    assert carga["mode"] == "customScheduled" and carga["schedulingType"] == "automatic"
    assert carga["dueAt"] == "2026-11-18T17:00:00.000Z" and carga["channelId"] == "canal-yt-001"
    assert carga["metadata"]["youtube"]["categoryId"] == "20"
    assert carga["metadata"]["youtube"]["title"].startswith("Toma 012.")
    assert "saveToDraft" not in carga
    assert not any("addToQueue" in q for q in sim.consultas)


def test_con_30_s_de_plazo_createpost_no_se_intenta_ni_se_reserva(tmp_path):
    from fabrica import plazo
    sim = BufferSimulado()
    pub, _ = vivo(tmp_path, sim)
    tx = textos(TOMA, FICHA_LIMPIA, "shorts")
    antes = len(pub._libro())
    plazo.iniciar(30)                    # quedan 25 s libres: no caben los 60 s de createPost
    try:
        with pytest.raises(plazo.SinTiempo):
            pub.programar("shorts", {**TOMA, "fuente_id": "propio"}, "https://x.invalid/v.mp4", tx,
                          hueco_a_utc(HOY, "11:00"))
    finally:
        plazo.detener()
    assert len(pub._libro()) == antes
    assert not any("createPost" in q for q in sim.consultas)


def test_instagram_manda_reel_y_feed(tmp_path):
    carga, _ = armar_carga("reels", "canal-ig-001", "texto", None, "https://x.invalid/v.mp4",
                           hueco_a_utc(HOY, "15:00"), esquema(tmp_path))
    assert carga["metadata"]["instagram"] == {"type": "reel", "shouldShareToFeed": True}


def test_tiktok_no_manda_metadata_si_el_esquema_no_exige_nada(tmp_path):
    carga, _ = armar_carga("tiktok", "canal-tt-001", "texto", None, "https://x.invalid/v.mp4",
                           hueco_a_utc(HOY, "15:00"), esquema(tmp_path))
    assert "metadata" not in carga


def test_mutation_error_descarta_con_su_mensaje(tmp_path):
    pub, _ = vivo(tmp_path, crear="createpost_error.json")
    tx = textos(TOMA, FICHA_LIMPIA, "shorts")
    with pytest.raises(Descartada, match="no admite este tipo de video"):
        pub.programar("shorts", TOMA, "https://x.invalid/v.mp4", tx, hueco_a_utc(HOY, "11:00"))
    assert not (tmp_path / "estado" / "buffer-textos.json").exists()


def test_error_graphql_lanza_error_buffer(tmp_path):
    esp = F.Espia([F.resp(200, F.leer("buffer", "error_graphql.json"))])
    c = ClienteBuffer(CLAVE, esp, tmp_path, dormir=lambda s: None)
    with pytest.raises(ErrorBuffer, match="madeForKids"):
        c.organizaciones()


def test_429_se_reintenta_una_vez_y_sale(tmp_path):
    esp = F.Espia([F.resp(429, "{}", **{"retry_after": "1"}), F.resp(200, F.leer("buffer", "organizacion.json"))])
    c = ClienteBuffer(CLAVE, esp, tmp_path, dormir=lambda s: None)
    assert c.organizaciones()[0]["id"] == "org-ejemplo-001"
    assert len(esp.llamadas) == 2


def test_dos_5xx_seguidos_fallan_sin_mas_reintentos(tmp_path):
    esp = F.Espia([F.resp(503, "{}")])
    c = ClienteBuffer(CLAVE, esp, tmp_path, dormir=lambda s: None)
    with pytest.raises(ErrorBuffer, match="código 503"):
        c.organizaciones()
    assert len(esp.llamadas) == 2


def test_la_llamada_lleva_bearer_y_un_solo_endpoint(tmp_path):
    esp = F.Espia([F.resp(200, F.leer("buffer", "organizacion.json"))])
    ClienteBuffer(CLAVE, esp, tmp_path).organizaciones()
    c = esp.llamadas[0]
    assert c["url"] == "https://api.buffer.com" and c["metodo"] == "POST"
    assert c["encabezados"]["Authorization"] == f"Bearer {CLAVE}"
    assert CLAVE not in c["url"]


# ---------------- introspección ----------------

def test_introspeccion_descubre_el_activo_de_video_y_las_consultas(tmp_path):
    e = esquema(tmp_path)
    assert {"CreatePostInput", "YoutubePostMetadataInput", "TikTokPostMetadataInput", "AssetInput",
            "VideoAssetInput", "DeletePostInput"} <= set(e["tipos"])
    assert e["tipos"]["YoutubePostMetadataInput"]["title"]["obligatorio"] is True
    assert e["tipos"]["CreatePostInput"]["assets"]["lista"] is True
    assert "post" in e["consultas"]


def test_con_madeforkids_se_envia(tmp_path):
    carga, anot = armar_carga("shorts", "c", "t", "titulo", "https://x.invalid/v.mp4",
                              hueco_a_utc(HOY, "11:00"), esquema(tmp_path))
    assert carga["metadata"]["youtube"]["madeForKids"] is False and anot == []


def test_sin_madeforkids_se_quita_y_se_anota(tmp_path):
    carga, anot = armar_carga("shorts", "c", "t", "titulo", "https://x.invalid/v.mp4",
                              hueco_a_utc(HOY, "11:00"), esquema(tmp_path, "esquema_sin_madeforkids.json"))
    assert "madeForKids" not in carga["metadata"]["youtube"]
    assert any("madeForKids" in a and "se quita" in a for a in anot)


def test_obligatorio_que_falta_descarta_con_motivo(tmp_path):
    with pytest.raises(Descartada, match="YoutubePostMetadataInput.license"):
        armar_carga("shorts", "c", "t", "titulo", "https://x.invalid/v.mp4",
                    hueco_a_utc(HOY, "11:00"), esquema(tmp_path, "esquema_falta_obligatorio.json"))


def test_campo_necesario_que_no_existe_descarta(tmp_path):
    e = esquema(tmp_path)
    del e["tipos"]["CreatePostInput"]["assets"]
    with pytest.raises(Descartada, match="CreatePostInput.assets"):
        armar_carga("reels", "c", "t", None, "https://x.invalid/v.mp4", hueco_a_utc(HOY, "11:00"), e)


# ---------------- hora explícita ----------------

def test_once_de_guatemala_son_las_17_utc():
    assert publicar.iso_utc(hueco_a_utc(HOY, "11:00")) == "2026-11-18T17:00:00.000Z"
    assert hueco_a_utc(HOY, "23:45", "America/Guatemala").hour == 5   # cruza al día siguiente en UTC


def test_hueco_en_el_pasado_se_rechaza():
    with pytest.raises(Descartada, match="10 minutos"):
        publicar.validar_futuro(hueco_a_utc(HOY, "05:00"), AHORA)


def test_hueco_a_menos_de_diez_minutos_se_rechaza_y_a_diez_pasa():
    with pytest.raises(Descartada):
        publicar.validar_futuro(AHORA + timedelta(minutes=9), AHORA)
    publicar.validar_futuro(AHORA + timedelta(minutes=10), AHORA)


def test_programar_con_hueco_pasado_no_llama_a_createpost(tmp_path):
    sim = BufferSimulado()
    pub, _ = vivo(tmp_path, sim)
    with pytest.raises(Descartada):
        pub.programar("shorts", TOMA, "https://x.invalid/v.mp4", textos(TOMA, FICHA_LIMPIA, "shorts"),
                      hueco_a_utc(HOY, "05:00"))
    assert not sim.hubo("createPost")


# ---------------- textos ----------------

def test_shorts_y_tiktok_gancho_credito_y_pocas_etiquetas():
    for plat in ("shorts", "tiktok"):
        r = textos(TOMA, FICHA, plat)
        t = r["texto"]
        assert t.startswith("Toma 012. Primera vez en el mapa\n")
        assert "🎥 @ejemplo" in t and "Frase uno" not in t
        assert len([x for x in r["etiquetas"] if x.startswith("#")]) <= 3
        assert r["etiquetas"] == ["@marcaejemplo", "#Ejemplo", "#GTA6", "#Gaming"]


def test_instagram_valor_credito_y_de_3_a_5_etiquetas():
    t = textos(TOMA, FICHA_LIMPIA, "reels")["texto"]
    assert "Frase uno. Frase dos." in t and "Frase tres" not in t and "🎥 @guionvivo" in t
    assert 3 <= len([x for x in t.split() if x.startswith("#")]) <= 5


def test_instagram_con_menos_de_3_etiquetas_se_descarta():
    with pytest.raises(Descartada, match="3 a 5"):
        textos({**TOMA, "etiquetas": ["#GTA6"]}, FICHA_LIMPIA, "reels")


def test_etiquetas_y_aviso_del_brief_van_tal_cual():
    t = textos(TOMA, FICHA, "shorts")["texto"]
    assert "#ad colaboración pagada" in t.splitlines()
    assert "@marcaejemplo #Ejemplo" in t


def test_informativa_usa_la_plantilla_y_el_credito_de_rockstar():
    toma = {"numero": 3, "pilar": "informativa", "titular": "Llega el 19 de noviembre",
            "datos": ["Sale en PS5", "Sale en Xbox Series X|S"]}
    t = textos(toma, FICHA_LIMPIA, "shorts")
    assert t["texto"] == ("GTA 6: Llega el 19 de noviembre\n\n- Sale en PS5\n- Sale en Xbox Series X|S\n\n"
                          "🎥 @RockstarGames")
    assert t["titulo"] == "GTA 6: Llega el 19 de noviembre"


def test_el_juego_se_escribe_gta_6():
    t = textos({**TOMA, "gancho": "Lo mejor de GTA VI y gta6"}, FICHA_LIMPIA, "shorts")["texto"]
    assert "GTA 6 y GTA 6" in t and "VI" not in t


def test_un_emoji_como_maximo():
    with pytest.raises(Descartada, match="emoji"):
        textos({**TOMA, "gancho": "Mira esto 🔥"}, FICHA_LIMPIA, "shorts")
    assert publicar.contar_emojis(textos(TOMA, FICHA_LIMPIA, "shorts")["texto"]) == 1


def test_nunca_sin_credito():
    with pytest.raises(Descartada, match="sin crédito"):
        textos(TOMA, {"id": "x", "creador": "", "reglas": {}}, "shorts")


def test_titulo_de_youtube_de_100_o_menos():
    larga = {**TOMA, "gancho": "palabra " * 40}
    t = textos(larga, FICHA_LIMPIA, "shorts")
    assert len(t["titulo"]) <= 100 and t["titulo"].startswith("Toma 012.")


def test_texto_repetido_en_7_dias_se_descarta(tmp_path):
    tx = textos(TOMA, FICHA_LIMPIA, "shorts", tmp_path, HOY)
    publicar._escribir_json(tmp_path / "estado" / "buffer-textos.json", [{
        "fecha": "2026-11-16", "plataforma": "shorts",
        "huella": publicar.hashlib.sha256(publicar._cuerpo_sin_toma(tx["texto"]).encode()).hexdigest()}])
    with pytest.raises(Descartada, match="repetido"):
        textos({**TOMA, "numero": 13}, FICHA_LIMPIA, "shorts", tmp_path, HOY)
    textos({**TOMA, "numero": 13}, FICHA_LIMPIA, "tiktok", tmp_path, HOY)      # otra plataforma, otro registro
    textos({**TOMA, "numero": 13}, FICHA_LIMPIA, "shorts", tmp_path, HOY + timedelta(days=8))


def test_gancho_repetido_segun_el_marcador(tmp_path):
    with open(tmp_path / "marcador.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(registro.CABECERA_MARCADOR)
        fila = dict.fromkeys(registro.CABECERA_MARCADOR, "")
        fila.update(toma="5", fecha="2026-11-15", plataforma="shorts", gancho="Primera vez en el mapa")
        w.writerow([fila[c] for c in registro.CABECERA_MARCADOR])
    with pytest.raises(Descartada, match="gancho repetido"):
        textos(TOMA, FICHA_LIMPIA, "shorts", tmp_path, HOY)
    textos(TOMA, FICHA_LIMPIA, "shorts", tmp_path, HOY + timedelta(days=8))


# ---------------- cuota ----------------

def test_la_cuota_se_detiene_al_80_por_ciento(tmp_path):
    esp = F.Espia([F.resp(200, F.leer("buffer", "organizacion.json"))])
    c = ClienteBuffer(CLAVE, esp, tmp_path, dormir=lambda s: None, ahora=lambda: AHORA)
    for _ in range(120):                       # 80 % de 150 en la ventana de 15 minutos
        c.organizaciones()
    with pytest.raises(ErrorCuota, match="80 %"):
        c.organizaciones()
    assert len(esp.llamadas) == 120
    estado = json.loads((tmp_path / "estado" / "buffer-cuota.json").read_text(encoding="utf-8"))
    assert estado["llamadas_dia"] == 120 and estado["llamadas_mes"] == 120
    assert any("no se llama más" in n for n in estado["notas"])


def test_la_ventana_de_15_minutos_se_libera(tmp_path):
    q = Cuota(tmp_path, lambda: AHORA)
    for _ in range(120):
        q.registrar()
    with pytest.raises(ErrorCuota):
        q.comprobar()
    Cuota(tmp_path, lambda: AHORA + timedelta(minutes=16)).comprobar()   # la ventana ya pasó (día: 120/600)


def test_cuota_por_canal_y_resumen(tmp_path):
    q = Cuota(tmp_path, lambda: AHORA)
    for _ in range(800):
        q.registrar("canal-yt-001")
    # 800 llamadas: el día (tope 600, 80 % = 480) se detiene antes que el canal (tope 1000)
    with pytest.raises(ErrorCuota, match="dia"):
        Cuota(tmp_path, lambda: AHORA + timedelta(hours=1)).comprobar("canal-yt-001")
    assert Cuota(tmp_path, lambda: AHORA + timedelta(days=1)).resumen()["dia"] == "0/600"


# ---------------- modos ----------------

def entorno_real():
    return {"BUFFER_API_KEY": CLAVE, "SUPABASE_URL": SUPA_URL, "SUPABASE_SERVICE_KEY": SUPA_CLAVE}


def test_con_pausa_no_hay_ninguna_llamada(repo):
    esp = F.Espia([F.resp(200, "{}")])
    sal = []
    cod = publicar.ejecutar(repo, env=entorno_real(), salida=sal.append, transporte_buffer=esp,
                            transporte_almacen=esp, ahora=lambda: AHORA)
    assert cod == 0 and esp.llamadas == [] and "PAUSA" in sal[0]


def test_con_pausa_ni_borrador_ni_vivo_llaman(repo, editar_yaml):
    esp = F.Espia([F.resp(200, "{}")])
    editar_yaml(repo, "config/modo.yml", lambda d: {**d, "modo": "vivo", "plataformas_activas": ["shorts"]})
    assert publicar.ejecutar(repo, con_borrador=True, env=entorno_real(), salida=lambda s: None,
                             transporte_buffer=esp) == 0
    assert publicar.ejecutar(repo, env=entorno_real(), salida=lambda s: None, transporte_buffer=esp) == 0
    assert esp.llamadas == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_ensayo_con_claves_nunca_hace_createpost_y_guarda_los_ids(repo):
    sin_pausa(repo)
    sim, supa, sal = BufferSimulado(), SupabaseSimulado(), []
    cod = publicar.ejecutar(repo, env=entorno_real(), salida=sal.append, transporte_buffer=sim,
                            transporte_almacen=supa, ahora=lambda: AHORA)
    assert cod == 0
    assert not sim.hubo("createPost") and not sim.hubo("deletePost")
    assert yaml.safe_load((repo / "config" / "canales.yml").read_text(encoding="utf-8"))[
        "shorts"]["buffer_channel_id"] == "canal-yt-001"
    texto = "\n".join(sal)
    assert "Canal confirmado: shorts -> youtube · Guion Vivo" in texto and "canal-yt-001" not in texto
    assert "Almacén ida y vuelta: subida sí, URL pública comprobada sí, borrado sí" in texto
    assert "Cuota usada (Buffer)" in texto and "simulado" not in texto
    assert supa.objetos == {}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_ensayo_sin_claves_se_simula_y_dice_pendiente_de_kevin(repo):
    sin_pausa(repo)
    sal = []
    antes = (repo / "config" / "canales.yml").read_text(encoding="utf-8")
    assert publicar.ejecutar(repo, env={}, salida=sal.append, ahora=lambda: AHORA) == 0
    texto = "\n".join(sal)
    for s in ("sin BUFFER_API_KEY", "sin SUPABASE_URL", "sin SUPABASE_SERVICE_KEY", "pendiente de Kevin"):
        assert s in texto
    assert (repo / "config" / "canales.yml").read_text(encoding="utf-8") == antes
    assert not (repo / "PAUSA").exists()


def test_ensayo_informa_de_campos_que_faltan(repo):
    sin_pausa(repo)
    sal = []
    sim = BufferSimulado(esquema="esquema_falta_obligatorio.json")
    cod = publicar.ejecutar(repo, env={"BUFFER_API_KEY": CLAVE}, salida=sal.append, transporte_buffer=sim,
                            ahora=lambda: AHORA)
    assert cod == 1 and any("FALTA o SOBRA" in s and "license" in s for s in sal)
    assert not sim.hubo("createPost")


def test_ensayo_con_canal_ajeno_es_alerta_roja_y_crea_pausa(repo):
    sin_pausa(repo)
    sal = []
    cod = publicar.ejecutar(repo, env={"BUFFER_API_KEY": CLAVE}, salida=sal.append,
                            transporte_buffer=BufferSimulado(canales="canales_ajeno.json"),
                            ahora=lambda: AHORA)
    assert cod == 3 and (repo / "PAUSA").exists()
    assert "no confirmado" in (repo / "PAUSA").read_text(encoding="utf-8")
    assert (repo / "estado" / "alerta-roja.json").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_borrador_hace_createpost_con_savetodraft_y_deletepost(repo):
    sin_pausa(repo)
    sim, sal = BufferSimulado(), []
    cod = publicar.ejecutar(repo, con_borrador=True, env=entorno_real(), salida=sal.append,
                            transporte_buffer=sim, transporte_almacen=SupabaseSimulado(), ahora=lambda: AHORA)
    assert cod == 0
    creaciones = [v["input"] for q, v in zip(sim.consultas, sim.variables) if "createPost" in q]
    assert len(creaciones) == 3 and all(c["saveToDraft"] is True for c in creaciones)
    borrados = [v["input"] for q, v in zip(sim.consultas, sim.variables) if "deletePost" in q]
    assert borrados == [{"id": "post-ejemplo-001"}] * 3
    assert "Borrador reels: creado y borrado (post-ejemplo-001)" in sal


def test_borrador_sin_clave_no_se_simula(repo):
    sin_pausa(repo)
    sal = []
    assert publicar.ejecutar(repo, con_borrador=True, env={}, salida=sal.append) == 1
    assert "no se puede simular" in sal[0]


def test_borrador_sin_savetodraft_en_el_esquema_no_crea_nada(tmp_path):
    e = esquema(tmp_path)
    del e["tipos"]["CreatePostInput"]["saveToDraft"]
    sim = BufferSimulado()
    pub = Publicador(cliente(tmp_path, sim), None, tmp_path, "borrador", lambda: AHORA,
                     {"shorts": "canal-yt-001"}, e)
    with pytest.raises(Descartada, match="saveToDraft"):
        pub.borrador("shorts", "https://x.invalid/v.mp4", AHORA + timedelta(days=1))
    assert not sim.hubo("createPost")


def test_en_ensayo_el_publicador_se_niega_a_crear(tmp_path):
    sim = BufferSimulado()
    pub = Publicador(cliente(tmp_path, sim), None, tmp_path, "ensayo", lambda: AHORA, {"shorts": "c"})
    with pytest.raises(ErrorBuffer):
        pub._crear({"text": "x"}, "c")
    with pytest.raises(ErrorBuffer, match="modo vivo"):
        pub.programar("shorts", TOMA, "u", {"texto": "t"}, AHORA)
    assert sim.consultas == []


# ---------------- registro, estado y limpieza ----------------

def test_programar_anota_la_fila_con_anotar_clip_si_existe(tmp_path, monkeypatch):
    filas = []
    monkeypatch.setattr(registro, "anotar_clip", lambda raiz, fila: filas.append(fila), raising=False)
    monkeypatch.setattr(publicar, "_anotar_fila", lambda *a: pytest.fail("no debía usarse"))
    pub, _ = vivo(tmp_path)
    toma = {**TOMA, "fuente_id": "propio", "duracion_s": 18, "campana": ""}
    pub.programar("shorts", toma, "https://x.invalid/v.mp4", textos(toma, FICHA_LIMPIA, "shorts"),
                  hueco_a_utc(HOY, "11:00"), "tomas/2026-11-18/toma-012-ab12cd34.mp4")
    f = filas[0]
    assert f["id_clip"] == "toma-012-shorts" and f["fecha"] == "2026-11-18" and f["hora"] == "11:00"
    assert f["plataforma"] == "shorts" and f["fuente_id"] == "propio" and f["duracion_s"] == 18
    assert f["url"] == "" and f["estado_campana"] == "" and f["credito"] == "🎥 @guionvivo"
    assert f["notas"] == "origen:propio buffer:post-ejemplo-001"
    assert set(f) <= set(registro.CABECERA_MARCADOR)


def test_sin_anotar_clip_se_usa_la_fila_local_y_queda_en_el_marcador(tmp_path, monkeypatch):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    pub, _ = vivo(tmp_path)
    pub.programar("tiktok", TOMA, "https://x.invalid/v.mp4", textos(TOMA, FICHA_LIMPIA, "tiktok"),
                  hueco_a_utc(HOY, "11:00"))
    with open(tmp_path / "marcador.csv", newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    assert filas[0]["plataforma"] == "tiktok" and filas[0]["notas"].endswith("buffer:post-ejemplo-001")


def test_estado_con_consulta_por_id_del_esquema(tmp_path):
    sim = BufferSimulado()
    pub, _ = vivo(tmp_path, sim)
    assert pub.estado("post-ejemplo-001") == "sent"
    assert 'post(input: { id: "post-ejemplo-001" })' in sim.consultas[-1]


def test_estado_sin_consulta_por_id_usa_posts(tmp_path):
    sim = BufferSimulado(esquema="esquema_sin_post.json")
    pub, _ = vivo(tmp_path, sim)
    assert pub.estado("post-ejemplo-001") == "sent"
    assert "posts(input:" in sim.consultas[-1] and "status: [sent]" in sim.consultas[-1]
    assert pub.estado("otro-post") is None


def almacen_con_video(tmp_path, ruta):
    supa = SupabaseSimulado()
    alm = Almacen("https://simulado.invalid", "k", CONFIG_ALMACEN, supa)
    supa.objetos["/storage/v1/object/public/clips/" + ruta] = b"x"
    publicar._escribir_json(tmp_path / "estado" / "buffer-envios.json", [
        {"id": "shorts|12|2026-11-18T17:00:00.000Z", "post_id": "post-ejemplo-001", "plataforma": "shorts",
         "ruta": ruta, "canal": "canal-yt-001", "estado": "creado", "registrado": True,
         "dueAt": "2026-11-18T17:00:00.000Z", "texto": "t", "datos": {}}])
    return alm, supa


def test_limpiar_borra_del_almacen_lo_que_ya_esta_sent(tmp_path):
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    alm, supa = almacen_con_video(tmp_path, ruta)
    pub = Publicador(cliente(tmp_path), alm, tmp_path, "vivo", lambda: AHORA, {})
    assert pub.limpiar_enviados() == [ruta] and supa.objetos == {}
    libro = json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8"))
    assert libro[0]["ruta_borrada"] is True and libro[0]["estado"] == "creado"   # la memoria del envío se queda


def test_limpiar_conserva_el_video_si_el_post_aun_no_esta_sent(tmp_path):
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    alm, supa = almacen_con_video(tmp_path, ruta)
    pub = Publicador(cliente(tmp_path, estado_post="post_programado.json"), alm, tmp_path, "vivo",
                     lambda: AHORA, {})
    assert pub.limpiar_enviados() == [] and len(supa.objetos) == 1


# ---------------- vivo de punta a punta (simulado) ----------------

def confirmar_en_config(repo):
    publicar.guardar_ids_de_canales(
        repo, {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"},
        dict.fromkeys(("shorts", "tiktok", "reels"), "org-ejemplo-001"))


def test_vivo_solo_en_plataformas_activas_y_registra(repo, editar_yaml):
    sin_pausa(repo)
    confirmar_en_config(repo)
    editar_yaml(repo, "config/modo.yml", lambda d: {**d, "modo": "vivo", "plataformas_activas": ["shorts"]})
    mp4 = repo / "v.bin"
    mp4.write_bytes(b"video-inventado")
    entrada = {"mp4": str(mp4), "fuente_id": "propio-tipografico", "fecha": "2026-11-18", "hueco": "11:00",
               "toma": {"numero": 7, "pilar": "informativa", "titular": "Llega el 19 de noviembre",
                        "datos": ["Sale en PS5"], "duracion_s": 15}}
    ruta_entrada = repo / "entrada.json"
    ruta_entrada.write_text(json.dumps(entrada), encoding="utf-8")
    sim, supa, sal = BufferSimulado(), SupabaseSimulado(), []
    cod = publicar.ejecutar(repo, entrada=str(ruta_entrada), env=entorno_real(), salida=sal.append,
                            transporte_buffer=sim, transporte_almacen=supa, ahora=lambda: AHORA)
    assert cod == 0, sal
    assert sum("createPost" in q for q in sim.consultas) == 1
    assert sal[-1].startswith("shorts: programada post-ejemplo-001")
    filas = list(csv.DictReader(open(repo / "marcador.csv", newline="", encoding="utf-8")))
    assert [f["plataforma"] for f in filas] == ["shorts"] and filas[0]["fuente_id"] == "propio-tipografico"
    assert len(supa.objetos) == 1          # el video queda hasta que el post esté en sent


def test_vivo_sin_plataformas_activas_no_hace_nada(repo):
    sin_pausa(repo)
    (repo / "config" / "modo.yml").write_text("modo: vivo\nplataformas_activas: []\n", encoding="utf-8")
    (repo / "e.json").write_text(json.dumps({"fuente_id": "x", "toma": {}}), encoding="utf-8")
    esp = F.Espia([F.resp(200, "{}")])
    assert publicar.ejecutar(repo, entrada=str(repo / "e.json"), env=entorno_real(), salida=lambda s: None,
                             transporte_buffer=esp) == 0
    assert esp.llamadas == []


def test_vivo_descarta_una_fuente_fuera_de_la_lista_blanca(repo, editar_yaml):
    sin_pausa(repo)
    editar_yaml(repo, "config/modo.yml", lambda d: {**d, "modo": "vivo", "plataformas_activas": ["shorts"]})
    (repo / "e.json").write_text(json.dumps({"fuente_id": "no-registrada", "toma": {"numero": 1}}),
                                 encoding="utf-8")
    esp = F.Espia([F.resp(200, "{}")])
    sal = []
    assert publicar.ejecutar(repo, entrada=str(repo / "e.json"), env=entorno_real(), salida=sal.append,
                             transporte_buffer=esp) == 0
    assert esp.llamadas == [] and "lista blanca" in sal[0]


# ---------------- secretos ----------------

def test_la_clave_no_aparece_en_errores_ni_en_archivos(tmp_path):
    esp = F.Espia([F.resp(200, {"errors": [{"message": f"clave {CLAVE} rechazada"}]})])
    c = ClienteBuffer(CLAVE, esp, tmp_path)
    with pytest.raises(ErrorBuffer) as e:
        c.organizaciones()
    assert CLAVE not in str(e.value) and "***" in str(e.value)
    esp2 = F.Espia([F.resp(401, "{}")])
    with pytest.raises(ErrorBuffer) as e2:
        ClienteBuffer(CLAVE, esp2, tmp_path, dormir=lambda s: None).organizaciones()
    assert CLAVE not in str(e2.value) and CLAVE not in repr(e2.value)
    assert CLAVE not in (tmp_path / "estado" / "buffer-cuota.json").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_ninguna_clave_en_la_salida_del_ensayo_ni_en_el_repo(repo):
    sin_pausa(repo)
    sal = []
    publicar.ejecutar(repo, env=entorno_real(), salida=sal.append, transporte_buffer=BufferSimulado(),
                      transporte_almacen=SupabaseSimulado(), ahora=lambda: AHORA)
    todo = "\n".join(sal)
    assert CLAVE not in todo and SUPA_CLAVE not in todo and SUPA_URL not in todo
    for p in repo.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".yml", ".md", ".csv"):
            texto = p.read_text(encoding="utf-8", errors="ignore")
            assert CLAVE not in texto and SUPA_CLAVE not in texto, p


def test_sin_clave_el_cliente_no_se_construye():
    with pytest.raises(ErrorBuffer, match="falta BUFFER_API_KEY"):
        ClienteBuffer("")


# ================= ronda 1: mutaciones sin reintento, reconciliación, orden de subida, borrador =================

class Transporte503Luego200:
    """createPost: 503 la primera vez y 200 después; el resto lo sirve BufferSimulado. Cuenta createPost."""

    def __init__(self, creado_de_verdad=True):
        self.sim = BufferSimulado()
        self.creaciones = 0
        self.libro_al_enviar = None
        self.ruta_libro = None

    def __call__(self, metodo, url, cab, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode())["query"]
        if "createPost" in q:
            self.creaciones += 1
            if self.ruta_libro is not None and self.ruta_libro.exists():
                self.libro_al_enviar = json.loads(self.ruta_libro.read_text(encoding="utf-8"))
            return (503, {}, b"{}") if self.creaciones == 1 else self.sim(metodo, url, cab, cuerpo, tiempo)
        return self.sim(metodo, url, cab, cuerpo, tiempo)


def test_503_seguido_de_200_envia_un_solo_createpost(tmp_path):
    t = Transporte503Luego200()
    c = ClienteBuffer(CLAVE, t, tmp_path, dormir=lambda s: None)
    with pytest.raises(publicar.ErrorIncierto):
        c.consulta(publicar.M_CREAR, {"input": {}}, mutacion=True)
    assert t.creaciones == 1


def test_una_lectura_si_se_reintenta_pero_una_mutacion_con_429_no(tmp_path):
    esp = F.Espia([F.resp(429, "{}"), F.resp(200, "{}")])
    with pytest.raises(ErrorBuffer, match="429"):
        ClienteBuffer(CLAVE, esp, tmp_path, dormir=lambda s: None).consulta(publicar.M_CREAR, mutacion=True)
    assert len(esp.llamadas) == 1


@pytest.mark.parametrize("respuesta", [F.resp(502, "{}"), F.resp(200, "esto no es json")])
def test_resultado_ilegible_o_5xx_de_una_mutacion_es_incierto(tmp_path, respuesta):
    esp = F.Espia([respuesta])
    with pytest.raises(publicar.ErrorIncierto):
        ClienteBuffer(CLAVE, esp, tmp_path, dormir=lambda s: None).consulta(publicar.M_CREAR, mutacion=True)
    assert len(esp.llamadas) == 1


def test_un_timeout_de_una_mutacion_es_incierto(tmp_path):
    from fabrica import red

    def corta(*a, **k):
        raise red.ErrorRed("POST /: sin respuesta (TimeoutError)")
    with pytest.raises(publicar.ErrorIncierto, match="incierto"):
        ClienteBuffer(CLAVE, corta, tmp_path).consulta(publicar.M_CREAR, mutacion=True)


def programar_una_vez(tmp_path, t, tx=None, toma=TOMA):
    c = ClienteBuffer(CLAVE, t, tmp_path, dormir=lambda s: None, ahora=lambda: AHORA)
    ids = {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"}
    pub = Publicador(c, None, tmp_path, "vivo", lambda: AHORA, ids, orgs={"shorts": "org-ejemplo-001"})
    tx = tx or textos(toma, FICHA_LIMPIA, "shorts")
    return pub, lambda: pub.programar("shorts", {**toma, "fuente_id": "propio"}, "https://x.invalid/v.mp4", tx,
                                      hueco_a_utc(HOY, "11:00"), "tomas/2026-11-18/toma-012-ab12cd34.mp4")


def con_posts(t, texto, vacio=False):
    """Hace que el listado de posts del canal incluya el post que Buffer sí creó (o venga vacío)."""
    posts = F.leer("buffer", "posts_del_canal.json").replace("__TEXTO__", texto.replace("\n", "\\n"))
    if vacio:
        posts = '{"data": {"posts": {"edges": []}}}'
    original = t.sim.__call__

    def llamar(metodo, url, cab, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode())["query"]
        if "posts(" in q and "channelIds" in q and "status: [sent]" not in q:
            return F.resp(200, posts)
        return original(metodo, url, cab, cuerpo, tiempo)
    t.sim = type("S", (), {"__call__": staticmethod(llamar), "hubo": t.sim.hubo, "consultas": t.sim.consultas})()
    return t


def test_un_500_tras_una_creacion_real_no_duplica_y_se_reconcilia(tmp_path, monkeypatch):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    tx = textos(TOMA, FICHA_LIMPIA, "shorts")
    t = con_posts(Transporte503Luego200(), tx["texto"])
    pub, programar = programar_una_vez(tmp_path, t, tx)
    with pytest.raises(publicar.ResultadoIncierto):
        programar()
    libro = json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8"))
    assert [e["estado"] for e in libro] == ["incierto"]
    # corrida siguiente: reconcilia (el post existe) y NO vuelve a enviar
    with pytest.raises(Descartada, match="ya hay un envío creado"):
        programar()
    assert t.creaciones == 1
    libro = json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8"))
    assert libro[0]["estado"] == "creado" and libro[0]["post_id"] == "post-ejemplo-001"
    filas = list(csv.DictReader(open(tmp_path / "marcador.csv", newline="", encoding="utf-8")))
    assert len(filas) == 1 and filas[0]["notas"].endswith("buffer:post-ejemplo-001")


def test_la_intencion_se_anota_antes_de_enviar(tmp_path, monkeypatch):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    t = Transporte503Luego200()
    t.ruta_libro = tmp_path / "estado" / "buffer-envios.json"
    pub, programar = programar_una_vez(tmp_path, t)
    with pytest.raises(publicar.ResultadoIncierto):
        programar()
    e = t.libro_al_enviar[0]
    assert e["estado"] == "enviando" and e["plataforma"] == "shorts" and e["toma"] == 12
    assert e["dueAt"] == "2026-11-18T17:00:00.000Z"


def test_si_falla_la_escritura_local_tras_un_exito_el_post_queda_creado_en_la_siguiente(tmp_path, monkeypatch):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    tx = textos(TOMA, FICHA_LIMPIA, "shorts")
    t = con_posts(Transporte503Luego200(), tx["texto"])
    t.creaciones = 1          # la creación ya "falló" una vez: ahora responde bien
    pub, programar = programar_una_vez(tmp_path, t, tx)
    original = Publicador._cerrar_creado
    monkeypatch.setattr(Publicador, "_cerrar_creado",
                        lambda self, *a: (_ for _ in ()).throw(OSError("disco lleno")))
    r = programar()                       # no lanza: el post se creó
    assert r["id"] == "post-ejemplo-001"
    libro = json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8"))
    assert libro[0]["estado"] == "enviando"
    monkeypatch.setattr(Publicador, "_cerrar_creado", original)
    assert pub.reconciliar() == {libro[0]["id"]: "creado"}
    filas = list(csv.DictReader(open(tmp_path / "marcador.csv", newline="", encoding="utf-8")))
    assert len(filas) == 1


def test_si_el_marcador_falla_tras_un_createpost_correcto_no_se_pierde(tmp_path, monkeypatch):
    llamadas = []

    def anota(raiz, fila):
        llamadas.append(fila)
        if len(llamadas) == 1:
            raise ValueError("columna rara")
    monkeypatch.setattr(registro, "anotar_clip", anota, raising=False)
    t = Transporte503Luego200()
    t.creaciones = 1
    pub, programar = programar_una_vez(tmp_path, t)
    assert programar()["id"] == "post-ejemplo-001"          # la excepción no sale
    libro = json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8"))
    assert libro[0]["estado"] == "creado" and libro[0]["registrado"] is False
    pub.reconciliar()
    libro = json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8"))
    assert libro[0]["registrado"] is True and len(llamadas) == 2


# ---- vivo: validar antes de subir, no dejar huérfanos ----

def vivo_listo(repo, editar_yaml, **entrada_extra):
    sin_pausa(repo)
    confirmar_en_config(repo)
    editar_yaml(repo, "config/modo.yml", lambda d: {**d, "modo": "vivo", "plataformas_activas": ["shorts"]})
    mp4 = repo / "v.bin"
    mp4.write_bytes(b"video-inventado")
    entrada = {"mp4": str(mp4), "fuente_id": "propio-tipografico", "fecha": "2026-11-18", "hueco": "11:00",
               "toma": {"numero": 7, "pilar": "informativa", "titular": "Llega el 19 de noviembre",
                        "datos": ["Sale en PS5"], "duracion_s": 15}}
    entrada.update(entrada_extra)
    (repo / "entrada.json").write_text(json.dumps(entrada), encoding="utf-8")
    return str(repo / "entrada.json")


def correr_vivo(repo, entrada, sim=None, supa=None, ahora=AHORA):
    sal = []
    cod = publicar.ejecutar(repo, entrada=entrada, env=entorno_real(), salida=sal.append,
                            transporte_buffer=sim or BufferSimulado(), transporte_almacen=supa or SupabaseSimulado(),
                            ahora=lambda: ahora)
    return cod, sal


def subidas(supa):
    return [x for x in supa.llamadas if x[0] == "POST" and "/object/" in x[1]]


def test_vivo_sin_id_guardado_no_programa_ni_sube(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml)
    texto = (repo / "config" / "canales.yml").read_text(encoding="utf-8")
    (repo / "config" / "canales.yml").write_text(
        texto.replace("buffer_channel_id: canal-yt-001", "buffer_channel_id: null"), encoding="utf-8")
    sim, supa = BufferSimulado(), SupabaseSimulado()
    cod, sal = correr_vivo(repo, entrada, sim, supa)
    assert cod == 1 and "ensayo con claves" in "\n".join(sal)
    assert not sim.hubo("createPost") and subidas(supa) == []


def test_vivo_con_id_guardado_distinto_es_alerta_roja_y_no_programa(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml)
    texto = (repo / "config" / "canales.yml").read_text(encoding="utf-8")
    (repo / "config" / "canales.yml").write_text(
        texto.replace("buffer_channel_id: canal-yt-001", "buffer_channel_id: canal-de-otro"), encoding="utf-8")
    sim, supa = BufferSimulado(), SupabaseSimulado()
    cod, _ = correr_vivo(repo, entrada, sim, supa)
    assert cod == 3 and not sim.hubo("createPost") and subidas(supa) == []
    assert (repo / "PAUSA").exists()


def test_vivo_con_hueco_pasado_no_sube_nada(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml, hueco="05:00")
    sim, supa = BufferSimulado(), SupabaseSimulado()
    cod, sal = correr_vivo(repo, entrada, sim, supa)
    assert cod == 0 and subidas(supa) == [] and not sim.hubo("createPost")
    assert any("antes de subir" in s for s in sal)


def test_vivo_con_texto_invalido_no_sube_nada(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml, toma={"numero": 7, "pilar": "informativa"})
    supa = SupabaseSimulado()
    cod, sal = correr_vivo(repo, entrada, supa=supa)
    assert cod == 0 and subidas(supa) == [] and any("antes de subir" in s for s in sal)


def test_vivo_con_carga_que_el_esquema_rechaza_no_sube_nada(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml)
    supa = SupabaseSimulado()
    cod, _ = correr_vivo(repo, entrada, BufferSimulado(esquema="esquema_falta_obligatorio.json"), supa)
    assert cod == 0 and subidas(supa) == []


def test_vivo_si_todos_los_destinos_se_rechazan_borra_el_video(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml)
    supa = SupabaseSimulado()
    cod, sal = correr_vivo(repo, entrada, BufferSimulado(crear="createpost_error.json"), supa)
    assert cod == 0 and len(subidas(supa)) == 1 and supa.objetos == {}
    assert any("se borró del almacén" in s for s in sal)


def test_vivo_con_resultado_incierto_conserva_el_video(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml)
    t = Transporte503Luego200()
    supa = SupabaseSimulado()
    cod, sal = correr_vivo(repo, entrada, t, supa)
    assert cod == 1 and len(supa.objetos) == 1 and t.creaciones == 1
    assert any("INCIERTO" in s for s in sal)


def test_soltar_si_huerfano_respeta_los_envios_vivos(tmp_path):
    supa = SupabaseSimulado()
    alm = Almacen("https://simulado.invalid", "k", CONFIG_ALMACEN, supa)
    pub = Publicador(cliente(tmp_path), alm, tmp_path, "vivo", lambda: AHORA, {})
    ruta = "tomas/2026-11-18/toma-001-aaaaaaaa.mp4"
    supa.objetos["/storage/v1/object/public/clips/" + ruta] = b"x"
    publicar._escribir_json(tmp_path / "estado" / "buffer-envios.json", [{"id": "a", "ruta": ruta, "estado": "incierto"}])
    assert pub.soltar_si_huerfano(ruta) is False and len(supa.objetos) == 1
    publicar._escribir_json(tmp_path / "estado" / "buffer-envios.json", [{"id": "a", "ruta": ruta, "estado": "rechazado"}])
    assert pub.soltar_si_huerfano(ruta) is True and supa.objetos == {}


def test_limpiar_enviados_es_publica_y_conserva_lo_incierto(tmp_path, monkeypatch):
    monkeypatch.setattr(Publicador, "reconciliar", lambda self: {})
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    alm, supa = almacen_con_video(tmp_path, ruta)
    libro = json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8"))
    libro[0].update(estado="incierto", post_id=None)
    publicar._escribir_json(tmp_path / "estado" / "buffer-envios.json", libro)
    sim = BufferSimulado()
    pub = Publicador(cliente(tmp_path, sim), alm, tmp_path, "vivo", lambda: AHORA,
                     {"shorts": "canal-yt-001"}, orgs={"shorts": "org-ejemplo-001"})
    assert "limpiar_enviados" in Publicador.__doc__ and pub.limpiar_enviados() == [] and len(supa.objetos) == 1


# ---- borrador: el borrado exige un tipo de éxito ----

@pytest.mark.parametrize("archivo", ["deletepost_nulo.json", "deletepost_sin_tipo.json",
                                     "deletepost_error.json", "deletepost_tipo_raro.json"])
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_borrador_sin_tipo_de_exito_no_cuenta_como_borrado(repo, archivo):
    sin_pausa(repo)
    sal = []
    cod = publicar.ejecutar(repo, con_borrador=True, env=entorno_real(), salida=sal.append,
                            transporte_buffer=BufferSimulado(borrar=archivo),
                            transporte_almacen=SupabaseSimulado(), ahora=lambda: AHORA)
    assert cod == 1
    assert any("BORRADOR SIN BORRAR" in s and "post-ejemplo-001" in s for s in sal)
    assert not any("creado y borrado" in s for s in sal)


def test_borrador_devuelve_borrado_false_y_conserva_el_id(tmp_path):
    pub = Publicador(cliente(tmp_path, BufferSimulado(borrar="deletepost_nulo.json")), None, tmp_path,
                     "borrador", lambda: AHORA, {"shorts": "canal-yt-001"})
    r = pub.borrador("shorts", "https://x.invalid/v.mp4", AHORA + timedelta(days=1))
    assert r["borrado"] is False and r["id"] == "post-ejemplo-001" and r["motivo"]


def test_borrador_con_exito_explicito_si_cuenta(tmp_path):
    pub = Publicador(cliente(tmp_path), None, tmp_path, "borrador", lambda: AHORA, {"shorts": "canal-yt-001"})
    assert pub.borrador("shorts", "https://x.invalid/v.mp4", AHORA + timedelta(days=1))["borrado"] is True


# ---- ensayo: un error de red del almacén no es un traceback ----

def test_ensayo_captura_el_error_de_red_del_almacen(repo):
    from fabrica import red
    sin_pausa(repo)

    def cae(*a, **k):
        raise red.ErrorRed("POST /storage/v1/bucket: sin respuesta (TimeoutError)")
    sal = []
    cod = publicar.ejecutar(repo, env=entorno_real(), salida=sal.append, transporte_buffer=BufferSimulado(),
                            transporte_almacen=cae, ahora=lambda: AHORA)
    assert cod == 1 and any("ERROR del almacén" in s for s in sal)
    assert SUPA_CLAVE not in "\n".join(sal)


# ================= ronda 2: un incierto no se reenvía nunca =================

LIBRO = lambda tmp: tmp / "estado" / "buffer-envios.json"


def leer_libro(tmp):
    return json.loads(LIBRO(tmp).read_text(encoding="utf-8"))


class Listado:
    """Transporte: createPost responde 503 la primera vez; el listado de posts se sirve por páginas."""

    def __init__(self, paginas):
        self.sim = BufferSimulado()
        self.paginas = paginas          # lista de listas de nodos; el índice es la página
        self.creaciones = 0
        self.lecturas = 0

    def __call__(self, metodo, url, cab, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode())["query"]
        if "createPost" in q:
            self.creaciones += 1
            return (503, {}, b"{}") if self.creaciones == 1 else self.sim(metodo, url, cab, cuerpo, tiempo)
        if "posts(" in q and "first:" in q and "status: [sent]" not in q:
            self.lecturas += 1
            i = 0
            if "after:" in q:
                i = int(q.split('after: "p')[1].split('"')[0])
            sig = i + 1 < len(self.paginas)
            return F.resp(200, {"data": {"posts": {
                "edges": [{"node": n} for n in self.paginas[i]],
                "pageInfo": {"hasNextPage": sig, "endCursor": f"p{i + 1}" if sig else None}}}})
        return self.sim(metodo, url, cab, cuerpo, tiempo)


def nodo(texto):
    return {"id": "post-ejemplo-001", "status": "scheduled", "dueAt": "2026-11-18T17:00:00.000Z", "text": texto}


def incierto_listo(tmp_path, paginas, monkeypatch, ahora=AHORA):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    tx = textos(TOMA, FICHA_LIMPIA, "shorts")
    t = Listado([[nodo(tx["texto"])] if p == "post" else [] if p == "vacia" else p for p in paginas])
    pub, programar = programar_una_vez(tmp_path, t, tx)
    with pytest.raises(publicar.ResultadoIncierto):
        programar()
    return t, pub, programar, tx


def test_post_no_visible_en_la_primera_pagina_se_encuentra_en_la_segunda(tmp_path, monkeypatch):
    t, pub, programar, tx = incierto_listo(tmp_path, ["vacia", "post"], monkeypatch)
    assert pub.reconciliar() and leer_libro(tmp_path)[0]["estado"] == "creado"
    assert t.lecturas == 2 and t.creaciones == 1


def test_un_incierto_que_no_aparece_sigue_incierto_y_nunca_se_reenvia(tmp_path, monkeypatch):
    t, pub, programar, tx = incierto_listo(tmp_path, ["vacia"], monkeypatch)
    assert pub.reconciliar() == {}
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"
    with pytest.raises(Descartada, match="ya hay un envío incierto"):
        programar()
    assert t.creaciones == 1


def test_un_post_que_aparece_tarde_pasa_a_creado(tmp_path, monkeypatch):
    t, pub, programar, tx = incierto_listo(tmp_path, ["vacia"], monkeypatch)
    pub.reconciliar()
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"
    t.paginas = [[nodo(tx["texto"])]]
    pub.reconciliar()
    e = leer_libro(tmp_path)[0]
    assert e["estado"] == "creado" and e["post_id"] == "post-ejemplo-001" and e["registrado"] is True
    assert t.creaciones == 1


def test_pasadas_24_horas_del_dueat_queda_abandonado_y_sigue_sin_reenviarse(tmp_path, monkeypatch):
    t, pub, programar, tx = incierto_listo(tmp_path, ["vacia"], monkeypatch)
    pub.ahora = lambda: datetime(2026, 11, 19, 17, 1, tzinfo=timezone.utc)   # 24 h y 1 min después
    assert pub.reconciliar() == {"shorts|12|2026-11-18T17:00:00.000Z": "abandonado"}
    assert leer_libro(tmp_path)[0]["estado"] == "abandonado"
    pub.ahora = lambda: AHORA
    with pytest.raises(Descartada, match="abandonado"):
        programar()
    # tampoco para otra toma en el mismo hueco
    otra = {**TOMA, "numero": 13, "gancho": "Otro gancho distinto"}
    with pytest.raises(Descartada, match="ya hay un envío"):
        pub.programar("shorts", otra, "https://x.invalid/v.mp4", textos(otra, FICHA_LIMPIA, "shorts"),
                      hueco_a_utc(HOY, "11:00"))
    assert t.creaciones == 1


def test_antes_de_las_24_horas_no_se_abandona(tmp_path, monkeypatch):
    t, pub, programar, tx = incierto_listo(tmp_path, ["vacia"], monkeypatch)
    pub.ahora = lambda: datetime(2026, 11, 19, 16, 59, tzinfo=timezone.utc)
    pub.reconciliar()
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"


def test_lectura_no_concluyente_no_cambia_nada(tmp_path, monkeypatch):
    llenas = [[{"id": f"otro-{i}", "status": "sent", "dueAt": "2026-11-10T10:00:00.000Z", "text": "x"}]
              for i in range(8)]
    t, pub, programar, tx = incierto_listo(tmp_path, llenas, monkeypatch)
    pub.ahora = lambda: datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)     # mucho después de 24 h
    assert pub.reconciliar() == {}
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"
    assert t.lecturas == publicar.PAGINAS_MAX


def test_un_error_de_lectura_no_cambia_nada(tmp_path, monkeypatch):
    t, pub, programar, tx = incierto_listo(tmp_path, ["vacia"], monkeypatch)
    pub.cliente.transporte = lambda *a, **k: (500, {}, b"{}")
    pub.ahora = lambda: datetime(2026, 11, 25, tzinfo=timezone.utc)
    assert pub.reconciliar() == {}
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"


def test_el_video_de_un_incierto_no_se_borra_hasta_creado_y_sent_o_abandonado(tmp_path, monkeypatch):
    t, pub, programar, tx = incierto_listo(tmp_path, ["vacia"], monkeypatch)
    supa = SupabaseSimulado()
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    supa.objetos["/storage/v1/object/public/clips/" + ruta] = b"x"
    pub.almacen = Almacen("https://simulado.invalid", "k", CONFIG_ALMACEN, supa)
    assert pub.limpiar_enviados() == [] and len(supa.objetos) == 1
    pub.ahora = lambda: datetime(2026, 11, 19, 17, 1, tzinfo=timezone.utc)
    assert pub.limpiar_enviados() == [ruta] and supa.objetos == {}      # abandonado: libera el video
    assert leer_libro(tmp_path)[0]["estado"] == "abandonado"             # y la memoria se queda


# ---- resultado incompleto = incierto; sólo MutationError cierra como rechazado ----

@pytest.mark.parametrize("archivo", ["createpost_nulo.json", "createpost_sin_id.json",
                                     "createpost_tipo_raro.json", "createpost_vacio.json",
                                     "createpost_con_errors.json"])
def test_un_200_incompleto_es_incierto_no_rechazado(tmp_path, monkeypatch, archivo):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    pub, _ = vivo(tmp_path, crear=archivo)
    with pytest.raises(publicar.ResultadoIncierto):
        pub.programar("shorts", TOMA, "https://x.invalid/v.mp4", textos(TOMA, FICHA_LIMPIA, "shorts"),
                      hueco_a_utc(HOY, "11:00"), "tomas/2026-11-18/toma-012-ab12cd34.mp4")
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"


@pytest.mark.parametrize("archivo", ["createpost_error.json", "createpost_mutationerror.json"])
def test_sólo_un_mutationerror_con_mensaje_es_rechazado(tmp_path, archivo):
    pub, _ = vivo(tmp_path, crear=archivo)
    with pytest.raises(Descartada, match="rechazó"):
        pub.programar("shorts", TOMA, "https://x.invalid/v.mp4", textos(TOMA, FICHA_LIMPIA, "shorts"),
                      hueco_a_utc(HOY, "11:00"))
    assert leer_libro(tmp_path)[0]["estado"] == "rechazado"


def test_vivo_con_respuesta_incompleta_conserva_el_video(repo, editar_yaml):
    entrada = vivo_listo(repo, editar_yaml)
    supa = SupabaseSimulado()
    cod, sal = correr_vivo(repo, entrada, BufferSimulado(crear="createpost_nulo.json"), supa)
    assert cod == 1 and len(supa.objetos) == 1 and any("INCIERTO" in s for s in sal)


# ---- candado entre procesos ----

SCRIPT_PROCESO = r'''
import sys, time, json
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fabrica import publicar
from fabrica.simulacion import BufferSimulado
raiz, contador, arranque = Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4])
sim = BufferSimulado()
def transporte(m, u, c, cuerpo=None, t=60):
    q = json.loads(cuerpo.decode())["query"]
    if "createPost" in q:
        with open(contador, "a") as f:
            f.write("x\n")
        time.sleep(0.6)
    return sim(m, u, c, cuerpo, t)
AHORA = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
cli = publicar.ClienteBuffer("clave-inventada", transporte, raiz / ("cuota" + sys.argv[5]), ahora=lambda: AHORA)
pub = publicar.Publicador(cli, None, raiz, "vivo", lambda: AHORA, {"shorts": "canal-yt-001"})
ficha = {"id": "propio", "creador": "@guionvivo", "reglas": {}}
toma = {"numero": 12, "gancho": "Primera vez en el mapa", "pilar": "momentos"}
tx = publicar.textos(toma, ficha, "shorts")
_libro = pub._libro
def libro_lento():            # ensancha la ventana entre leer y reservar para provocar la carrera
    datos = _libro()
    time.sleep(0.3)
    return datos
pub._libro = libro_lento
while time.time() < arranque:
    pass
try:
    pub.programar("shorts", toma, "https://x.invalid/v.mp4", tx, publicar.hueco_a_utc(AHORA.date(), "11:00"))
    print("PROGRAMADA")
except publicar.Descartada as e:
    print("DESCARTADA")
'''


def test_dos_procesos_sobre_el_mismo_envio_hacen_un_solo_createpost(tmp_path):
    import subprocess
    import sys
    import time
    raiz_repo_ = F.DATOS.parent.parent
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    contador = tmp_path / "createpost.txt"
    script = tmp_path / "proceso.py"
    script.write_text(SCRIPT_PROCESO, encoding="utf-8")
    arranque = time.time() + 2.0
    procs = [subprocess.Popen([sys.executable, str(script), str(raiz_repo_), str(tmp_path), str(contador),
                               str(arranque), str(i)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for i in (1, 2)]
    salidas = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), salidas
    assert sorted(o[0].strip() for o in salidas) == ["DESCARTADA", "PROGRAMADA"], salidas
    assert contador.read_text().count("x") == 1
    assert [e["estado"] for e in leer_libro(tmp_path)] == ["creado"]
    filas = list(csv.DictReader(open(tmp_path / "marcador.csv", newline="", encoding="utf-8")))
    assert len(filas) == 1


# ---- formato anterior del libro ----

def test_entradas_del_formato_anterior_se_leen_como_creadas_y_registradas(tmp_path):
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    supa = SupabaseSimulado()
    supa.objetos["/storage/v1/object/public/clips/" + ruta] = b"x"
    alm = Almacen("https://simulado.invalid", "k", CONFIG_ALMACEN, supa)
    publicar._escribir_json(LIBRO(tmp_path), [{"post_id": "post-ejemplo-001", "plataforma": "shorts",
                                              "ruta": ruta, "canal": "canal-yt-001"}])
    pub = Publicador(cliente(tmp_path), alm, tmp_path, "vivo", lambda: AHORA, {"shorts": "canal-yt-001"})
    e = pub._libro()[0]
    assert e["estado"] == "creado" and e["registrado"] is True and e["id"]
    assert pub.reconciliar() == {}
    assert pub.limpiar_enviados() == [ruta]          # sin KeyError; su post está en sent


# ================= cierre: casos límite del modo vivo =================

@pytest.mark.parametrize("archivo", ["createpost_sin_discriminador_mensaje.json",
                                     "createpost_sin_discriminador_post.json"])
def test_sin_discriminador_es_incierto_y_la_segunda_llamada_no_envia(tmp_path, monkeypatch, archivo):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    sim = BufferSimulado(crear=archivo)
    pub, _ = vivo(tmp_path, sim)
    args = ("shorts", TOMA, "https://x.invalid/v.mp4", textos(TOMA, FICHA_LIMPIA, "shorts"), hueco_a_utc(HOY, "11:00"))
    with pytest.raises(publicar.ResultadoIncierto):
        pub.programar(*args)
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"
    with pytest.raises(Descartada, match="ya hay un envío"):   # incierto, o creado si el listado ya lo muestra
        pub.programar(*args)
    assert sum("createPost" in q for q in sim.consultas) == 1


def test_exito_con_id_vacio_o_tipo_distinto_es_incierto(tmp_path):
    pub, _ = vivo(tmp_path)
    for r in ({"__typename": "PostActionSuccess", "post": {"id": " "}},
              {"__typename": "OtroExito", "post": {"id": "1"}},
              {"__typename": "MutationError", "message": "  "},
              {"__typename": "MutationError", "message": 5}):
        pub.cliente.transporte = lambda *a, r=r, **k: F.resp(200, {"data": {"createPost": r}})
        with pytest.raises(publicar.ErrorIncierto):
            pub._crear({"text": "x"}, "canal-yt-001")


def libro_a_mano(tmp_path, *envios):
    base = {"plataforma": "shorts", "canal": "canal-yt-001", "ruta": None, "estado": "incierto",
            "post_id": None, "registrado": False, "intento": "i1"}
    libro = []
    for n, hora, texto in envios:
        due = f"2026-11-18T{hora}:00:00.000Z"
        libro.append({**base, "id": f"shorts|{n}|{due}", "toma": n, "dueAt": due, "texto": texto,
                      "datos": {"toma": {**TOMA, "numero": n}, "textos": {"texto": texto, "credito": "c"},
                                "ficha_id": "propio"}})
    publicar._escribir_json(LIBRO(tmp_path), libro)


class Paginas:
    def __init__(self, paginas):
        self.sim, self.paginas = BufferSimulado(), paginas

    def __call__(self, metodo, url, cab, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode())["query"]
        if "posts(" in q and "first:" in q:
            i = int(q.split('after: "p')[1].split('"')[0]) if "after:" in q else 0
            return F.resp(200, self.paginas[i])
        return self.sim(metodo, url, cab, cuerpo, tiempo)


def pagina(nodos, siguiente):
    return {"data": {"posts": {"edges": [{"node": n} for n in nodos], "pageInfo": {
        "hasNextPage": siguiente, "endCursor": "p1" if siguiente else None}}}}


def nodo_de(n, hora, texto):
    return {"id": f"post-{n}", "status": "scheduled", "dueAt": f"2026-11-18T{hora}:00:00.000Z", "text": texto}


def pub_con(tmp_path, transporte, ahora):
    c = ClienteBuffer(CLAVE, transporte, tmp_path, dormir=lambda s: None, ahora=lambda: ahora)
    return Publicador(c, None, tmp_path, "vivo", lambda: ahora, {"shorts": "canal-yt-001"},
                      orgs={"shorts": "org-ejemplo-001"})


TARDE = datetime(2026, 11, 25, tzinfo=timezone.utc)


def test_a_en_la_pagina_1_y_b_en_la_2_b_se_resuelve_sin_abandonarse(tmp_path, monkeypatch):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    libro_a_mano(tmp_path, (12, "17", "tA"), (13, "19", "tB"))
    t = Paginas([pagina([nodo_de(12, "17", "tA")], True), pagina([nodo_de(13, "19", "tB")], False)])
    r = pub_con(tmp_path, t, TARDE).reconciliar()
    assert sorted(r.values()) == ["creado", "creado"]


def test_encontrar_a_no_hace_concluyente_la_busqueda_de_b(tmp_path, monkeypatch):
    monkeypatch.delattr(registro, "anotar_clip", raising=False)
    (tmp_path / "marcador.csv").write_text(",".join(registro.CABECERA_MARCADOR) + "\n", encoding="utf-8")
    libro_a_mano(tmp_path, (12, "17", "tA"), (13, "19", "tB"))
    sin_info = {"data": {"posts": {"edges": []}}}          # la página 2 no trae pageInfo
    t = Paginas([pagina([nodo_de(12, "17", "tA")], True), sin_info])
    pub_con(tmp_path, t, TARDE).reconciliar()
    assert {e["toma"]: e["estado"] for e in leer_libro(tmp_path)} == {12: "creado", 13: "incierto"}


@pytest.mark.parametrize("respuesta", [{"data": {}}, {"data": {"posts": None}},
                                       {"data": {"posts": {"edges": []}}},
                                       {"data": {"posts": {"edges": [], "pageInfo": {"hasNextPage": None}}}},
                                       {"data": {"posts": {"edges": [], "pageInfo": {"hasNextPage": "false"}}}}])
def test_sin_posts_o_sin_pageinfo_valido_no_es_concluyente(tmp_path, respuesta):
    libro_a_mano(tmp_path, (12, "17", "tA"))
    pub_con(tmp_path, Paginas([respuesta]), TARDE).reconciliar()
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"


def test_con_pageinfo_valido_y_sin_mas_paginas_la_ausencia_si_es_concluyente(tmp_path):
    libro_a_mano(tmp_path, (12, "17", "tA"))
    pub_con(tmp_path, Paginas([pagina([], False)]), TARDE).reconciliar()
    assert leer_libro(tmp_path)[0]["estado"] == "abandonado"


def test_una_transicion_vieja_no_pisa_un_creado_ni_otro_intento(tmp_path):
    libro_a_mano(tmp_path, (12, "17", "tA"))
    pub = pub_con(tmp_path, Paginas([pagina([], False)]), TARDE)
    vieja = pub._libro()[0]
    libro = pub._libro()
    libro[0].update(estado="creado", post_id="post-12", registrado=True)
    publicar._escribir_json(LIBRO(tmp_path), libro)
    assert pub._transicionar(vieja, "abandonado") is False
    assert leer_libro(tmp_path)[0]["estado"] == "creado"
    libro[0].update(estado="incierto", intento="i2")                 # otro intento con el mismo id
    publicar._escribir_json(LIBRO(tmp_path), libro)
    assert pub._transicionar(vieja, "abandonado") is False
    assert leer_libro(tmp_path)[0]["estado"] == "incierto"


def test_reconciliar_no_abandona_si_el_envio_se_hizo_creado_mientras_leia(tmp_path):
    libro_a_mano(tmp_path, (12, "17", "tA"))

    class Lento(Paginas):
        def __call__(self, metodo, url, cab, cuerpo=None, tiempo=60):
            if "posts(" in json.loads(cuerpo.decode())["query"]:
                libro = json.loads(LIBRO(tmp_path).read_text(encoding="utf-8"))
                libro[0].update(estado="creado", post_id="post-12", registrado=True)   # otro proceso lo cerró
                LIBRO(tmp_path).write_text(json.dumps(libro), encoding="utf-8")
            return super().__call__(metodo, url, cab, cuerpo, tiempo)
    assert pub_con(tmp_path, Lento([pagina([], False)]), TARDE).reconciliar() == {}
    assert leer_libro(tmp_path)[0]["estado"] == "creado"


def test_limpiar_borra_bajo_el_candado_del_libro(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    alm, supa = almacen_con_video(tmp_path, ruta)
    visto = {}
    original = alm.borrar

    def borrar(r):
        with open(str(LIBRO(tmp_path)) + ".lock", "a+b") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                visto["libre"] = True
            except BlockingIOError:
                visto["libre"] = False
        original(r)
    alm.borrar = borrar
    pub = Publicador(cliente(tmp_path), alm, tmp_path, "vivo", lambda: AHORA, {})
    assert pub.limpiar_enviados() == [ruta]
    assert visto == {"libre": False}


def test_limpiar_no_borra_si_alguien_reservo_la_ruta_durante_la_comprobacion(tmp_path):
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    alm, supa = almacen_con_video(tmp_path, ruta)
    pub = Publicador(cliente(tmp_path), alm, tmp_path, "vivo", lambda: AHORA, {})
    original = Publicador.estado

    def estado_y_reserva(self, post_id, org_id=None):
        libro = json.loads(LIBRO(tmp_path).read_text(encoding="utf-8"))
        libro.append({**libro[0], "id": "otro", "estado": "enviando", "toma": 99, "post_id": None})
        LIBRO(tmp_path).write_text(json.dumps(libro), encoding="utf-8")     # simula otra reserva
        return original(self, post_id, org_id)
    pub.estado = estado_y_reserva.__get__(pub)
    assert pub.limpiar_enviados() == [] and len(supa.objetos) == 1



@pytest.mark.parametrize("tipo,es_rechazo", [("InvalidInputError", True), ("LimitReachedError", True),
                                             ("UnauthorizedError", False), ("NotFoundError", False),
                                             ("UnexpectedError", False), ("RestProxyError", False),
                                             ("OtroTipoNuevo", False)])
def test_los_tipos_concretos_de_mutationerror_se_clasifican_y_dicen_su_tipo(tmp_path, tipo, es_rechazo):
    pub, _ = vivo(tmp_path)
    r = {"__typename": tipo, "message": "video demasiado corto"}
    pub.cliente.transporte = lambda *a, **k: F.resp(200, {"data": {"createPost": r}})
    error = Descartada if es_rechazo else publicar.ErrorIncierto
    with pytest.raises(error, match=f"{tipo}.*video demasiado corto"):
        pub._crear({"text": "x"}, "canal-yt-001")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_borrador_con_supabase_usa_un_video_real_y_lo_borra_al_final(repo):
    sin_pausa(repo)
    sim, supa, sal = BufferSimulado(), SupabaseSimulado(), []
    cod = publicar.ejecutar(repo, con_borrador=True, env=entorno_real(), salida=sal.append,
                            transporte_buffer=sim, transporte_almacen=supa, ahora=lambda: AHORA)
    assert cod == 0, sal
    urls = {json.dumps(v["input"]) for q, v in zip(sim.consultas, sim.variables) if "createPost" in q}
    assert len(urls) == 3 and all("/storage/v1/object/public/clips/tomas/" in u for u in urls)
    assert "ejemplo.invalid" not in "".join(urls)
    assert supa.objetos == {} and "borrado del almacén" in sal[-1]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_borrador_que_falla_en_buffer_igual_borra_el_video_de_prueba(repo):
    sin_pausa(repo)
    supa, sal = SupabaseSimulado(), []
    cod = publicar.ejecutar(repo, con_borrador=True, env=entorno_real(), salida=sal.append,
                            transporte_buffer=BufferSimulado(crear="createpost_error.json"),
                            transporte_almacen=supa, ahora=lambda: AHORA)
    assert cod == 1 and supa.objetos == {}
    assert any(s.startswith("ERROR:") for s in sal)



def test_borrador_sin_claves_de_supabase_no_llama_a_buffer(repo):
    sin_pausa(repo)
    sim, sal = BufferSimulado(), []
    cod = publicar.ejecutar(repo, con_borrador=True, env={"BUFFER_API_KEY": CLAVE}, salida=sal.append,
                            transporte_buffer=sim, ahora=lambda: AHORA)
    assert cod == 1 and sim.consultas == [] and "SUPABASE_URL" in sal[-1]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_borrador_con_subida_guardada_pero_respuesta_perdida_igual_borra(repo):
    sin_pausa(repo)
    supa, sal = SupabaseSimulado(), []

    def transporte(metodo, url, cab, cuerpo=None, tiempo=60):
        r = supa(metodo, url, cab, cuerpo, tiempo)
        if metodo == "POST" and "/storage/v1/object/clips/" in url:
            raise red.ErrorRed("POST: respuesta perdida")   # Supabase lo guardó, la respuesta no llegó
        return r
    cod = publicar.ejecutar(repo, con_borrador=True, env=entorno_real(), salida=sal.append,
                            transporte_buffer=BufferSimulado(), transporte_almacen=transporte,
                            ahora=lambda: AHORA)
    assert cod == 1 and supa.objetos == {}, sal

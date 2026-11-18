"""N5 secciones 2 y 3: publicador de Buffer, textos, cuota, canales y modos. Sin red ni claves reales."""
import csv
import json
import shutil
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

import falsos_n5 as F
from fabrica import publicar, registro, validar
from fabrica.almacen import Almacen
from fabrica.publicar import (AlertaRoja, ClienteBuffer, Cuota, Descartada, ErrorBuffer, ErrorCuota,
                              Publicador, armar_carga, confirmar_canales, hueco_a_utc, textos)
from fabrica.simulacion import BufferSimulado, SupabaseSimulado

CLAVE = "bfr_clave-inventada-n5"       # EJEMPLO-FALSO
SUPA_URL = "https://proyecto-falso.supabase.invalid"
SUPA_CLAVE = "sb_secret_inventada-n5"  # EJEMPLO-FALSO
AHORA = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)     # 06:00 en Guatemala
HOY = date(2026, 11, 18)
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


def test_confirmar_acepta_arroba_y_mayusculas_y_displayname():
    api = [{"id": "x1", "name": "otro", "displayName": "@GuionVivo", "service": "youtube", "isQueuePaused": False}]
    cfg = {"shorts": CANALES_CFG["shorts"]}
    assert confirmar_canales(api, {"shorts": {**cfg["shorts"], "nombre_esperado": "@guionVIVO"}},
                             None, ["shorts"], escribir=False) == {"shorts": "x1"}


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
    publicar.guardar_ids_de_canales(repo, {"shorts": "canal-yt-001", "reels": "canal-ig-001"})
    datos = yaml.safe_load((repo / "config" / "canales.yml").read_text(encoding="utf-8"))
    assert datos["shorts"]["buffer_channel_id"] == "canal-yt-001"
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
    # el día ya va por 480 (80 % de 600): se detiene por el día antes que por el canal
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


def test_borrador_hace_createpost_con_savetodraft_y_deletepost(repo):
    sin_pausa(repo)
    sim, sal = BufferSimulado(), []
    cod = publicar.ejecutar(repo, con_borrador=True, env={"BUFFER_API_KEY": CLAVE}, salida=sal.append,
                            transporte_buffer=sim, ahora=lambda: AHORA)
    assert cod == 0
    creaciones = [v["input"] for q, v in zip(sim.consultas, sim.variables) if "createPost" in q]
    assert len(creaciones) == 3 and all(c["saveToDraft"] is True for c in creaciones)
    borrados = [v["input"] for q, v in zip(sim.consultas, sim.variables) if "deletePost" in q]
    assert borrados == [{"id": "post-ejemplo-001"}] * 3
    assert sal[-1] == "Borrador reels: creado y borrado (post-ejemplo-001)"


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
        {"post_id": "post-ejemplo-001", "plataforma": "shorts", "ruta": ruta, "canal": "canal-yt-001"}])
    return alm, supa


def test_limpiar_borra_del_almacen_lo_que_ya_esta_sent(tmp_path):
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    alm, supa = almacen_con_video(tmp_path, ruta)
    pub = Publicador(cliente(tmp_path), alm, tmp_path, "vivo", lambda: AHORA, {})
    assert pub.limpiar_enviados() == [ruta] and supa.objetos == {}
    assert json.loads((tmp_path / "estado" / "buffer-envios.json").read_text(encoding="utf-8")) == []


def test_limpiar_conserva_el_video_si_el_post_aun_no_esta_sent(tmp_path):
    ruta = "tomas/2026-11-18/toma-012-ab12cd34.mp4"
    alm, supa = almacen_con_video(tmp_path, ruta)
    pub = Publicador(cliente(tmp_path, estado_post="post_programado.json"), alm, tmp_path, "vivo",
                     lambda: AHORA, {})
    assert pub.limpiar_enviados() == [] and len(supa.objetos) == 1


# ---------------- vivo de punta a punta (simulado) ----------------

def test_vivo_solo_en_plataformas_activas_y_registra(repo, editar_yaml):
    sin_pausa(repo)
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

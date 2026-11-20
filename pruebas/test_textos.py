"""Tarea #8: textos por red (playbook, sección 10). Sin red ni claves."""
from __future__ import annotations

import copy

import pytest

from fabrica import publicar, textos as mod
from fabrica.fuentes import cargar_fuentes
from fabrica.publicar import Descartada, armar_carga, textos

REDES = ("shorts", "tiktok", "reels")


def ficha_fm27() -> dict:
    return copy.deepcopy(next(f for f in cargar_fuentes() if f["id"] == "sega-fm27"))


def ficha_t(login="ibai", etiquetas=("#GTA6", "#Gaming", "#Clips")) -> dict:
    return {"id": f"t-{login}", "tipo": "T", "creador": f"twitch.tv/{login}", "credito": f"twitch.tv/{login}",
            "twitch_login": login, "origen": "twitch",
            "reglas": {"etiquetas_obligatorias": list(etiquetas), "divulgacion": "ninguna"},
            "campana": None, "estado": "vigente", "uso": "produccion"}


FICHA_PROPIA = {"id": "propio", "creador": "@guionvivo",
                "reglas": {"etiquetas_obligatorias": [], "divulgacion": "ninguna"}}
TOMA_T = {"numero": 5, "gancho": "EL NPC LE CONTESTA AL STREAMER", "pilar": "momentos",
          "creador": "twitch.tv/ibai", "valor": ["Nadie esperaba esa respuesta.", "Pasó en directo.", "Tercera."],
          "etiquetas": ["#GTA6", "#Gaming", "#Clips", "#Extra"]}
TOMA_A = {"numero": 7, "gancho": "Sebas ficha al delantero", "pilar": "momentos", "creador": "SEBASBERON",
          "campana": "SEGA FM27 (Clipping Culture)", "valor": ["Un fichaje de última hora."]}
TOMA_I = {"numero": 3, "gancho": "Llega el 19 de noviembre", "titular": "Llega el 19 de noviembre",
          "pilar": "informativa", "datos": ["Sale en PS5", "Sale en Xbox Series X|S"],
          "etiquetas": ["#GTA6", "#Gaming", "#Clips"]}
TOMA_P = {"numero": 12, "gancho": "Primera vez en el mapa", "pilar": "momentos",
          "etiquetas": ["#GTA6", "#Gaming", "#Clips", "#Mas", "#Otra"]}


# ---------------------------------------------------------------- 9 casos positivos, texto exacto

def test_t_shorts():
    r = textos(TOMA_T, ficha_t(), "shorts")
    assert r["texto"] == "Toma 005. El NPC le contesta al streamer\n\n🎥 twitch.tv/ibai\n#GTA6 #Gaming #Clips"
    assert r["titulo"] == "Toma 005. El NPC le contesta al streamer"
    assert r["credito"] == "🎥 twitch.tv/ibai" and r["descripcion"] == r["texto"]
    assert r["hashtags"] == ["#GTA6", "#Gaming", "#Clips"]
    assert r["largo"] == mod.utf16(r["texto"])


def test_t_tiktok():
    r = textos(TOMA_T, ficha_t(), "tiktok")
    assert r["texto"] == "Toma 005. El NPC le contesta al streamer\n\n🎥 twitch.tv/ibai\n#GTA6 #Gaming #Clips"
    assert r["titulo"] is None


def test_t_reels():
    r = textos(TOMA_T, ficha_t(), "reels")
    assert r["texto"] == ("Toma 005. El NPC le contesta al streamer\n\n"
                          "Nadie esperaba esa respuesta. Pasó en directo.\n\n"
                          "🎥 twitch.tv/ibai\n#GTA6 #Gaming #Clips #Extra")
    assert r["titulo"] is None and len(r["hashtags"]) == 4
    assert r["largo"] == mod.utf16(r["texto"]) + r["texto"].count("\n")


@pytest.mark.parametrize("red,tag", [("shorts", "@sebasberon"), ("tiktok", "@sebasclips"), ("reels", "@sebasberon")])
def test_a_con_la_ficha_real_de_fm27(red, tag):
    r = textos(TOMA_A, ficha_fm27(), red)
    assert r["texto"] == ("Toma 007. Sebas ficha al delantero\n\n@footballmanager\n" + tag +
                          "\n#Publicidad\n#FootballManager #FM27 #FootballManager27")
    assert r["credito"] == tag and (r["titulo"] is not None) == (red == "shorts")
    assert r["hashtags"][0] == "#Publicidad"


@pytest.mark.parametrize("red", REDES)
def test_informativa(red):
    r = textos(TOMA_I, FICHA_PROPIA, red)
    assert r["texto"] == ("GTA 6: Llega el 19 de noviembre\n\n- Sale en PS5\n- Sale en Xbox Series X|S\n\n"
                          "🎥 @RockstarGames\n#GTA6 #Gaming #Clips")
    assert r["credito"] == "🎥 @RockstarGames"
    assert (r["titulo"] == "GTA 6: Llega el 19 de noviembre") == (red == "shorts")


# ---------------------------------------------------------------- crédito

def test_ficha_t_sin_login_se_descarta():
    f = {**ficha_t(), "creador": "", "credito": "", "twitch_login": ""}
    with pytest.raises(Descartada, match="sin crédito"):
        textos({**TOMA_T, "creador": ""}, f, "shorts")


def test_el_login_sale_de_credito_creador_o_twitch_login():
    f = {**ficha_t("ibai"), "credito": "", "creador": ""}      # sólo twitch_login
    assert "🎥 twitch.tv/ibai\n" in textos(TOMA_T, f, "shorts")["texto"]
    f = {**ficha_t("ibai"), "credito": "", "twitch_login": ""}  # sólo creador
    assert "🎥 twitch.tv/ibai\n" in textos(TOMA_T, f, "shorts")["texto"]


def test_varios_creadores_un_solo_emoji():
    toma = {**TOMA_T, "creadores": ["twitch.tv/ibai", "twitch.tv/auronplay"]}
    t = textos(toma, ficha_t(), "tiktok")["texto"]
    assert "🎥 twitch.tv/ibai · twitch.tv/auronplay\n" in t and publicar.contar_emojis(t) == 1


def test_varios_creadores_en_campana_etiqueta_de_cada_uno_en_orden():
    toma = {**TOMA_A, "creadores": ["DARO", "SEBASBERON"]}
    assert textos(toma, ficha_fm27(), "tiktok")["texto"].split("\n")[2:6] == [
        "@footballmanager", "Credit to Dario Zenz", "@sebasclips", "#Publicidad"]
    with pytest.raises(Descartada, match="sin etiqueta del creador NADIE"):
        textos({**TOMA_A, "creadores": ["DARO", "NADIE"]}, ficha_fm27(), "tiktok")


def test_el_credito_y_publicidad_no_se_recortan_para_caber():
    toma = {**TOMA_A, "gancho": "tiroteo " * 280}          # 2240 caracteres: no cabe en TikTok
    with pytest.raises(Descartada, match="2200"):
        textos(toma, ficha_fm27(), "tiktok")


# ---------------------------------------------------------------- gancho concreto

@pytest.mark.parametrize("gancho", ["NO VAS A CREER lo que pasa aquí", "Esto cambia todo para el mapa",
                                    "Mira esto: un auto volando", "Un momento ÉPICO... no, momento épico total",
                                    "Una locura total en la playa", "Es increíble el tiroteo", "El final te sorprenderá"])
def test_gancho_generico_se_descarta(gancho):
    with pytest.raises(Descartada, match="genérico"):
        textos({**TOMA_P, "gancho": gancho}, FICHA_PROPIA, "shorts")


def test_genericos_se_comparan_sin_acentos_ni_mayusculas():
    with pytest.raises(Descartada, match="genérico"):
        textos({**TOMA_P, "gancho": "INCREIBLE tiroteo en la playa"}, FICHA_PROPIA, "shorts")


@pytest.mark.parametrize("gancho", ["gta6", "GTA 6 clip", "ibai", "#GTA6 ibai", "El stream", "Clip de GTA 6"])
def test_gancho_sin_contenido_se_descarta(gancho):
    with pytest.raises(Descartada, match="sin contenido"):
        textos({**TOMA_P, "gancho": gancho, "creador": "twitch.tv/ibai"}, ficha_t(), "shorts")


def test_gancho_en_ingles_se_descarta():
    with pytest.raises(Descartada, match="inglés"):
        textos({**TOMA_P, "gancho": "When the NPC runs"}, FICHA_PROPIA, "shorts")
    textos({**TOMA_P, "gancho": "El NPC runs fast"}, FICHA_PROPIA, "shorts")     # una sola palabra: pasa


def test_palabra_evitada_de_la_ficha_se_descarta():
    with pytest.raises(Descartada, match="gratis"):
        textos({**TOMA_A, "gancho": "Sebas fichó gratis al delantero"}, ficha_fm27(), "shorts")
    with pytest.raises(Descartada, match="leak"):
        textos({**TOMA_A, "gancho": "Un LEAK del delantero nuevo"}, ficha_fm27(), "reels")
    with pytest.raises(Descartada, match="filtración"):          # la palabra evitada también cuenta en el valor
        textos({**TOMA_T, "valor": ["Una filtración."]}, {**ficha_t(), "reglas": {
            **ficha_t()["reglas"], "palabras_evitar": ["filtración"]}}, "reels")


def test_mayusculas_pasan_a_oracion_conservando_siglas():
    t = textos({**TOMA_P, "gancho": "EL NPC LE CONTESTA"}, FICHA_PROPIA, "tiktok")["texto"]
    assert t.startswith("Toma 012. El NPC le contesta\n")
    t = textos({**TOMA_P, "gancho": "SALE EN PS5 CON DLC Y FIFA"}, FICHA_PROPIA, "tiktok")["texto"]
    assert t.startswith("Toma 012. Sale en PS5 con DLC y FIFA\n")


def test_el_juego_es_gta_6_pero_el_hashtag_no_cambia():
    r = textos({**TOMA_P, "gancho": "El mejor tiroteo de GTA VI y gta6 #GTA6"}, FICHA_PROPIA, "shorts")
    assert r["texto"].startswith("Toma 012. El mejor tiroteo de GTA 6 y GTA 6\n")
    assert "#GTA6" in r["texto"] and "#GTA 6" not in r["texto"]


def test_hashtags_incrustados_en_el_gancho_se_quitan():
    r = textos({**TOMA_P, "gancho": "Primera vez #Gaming en el mapa #Otro"}, FICHA_PROPIA, "shorts")
    assert r["texto"].startswith("Toma 012. Primera vez en el mapa\n")
    assert r["titulo"] == "Toma 012. Primera vez en el mapa"


# ---------------------------------------------------------------- emojis, < >, título

def test_emoji_del_gancho_se_quita_y_queda_uno_solo():
    toma = {**TOMA_P, "gancho": "Primera 🔥 vez en el mapa ❤️", "valor": ["Frase 😂 uno.", "Frase dos."]}
    for red in REDES:
        t = textos(toma, FICHA_PROPIA, red)["texto"]
        assert publicar.contar_emojis(t) == 1 and "🎥 @guionvivo" in t
    inf = {**TOMA_I, "titular": "Llega 🚀 el 19 de noviembre", "datos": ["Sale 🎮 en PS5"]}
    t = textos(inf, FICHA_PROPIA, "reels")["texto"]
    assert publicar.contar_emojis(t) == 1 and "- Sale en PS5" in t
    assert publicar.contar_emojis(textos(TOMA_A, ficha_fm27(), "reels")["texto"]) == 0


def test_shorts_titulo_sin_emoji_ni_hashtags_ni_menor_mayor():
    toma = {**TOMA_P, "gancho": "Primera <vez> 🔥 en el mapa #GTA6", "valor": ["<b>hola</b>"]}
    r = textos(toma, FICHA_PROPIA, "shorts")
    assert r["titulo"] == "Toma 012. Primera vez en el mapa"
    assert "<" not in r["texto"] and ">" not in r["texto"]
    r = textos(toma, FICHA_PROPIA, "reels")
    assert "<" not in r["texto"] and "bhola/b" in r["texto"]


def test_titulo_de_100_o_menos_en_utf16():
    r = textos({**TOMA_P, "gancho": "tiroteo " * 40}, FICHA_PROPIA, "shorts")
    assert mod.utf16(r["titulo"]) <= 100 and r["titulo"].startswith("Toma 012.") and r["titulo"].endswith("…")


def test_armar_carga_de_shorts_manda_categoria_gaming(tmp_path):
    from datetime import datetime, timezone
    from pruebas.test_n5_publicar import esquema
    r = textos(TOMA_P, FICHA_PROPIA, "shorts")
    carga, _ = armar_carga("shorts", "c1", r["texto"], r["titulo"], "https://x.invalid/v.mp4",
                           datetime(2030, 1, 1, tzinfo=timezone.utc), esquema(tmp_path))
    assert carga["metadata"]["youtube"]["categoryId"] == "20"


# ---------------------------------------------------------------- hashtags

def test_reels_tope_de_5_y_minimo_de_3():
    toma = {**TOMA_P, "etiquetas": ["#a", "#b", "#c", "#d", "#e", "#f", "#g"]}
    r = textos(toma, FICHA_PROPIA, "reels")
    assert r["hashtags"] == ["#a", "#b", "#c", "#d", "#e"]
    assert textos(toma, FICHA_PROPIA, "shorts")["hashtags"] == ["#a", "#b", "#c"]
    with pytest.raises(Descartada, match="3 a 5"):
        textos({**TOMA_P, "etiquetas": ["#a", "#b"]}, FICHA_PROPIA, "reels")


def test_hashtags_en_campana_reels_cuenta_publicidad_dentro_del_tope():
    f = ficha_fm27()
    f["reglas"]["hashtags"] = ["#a", "#b", "#c", "#d", "#e"]
    r = textos(TOMA_A, f, "reels")
    assert r["hashtags"] == ["#Publicidad", "#a", "#b", "#c"] and len(r["hashtags"]) <= 5
    f["reglas"]["hashtags"] = ["#a"]                          # #Publicidad + 1 = 2: Reels pide 3
    with pytest.raises(Descartada, match="3 a 5"):
        textos(TOMA_A, f, "reels")
    assert textos(TOMA_A, f, "tiktok")["hashtags"] == ["#Publicidad", "#a"]


def test_hashtags_formato_valido_y_sin_duplicados():
    toma = {**TOMA_P, "etiquetas": ["#GTA6", "#gta6", "#con espacio", "#sig!no", "#Barça", "#ñandú_1", "#Liga", "#x"]}
    r = textos(toma, FICHA_PROPIA, "reels")
    assert r["hashtags"] == ["#GTA6", "#Barça", "#ñandú_1", "#Liga", "#x"]
    assert mod.es_hashtag("#Barça") and not mod.es_hashtag("#con espacio") and not mod.es_hashtag("#a-b")


# ---------------------------------------------------------------- conteo y largo

def test_conteo_utf16_con_emoji_y_saltos_de_linea_en_reels():
    assert mod.utf16("🎥") == 2 and mod.utf16("á") == 1
    assert mod.largo("a\n🎥\nb", "tiktok") == 1 + 1 + 2 + 1 + 1
    assert mod.largo("a\n🎥\nb", "reels") == 1 + 2 + 2 + 2 + 1
    r = textos(TOMA_T, ficha_t(), "reels")
    assert r["largo"] == mod.utf16(r["texto"]) + r["texto"].count("\n")


def test_largo_de_reels_que_pasa_con_caracteres_pero_no_con_saltos():
    # Cada salto cuenta 2: 2196 de límite. Con valor cortado a una frase cabe; con las dos no.
    toma = {**TOMA_T, "valor": ["a" * 1100 + ".", "b" * 1100 + "."]}
    r = textos(toma, ficha_t(), "reels")
    assert "b" * 1100 not in r["texto"] and "a" * 1100 in r["texto"] and r["largo"] <= 2196


def test_si_aun_sin_valor_no_cabe_se_descarta_con_largo_y_limite():
    with pytest.raises(Descartada, match=r"mide \d+ y reels permite 2196"):
        textos({**TOMA_T, "gancho": "tiroteo " * 300}, ficha_t(), "reels")


def test_informativa_recorta_datos_por_frases_enteras():
    toma = {**TOMA_I, "datos": ["a" * 1000, "b" * 1000, "c" * 1000]}
    r = textos(toma, FICHA_PROPIA, "tiktok")
    assert "a" * 1000 in r["texto"] and "b" * 1000 in r["texto"] and "c" * 1000 not in r["texto"]
    assert r["largo"] <= 2200 and r["texto"].endswith("🎥 @RockstarGames\n#GTA6 #Gaming #Clips")
    with pytest.raises(Descartada, match="2200"):
        textos({**toma, "titular": "tiroteo " * 280}, FICHA_PROPIA, "tiktok")


# ---------------------------------------------------------------- repetición (sigue vigente)

def test_no_se_repite_el_mismo_texto_en_7_dias(tmp_path):
    from datetime import date, timedelta
    hoy = date(2026, 11, 20)
    tx = textos(TOMA_P, FICHA_PROPIA, "shorts", tmp_path, hoy)
    publicar._escribir_json(tmp_path / "estado" / "buffer-textos.json", [{
        "fecha": "2026-11-18", "plataforma": "shorts",
        "huella": publicar.hashlib.sha256(publicar._cuerpo_sin_toma(tx["texto"]).encode()).hexdigest()}])
    with pytest.raises(Descartada, match="repetido"):
        textos({**TOMA_P, "numero": 13}, FICHA_PROPIA, "shorts", tmp_path, hoy)
    textos({**TOMA_P, "numero": 13}, FICHA_PROPIA, "shorts", tmp_path, hoy + timedelta(days=8))


# ---------------------------------------------------------------- ronda 2

# 1. Frases genéricas y palabras evitadas: por palabras completas.
@pytest.mark.parametrize("gancho", ["Increíblemente rápido el auto", "La parte 23 del atraco", "Admira esto de cerca"])
def test_r2_generico_por_palabra_completa_no_descarta_subcadenas(gancho):
    textos({**TOMA_P, "gancho": gancho}, FICHA_PROPIA, "shorts")


@pytest.mark.parametrize("gancho", ["Mira esto", "PARTE 2 del robo", "Es increíble el auto"])
def test_r2_generico_sigue_descartando_la_frase_completa(gancho):
    with pytest.raises(Descartada, match="genérico"):
        textos({**TOMA_P, "gancho": gancho}, FICHA_PROPIA, "shorts")


def test_r2_palabras_evitar_por_palabra_completa_y_sin_acentos():
    f = ficha_fm27()
    with pytest.raises(Descartada, match="filtración"):
        textos({**TOMA_A, "gancho": "Sebas confirma una FILTRACION nueva"}, f, "shorts")
    textos({**TOMA_A, "gancho": "Sebas habla de filtraciones nuevas"}, f, "shorts")     # otra palabra: pasa
    textos({**TOMA_A, "gancho": "Sebas lo ve gratuito en casa"}, f, "shorts")


# 2. Gancho concreto: 0 palabras con contenido, o 1 sola con menos de 3 palabras en total.
@pytest.mark.parametrize("gancho", ["Ibai se asusta", "Sale GTA 6", "GTA 6 ya está a la venta"])
def test_r2_ganchos_cortos_pero_concretos_pasan(gancho):
    textos({**TOMA_P, "gancho": gancho, "creador": "twitch.tv/ibai"}, ficha_t(), "shorts")


@pytest.mark.parametrize("gancho", ["gta6", "ibai", "GTA 6 clip", "El clip"])
def test_r2_ganchos_vacios_siguen_fuera(gancho):
    with pytest.raises(Descartada, match="sin contenido"):
        textos({**TOMA_P, "gancho": gancho, "creador": "twitch.tv/ibai"}, ficha_t(), "shorts")


# 3. Sólo se ignora el creador de la toma, su login o etiqueta, el credito de T y el juego.
def test_r2_el_texto_libre_de_ficha_creador_de_campana_no_se_ignora():
    r = textos({**TOMA_A, "gancho": "Fichaje oficial"}, ficha_fm27(), "tiktok")    # «oficial» está en ficha.creador
    assert r["texto"].startswith("Toma 007. Fichaje oficial\n")
    with pytest.raises(Descartada, match="sin contenido"):
        textos({**TOMA_A, "gancho": "Sebasberon Sebasclips"}, ficha_fm27(), "tiktok")     # creador y etiqueta


# 4. Login de Twitch.
def test_r2_login_de_twitch_se_normaliza_y_se_valida():
    toma = {**TOMA_T, "creador": "https://www.twitch.tv/Ibai/clip/AbC-x?a=1"}
    f = {**ficha_t(), "credito": "https://www.twitch.tv/Ibai/clip/AbC-x?a=1"}
    assert "🎥 twitch.tv/Ibai\n" in textos(toma, f, "tiktok")["texto"]
    for malo in ("twitch.tv/ib ai!", "twitch.tv/" + "a" * 26, "twitch.tv/ibai-x"):
        with pytest.raises(Descartada, match="sin crédito: login de Twitch inválido"):
            textos(TOMA_T, {**ficha_t(), "credito": malo}, "tiktok")


# 5. NFC.
def test_r2_hashtag_nfd_queda_valido():
    nfd = "#Cancún"
    r = textos({**TOMA_P, "etiquetas": [nfd, "#a", "#b"]}, FICHA_PROPIA, "reels")
    assert "#Cancún" in r["hashtags"] and "́" not in r["texto"]
    r = textos({**TOMA_P, "gancho": "Primera vez en Cancún"}, FICHA_PROPIA, "tiktok")
    assert "Cancún" in r["texto"]


# 6. Título por UTF-16.
def test_r2_titulo_se_recorta_por_utf16():
    r = textos({**TOMA_P, "gancho": "𝕏𝕏𝕏𝕏 " * 30}, FICHA_PROPIA, "shorts")
    assert mod.utf16(r["titulo"]) <= 100 and r["titulo"].endswith("…")
    assert len(mod._titulo_youtube("𝕏" * 80)) < 80 and mod.utf16(mod._titulo_youtube("𝕏" * 80)) <= 100


# 7. Tipo oración con puntuación y nombre del creador.
def test_r2_oracion_tras_signos_y_con_nombre_del_creador():
    t = textos({**TOMA_P, "gancho": "¡NO PUEDE SER! EL NPC TROLEA. ¿QUÉ PASA?"}, FICHA_PROPIA, "tiktok")["texto"]
    assert t.startswith("Toma 012. ¡No puede ser! El NPC trolea. ¿Qué pasa?\n")
    t = textos({**TOMA_P, "gancho": "SE ASUSTA IBAILLANOS CON EL NPC", "creador": "twitch.tv/IbaiLlanos"},
               ficha_t("IbaiLlanos"), "tiktok")["texto"]
    assert t.startswith("Toma 012. Se asusta IbaiLlanos con el NPC\n")


# 8. Hashtags incrustados: sólo los que empiezan con letra.
def test_r2_hashtag_numerico_en_el_gancho_se_queda():
    r = textos({**TOMA_P, "gancho": "El top #1 de Latam"}, FICHA_PROPIA, "tiktok")
    assert r["texto"].startswith("Toma 012. El top #1 de Latam\n")
    r = textos({**TOMA_A, "gancho": "El top #1 de Latam"}, ficha_fm27(), "tiktok")      # no es el # de la divulgación
    assert r["hashtags"][0] == "#Publicidad"


# 9. Emojis extra.
def test_r2_emojis_cuadrados_y_banderas_de_etiqueta():
    escocia = "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
    r = textos({**TOMA_P, "gancho": f"Primera ⬛ vez ⬜ en {escocia} el mapa ️‍"}, FICHA_PROPIA, "tiktok")
    assert r["texto"].startswith("Toma 012. Primera vez en el mapa\n")
    assert not any(0xE0020 <= ord(c) <= 0xE007F or c in "⬛⬜️‍" for c in r["texto"])
    assert publicar.contar_emojis(r["texto"]) == 1 and publicar.contar_emojis(escocia) == 1


# 10. En campaña, primero los hashtags del brief.
def test_r2_campana_hashtags_del_brief_antes_que_los_de_la_toma():
    r = textos({**TOMA_A, "etiquetas": ["#Zeta", "#Eta"]}, ficha_fm27(), "tiktok")
    assert r["texto"].endswith("#Publicidad\n#FootballManager #FM27 #FootballManager27")


# 11. Ensayo con PAUSA.
def test_r2_ensayo_con_pausa_imprime_los_9_textos_y_no_llama_a_buffer(repo, monkeypatch):
    assert (repo / "PAUSA").exists()
    monkeypatch.setattr(publicar, "ClienteBuffer", lambda *a, **k: pytest.fail("no debe llamar a Buffer"))
    sal: list[str] = []
    cod = publicar.ejecutar(repo, ensayo_forzado=True, env={}, salida=sal.append)
    assert cod == 0
    assert len([x for x in sal if x.startswith("Texto ")]) == 9 and all("... ok" in x for x in sal if x.startswith("Texto "))
    assert sal[-1].startswith("PAUSA:")
    assert not (repo / "estado" / "buffer-textos.json").exists()


def test_r2_ensayo_sale_con_error_si_un_texto_no_cumple(repo, monkeypatch):
    from fabrica import textos as t
    monkeypatch.setattr(t, "cargar_config", lambda c=t.cargar_config(): {
        **c, "limites": {**c["limites"], "tiktok": {**c["limites"]["tiktok"], "max_texto": 10}}})
    sal: list[str] = []
    assert publicar.ejecutar(repo, ensayo_forzado=True, env={}, salida=sal.append) == publicar.COD_ERROR
    assert any("FALLA" in x or "DESCARTADO" in x for x in sal)


def test_r2_ensayo_sin_la_ficha_de_fm27_informa_y_sigue(repo, monkeypatch):
    from fabrica import fuentes
    real = fuentes.cargar_fuentes
    monkeypatch.setattr(fuentes, "cargar_fuentes", lambda raiz=None: [f for f in real(raiz) if f["id"] != "sega-fm27"])
    sal: list[str] = []
    cod = publicar.ejecutar(repo, ensayo_forzado=True, env={}, salida=sal.append)
    assert cod == publicar.COD_ERROR and any("sega-fm27" in x for x in sal)
    assert len([x for x in sal if x.startswith("Texto ")]) == 6


# 13. El gancho del marcador pasa por la misma limpieza.
def test_r2_repetido_contra_el_marcador_ignora_emojis_y_hashtags(tmp_path):
    import csv
    from datetime import date
    from fabrica import registro
    with open(tmp_path / "marcador.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(registro.CABECERA_MARCADOR)
        fila = dict.fromkeys(registro.CABECERA_MARCADOR, "")
        fila.update(toma="5", fecha="2026-11-18", plataforma="shorts", gancho="PRIMERA VEZ EN EL MAPA 🔥 #Gaming")
        w.writerow([fila[c] for c in registro.CABECERA_MARCADOR])
    with pytest.raises(Descartada, match="gancho repetido"):
        textos(TOMA_P, FICHA_PROPIA, "shorts", tmp_path, date(2026, 11, 20))

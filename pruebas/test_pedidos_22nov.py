"""Pedidos de Kevin del 22 nov 2026: contexto del estreno para Gemini, trucos permitidos, subtítulos ya quemados,
caption distinto del gancho y nombre real del streamer. Sin red."""
from __future__ import annotations

import copy
import json
from datetime import date

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import detectar, ojos, textos as mod_textos, validar
from fabrica.editar import componer as mod_componer
from fabrica.editar import estilo, nombres, placas
from fabrica.editar.lienzo import Toma
from fabrica.editar.marca import Marca
from fabrica.publicar import textos as armar_textos
from test_n4 import (carpetas, cargar_dato, correr, marca, temporada, video20, ficha_propia, candidato,  # noqa: F401
                     abrir_con, transporte_con, AHORA)
from fabrica.editar import toma as mod_toma


# ---------------------------------------------------------------- 1 y 2. contexto del estreno y trucos

def test_el_prompt_lleva_la_fecha_de_estreno_las_plataformas_y_la_fecha_de_hoy(marca, temporada):
    p = ojos.instruccion(marca, temporada, 12.0, hoy=date(2026, 11, 22))
    assert "2026-11-19" in p and "19 de noviembre de 2026" in p
    assert "PS5" in p and "Xbox Series X|S" in p
    assert "22 de noviembre de 2026" in p and "2026-11-22" in p
    assert "GTA 6 salió el" in p and "categoría oficial de Twitch" in p
    assert "no es una filtración ni un juego sin lanzar" in p
    assert "Sigue siendo `apto: false` el material previo al estreno o extraído del juego (filtración)" in p
    assert "una copia pirata" in p and "programas de trampas externos" in p
    assert "no es una filtración, un tráiler" not in p and "No lo descartes por eso" not in p
    assert "{{" not in p


def test_el_prompt_pide_juego_incorrecto_solo_si_es_claramente_otro_y_mods_si_parece_gta(marca, temporada):
    p = ojos.instruccion(marca, temporada, 12.0, hoy=date(2026, 11, 22))
    assert "CLARAMENTE" in p and "Minecraft" in p
    assert "es `mods: true`, no otro juego" in p


def test_con_otro_juego_en_la_ficha_no_se_afirma_el_estreno_de_gta(marca, temporada):
    p = ojos.instruccion(marca, temporada, 12.0, juego="Football Manager 27", hoy=date(2026, 11, 22))
    assert "2026-11-22" in p and "salió el" not in p and "2026-11-19" not in p


def test_la_fecha_de_hoy_por_defecto_es_la_de_guatemala(marca, temporada):
    assert ojos._fecha_texto(date(2026, 1, 5)) == "5 de enero de 2026 (2026-01-05)"
    assert "(20" in ojos.instruccion(marca, temporada, 12.0)


def test_los_trucos_y_curiosidades_se_permiten_y_los_hacks_no(marca, temporada):
    p = ojos.instruccion(marca, temporada, 12.0)
    assert "trucos del propio juego" in p and "SÍ se permiten" in p and "no son motivo de descarte" in p
    assert "programas de trampas externos (hacks)" in p and "copia pirata" in p
    assert "extraído del juego (filtración)" in p


# ---------------------------------------------------------------- 3. subtítulos ya quemados

def test_subtitulos_quemados_esta_en_el_esquema_y_vale_false_por_defecto(marca, temporada):
    assert "subtitulos_quemados" in ojos.CAMPOS_BOOL
    props = ojos.esquema_respuesta()["properties"]
    assert props["subtitulos_quemados"] == {"type": "BOOLEAN"} and props["caption"] == {"type": "STRING"}
    assert "subtitulos_quemados" in ojos.esquema_respuesta()["required"]
    datos = cargar_dato("valida")
    assert "subtitulos_quemados" not in datos and ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)[0]
    datos["subtitulos_quemados"] = "sí"
    ok, motivos = ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)
    assert not ok and any("subtitulos_quemados" in m for m in motivos)
    assert ojos.validar_respuesta(cargar_dato("subtitulos_quemados"), marca, temporada, duracion=20.0)[0]


def test_el_prompt_pide_subtitulos_quemados_y_caption(marca, temporada):
    p = ojos.instruccion(marca, temporada, 12.0, streamer="MIRIAM PETRICOR")
    assert "`subtitulos_quemados`" in p and "`caption`" in p and "MIRIAM PETRICOR" in p and "120" in p


def toma_sub(**kw):
    return Toma(numero=3, gancho="Un pájaro abre las alas", palabra_lapiz="ALAS", escena="EXT. RAMA — AMANECER",
                credito="Con: twitch.tv/x", subtitulos_del_clip=True, **kw)


def test_las_placas_de_una_toma_con_subtitulos_del_clip_no_llevan_dialogo(marca):
    lineas = [{"t0": 1.0, "t1": 3.0, "nombre": "ANA", "frase": "Hola"}]
    con = placas.construir(marca, Toma(numero=3, gancho="Un pájaro abre las alas", palabra_lapiz="ALAS", escena="E",
                                       credito="Con: x"), lineas, 10.0)
    sin = placas.construir(marca, toma_sub(), lineas, 10.0)
    assert [p.tipo for p in con if p.tipo.startswith("dialogo")] == ["dialogo_1"]
    assert not [p.tipo for p in sin if p.tipo.startswith("dialogo")]
    assert {"fila", "cabecera", "mosca", "cierre"} <= {p.tipo for p in sin}          # lo demás sí va


def test_la_orden_de_ffmpeg_sin_subtitulos_no_lleva_el_bloque_de_dialogo(marca):
    def orden(t):
        lineas = [{"t0": 1.0, "t1": 3.0, "nombre": "ANA", "frase": "Hola"}]
        todas = placas.construir(marca, t, lineas, 10.0)
        rutas = [(f"{p.tipo}.png", p.x, p.y, p.t0, p.t1) for p in todas]
        video = {"ruta": "f.mp4", "inicio": 0, "audio": True, "recorte": {"x": 0, "y": 0, "ancho": 1080, "alto": 1920}}
        return mod_componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=10.0, ancho=1080, alto=1920,
                                                           video=video, placas=rutas)
    assert any("dialogo_1.png" in x for x in orden(Toma(numero=3, gancho="Un pájaro abre las alas",
                                                         palabra_lapiz="ALAS", escena="E", credito="Con: x")))
    assert not any("dialogo" in x for x in orden(toma_sub()))


def test_el_plan_declara_subtitulos_del_clip_y_la_puerta_de_estilo_lo_acepta(marca):
    todas = placas.construir(marca, toma_sub(), [], 10.0)
    geo = {"cuerpo": {"x": 0, "y": 0, "ancho": 1080, "alto": 1920}, "capas": [p.capa() for p in todas]}
    plan = mod_componer.plan_json(marca, toma_sub(), [], 10.0, geometria=geo, plantilla=mod_componer.PLANTILLA_V2)
    assert plan["subtitulos"] == "del clip" and plan["lineas"] == []
    assert estilo._toma(plan).subtitulos_del_clip is True
    assert estilo.capas_coinciden(plan, marca)[0]
    assert estilo.sin_dialogo_propio(plan)[0]
    # con líneas o con placas de diálogo en un plan que dice «del clip», la puerta se pone en rojo
    assert not estilo.sin_dialogo_propio({**plan, "lineas": [{"t0": 1, "t1": 2, "frase": "x"}]})[0]
    sucio = copy.deepcopy(plan)
    sucio["geometria"]["capas"].append({"tipo": "dialogo_1", "x": 0, "y": 0, "ancho": 1, "alto": 1, "t0": 0, "t1": 1})
    assert not estilo.sin_dialogo_propio(sucio)[0]
    normal = mod_componer.plan_json(marca, Toma(numero=3, gancho="Un pájaro abre las alas", palabra_lapiz="ALAS"), [], 10.0)
    assert "subtitulos" not in normal


@pytest.mark.slow
def test_hacer_toma_con_subtitulos_quemados_sale_en_verde_sin_dialogo(repo, tmp_path, video20, carpetas):
    r = correr(repo, "subtitulos_quemados", tmp_path, video20)
    assert r.ok, r.motivos
    assert all(ok for _, ok, _ in r.puntos), [(n, d) for n, ok, d in r.puntos if not ok]
    assert r.plan["subtitulos"] == "del clip" and r.plan["lineas"] == []
    assert not [c for c in r.plan["geometria"]["capas"] if c["tipo"].startswith("dialogo")]
    assert any("Subtítulos del clip" in n and ok for n, ok, _ in r.puntos)
    assert json.loads(r.mp4.with_suffix(".plan.json").read_text(encoding="utf-8"))["subtitulos"] == "del clip"
    assert r.gemini["caption"]                                  # el caption llega para la publicación


@pytest.mark.slow
def test_hacer_toma_sin_subtitulos_quemados_sigue_con_dialogo(repo, tmp_path, video20, carpetas):
    r = correr(repo, "valida", tmp_path, video20)
    assert r.ok and "subtitulos" not in r.plan and r.plan["lineas"]


# ---------------------------------------------------------------- 4. caption distinto del gancho

TOMA_C = {"numero": 4, "gancho": "Pollo atropella un coche", "pilar": "momentos", "creador": "twitch.tv/ibai",
          "etiquetas": ["#GTA6", "#Gaming", "#Clips"]}


def ficha_t(login="ibai"):
    return {"id": f"t-{login}", "tipo": "T", "creador": f"twitch.tv/{login}", "credito": f"twitch.tv/{login}",
            "twitch_login": login, "origen": "twitch",
            "reglas": {"etiquetas_obligatorias": ["#GTA6", "#Gaming", "#Clips"], "divulgacion": "ninguna"},
            "campana": None, "estado": "vigente", "uso": "produccion"}


def primera(toma, red="shorts", ficha=None):
    return armar_textos(toma, ficha or ficha_t(), red)["texto"].split("\n")[0]


def test_el_caption_de_gemini_se_usa_en_lugar_del_gancho():
    t = {**TOMA_C, "caption": "¿Quién conducía ese coche?"}
    assert primera(t) == "Toma 004. ¿Quién conducía ese coche?"
    r = armar_textos(t, ficha_t(), "shorts")
    assert r["titulo"] == "Toma 004. ¿Quién conducía ese coche?" and "Pollo atropella" not in r["texto"]


@pytest.mark.parametrize("caption", [
    None, "", "   ", 5,
    "Pollo atropella un coche",                      # igual
    "POLLO ATROPELLA UN COCHE!!",                    # igual normalizado
    "Pollo atropella un coche en pleno directo",     # contiene al gancho
    "atropella un coche",                            # el gancho lo contiene
    "Un pollo atropella un coche",                   # casi igual
    "x" * 121,                                       # larga
    "Mira esto",                                     # genérico
    "Dice @alguien algo",                            # arroba
    "Gemini lo vio venir",                           # nombre del modelo
    "Rubius lo vio venir",                           # nombre vetado
])
@pytest.mark.parametrize("pilar", ["momentos", "primeras_veces", "dato_util", "compilados"])
def test_sin_caption_valido_se_usa_una_frase_de_respaldo_distinta_del_gancho(caption, pilar):
    t = {**TOMA_C, "pilar": pilar, "caption": caption}
    for red in ("shorts", "tiktok", "reels"):
        linea = primera(t, red)
        assert linea.startswith("Toma 004. ") and len(linea) > len("Toma 004. ")
        texto = armar_textos(t, ficha_t(), red)["texto"]
        assert not mod_textos._casi_igual(linea[len("Toma 004. "):], t["gancho"])
        assert "pollo atropella un coche" not in mod_textos._n(texto)       # nunca el gancho completo


def test_el_respaldo_es_determinista_y_cambia_con_el_numero():
    a = primera({**TOMA_C, "caption": None})
    assert a == primera({**TOMA_C, "caption": None})
    assert len({primera({**TOMA_C, "numero": n}) for n in range(1, 6)}) == 5


def test_el_caption_sin_emojis_ni_hashtags_y_en_tipo_oracion():
    t = {**TOMA_C, "caption": "😂 ¿Lo habías visto antes? #gta6"}
    assert primera(t) == "Toma 004. ¿Lo habías visto antes?"
    assert primera({**TOMA_C, "caption": "¿ESTO ES NORMAL EN EL JUEGO?"}).startswith("Toma 004. ¿Esto es normal")


def test_un_respaldo_que_se_parece_al_gancho_se_salta():
    t = {**TOMA_C, "gancho": "Un dato curioso del juego", "pilar": "dato_util", "caption": None, "numero": 3}
    assert mod_textos._casi_igual(mod_textos.RESPALDOS_CAPTION["dato_util"][3], t["gancho"])    # el que tocaba
    linea = primera(t)
    assert linea.startswith("Toma 003. ") and "dato curioso del juego" not in mod_textos._n(linea)


def test_el_caption_sigue_en_la_campana_y_el_gancho_se_sigue_validando():
    from test_textos import TOMA_A, ficha_fm27
    t = {**TOMA_A, "caption": "¿Habrá sido una buena inversión?"}
    for red in ("shorts", "tiktok", "reels"):
        assert armar_textos(t, ficha_fm27(), red)["texto"].startswith("Toma 007. ¿Habrá sido una buena inversión?\n\n")
    from fabrica.publicar import Descartada
    with pytest.raises(Descartada, match="gancho genérico"):
        armar_textos({**t, "gancho": "Mira esto"}, ficha_fm27(), "shorts")


def test_la_informativa_no_cambia():
    from test_textos import TOMA_I
    r = armar_textos(TOMA_I, ficha_t(), "shorts")
    assert r["texto"].startswith("GTA 6: Llega el 19 de noviembre")


def test_el_ciclo_pasa_el_caption_de_gemini_a_la_toma():
    from types import SimpleNamespace as S
    from fabrica import ciclo
    r = S(fila={"toma": 7, "gancho": "G", "pilar": "momentos", "duracion_s": 10, "historia": ""},
          gemini={"caption": "Una frase"})
    c = S(creador="twitch.tv/x", id_origen="a", ids_origen=[])
    assert ciclo._toma_dict(r, c, {"id": "t-x"})["caption"] == "Una frase"
    r.gemini = None
    assert ciclo._toma_dict(r, c, {"id": "t-x"})["caption"] == ""


# ---------------------------------------------------------------- 5. nombre real del streamer

@pytest.mark.parametrize("extra,esperado", [
    ({"nombre_visible": "Miriam_Petricor"}, "MIRIAM PETRICOR"),
    ({"nombre_visible": "Miriam_Petricor", "apodo": "La Miri"}, "LA MIRI"),
    ({}, "MIRIAM PETRICOR"),                                 # sin nada: el login, sin guiones bajos
    ({"apodo": "  la  miri "}, "LA MIRI"),
])
def test_nombre_para_mostrar(extra, esperado):
    ficha = {"twitch_login": "miriam_petricor", "creador": "twitch.tv/miriam_petricor", **extra}
    assert nombres.nombre_para_mostrar("twitch.tv/miriam_petricor", ficha) == esperado
    assert nombres.nombre_para_mostrar("miriam_petricor", ficha) == esperado


def test_nombre_vetado_o_de_otro_creador_no_se_usa():
    ficha = {"twitch_login": "ana", "creador": "twitch.tv/ana", "apodo": "Ibai", "nombre_visible": "Ana_Lia"}
    assert nombres.nombre_para_mostrar("ana", ficha) == "ANA LIA"              # el apodo vetado cae al siguiente
    ficha["nombre_visible"] = "Auronplay"
    assert nombres.nombre_para_mostrar("ana", ficha) == "ANA"                  # los dos vetados: el login
    # el creador que llega por archivo (campañas) no es el de la ficha: no se le pone su apodo
    assert nombres.nombre_para_mostrar("@sebasberon", {**ficha, "apodo": "La Miri"}) == "SEBASBERON"
    assert nombres.nombre_para_mostrar(None, ficha) == ""


def test_armar_pone_el_nombre_visible_en_hablante_y_escena_y_el_login_en_el_credito(marca, temporada):
    ficha = {**ficha_propia(creador="twitch.tv/miriam_petricor", twitch_login="miriam_petricor"),
             "nombre_visible": "Miriam_Petricor"}
    datos = cargar_dato("valida")
    datos["lineas"][0]["hablante"] = "STREAMER"
    datos["escena"] = "INT. STREAM — MINUTO 3"
    t, lineas, _, _ = mod_toma.armar(datos, marca, ficha, 5, temporada=temporada, raiz=RAIZ_REAL)
    assert lineas[0].nombre == "MIRIAM PETRICOR" and lineas[1].nombre == "OTRA VOZ"
    assert t.escena == "INT. STREAM DE MIRIAM PETRICOR"
    assert t.credito == "Con: twitch.tv/miriam_petricor"
    assert nombres.revisar_nombres(t, lineas, "MIRIAM PETRICOR", RAIZ_REAL, tambien=("twitch.tv/miriam_petricor",)) == []
    ficha["apodo"] = "La Miri"
    t, lineas, _, _ = mod_toma.armar(datos, marca, ficha, 5, temporada=temporada, raiz=RAIZ_REAL)
    assert lineas[0].nombre == "LA MIRI" and t.escena == "INT. STREAM DE LA MIRI"
    del ficha["apodo"], ficha["nombre_visible"]
    t, lineas, _, _ = mod_toma.armar(datos, marca, ficha, 5, temporada=temporada, raiz=RAIZ_REAL)
    assert lineas[0].nombre == "MIRIAM PETRICOR"


def test_el_modelo_no_pone_el_nombre(marca, temporada):
    ficha = {**ficha_propia(creador="twitch.tv/ana", twitch_login="ana"), "apodo": "Anita"}
    datos = cargar_dato("valida")
    datos["lineas"][0]["hablante"] = "Pedro"            # un nombre inventado por el modelo
    datos["lineas"][1]["hablante"] = "ANITA"
    _, lineas, _, _ = mod_toma.armar(datos, marca, ficha, 5, temporada=temporada, raiz=RAIZ_REAL)
    assert lineas[0].nombre == "OTRA VOZ" and lineas[1].nombre == "ANITA"


def test_la_ficha_t_guarda_el_nombre_visible_de_twitch_y_conserva_el_apodo(tmp_path):
    cfg = detectar.config_t(RAIZ_REAL)
    f = detectar._ficha_t("miriam_petricor", "123", 5000, "GTA 6", cfg, date(2026, 11, 22), ["#GTA6"], "Miriam_Petricor")
    assert f["nombre_visible"] == "Miriam_Petricor" and f["twitch_login"] == "miriam_petricor"
    assert "nombre_visible" not in detectar._ficha_t("x", "1", 1, "GTA 6", cfg, date(2026, 11, 22), [], "")
    assert "nombre_visible" not in detectar._ficha_t("x", "1", 1, "GTA 6", cfg, date(2026, 11, 22), [], "a<b>")
    (tmp_path / "estado").mkdir()
    ruta = tmp_path / "estado" / "fichas-t.yml"
    ruta.write_text(yaml.safe_dump([{**f, "nombre_visible": "Viejo", "apodo": "La Miri"}]), encoding="utf-8")
    detectar.guardar_fichas_t(tmp_path, [f])
    (g,) = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    assert g["nombre_visible"] == "Miriam_Petricor" and g["apodo"] == "La Miri"


def test_validar_acepta_apodo_y_nombre_visible_y_rechaza_los_malos():
    def errores(**campos):
        inf = validar.Informe()
        validar._validar_campos_n3({"twitch_login": "ana", **campos}, "ficha", inf)
        return inf.errores
    assert errores(apodo="La Miri", nombre_visible="Miriam_Petricor") == []
    assert errores() == []
    for malo in ("", "   ", 7, "x" * 41, "@ana", "ana/clip", "#ana", "<b>"):
        assert any("apodo" in e for e in errores(apodo=malo)), malo
        assert any("nombre_visible" in e for e in errores(nombre_visible=malo)), malo


def test_la_regla_de_7_dias_es_la_de_main_y_los_respaldos_de_dialogo_no_chocan(tmp_path):
    from fabrica import publicar
    from fabrica.publicar import Descartada
    assert not hasattr(publicar, "_clave_gancho")
    hoy = date(2026, 11, 22)
    a = {**TOMA_C, "numero": 1, "caption": None, "gancho": "Pollo atropella un coche",
         "frase": "Ya podéis perrear", "hablante": "MIRIAM PETRICOR"}
    b = {**a, "numero": 8, "gancho": "Dragón sobrevuela la ciudad", "frase": "No me lo puedo creer"}
    ta = armar_textos(a, ficha_t(), "shorts", tmp_path, hoy)
    assert primera(a) == "Toma 001. «Ya podéis perrear», dice MIRIAM PETRICOR"
    ruta = tmp_path / "estado" / "buffer-textos.json"
    publicar._escribir_json(ruta, [{"fecha": "2026-11-21", "plataforma": "shorts",
                                    "huella": publicar.hashlib.sha256(publicar._cuerpo_sin_toma(ta["texto"]).encode()).hexdigest()}])
    armar_textos(b, ficha_t(), "shorts", tmp_path, hoy)                  # frases distintas: no chocan
    with pytest.raises(Descartada, match="repetido"):
        armar_textos({**a, "numero": 8}, ficha_t(), "shorts", tmp_path, hoy)   # mismo texto: choca, como en main


def test_el_respaldo_sin_frase_o_con_frase_mala_vuelve_a_las_frases_fijas():
    base = {**TOMA_C, "caption": None, "hablante": "MIRIAM PETRICOR"}
    assert primera({**base, "frase": ""}) == primera({**base, "frase": None, "hablante": ""})
    assert "dice" not in primera({**base, "frase": "x" * 130})                   # demasiado larga
    assert "dice" not in primera({**base, "frase": "Pollo atropella un coche"})   # igual al gancho
    assert "dice" not in primera({**base, "frase": "Rubius se ríe"})              # nombre vetado
    assert "dice" not in primera({**base, "frase": "Ya podéis perrear", "hablante": ""})   # sin nombre


def test_el_caption_de_gemini_con_palabra_vetada_de_la_ficha_usa_el_respaldo():
    ficha = {**ficha_t(), "reglas": {**ficha_t().get("reglas", {}), "palabras_evitar": ["filtración", "pirata"]}}
    for malo in ("Esto parece una filtración del juego", "Versión pirata a la vista"):
        t = {**TOMA_C, "caption": malo, "frase": "Ya podéis perrear", "hablante": "ANA"}
        linea = primera(t, ficha=ficha)
        assert "filtraci" not in linea and "pirata" not in linea and linea == "Toma 004. «Ya podéis perrear», dice ANA"
    ok = primera({**TOMA_C, "caption": "¿Quién conducía ese coche?"}, ficha=ficha)
    assert ok == "Toma 004. ¿Quién conducía ese coche?"
    assert primera({**TOMA_C, "caption": "Rubius lo vio venir"}, ficha=ficha) != "Toma 004. Rubius lo vio venir"


def test_toma_dict_lleva_la_primera_frase_del_streamer_y_su_nombre():
    from types import SimpleNamespace as S
    from fabrica import ciclo
    gem = {"caption": "", "lineas": [{"hablante": "OTRA VOZ", "es": "Hola"}, {"hablante": "STREAMER", "es": "Ya podéis perrear"}]}
    r = S(fila={"toma": 7, "gancho": "G", "pilar": "momentos", "duracion_s": 10, "historia": ""}, gemini=gem)
    c = S(creador="twitch.tv/miriam_petricor", id_origen="a", ids_origen=[])
    t = ciclo._toma_dict(r, c, {"id": "t-x", "twitch_login": "miriam_petricor", "creador": "twitch.tv/miriam_petricor"})
    assert t["frase"] == "Ya podéis perrear" and t["hablante"] == "MIRIAM PETRICOR"


def test_con_login_apodo_y_nombre_visible_vetados_no_hay_nombre_y_el_credito_queda(marca, temporada):
    ficha = {"twitch_login": "auronplay", "creador": "twitch.tv/auronplay", "apodo": "Ibai", "nombre_visible": "Rubius"}
    assert nombres.nombre_para_mostrar("twitch.tv/auronplay", ficha) == ""
    assert nombres.nombre_para_mostrar("auronplay", {"twitch_login": "auronplay"}) == ""
    assert nombres.nombre_para_mostrar("ana", {"twitch_login": "ana"}) == "ANA"
    ficha_ok = {**ficha, "nombre_visible": "Ana_Lia"}
    assert nombres.nombre_para_mostrar("auronplay", ficha_ok) == "ANA LIA"          # basta un nombre bueno
    ficha = {**ficha_propia(creador="twitch.tv/auronplay", twitch_login="auronplay"), "apodo": "Ibai", "nombre_visible": "Rubius"}
    toma, lineas, _, _ = mod_toma.armar(cargar_dato("valida"), marca, ficha, 5, "twitch.tv/auronplay", temporada)
    assert toma.escena == "INT. STREAM"
    assert {ln.nombre for ln in lineas if ln.nombre} <= {nombres.OTRA_VOZ}
    assert "auronplay" in toma.credito.lower()                       # el crédito de campaña se queda


def test_compilado_descarta_el_segmento_con_aviso(tmp_path, marca, temporada, monkeypatch):
    import types
    from fabrica import formatos
    from fabrica.editar.lienzo import Toma
    toma = Toma(numero=1, gancho="x", palabra_lapiz="x", lapiz=1, escena="INT. STREAM", credito="Con: a")
    monkeypatch.setattr(formatos, "armar", lambda *a, **k: (toma, [], 0.0, 10.0))
    monkeypatch.setattr(formatos.ojos, "validar_respuesta", lambda *a, **k: (True, []))
    monkeypatch.setattr(formatos.ojos, "mirar", lambda *a, **k: types.SimpleNamespace(datos={"spoiler": True}, motivo=""))
    monkeypatch.setattr(formatos, "copia_para_ojos", lambda *a, **k: tmp_path / "x.mp4")
    monkeypatch.setattr(formatos.mod_descargar, "descargar", lambda *a, **k: _Desc(tmp_path))
    ctx = types.SimpleNamespace(ahora=AHORA, raiz=RAIZ_REAL, pila=_Pila(), cfg={}, marca=marca, temporada=temporada,
                                numero=1, abrir=None, ejecutor=None, transporte=None, espera=None, entorno=None)
    monkeypatch.setattr(formatos, "_recorte_de", lambda *a, **k: None)
    monkeypatch.setattr(formatos.ojos, "temporada_de", lambda f, t: t)
    monkeypatch.setattr(formatos.ojos, "juego_de", lambda f, t: "GTA 6")
    monkeypatch.setattr(formatos.ojos, "nota_de_ficha", lambda f: "")
    cand = types.SimpleNamespace(id_origen="a", url="u", creador="ana", creado_en=AHORA)
    with pytest.raises(formatos.ErrorSegmento, match="aviso spoiler: no va en compilados"):
        formatos.preparar_segmento(cand, {"id": "f", "creador": "ana"}, ctx)


class _Pila:
    def enter_context(self, x):
        return x


class _Desc:
    def __init__(self, ruta):
        self.ruta, self.duracion = ruta / "x.mp4", 20.0

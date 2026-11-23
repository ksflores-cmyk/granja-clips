"""Avisos al inicio (22 nov 2026, decisión de Kevin): spoiler, cinemática y mods ya no descartan la toma; la marcan y
abre con una placa simple («ALERTA SPOILER · CINEMÁTICA · MODS») de 0 a `avisos.duracion_s` de marca.yml. Sin red."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace as L

import pytest

from conftest import RAIZ_REAL
from fabrica import ojos, validar
from fabrica.editar import componer as mod_componer
from fabrica.editar import estilo, placas, toma as mod_toma
from fabrica.editar.lienzo import Toma
from fabrica.editar.marca import Marca
from fabrica.editar import sin_ia
from test_n4 import (carpetas, cargar_dato, correr, marca, temporada, video20)  # noqa: F401  (fixtures y ayudas)


def toma_con(*avisos, **kw):
    return Toma(numero=3, gancho="Un pájaro abre las alas", palabra_lapiz="ALAS", escena="EXT. RAMA — AMANECER",
                credito="Con: Blender Foundation (CC BY 3.0)", avisos=list(avisos), **kw)


# ---------------------------------------------------------------- 1. nada de esto descarta

@pytest.mark.parametrize("nombre,aviso", [("spoiler", "spoiler"), ("cinematica", "cinematica"), ("mods", "mods")])
def test_gemini_con_spoiler_cinematica_o_mods_ya_no_descarta_y_marca(nombre, aviso, marca, temporada):
    datos = cargar_dato(nombre)
    assert ojos.validar_respuesta(datos, marca, temporada, duracion=20.0) == (True, [])
    assert list(ojos.avisos_de(datos, temporada)) == [aviso]


def test_lo_demas_sigue_descartando(marca, temporada):
    for nombre, fragmento in (("desnudez", "desnudez"), ("juego_incorrecto", "juego esperado")):
        ok, motivos = ojos.validar_respuesta(cargar_dato(nombre), marca, temporada, duracion=20.0)
        assert not ok and any(fragmento in m for m in motivos)


def test_mods_ausente_vale_false_y_mal_tipado_se_descarta(marca, temporada):
    datos = cargar_dato("valida")
    assert "mods" not in datos and ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)[0]
    assert ojos.avisos_de(datos, temporada) == {}
    datos["mods"] = "sí"
    ok, motivos = ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)
    assert not ok and any("campo mods" in m for m in motivos)


def test_el_esquema_y_el_prompt_piden_mods_y_ya_no_vetan_por_spoiler():
    assert "mods" in ojos.CAMPOS_BOOL and "mods" in ojos.esquema_respuesta()["properties"]
    prompt = (RAIZ_REAL / "fabrica" / "ojos_prompt.md").read_text(encoding="utf-8")
    assert "`mods: true`" in prompt and "`spoiler: true`" in prompt and "`cinematica: true`" in prompt
    assert "trampas, mods o exploits; o si" not in prompt          # ya no es motivo de `apto: false`


def test_palabra_a_evitar_en_gancho_escena_o_linea_marca_y_no_descarta(marca, temporada):
    datos = cargar_dato("valida")
    datos["gancho"] = "Llega el jefe final ya"
    datos["palabra_lapiz"] = "FINAL"
    assert ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)[0]
    assert any("jefe final" in m and "gancho" in m for m in ojos.avisos_de(datos, temporada)["spoiler"])
    datos = cargar_dato("valida")
    datos["lineas"][0]["es"] = "Ya vi el final del juego entero"
    assert ojos.validar_respuesta(datos, marca, temporada, duracion=20.0)[0]
    assert any("línea 1" in m for m in ojos.avisos_de(datos, temporada)["spoiler"])


def test_palabra_a_evitar_en_una_linea_de_whisper_marca(temporada):
    t = toma_con()
    lineas = [L(nombre="X", frase="no me digas el FINAL del juego", acotacion="")]
    mod_toma.marcar_avisos(t, cargar_dato("valida"), temporada, lineas)
    assert t.avisos == ["spoiler"] and "línea 1" in t.avisos_motivos["spoiler"][0]
    mod_toma.marcar_avisos(t, cargar_dato("valida"), temporada, [L(nombre="X", frase="qué locura", acotacion="")])
    assert t.avisos == [] and t.avisos_motivos == {}


def test_marcar_avisos_los_ordena_y_los_junta(temporada):
    datos = {**cargar_dato("valida"), "mods": True, "cinematica": True, "spoiler": True}
    t = mod_toma.marcar_avisos(toma_con(), datos, temporada, [])
    assert t.avisos == ["spoiler", "cinematica", "mods"] and set(t.avisos_motivos) == set(t.avisos)


def test_sin_ia_una_palabra_a_evitar_en_el_titulo_ya_no_descarta(marca, temporada):
    g, motivo, definitivo = sin_ia.gancho_de("El final de la historia", None, marca, temporada)
    assert g == "El final de la historia" and motivo is None and definitivo is False


# ---------------------------------------------------------------- 2-3. la placa

def test_texto_de_la_placa_une_los_avisos_en_orden_fijo(marca):
    assert placas.texto_aviso(marca, ["spoiler"]) == "ALERTA SPOILER"
    assert placas.texto_aviso(marca, ["mods", "spoiler"]) == "ALERTA SPOILER · MODS"
    assert placas.texto_aviso(marca, ["mods", "cinematica", "spoiler"]) == "ALERTA SPOILER · CINEMÁTICA · MODS"


def _por_tipo(lista, tipo):
    return [p for p in lista if p.tipo == tipo]


@pytest.mark.parametrize("posicion", ["estandar", "alterna"])
def test_la_placa_de_aviso_abre_la_toma_y_el_gancho_entra_despues(marca, posicion):
    d = float(marca.avisos["duracion_s"])
    todas = placas.construir(marca, toma_con("spoiler", "cinematica"), [], 10.0, None, posicion)
    (a,), (c,) = _por_tipo(todas, "alerta"), _por_tipo(todas, "cabecera")
    assert a.t0 == 0.0 and a.t1 == d and 0.8 <= d <= 1.5
    assert c.t0 == d and c.t1 == d + float(marca.pc["gancho_s"])      # el gancho se ve lo mismo que siempre
    capas = [p.capa() for p in todas]
    accion = marca.datos["dividida"]["zona_accion"] if posicion == "alterna" else None
    assert placas.problemas_capas(capas, marca, accion) == []          # sin solapes, zona segura ni zona de acción
    assert a.ancho <= marca.pc["ancho_max_encabezado"] and a.y == c.y and a.x == c.x


def test_sin_avisos_no_hay_placa_y_el_gancho_empieza_en_0(marca):
    todas = placas.construir(marca, toma_con(), [], 10.0)
    assert _por_tipo(todas, "alerta") == [] and _por_tipo(todas, "cabecera")[0].t0 == 0.0


def test_la_toma_no_se_alarga_ni_cabe_un_aviso_en_una_toma_diminuta(marca):
    with pytest.raises(ValueError, match="no cabe el aviso"):
        placas.construir(marca, toma_con("spoiler"), [], 2.0)


def test_la_orden_de_ffmpeg_lleva_el_aviso_entre_0_y_duracion_s(marca, tmp_path):
    d = float(marca.avisos["duracion_s"])

    def orden(t):
        todas = placas.construir(marca, t, [], 10.0)
        rutas = [(f"{p.tipo}.png", p.x, p.y, p.t0, p.t1) for p in todas]
        video = {"ruta": "f.mp4", "inicio": 0, "audio": True,
                 "recorte": {"x": 0, "y": 0, "ancho": 1080, "alto": 1920}}
        cmd = mod_componer.orden_ffmpeg_pantalla_completa(salida="s.mp4", duracion=10.0, ancho=1080, alto=1920,
                                                          video=video, placas=rutas)
        return cmd, rutas

    cmd, rutas = orden(toma_con("spoiler"))
    i = [r[0] for r in rutas].index("alerta.png")
    filtro = next(x for x in cmd if "overlay" in x)
    assert f"enable='gte(t,0)*lt(t,{d:g})'" in filtro.split(";")[i + 1]
    assert "alerta.png" in cmd
    cmd2, rutas2 = orden(toma_con())
    assert "alerta.png" not in cmd2 and "alerta.png" not in [r[0] for r in rutas2]


def test_el_plan_declara_los_avisos_y_la_puerta_de_estilo_dibuja_la_misma_placa(marca):
    t = toma_con("spoiler", "mods", avisos_motivos={"spoiler": ["x"], "mods": ["y"]})
    todas = placas.construir(marca, t, [], 10.0)
    geo = {"cuerpo": {"x": 0, "y": 0, "ancho": 1080, "alto": 1920}, "capas": [p.capa() for p in todas]}
    plan = mod_componer.plan_json(marca, t, [], 10.0, geometria=geo, plantilla=mod_componer.PLANTILLA_V2)
    assert plan["avisos"] == ["spoiler", "mods"] and plan["spoiler"] == {"es": True, "motivos": ["x"]}
    assert plan["avisos_motivos"] == {"spoiler": ["x"], "mods": ["y"]}
    assert estilo._toma(plan).avisos == ["spoiler", "mods"]
    ok, _ = estilo.capas_coinciden(plan, marca)
    assert ok
    sin = mod_componer.plan_json(marca, toma_con(), [], 10.0)
    assert "avisos" not in sin and "spoiler" not in sin


# ---------------------------------------------------------------- validar.py

def _validar_avisos(cambios):
    m = copy.deepcopy(Marca.cargar(RAIZ_REAL).datos)
    for k, v in cambios.items():
        if k == "textos":
            m["avisos"]["textos"].update(v)
        else:
            m["avisos"][k] = v
    inf = validar.Informe()
    validar.validar_avisos(m, inf)
    return inf.errores


def test_validar_acepta_marca_yml_y_rechaza_duracion_fuera_de_rango():
    assert _validar_avisos({}) == []
    for ok in (0.8, 1.2, 1.5):
        assert _validar_avisos({"duracion_s": ok}) == []
    for mal in (0.7, 1.6, 2.0, "1.2", True):
        assert any("duracion_s" in e for e in _validar_avisos({"duracion_s": mal})), mal


def test_validar_rechaza_textos_malos_o_ausentes():
    assert any("textos.mods" in e for e in _validar_avisos({"textos": {"mods": "alerta mods"}}))
    assert any("textos.spoiler" in e for e in _validar_avisos({"textos": {"spoiler": "ALERTA " + "X" * 30}}))
    m = copy.deepcopy(Marca.cargar(RAIZ_REAL).datos)
    del m["avisos"]
    inf = validar.Informe()
    validar.validar_avisos(m, inf)
    assert any("falta la clave 'avisos'" in e or "avisos" in e for e in inf.errores)


# ---------------------------------------------------------------- de punta a punta

@pytest.mark.slow
@pytest.mark.parametrize("nombre,avisos", [("spoiler", ["spoiler"]), ("cinematica", ["cinematica"]), ("mods", ["mods"])])
def test_hacer_toma_con_aviso_sale_en_verde_y_lo_declara(nombre, avisos, repo, tmp_path, video20, carpetas):
    r = correr(repo, nombre, tmp_path, video20)
    assert r.ok, r.motivos
    assert all(ok for _, ok, _ in r.puntos), [(n, d) for n, ok, d in r.puntos if not ok]
    assert r.plan["avisos"] == avisos
    assert r.fila["notas"].startswith(f"{avisos[0]}:")
    alerta = [c for c in r.plan["geometria"]["capas"] if c["tipo"] == "alerta"]
    assert len(alerta) == 1 and alerta[0]["t0"] == 0.0 and 0.8 <= alerta[0]["t1"] <= 1.5
    assert json.loads(r.mp4.with_suffix(".plan.json").read_text(encoding="utf-8"))["avisos"] == avisos


@pytest.mark.slow
def test_hacer_toma_sin_aviso_no_lleva_placa(repo, tmp_path, video20, carpetas):
    r = correr(repo, "valida", tmp_path, video20)
    assert r.ok and "avisos" not in r.plan and not [c for c in r.plan["geometria"]["capas"] if c["tipo"] == "alerta"]
    assert not r.fila.get("notas")


def test_las_palabras_vetadas_de_la_ficha_no_son_spoilers_y_siguen_descartando(marca, temporada):
    ficha = {"juego": "Football Manager 27", "reglas": {"palabras_evitar": ["leak", "pirata"]}}
    t = ojos.temporada_de(ficha, temporada)
    assert t["palabras_evitar"] == [] and t["palabras_vetadas"] == ["leak", "pirata"]
    datos = cargar_dato("valida")
    datos["gancho"], datos["palabra_lapiz"] = "Leak del fichaje de Sebas", "FICHAJE"
    ok, motivos = ojos.validar_respuesta(datos, marca, t, duracion=20.0)
    assert not ok and any("palabra a evitar 'leak'" in m for m in motivos)

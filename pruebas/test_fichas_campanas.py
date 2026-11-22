"""#23: fichas tipo A de las 3 campañas de Content Rewards a las que Kevin está unido, y FM27 pausada. Sin red."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from conftest import RAIZ_REAL, como_vigente
from fabrica import brief_cr, enviar_cr, fuentes, validar

HOY = date(2026, 11, 21)
CAMPANAS = {
    "sega-black-friday": ("1391fb95-e8a3-4f0b-b146-d0d786e6aee0", "SEGA - Steam Black Friday Sale Clipping"),
    "cod-mw4-dmz-intel": ("a1cefc3b-4533-438f-8e70-a7299550f1cf", "Call of Duty - MW4 DMZ Intel Briefing Clipping"),
    "reach-expedia-speed": ("03bda4fd-3474-4b1e-a914-88aaf3ac8372", "$8,000 IShowSpeed x Expedia Clipping Campaign"),
}


def fichas() -> dict:
    return {f["id"]: f for f in fuentes.cargar_fuentes(RAIZ_REAL)}


@pytest.fixture(scope="module")
def reales():
    return fichas()


# ---------------------------------------------------------------- las tres fichas nuevas

@pytest.mark.parametrize("fid", list(CAMPANAS))
def test_ficha_nueva_con_id_nombre_exacto_y_pagina(reales, fid):
    cr_id, nombre = CAMPANAS[fid]
    f = reales[fid]
    assert f["tipo"] == "A" and f["uso"] == "produccion"
    assert f["cr_campaign_id"] == cr_id and f["campana"] == nombre
    assert f["permiso_url"] == enviar_cr.BASE + cr_id
    assert str(f["permiso_leido"]) == "2026-11-21"
    for c in ("tarifa_usd_por_mil", "minimo_por_clip_usd", "tope_por_clip_usd", "presupuesto_restante",
              "presupuesto_total", "vence"):
        assert f.get(c) is not None, c
    assert 0 <= f["presupuesto_restante"] <= f["presupuesto_total"]
    r = f["reglas"]
    for c in ("duracion_s", "plataformas", "divulgacion", "idioma", "audiencia", "contenido"):
        assert r.get(c), c
    assert r["idioma"] == "en"                                      # inglés, dentro de la misma marca


@pytest.mark.parametrize("fid", list(CAMPANAS))
def test_ficha_nueva_pausada_con_motivo_y_sin_material_inventado(reales, fid):
    f = reales[fid]
    assert f["estado"] == "pausada" and f["publicable"] is False
    assert len(f["motivo_pausa"]) > 60
    assert "material" not in f                                     # sólo el del brief; aún no se pudo copiar
    assert not fuentes.es_publicable(f, HOY)[0]
    assert not fuentes.autoriza_descarga(f, HOY, "produccion", "https://example.org/x.mp4", pilar="momentos")[0]


def test_sega_reglas_del_brief(reales):
    f = reales["sega-black-friday"]
    r = f["reglas"]
    assert r["plataformas"] == ["shorts", "tiktok", "reels"]
    assert r["divulgacion"] in ("#Ad", "#Advertisement", "#Sponsored")
    assert r["etiquetas_por_red"] == {"tiktok": ["@sega_west"], "shorts": ["@sega_west"], "reels": ["@sega"]}
    assert f["tarifa_por_red"] == {"reels": 3.5, "tiktok": 2.0, "shorts": 2.0}
    assert (f["tope_por_clip_usd"], f["presupuesto_total"]) == (800, 12000)
    assert "Steam" in r["contenido"] and "tiempo limitado" in r["contenido"]
    assert "Pakistán" in r["audiencia"]


def test_dmz_solo_tiktok_e_instagram(reales):
    f = reales["cod-mw4-dmz-intel"]
    assert f["reglas"]["plataformas"] == ["tiktok", "reels"]
    assert f["tarifa_por_red"] == {"tiktok": 2.0, "reels": 1.5}
    assert (f["tope_por_clip_usd"], f["presupuesto_total"]) == (1200, 30000)
    assert "DMZ Intel Briefing" in f["reglas"]["contenido"]


def test_expedia_pausada_por_presupuesto_y_choques_con_la_marca(reales):
    f = reales["reach-expedia-speed"]
    assert (f["tarifa_usd_por_mil"], f["minimo_por_clip_usd"], f["tope_por_clip_usd"]) == (1.0, 10, 500)
    assert f["presupuesto_restante"] <= 25
    assert f["reglas"]["etiquetas_obligatorias"] == ["#IShowSpeed"]
    m = f["motivo_pausa"]
    for pista in ("Presupuesto", "50 %", "IA", "Kyv61l2vF6M"):
        assert pista in m, pista
    assert "IA" in f["reglas"]["contenido"]                         # regla anti-IA de Reach anotada


def test_ids_y_nombres_de_campana_unicos(reales):
    ids = [f["cr_campaign_id"] for f in reales.values() if f.get("cr_campaign_id")]
    nombres = [f["campana"] for f in reales.values() if f.get("cr_campaign_id")]
    assert len(ids) == len(set(ids)) == 4 and len(nombres) == len(set(nombres))
    assert all(re.fullmatch(r"[0-9a-f-]{36}", i) for i in ids)


def test_el_robot_no_envia_a_ninguna_campana_pausada():
    assert enviar_cr.campanas_cr(fuentes.cargar_fuentes(RAIZ_REAL)) == {}


def test_al_reanudar_el_robot_si_la_conoce(reales):
    f = como_vigente(reales["sega-black-friday"])
    assert enviar_cr.campanas_cr([f]) == {"SEGA - Steam Black Friday Sale Clipping": CAMPANAS["sega-black-friday"][0]}


def test_validar_en_verde_con_las_fichas_reales():
    inf = validar.validar(RAIZ_REAL)
    assert inf.errores == [] and not any("sega-black-friday" in a or "cod-mw4" in a or "expedia" in a
                                         for a in inf.avisos)


# ---------------------------------------------------------------- FM27 pausada

def test_fm27_pausada_por_la_marca(reales):
    f = reales["sega-fm27"]
    assert (f["estado"], f["publicable"]) == ("pausada", False)
    assert "Pausada por la marca" in f["motivo_pausa"]
    assert not fuentes.es_publicable(f, HOY)[0]
    assert fuentes.es_publicable(como_vigente(f), HOY)[0]          # la copia vigente sigue sirviendo


# ---------------------------------------------------------------- validar: campos nuevos

def _errores(repo, editar_yaml, fid, fn):
    def cambio(datos):
        fn(next(f for f in datos if f["id"] == fid))
    editar_yaml(repo, "fuentes.yml", cambio)
    return validar.validar(repo).errores


def test_validar_exige_motivo_en_ficha_a_pausada(repo, editar_yaml):
    e = _errores(repo, editar_yaml, "cod-mw4-dmz-intel", lambda f: f.pop("motivo_pausa"))
    assert any("exige motivo_pausa" in x for x in e)


def test_validar_rechaza_cr_campaign_id_repetido_o_mal_escrito(repo, editar_yaml):
    e = _errores(repo, editar_yaml, "cod-mw4-dmz-intel",
                 lambda f: f.update(cr_campaign_id=CAMPANAS["sega-black-friday"][0]))
    assert any("cr_campaign_id repetido" in x for x in e)
    e = _errores(repo, editar_yaml, "cod-mw4-dmz-intel", lambda f: f.update(cr_campaign_id="A1CEFC3B"))
    assert any("uuid" in x for x in e)


def test_validar_rechaza_permiso_url_de_otra_campana(repo, editar_yaml):
    e = _errores(repo, editar_yaml, "sega-black-friday",
                 lambda f: f.update(permiso_url=enviar_cr.BASE + CAMPANAS["cod-mw4-dmz-intel"][0]))
    assert any("no es la página de la campaña" in x for x in e)


def test_validar_rechaza_etiquetas_y_tarifas_por_red_fuera_de_plataformas(repo, editar_yaml):
    e = _errores(repo, editar_yaml, "cod-mw4-dmz-intel",
                 lambda f: f["reglas"].update(etiquetas_por_red={"shorts": ["@x"]}))
    assert any("etiquetas_por_red con una red fuera" in x for x in e)
    e = _errores(repo, editar_yaml, "cod-mw4-dmz-intel", lambda f: f.update(tarifa_por_red={"tiktok": 2.0}))
    assert any("tarifa_por_red debe cubrir" in x for x in e)
    e = _errores(repo, editar_yaml, "sega-black-friday", lambda f: f.update(tarifa_por_red={"reels": "3,5"}))
    assert any("tarifa_por_red debe ser" in x for x in e)


def test_validar_rechaza_presupuesto_restante_mayor_que_total(repo, editar_yaml):
    e = _errores(repo, editar_yaml, "reach-expedia-speed", lambda f: f.update(presupuesto_restante=9000))
    assert any("presupuesto_restante mayor" in x for x in e)


# ---------------------------------------------------------------- lector de briefs (workflow brief-cr)

DOC = "https://docs.google.com/document/d/1J3Q_JrKuFZ1qCPDvC9f9y2YJWWWqGdKLn81zGIa1bQU/edit?usp=sharing"


def test_enlaces_utiles_filtra_navegacion_y_repetidos():
    hrefs = ["https://contentrewards.com/c/discover", DOC, DOC, "http://drive.google.com/x",
             "https://drive.google.com/drive/folders/abc", "https://www.youtube.com/live/Kyv61l2vF6M?t=10589",
             "https://evil-youtube.com/x", None, "https://whop.com/login"]
    assert brief_cr.enlaces_utiles(hrefs) == [DOC, "https://drive.google.com/drive/folders/abc",
                                              "https://www.youtube.com/live/Kyv61l2vF6M?t=10589"]


def test_url_texto_doc():
    assert brief_cr.url_texto_doc(DOC) == ("https://docs.google.com/document/d/"
                                           "1J3Q_JrKuFZ1qCPDvC9f9y2YJWWWqGdKLn81zGIa1bQU/export?format=txt")
    assert brief_cr.url_texto_doc("https://drive.google.com/drive/folders/abc") is None


class _Resp:
    def __init__(self, cuerpo=b"Brief\nUse #Ad", tipo="text/plain; charset=utf-8"):
        self.cuerpo, self.headers = cuerpo, {"Content-Type": tipo}

    def read(self, n=-1):
        return self.cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Pagina:
    def __init__(self, cuerpo, hrefs, url="https://contentrewards.com/c/campaigns/x"):
        self.cuerpo, self.hrefs, self.url, self.fotos = cuerpo, hrefs, url, []

    def goto(self, url, **k):
        self.visitada = url

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, sel):
        return self.cuerpo

    def eval_on_selector_all(self, sel, js):
        return self.hrefs

    def screenshot(self, path, **k):
        Path(path).write_bytes(b"png")


ID = "1391fb95-e8a3-4f0b-b146-d0d786e6aee0"


def test_leer_guarda_texto_enlaces_y_documento(tmp_path):
    p = _Pagina("SEGA - Steam Black Friday Sale Clipping\nReference materials\nBrief doc", [DOC, "https://x.org"])
    r = brief_cr.leer(p, ID, tmp_path, abrir=lambda req, timeout: _Resp())
    assert p.visitada == enviar_cr.BASE + ID
    assert r["titulo"] == "SEGA - Steam Black Friday Sale Clipping" and r["enlaces"] == [DOC] and not r["pausada"]
    assert (tmp_path / r["docs"][DOC]).read_text(encoding="utf-8") == "Brief\nUse #Ad"
    assert (tmp_path / f"{ID}.txt").exists() and (tmp_path / f"{ID}.png").exists()


def test_leer_documento_privado_queda_como_error_sin_romper(tmp_path):
    p = _Pagina("Campaña\nPausada por la marca", [DOC])
    r = brief_cr.leer(p, ID, tmp_path, abrir=lambda req, timeout: _Resp(b"<html>", "text/html"))
    assert r["pausada"] and r["docs"][DOC].startswith("error: ValueError")


def test_leer_redirigido_a_login_es_sesion_vencida(tmp_path):
    with pytest.raises(enviar_cr.SesionVencida):
        brief_cr.leer(_Pagina("Inicia sesión", [], url="https://contentrewards.com/login?next=x"), ID, tmp_path)


def test_leer_con_parte_bloqueada_la_anota_y_sigue(tmp_path, monkeypatch):
    resumen = tmp_path / "resumen.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumen))
    r = brief_cr.leer(_Pagina("Expedia\nJoin to unlock 1 requirement", [DOC]), ID, tmp_path,
                      abrir=lambda req, timeout: _Resp())
    assert r["bloqueada"] and r["enlaces"] == [DOC] and (tmp_path / f"{ID}.png").exists()
    brief_cr._resumen_paso(r)
    assert f"| {ID} | Expedia | 1 | 1 | no | sí |" in resumen.read_text(encoding="utf-8")


def test_principal_rechaza_ids_invalidos_y_sin_sesion_sale_3(tmp_path, monkeypatch, capsys):
    assert brief_cr.principal(["--campana", "no-es-uuid", "--salida", str(tmp_path)]) == 2
    monkeypatch.delenv("CR_SESION", raising=False)
    assert brief_cr.principal(["--campana", ID, "--salida", str(tmp_path)]) == enviar_cr.SALIDA_SESION
    assert "CR_SESION" in capsys.readouterr().out


def test_workflow_brief_cr_pasa_el_secreto_por_env_y_lee_las_tres():
    import yaml
    wf = yaml.safe_load((RAIZ_REAL / ".github" / "workflows" / "brief-cr.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["leer"]
    assert job["env"]["CR_SESION"] == "${{ secrets.CR_SESION }}"
    assert wf["permissions"] == {"contents": "read"}
    defecto = wf[True]["workflow_dispatch"]["inputs"]["campanas"]["default"].split()
    assert sorted(defecto) == sorted(c for c, _ in CAMPANAS.values())

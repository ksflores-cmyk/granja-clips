"""Robot de envío a Content Rewards: lógica pura (qué se envía y cuándo). El navegador no se prueba aquí."""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from fabrica import enviar_cr as cr

AHORA = datetime(2026, 11, 21, 15, 10, tzinfo=timezone.utc)
MAPA = {"Campaña X": "id-x"}


def fila(min_desde_ahora, campana="Campaña X", post="p1", plat="tiktok"):
    hora = (AHORA + timedelta(minutes=min_desde_ahora)).isoformat()
    return {"plataforma": plat, "post_id": post, "hora": hora, "campana": campana, "toma": 7}


def test_mapa_solo_fichas_vigentes_con_id():
    fuentes = [
        {"campana": "Campaña X", "cr_campaign_id": "id-x", "estado": "vigente"},
        {"campana": "Pausada", "cr_campaign_id": "id-p", "estado": "pausada"},
        {"campana": "Sin id", "estado": "vigente"},
        "basura",
    ]
    assert cr.campanas_cr(fuentes) == {"Campaña X": "id-x"}


@pytest.mark.parametrize("minutos,entra", [(-29, False), (-27, True), (0, True), (14, True), (16, False)])
def test_ventana_de_30_minutos(minutos, entra):
    assert bool(cr.pendientes([fila(minutos)], {}, MAPA, AHORA)) is entra


def test_campana_sin_id_no_se_envia():
    assert cr.pendientes([fila(0, campana="Otra")], {}, MAPA, AHORA) == []


def test_resultado_final_no_se_repite_pero_error_si():
    f = fila(-5)
    for final in ("enviado", "rechazado", "campana_pausada"):
        assert cr.pendientes([f], {"p1": {"resultado": final}}, MAPA, AHORA) == []
    assert len(cr.pendientes([f], {"p1": {"resultado": "error"}}, MAPA, AHORA)) == 1


def test_pendiente_lleva_el_id_de_la_campana():
    assert cr.pendientes([fila(-1)], {}, MAPA, AHORA)[0]["cr_campaign_id"] == "id-x"


def test_vencido():
    assert cr.vencido(fila(-29), AHORA)
    assert not cr.vencido(fila(-10), AHORA)
    assert cr.vencido({"hora": "no es fecha"}, AHORA)


def test_url_de_youtube_pasa_a_short():
    assert cr.normalizar_url("https://www.youtube.com/watch?v=TI5g-xDXblU") == "https://www.youtube.com/shorts/TI5g-xDXblU"
    t = "https://tiktok.com/@guionvivo/video/7710268883680234804"
    assert cr.normalizar_url(t) == t


def test_sesion_falta_o_ilegible(monkeypatch):
    monkeypatch.delenv("CR_SESION", raising=False)
    with pytest.raises(cr.SesionVencida):
        cr.cargar_sesion()
    monkeypatch.setenv("CR_SESION", "no-es-base64!!")
    with pytest.raises(cr.SesionVencida):
        cr.cargar_sesion()
    monkeypatch.setenv("CR_SESION", base64.b64encode(json.dumps({"cookies": []}).encode()).decode())
    with pytest.raises(cr.SesionVencida):
        cr.cargar_sesion()
    buena = {"cookies": [{"name": "a", "value": "b", "domain": "contentrewards.com", "path": "/"}], "origins": []}
    monkeypatch.setenv("CR_SESION", base64.b64encode(json.dumps(buena).encode()).decode())
    assert cr.cargar_sesion()["cookies"][0]["name"] == "a"


def test_anotar_y_leer(tmp_path):
    (tmp_path / "estado").mkdir()
    cr.anotar(tmp_path, "p1", resultado="enviado", url="u")
    cr.anotar(tmp_path, "p1", detalle="ok")
    h = cr.leer_hechos(tmp_path)
    assert h["p1"]["resultado"] == "enviado" and h["p1"]["detalle"] == "ok"


def test_hay_sale_10_sin_pendientes(tmp_path, monkeypatch):
    (tmp_path / "estado").mkdir()
    (tmp_path / "fuentes.yml").write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(cr, "cargar_yaml", lambda nombre, raiz=None: [])
    assert cr.principal(["--hay"], raiz=tmp_path) == cr.SALIDA_NADA

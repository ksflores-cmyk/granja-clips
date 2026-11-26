"""Cupo de Shorts (A-20261125-1): 2 por canal cada 24 h en ventana móvil; campañas primero; tardes."""
from datetime import datetime, timedelta, timezone

from fabrica import shorts
from fabrica.registro import ZONA

CFG = dict(shorts.POR_DEFECTO)


def gt(dia, hh, mm=0):
    return datetime(2026, 11, dia, hh, mm, tzinfo=ZONA)


def envio(toma, cuando, estado="creado", post_id=None, **extra):
    return {"plataforma": "shorts", "toma": toma, "estado": estado, "post_id": post_id or f"p{toma}",
            "dueAt": cuando.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), **extra}


FICHA_T = {"tipo": "T"}
FICHA_A = {"tipo": "A", "reglas": {"plataformas": ["shorts", "tiktok", "reels"]}}


def test_sin_envios_admite_por_la_tarde():
    ok, motivo = shorts.admite([], FICHA_T, gt(26, 16), gt(26, 15), CFG)
    assert ok and "1 de 2" in motivo


def test_manana_solo_campanas():
    assert not shorts.admite([], FICHA_T, gt(26, 9), gt(26, 8), CFG)[0]
    assert shorts.admite([], FICHA_A, gt(26, 9), gt(26, 8), CFG)[0]


def test_campana_sin_shorts_no_es_campana_de_shorts():
    ficha = {"tipo": "A", "reglas": {"plataformas": ["tiktok", "reels"]}}
    assert not shorts.es_de_campana(ficha)


def test_dos_en_24h_cierran_el_cupo_y_la_ventana_es_movil():
    libro = [envio(17, gt(25, 13)), envio(18, gt(25, 19))]
    assert not shorts.admite(libro, FICHA_T, gt(25, 22), gt(25, 21), CFG)[0]
    assert not shorts.admite(libro, FICHA_A, gt(26, 9), gt(26, 8), CFG)[0]      # campaña tampoco pasa el cupo
    # 24 h exactas no bastan (margen de 10 min); 30 min después la de las 13:00 ya salió de la ventana.
    assert not shorts.admite(libro, FICHA_T, gt(26, 13), gt(26, 12), CFG)[0]
    assert shorts.admite(libro, FICHA_T, gt(26, 13, 30), gt(26, 12), CFG)[0]


def test_rechazados_y_otras_redes_no_cuentan():
    libro = [envio(17, gt(25, 13), estado="rechazado"), envio(18, gt(25, 19), estado="no_creado"),
             {**envio(19, gt(25, 20)), "plataforma": "tiktok"}]
    assert shorts.admite(libro, FICHA_T, gt(25, 22), gt(25, 21), CFG)[0]


def test_post_en_error_en_buffer_no_cuenta():
    libro = [envio(17, gt(25, 13)), envio(18, gt(25, 19))]
    estados = {"p17": "error", "p18": "sent"}
    ok, motivo = shorts.admite(libro, FICHA_T, gt(25, 22), gt(25, 21), CFG, estados.get)
    assert ok and "en error" in motivo


def test_buffer_que_falla_cuenta_como_ocupado():
    libro = [envio(17, gt(25, 13)), envio(18, gt(25, 19))]

    def falla(_):
        raise OSError("sin red")
    ok, motivo = shorts.admite(libro, FICHA_T, gt(25, 22), gt(25, 21), CFG, falla)
    assert not ok and "no respondió" in motivo


def test_futuros_no_se_consultan_y_cuentan():
    llamadas = []
    libro = [envio(20, gt(26, 16)), envio(21, gt(26, 19))]
    ok, _ = shorts.admite(libro, FICHA_T, gt(26, 22), gt(26, 14), CFG, lambda p: llamadas.append(p) or "error")
    assert not ok and llamadas == []


def test_tope_de_consultas():
    llamadas = []
    libro = [envio(i, gt(25, 13) + timedelta(minutes=i)) for i in range(10)]
    shorts.cuenta(libro, gt(25, 22), gt(25, 23), {**CFG, "max_consultas": 2}, lambda p: llamadas.append(p) or "sent")
    assert len(llamadas) == 2


def test_config_real(tmp_path):
    cfg = shorts.config(None)
    assert cfg["max_24h"] == 2 and cfg["desde"] == "13:00"

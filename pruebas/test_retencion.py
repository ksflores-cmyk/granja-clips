"""Prueba de retención (corta 10-14 s contra control): asignación, ajuste de largo, registro e informe. Sin red."""
from __future__ import annotations

import csv
import json
import re
import subprocess
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import ojos, registro, retencion, validar
from fabrica.editar import toma
from fabrica.editar.marca import Marca
from test_n4 import Transporte, abrir_con, candidato, ficha_propia, sobre

CFG = yaml.safe_load((RAIZ_REAL / "config" / "prueba-retencion.yml").read_text(encoding="utf-8"))
HOY = date(2026, 11, 22)
AHORA = datetime(2026, 11, 22, 9, 0, tzinfo=ZoneInfo("America/Guatemala"))


# ---- asignación

def test_config_real_valida():
    assert CFG["activa"] is True and CFG["nombre"] == "corta-10-14" and CFG["min_tomas_por_brazo"] == 10
    assert CFG["brazos"] == {"corta": {"duracion_s": [10, 14], "gancho_max_s": 1.0}, "control": {}}
    assert validar.validar(RAIZ_REAL).errores == []


@pytest.mark.parametrize("n,esperado", [(0, "corta"), (2, "corta"), (6, "corta"), (1, "control"), (5, "control"), ("7", "control")])
def test_brazo_por_paridad(n, esperado):
    assert retencion.brazo(n, CFG, HOY) == esperado


def test_brazo_inactiva_o_antes_de_inicio():
    assert retencion.brazo(2, {**CFG, "activa": False}, HOY) is None
    assert retencion.brazo(2, CFG, date(2026, 11, 21)) is None
    assert retencion.brazo(2, CFG, date(2026, 11, 22)) == "corta"
    assert retencion.brazo(2, None, HOY) is None
    assert retencion.brazo("x", CFG, HOY) is None


def test_validar_rechaza_config_mala(repo, editar_yaml):
    editar_yaml(repo, "config/prueba-retencion.yml", lambda d: d["brazos"]["corta"].update(duracion_s=[14, 10]))
    assert any("duracion_s" in e for e in validar.validar(repo).errores)


# ---- ajuste de largo

def test_ajuste_recorta_el_final_sin_estirar():
    assert retencion.ajustar_recorte(2.0, 19.0, 30.0, (10, 14)) == (2.0, 16.0)
    assert retencion.ajustar_recorte(2.0, 12.0, 30.0, (10, 14)) == (2.0, 12.0)           # ya cabe: no se toca


def test_ajuste_de_un_recorte_corto_toma_mas_fuente():
    assert retencion.ajustar_recorte(2.0, 9.0, 30.0, (10, 14)) == (2.0, 12.0)
    assert retencion.ajustar_recorte(8.0, 9.0, 11.0, (10, 14)) == (1.0, 11.0)           # sin cola: hacia atrás


def test_fuente_menor_al_minimo_no_se_puede():
    assert retencion.ajustar_recorte(0.0, 8.0, 9.0, (10, 14)) is None


def test_rango_corta_se_cruza_con_la_ficha():
    assert retencion.rango_corta(CFG) == (10, 14)
    assert retencion.rango_corta(CFG, (7, 30)) == (10, 14)
    assert retencion.rango_corta(CFG, (12, 30)) == (12, 14)
    assert retencion.rango_corta(CFG, (15, 30)) is None


def test_la_instruccion_de_gemini_pide_rango_y_gancho_al_inicio():
    marca = Marca.cargar(RAIZ_REAL)
    temp = yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))
    corta = ojos.instruccion(marca, temp, 20.0, recorte=(10, 14), gancho_inicio_s=1.0)
    assert "de 10 a 14 segundos" in corta and "como mucho 1 s antes del momento más fuerte" in corta
    control = ojos.instruccion(marca, temp, 20.0, recorte=(7, 30))
    assert "como mucho" not in control and "{{" not in control and "{{" not in corta


# ---- fila del marcador

def test_fila_marcador_lleva_la_prueba(repo):
    t = toma.Toma(numero=6, gancho="Kai llega", palabra_lapiz="Kai", lapiz="subrayado", escena="EXT. X", credito="Con: @k")
    f = toma.fila_marcador(candidato(), ficha_propia(), t, 12.0, AHORA, retencion.etiqueta(CFG, "corta"))
    assert f["prueba"] == "corta-10-14:corta"
    assert toma.fila_marcador(candidato(), ficha_propia(), t, 12.0, AHORA)["prueba"] == ""
    registro.anotar_clip(repo, candidato(), {**f, "plataforma": "tiktok"})
    with open(repo / "marcador.csv", newline="", encoding="utf-8") as fh:
        ultima = list(csv.DictReader(fh))[-1]
    assert ultima["prueba"] == "corta-10-14:corta" and ultima["visto_medio_s"] == ""


def test_marcador_tiene_visto_medio_s_al_final():
    assert registro.CABECERA_MARCADOR[-1] == "visto_medio_s"
    assert validar.validar(RAIZ_REAL).errores == []


# ---- hacer_toma de punta a punta (render real: lentas)

def _video(ruta, seg):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size=1280x720:rate=30:duration={seg}", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seg}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(ruta)], check=True)
    return ruta


def _contador(repo, n):
    p = repo / "marca.yml"
    p.write_text(re.sub(r"(?m)^(\s+contador:\s*)\d+", rf"\g<1>{n}", p.read_text(encoding="utf-8")), encoding="utf-8")


def _respuesta(inicio, fin):
    d = json.loads((RAIZ_REAL / "pruebas" / "datos" / "gemini" / "valida.json").read_text(encoding="utf-8"))
    d["recorte"] = {"inicio": inicio, "fin": fin}
    d["lineas"] = [ln for ln in d["lineas"] if ln["t1"] <= fin]
    return Transporte((200, sobre(json.dumps(d))))


def _correr(repo, tmp_path, video, tr):
    return toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, salida=tmp_path / "s", abrir=abrir_con(video),
                           transporte=tr, entorno={"GEMINI_API_KEY": "x"}, espera=lambda s: None)


@pytest.mark.slow
def test_toma_par_se_ajusta_a_14_y_anota_el_brazo(repo, tmp_path):
    _contador(repo, 6)
    tr = _respuesta(1.0, 18.0)                       # Gemini devuelve 17 s
    r = _correr(repo, tmp_path, _video(tmp_path / "v20.mp4", 20), tr)
    assert r.ok, r.motivos
    assert r.fila["prueba"] == "corta-10-14:corta" and r.plan["prueba"]["brazo"] == "corta"
    assert r.plan["duracion"] == pytest.approx(14.0) and "ajustado" in r.plan["prueba"]["nota"]
    assert "como mucho 1 s antes del momento más fuerte" in json.dumps(json.loads(tr.llamadas[0][2]), ensure_ascii=False)


@pytest.mark.slow
def test_toma_impar_es_control_y_no_se_toca(repo, tmp_path):
    _contador(repo, 7)
    r = _correr(repo, tmp_path, _video(tmp_path / "v20.mp4", 20), _respuesta(1.0, 18.0))
    assert r.ok, r.motivos
    assert r.fila["prueba"] == "corta-10-14:control" and r.plan["duracion"] == pytest.approx(17.0)


@pytest.mark.slow
def test_fuente_corta_pasa_a_control_y_se_anota(repo, tmp_path):
    _contador(repo, 6)
    r = _correr(repo, tmp_path, _video(tmp_path / "v8.mp4", 8), _respuesta(0.0, 7.5))
    assert r.ok, r.motivos
    assert r.fila["prueba"] == "corta-10-14:control" and "la fuente dura" in r.plan["prueba"]["nota"]


def test_antes_del_inicio_la_fila_queda_sin_prueba(repo, tmp_path):
    t = toma.Toma(numero=6, gancho="g", palabra_lapiz="g", lapiz="círculo", escena="e", credito="c")
    assert toma.fila_marcador(candidato(), ficha_propia(), t, 12.0, AHORA)["prueba"] == ""
    assert retencion.brazo(6, CFG, date(2026, 11, 18)) is None


# ---- informe

def _fila(red, b, dur, visto, v24, n):
    return {"toma": n, "plataforma": red, "prueba": f"corta-10-14:{b}", "duracion_s": dur,
            "visto_medio_s": visto, "vistas_24h": v24}


def test_informe_con_datos_falsos(repo, capsys):
    filas = ([_fila("tiktok", "corta", 12, 7.2, 110, i) for i in range(10)]          # 60 % visto
             + [_fila("tiktok", "control", 18, 5.4, 100, i) for i in range(10)]      # 30 % visto
             + [_fila("reels", "corta", 12, 6, 90, i) for i in range(3)]
             + [_fila("reels", "control", 18, 7, 100, i) for i in range(10)]
             + [{"toma": 1, "plataforma": "shorts", "prueba": "", "duracion_s": 11}])   # sin prueba: no cuenta
    with open(repo / "marcador.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, registro.CABECERA_MARCADOR, lineterminator="\n")
        for x in filas:
            w.writerow(x)
    r = retencion.resumen(filas, CFG)
    assert r["tiktok"]["corta"]["pct_visto"] == pytest.approx(60.0)
    assert r["tiktok"]["control"]["pct_visto"] == pytest.approx(30.0)
    assert retencion.veredicto(r["tiktok"]) == "corta"
    assert r["reels"]["corta"]["faltan"] == 7 and retencion.veredicto(r["reels"]) == "seguir"
    assert "shorts" not in r
    texto = retencion.informe(repo)
    assert "tiktok  corta" in texto and "faltan 7" in texto and "veredicto: corta" in texto
    assert retencion.main(["--informe"]) == 0


def test_veredicto_exige_20_por_ciento_y_vistas_sin_caer():
    def d(pct_c, v_c, pct_k=50.0, v_k=100.0):
        base = {"n": 10, "n_visto": 10, "faltan": 0}
        return {"corta": {**base, "pct_visto": pct_c, "vistas_24h": v_c}, "control": {**base, "pct_visto": pct_k, "vistas_24h": v_k}}
    assert retencion.veredicto(d(60.0, 95)) == "corta"          # +20 % exacto, vistas -5 %
    assert retencion.veredicto(d(59.9, 100)) == "seguir"        # +19,8 %: empate
    assert retencion.veredicto(d(70.0, 85)) == "seguir"         # gana en % pero pierde 15 % de vistas
    assert retencion.veredicto(d(40.0, 100)) == "control"


def test_la_fila_publicada_conserva_los_avisos_de_la_edicion():
    # Revisión de Codex (unión de #28 y #30): las notas de la fila publicada llevan los avisos de la edición.
    import inspect
    from fabrica import ciclo, publicar
    assert "nota_edicion" in inspect.getsource(ciclo._toma_dict)
    assert "nota_edicion" in inspect.getsource(publicar)


def test_gancho_inicio_va_al_final_de_la_firma():
    import inspect
    from fabrica import ojos
    for fn in (ojos.instruccion, ojos.mirar):
        assert list(inspect.signature(fn).parameters)[-1] == "gancho_inicio_s"

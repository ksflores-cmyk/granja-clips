"""Tarea #24, ahorro de Actions: respaldo de cron, retención, disparadores, robot CR y contador de minutos."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from fabrica import ciclo, fuentes, minutos
from fabrica.pausa import ZONA

RAIZ_REAL = Path(__file__).resolve().parent.parent
FLUJOS = RAIZ_REAL / ".github" / "workflows"


def _wf(nombre):
    return yaml.safe_load((FLUJOS / nombre).read_text(encoding="utf-8"))


def _horas_cron(expr):
    m, h, *resto = expr.split()
    assert resto == ["*", "*", "*"] and m.isdigit()
    return int(m), [int(x) for x in h.split(",")]


def test_cada_hueco_tiene_su_respaldo_en_el_cron():
    cfg = yaml.safe_load((RAIZ_REAL / "config" / "horarios.yml").read_text(encoding="utf-8"))
    cron = _wf("fabrica.yml")[True]["schedule"]
    assert len(cron) == 1
    minuto, horas_utc = _horas_cron(cron[0]["cron"])
    base = datetime(2026, 11, 23, tzinfo=timezone.utc)              # un día cualquiera; GT = UTC-6
    corridas = [base + timedelta(days=d, hours=h, minutes=minuto) for d in (-1, 0, 1) for h in horas_utc]
    for hueco in cfg["huecos"]:
        hh, mm = (int(x) for x in hueco.split(":"))
        objetivo = datetime(2026, 11, 23, hh, mm, tzinfo=ZONA)
        ok = [c for c in corridas
              if c + timedelta(minutes=cfg["margen_min_minutos"]) <= objetivo <= c + timedelta(minutes=cfg["margen_max_minutos"])]
        assert ok, f"el hueco {hueco} GT no tiene respaldo en el cron"
    # y no hay respaldos de más: cada hora del cron sirve a algún hueco
    assert len(horas_utc) == len(cfg["huecos"])


def test_muestras_y_formatos_son_sólo_manuales_y_puerta_no_corre_en_borradores():
    for n in ("muestras.yml", "formatos.yml"):
        assert list(_wf(n)[True]) == ["workflow_dispatch"], n
    p = _wf("puerta.yml")
    assert p[True]["pull_request"]["types"] == ["opened", "synchronize", "reopened", "ready_for_review"]
    assert "github.event.pull_request.draft == false" in p["jobs"]["puerta"]["if"]


def test_todo_artefacto_dura_un_dia():
    n = 0
    for f in FLUJOS.glob("*.yml"):
        for job in (yaml.safe_load(f.read_text(encoding="utf-8"))["jobs"] or {}).values():
            for paso in job.get("steps", []):
                if paso.get("uses", "").startswith("actions/upload-artifact"):
                    n += 1
                    assert paso["with"]["retention-days"] == 1, f.name
    assert n >= 5


def test_ffmpeg_cachea_los_deb_con_apt_de_respaldo():
    a = yaml.safe_load((RAIZ_REAL / ".github/actions/ffmpeg/action.yml").read_text(encoding="utf-8"))
    cache = next(p for p in a["runs"]["steps"] if p.get("uses", "").startswith("actions/cache@"))
    assert "VERSION_ID" in (RAIZ_REAL / ".github/actions/ffmpeg/action.yml").read_text(encoding="utf-8")
    assert "steps.clave.outputs.clave" in cache["with"]["key"]
    assert "apt-get" in (RAIZ_REAL / ".github/actions/ffmpeg/action.yml").read_text(encoding="utf-8")


def test_los_lite_van_antes_del_flash_que_da_503_en_el_respaldo():
    o = yaml.safe_load((RAIZ_REAL / "config" / "ojos.yml").read_text(encoding="utf-8"))
    assert o["modelo"] == "gemini-3-flash-preview"
    assert o["modelos_respaldo"] == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash"]
    assert o["respaldos_por_falla"] >= len(o["modelos_respaldo"])      # el ciclo llega a todos, también a los -lite


# ---------------------------------------------------------------- robot CR: salida cr

def _fuentes(tmp_path, *fichas):
    (tmp_path / "fuentes.yml").write_text(yaml.safe_dump(list(fichas)), encoding="utf-8")


def test_hay_campana_cr_exige_vigente_publicable_y_id(tmp_path):
    base = {"id": "x", "estado": "vigente", "publicable": True, "cr_campaign_id": "abc"}
    _fuentes(tmp_path, base)
    assert fuentes.hay_campana_cr(tmp_path) is True
    for cambio in ({"estado": "pausada"}, {"publicable": False}, {"cr_campaign_id": None}):
        _fuentes(tmp_path, {**base, **cambio}, {"id": "y", "estado": "vigente", "publicable": True})
        assert fuentes.hay_campana_cr(tmp_path) is False, cambio
    _fuentes(tmp_path)
    assert fuentes.hay_campana_cr(tmp_path) is False


# ---------------------------------------------------------------- contador de minutos (transporte falso)

def _job(ini, seg):
    t0 = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc) + timedelta(minutes=ini)
    return {"started_at": t0.isoformat().replace("+00:00", "Z"),
            "completed_at": (t0 + timedelta(seconds=seg)).isoformat().replace("+00:00", "Z")}


class GhFalso:
    def __init__(self):
        self.llamadas = []

    def __call__(self, ruta):
        self.llamadas.append(ruta)
        if "/actions/runs?" in ruta:
            if "page=1" not in ruta:
                return {"workflow_runs": []}
            return {"workflow_runs": [{"id": 1, "name": "fabrica"}, {"id": 2, "name": "fabrica"},
                                      {"id": 3, "name": "puerta"}]}
        m = re.search(r"/runs/(\d+)/jobs", ruta)
        return {"jobs": {"1": [_job(0, 61), _job(2, 30)],      # 2 + 1 min
                         "2": [_job(0, 1)],                     # 1
                         "3": [_job(0, 600), {"started_at": None, "completed_at": None}]}[m.group(1)]}  # 10 + 0


def test_minutos_de_job_redondea_hacia_arriba():
    assert minutos.minutos_de_job(_job(0, 60)) == 1 and minutos.minutos_de_job(_job(0, 61)) == 2
    assert minutos.minutos_de_job(_job(0, 0)) == 1 and minutos.minutos_de_job({"started_at": None}) == 0


def _raiz_con_minutos(tmp_path, datos):
    (tmp_path / "estado").mkdir()
    (tmp_path / "estado" / "actions-minutos.json").write_text(json.dumps(datos), encoding="utf-8")
    return tmp_path


AHORA = datetime(2026, 11, 22, 9, 0, tzinfo=ZONA)


def test_contar_ayer_suma_por_workflow_y_no_rompe_el_formato(tmp_path):
    raiz = _raiz_con_minutos(tmp_path, {"2026-11-21": 34.0, "_en_curso": {"inicio": "x", "run_id": "9"}})
    gh = GhFalso()
    r = minutos.contar_ayer(raiz, AHORA, "o/r", gh)
    assert r == {"dia": "2026-11-21", "total": 14, "mes": 14}
    datos = json.loads((raiz / "estado" / "actions-minutos.json").read_text())
    assert datos["2026-11-21"] == 34.0 and "_en_curso" in datos
    assert datos["total_por_dia"]["2026-11-21"] == {"total": 14, "por_workflow": {"fabrica": 4, "puerta": 10}}
    assert "created=2026-11-21T06:00:00Z..2026-11-22T05:59:59Z" in gh.llamadas[0]
    n = len(gh.llamadas)
    assert minutos.contar_ayer(raiz, AHORA, "o/r", gh) is None and len(gh.llamadas) == n   # una vez al día


def test_el_mes_se_suma_y_pasar_de_2400_escribe_pausa(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_PRIVADO", "true")
    previos = {f"2026-11-{d:02d}": {"total": 120, "por_workflow": {}} for d in range(1, 21)}   # 2400
    raiz = _raiz_con_minutos(tmp_path, {"total_por_dia": previos})
    (tmp_path / "config").mkdir()
    (tmp_path / "estado" / "alerta.json").write_text("{}", encoding="utf-8")
    sal = []
    assert ciclo.contar_minutos(raiz, AHORA, "o/r", GhFalso(), sal.append) == 0
    pausa = (raiz / "PAUSA").read_text(encoding="utf-8")
    assert "motivo: Actions pasó de 2400 minutos en el mes (2414 al 2026-11-21)" in pausa
    assert json.loads((raiz / "estado" / "alerta.json").read_text())["motivo"].startswith("Actions pasó de 2400")


def test_bajo_el_tope_no_pausa_y_un_fallo_de_gh_no_rompe(tmp_path):
    raiz = _raiz_con_minutos(tmp_path, {})
    sal = []
    assert ciclo.contar_minutos(raiz, AHORA, "o/r", GhFalso(), sal.append) == 0 and not (raiz / "PAUSA").exists()

    def roto(ruta):
        raise OSError("sin red")
    raiz2 = _raiz_con_minutos(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, {})
    assert ciclo.contar_minutos(raiz2, AHORA, "o/r", roto, sal.append) == 0
    assert "no se pudo (OSError)" in sal[-1]


def test_repo_publico_no_pausa_aunque_pase_el_tope(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_PRIVADO", "false")
    previos = {f"2026-11-{d:02d}": {"total": 120, "por_workflow": {}} for d in range(1, 21)}
    raiz = _raiz_con_minutos(tmp_path, {"total_por_dia": previos})
    (tmp_path / "config").mkdir()
    sal = []
    assert ciclo.contar_minutos(raiz, AHORA, "o/r", GhFalso(), sal.append) == 0
    assert not (raiz / "PAUSA").exists() and any("repo es público" in x for x in sal)

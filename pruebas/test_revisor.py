"""Revisor de la auto-fusión (decisión de Kevin «Auto-fusión», 25 nov 2026)."""
import io
import json

from fabrica import revisor


def a(nombre, mas=1, menos=0):
    return {"filename": nombre, "additions": mas, "deletions": menos}


def test_toca_workflows_o_pausa_va_a_kevin():
    assert revisor.reglas_fijas([a(".github/workflows/fabrica.yml")], "")
    assert revisor.reglas_fijas([a("PAUSA")], "")
    assert revisor.reglas_fijas([a("config/divulgacion.yml")], "")
    assert not revisor.reglas_fijas([a("fabrica/shorts.py"), a("pruebas/test_shorts.py")], "+x = 1")


def test_clave_en_el_diff_va_a_kevin():
    assert revisor.reglas_fijas([a("fabrica/x.py")], "+k = 'AIza" + "B" * 35 + "'")
    assert revisor.reglas_fijas([a("fabrica/x.py")], "+-----BEGIN RSA PRIVATE KEY-----")


def test_borrar_pruebas_sin_anadir_va_a_kevin():
    assert revisor.reglas_fijas([a("pruebas/test_x.py", 0, 40)], "")
    assert not revisor.reglas_fijas([a("pruebas/test_x.py", 5, 40)], "")


def test_leer_veredicto():
    assert revisor.leer_veredicto('{"veredicto": "aprobado", "motivos": []}')["veredicto"] == "aprobado"
    assert revisor.leer_veredicto('texto {"veredicto": "rechazado", "motivos": ["x"]} fin')["motivos"] == ["x"]
    assert revisor.leer_veredicto('{"veredicto": "quizas"}') is None
    assert revisor.leer_veredicto("nada") is None


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_preguntar_prueba_modelos_hasta_un_veredicto():
    llamadas = []

    def abrir(req, timeout=0):
        llamadas.append(req.full_url)
        if len(llamadas) == 1:
            raise OSError("503")
        texto = json.dumps({"veredicto": "aprobado", "motivos": []})
        return Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": texto}]}}]}).encode())
    v, notas = revisor.preguntar("+x", "k", ["m1", "m2"], abrir)
    assert v["veredicto"] == "aprobado" and v["modelo"] == "m2" and notas == ["m1: OSError"]


def test_sin_veredicto_no_aprueba(tmp_path, monkeypatch):
    (tmp_path / "f.json").write_text(json.dumps([a("fabrica/x.py")]))
    (tmp_path / "d.diff").write_text("+x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cod = revisor.main(["--archivos", str(tmp_path / "f.json"), "--diff", str(tmp_path / "d.diff"),
                        "--salida", str(tmp_path / "v.json")])
    assert cod == 1 and json.loads((tmp_path / "v.json").read_text())["veredicto"] == "sin_revisor"


def test_workflow_corre_desde_main_y_no_toca_el_codigo_de_la_pr():
    import yaml
    from pathlib import Path
    wf = yaml.safe_load((Path(__file__).resolve().parent.parent / ".github/workflows/auto-fusion.yml").read_text())
    job = wf["jobs"]["fusionar"]
    assert "workflow_run" in wf[True] and "claude/" in job["if"] and "head_repository.full_name" in job["if"]
    co = next(p for p in job["steps"] if str(p.get("uses", "")).startswith("actions/checkout"))
    assert "default_branch" in co["with"]["ref"]
    fusion = next(p for p in job["steps"] if p.get("name") == "Fusionar")
    assert "aprobado == 'si'" in fusion["if"] and "--match-head-commit" in fusion["run"]

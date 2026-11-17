"""Fixtures: cada prueba trabaja sobre una copia temporal del repo, nunca sobre el real."""
import shutil
from pathlib import Path

import pytest
import yaml

RAIZ_REAL = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path):
    destino = tmp_path / "repo"
    shutil.copytree(
        RAIZ_REAL, destino,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv"),
    )
    return destino


@pytest.fixture
def editar_yaml():
    """Carga un YAML, aplica una función y lo vuelve a escribir."""
    def _editar(repo, rel, fn):
        ruta = repo / rel
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        resultado = fn(datos)
        ruta.write_text(yaml.safe_dump(resultado if resultado is not None else datos,
                                       allow_unicode=True), encoding="utf-8")
    return _editar

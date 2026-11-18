"""Fixtures: cada prueba trabaja sobre una copia temporal del repo, nunca sobre el real."""
import shutil
from pathlib import Path

import pytest
import yaml

RAIZ_REAL = Path(__file__).resolve().parent.parent


PAUSA_DE_ARRANQUE = (
    "motivo: arranque: no publicar antes del 18 nov 11:00 GT\n"
    "desde: 2026-11-17T16:30:00-06:00\n"
    "hasta: 2026-11-18T11:00:00-06:00\n"
    "autor: jefe de Clips\n"
)


MODO_DE_ARRANQUE = "modo: ensayo\nplataformas_activas: []\n"


def _estado_de_arranque(destino):
    """Deshace lo que la fábrica en vivo escribe en el repo real (marcador, vistos, contador, informativas
    usadas), para que las pruebas no dependan de lo que ya se publicó."""
    import re
    shutil.rmtree(destino / "estado", ignore_errors=True)
    for nombre in ("marcador.csv", "envios-pendientes.csv"):
        ruta = destino / nombre
        if ruta.exists():
            ruta.write_text(ruta.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    marca = destino / "marca.yml"
    marca.write_text(re.sub(r"(?m)^(  contador: )\d+", r"\g<1>1", marca.read_text(encoding="utf-8")),
                     encoding="utf-8")
    info = destino / "informativas.yml"
    if info.exists():
        info.write_text(re.sub(r"(?m)^(\s+usada: )true", r"\g<1>false", info.read_text(encoding="utf-8")),
                        encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    destino = tmp_path / "repo"
    shutil.copytree(
        RAIZ_REAL, destino,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv", "PAUSA"),
    )
    # Las pruebas parten del estado de arranque (con PAUSA), sea cual sea el estado real del repo.
    (destino / "PAUSA").write_text(PAUSA_DE_ARRANQUE, encoding="utf-8")
    (destino / "config" / "modo.yml").write_text(MODO_DE_ARRANQUE, encoding="utf-8")
    _estado_de_arranque(destino)
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

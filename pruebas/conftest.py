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


LICENCIA_FALSA = ("# Licencia\n\n- Fuente: https://opengameart.org/content/falsa\n- Autor: Prueba\n"
                  "- Licencia: CC0\n- Enlace: https://creativecommons.org/publicdomain/zero/1.0/\n")


@pytest.fixture(scope="session")
def raiz_musica(tmp_path_factory):
    """(raíz, carpeta de audio): copia ligera del repo (marca.yml y marca/) con 3 pistas CC0 falsas ya verificadas.

    Los audios son tonos `sine` de ffmpeg en una carpeta temporal; el repo real no se toca ni recibe audio. Sirve a
    las pruebas que renderizan una pieza sin audio propio y deben pasar la puerta de estilo completa.
    """
    import hashlib
    import subprocess
    base = tmp_path_factory.mktemp("raiz-musica")
    raiz, audio = base / "repo", base / "audio"
    audio.mkdir()
    raiz.mkdir()
    shutil.copy(RAIZ_REAL / "marca.yml", raiz / "marca.yml")
    shutil.copy(RAIZ_REAL / "reglas-kevin.yml", raiz / "reglas-kevin.yml")   # la puerta lee las reglas de Kevin
    shutil.copytree(RAIZ_REAL / "marca", raiz / "marca", ignore=shutil.ignore_patterns("musica"))
    pistas = []
    for i in range(3):
        ruta = audio / f"tono-{i}.wav"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 100 * i}:duration=14", str(ruta)], check=True)
        pistas.append({"id": f"tono-{i}", "titulo": f"Tono {i}", "autor": "Prueba",
                       "pagina": f"https://opengameart.org/content/tono-{i}", "archivo": "x.ogg", "licencia": "CC0",
                       "estilo": "tono", "inicio_s": 0, "asset": ruta.name,
                       "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest(), "estado": "verificada"})
        carpeta = raiz / "marca" / "musica" / f"tono-{i}"
        carpeta.mkdir(parents=True)
        (carpeta / "LICENCIA.md").write_text(LICENCIA_FALSA, encoding="utf-8")
    (raiz / "marca" / "musica" / "pistas.yml").write_text(
        yaml.safe_dump({"licencias_permitidas": ["CC0"], "release": "musica", "pistas": pistas},
                       allow_unicode=True), encoding="utf-8")
    return raiz, audio


@pytest.fixture
def repo_con_musica(repo, raiz_musica, monkeypatch):
    """La copia temporal del repo con las 3 pistas falsas verificadas, y GRANJA_MUSICA_DIR apuntando a sus tonos."""
    raiz, audio = raiz_musica
    shutil.rmtree(repo / "marca" / "musica", ignore_errors=True)
    shutil.copytree(raiz / "marca" / "musica", repo / "marca" / "musica")
    monkeypatch.setenv("GRANJA_MUSICA_DIR", str(audio))
    return repo

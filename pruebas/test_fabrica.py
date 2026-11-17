import csv

import pytest

from fabrica import config, pausa, registro


def test_cargar_todo_devuelve_las_seis_secciones(repo):
    datos = config.cargar_todo(repo)
    assert set(datos) == {"temporada", "fuentes", "marca", "horarios", "modo", "canales"}
    assert datos["temporada"]["id"] == "gta6"


def test_raiz_por_defecto_es_el_repo():
    assert (config.raiz_repo() / "marca.yml").exists()


def test_pausa_presente_y_ausente(repo):
    assert pausa.esta_pausada(repo)[0] is True
    (repo / "PAUSA").unlink()
    assert pausa.esta_pausada(repo) == (False, "")


def test_pausada_aunque_hasta_haya_pasado(repo):
    (repo / "PAUSA").write_text("motivo: prueba\nhasta: 2000-01-01T00:00:00-06:00\n", encoding="utf-8")
    assert pausa.esta_pausada(repo) == (True, "prueba")


def test_crear_pausa(repo):
    (repo / "PAUSA").unlink()
    pausa.crear_pausa(repo, "retiro\nde un clip", "director")
    d = pausa.leer_pausa(repo)
    assert d["motivo"] == "retiro de un clip"
    assert d["autor"] == "director"
    assert "desde" in d
    assert pausa.esta_pausada(repo)[0]


def test_crear_pausa_sin_motivo_falla(repo):
    with pytest.raises(ValueError):
        pausa.crear_pausa(repo, "  ", "x")


def test_anotar_fila_deja_metricas_vacias(repo):
    registro.anotar_fila(repo, {"id_clip": "c1", "toma": 1, "plataforma": "shorts"})
    with open(repo / "marcador.csv", newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    assert len(filas) == 1
    assert filas[0]["id_clip"] == "c1"
    for m in ("vistas_12h", "vistas_24h", "vistas_72h", "likes", "pago_usd"):
        assert filas[0][m] == ""


def test_anotar_fila_rechaza_claves_raras(repo):
    with pytest.raises(ValueError):
        registro.anotar_fila(repo, {"id_clip": "c1", "inventada": 5})
    assert (repo / "marcador.csv").read_text(encoding="utf-8").count("\n") == 1


def test_anotar_registro_agrega_linea_con_hora(repo):
    linea = registro.anotar_registro(repo, "TOMA 001 publicada")
    assert " GT · TOMA 001 publicada" in linea
    assert (repo / "registro.md").read_text(encoding="utf-8").rstrip().endswith("TOMA 001 publicada")


def test_avanzar_toma_conserva_comentarios(repo):
    antes = (repo / "marca.yml").read_text(encoding="utf-8")
    assert registro.contador_toma(repo) == 1
    assert registro.avanzar_toma(repo) == 2
    assert registro.avanzar_toma(repo) == 3
    despues = (repo / "marca.yml").read_text(encoding="utf-8")
    assert registro.contador_toma(repo) == 3
    assert "# sube en 1 con cada clip publicado" in despues
    assert "# Sistema visual [Guion]" in despues
    ant, des = antes.splitlines(), despues.splitlines()
    assert len(ant) == len(des)
    assert [i for i, (a, b) in enumerate(zip(ant, des)) if a != b] == [
        i for i, l in enumerate(ant) if "contador:" in l]

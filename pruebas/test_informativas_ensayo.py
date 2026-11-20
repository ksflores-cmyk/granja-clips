"""Credito y acotación por entrada, su validación y el ensayo de informativas (sin marcar usada ni tocar el repo)."""
import hashlib
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from fabrica import informativa, validar
from fabrica.editar import muestras
from fabrica.editar.toma import Resultado
from fabrica.registro import ZONA

from conftest import RAIZ_REAL

AHORA = datetime(2026, 11, 20, 9, 0, tzinfo=ZONA)


def entrada(**kw):
    e = {"id": "x-uno", "gancho": "GTA 6 bate récord", "palabra_lapiz": "RÉCORD", "titular": "t",
         "datos": ["uno", "dos"], "fuente_url": "https://ejemplo.com/a", "verificado_el": "2026-11-20",
         "vigente_desde": "2026-11-20T06:00:00-06:00", "vigente_hasta": "2026-11-21T23:59:00-06:00",
         "usada": False}
    e.update(kw)
    return e


def errores(repo, entradas):
    inf = validar.Informe()
    validar.validar_informativas({"informativas": entradas}, validar._cargar(repo, "marca.yml", inf), inf)
    return [x for x in inf.errores if "informativas.yml" in x]


# ---- credito y acotación

def test_acotacion_por_entrada_y_por_omision():
    assert informativa._lineas(entrada(acotacion="según Streams Charts"))[0].acotacion == "según Streams Charts"
    assert informativa._lineas(entrada())[0].acotacion == "según el soporte de Rockstar"
    assert informativa._lineas(entrada())[1].acotacion in (None, "")


def test_armar_usa_credito_de_la_entrada_o_el_de_omision(repo, monkeypatch, tmp_path):
    vistos = []

    def componer_falso(marca, toma, lineas, mp4, **kw):
        vistos.append(toma.credito)
        raise ValueError("corta aquí")

    monkeypatch.setattr(informativa, "componer", componer_falso)
    monkeypatch.setattr(informativa.mod_musica, "elegir", lambda r, n: {"id": "p"})
    informativa.armar(repo, entrada(credito="Fuente: Otra"), AHORA, tmp_path)
    informativa.armar(repo, entrada(), AHORA, tmp_path)
    assert vistos == ["Fuente: Otra", informativa.CREDITO]


def test_muestra_con_musica_usa_el_credito_de_la_entrada(repo, monkeypatch, tmp_path):
    vistos = []
    monkeypatch.setattr(muestras.mod_musica, "elegir", lambda r, n: {"id": "p", "autor": "a", "pagina": "u"})
    monkeypatch.setattr(muestras, "_entrada_de_prueba",
                        lambda raiz, hoy: (entrada(credito="Fuente: Otra"), "informativas.yml:x-uno"))

    def componer_falso(marca, t, *a, **kw):
        vistos.append(t.credito)
        raise ValueError("corta aquí")

    monkeypatch.setattr(muestras, "componer", componer_falso)
    with pytest.raises(ValueError):
        muestras._muestra_con_musica(tmp_path, muestras.Marca.cargar(repo), None, None, None)
    assert vistos == ["Fuente: Otra"]


# ---- validación

def test_informativas_yml_real_valida_sin_errores():
    inf = validar.validar(RAIZ_REAL)
    assert [x for x in inf.errores if "informativas.yml" in x] == []
    claves = {k for e in informativa.cargar(RAIZ_REAL) for k in e}
    assert {"credito", "acotacion"} <= claves


def test_claves_nuevas_buenas(repo):
    assert errores(repo, [entrada(credito="Fuente: Streams Charts", acotacion="según Streams Charts",
                                  escena="INT. TWITCH — NOCHE")]) == []


@pytest.mark.parametrize("clave,valor,texto", [
    ("credito", "Streams Charts", "empezar con"),
    ("credito", "Fuente: " + "x" * 40, "pasa de 40"),
    ("credito", "", "no vacío"),
    ("credito", 5, "no vacío"),
    ("acotacion", "   ", "no vacío"),
    ("acotacion", "x" * 41, "pasa de 40"),
    ("escena", "", "no vacío"),
    ("escena", "x" * 41, "pasa de 40"),
    ("escena", ["a"], "no vacío"),
])
def test_claves_nuevas_malas(repo, clave, valor, texto):
    es = errores(repo, [entrada(**{clave: valor})])
    assert any(clave in x and texto in x for x in es), es


# ---- ensayo

def huella(raiz):
    return {str(p.relative_to(raiz)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(raiz).rglob("*")) if p.is_file() and ".git" not in p.parts}


@pytest.fixture
def banco(repo):
    lista = [entrada(id="a-vigente"), entrada(id="b-usada", usada=True), entrada(id="c-vigente"),
             entrada(id="d-vencida", vigente_desde="2026-11-01T00:00:00-06:00",
                     vigente_hasta="2026-11-02T00:00:00-06:00")]
    (repo / "informativas.yml").write_text(yaml.safe_dump({"informativas": lista}, allow_unicode=True),
                                           encoding="utf-8")
    return repo


def falso(fallan=()):
    llamadas = []

    def armar(raiz, e, ahora, salida):
        llamadas.append(e["id"])
        if e["id"] in fallan:
            return Resultado(False, ["puerta de estilo: contraste"], None, {}, None, None, [("[x] contraste", False, "bajo")])
        return Resultado(True, [], None, {}, None, None, [("[x] ritmo", True, "")])
    return armar, llamadas


def test_ensayo_verde_no_marca_usada_ni_escribe_en_el_repo(banco, monkeypatch, tmp_path):
    armar, llamadas = falso()
    monkeypatch.setattr(informativa, "armar", armar)
    antes = huella(banco)
    cod = informativa.ensayo(banco, tmp_path / "s", AHORA, imprimir=lambda *_: None)
    assert cod == 0 and llamadas == ["a-vigente", "c-vigente"]
    assert huella(banco) == antes
    inf = (tmp_path / "s" / "informe.md").read_text(encoding="utf-8")
    assert "## a-vigente: EN VERDE" in inf and "## c-vigente: EN VERDE" in inf and "SÍ: [x] ritmo" in inf


def test_ensayo_sale_1_si_una_falla(banco, monkeypatch, tmp_path):
    armar, _ = falso(fallan={"c-vigente"})
    monkeypatch.setattr(informativa, "armar", armar)
    cod = informativa.ensayo(banco, tmp_path / "s", AHORA, imprimir=lambda *_: None)
    inf = (tmp_path / "s" / "informe.md").read_text(encoding="utf-8")
    assert cod == 1 and "## c-vigente: EN ROJO" in inf and "NO: puerta de estilo: contraste" in inf
    assert (banco / "informativas.yml").read_text(encoding="utf-8").count("usada: true") == 1   # sólo la que ya lo era


def test_ensayo_sin_entradas_sale_1(banco, monkeypatch, tmp_path):
    armar, llamadas = falso()
    monkeypatch.setattr(informativa, "armar", armar)
    cod = informativa.ensayo(banco, tmp_path / "s", datetime(2026, 12, 25, tzinfo=ZONA), imprimir=lambda *_: None)
    assert cod == 1 and llamadas == [] and (tmp_path / "s" / "informe.md").exists()


def test_ensayo_con_id_respeta_no_vigentes_y_salta_usadas(banco, monkeypatch, tmp_path):
    armar, llamadas = falso()
    monkeypatch.setattr(informativa, "armar", armar)
    cod = informativa.ensayo(banco, tmp_path / "s", AHORA, ids=["d-vencida", "b-usada"], imprimir=lambda *_: None)
    assert llamadas == ["d-vencida"] and cod == 1        # la usada se reporta en rojo, no se compone


def test_cli_ensayo_por_main(banco, monkeypatch, tmp_path):
    armar, llamadas = falso()
    monkeypatch.setattr(informativa, "armar", armar)
    monkeypatch.setattr(informativa, "raiz_repo", lambda r=None: banco)
    cod = informativa.main(["--ensayo", "--salida", str(tmp_path / "s"), "--ahora", "2026-11-20T09:00:00-06:00",
                            "--id", "c-vigente"])
    assert cod == 0 and llamadas == ["c-vigente"]


def test_armar_real_no_escribe_en_el_repo(repo_con_musica, tmp_path):
    antes = huella(repo_con_musica)
    r = informativa.armar(repo_con_musica, entrada(), AHORA, tmp_path / "s")
    assert isinstance(r, Resultado)
    assert huella(repo_con_musica) == antes


# --- Ronda 2: la fuente también en el texto publicado, y ensayo de entradas futuras ---

def test_toma_dict_lleva_la_fuente_de_la_entrada():
    from fabrica import informativa as inf
    e = {"gancho": "Récord de un juego en Twitch", "titular": "t", "datos": ["a.", "b."],
         "credito": "Fuente: Streams Charts (preliminar)"}
    assert inf.toma_dict(e, 7)["fuente"] == "Fuente: Streams Charts (preliminar)"
    sin = {k: v for k, v in e.items() if k != "credito"}
    assert inf.toma_dict(sin, 7)["fuente"] == inf.CREDITO


def test_texto_de_informativa_cita_la_fuente_en_las_tres_redes():
    from fabrica import informativa as inf
    from fabrica import publicar
    e = {"gancho": "GTA 6 saca 99 en Metacritic", "titular": "99 en Metacritic, empata el récord histórico",
         "datos": ["99 de 100 en PS5 (147 reseñas, al 18 nov).", "118 de esas reseñas son notas perfectas."],
         "credito": "Fuente: Metacritic vía TechPowerUp"}
    toma = inf.toma_dict(e, 12)
    for red in ("shorts", "tiktok", "reels"):
        texto = publicar.textos(toma, {}, red)["texto"]
        assert "Fuente: Metacritic vía TechPowerUp" in texto
        assert texto.index("Fuente:") < texto.index("🎥")


def test_ensayo_incluye_entradas_futuras_y_salta_vencidas(tmp_path, monkeypatch):
    from datetime import datetime
    from fabrica import informativa as inf
    from fabrica.editar.toma import Resultado
    ahora = datetime.fromisoformat("2026-11-20T09:00:00-06:00")
    base = {"gancho": "g", "palabra_lapiz": "G", "titular": "t", "datos": ["a.", "b."], "usada": False}
    entradas = [
        {**base, "id": "futura", "vigente_desde": "2026-11-25T00:00:00-06:00", "vigente_hasta": "2026-11-26T00:00:00-06:00"},
        {**base, "id": "vencida", "vigente_desde": "2026-11-10T00:00:00-06:00", "vigente_hasta": "2026-11-11T00:00:00-06:00"},
    ]
    monkeypatch.setattr(inf, "cargar", lambda raiz=None: entradas)
    vistos = []
    monkeypatch.setattr(inf, "armar", lambda raiz, e, ah, sal: vistos.append(e["id"]) or Resultado(True, []))
    assert inf.ensayo(tmp_path, tmp_path / "s", ahora, imprimir=lambda *_: None) == 0
    assert vistos == ["futura"]


def test_ensayo_sin_entradas_con_vacio_ok_sale_cero(tmp_path, monkeypatch):
    from datetime import datetime
    from fabrica import informativa as inf
    monkeypatch.setattr(inf, "cargar", lambda raiz=None: [])
    ahora = datetime.fromisoformat("2026-11-20T09:00:00-06:00")
    assert inf.ensayo(tmp_path, tmp_path / "a", ahora, imprimir=lambda *_: None) == 1
    assert inf.ensayo(tmp_path, tmp_path / "b", ahora, imprimir=lambda *_: None, vacio_ok=True) == 0

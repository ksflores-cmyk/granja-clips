import datetime
import subprocess
import sys

import pytest

from fabrica import validar
from fabrica.registro import CABECERA_ENVIOS, CABECERA_MARCADOR

from conftest import RAIZ_REAL


def errores(repo):
    return validar.validar(repo).errores


def hay(repo, fragmento):
    return any(fragmento in e for e in errores(repo))


def test_repo_valido_pasa(repo):
    inf = validar.validar(repo)
    assert inf.errores == []


# ---- fuentes.yml ----

def ficha(repo, editar_yaml, **cambios):
    def f(d):
        d[0].update(cambios)
        return d
    editar_yaml(repo, "fuentes.yml", f)


def test_id_con_mayusculas_falla(repo, editar_yaml):
    ficha(repo, editar_yaml, id="Propio_X")
    assert hay(repo, "id debe estar en minúsculas")


def test_id_repetido_falla(repo, editar_yaml):
    editar_yaml(repo, "fuentes.yml", lambda d: d + [dict(d[0])])
    assert hay(repo, "id repetido")


@pytest.mark.parametrize("campo,valor,msg", [
    ("tipo", "Z", "tipo debe ser"),
    ("origen", "tiktok", "origen inválido"),
    ("estado", "activa", "estado inválido"),
    ("uso", "prueba", "uso debe ser"),
    ("publicable", "si", "publicable debe ser booleano"),
])
def test_campos_enumerados_invalidos(repo, editar_yaml, campo, valor, msg):
    ficha(repo, editar_yaml, **{campo: valor})
    assert hay(repo, msg)


@pytest.mark.parametrize("duracion", [[0, 10], [10, 5], [5, 200], [7], ["a", "b"]])
def test_duracion_invalida(repo, editar_yaml, duracion):
    def f(d):
        d[0]["reglas"]["duracion_s"] = duracion
        return d
    editar_yaml(repo, "fuentes.yml", f)
    assert hay(repo, "duracion_s")


def test_plataforma_invalida(repo, editar_yaml):
    def f(d):
        d[0]["reglas"]["plataformas"] = ["shorts", "facebook"]
        return d
    editar_yaml(repo, "fuentes.yml", f)
    assert hay(repo, "reglas.plataformas")


def test_tipo_a_exige_permiso_https_tarifa_y_vence(repo, editar_yaml):
    ficha(repo, editar_yaml, tipo="A", permiso_url="http://x.com", permiso_leido=None)
    es = errores(repo)
    assert any("permiso_url https" in e for e in es)
    assert any("permiso_leido" in e for e in es)
    assert any("tarifa_usd_por_mil" in e for e in es)
    assert any("exige vence" in e for e in es)


def test_tipo_b_exige_permiso_url(repo, editar_yaml):
    ficha(repo, editar_yaml, tipo="B")
    assert hay(repo, "tipo B exige permiso_url https")


def test_tipo_a_completo_pasa(repo, editar_yaml):
    ficha(repo, editar_yaml, tipo="A", permiso_url="https://ejemplo.org/brief",
          tarifa_usd_por_mil=1.0, vence=datetime.date(2099, 1, 1))
    assert errores(repo) == []


def test_tipo_d_solo_informativa_y_sin_campana(repo, editar_yaml):
    ficha(repo, editar_yaml, tipo="D", pilares=["momentos"], campana="x")
    es = errores(repo)
    assert any("sólo con pilar informativa" in e for e in es)
    assert any("campana null" in e for e in es)


def test_muestras_exige_no_publicable(repo, editar_yaml):
    ficha(repo, editar_yaml, uso="muestras", publicable=True)
    assert hay(repo, "uso muestras exige publicable false")


def test_ficha_vencida_da_aviso_no_error(repo, editar_yaml):
    ficha(repo, editar_yaml, vence=datetime.date(2020, 1, 1))
    inf = validar.validar(repo)
    assert inf.errores == []
    assert any("ya pasado" in a for a in inf.avisos)


# ---- marca.yml ----

def marca(repo, editar_yaml, fn):
    editar_yaml(repo, "marca.yml", fn)


def test_color_no_hex_falla(repo, editar_yaml):
    marca(repo, editar_yaml, lambda d: d["colores"].update(papel="crema"))
    assert hay(repo, "colores.papel")


def test_fuente_distinta_falla(repo, editar_yaml):
    marca(repo, editar_yaml, lambda d: d["fuente"].update(familia="Impact"))
    assert hay(repo, "Courier Prime")


def test_contador_invalido_falla(repo, editar_yaml):
    marca(repo, editar_yaml, lambda d: d["toma"].update(contador=0))
    assert hay(repo, "toma.contador")


@pytest.mark.parametrize("seccion,clave,valor,msg", [
    ("movimiento", "transiciones", "fundido", "transiciones"),
    ("movimiento", "zoom_automatico", True, "zoom_automatico"),
    ("sonido", "musica_de_biblioteca", True, "musica_de_biblioteca"),
])
def test_reglas_de_movimiento_y_sonido(repo, editar_yaml, seccion, clave, valor, msg):
    marca(repo, editar_yaml, lambda d: d[seccion].update({clave: valor}))
    assert hay(repo, msg)


def test_encabezado_y_subtitulo_limites(repo, editar_yaml):
    def f(d):
        d["encabezado_max_palabras"] = 7
        d["subtitulo_max_lineas"] = 3
    marca(repo, editar_yaml, f)
    es = errores(repo)
    assert any("encabezado_max_palabras" in e for e in es)
    assert any("subtitulo_max_lineas" in e for e in es)


# ---- horarios, modo, canales ----

def test_zona_invalida(repo, editar_yaml):
    editar_yaml(repo, "config/horarios.yml", lambda d: d.update(zona="Marte/Olimpo"))
    assert hay(repo, "zona inválida")


def test_hora_mal_formada(repo, editar_yaml):
    editar_yaml(repo, "config/horarios.yml", lambda d: d.update(huecos=["25:00", "9am"]))
    assert hay(repo, "hora inválida")


def test_mas_de_8_huecos(repo, editar_yaml):
    editar_yaml(repo, "config/horarios.yml",
                lambda d: d.update(huecos=[f"{h:02d}:00" for h in range(9)]))
    assert hay(repo, "8 como máximo")


def test_modo_y_plataformas_invalidos(repo, editar_yaml):
    editar_yaml(repo, "config/modo.yml", lambda d: d.update(modo="turbo", plataformas_activas=["x"]))
    es = errores(repo)
    assert any("modo debe ser" in e for e in es)
    assert any("plataformas_activas" in e for e in es)


def test_canal_con_servicio_equivocado(repo, editar_yaml):
    editar_yaml(repo, "config/canales.yml", lambda d: d["reels"].update(servicio="youtube"))
    assert hay(repo, "reels.servicio")


# ---- PAUSA, CSV, media, claves ----

def test_pausa_sin_motivo_falla(repo):
    (repo / "PAUSA").write_text("autor: x\n", encoding="utf-8")
    assert hay(repo, "PAUSA")


def test_sin_pausa_pasa(repo):
    (repo / "PAUSA").unlink()
    assert errores(repo) == []


def test_cabecera_marcador_exacta():
    assert len(CABECERA_MARCADOR) == 25
    # Sólo la primera línea: en vivo la fábrica añade filas.
    assert (RAIZ_REAL / "marcador.csv").read_text(encoding="utf-8").splitlines()[0] == ",".join(CABECERA_MARCADOR)
    assert (RAIZ_REAL / "envios-pendientes.csv").read_text(encoding="utf-8").splitlines()[0] \
        == ",".join(CABECERA_ENVIOS)


def test_cabecera_alterada_falla(repo):
    (repo / "marcador.csv").write_text("a,b,c\n", encoding="utf-8")
    (repo / "envios-pendientes.csv").write_text("fecha\n", encoding="utf-8")
    es = errores(repo)
    assert any("marcador.csv" in e for e in es)
    assert any("envios-pendientes.csv" in e for e in es)


@pytest.mark.parametrize("nombre", ["a.mp4", "sub/b.MOV", "c.wav", "d.mp3", "e.webm"])
def test_detecta_video_y_audio(repo, nombre):
    ruta = repo / nombre
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_bytes(b"\x00")
    assert hay(repo, "video o audio")


@pytest.mark.parametrize("clave", [
    "AIza" + "a" * 35,
    "sk-" + "b" * 24,
    "ghp_" + "c" * 36,
    "-----BEGIN " + "RSA " + "PRIVATE KEY-----",
])
def test_detecta_claves(repo, clave):
    (repo / "notas.md").write_text(f"token: {clave}\n", encoding="utf-8")
    assert hay(repo, "clave expuesta")


def test_ejemplo_falso_marcado_en_pruebas_se_ignora(repo):
    clave = "ghp_" + "d" * 36
    (repo / "pruebas" / "ejemplo.txt").write_text(f"{clave} EJEMPLO-FALSO\n", encoding="utf-8")
    assert errores(repo) == []


def test_marca_de_falso_fuera_de_pruebas_no_exime(repo):
    clave = "ghp_" + "d" * 36
    (repo / "notas.md").write_text(f"{clave} EJEMPLO-FALSO\n", encoding="utf-8")
    assert hay(repo, "clave expuesta")


# ---- CLI ----

def correr(repo):
    return subprocess.run([sys.executable, "-m", "fabrica.validar", "--raiz", str(repo)],
                          cwd=RAIZ_REAL, capture_output=True, text=True)


def test_cli_sale_0_en_repo_valido(repo):
    r = correr(repo)
    assert r.returncode == 0, r.stdout
    assert "VERDE" in r.stdout


def test_cli_sale_1_con_errores(repo):
    (repo / "x.mp4").write_bytes(b"0")
    r = correr(repo)
    assert r.returncode == 1
    assert "ERROR" in r.stdout


def test_yaml_roto_es_error(repo):
    (repo / "temporada.yml").write_text("a: [", encoding="utf-8")
    assert hay(repo, "YAML inválido")


# ---- ronda 1 ----

@pytest.mark.parametrize("rel", ["temporada.yml", "marca.yml", "config/horarios.yml",
                                 "config/modo.yml", "config/canales.yml", "fuentes.yml"])
@pytest.mark.parametrize("contenido", ["", "# sólo comentarios\n"])
def test_yaml_vacio_es_error(repo, rel, contenido):
    (repo / rel).write_text(contenido, encoding="utf-8")
    assert any(rel in e and "vacío" in e for e in errores(repo))


def test_fuentes_no_lista_es_error(repo):
    (repo / "fuentes.yml").write_text("a: 1\n", encoding="utf-8")
    assert hay(repo, "debe ser una lista")


def test_fuentes_lista_vacia_es_aviso(repo):
    (repo / "fuentes.yml").write_text("[]\n", encoding="utf-8")
    inf = validar.validar(repo)
    assert inf.errores == []
    assert any("sin fuentes" in a for a in inf.avisos)


@pytest.mark.parametrize("estado", ["vencida", "pausada", "vetada"])
def test_estado_no_vigente_publicable_da_aviso(repo, editar_yaml, estado):
    ficha(repo, editar_yaml, estado=estado)
    inf = validar.validar(repo)
    assert inf.errores == []
    assert any("exigirá estado vigente" in a for a in inf.avisos)


def test_vigente_publicable_sin_aviso(repo):
    assert validar.validar(repo).avisos == []


def test_clave_en_archivo_no_utf8_se_detecta(repo):
    clave = "ghp_" + "e" * 36
    (repo / "latin.txt").write_bytes(b"caf\xe9 " + clave.encode() + b"\n")
    assert hay(repo, "clave expuesta")


def test_binarios_conocidos_se_omiten(repo):
    clave = "ghp_" + "e" * 36
    (repo / "x.png").write_bytes(clave.encode())
    assert errores(repo) == []

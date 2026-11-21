"""Tarea #18 `reglas-kevin`: cada queja de Kevin es una regla en `reglas-kevin.yml` y la puerta de estilo la mide."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import validar
from fabrica.editar import componer, estilo, lienzo, reglas_kevin
from fabrica.editar.marca import Marca
from test_pantalla_completa import LINEAS, TOMA, fuente, lavfi

hay_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
_sin_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")


def necesita_ffmpeg(f):
    """Renderiza con ffmpeg: va marcada como lenta."""
    return pytest.mark.slow(_sin_ffmpeg(f))

NOMBRES = {i: f"[regla-kevin] {i}: " for i in ("muy-simple", "sin-musica", "ocupa-todo")}


def estado(puntos):
    """{id: (ok, detalle)} de los puntos de reglas de Kevin."""
    return {n.split(":")[0].removeprefix("[regla-kevin] "): (ok, d) for n, ok, d in puntos}


def regla(**kw):
    base = {"id": "nueva-queja", "fecha": "2026-11-20", "queja": "no me gusta", "regla": "Algo debe cumplirse",
            "revision": None, "estado": "vigente"}
    return {**base, **kw}


def escribir(raiz: Path, reglas, version=1):
    (raiz / "reglas-kevin.yml").write_text(yaml.safe_dump({"version": version, "reglas": reglas},
                                                          allow_unicode=True), encoding="utf-8")


@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


def pista_de(raiz_musica, i=1):
    raiz, audio = raiz_musica
    pistas = yaml.safe_load((raiz / "marca/musica/pistas.yml").read_text(encoding="utf-8"))["pistas"]
    return {**pistas[i], "ruta": str(audio / pistas[i]["asset"])}


# ----------------------------------------------------------------------------------- el archivo

def test_el_archivo_del_repo_trae_las_3_reglas_y_el_procedimiento():
    texto = (RAIZ_REAL / "reglas-kevin.yml").read_text(encoding="utf-8")
    cabecera = texto.split("version:")[0]
    assert "añadir una queja nueva" in cabecera and "  1." in cabecera and "  6." in cabecera
    reglas = reglas_kevin.cargar(RAIZ_REAL)
    assert [r["id"] for r in reglas] == ["muy-simple", "sin-musica", "ocupa-todo"]
    assert [r["revision"] for r in reglas] == ["con_movimiento_y_audio", "musica_cc0_sin_audio_propio", "sin_franjas"]
    assert set(reglas_kevin.REVISIONES) == {r["revision"] for r in reglas}
    assert all(r["estado"] == "vigente" and r["queja"] for r in reglas)
    assert reglas_kevin.notas_para_criticos(RAIZ_REAL) == ""


@pytest.mark.parametrize("cambio, motivo", [
    ({"id": "Mala_Id"}, "id mal formado"),
    ({"fecha": "2026-13-45"}, "fecha inválida"),
    ({"estado": "dormida"}, "estado desconocido"),
    ({"revision": "no_existe"}, "revision desconocida"),
    ({"queja": "  "}, "queja está vacío"),
    ({"regla": ""}, "regla está vacío"),
])
def test_cargar_rechaza_con_el_motivo_exacto(repo, cambio, motivo):
    escribir(repo, [regla(**cambio)])
    with pytest.raises(ValueError, match=motivo):
        reglas_kevin.cargar(repo)


def test_cargar_rechaza_faltas_de_estructura(repo):
    (repo / "reglas-kevin.yml").unlink()
    with pytest.raises(ValueError, match="falta el archivo"):
        reglas_kevin.cargar(repo)
    (repo / "reglas-kevin.yml").write_text("version: 1\nreglas: [\n  - {id: x", encoding="utf-8")
    with pytest.raises(ValueError, match="no es YAML válido"):
        reglas_kevin.cargar(repo)
    escribir(repo, [regla(), regla()])
    with pytest.raises(ValueError, match="id repetido"):
        reglas_kevin.cargar(repo)
    r = regla()
    del r["queja"]
    escribir(repo, [r])
    with pytest.raises(ValueError, match="falta el campo queja"):
        reglas_kevin.cargar(repo)
    escribir(repo, [regla()], version=2)
    with pytest.raises(ValueError, match="version debe ser 1"):
        reglas_kevin.cargar(repo)


def test_regla_nueva_sin_revision_es_nota_y_no_pone_punto(repo):
    escribir(repo, [regla(id="texto-chico", queja="el texto es chico", regla="Texto más grande")])
    assert reglas_kevin.puntos(Path("no-existe.mp4"), {}, Marca.cargar(repo)) == []
    assert reglas_kevin.notas_para_criticos(repo) == "texto-chico: Texto más grande (queja: «el texto es chico», 2026-11-20)"


def test_regla_retirada_no_pone_punto_ni_nota(repo):
    escribir(repo, [regla(estado="retirada"), regla(id="otra", estado="retirada", revision="sin_franjas")])
    assert reglas_kevin.puntos(Path("no-existe.mp4"), {}, Marca.cargar(repo)) == []
    assert reglas_kevin.notas_para_criticos(repo) == ""


def test_la_puerta_falla_cerrada_con_archivo_roto_o_revision_desconocida(repo):
    marca = Marca.cargar(repo)
    escribir(repo, [regla(revision="no_existe")])
    p = reglas_kevin.puntos(Path("x.mp4"), {}, marca)
    assert len(p) == 1 and p[0][0] == "[regla-kevin] reglas-kevin.yml válido" and not p[0][1]
    assert "revision desconocida" in p[0][2]
    (repo / "reglas-kevin.yml").unlink()
    p = reglas_kevin.puntos(Path("x.mp4"), {}, marca)
    assert len(p) == 1 and not p[0][1] and "falta el archivo" in p[0][2]


def test_validar_usa_cargar_y_avisa_de_las_notas(repo):
    assert validar.validar(repo).errores == []
    escribir(repo, [regla(id="texto-chico")])
    inf = validar.validar(repo)
    assert inf.errores == [] and any("1 regla(s) vigente(s) sin revisión" in a for a in inf.avisos)
    escribir(repo, [regla(revision="no_existe")])
    assert any("reglas-kevin.yml: " in e and "revision desconocida" in e for e in validar.validar(repo).errores)


def test_main_imprime_las_notas_despues_del_veredicto(repo, capsys, monkeypatch):
    escribir(repo, [regla(id="texto-chico", regla="Texto más grande")])
    monkeypatch.setattr(estilo, "revisar", lambda *a, **k: [("[plan] x", True, "")])
    plan = repo / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    assert estilo.main(["v.mp4", str(plan), "--raiz", str(repo)]) == 0
    sal = capsys.readouterr().out
    assert sal.index("EN VERDE") < sal.index("texto-chico: Texto más grande")


# ----------------------------------------------------------------------------------- los videos

def componer_v2(marca, tmp_path, src, *, musica=None, ficha=None, duracion=4.0):
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, lienzo.Toma(**TOMA), LINEAS, mp4, duracion=duracion, trabajo=tmp_path / "w",
                             fuente_video=src, inicio=0.0, musica=musica, ficha=ficha)
    return mp4, plan


@pytest.fixture(scope="module")
def buena(tmp_path_factory, marca):
    """Toma con video en movimiento y audio original: debe pasar las 3."""
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    base = tmp_path_factory.mktemp("buena")
    mp4, plan = componer_v2(marca, base, fuente(base / "a.mp4", 640, 360, 4, patron="testsrc2"), duracion=4.0)
    return {"mp4": mp4, "plan": plan, "base": base}


@necesita_ffmpeg
def test_una_toma_buena_sale_en_verde_en_las_3(buena, marca):
    puntos = estilo.revisar(buena["mp4"], buena["plan"], marca)
    est = estado(p for p in puntos if p[0].startswith("[regla-kevin]"))
    assert set(est) == set(NOMBRES), est
    assert all(ok for ok, _ in est.values()), est
    assert "mayor cambio" in est["muy-simple"][1] and "cropdetect: caja 1080x1920" in est["ocupa-todo"][1]
    # las reglas van al final de la puerta
    assert [n for n, _, _ in puntos][-3:] == [n for n, _, _ in puntos if n.startswith("[regla-kevin]")]


@necesita_ffmpeg
def test_toma_de_imagen_fija_sale_en_rojo_por_movimiento(marca, tmp_path):
    src = lavfi(tmp_path / "fija.mp4", "color=c=0x305070:s=640x360:r=30:d=4", 4)
    mp4, plan = componer_v2(marca, tmp_path, src)
    ok, det = reglas_kevin.con_movimiento_y_audio(mp4, plan, marca, None)
    assert not ok and "FALLA movimiento" in det and "bien audio" in det, det


@necesita_ffmpeg
def test_toma_muda_sin_sin_musica_sale_en_rojo_por_audio(marca, tmp_path):
    src = fuente(tmp_path / "muda.mp4", 640, 360, 4, audio=False, patron="testsrc2")
    mp4, plan = componer_v2(marca, tmp_path, src)
    ok, det = reglas_kevin.con_movimiento_y_audio(mp4, plan, marca, None)
    assert not ok and "FALLA audio" in det and "bien movimiento" in det, det


@necesita_ffmpeg
def test_toma_muda_con_sin_musica_y_fuente_muda_el_audio_no_aplica(marca, tmp_path):
    ficha = {"id": "f", "reglas": {"sin_musica": True}}
    src = fuente(tmp_path / "muda.mp4", 640, 360, 4, audio=False, patron="testsrc2")
    mp4, plan = componer_v2(marca, tmp_path, src, ficha=ficha)
    ok, det = reglas_kevin.con_movimiento_y_audio(mp4, plan, marca, ficha)
    assert ok and "audio no aplica" in det, det
    # el movimiento se mide igual: muda, con sin_musica e imagen fija sigue en rojo
    fija = lavfi(tmp_path / "fija.mp4", "color=c=0x305070:s=640x360:r=30:d=4", 4, audio=False)
    mp4, plan = componer_v2(marca, tmp_path / "otra", fija, ficha=ficha)
    ok, det = reglas_kevin.con_movimiento_y_audio(mp4, plan, marca, ficha)
    assert not ok and "audio no aplica" in det and "FALLA movimiento" in det


@necesita_ffmpeg
def test_pieza_sin_audio_propio_sin_pista_sale_en_rojo_por_musica(marca, tmp_path):
    src = fuente(tmp_path / "muda.mp4", 640, 360, 4, audio=False, patron="testsrc2")
    mp4, plan = componer_v2(marca, tmp_path, src)
    ok, det = reglas_kevin.musica_cc0_sin_audio_propio(mp4, plan, marca, None)
    assert not ok and "debe llevar música CC0" in det
    est = estado(estilo.revisar(mp4, plan, marca))
    assert not est["sin-musica"][0] and not est["muy-simple"][0]


@necesita_ffmpeg
def test_fuente_muda_con_pista_cc0_pasa_musica_y_audio(raiz_musica, tmp_path):
    m = Marca.cargar(raiz_musica[0])
    src = fuente(tmp_path / "muda.mp4", 640, 360, 4, audio=False, patron="testsrc2")
    mp4, plan = componer_v2(m, tmp_path, src, musica=pista_de(raiz_musica))
    est = estado(reglas_kevin.puntos(mp4, plan, m))
    assert all(ok for ok, _ in est.values()), est
    assert "suena con pico" in est["sin-musica"][1]


@necesita_ffmpeg
def test_video_con_franjas_negras_arriba_y_abajo_sale_en_rojo_por_cropdetect(buena, marca, tmp_path):
    franjas = tmp_path / "franjas.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(buena["mp4"]),
                    "-vf", "scale=1080:608,pad=1080:1920:0:656:color=black",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "copy", str(franjas)],
                   check=True)
    ok, det = reglas_kevin.sin_franjas(franjas, buena["plan"], marca, None)
    assert not ok and "FALLA cropdetect" in det and "franja de arriba" in det and "abajo" in det, det
    assert "cropdetect: caja" in reglas_kevin.sin_franjas(buena["mp4"], buena["plan"], marca, None)[1]


@necesita_ffmpeg
def test_pieza_sin_video_con_movimiento_y_musica_sale_en_verde(raiz_musica, tmp_path):
    m = Marca.cargar(raiz_musica[0])
    t = lienzo.Toma(numero=1, gancho="GTA 6 llega esta medianoche", palabra_lapiz="MEDIANOCHE", lapiz="círculo",
                    escena="INT. TU CONSOLA", credito="Fuente: Rockstar Games Support")
    ln = [componer.Linea(0.0, 4.0, "EL DATO", "Medianoche local del 19 de noviembre."),
          componer.Linea(4.1, 7.0, "OTRO DATO", "Segunda frase distinta.")]
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(m, t, ln, mp4, duracion=8.0, trabajo=tmp_path / "w", musica=pista_de(raiz_musica, 0),
                             movimiento=True)
    est = estado(reglas_kevin.puntos(mp4, plan, m))
    assert all(ok for ok, _ in est.values()), est
    assert "primera línea" in est["muy-simple"][1]

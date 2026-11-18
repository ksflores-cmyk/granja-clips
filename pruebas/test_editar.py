"""Pruebas del paquete fabrica.editar (sin red; las de ffmpeg se saltan solas si no está)."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from conftest import RAIZ_REAL
from fabrica import validar
from fabrica.editar import cierre, componer, dialogo, estilo, lapiz, lienzo, muestras
from fabrica.editar.marca import Marca

hay_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
necesita_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")

HASHES_FUENTES = {
    "CourierPrime-Regular.ttf": "72f79337", "CourierPrime-Bold.ttf": "ff1f3878",
    "CourierPrime-Italic.ttf": "f1b9a582", "CourierPrime-BoldItalic.ttf": "3355273f",
    "OFL.txt": "9a755af0",
}


@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


def pixeles(img):
    return np.asarray(img.convert("RGBA"))


# ---------------------------------------------------------------- trazos

def test_trazos_deterministas_misma_semilla(marca, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    lapiz.generar_biblioteca(marca, a)
    lapiz.generar_biblioteca(marca, b)
    for ruta in sorted(a.iterdir()):
        assert np.array_equal(pixeles(Image.open(ruta)), pixeles(Image.open(b / ruta.name)))


def test_diez_trazos_presentes_y_distintos(marca):
    archivos = sorted(p.name for p in lapiz.DIR_TRAZOS.glob("*.png"))
    assert len(archivos) == 10
    assert len([a for a in archivos if a.startswith("subrayado")]) == 5
    assert len([a for a in archivos if a.startswith("circulo")]) == 5
    huellas = set()
    for a in archivos:
        img = Image.open(lapiz.DIR_TRAZOS / a)
        assert img.mode == "RGBA"
        assert pixeles(img)[..., 3].max() > 0
        huellas.add(pixeles(img).tobytes())
    assert len(huellas) == 10


def test_trazos_del_repo_coinciden_con_el_generador(marca, tmp_path):
    lapiz.generar_biblioteca(marca, tmp_path)
    for ruta in sorted(tmp_path.iterdir()):
        a = pixeles(Image.open(ruta)).astype(int)
        b = pixeles(Image.open(lapiz.DIR_TRAZOS / ruta.name)).astype(int)
        assert a.shape == b.shape
        assert np.abs(a - b).max() <= 3, ruta.name


def test_trazos_usan_el_rojo_de_marca(marca):
    img = lapiz.cargar_trazo("circulo", 0)
    px = pixeles(img)
    rojo = px[px[..., 3] > 200][:, :3]
    assert len(rojo) > 0
    assert tuple(np.median(rojo, axis=0).astype(int)) == marca.color("lapiz_rojo")


@pytest.mark.parametrize("ancho,alto", [(240, 46), (400, 50), (120, 46), (860, 52)])
@pytest.mark.parametrize("indice", range(5))
def test_circulo_no_cruza_la_caja_de_la_palabra(marca, ancho, alto, indice):
    img = lapiz.trazo_circulo(marca, ancho, alto, indice)
    alfa = pixeles(img)[..., 3]
    h, w = alfa.shape
    cx, cy = w / 2, h / 2
    x0, x1 = int(round(cx - ancho / 2)) + 3, int(round(cx + ancho / 2)) - 3
    y0, y1 = int(round(cy - alto / 2)) + 3, int(round(cy + alto / 2)) - 3
    assert alfa[y0:y1, x0:x1].max() == 0
    # y rodea la palabra: hay trazo a los cuatro lados de la caja
    assert alfa[:, : int(cx - ancho / 2)].max() > 0
    assert alfa[:, int(cx + ancho / 2):].max() > 0
    assert alfa[: int(cy - alto / 2), :].max() > 0
    assert alfa[int(cy + alto / 2):, :].max() > 0


def test_subrayado_sobresale_a_cada_lado(marca):
    img = lapiz.trazo_subrayado(marca, 300, 2)
    ancho = img.width - 2 * lapiz.RELLENO
    assert 300 + 24 <= ancho <= 300 + 48 + 1
    grosor_max = (pixeles(img)[..., 3] > 128).sum(axis=0).max()
    assert 5 <= grosor_max <= 12


def test_la_toma_elige_el_trazo_por_numero_modulo_5():
    assert [lapiz.indice_trazo(n) for n in range(1, 8)] == [1, 2, 3, 4, 0, 1, 2]
    assert lapiz.indice_trazo(3) != lapiz.indice_trazo(4)


def test_tipo_de_lapiz_acepta_circulo_con_acento():
    assert lapiz.normalizar_tipo("círculo") == "circulo"
    with pytest.raises(ValueError):
        lapiz.normalizar_tipo("flecha")


# ---------------------------------------------------------------- errores claros

def toma(**kw):
    base = dict(numero=1, gancho="El conejo sale a la luz", palabra_lapiz="LUZ", lapiz="subrayado",
                escena="EXT. PRADERA", credito="Con: alguien")
    base.update(kw)
    return lienzo.Toma(**base)


def test_gancho_de_7_palabras_falla(marca):
    with pytest.raises(ValueError, match="7 palabras"):
        lienzo.cabecera(marca, toma(gancho="uno dos tres cuatro cinco seis siete", palabra_lapiz="UNO"))


def test_gancho_que_no_cabe_en_2_lineas_falla(marca):
    g = "ANTICONSTITUCIONALMENTE desproporcionadamente electroencefalografista"
    with pytest.raises(ValueError, match="no cabe"):      # la palabra sola ya excede el ancho
        lienzo.cabecera(marca, toma(gancho=g, palabra_lapiz="ANTICONSTITUCIONALMENTE"))
    g3 = "DESPROPORCIONADAMENTE ANTICONSTITUCIONAL ELECTROENCEFALO"   # cada palabra cabe; salen 3 líneas
    with pytest.raises(ValueError, match="2 líneas"):
        lienzo.cabecera(marca, toma(gancho=g3, palabra_lapiz="ANTICONSTITUCIONAL"))


def test_palabra_del_lapiz_ausente_falla(marca):
    with pytest.raises(ValueError, match="no está en el gancho"):
        lienzo.cabecera(marca, toma(palabra_lapiz="DRAGÓN"))


def test_frase_de_3_lineas_falla(marca):
    frase = "abcdefghijklm " * 5   # 2 por línea: 3 líneas
    with pytest.raises(ValueError, match="3 líneas"):
        dialogo.bloque(marca, 900, nombre="ANA", frase=frase.strip())


def test_acotacion_demasiado_larga_falla(marca):
    with pytest.raises(ValueError, match="acotación"):
        dialogo.bloque(marca, 900, acotacion="x" * 80)


def test_bloque_de_solo_acotacion_sin_nombre(marca):
    capa = dialogo.bloque(marca, 900, acotacion="(sale sin prisa)")
    alfa = pixeles(capa)[..., 3]
    ys = np.where(alfa.max(axis=1) > 0)[0]
    assert ys.min() >= 900 and ys.max() < 900 + marca.m("alto_acotacion") + 10
    xs = np.where(alfa.max(axis=0) > 0)[0]
    centro = (xs.min() + xs.max()) / 2
    assert abs(centro - sum(marca.columna) / 2) < 6
    with pytest.raises(ValueError):
        dialogo.bloque(marca, 900)
    with pytest.raises(ValueError, match="nombre"):
        dialogo.bloque(marca, 900, frase="hola")


# ---------------------------------------------------------------- páginas

def test_cabecera_pagina_1080x1920_y_cierre(marca):
    img, y = lienzo.cabecera(marca, toma())
    assert img.size == (1080, 1920) and img.mode == "RGB"
    assert y > marca.m("gancho_y")
    c = cierre.pagina_cierre(marca, toma())
    assert c.size == (1080, 1920)
    assert pixeles(c)[1700, 540, :3].tolist() != [0, 0, 0]


def test_papel_con_grano_y_semilla_fija(marca):
    a, b = pixeles(lienzo.papel(marca)), pixeles(lienzo.papel(marca))
    assert np.array_equal(a, b)
    assert a[..., :3].std() > 0.5


def test_mosca_en_la_esquina_elegida(marca):
    """La mosca (GUION VIVO, 10 letras) y TOMA nnn (8) cambian de mitad según mosca.esquina."""
    def anchos(m):
        img = lienzo.papel(m)
        lienzo.fila_superior(img, m, 5)
        a = np.asarray(img.convert("RGB")).astype(int)
        y0 = int(m.m("fila_superior_y"))
        banda = a[y0: y0 + int(m.m("tam_fila")) + 10]
        cols = np.where((banda.sum(axis=2) < 300).any(axis=0))[0]
        mitad = a.shape[1] // 2
        izq, der = cols[cols < mitad], cols[cols >= mitad]
        assert izq.size and der.size
        return izq.max() - izq.min(), der.max() - der.min()

    assert marca.datos["mosca"]["esquina"] == "superior_derecha"
    izq_d, der_d = anchos(marca)
    assert der_d > izq_d
    datos = json.loads(json.dumps(marca.datos))
    datos["mosca"]["esquina"] = "superior_izquierda"
    izq_i, der_i = anchos(Marca(datos, marca.raiz))
    assert izq_i > der_i


def test_cambiar_una_medida_de_marca_cambia_la_imagen(marca, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(RAIZ_REAL, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    ruta = repo / "marca.yml"
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    original, _ = lienzo.cabecera(Marca.cargar(repo), toma())
    datos["maqueta"]["tam_gancho"] = 50
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    cambiada, _ = lienzo.cabecera(Marca.cargar(repo), toma())
    assert not np.array_equal(pixeles(original), pixeles(cambiada))
    # y un color distinto cambia el papel
    datos["colores"]["papel"] = "#FFFFFF"
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    blanco = lienzo.papel(Marca.cargar(repo))
    assert pixeles(blanco)[..., :3].mean() > pixeles(lienzo.papel(marca))[..., :3].mean()


def test_dialogo_cambia_con_la_medida_de_la_frase(marca):
    a = dialogo.bloque(marca, 900, nombre="ANA", frase="Hola mundo")
    b = dialogo.bloque(marca.con_maqueta(tam_frase=30), 900, nombre="ANA", frase="Hola mundo")
    assert not np.array_equal(pixeles(a), pixeles(b))


def test_el_codigo_no_lleva_medidas_ni_colores_escritos_a_mano():
    import re
    for ruta in (RAIZ_REAL / "fabrica" / "editar").glob("*.py"):
        texto = ruta.read_text(encoding="utf-8")
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", texto), ruta.name
    for nombre in ("lienzo.py", "dialogo.py", "cierre.py"):
        texto = (RAIZ_REAL / "fabrica" / "editar" / nombre).read_text(encoding="utf-8")
        assert not re.search(r"\b(1080|1920)\b", texto), nombre


# ---------------------------------------------------------------- ffmpeg (orden pura)

ARGS = dict(salida="s.mp4", duracion=14.0, ancho=1080, cierre_s=0.5, pagina="p.png", cierre="c.png",
            capas=[("d0.png", 7.5, 13.4)])


def test_orden_ffmpeg_con_video():
    cmd = componer.orden_ffmpeg(**ARGS, video={"ruta": "v.mp4", "inicio": 40.0, "alto": 608, "y": 530, "audio": True})
    txt = " ".join(cmd)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "s.mp4"
    assert "-ss 40 -t 14 -i v.mp4" in txt
    assert "scale=1080:608:flags=lanczos" in txt and "overlay=0:530:shortest=1" in txt
    assert "between(t,7.5,13.4)" in txt and "gte(t,13.50)" in txt
    assert "-map [out] -map 1:a" in txt
    for trozo in ("-preset veryfast", "-crf 20", "-profile:v high", "-pix_fmt yuv420p", "-r 30",
                  "-c:a aac", "-b:a 128k", "-ar 48000", "-movflags +faststart"):
        assert trozo in txt
    assert "anullsrc" not in txt


def test_orden_ffmpeg_sin_video_usa_audio_silencioso():
    cmd = componer.orden_ffmpeg(**ARGS, video=None)
    txt = " ".join(cmd)
    assert "anullsrc=r=48000" in txt and "[0:v]null[c0]" in txt
    assert "[1:v]overlay=0:0:enable='between(t,7.5,13.4)'" in txt
    assert "[2:v]overlay=0:0:enable='gte(t,13.50)'" in txt
    assert "-map [out] -map 3:a" in txt
    assert "-ss" not in cmd


def test_orden_ffmpeg_video_sin_audio_usa_silencio():
    cmd = componer.orden_ffmpeg(**ARGS, video={"ruta": "v.mp4", "inicio": 0, "alto": 600, "y": 500, "audio": False})
    assert "anullsrc=r=48000:cl=stereo" in " ".join(cmd) and "1:a" not in cmd


def test_alto_escalado_es_par_y_sin_recorte():
    assert componer.alto_escalado(1080, 640, 360) == 608
    assert componer.alto_escalado(1080, 720, 306) == 460
    assert componer.alto_escalado(1080, 720, 306) % 2 == 0


# ---------------------------------------------------------------- puerta de estilo

PLAN = {
    "toma": {"numero": 1, "gancho": "GTA 6 llega esta medianoche", "palabra_lapiz": "MEDIANOCHE",
             "lapiz": "circulo", "escena": "INT. TU CONSOLA", "credito": "Fuente: Rockstar Games Support"},
    "lineas": [{"t0": 0, "t1": 3.6, "nombre": "EL DATO", "frase": "Medianoche local del 19 de noviembre."}],
    "duracion": 8.0,
}


def plan_con(marca, **cambios_toma):
    p = json.loads(json.dumps(PLAN))
    p["toma"].update(cambios_toma)
    p["colores"] = dict(marca.datos["colores"])
    p["fuente"] = {"familia": marca.datos["fuente"]["familia"], "archivos": dict(marca.datos["fuente"]["archivos"])}
    return p


@pytest.fixture(scope="module")
def mp4_sintetico(tmp_path_factory):
    """Toma tipográfica de 8 s hecha con componer: trae la página de cierre del PLAN."""
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    base = tmp_path_factory.mktemp("sint")
    ruta = base / "sint.mp4"
    m = Marca.cargar()
    t = lienzo.Toma(**PLAN["toma"])
    ln = [componer.Linea(0.0, 3.6, "EL DATO", "Medianoche local del 19 de noviembre.")]
    componer.componer(m, t, ln, ruta, duracion=8.0, trabajo=base / "w")
    return ruta


def test_cierre_ausente_dice_no(marca, tmp_path):
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    sin_cierre = tmp_path / "sin.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=8",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "8",
        "-c:v", "libx264", "-preset", "ultrafast", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart", str(sin_cierre)], check=True)
    ok, detalle = estilo.cierre_visible(sin_cierre, plan_con(marca), marca)
    assert ok is False, detalle


def test_cierre_con_otro_credito_dice_no(marca, mp4_sintetico):
    ok, detalle = estilo.cierre_visible(mp4_sintetico, plan_con(marca, credito="Con: @otro_creador_distinto"), marca)
    assert ok is False, detalle


@necesita_ffmpeg
def test_estilo_plan_valido_todo_si(marca, mp4_sintetico):
    puntos = estilo.revisar(mp4_sintetico, plan_con(marca), marca)
    assert len(puntos) >= 14
    assert all(ok for _, ok, _ in puntos), [p for p in puntos if not p[1]]


@necesita_ffmpeg
def test_estilo_gancho_generico_dice_no(marca, mp4_sintetico):
    puntos = estilo.revisar(mp4_sintetico, plan_con(marca, gancho="Esto cambia todo", palabra_lapiz="TODO"), marca)
    malos = [n for n, ok, _ in puntos if not ok]
    assert "[plan] Sin ganchos genéricos prohibidos" in malos
    # el plan ya no coincide con el video: la cabecera del MP4 es otra, y la puerta lo ve
    assert "[fotograma] Cabecera en el video" in malos


@necesita_ffmpeg
def test_estilo_detecta_emoji_credito_vacio_y_gancho_largo(marca, mp4_sintetico):
    puntos = dict((n, ok) for n, ok, _ in estilo.revisar(mp4_sintetico, plan_con(marca, credito="  "), marca))
    assert puntos["[plan] Crédito presente y no vacío"] is False
    puntos = dict((n, ok) for n, ok, _ in estilo.revisar(mp4_sintetico, plan_con(marca, escena="INT. 🎮 CASA"), marca))
    assert puntos["[plan] Sin emojis"] is False
    largo = "uno dos tres cuatro cinco seis siete"
    puntos = dict((n, ok) for n, ok, _ in estilo.revisar(mp4_sintetico, plan_con(marca, gancho=largo, palabra_lapiz="UNO"), marca))
    assert puntos["[plan] Gancho de 6 palabras o menos"] is False


@necesita_ffmpeg
def test_estilo_detecta_video_fuera_de_formato(marca, tmp_path):
    corto = tmp_path / "corto.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=25:duration=3", "-pix_fmt", "yuv420p", str(corto)], check=True)
    malos = [n for n, ok, _ in estilo.revisar(corto, plan_con(marca), marca) if not ok]
    assert "[fotograma] Resolución 1080x1920" in malos and "[fotograma] Duración de 7 a 30 s" in malos


@necesita_ffmpeg
def test_estilo_cli_sale_0_y_1(marca, mp4_sintetico, tmp_path):
    bueno = tmp_path / "bueno.json"
    bueno.write_text(json.dumps(plan_con(marca)), encoding="utf-8")
    malo = tmp_path / "malo.json"
    malo.write_text(json.dumps(plan_con(marca, gancho="Esto cambia todo", palabra_lapiz="TODO")), encoding="utf-8")
    assert estilo.main([str(mp4_sintetico), str(bueno)]) == 0
    assert estilo.main([str(mp4_sintetico), str(malo)]) == 1


@necesita_ffmpeg
def test_render_completo_de_una_toma_sin_video(marca, tmp_path):
    t = toma(gancho="GTA 6 llega esta medianoche", palabra_lapiz="MEDIANOCHE", lapiz="circulo")
    ln = [componer.Linea(0.0, 4.0, "EL DATO", "Medianoche local del 19 de noviembre.", "según Rockstar")]
    mp4 = tmp_path / "t.mp4"
    plan = componer.componer(marca, t, ln, mp4, duracion=8.0, trabajo=tmp_path / "w", plan_salida=tmp_path / "t.json")
    assert mp4.stat().st_size > 1000 and plan["duracion"] == 8.0
    assert all(ok for _, ok, _ in estilo.revisar(mp4, json.loads((tmp_path / "t.json").read_text()), marca))


# ---------------------------------------------------------------- repo y muestras

def test_fuentes_de_marca_con_sumas_esperadas():
    import hashlib
    for nombre, prefijo in HASHES_FUENTES.items():
        h = hashlib.sha256((RAIZ_REAL / "marca" / "fuentes" / nombre).read_bytes()).hexdigest()
        assert h.startswith(prefijo), nombre


def test_muestras_yml_tiene_las_3_muestras_exactas():
    m = {x["id"]: x for x in muestras.cargar_muestras()}
    assert list(m) == ["dialogo-sintel", "momento-bbb", "informativa-gta6"]
    assert m["dialogo-sintel"]["recorte"] == {"inicio": 39.6, "duracion": 17.0}
    assert m["dialogo-sintel"]["toma"]["gancho"] == "Ella busca a un dragón"
    assert len(m["dialogo-sintel"]["lineas"]) == 4
    assert m["momento-bbb"]["lineas"][0]["acotacion"] == "(sale sin prisa)"
    assert m["informativa-gta6"]["duracion"] == 11.0 and m["informativa-gta6"]["video"] is None
    marca = Marca.cargar(RAIZ_REAL)
    for x in m.values():  # todas caben en la maqueta
        lienzo.preparar_gancho(marca, x["toma"]["gancho"], x["toma"]["palabra_lapiz"])
        for ln in x["lineas"]:
            dialogo.validar_bloque(marca, ln.get("nombre"), ln.get("frase"), ln.get("acotacion"))


def test_muestras_rechaza_salida_dentro_del_repo(tmp_path):
    assert muestras.main(["--salida", str(RAIZ_REAL / "salida-prueba")]) == 2
    assert not (RAIZ_REAL / "salida-prueba").exists()


def test_descarga_con_sha256_distinto_falla(tmp_path, monkeypatch):
    import io
    import urllib.request

    class Falsa(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Falsa(b"hola"))
    destino = tmp_path / "x.bin"
    with pytest.raises(RuntimeError, match="sha256"):
        muestras.descargar("https://raw.githubusercontent.com/a/b/c.bin", "0" * 64, destino)
    assert not destino.exists()


def test_descarga_rechaza_hosts_fuera_de_lista(tmp_path):
    with pytest.raises(RuntimeError, match="lista permitida"):
        muestras.descargar("https://example.com/x.mp4", "0" * 64, tmp_path / "x.mp4")


def test_fuentes_yml_tiene_las_fichas_de_muestras():
    fichas = {f["id"]: f for f in yaml.safe_load((RAIZ_REAL / "fuentes.yml").read_text(encoding="utf-8"))}
    for fid in ("sintel-cc-by", "bbb-cc-by"):
        f = fichas[fid]
        assert f["tipo"] == "B" and f["uso"] == "muestras" and f["publicable"] is False
        assert f["permiso_url"] == "https://creativecommons.org/licenses/by/3.0/"
        assert f["campana"] is None and f["vence"] is None and f["estado"] == "vigente"
    assert "358447f3" in fichas["sintel-cc-by"]["notas"] and "543a4ad9" in fichas["bbb-cc-by"]["notas"]


# ---------------------------------------------------------------- validar.py

def test_validar_exige_maqueta(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda d: (d.pop("maqueta"), None)[1])
    assert any("falta la sección maqueta" in e for e in validar.validar(repo).errores)


def test_validar_maqueta_numeros_positivos(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda d: d["maqueta"].update(tam_gancho=0))
    assert any("tam_gancho debe ser positivo" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "marca.yml", lambda d: d["maqueta"].update(tam_gancho=66, grano={"papel": -1, "fibra": 1, "bloque_fibra": 4, "semilla": 7}))
    assert any("grano.papel debe ser positivo" in e for e in validar.validar(repo).errores)


def test_validar_esquina_de_la_mosca(repo, editar_yaml):
    editar_yaml(repo, "marca.yml", lambda d: d["mosca"].update(esquina="inferior_derecha"))
    assert any("mosca.esquina" in e for e in validar.validar(repo).errores)
    editar_yaml(repo, "marca.yml", lambda d: d["mosca"].update(esquina="superior_izquierda"))
    assert validar.validar(repo).errores == []


def test_validar_fuente_ausente(repo):
    (repo / "marca" / "fuentes" / "CourierPrime-Bold.ttf").unlink()
    assert any("fuente.archivos.bold" in e for e in validar.validar(repo).errores)

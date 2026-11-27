"""Estilo v3, punto 3 (Kevin, 25 nov): paneo horizontal muy suave que sigue la acción, sin zoom ni recorte vertical."""
import shutil
import subprocess

import pytest

from fabrica.editar import encuadre
from fabrica.editar.marca import Marca

necesita_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")


def fuente_movil(ruta, dur=8):
    """1920x1080: un cuadro blanco que cruza de izquierda a derecha sobre fondo gris con algo de textura."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=0x404040:s=1920x1080:r=30:d={dur}", "-f", "lavfi", "-i",
                    f"color=c=white:s=160x280:r=30:d={dur}", "-filter_complex",
                    f"[0:v]noise=alls=6:allf=t[b];[b][1:v]overlay=x='200+1400*t/{dur}':y=400,format=yuv420p[v]",
                    "-map", "[v]", "-c:v", "libx264", str(ruta)], check=True)
    return ruta


def test_x_en_interpola_y_se_queda_en_los_extremos():
    r = {"x": 100, "ancho": 608, "paneo": [[0.0, 100], [1.0, 120], [2.0, 120]]}
    assert encuadre.x_en(r, -1) == 100 and encuadre.x_en(r, 0.5) == 110 and encuadre.x_en(r, 9) == 120
    assert encuadre.x_en({"x": 7, "ancho": 10}, 3.0) == 7


def test_expresion_x_sin_y_con_paneo():
    assert encuadre.expresion_x({"x": 42}) == "42"
    e = encuadre.expresion_x({"x": 0, "paneo": [[0, 0], [0.5, 10], [1.0, 20]]})
    assert e.startswith("'if(lt(t,0.5),") and e.endswith(",20))'")


def test_problemas_paneo():
    bien = {"x": 0, "ancho": 600, "paneo": [[0, 0], [0.5, 30], [1.0, 60]]}
    assert encuadre.problemas_paneo(bien, 1920) == []
    rapido = {"x": 0, "ancho": 600, "paneo": [[0, 0], [0.5, 400]]}
    assert "rápido" in encuadre.problemas_paneo(rapido, 1920)[0]
    fuera = {"x": 0, "ancho": 600, "paneo": [[0, 0], [0.5, 1400]]}
    assert "fuera" in encuadre.problemas_paneo(fuera, 1920)[0]


@necesita_ffmpeg
def test_el_paneo_sigue_la_accion_despacio_y_sin_zoom(tmp_path):
    m = Marca.cargar()
    src = fuente_movil(tmp_path / "f.mp4")
    r = encuadre.elegir_recorte(src, 0.0, 8.0, 1920, 1080, m.ancho, m.alto, ajustes=m.pc["recorte"])
    assert r["alto"] == 1080                               # nada vertical se recorta
    assert r.get("paneo"), r
    xs = [x for _, x in r["paneo"]]
    assert xs[-1] > xs[0]                                  # el cuadro va a la derecha y la cámara lo sigue
    assert max(xs) - min(xs) <= 0.25 * r["ancho"] * 2 + 2  # nunca más lejos que la desviación máxima
    assert encuadre.problemas_paneo(r, 1920) == []
    assert all(x % 2 == 0 for x in xs)


@necesita_ffmpeg
def test_fuente_quieta_no_panea(tmp_path):
    m = Marca.cargar()
    src = tmp_path / "q.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=1920x1080:r=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    r = encuadre.elegir_recorte(src, 0.0, 4.0, 1920, 1080, m.ancho, m.alto, ajustes=m.pc["recorte"])
    assert "paneo" not in r


@necesita_ffmpeg
def test_ffmpeg_acepta_la_expresion(tmp_path):
    m = Marca.cargar()
    src = fuente_movil(tmp_path / "f.mp4", 4)
    r = encuadre.elegir_recorte(src, 0.0, 4.0, 1920, 1080, m.ancho, m.alto, ajustes=m.pc["recorte"])
    out = tmp_path / "o.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex",
                    f"[0:v]fps=30,crop={r['ancho']}:{r['alto']}:{encuadre.expresion_x(r)}:{r['y']},scale=540:960[v]",
                    "-map", "[v]", "-t", "4", str(out)], check=True)
    assert out.stat().st_size > 0

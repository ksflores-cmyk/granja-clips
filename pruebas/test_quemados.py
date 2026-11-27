"""Estilo v3, punto 4: franja de subtítulos quemados (Gemini) y su tapado con ffmpeg."""
import shutil
import subprocess

import pytest

from fabrica import ojos
from fabrica.editar import toma


def test_franja_fiable():
    assert ojos.franja_quemados({"subtitulos_quemados": True, "subtitulos_franja": [0.78, 0.92]}) == (0.78, 0.92)


@pytest.mark.parametrize("fr", [None, [0.8], [0.9, 0.8], [0.1, 0.5], [0.5, 0.505], [-0.1, 0.1], [0.9, 1.2], ["a", 1]])
def test_franja_no_fiable(fr):
    d = {"subtitulos_quemados": True}
    if fr is not None:
        d["subtitulos_franja"] = fr
    assert ojos.franja_quemados(d) is None
    assert "franja fiable" in toma.motivo_quemados(d)


def test_sin_quemados_no_hay_franja_ni_motivo():
    d = {"subtitulos_quemados": False, "subtitulos_franja": [0.8, 0.9]}
    assert ojos.franja_quemados(d) is None and toma.motivo_quemados(d) is None


def test_orden_tapar_es_pura_y_difumina_a_lo_ancho():
    o = toma.orden_tapar("f.mp4", 3.5, 12.0, (0.8, 0.9), "d.mp4")
    g = o[o.index("-filter_complex") + 1]
    assert o[o.index("-ss") + 1] == "3.5" and o[o.index("-t") + 1] == "12"
    assert "crop=iw:" in g and "boxblur" in g and "overlay=0:" in g
    assert "-map" in o and "0:a?" in o and o[-1] == "d.mp4"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_tapar_franja_con_ffmpeg(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=3", "-f", "lavfi",
                    "-i", "sine=d=3", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    out = toma.tapar_franja(src, 0.5, 2.0, (0.7, 0.9), tmp_path / "t.mp4")
    sal = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                                   "-of", "csv=p=0", str(out)], text=True)
    assert "video,640,360" in sal and "audio" in sal


def test_validar_respuesta_rechaza_franja_mal_formada():
    from fabrica.editar.marca import Marca
    ok, motivos = ojos.validar_respuesta({"subtitulos_franja": "abajo"}, Marca.cargar(), {})
    assert any("subtitulos_franja" in m for m in motivos)

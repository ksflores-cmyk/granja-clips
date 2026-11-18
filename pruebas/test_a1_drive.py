"""Descarga desde Google Drive cuando devuelve su página de aviso (sin red)."""
import io
from pathlib import Path

import pytest

from fabrica import descargar as d

ID = "1ZzpMQ5LzOem94pys0KAgmLYSf92YkmEP"
URL = f"https://drive.usercontent.google.com/download?id={ID}&export=download&confirm=t"
AVISO = ('<html><head><title>Google Drive - Virus scan warning</title></head><body>'
         '<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
         f'<input type="hidden" name="id" value="{ID}"><input type="hidden" name="export" value="download">'
         '<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="abc-123">'
         '</form></body></html>')


class Resp(io.BytesIO):
    def __init__(self, cuerpo, tipo):
        super().__init__(cuerpo)
        self.headers = {"Content-Type": tipo}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_sigue_el_formulario_de_aviso_una_vez(tmp_path):
    pedidas = []

    def abrir(url):
        pedidas.append(url)
        if "uuid=" in url:
            return Resp(b"\x00" * 1000, "video/mp4")
        return Resp(AVISO.encode(), "text/html; charset=utf-8")

    destino = d.bajar_https(URL, tmp_path / "v.mp4", abrir=abrir)
    assert destino.stat().st_size == 1000
    assert len(pedidas) == 2 and "uuid=abc-123" in pedidas[1] and f"id={ID}" in pedidas[1]


def test_no_sigue_un_formulario_con_otro_id_ni_otro_host(tmp_path):
    otro = AVISO.replace(ID, "OTRO").replace('name="id" value="OTRO"', 'name="id" value="OTRO"')
    for pagina in (otro, AVISO.replace("drive.usercontent.google.com/download", "malo.example.com/download")):
        def abrir(url, pagina=pagina):
            return Resp(pagina.encode(), "text/html")
        with pytest.raises(d.ErrorDescarga, match="página"):
            d.bajar_https(URL, tmp_path / "v.mp4", abrir=abrir)


def test_no_reintenta_dos_veces(tmp_path):
    def abrir(url):
        return Resp(AVISO.encode(), "text/html")
    with pytest.raises(d.ErrorDescarga, match="Virus scan warning"):
        d.bajar_https(URL, tmp_path / "v.mp4", abrir=abrir)


def test_html_fuera_de_drive_sigue_siendo_no_video(tmp_path):
    def abrir(url):
        return Resp(b"<html></html>", "text/html")
    with pytest.raises(d.ErrorDescarga, match="no es video"):
        d.bajar_https("https://example.com/v.mp4", tmp_path / "v.mp4", abrir=abrir)

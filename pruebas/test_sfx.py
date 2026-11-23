"""Efectos CC0 (tarea #20 «historia», parte E): release generalizado y `editar/sfx.py`. Sin red."""
import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

import yaml

from fabrica import musica_release as mr
from fabrica import validar
from fabrica.editar import sfx

PAGINA = """
<div class="field field-name-author-submitter"><div class="field-items"><span class="username"><a href="/users/k">Kenney</a></span></div></div>
<div class="field field-name-field-art-licenses"><div class="field-items"><div class='license-icon'><a href='http://creativecommons.org/publicdomain/zero/1.0/'><div class='license-name'>CC0</div></a></div></div></div>
<div class="field field-name-field-art-files"><div class="field-items"><span class="file"><a href="https://opengameart.org/sites/default/files/pack.zip">pack.zip</a></span></div></div>
<div class="field field-name-field-copyright-notice"></div>
"""
URL_PAGINA = "https://opengameart.org/content/pack"
URL_ZIP = "https://opengameart.org/sites/default/files/pack.zip"
LIC = ("# Efecto\n\n- Fuente: https://opengameart.org/content/pack\n- Autor: Kenney\n"
       "- Licencia: CC0\n- Enlace: https://creativecommons.org/publicdomain/zero/1.0/\n")


def tono(seg: float, hz: int = 440) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = Path(d) / "t.ogg"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"sine=frequency={hz}:duration={seg}", str(r)], check=True)
        return r.read_bytes()


def hacer_zip(miembros: dict[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in miembros.items():
            z.writestr(n, d)
    return b.getvalue()


def entrada(eid, miembro, **extra):
    return {"id": eid, "titulo": eid, "autor": "Kenney", "pagina": URL_PAGINA, "archivo": "pack.zip",
            "zip_miembro": miembro, "licencia": "CC0", "tipo": "golpe", "asset": f"{eid}.ogg",
            "sha256": None, "estado": "candidata", **extra}


def catalogo(tmp_path, efectos, **extra):
    m = tmp_path / "efectos.yml"
    m.write_text(yaml.safe_dump({"licencias_permitidas": ["CC0"], "release": "sfx", "duracion_min_s": 0.1,
                                 "duracion_max_s": 4.0, "minimo": 2, "efectos": efectos, **extra}), encoding="utf-8")
    return m


def simular(monkeypatch, zip_bytes):
    llamadas = []

    def falso(url, tope=0):
        llamadas.append(url)
        return PAGINA.encode() if url == URL_PAGINA else zip_bytes
    monkeypatch.setattr(mr, "_bajar", falso)
    return llamadas


def test_manifiesto_zip_con_varios_miembros_baja_el_zip_una_vez(tmp_path, monkeypatch):
    z = hacer_zip({"Audio/golpe.ogg": tono(0.5), "Audio/tic.ogg": tono(0.3, 880), "LEEME.txt": b"x"})
    llamadas = simular(monkeypatch, z)
    m = catalogo(tmp_path, [entrada("golpe", "golpe.ogg"), entrada("tic", "tic.ogg")])
    out = tmp_path / "salida"
    assert mr.main(["--manifiesto", str(m), "--release", "sfx", "--salida", str(out)]) == 0
    assert llamadas.count(URL_ZIP) == 1 and llamadas.count(URL_PAGINA) == 1
    assert (out / "golpe.ogg").is_file() and (out / "tic.ogg").is_file()
    import json
    inf = json.loads((out / "lecturas.json").read_text(encoding="utf-8"))
    assert [x["id"] for x in inf["verificadas"]] == ["golpe", "tic"]
    assert inf["verificadas"][0]["zip_miembro"] == "Audio/golpe.ogg"


def test_miembro_ausente_lista_los_nombres(tmp_path, monkeypatch):
    z = hacer_zip({"Audio/golpe.ogg": tono(0.5), "Audio/otro.ogg": tono(0.5)})
    simular(monkeypatch, z)
    out = tmp_path / "salida"
    out.mkdir()
    try:
        mr.procesar(entrada("x", "no-existe.ogg"), {"CC0"}, out)
        assert False, "debía rechazar"
    except ValueError as e:
        assert "Audio/golpe.ogg" in str(e) and "Audio/otro.ogg" in str(e)


def test_duracion_maxima_y_minima(tmp_path, monkeypatch):
    z = hacer_zip({"largo.ogg": tono(5.0), "corto.ogg": tono(0.05)})
    simular(monkeypatch, z)
    out = tmp_path / "salida"
    out.mkdir()
    for miembro in ("largo.ogg", "corto.ogg"):
        try:
            mr.procesar(entrada("x", miembro), {"CC0"}, out, 0.1, 4.0)
            assert False, "debía rechazar"
        except ValueError as e:
            assert "máximo 4" in str(e)
    ok = mr.procesar(entrada("x", "largo.ogg"), {"CC0"}, out, 0.1, None)
    assert ok["duracion_s"] > 4


def test_por_defecto_la_musica_sigue_igual(tmp_path, monkeypatch):
    z = hacer_zip({"corto.ogg": tono(1.0)})
    simular(monkeypatch, z)
    try:
        mr.procesar(entrada("x", "corto.ogg"), {"CC0"}, tmp_path)
        assert False
    except ValueError as e:
        assert "mínimo 12" in str(e)


def test_licencia_ajena_se_rechaza(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "_bajar", lambda url, tope=0: PAGINA.replace("CC0</div>", "CC-BY</div>").encode())
    try:
        mr.procesar(entrada("x", "a.ogg"), {"CC0"}, tmp_path)
        assert False
    except ValueError as e:
        assert "licencia leída" in str(e)


# --- editar/sfx.py ---

def montar(tmp_path, estado="verificada", sha_malo=False, con_licencia=True):
    raiz = tmp_path / "repo"
    (raiz / "marca" / "sfx" / "golpe").mkdir(parents=True)
    audio = tmp_path / "audio"
    audio.mkdir()
    datos = tono(0.4)
    (audio / "golpe.ogg").write_bytes(datos)
    sha = "0" * 64 if sha_malo else hashlib.sha256(datos).hexdigest()
    (raiz / "marca" / "sfx" / "efectos.yml").write_text(yaml.safe_dump({
        "licencias_permitidas": ["CC0"], "release": "sfx",
        "efectos": [entrada("golpe", "golpe.ogg", sha256=sha, estado=estado)]}), encoding="utf-8")
    if con_licencia:
        (raiz / "marca" / "sfx" / "golpe" / "LICENCIA.md").write_text(LIC, encoding="utf-8")
    return raiz, audio


def test_disponibles_con_sha_bueno(tmp_path):
    raiz, audio = montar(tmp_path)
    d = sfx.disponibles(raiz, audio)
    assert list(d) == ["golpe"] and d["golpe"]["ruta"] == str(audio / "golpe.ogg") and d["golpe"]["tipo"] == "golpe"
    assert sfx.de_tipo(raiz, "golpe", audio)["id"] == "golpe"
    assert sfx.de_tipo(raiz, "boom", audio) is None


def test_disponibles_descarta_sha_malo_sin_licencia_y_candidata(tmp_path):
    for kw in ({"sha_malo": True}, {"con_licencia": False}, {"estado": "candidata"}):
        raiz, audio = montar(tmp_path / str(len(kw) and list(kw)[0]), **kw)
        assert sfx.disponibles(raiz, audio) == {}


def test_sin_carpeta_o_sin_archivos_no_lanza(tmp_path, monkeypatch):
    raiz, audio = montar(tmp_path)
    monkeypatch.delenv(sfx.VAR_CARPETA, raising=False)
    assert sfx.carpeta() is None and sfx.disponibles(raiz) == {}
    assert sfx.disponibles(raiz, tmp_path / "no-existe") == {}
    monkeypatch.setenv(sfx.VAR_CARPETA, str(audio))
    assert list(sfx.disponibles(raiz)) == ["golpe"]
    assert sfx.disponibles(tmp_path / "vacio", audio) == {}


def test_catalogo_real_valida():
    inf = validar.validar()
    assert not [e for e in inf.errores if "sfx" in e]

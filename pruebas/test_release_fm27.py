"""Material de FM27 en el release privado `fuentes-fm27` (sin red: transportes y `gh` falsos)."""
from __future__ import annotations

import io
import json
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import RAIZ_REAL
from fabrica import descargar as d
from fabrica import fuentes, release, validar
from fabrica.descargar import ErrorDescarga

REPO = "ksflores-cmyk/granja-clips"
TAG = "fuentes-fm27"
BASE = f"https://github.com/{REPO}/releases/download/{TAG}/"
NOMBRES = ["fm27-sebasberon.mp4", "fm27-daro.mp4", "fm27-eric-zzeta.mp4", "fm27-habemus-gamer.mov", "fm27-tio-pedro.mp4"]
URL = BASE + "fm27-daro.mp4"
FIRMADA = "https://release-assets.githubusercontent.com/github-production-release-asset/1/abc?sig=SECRETO"
TOKEN = "ghs_TOKEN-DE-PRUEBA-EJEMPLO-FALSO"
ENTORNO = {"GITHUB_TOKEN": TOKEN, "GITHUB_REPOSITORY": REPO}
CONTENIDO = b"\x01" * 3000


def asset(nombre="fm27-daro.mp4", id_=7, size=len(CONTENIDO), state="uploaded"):
    return {"id": id_, "name": nombre, "size": size, "state": state}


class Api:
    """Transporte falso de api.github.com. Guarda las peticiones para comprobar a dónde va el token."""

    def __init__(self, assets=None, release=True, location=FIRMADA, estado_asset=302):
        self.assets, self.release, self.location, self.estado_asset = assets, release, location, estado_asset
        self.llamadas: list[tuple[str, str, dict]] = []

    def __call__(self, metodo, url, encabezados):
        self.llamadas.append((metodo, url, dict(encabezados)))
        if "/releases/tags/" in url:
            if not self.release:
                return 404, {}, b"{}"
            return 200, {}, json.dumps({"assets": [asset()] if self.assets is None else self.assets}).encode()
        if "/releases/assets/" in url:
            return self.estado_asset, ({"Location": self.location} if self.location else {}), b""
        raise AssertionError(url)


class Resp(io.BytesIO):
    def __init__(self, cuerpo=CONTENIDO, tipo="application/octet-stream"):
        super().__init__(cuerpo)
        self.headers = {"Content-Type": tipo}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------- URL

def test_url_de_release_valida():
    assert release.es_url_release(URL)
    assert release.partes(URL) == (REPO, TAG, "fm27-daro.mp4")


@pytest.mark.parametrize("url", [
    "https://gitlab.com/a/b/releases/download/t/x.mp4",
    "https://github.com.evil.org/a/b/releases/download/t/x.mp4",
    "http://github.com/a/b/releases/download/t/x.mp4",
    BASE + "../x.mp4", BASE + "..",
    BASE + "x.mp4?token=1", BASE + "x.mp4#a", BASE + "x y.mp4", BASE + "x/y.mp4",
    f"https://github.com/{REPO}/releases/latest/download/x.mp4",
    f"https://github.com/{REPO}/archive/x.mp4",
    "https://usuario:clave@github.com/a/b/releases/download/t/x.mp4",
    None, 7,
])
def test_url_de_release_mala(url):
    assert not release.es_url_release(url)


# ---------------------------------------------------------------- resolver

def test_resolver_sin_token():
    with pytest.raises(ErrorDescarga, match="sin GITHUB_TOKEN"):
        release.resolver(URL, entorno={}, transporte=Api())


def test_resolver_con_gh_token_vale():
    a = release.resolver(URL, entorno={"GH_TOKEN": TOKEN}, transporte=Api())
    assert a.url_firmada == FIRMADA


def test_resolver_repo_distinto_no_usa_el_token():
    api = Api()
    with pytest.raises(ErrorDescarga, match="otro repo"):
        release.resolver(URL, entorno={**ENTORNO, "GITHUB_REPOSITORY": "otro/repo"}, transporte=api)
    assert api.llamadas == []


def test_resolver_release_404():
    with pytest.raises(ErrorDescarga, match=f"release {TAG} aún no existe"):
        release.resolver(URL, entorno=ENTORNO, transporte=Api(release=False))


def test_resolver_asset_ausente_o_sin_terminar():
    with pytest.raises(ErrorDescarga, match="fm27-daro.mp4 aún no está en el release"):
        release.resolver(URL, entorno=ENTORNO, transporte=Api(assets=[asset("otro.mp4")]))
    with pytest.raises(ErrorDescarga, match="aún no está"):
        release.resolver(URL, entorno=ENTORNO, transporte=Api(assets=[asset(state="starter")]))


@pytest.mark.parametrize("loc", ["https://evil.example.com/x", "http://objects.githubusercontent.com/x",
                                 "https://objects.githubusercontent.com.evil.org/x", ""])
def test_resolver_rechaza_redireccion_a_host_no_permitido(loc):
    with pytest.raises(ErrorDescarga, match="host no permitido"):
        release.resolver(URL, entorno=ENTORNO, transporte=Api(location=loc))


def test_resolver_rechaza_si_no_hay_redireccion():
    with pytest.raises(ErrorDescarga, match="HTTP 200"):
        release.resolver(URL, entorno=ENTORNO, transporte=Api(estado_asset=200))


def test_resolver_camino_feliz_y_el_token_sólo_va_a_api_github():
    api = Api()
    a = release.resolver(URL, entorno=ENTORNO, transporte=api)
    assert a == release.Asset(FIRMADA, len(CONTENIDO), "fm27-daro.mp4")
    assert [(m, u.split("/repos/")[1]) for m, u, _ in api.llamadas] == [
        ("GET", f"{REPO}/releases/tags/{TAG}"), ("GET", f"{REPO}/releases/assets/7")]
    for _, u, enc in api.llamadas:
        assert u.startswith("https://api.github.com/") and enc["Authorization"] == f"Bearer {TOKEN}"
        assert enc["X-GitHub-Api-Version"] == "2022-11-28"
    assert api.llamadas[1][2]["Accept"] == "application/octet-stream"


# ---------------------------------------------------------------- bajar

def test_bajar_camino_feliz_sin_mandar_el_token(tmp_path):
    abiertas = []

    def abrir(url):
        abiertas.append(url)
        return Resp()
    r = release.bajar(URL, tmp_path / "v.mp4", entorno=ENTORNO, transporte=Api(), abrir=abrir)
    assert r.read_bytes() == CONTENIDO
    assert abiertas == [FIRMADA]               # `abrir` sólo recibe la URL: no hay dónde meter Authorization


def test_bajar_acepta_video_y_octet_stream_y_rechaza_html(tmp_path):
    for tipo in ("video/mp4", "video/quicktime", "application/octet-stream; charset=x"):
        release.bajar(URL, tmp_path / "v.mp4", entorno=ENTORNO, transporte=Api(), abrir=lambda u, t=tipo: Resp(tipo=t))
    with pytest.raises(ErrorDescarga, match="no es video"):
        release.bajar(URL, tmp_path / "h.mp4", entorno=ENTORNO, transporte=Api(), abrir=lambda u: Resp(tipo="text/html"))
    assert not (tmp_path / "h.mp4").exists()


def test_bajar_con_tamano_distinto_falla_y_borra(tmp_path):
    destino = tmp_path / "v.mp4"
    with pytest.raises(ErrorDescarga, match="bajaron 10 bytes"):
        release.bajar(URL, destino, entorno=ENTORNO, transporte=Api(), abrir=lambda u: Resp(b"x" * 10))
    assert not destino.exists()


def test_bajar_respeta_el_tope_de_bytes(tmp_path):
    destino = tmp_path / "v.mp4"
    with pytest.raises(ErrorDescarga, match="tope"):
        release.bajar(URL, destino, entorno=ENTORNO, transporte=Api(), abrir=lambda u: Resp(), tope=100)
    assert not destino.exists()
    # y si el release dice más de lo que cabe, ni se abre la conexión
    abiertas = []
    with pytest.raises(ErrorDescarga, match="tope"):
        release.bajar(URL, destino, entorno=ENTORNO, transporte=Api(), tope=100,
                      abrir=lambda u: abiertas.append(u) or Resp())
    assert abiertas == []


def test_bajar_respeta_el_tope_de_tiempo(tmp_path, monkeypatch):
    destino = tmp_path / "v.mp4"
    relojes = iter([0.0] + [5.0] * 50)
    monkeypatch.setattr(release, "time", SimpleNamespace(monotonic=lambda: next(relojes)))
    with pytest.raises(ErrorDescarga, match="tope de 1 s"):
        release.bajar(URL, destino, entorno=ENTORNO, transporte=Api(), abrir=lambda u: Resp(), tope_s=1)
    assert not destino.exists()


def test_bajar_error_de_red_borra_y_no_filtra_el_token(tmp_path):
    def abrir(url):
        raise OSError(f"fallo con {TOKEN}")
    destino = tmp_path / "v.mp4"
    destino.write_bytes(b"viejo")
    with pytest.raises(ErrorDescarga) as e:
        release.bajar(URL, destino, entorno=ENTORNO, transporte=Api(), abrir=abrir)
    assert TOKEN not in str(e.value) and not destino.exists()


# ---------------------------------------------------------------- subir (gh y Drive falsos)

class Gh:
    def __init__(self, falla_en=()):
        self.ordenes: list[list[str]] = []
        self.falla_en = set(falla_en)

    def __call__(self, orden):
        self.ordenes.append(orden)
        malo = any(f in " ".join(orden) for f in self.falla_en)
        return SimpleNamespace(returncode=1 if malo else 0, stdout="", stderr="boom" if malo else "")

    def subidas(self):
        return [Path(o[4]).name for o in self.ordenes if o[:3] == ["gh", "release", "upload"]]


class Drive:
    def __init__(self, cuota=()):
        self.cuota, self.pedidas = set(cuota), []

    def __call__(self, url, destino):
        self.pedidas.append(destino.name)
        if destino.name in self.cuota:
            raise ErrorDescarga("Google Drive devolvió una página y no el video ('Quota exceeded')")
        destino.write_bytes(CONTENIDO)
        return destino


def correr(tmp_path, *, run="0", api=None, gh=None, drive=None, extra=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    salida, resumen = tmp_path / "salida.txt", tmp_path / "resumen.md"
    entorno = {**ENTORNO, "GITHUB_RUN_NUMBER": run, "GITHUB_OUTPUT": str(salida), "GITHUB_STEP_SUMMARY": str(resumen),
               **(extra or {})}
    gh, drive = gh or Gh(), drive or Drive()
    codigo = release.orden_subir("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=entorno,
                                 transporte=api or Api(assets=[]), ejecutor=gh, bajar_drive=drive,
                                 sondear=lambda ruta: 12.5, carpeta=tmp_path / "trabajo")
    return codigo, gh, drive, salida, resumen


def test_subir_todo_crea_el_release_sube_5_y_marca_completo(tmp_path, capsys):
    codigo, gh, drive, salida, resumen = correr(tmp_path, api=Api(release=False))
    assert codigo == 0
    crear = gh.ordenes[0]
    assert crear[:4] == ["gh", "release", "create", TAG] and "--latest=false" in crear and "--draft" not in crear
    assert sorted(gh.subidas()) == sorted(NOMBRES) and all("--clobber" in o for o in gh.ordenes[1:])
    assert salida.read_text() == "completo=si\n"
    assert f"release {TAG}: 5 de 5 subidos" in capsys.readouterr().out
    assert "fm27-daro.mp4" in resumen.read_text() and "| subido |" in resumen.read_text()
    assert not any((tmp_path / "trabajo").iterdir())            # nada de video queda en disco


def test_subir_rota_el_orden_segun_la_corrida(tmp_path):
    _, _, d0, _, _ = correr(tmp_path / "a", run="0", api=Api(release=False))
    _, _, d2, _, _ = correr(tmp_path / "b", run="2", api=Api(release=False))
    assert d0.pedidas[0] == NOMBRES[0] and d2.pedidas[0] == NOMBRES[2]
    assert d0.pedidas != d2.pedidas and sorted(d0.pedidas) == sorted(d2.pedidas)


def test_subir_cuota_de_drive_es_warning_y_sigue(tmp_path, capsys):
    codigo, gh, drive, salida, resumen = correr(tmp_path, api=Api(release=False), drive=Drive(cuota={"fm27-daro.mp4"}))
    out = capsys.readouterr().out
    assert codigo == 0
    assert "::warning::fm27-daro.mp4: " in out and "(se reintenta en la próxima corrida)" in out
    assert "fm27-daro.mp4" not in gh.subidas() and len(gh.subidas()) == 4
    assert salida.read_text() == "completo=no\n" and "4 de 5 subidos" in out


def test_subir_solo_intenta_los_que_faltan_y_el_resto_ya_estaba(tmp_path, capsys):
    ya = [asset(n, i + 1) for i, n in enumerate(NOMBRES[:3])]
    codigo, gh, drive, salida, _ = correr(tmp_path, api=Api(assets=ya))
    assert codigo == 0 and sorted(drive.pedidas) == sorted(NOMBRES[3:])
    assert not any(o[:3] == ["gh", "release", "create"] for o in gh.ordenes)      # el release ya existía
    assert salida.read_text() == "completo=si\n"


def test_subir_con_los_cinco_ya_en_el_release_no_baja_nada(tmp_path, capsys):
    codigo, gh, drive, salida, _ = correr(tmp_path, api=Api(assets=[asset(n, i + 1) for i, n in enumerate(NOMBRES)]))
    assert codigo == 0 and drive.pedidas == [] and gh.ordenes == []
    assert salida.read_text() == "completo=si\n" and "5 de 5 subidos" in capsys.readouterr().out


def test_subir_un_asset_sin_terminar_cuenta_como_faltante(tmp_path):
    ya = [asset(n, i + 1) for i, n in enumerate(NOMBRES)]
    ya[1]["state"] = "starter"
    _, gh, drive, _, _ = correr(tmp_path, api=Api(assets=ya))
    assert drive.pedidas == [NOMBRES[1]] and gh.subidas() == [NOMBRES[1]]


def test_subir_si_gh_falla_sigue_con_las_demas_y_sale_1(tmp_path, capsys):
    codigo, gh, drive, salida, _ = correr(tmp_path, api=Api(release=False), gh=Gh(falla_en={"fm27-eric-zzeta.mp4"}))
    out = capsys.readouterr().out
    assert codigo == 1 and len(drive.pedidas) == 5 and len(gh.subidas()) == 5
    assert "::error::fm27-eric-zzeta.mp4" in out and "4 de 5 subidos" in out
    assert salida.read_text() == "completo=no\n"


def test_subir_si_no_se_puede_crear_el_release_sale_1_sin_bajar_de_drive(tmp_path):
    codigo, gh, drive, _, _ = correr(tmp_path, api=Api(release=False), gh=Gh(falla_en={"release create"}))
    assert codigo == 1 and drive.pedidas == []


def test_subir_rechaza_un_video_que_ffprobe_no_mide(tmp_path, capsys):
    gh, drive = Gh(), Drive()
    codigo = release.orden_subir("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=ENTORNO, transporte=Api(release=False),
                                 ejecutor=gh, bajar_drive=drive, sondear=lambda r: 0.0, carpeta=tmp_path / "t")
    out = capsys.readouterr().out
    assert codigo == 0 and gh.subidas() == [] and out.count("::warning::") == 5


def test_el_token_no_sale_en_ninguna_linea(tmp_path, capsys):
    correr(tmp_path, api=Api(release=False), gh=Gh(falla_en={"fm27-daro.mp4"}))
    assert TOKEN not in capsys.readouterr().out


# ---------------------------------------------------------------- faltan y verificar

def test_faltan_lista_los_que_no_estan(capsys):
    r = release.orden_faltan("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=ENTORNO, transporte=Api(assets=[asset()]))
    out = capsys.readouterr().out
    assert r == 0 and "faltan 4 de 5" in out and "fm27-daro.mp4" not in out.split(":", 2)[2]
    assert release.orden_faltan("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=ENTORNO, transporte=Api(release=False)) == 0
    assert "faltan 5 de 5" in capsys.readouterr().out


def test_verificar_sale_1_si_alguno_no_baja(tmp_path, capsys):
    api = Api(assets=[asset(n, i + 1) for i, n in enumerate(NOMBRES)])
    ok = release.orden_verificar("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=ENTORNO, transporte=api,
                                 abrir=lambda u: Resp(), sondear=lambda r: 12.0)
    assert ok == 0 and "5 de 5 verificados" in capsys.readouterr().out
    mal = release.orden_verificar("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=ENTORNO,
                                  transporte=Api(assets=[asset(n, i + 1) for i, n in enumerate(NOMBRES[:4])]),
                                  abrir=lambda u: Resp(), sondear=lambda r: 12.0)
    assert mal == 1 and "::error::fm27-tio-pedro.mp4" in capsys.readouterr().out


# ---------------------------------------------------------------- descargar.descargar

def _ficha():
    return next(f for f in fuentes.cargar_fuentes(RAIZ_REAL) if f["id"] == "sega-fm27")


def _candidato(url):
    return SimpleNamespace(url=url, origen="brief", pilar="momentos", id_origen="x")


def _preparar(monkeypatch, tmp_path):
    monkeypatch.setattr(d.puertas, "anotar_visto", lambda *a, **k: None)
    monkeypatch.setattr(d, "sondear_video", lambda r: {"duracion": 20.0})


def test_descargar_con_url_de_release_y_sin_abrir_usa_release_bajar(monkeypatch, tmp_path):
    _preparar(monkeypatch, tmp_path)
    llamadas = []

    def falso(url, destino, **kw):
        llamadas.append(url)
        destino.write_bytes(b"x")
        return destino
    monkeypatch.setattr(release, "bajar", falso)
    monkeypatch.setattr(d, "bajar_https", lambda *a, **k: pytest.fail("no debe tocar Drive"))
    with d.descargar(tmp_path, _candidato(URL), _ficha() | {"material": [URL]}, date(2026, 11, 20)) as r:
        assert r.duracion == 20.0
    assert llamadas == [URL]


def test_descargar_con_abrir_inyectado_no_usa_release(monkeypatch, tmp_path):
    _preparar(monkeypatch, tmp_path)
    monkeypatch.setattr(release, "bajar", lambda *a, **k: pytest.fail("no debe usar el release"))
    with d.descargar(tmp_path, _candidato(URL), _ficha() | {"material": [URL]}, date(2026, 11, 20),
                     abrir=lambda u: Resp(tipo="video/mp4")) as r:
        assert r.duracion == 20.0


def test_descargar_con_otra_url_sigue_el_camino_de_siempre(monkeypatch, tmp_path):
    _preparar(monkeypatch, tmp_path)
    otra = "https://ejemplo.org/v.mp4"
    monkeypatch.setattr(release, "bajar", lambda *a, **k: pytest.fail("no es de release"))
    visto = []
    monkeypatch.setattr(d, "bajar_https", lambda url, destino, **k: visto.append(url) or destino.write_bytes(b"x") or destino)
    with d.descargar(tmp_path, _candidato(otra), _ficha() | {"material": [otra]}, date(2026, 11, 20)):
        pass
    assert visto == [otra]


# ---------------------------------------------------------------- validar y ficha real

def _con_ficha(repo, editar_yaml, fn):
    editar_yaml(repo, "fuentes.yml", lambda datos: fn(next(f for f in datos if f["id"] == "sega-fm27")))
    return validar.validar(repo).errores


def test_validar_rechaza_drive_url_ajena_y_url_de_github_que_no_es_release(repo, editar_yaml):
    e = _con_ficha(repo, editar_yaml, lambda f: f["material"][0].update(drive_url="https://evil.example.com/x"))
    assert any("material[0]: drive_url debe empezar por" in x for x in e)
    e = _con_ficha(repo, editar_yaml, lambda f: f["material"][1].update(url="https://github.com/a/b/blob/main/x.mp4"))
    assert any("material[1]: url de github.com debe ser un release" in x for x in e)


def test_validar_en_verde_con_la_ficha_de_release(repo):
    assert validar.validar(repo).errores == []


def test_la_ficha_real_apunta_al_release_y_conserva_drive():
    f = _ficha()
    nombres = [release.partes(m["url"])[2] for m in f["material"]]
    assert nombres == NOMBRES
    assert all(release.partes(m["url"])[:2] == (REPO, TAG) and "drive_url" in m for m in f["material"])
    assert "fábrica no toca Drive" in f["notas"]


def test_el_workflow_no_usa_otros_secretos_ni_set_x():
    t = (RAIZ_REAL / ".github" / "workflows" / "release-fm27.yml").read_text(encoding="utf-8")
    assert "secrets." not in t and "set -x" not in t and "github.token" in t


def test_cli_sin_token_sale_1(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert release.main(["faltan", "--ficha", "sega-fm27", "--tag", TAG, "--raiz", str(RAIZ_REAL)]) == 1
    assert "sin GITHUB_TOKEN" in capsys.readouterr().out


# ---------------------------------------------------------------- ronda 1

def test_un_asset_ajeno_no_cuenta_para_completo(tmp_path, capsys):
    ya = [asset(n, i + 1) for i, n in enumerate(NOMBRES[:4])] + [asset("otra-cosa.mp4", 99)]
    codigo, gh, drive, salida, _ = correr(tmp_path, api=Api(assets=ya))
    assert codigo == 0 and drive.pedidas == [NOMBRES[4]]
    assert salida.read_text() == "completo=si\n" and "5 de 5 subidos" in capsys.readouterr().out
    # con 4 de la ficha + 1 ajeno y Drive caído: 4 de 5, no completo
    tmp2 = tmp_path / "otro"
    codigo, _, _, salida, _ = correr(tmp2, api=Api(assets=ya), drive=Drive(cuota={NOMBRES[4]}))
    assert salida.read_text() == "completo=no\n" and "4 de 5 subidos" in capsys.readouterr().out


def test_drive_octet_stream_se_presenta_como_video_solo_en_drive():
    for nombre, esperado in (("fm27-habemus-gamer.mov", "video/quicktime"), ("fm27-daro.mp4", "video/mp4")):
        a = release._abrir_drive(nombre, lambda u: Resp(tipo="application/octet-stream"))
        with a(f"https://{d.DRIVE}/download?id=X") as r:
            assert r.headers["Content-Type"] == esperado and r.read(5) == CONTENIDO[:5]
        with a("https://otro.example.com/x") as r:      # otro host: no se toca
            assert r.headers["Content-Type"] == "application/octet-stream"
    a = release._abrir_drive("x.mp4", lambda u: Resp(tipo="text/html"))
    with a(f"https://{d.DRIVE}/download?id=X") as r:     # html sigue siendo html (aviso de virus)
        assert r.headers["Content-Type"] == "text/html"


def test_bajar_https_de_drive_acepta_el_mov_octet_stream(tmp_path):
    a = release._abrir_drive("fm27-habemus-gamer.mov", lambda u: Resp(tipo="application/octet-stream"))
    r = d.bajar_https(f"https://{d.DRIVE}/download?id=X&export=download&confirm=t", tmp_path / "v.mov", abrir=a)
    assert r.read_bytes() == CONTENIDO


def test_subir_pasa_el_envoltorio_a_bajar_https(tmp_path, monkeypatch):
    vistos = []

    def falso(url, destino, *, abrir=None, **k):
        vistos.append(abrir is not None)
        destino.write_bytes(CONTENIDO)
        return destino
    monkeypatch.setattr(d, "bajar_https", falso)
    tmp_path.mkdir(exist_ok=True)
    release.orden_subir("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=ENTORNO, transporte=Api(assets=[]), ejecutor=Gh(),
                        sondear=lambda r: 5.0, carpeta=tmp_path / "t")
    assert vistos == [True] * 5


def test_un_oserror_de_disco_no_aborta_el_bucle(tmp_path, capsys):
    llamadas = []

    def sondear(ruta):
        llamadas.append(ruta.name)
        if len(llamadas) == 1:
            raise OSError("disco")
        return 9.0
    gh = Gh()
    codigo = release.orden_subir("sega-fm27", TAG, raiz=RAIZ_REAL, entorno=ENTORNO, transporte=Api(assets=[]),
                                 ejecutor=gh, bajar_drive=Drive(), sondear=sondear, carpeta=tmp_path / "t")
    assert codigo == 0 and len(gh.subidas()) == 4 and capsys.readouterr().out.count("::warning::") == 1


def test_repo_en_otra_capitalizacion_se_acepta_y_punto_se_rechaza():
    entorno = {**ENTORNO, "GITHUB_REPOSITORY": REPO.upper()}
    assert release.resolver(URL, entorno=entorno, transporte=Api()).url_firmada == FIRMADA
    assert not release.es_url_release(f"https://github.com/{REPO}/releases/download/./x.mp4")
    assert not release.es_url_release(f"https://github.com/./granja-clips/releases/download/{TAG}/x.mp4")


def test_faltan_sin_material_de_ese_tag_sale_0(capsys):
    assert release.orden_faltan("sega-fm27", "otro-tag", raiz=RAIZ_REAL, entorno=ENTORNO, transporte=Api()) == 0
    assert "nada que faltar" in capsys.readouterr().out


def test_vistos_con_la_url_de_drive_bloquea_al_candidato_del_release(repo):
    from fabrica import puertas
    from fabrica.detectar import detectar
    from test_n8 import AHORA
    ficha = next(f for f in fuentes.cargar_fuentes(repo) if f["id"] == "sega-fm27")
    cands = {c.creador: c for c in detectar(repo, AHORA, None) if c.fuente_id == "sega-fm27"}
    eric, daro = cands["ERIC ZZETA"], cands["DARO"]
    drive_eric = fuentes.entrada_material(ficha, url=eric.url)["drive_url"]
    assert puertas.puerta_no_repetido(repo, eric)[0] and puertas.puerta_no_repetido(repo, daro)[0]
    puertas.anotar_visto(repo, drive_eric)
    assert not puertas.puerta_no_repetido(repo, eric)[0]
    assert puertas.puerta_no_repetido(repo, daro)[0]
    puertas.anotar_visto(repo, daro.url)                   # y la URL nueva sigue valiendo
    assert not puertas.puerta_no_repetido(repo, daro)[0]

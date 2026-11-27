"""Video largo semanal: selección, receta, render horizontal, memes, armado, puerta y subida a YouTube."""
import io
import json
import shutil
import subprocess
import urllib.error
from datetime import date, datetime
from pathlib import Path

import pytest

from fabrica import recetas
from fabrica.editar import estilo
from fabrica.editar.marca import Marca
from fabrica.largo import __main__ as cli
from fabrica.largo import armar, horizontal, memes, puerta, seleccion, youtube
from fabrica.pausa import ZONA

hay_ffmpeg = shutil.which("ffmpeg") is not None
necesita_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")


# ---------------------------------------------------------------- recetas

def test_receta_guarda_tramo_lineas_y_origen_sin_rutas(repo):
    class C:
        origen, url, titulo, duracion_s = "twitch", "https://clips.twitch.tv/Abc", "t", 30.0
    plan = {"origen": {"archivo": "/tmp/x.mp4", "sha256": "f" * 64, "inicio": 3.5, "duracion": 12.0,
                       "tramos": [[0, 5], [6, 12]]},
            "lineas": [{"t0": 0.5, "t1": 2.0, "frase": "Hola", "voz": "streamer"}], "toma": {"escena": "INT."}}
    toma = {"numero": 21, "gancho": "g", "creador": "twitch.tv/x", "id_origen": "Abc", "duracion_s": 11.0}
    rec = recetas.receta_de(toma, {"id": "t-x", "tipo": "T", "credito": "twitch.tv/x"}, plan, C(), "2026-11-26")
    assert rec["tramo"] == {"inicio": 3.5, "duracion": 12.0, "tramos": [[0, 5], [6, 12]]}
    assert rec["origen"]["url"] == "https://clips.twitch.tv/Abc" and "/tmp/" not in json.dumps(rec)
    recetas.guardar(repo, rec)
    recetas.guardar(repo, rec)                       # la misma toma no se repite
    assert list(recetas.por_numero(repo)) == [21]


def test_receta_marca_historia_y_subtitulos_del_clip():
    rec = recetas.receta_de({"numero": 1}, {}, {"historia": {"proveedor": "gemini"}, "subtitulos": "del clip"})
    assert rec["historia"] is True and rec["subtitulos_del_clip"] is True
    assert horizontal.lineas_de({**rec, "lineas": [{"t0": 0, "t1": 2, "frase": "x"}]}, 0, 10) == []


# ---------------------------------------------------------------- selección

def _libro(repo, filas):
    (repo / "estado").mkdir(exist_ok=True)
    (repo / "estado" / "buffer-envios.json").write_text(json.dumps(filas), encoding="utf-8")


def _envio(n, plat, estado, dia="2026-11-26", ficha="t-aaron", creador="twitch.tv/aaron", campana=""):
    return {"toma": n, "plataforma": plat, "estado": estado, "dueAt": f"{dia}T18:00:00.000Z",
            "datos": {"ficha_id": ficha, "toma": {"numero": n, "gancho": f"g{n}", "caption": f"c{n}",
                                                  "creador": creador, "id_origen": f"Clip{n}", "campana": campana,
                                                  "duracion_s": 12.0, "hablante": "AARON"}}}


def test_candidatas_solo_las_que_no_salieron_en_shorts(repo):
    _libro(repo, [
        _envio(1, "shorts", "creado"), _envio(1, "tiktok", "creado"),                 # salió en Shorts
        _envio(2, "tiktok", "creado"), _envio(2, "reels", "creado"),                  # sin Short (cupo)
        _envio(3, "shorts", "rechazado"), _envio(3, "tiktok", "creado"),              # Short rechazado
        _envio(4, "tiktok", "creado", campana="sega"),                                # campaña: fuera
        _envio(5, "tiktok", "creado", creador="twitch.tv/ibai", ficha="t-ibai"),      # vetado
        _envio(6, "tiktok", "creado", ficha="propio-x"),                              # tipo desconocido
        _envio(7, "tiktok", "creado", dia="2026-11-10"),                              # fuera de la semana
    ])
    entran, fuera = seleccion.candidatas(repo, date(2026, 11, 20), date(2026, 11, 29))
    assert [e.numero for e in entran] == [2, 3]
    assert entran[0].motivo_short == "cupo" and entran[1].motivo_short == "rechazado"
    assert any("campaña" in f for f in fuera) and any("vetado" in f for f in fuera)
    assert any("toma 6" in f for f in fuera)


def test_candidatas_no_repiten_tomas_de_otro_episodio(repo):
    _libro(repo, [_envio(2, "tiktok", "creado"), _envio(3, "tiktok", "creado")])
    (repo / "estado" / "largos.json").write_text(json.dumps([{"episodio": 1, "estado": "subido", "tomas": [2]}]))
    entran, fuera = seleccion.candidatas(repo)
    assert [e.numero for e in entran] == [3] and any("ya salió" in f for f in fuera)


def test_orden_cuenta_atras_por_vistas():
    es = [seleccion.Elegida(n, date(2026, 11, 26), "t-a", "twitch.tv/a", f"C{n}", vistas=v)
          for n, v in ((1, 50), (2, 900), (3, 10))]
    assert [e.numero for e in seleccion.ordenar(es, {"orden": "vistas"})] == [3, 1, 2]
    assert [e.numero for e in seleccion.ordenar(es, {"orden": "fecha"})] == [1, 2, 3]


# ---------------------------------------------------------------- horizontal

def test_recorte_16x9_entero_y_centrado():
    assert horizontal.recorte_16x9(1280, 720) == {"x": 0, "y": 0, "ancho": 1280, "alto": 720}
    r = horizontal.recorte_16x9(1440, 720)                           # más ancho: se recortan los lados
    assert (r["ancho"], r["alto"], r["x"]) == (1280, 720, 80)
    r = horizontal.recorte_16x9(1080, 1920)                          # vertical: franja central
    assert r["ancho"] == 1080 and r["alto"] == 608 and r["y"] % 2 == 0


def test_tramo_con_contexto_sin_y_con_silencios():
    t = horizontal.tramo_con_contexto({"tramo": {"inicio": 3.0, "duracion": 10.0}}, 30.0, 1.0)
    assert (t.inicio, t.duracion, t.tramos, t.desplazamiento) == (2.0, 12.0, None, 1.0)
    t = horizontal.tramo_con_contexto({"tramo": {"inicio": 0.4, "duracion": 10.0}}, 10.8, 1.0)
    assert (t.inicio, t.duracion, t.desplazamiento) == (0.0, 10.8, 0.4)      # la fuente no tiene más
    t = horizontal.tramo_con_contexto({"tramo": {"inicio": 3.0, "duracion": 12.0, "tramos": [[0, 5], [6, 12]]}},
                                      30.0, 1.0)
    assert t.tramos == [(0.0, 6.0), (7.0, 14.0)] and t.duracion == 13.0 and t.inicio == 2.0


def test_lineas_se_corren_y_no_pisan_el_cierre():
    rec = {"lineas": [{"t0": 0.5, "t1": 2.0, "frase": "uno"}, {"t0": 9.0, "t1": 12.0, "frase": "dos"},
                      {"t0": 3.0, "t1": 3.1, "frase": "corta"}]}
    ls = horizontal.lineas_de(rec, 1.0, 12.0)
    assert [(ln.t0, ln.t1, ln.frase) for ln in ls] == [(1.5, 3.0, "uno"), (10.0, 11.7, "dos")]


def test_marca_horizontal_no_toca_la_vertical():
    m = Marca.cargar()
    mh = horizontal.marca_horizontal(m, seleccion.config())
    assert (mh.ancho, mh.alto) == (1920, 1080) and (m.ancho, m.alto) == (1080, 1920)
    assert mh.pc["dialogo"]["borde_inferior"] == 1010 and m.pc["dialogo"]["borde_inferior"] == 1590
    assert mh.pc["dialogo"]["estilo"] == "contorno"                     # lo demás se hereda


def fuente_sintetica(ruta, seg=16, ancho=1280, alto=720, tono=330):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={ancho}x{alto}:rate=30:duration={seg}",
                    "-f", "lavfi", "-i", f"sine=frequency={tono}:duration={seg}", "-f", "lavfi", "-i",
                    f"sine=frequency=900:duration=0.5", "-filter_complex",
                    f"[2:a]adelay=9000|9000,volume=8[g];[1:a][g]amix=inputs=2:duration=first[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(ruta)], check=True)
    return ruta


RECETA = {"numero": 21, "tramo": {"inicio": 2.0, "duracion": 12.0},
          "lineas": [{"t0": 0.5, "t1": 3.0, "nombre": "AARON", "frase": "Hacer clip de eso.", "voz": "streamer"}],
          "ficha": {"id": "t-aaron", "tipo": "T", "credito": "twitch.tv/aaron"}}


@necesita_ffmpeg
def test_render_segmento_horizontal_con_fila_subtitulo_y_puesto(tmp_path):
    m = Marca.cargar()
    cfg = seleccion.config()
    mh = horizontal.marca_horizontal(m, cfg)
    f = fuente_sintetica(tmp_path / "f.mp4")
    plan = horizontal.render_segmento(mh, RECETA, 21, "twitch.tv/aaron", f, tmp_path / "s.mp4", tmp_path / "w", cfg,
                                      puesto=7)
    info = estilo.sondear(tmp_path / "s.mp4")
    assert (info["video"]["width"], info["video"]["height"]) == (1920, 1080)
    assert abs(info["duracion"] - 14.0) < 0.1 and plan["lineas"] == 1
    assert {c["tipo"] for c in plan["capas"]} >= {"fila", "mosca", "puesto", "dialogo_0"}
    pico = horizontal.pico_de_audio(tmp_path / "s.mp4", 3, 13)
    assert pico is not None and 7.5 <= pico <= 8.5                    # el golpe de 9 s de la fuente, menos 1 s de inicio


# ---------------------------------------------------------------- memes

HTML = """<div class="instant"><button class="small-button" onclick="play('/media/sounds/faaah.mp3', 'l1', 'faaah-1')"></button>
<a href="/es/instant/faaah-63455/" class="instant-link">FAAAH</a></div>
<div class="instant"><button onclick="play('/media/sounds/cat-laugh.mp3', 'l2', 'c')"></button>
<a href="/es/instant/cat-laugh-15761/" class="instant-link">cat laugh &amp; meme</a></div>
<div class="instant"><button onclick="play('/media/sounds/faaah.mp3', 'l3', 'x')"></button>
<a href="/es/instant/faaah-dup/" class="instant-link">dup</a></div>"""


def test_leer_populares_de_myinstants():
    ls = memes.leer_populares(HTML, "gt")
    assert [(m.nombre, m.puesto) for m in ls] == [("FAAAH", 1), ("cat laugh & meme", 2)]
    assert ls[0].mp3 == "https://www.myinstants.com/media/sounds/faaah.mp3"
    assert ls[1].pagina == "https://www.myinstants.com/es/instant/cat-laugh-15761/"
    assert memes.url_populares("us").endswith("/en/index/us/") and memes.url_populares("mx").endswith("/es/index/mx/")


class Respuesta(io.BytesIO):
    def __init__(self, cuerpo, estado=200, cabeceras=None):
        super().__init__(cuerpo if isinstance(cuerpo, bytes) else cuerpo.encode())
        self.status, self.headers = estado, cabeceras or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gemini(respuesta: dict):
    texto = json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(respuesta)}]}}]})
    return lambda req, timeout=None: Respuesta(texto)


def test_respaldo_por_la_pagina_de_cada_sonido():
    indice = '<a href="/es/instant/faaah-63455/" class="instant-link">FAAAH</a>'
    pagina = '<a href="https://www.myinstants.com/media/sounds/faaah.mp3" download>Descargar MP3</a>'
    abrir = lambda req, timeout=None: Respuesta(indice if "/index/" in req.full_url else pagina)
    ls = memes.populares(["gt"], abrir=abrir, salida=lambda *_: None)
    assert [(m.nombre, m.mp3) for m in ls] == [("FAAAH", "https://www.myinstants.com/media/sounds/faaah.mp3")]


def test_populares_suma_paises_y_filtrar_con_gemini():
    paginas = {"gt": HTML, "mx": HTML.replace("cat-laugh", "otro")}
    abrir = lambda req, timeout=None: Respuesta(paginas[req.full_url.rstrip("/").rsplit("/", 1)[-1]])
    ls = memes.populares(["gt", "mx"], abrir=abrir, salida=lambda *_: None)
    assert ls[0].nombre == "FAAAH" and ls[0].paises == ["gt", "mx"]
    elegidos = memes.filtrar(ls, "clave", ["m"], 5,
                             abrir=_gemini({"elegidos": [{"i": 0, "categoria": "fail"}, {"i": 9, "categoria": "risa"},
                                                         {"i": 1, "categoria": "inventada"}]}),
                             salida=lambda *_: None)
    assert [(m.nombre, m.categoria) for m in elegidos] == [("FAAAH", "fail")]
    assert memes.filtrar(ls, "", ["m"], 5) == []                       # sin Gemini, sin memes


def test_asignar_y_espaciar():
    bib = [memes.Meme("a", "u", categoria="risa"), memes.Meme("b", "u", categoria="fail")]
    clips = [{"texto": "x"}] * 4
    d = memes.asignar(clips, bib, "k", ["m"], abrir=_gemini({"asignados": [{"clip": 0, "efecto": 1},
                                                                            {"clip": 1, "efecto": None},
                                                                            {"clip": 2, "efecto": 0}]}),
                      salida=lambda *_: None)
    assert {i: m.nombre for i, m in d.items()} == {0: "b", 2: "a"}
    assert set(memes.espaciar({0: bib[0], 1: bib[1], 2: bib[0]}, [0, 12, 25], 20)) == {0, 2}


def test_momento_del_meme():
    assert armar.momento_meme(12.0, 6.0, 1.5) == 6.15
    assert armar.momento_meme(12.0, 11.8, 1.5) == 10.6                 # no pisa el cierre
    assert armar.momento_meme(12.0, None, 1.5) is None
    assert armar.momento_meme(1.5, 0.5, 1.0) is None                   # no cabe


@necesita_ffmpeg
def test_normalizar_y_mezclar_meme(tmp_path):
    crudo = tmp_path / "m.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-f", "lavfi", "-i",
                    "sine=frequency=500:duration=4", "-filter_complex", "[0:a]atrim=0:0.5[s];[s][1:a]concat=n=2:v=0:a=1",
                    str(crudo)], check=True)
    wav = tmp_path / "m.wav"
    subprocess.run(memes.orden_normalizar(crudo, wav, 2.5, -18), check=True)
    assert 2.3 <= memes.duracion_audio(wav) <= 2.55                     # sin el silencio inicial y recortado
    seg = fuente_sintetica(tmp_path / "s.mp4", seg=6)
    final = tmp_path / "f.mp4"
    subprocess.run(armar.orden_audio_final(seg, final, meme=wav, t_meme=2.0), check=True)
    assert abs(estilo.sondear(final)["duracion"] - 6.0) < 0.1


# ---------------------------------------------------------------- armado y puerta

def test_capitulos_juntan_los_cortos_y_validan():
    clips = [armar.Clip(n, p, Path("x"), d, "twitch.tv/a", "A", "cap") for n, p, d in ((1, 3, 12), (2, 2, 6), (3, 1, 14))]
    caps = armar.capitulos(5.0, clips)
    assert [(round(t), s[:3]) for t, s in caps] == [(0, "#3 "), (17, "#2 "), (37, "Cré")]   # el #1 (6 s) va con el #2
    assert all(b - a >= 10 for (a, _), (b, _) in zip(caps, caps[1:]))
    assert armar.capitulos(12.0, clips)[:2] == [(0.0, "Intro"), (12.0, "#3 A: cap")]
    assert puerta.capitulos_validos([(0, "a"), (12, "b")])[0] is False
    assert puerta.capitulos_validos([(0, "a"), (12, "b"), (30, "c")])[0] is True
    assert armar.mmss(75) == "01:15" and armar.mmss(3725) == "1:02:05"


@necesita_ffmpeg
def test_armar_episodio_de_punta_a_punta_sin_memes(repo, monkeypatch, tmp_path):
    (repo / "PAUSA").unlink()
    filas, recs = [], []
    for n in range(1, 7):
        filas += [_envio(n, "tiktok", "creado"), _envio(n, "reels", "creado")]
        recs.append({**RECETA, "numero": n, "fecha": "2026-11-26",
                     "origen": {"tipo": "twitch", "url": f"https://clips.twitch.tv/Clip{n}"}})
    _libro(repo, filas)
    for r in recs:
        recetas.guardar(repo, r)
    cfg = seleccion.config(repo)
    cfg.update({"min_clips": 6, "duracion_min_s": 60})
    monkeypatch.setattr(seleccion, "config", lambda raiz=None: cfg)
    fuentes = tmp_path / "fuentes"
    fuentes.mkdir()

    def bajar_falso(url, carpeta, ejecutor=None):
        carpeta.mkdir(parents=True, exist_ok=True)
        return fuente_sintetica(carpeta / "clip.mp4", tono=300 + 10 * int(url[-1]))
    monkeypatch.setattr("fabrica.descargar.bajar_twitch", bajar_falso)
    monkeypatch.setattr(puerta, "sonoridad", lambda mp4: -16.0)
    carpeta = tmp_path / "ep"
    ahora = datetime(2026, 11, 29, 10, 0, tzinfo=ZONA)
    cod = cli.armar(repo, carpeta, date(2026, 11, 23), date(2026, 11, 29), con_memes=False, ahora=ahora,
                    env={}, salida=lambda *_: None)
    plan = json.loads((carpeta / "episodio.json").read_text(encoding="utf-8"))
    assert cod == 0, plan["puerta"]
    assert [c["puesto"] for c in plan["clips"]] == [6, 5, 4, 3, 2, 1]
    info = estilo.sondear(carpeta / "episodio.mp4")
    assert (info["video"]["width"], info["video"]["height"]) == (1920, 1080)
    assert abs(info["duracion"] - plan["duracion_esperada"]) <= 1.0
    assert "https://twitch.tv/aaron" in plan["descripcion"] and plan["descripcion"].count("\n00:") >= 1
    ep = seleccion.episodios(repo)
    assert ep[-1]["estado"] == "armado" and ep[-1]["tomas"] == [1, 2, 3, 4, 5, 6]
    assert (carpeta / "miniatura.png").is_file()


def test_armar_espera_con_pocas_tomas(repo):
    (repo / "PAUSA").unlink()
    _libro(repo, [_envio(1, "tiktok", "creado")])
    salida = []
    assert cli.armar(repo, repo / "ep", None, None, env={}, salida=salida.append) == 0
    assert any("espera" in s for s in salida) and not (repo / "ep" / "episodio.json").exists()


def test_armar_no_corre_con_pausa(repo):
    salida = []
    assert cli.armar(repo, repo / "ep", None, None, env={}, salida=salida.append) == 0
    assert any("PAUSA" in s for s in salida)


# ---------------------------------------------------------------- YouTube

ENV = {"YT_CLIENT_ID": "id", "YT_CLIENT_SECRET": "sec", "YT_REFRESH_TOKEN": "ref"}


def test_cliente_exige_los_tres_secretos():
    with pytest.raises(youtube.ErrorYouTube, match="YT_REFRESH_TOKEN"):
        youtube.Cliente({"YT_CLIENT_ID": "a", "YT_CLIENT_SECRET": "b"})


def test_subida_reanudable_en_trozos(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "TROZO", 4)
    mp4 = tmp_path / "e.mp4"
    mp4.write_bytes(b"0123456789")
    llamadas = []

    def abrir(req, timeout=None):
        llamadas.append((req.get_method(), req.full_url, dict(req.header_items())))
        if req.full_url == youtube.URL_TOKEN:
            assert b"refresh_token=ref" in req.data
            return Respuesta(json.dumps({"access_token": "tok"}))
        assert req.get_header("Authorization") == "Bearer tok"
        if req.get_method() == "POST":
            meta = json.loads(req.data)
            assert meta["status"]["privacyStatus"] == "public" and meta["snippet"]["categoryId"] == "20"
            return Respuesta(b"", cabeceras={"Location": "https://subida/sesion"})
        rango = req.get_header("Content-range")
        if rango.startswith("bytes 8-"):
            return Respuesta(json.dumps({"id": "VID123"}), estado=200)
        fin = int(rango.split()[1].split("-")[1].split("/")[0])
        raise urllib.error.HTTPError(req.full_url, 308, "Resume", {"Range": f"bytes=0-{fin}"}, io.BytesIO(b""))

    cli_yt = youtube.Cliente(ENV, abrir=abrir, dormir=lambda s: None)
    vid = cli_yt.subir(mp4, youtube.snippet_de("t", "d", {"categoria": 20}), youtube.status_de({}))
    assert vid == "VID123"
    rangos = [h.get("Content-range") for m, u, h in llamadas if m == "PUT"]
    assert rangos == ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"]
    assert all("=ref" not in u and "tok&" not in u and not u.endswith("=tok") for _, u, _ in llamadas)  # nada secreto en una URL


def test_token_revocado_da_error_claro():
    def abrir(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(b'{"error": "invalid_grant"}'))
    with pytest.raises(youtube.ErrorYouTube, match="invalid_grant"):
        youtube.Cliente(ENV, abrir=abrir).token()


def test_subir_no_sube_con_puerta_roja(tmp_path, repo):
    (tmp_path / "episodio.json").write_text(json.dumps({"puerta": [{"punto": "x", "ok": False, "detalle": ""}]}))
    salida = []
    assert cli.subir(repo, tmp_path, env=ENV, salida=salida.append) == 1 and "verde" in salida[0]


def test_no_repite_episodio_en_la_misma_semana(repo):
    (repo / "PAUSA").unlink()
    (repo / "estado").mkdir(exist_ok=True)
    (repo / "estado" / "largos.json").write_text(json.dumps([{"episodio": 1, "estado": "subido", "tomas": [1],
                                                               "subido": "2026-11-29T10:40-06:00"}]))
    salida = []
    ahora = datetime(2026, 11, 30, 0, 20, tzinfo=ZONA)
    assert cli.armar(repo, repo / "ep", None, None, ahora=ahora, env={}, salida=salida.append) == 0
    assert any("ya salió" in s for s in salida)
    assert cli.numero_episodio(repo) == 2

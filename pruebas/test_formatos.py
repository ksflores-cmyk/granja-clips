"""Tarea #7: «Mejores momentos» y «Mejores reacciones» (compilados). Sin red; las de ffmpeg se saltan sin ffmpeg."""
import copy
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import formatos, fuentes, validar
from fabrica.detectar import Candidato
from fabrica.editar import compilado, estilo, muestras
from fabrica.editar.componer import Linea
from fabrica.editar.lienzo import Toma
from fabrica.editar.marca import Marca
from fabrica.publicar import MAX_TEXTO, Descartada, contar_emojis
from fabrica.registro import ZONA

hay_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
necesita_ffmpeg = pytest.mark.skipif(not hay_ffmpeg, reason="ffmpeg no está instalado")

AHORA = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)          # 12:00 del 20 nov en Guatemala
HOY = AHORA.astimezone(ZONA).date()


@pytest.fixture(scope="module")
def marca():
    return Marca.cargar(RAIZ_REAL)


@pytest.fixture(scope="module")
def cfg():
    return formatos.cargar_config(RAIZ_REAL)


@pytest.fixture(scope="module")
def temporada():
    return yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- configuración

def test_la_config_del_repo_se_acepta(cfg):
    assert cfg["pilar"] == "compilados" and cfg["segmentos"] == [2, 3] and cfg["duracion_s"] == [30, 60]
    assert set(cfg["formatos"]) == {"mejores-momentos", "mejores-reacciones"}
    f = formatos.formato_con_limites(cfg, "mejores-momentos")
    assert f["id"] == "mejores-momentos" and f["duracion_s"] == [30, 60] and f["gancho"] == "Mejores momentos del día"


def _cambiar(repo, rel, fn):
    ruta = repo / rel
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    fn(datos)
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")


@pytest.mark.parametrize("nombre,fn,motivo", [
    ("gancho de 7 palabras",
     lambda d: d["formatos"]["mejores-momentos"].update(gancho="Los mejores momentos de todo el día"), "palabras"),
    ("palabra del lápiz fuera del gancho",
     lambda d: d["formatos"]["mejores-momentos"].update(palabra_lapiz="NOCHE"), "palabra_lapiz"),
    ("gancho genérico",
     lambda d: d["formatos"]["mejores-momentos"].update(gancho="Esto cambia todo", palabra_lapiz="TODO"),
     "genérico"),
    ("un solo segmento", lambda d: d.update(segmentos=[1, 3]), "segmentos"),
    ("pilar ausente de temporada.yml", lambda d: d.update(pilar="inventado"), "pilar"),
    ("duración fuera de rango", lambda d: d.update(duracion_s=[30, 90]), "duracion_s"),
    ("hashtag sin #", lambda d: d["formatos"]["mejores-momentos"].update(hashtags=["Streamers"]), "hashtags"),
])
def test_la_config_mala_lanza_valueerror(repo, nombre, fn, motivo):
    _cambiar(repo, "config/formatos.yml", fn)
    with pytest.raises(ValueError, match=motivo):
        formatos.cargar_config(repo)


def test_temporada_tiene_el_pilar_compilados_y_suma_uno(temporada):
    pilares = temporada["pilares"]
    assert pilares["compilados"] > 0
    assert sum(pilares.values()) == pytest.approx(1.0)
    assert validar.main(["--raiz", str(RAIZ_REAL)]) == 0


# ---------------------------------------------------------------- elegir candidatos

def ficha_t(fid="twitch-a", **cambios):
    f = {"id": fid, "tipo": "T", "creador": "twitch.tv/streamers", "twitch_login": "streamers", "origen": "twitch", "estado": "vigente",
         "uso": "produccion", "publicable": True, "vence": None, "pilares": ["momentos", "primeras_veces"],
         "campana": None, "presupuesto_restante": None,
         "reglas": {"duracion_s": [7, 30], "plataformas": ["shorts", "tiktok", "reels"], "permite_descarga": True,
                    "etiquetas_obligatorias": [], "divulgacion": "ninguna"}}
    f.update(cambios)
    return f


def cand(id_origen, creador, vph, *, fuente="twitch-a", creado=None, **cambios):
    c = dict(fuente_id=fuente, origen="twitch", id_origen=id_origen, url=f"https://clips.twitch.tv/{id_origen}",
             titulo="t", creador=creador, idioma="es", juego_id="1867279146", duracion_s=30.0, vistas=10,
             vistas_por_hora=vph, creado_en=creado or AHORA - timedelta(hours=2), vod_offset=None, pilar="momentos")
    c.update(cambios)
    return Candidato(**c)


def ids(lista):
    return [c.id_origen for c in lista]


def test_elegir_ordena_por_vistas_por_hora_y_limita_a_3(cfg):
    cs = [cand("a", "uno", 5), cand("b", "dos", 50), cand("c", "tres", 20), cand("d", "cuatro", 10)]
    assert ids(formatos.elegir_candidatos(cs, [ficha_t()], "mejores-momentos", AHORA, cfg)) == ["b", "c", "d"]


def test_elegir_descarta_otro_dia_en_zona_guatemala(cfg):
    ayer_23_30_gt = datetime(2026, 11, 19, 23, 30, tzinfo=ZONA)           # ya es 20 nov en UTC, pero 19 en GT
    asumido = datetime(2026, 11, 20, 0, 5, tzinfo=ZONA)                   # 20 nov GT: justo pasada la medianoche
    cs = [cand("ayer", "uno", 99, creado=ayer_23_30_gt), cand("hoy1", "dos", 5, creado=asumido),
          cand("hoy2", "tres", 4, creado=asumido), cand("sin-hora", "cuatro", 80, creado_en=None)]
    elegidos = formatos.elegir_candidatos(cs, [ficha_t()], "mejores-momentos", AHORA, cfg)
    assert ids(elegidos) == ["hoy1", "hoy2"]


@pytest.mark.parametrize("ficha", [
    ficha_t(vence="2026-11-01"),                         # vencida
    ficha_t(uso="muestras"),
    ficha_t(tipo="B"), ficha_t(tipo="C"), ficha_t(tipo="D"),
    ficha_t(tipo="A"),                                   # A sin reglas.permite_compilados
    ficha_t(pilares=["primeras_veces"]),                 # el pilar de «Mejores momentos» no está en la ficha
    ficha_t(estado="pausada"), ficha_t(publicable=False),
])
def test_elegir_descarta_fichas_no_aptas(cfg, ficha):
    malas = [cand("m1", "uno", 10, fuente="twitch-a"), cand("m2", "dos", 9, fuente="twitch-a")]
    assert formatos.elegir_candidatos(malas, [ficha], "mejores-momentos", AHORA, cfg) == []


def test_elegir_acepta_ficha_a_solo_con_permite_compilados(cfg):
    a = ficha_t(tipo="A")
    a["reglas"] = {**a["reglas"], "permite_compilados": True}
    cs = [cand("m1", "uno", 10), cand("m2", "dos", 9)]
    assert len(formatos.elegir_candidatos(cs, [a], "mejores-momentos", AHORA, cfg)) == 2


def test_elegir_tope_por_creador_y_sin_repetidos(cfg):
    cs = [cand("a1", "Ibai", 90), cand("a2", "ibai", 80), cand("a3", " IBAI ", 70), cand("a2", "otro", 60),
          cand("b1", "dos", 10)]
    assert ids(formatos.elegir_candidatos(cs, [ficha_t()], "mejores-momentos", AHORA, cfg)) == ["a1", "a2", "b1"]


def test_elegir_con_menos_de_dos_aptos_devuelve_lista_vacia(cfg):
    assert formatos.elegir_candidatos([cand("solo", "uno", 5)], [ficha_t()], "mejores-momentos", AHORA, cfg) == []
    assert formatos.elegir_candidatos([cand("a", "uno", 5), cand("b", "uno", 5, fuente="otra")], [ficha_t()],
                                      "mejores-momentos", AHORA, cfg) == []


def test_elegir_usa_el_pilar_del_formato(cfg):
    f = ficha_t(pilares=["primeras_veces"])
    cs = [cand("a", "uno", 5), cand("b", "dos", 4)]
    assert formatos.elegir_candidatos(cs, [f], "mejores-momentos", AHORA, cfg) == []
    assert len(formatos.elegir_candidatos(cs, [f], "mejores-reacciones", AHORA, cfg)) == 2


def test_apto_con_muestra_acepta_fichas_de_muestras_y_no_las_de_produccion_de_otros_tipos():
    f = ficha_t(tipo="B", uso="muestras", publicable=False)
    assert not formatos.apto(f, HOY, "momentos")[0]
    assert formatos.apto(f, HOY, "momentos", muestra=True)[0]
    assert not formatos.apto(ficha_t(tipo="D", uso="muestras"), HOY, "momentos", muestra=True)[0]
    assert not formatos.apto(ficha_t(), HOY, "momentos", muestra=True)[0]       # producción no es muestra


# ---------------------------------------------------------------- combinar

def seg_falso(dur, fecha=HOY, creador="a"):
    return formatos.Segmento(fuente=Path("x.mp4"), inicio=0.0, duracion=dur,
                             toma=Toma(1, "Hola mundo", "MUNDO"), lineas=[], ficha=ficha_t(), creador=creador,
                             fecha=fecha, id_origen=f"id{dur}{creador}")


def test_combinar_elige_el_primer_prefijo_que_cae_en_rango(cfg):
    assert len(formatos.combinar([seg_falso(15), seg_falso(15, creador="b"), seg_falso(15, creador="c")], cfg)) == 3
    r = formatos.combinar([seg_falso(25), seg_falso(25, creador="b"), seg_falso(25, creador="c")], cfg)
    assert len(r) == 2 and sum(s.duracion for s in r) == 50
    assert formatos.combinar([seg_falso(12), seg_falso(12, creador="b")], cfg) is None
    r = formatos.combinar([seg_falso(28), seg_falso(28, creador="b"), seg_falso(28, creador="c")], cfg)
    assert len(r) == 2 and sum(s.duracion for s in r) == 56
    assert formatos.combinar([seg_falso(15), seg_falso(15, fecha=HOY - timedelta(days=1))], cfg) is None
    assert formatos.combinar([], cfg) is None


def test_combinar_deja_margen_en_los_bordes(cfg):
    assert formatos.combinar([seg_falso(30.0), seg_falso(0.05, creador="b")], cfg) is None      # 30.05: pegado al mínimo
    assert formatos.combinar([seg_falso(15.2), seg_falso(15.2, creador="b")], cfg) is not None  # 30.4
    assert formatos.combinar([seg_falso(30.0), seg_falso(30.0, creador="b")], cfg) is None      # 60 exactos: el AAC lo pasa
    assert formatos.combinar([seg_falso(29.96), seg_falso(29.96, creador="b")], cfg) is None    # 59.92
    assert formatos.combinar([seg_falso(29.9), seg_falso(29.9, creador="b")], cfg) is not None  # 59.8


def test_login_real_de_twitch_en_el_credito():
    f = ficha_t(twitch_login="ibai", creador="Ibai Llanos")
    assert formatos.etiqueta_credito(f, "Ibai Llanos") == "twitch.tv/ibai"
    assert formatos.etiqueta_credito(ficha_t(), "twitch.tv/AuronPlay") == "twitch.tv/auronplay"
    assert formatos.etiqueta_credito(ficha_t(creador="twitch.tv/elxokas"), "twitch.tv/elxokas") == "twitch.tv/elxokas"
    assert formatos.etiqueta_credito(ficha_t(creador="otros"), "Auron Play") == "twitch.tv/auronplay"   # último recurso
    assert formatos.etiqueta_credito(f, "Otro Streamer") == "twitch.tv/otrostreamer"


def test_fechas_sin_zona_se_toman_como_hora_de_guatemala(cfg):
    naive_ahora = datetime(2026, 11, 20, 12, 0)                    # 12:00 GT; en UTC del sistema sería otro día/hora
    ayer_naive = datetime(2026, 11, 19, 23, 30)
    hoy_naive = datetime(2026, 11, 20, 0, 5)
    cs = [cand("ayer", "uno", 99, creado=ayer_naive), cand("h1", "dos", 5, creado=hoy_naive),
          cand("h2", "tres", 4, creado=hoy_naive)]
    assert ids(formatos.elegir_candidatos(cs, [ficha_t()], "mejores-momentos", naive_ahora, cfg)) == ["h1", "h2"]
    assert formatos.fila_marcador(compilado_de_texto(), naive_ahora)["hora"] == "12:00"
    assert formatos.hora_local(hoy_naive).utcoffset() == timedelta(hours=-6)


# ---------------------------------------------------------------- tomas

def test_toma_de_segmento_primero_lleva_el_gancho_del_formato(cfg):
    fm = formatos.formato_con_limites(cfg, "mejores-momentos")
    propia = Toma(1, "Ella busca un dragón", "DRAGÓN", escena="INT. CABAÑA — NOCHE")
    f = ficha_t()
    t0 = formatos.toma_de_segmento(propia, fm, 12, 0, "IbaiLlanos", f)
    t1 = formatos.toma_de_segmento(propia, fm, 12, 1, "AuronPlay", f)
    assert (t0.gancho, t0.palabra_lapiz) == ("Mejores momentos del día", "MOMENTOS")
    assert (t1.gancho, t1.palabra_lapiz) == ("Ella busca un dragón", "DRAGÓN")
    assert t0.numero == t1.numero == 12 and t1.escena == "INT. CABAÑA — NOCHE"
    assert t0.credito == "Con: twitch.tv/ibaillanos" and t1.credito == "Con: twitch.tv/auronplay"
    b = formatos.toma_de_segmento(propia, fm, 12, 1, "Blender (Sintel)", ficha_t(tipo="B"))
    assert b.credito == "Con: Blender (Sintel)"


# ---------------------------------------------------------------- textos

def compilado_de_texto(creadores=("ibai", "auron", "grefg"), formato="mejores-momentos"):
    segs = [formatos.Segmento(Path("x.mp4"), 0.0, 14.0, Toma(12, "g", "g"), [], ficha_t(f"t{i}"), c, HOY, f"o{i}")
            for i, c in enumerate(creadores)]
    return formatos.Compilado(formato, segs)


@pytest.mark.parametrize("plataforma", ["shorts", "tiktok", "reels"])
def test_textos_traen_cada_creador_un_emoji_y_el_juego(cfg, temporada, plataforma):
    t = formatos.textos_compilado(compilado_de_texto(), plataforma, temporada, cfg)
    texto = t["texto"]
    assert texto.startswith("Mejores momentos del día en GTA 6\n")
    linea = next(x for x in texto.splitlines() if x.startswith("🎥"))
    assert all(f"twitch.tv/{c}" in linea for c in ("ibai", "auron", "grefg"))
    assert contar_emojis(texto) == 1 and len(texto) <= MAX_TEXTO
    etiquetas = [e for e in t["etiquetas"] if e.startswith("#")]
    assert "#GTA6" in etiquetas and "#MejoresMomentos" in etiquetas
    assert texto.splitlines()[-1] == " ".join(t["etiquetas"])
    if plataforma == "reels":
        assert 3 <= len(etiquetas) <= 5 and "3 momentos de hoy, de 3 streamers." in texto and t["titulo"] is None
    else:
        assert len(etiquetas) <= 3 and "de hoy" not in texto
    assert (t["titulo"] is not None) == (plataforma == "shorts")


def test_textos_de_reacciones_y_hashtags_de_la_temporada(cfg, temporada):
    temp = {**temporada, "hashtags": ["#GTA6", "#GTAVI", "#Rockstar", "#Gaming"]}
    t = formatos.textos_compilado(compilado_de_texto(formato="mejores-reacciones"), "reels", temp, cfg)
    assert t["etiquetas"] == ["#GTA6", "#GTAVI", "#Rockstar", "#Gaming", "#Reacciones"]
    assert "3 reacciones de hoy" in t["texto"] and "Mejores reacciones del día en GTA 6" in t["texto"]
    s = formatos.textos_compilado(compilado_de_texto(), "shorts", temp, cfg)
    # Con tope de 3 y 4 hashtags de temporada, el del formato conserva su lugar (decisión al fusionar #7 con #8).
    assert s["etiquetas"] == ["#GTA6", "#GTAVI", "#MejoresMomentos"]


def test_textos_sin_un_creador_en_la_linea_es_descartada(cfg, temporada, monkeypatch):
    original = formatos._linea_credito
    monkeypatch.setattr(formatos, "_linea_credito", lambda segs, plat: original(segs[:-1], plat))
    with pytest.raises(Descartada, match="línea del crédito"):
        formatos.textos_compilado(compilado_de_texto(), "tiktok", temporada, cfg)


def test_textos_sin_creador_o_con_juego_vacio_es_descartada(cfg, temporada):
    c = compilado_de_texto()
    c.segmentos[1].creador = " "
    with pytest.raises(Descartada, match="crédito"):
        formatos.textos_compilado(c, "tiktok", temporada, cfg)
    with pytest.raises(Descartada, match="juego"):
        formatos.textos_compilado(compilado_de_texto(), "tiktok", {**temporada, "nombre": ""}, cfg)
    with pytest.raises(Descartada, match="plataforma"):
        formatos.textos_compilado(compilado_de_texto(), "x", temporada, cfg)


def test_textos_con_divulgacion_y_creador_repetido(cfg, temporada):
    c = compilado_de_texto(("ibai", "ibai", "auron"))
    c.segmentos[0].ficha["reglas"]["divulgacion"] = "#Publicidad"
    t = formatos.textos_compilado(c, "reels", temporada, cfg)
    assert "🎥 twitch.tv/ibai · twitch.tv/auron\n#Publicidad\n#GTA6" in t["texto"]
    assert "3 momentos de hoy, de 2 streamers." in t["texto"]


def test_textos_con_raiz_no_repite_el_mismo_compilado_en_7_dias(repo, cfg, temporada):
    import hashlib

    from fabrica.publicar import _cuerpo_sin_toma
    c = compilado_de_texto()
    t = formatos.textos_compilado(c, "tiktok", temporada, cfg, raiz=repo, hoy=HOY)
    huella = hashlib.sha256(_cuerpo_sin_toma(t["texto"]).encode("utf-8")).hexdigest()
    (repo / "estado").mkdir(exist_ok=True)
    (repo / "estado" / "buffer-textos.json").write_text(json.dumps(
        [{"plataforma": "tiktok", "huella": huella, "fecha": (HOY - timedelta(days=2)).isoformat()}]), encoding="utf-8")
    with pytest.raises(Descartada, match="texto repetido"):
        formatos.textos_compilado(c, "tiktok", temporada, cfg, raiz=repo, hoy=HOY)
    formatos.textos_compilado(c, "shorts", temporada, cfg, raiz=repo, hoy=HOY)     # otra red: sin choque


def test_el_gancho_del_formato_de_ayer_no_bloquea_el_compilado_de_hoy(repo, cfg, temporada):
    """El gancho se repite a propósito cada día: el control de gancho repetido usa el gancho con la fecha."""
    import csv

    from fabrica.registro import CABECERA_MARCADOR
    fila = {k: "" for k in CABECERA_MARCADOR}
    fila.update(toma="11", fecha=(HOY - timedelta(days=1)).isoformat(), plataforma="tiktok",
                gancho="Mejores momentos del día")
    with open(repo / "marcador.csv", "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, CABECERA_MARCADOR).writerow(fila)
    t = formatos.textos_compilado(compilado_de_texto(), "tiktok", temporada, cfg, raiz=repo, hoy=HOY)
    assert t["texto"].startswith("Mejores momentos del día")


def test_fila_del_marcador(cfg):
    c = compilado_de_texto()
    for s, tc in zip(c.segmentos, ("Con: twitch.tv/ibai", "Con: twitch.tv/auron", "Con: twitch.tv/grefg")):
        s.toma = Toma(12, "Mejores momentos del día", "MOMENTOS", credito=tc)
    fila = formatos.fila_marcador(c, AHORA, raiz=RAIZ_REAL)
    assert fila["pilar"] == "compilados" and fila["fuente_id"] == "t0+t1+t2" and fila["toma"] == 12
    assert fila["credito"] == "Con: twitch.tv/ibai · Con: twitch.tv/auron · Con: twitch.tv/grefg"
    assert fila["duracion_s"] == 42.0 and fila["fecha"] == "2026-11-20" and fila["hora"] == "12:00"
    assert formatos.fila_marcador(c, AHORA, duracion=42.04)["duracion_s"] == 42.04


# ---------------------------------------------------------------- muestras del repo

def test_las_muestras_del_repo_son_coherentes(cfg):
    datos = compilado.cargar_muestras()
    base = {m["fuente_id"]: m["video"] for m in muestras.cargar_muestras()}
    por_id = {f["id"]: f for f in fuentes.cargar_fuentes(RAIZ_REAL)}
    assert [c["id"] for c in datos["compilados"]] == ["mejores-momentos", "mejores-reacciones"]
    for c in datos["compilados"]:
        assert c["muestra"] is True and c["formato"] in cfg["formatos"]
        assert 2 <= len(c["segmentos"]) <= 3
        total = sum(s["recorte"]["duracion"] for s in c["segmentos"])
        assert cfg["duracion_s"][0] <= total <= cfg["duracion_s"][1]
        assert "gancho" not in c["segmentos"][0]                   # el primero lleva el gancho del formato
        for s in c["segmentos"]:
            assert formatos.apto(por_id[s["fuente_id"]], HOY, "momentos", muestra=True)[0]
            assert s["video"]["url"] in (m["url"] for m in base.values()) and s["video"]["sha256"] in (
                m["sha256"] for m in base.values())
            assert por_id[s["fuente_id"]]["uso"] == "muestras"
    assert {s["creador"] for c in datos["compilados"] for s in c["segmentos"]} == {"Blender (Big Buck Bunny)",
                                                                                   "Blender (Sintel)"}


def test_el_cli_de_muestras_no_escribe_dentro_del_repo(tmp_path):
    assert compilado.main(["--muestras", "--salida", str(RAIZ_REAL / "salida-prohibida")]) == 2
    assert not (RAIZ_REAL / "salida-prohibida").exists()


def test_las_muestras_no_descargan_si_la_ficha_no_autoriza(tmp_path, marca):
    datos = compilado.cargar_muestras()
    fichas = [{**f, "estado": "vetada"} for f in fuentes.cargar_fuentes(RAIZ_REAL)]

    def descargador(*a, **k):
        raise AssertionError("no debe descargar")
    with pytest.raises(RuntimeError, match="no autorizada"):
        compilado.generar_muestras(tmp_path, marca, datos, tmp_path, fichas=fichas, descargador=descargador)


# ---------------------------------------------------------------- video

def fuente_sintetica(ruta, seg, tono, ancho=320, alto=180, patron="testsrc2"):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-t", f"{seg}", "-i", f"{patron}=size={ancho}x{alto}:rate=30",
                    "-f", "lavfi", "-t", f"{seg}", "-i", f"sine=frequency={tono}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(ruta)], check=True)
    return ruta


CREADORES = ("ibai", "auronplay", "thegrefg")
DUR_SEG = 11.0


def segmentos_sinteticos(carpeta, n=3, dur=DUR_SEG, fecha=HOY, formato=None, cfg=None, duraciones=None,
                         patrones=("testsrc2", "mandelbrot", "testsrc")):
    segs = []
    for i, (creador, tono) in enumerate(zip(CREADORES[:n], (440, 660, 880))):
        dur = duraciones[i] if duraciones else dur
        fuente = fuente_sintetica(carpeta / f"f{i}.mp4", dur + 2.0, tono, patron=patrones[i])
        propia = Toma(7, f"Clip {'uno dos tres'.split()[i]} del día", "CLIP", escena=f"INT. STREAM {i + 1} — TARDE")
        ficha = ficha_t(f"twitch-{creador}", creador=f"twitch.tv/{creador}", twitch_login=creador)
        toma = formatos.toma_de_segmento(propia, formato, 7, i, creador, ficha) if formato else propia
        segs.append(formatos.Segmento(
            fuente=fuente, inicio=1.0, duracion=dur, toma=toma,
            lineas=[Linea(0.5, 3.5, creador.upper(), "Esto es increíble.")], ficha=ficha, creador=creador,
            fecha=fecha, id_origen=f"{creador}-clip"))
    return segs


@pytest.fixture(scope="module")
def compilado_ok(tmp_path_factory, marca, cfg):
    """Un compilado de 3 segmentos de 11 s, con sus MP4 de trabajo conservados (lo reutilizan casi todas)."""
    if not hay_ffmpeg:
        pytest.skip("ffmpeg no está instalado")
    base = tmp_path_factory.mktemp("compilado")
    fm = formatos.formato_con_limites(cfg, "mejores-momentos")
    segs = segmentos_sinteticos(base, formato=fm)
    plan, puntos = compilado.compilar(marca, fm, segs, base / "salida" / "c.mp4", trabajo=base / "trabajo")
    return {"base": base, "mp4": base / "salida" / "c.mp4", "plan": plan, "puntos": puntos, "fm": fm,
            "segs_mp4": compilado.rutas_segmentos(base / "trabajo", 3), "segmentos": segs}


def rojos(puntos):
    return [n for n, ok, _ in puntos if not ok]


@necesita_ffmpeg
def test_compilar_produce_un_mp4_valido_y_todo_en_verde(compilado_ok):
    c = compilado_ok
    assert rojos(c["puntos"]) == [], c["puntos"]
    info = estilo.sondear(c["mp4"])
    assert (info["video"]["width"], info["video"]["height"]) == (1080, 1920)
    assert info["video"]["codec_name"] == "h264" and info["audio"]["codec_name"] == "aac"
    assert info["video"]["profile"] == "High" and info["video"]["pix_fmt"] == "yuv420p"
    assert abs(info["fps"] - 30) < 0.01 and estilo.faststart(c["mp4"])
    assert abs(info["duracion"] - 3 * DUR_SEG) <= 0.15 * 3
    plan = json.loads(c["mp4"].with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert plan["tipo"] == "compilado" and plan["formato"] == "mejores-momentos" and plan["pilar"] == "compilados"
    # La plantilla es la vigente de componer (v1 en main; v2 al fusionar la tarea #5): la del segmento.
    assert plan["plantilla"] == plan["segmentos"][0]["plan"].get("plantilla", "v1-pagina")
    assert plan["numero"] == 7 and plan["fecha"] == HOY.isoformat()
    assert plan["creditos"] == [f"Con: twitch.tv/{x}" for x in CREADORES]
    assert [s["n"] for s in plan["segmentos"]] == [1, 2, 3]
    assert all(s["puerta_ok"] and len(s["sha256_mp4"]) == 64 for s in plan["segmentos"])
    assert plan["segmentos"][1]["t0"] == pytest.approx(DUR_SEG) and plan["segmentos"][2]["t1"] == pytest.approx(33.0)
    assert plan["segmentos"][0]["plan"]["toma"]["gancho"] == "Mejores momentos del día"


@necesita_ffmpeg
def test_compilar_no_usa_transiciones_ni_fundidos(marca, compilado_ok):
    cmd = " ".join(compilado.orden_concat(compilado_ok["segs_mp4"], Path("s.mp4"), marca))
    assert "concat=n=3:v=1:a=1" in cmd and "-movflags +faststart" in cmd
    assert not any(x in cmd for x in ("xfade", "acrossfade", "fade", "zoompan"))
    assert cmd.count("ffmpeg") == 1


def punto_de(puntos, texto):
    return next((n, ok, d) for n, ok, d in puntos if texto in n)


def revisar(c, mp4=None, plan=None, segs_mp4=None, cfg=None):
    return compilado.revisar_compilado(mp4 or c["mp4"], plan or c["plan"], segs_mp4 or c["segs_mp4"],
                                       cfg or c["fm"], Marca.cargar(RAIZ_REAL))


@pytest.fixture
def rapido(monkeypatch):
    """Para las variantes que sólo cambian el plan: se saltan los puntos de píxeles y audio (ya probados aparte)."""
    monkeypatch.setattr(compilado, "_cortes_secos", lambda *a, **k: (True, "omitido"))
    monkeypatch.setattr(compilado, "_audio_de_segmentos", lambda *a, **k: (True, "omitido"))


def solo_rojo(puntos, texto):
    """El punto con `texto` está en rojo (los demás pueden también, pero éste sí)."""
    assert not punto_de(puntos, texto)[1], punto_de(puntos, texto)


@pytest.fixture(scope="module")
def con_xfade(compilado_ok):
    """Los mismos 3 segmentos unidos con xfade de 0.5 s (no es corte seco)."""
    c = compilado_ok
    s = c["segs_mp4"]
    d = DUR_SEG
    salida = c["base"] / "xfade.mp4"
    fc = (f"[0:v][1:v]xfade=transition=fade:duration=0.5:offset={d - 0.5}[x1];"
          f"[x1][2:v]xfade=transition=fade:duration=0.5:offset={2 * d - 1.0}[v];"
          "[0:a][1:a]acrossfade=d=0.5[y1];[y1][2:a]acrossfade=d=0.5[a]")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(s[0]), "-i", str(s[1]), "-i", str(s[2]),
                    "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-ar", "48000",
                    "-movflags", "+faststart", str(salida)], check=True)
    return salida


@necesita_ffmpeg
def test_revisar_en_rojo_con_xfade(compilado_ok, con_xfade):
    p = revisar(compilado_ok, mp4=con_xfade)
    solo_rojo(p, "Corte seco")
    assert "no es corte seco" in punto_de(p, "Corte seco")[2]


@necesita_ffmpeg
def test_revisar_en_rojo_con_fundido_a_negro_en_una_union(compilado_ok):
    c = compilado_ok
    s = c["segs_mp4"]
    salida = c["base"] / "fundido.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(s[0]), "-i", str(s[1]), "-i", str(s[2]),
                    "-filter_complex",
                    f"[0:v]fade=t=out:st={DUR_SEG - 0.3}:d=0.3[v0];[v0][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-movflags", "+faststart", str(salida)],
                   check=True)
    solo_rojo(revisar(c, mp4=salida), "Corte seco")


@necesita_ffmpeg
def test_revisar_en_rojo_con_un_solo_segmento(rapido, compilado_ok):
    c = compilado_ok
    plan = copy.deepcopy(c["plan"])
    plan["segmentos"] = plan["segmentos"][:1]
    plan["creditos"] = plan["creditos"][:1]
    p = revisar(c, mp4=c["segs_mp4"][0], plan=plan, segs_mp4=c["segs_mp4"][:1])
    solo_rojo(p, "2 o 3 segmentos")


@necesita_ffmpeg
def test_revisar_en_rojo_con_menos_de_30_segundos(rapido, compilado_ok):
    c = compilado_ok
    corto = c["base"] / "corto.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(c["mp4"]), "-t", "25", "-c", "copy",
                    str(corto)], check=True)
    solo_rojo(revisar(c, mp4=corto), "Duración de 30 a 60 s")


@necesita_ffmpeg
@pytest.mark.parametrize("credito", ["", "Con: otra persona"])
def test_revisar_en_rojo_con_credito_vacio_o_de_otro(rapido, compilado_ok, credito):
    c = compilado_ok
    plan = copy.deepcopy(c["plan"])
    plan["segmentos"][1]["credito"] = credito
    solo_rojo(revisar(c, plan=plan), "Crédito de cada segmento")


@necesita_ffmpeg
def test_revisar_en_rojo_si_el_credito_falta_en_la_lista_de_creditos(rapido, compilado_ok):
    plan = copy.deepcopy(compilado_ok["plan"])
    plan["creditos"] = plan["creditos"][:2]
    solo_rojo(revisar(compilado_ok, plan=plan), "Crédito de cada segmento")


@necesita_ffmpeg
def test_revisar_en_rojo_con_fechas_distintas(rapido, compilado_ok):
    plan = copy.deepcopy(compilado_ok["plan"])
    plan["segmentos"][2]["fecha"] = (HOY - timedelta(days=1)).isoformat()
    solo_rojo(revisar(compilado_ok, plan=plan), "Mismo día")


@necesita_ffmpeg
def test_revisar_en_rojo_con_el_audio_de_un_segmento_en_silencio(compilado_ok):
    c = compilado_ok
    mudo = c["base"] / "mudo.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(c["mp4"]), "-c:v", "copy", "-af",
                    f"volume=0:enable='between(t,{DUR_SEG},{2 * DUR_SEG})'", "-c:a", "aac", str(mudo)], check=True)
    p = revisar(c, mp4=mudo)
    solo_rojo(p, "Audio de cada segmento")
    assert "segmento 2" in punto_de(p, "Audio de cada segmento")[2]


@necesita_ffmpeg
def test_revisar_en_rojo_con_transiciones_declaradas_o_en_la_marca(rapido, compilado_ok, marca):
    plan = copy.deepcopy(compilado_ok["plan"])
    plan["transiciones"] = ["fundido"]
    solo_rojo(revisar(compilado_ok, plan=plan), "Nada de transiciones")
    otra = Marca({**marca.datos, "movimiento": {"transiciones": "fundido"}}, marca.raiz)
    p = compilado.revisar_compilado(compilado_ok["mp4"], compilado_ok["plan"], compilado_ok["segs_mp4"],
                                    compilado_ok["fm"], otra)
    solo_rojo(p, "Nada de transiciones")


@necesita_ffmpeg
def test_revisar_en_rojo_con_puerta_de_estilo_de_un_segmento_en_rojo(rapido, compilado_ok):
    plan = copy.deepcopy(compilado_ok["plan"])
    plan["segmentos"][0]["puerta_ok"] = False
    solo_rojo(revisar(compilado_ok, plan=plan), "Puerta de estilo de cada segmento")


@necesita_ffmpeg
def test_el_umbral_de_corte_deja_margen_a_los_dos_lados(compilado_ok, con_xfade, marca):
    """Calibración: el compilado correcto queda muy por debajo del umbral y el xfade muy por encima."""
    detalle = punto_de(revisar(compilado_ok), "Corte seco")[2]
    valores = [float(x.rsplit(" ", 1)[1]) for x in detalle.split(" (máximo")[0].removeprefix("diferencias ").split(", ")]
    assert max(valores) < compilado.UMBRAL_CORTE / 2
    malo = punto_de(revisar(compilado_ok, mp4=con_xfade), "Corte seco")[2]
    altos = [float(x.rsplit(" ", 1)[1]) for x in malo.split(": ", 1)[1].split(", ")]
    assert max(altos) > compilado.UMBRAL_CORTE * 4


# ---------------------------------------------------------------- hacer_compilado

def candidatos_falsos(n=3, vph=(30, 20, 10)):
    return [cand(f"c{i}", CREADORES[i], vph[i], fuente=f"twitch-{CREADORES[i]}") for i in range(n)]


def fichas_falsas():
    return [ficha_t(f"twitch-{c}", creador=f"twitch.tv/{c}", twitch_login=c) for c in CREADORES]


def segmento_falso_fn(carpeta, dur=DUR_SEG, duraciones=None, falla=()):
    """Un `hacer_segmento_fn` falso: fuentes sintéticas en `carpeta` en vez de Gemini y descarga."""
    hechos = {}

    def fn(candidato, ficha, ctx):
        if candidato.id_origen in falla:
            raise formatos.ErrorSegmento("ojos: sin respuesta de prueba")
        i = int(candidato.id_origen[1:])
        d = (duraciones or {}).get(candidato.id_origen, dur)
        fuente = fuente_sintetica(carpeta / f"{candidato.id_origen}.mp4", d + 2.0, (440, 660, 880)[i],
                                  patron=("testsrc2", "mandelbrot", "testsrc")[i])
        hechos[candidato.id_origen] = fuente
        propia = Toma(ctx.numero, f"Clip número {i} del día", "CLIP", escena=f"INT. STREAM {i + 1} — TARDE")
        return formatos.Segmento(fuente=fuente, inicio=1.0, duracion=d, toma=propia,
                                 lineas=[Linea(0.5, 3.5, ficha["creador"].upper(), "Esto es increíble.")],
                                 ficha=ficha, creador=ficha["creador"], fecha=candidato.creado_en.astimezone(ZONA).date(),
                                 id_origen=candidato.id_origen)
    fn.hechos = hechos
    return fn


def sin_mp4(carpeta):
    return list(Path(carpeta).rglob("*.mp4")) if Path(carpeta).exists() else []


@necesita_ffmpeg
def test_hacer_compilado_ok_con_tres_segmentos(repo, tmp_path):
    salida = tmp_path / "salida"
    r = formatos.hacer_compilado(repo, "mejores-momentos", candidatos_falsos(), fichas_falsas(), AHORA, salida=salida,
                                 hacer_segmento_fn=segmento_falso_fn(tmp_path))
    assert r.ok, r.motivos
    assert r.mp4.is_file() and r.mp4.parent == salida and r.mp4.with_suffix(".plan.json").is_file()
    assert r.fila["pilar"] == "compilados" and r.fila["fuente_id"] == "twitch-ibai+twitch-auronplay+twitch-thegrefg"
    assert r.fila["toma"] == 1 and r.fila["duracion_s"] == pytest.approx(33.0, abs=0.1)
    assert r.fila["gancho"] == "Mejores momentos del día" and r.fila["credito"].count("Con: twitch.tv/") == 3
    assert r.plan["tipo"] == "compilado" and rojos(r.puntos) == []


def test_hacer_compilado_descarta_con_un_solo_apto(repo, tmp_path):
    r = formatos.hacer_compilado(repo, "mejores-momentos", candidatos_falsos(1), fichas_falsas(), AHORA, salida=tmp_path / "s",
                                 hacer_segmento_fn=segmento_falso_fn(tmp_path))
    assert not r.ok and "menos de 2 candidatos aptos" in r.motivos[0] and r.mp4 is None
    assert sin_mp4(tmp_path) == []


def test_hacer_compilado_descarta_si_otro_dia_o_formato_desconocido(repo, tmp_path):
    cs = candidatos_falsos(2)
    cs[0].creado_en = AHORA - timedelta(days=1)
    r = formatos.hacer_compilado(repo, "mejores-momentos", cs, fichas_falsas(), AHORA,
                                 hacer_segmento_fn=segmento_falso_fn(tmp_path))
    assert not r.ok
    r = formatos.hacer_compilado(repo, "inventado", candidatos_falsos(), fichas_falsas(), AHORA)
    assert not r.ok and "formato desconocido" in r.motivos[0]


@necesita_ffmpeg
def test_hacer_compilado_descarta_si_la_suma_no_cae_en_el_rango(repo, tmp_path):
    salida = tmp_path / "s"
    r = formatos.hacer_compilado(repo, "mejores-momentos", candidatos_falsos(2, (30, 20)), fichas_falsas(), AHORA,
                                 salida=salida, hacer_segmento_fn=segmento_falso_fn(tmp_path, dur=8.0))
    assert not r.ok and "ninguna combinación suma de 30 a 60 s" in " ".join(r.motivos)
    assert sin_mp4(salida) == []


@necesita_ffmpeg
def test_hacer_compilado_descarta_si_un_segmento_sale_rojo_en_la_puerta_de_estilo(repo, tmp_path):
    salida = tmp_path / "s"
    # el primer segmento dura 4 s y sale en rojo en «Duración de 7 a 30 s»; con los otros dos suman 34 s y caben
    r = formatos.hacer_compilado(repo, "mejores-momentos", candidatos_falsos(3, (30, 20, 10)), fichas_falsas(), AHORA,
                                 salida=salida, hacer_segmento_fn=segmento_falso_fn(
                                     tmp_path, duraciones={"c0": 4.0, "c1": 15.0, "c2": 15.0}))
    assert not r.ok and any("segmento 1" in m and "Duración de 7 a 30 s" in m for m in r.motivos), r.motivos
    assert sin_mp4(salida) == [] and not list(salida.glob("*.plan.json"))


def test_hacer_compilado_descarta_si_un_segmento_falla_y_queda_uno(repo, tmp_path):
    fn = segmento_falso_fn(tmp_path, falla=("c1",))
    r = formatos.hacer_compilado(repo, "mejores-momentos", candidatos_falsos(2), fichas_falsas(), AHORA,
                                 hacer_segmento_fn=fn)
    assert not r.ok and "segmento c1: ojos: sin respuesta de prueba" in r.motivos[0]
    assert "quedan 1 segmentos y el mínimo es 2" in r.motivos[-1]


@necesita_ffmpeg
def test_hacer_compilado_omite_el_segmento_fallido_y_sigue_con_los_otros_dos(repo, tmp_path):
    fn = segmento_falso_fn(tmp_path, dur=16.0, falla=("c1",))
    r = formatos.hacer_compilado(repo, "mejores-reacciones", candidatos_falsos(3), fichas_falsas(), AHORA,
                                 salida=tmp_path / "s", hacer_segmento_fn=fn)
    assert r.ok, r.motivos
    assert [s["id_origen"] for s in r.plan["segmentos"]] == ["c0", "c2"]
    assert r.plan["omitidos"] == ["segmento c1: ojos: sin respuesta de prueba"]
    assert r.fila["fuente_id"] == "twitch-ibai+twitch-thegrefg"


@necesita_ffmpeg
def test_un_descarte_no_borra_el_mp4_de_otro_compilado_en_la_misma_carpeta(repo, tmp_path):
    salida = tmp_path / "s"
    salida.mkdir()
    ajeno, ajeno_plan = salida / "compilado-009-mejores-momentos.mp4", salida / "compilado-009-mejores-momentos.plan.json"
    ajeno.write_bytes(b"video de otro")
    ajeno_plan.write_text("{}", encoding="utf-8")
    r = formatos.hacer_compilado(repo, "mejores-reacciones", candidatos_falsos(2, (30, 20)), fichas_falsas(), AHORA,
                                 salida=salida, hacer_segmento_fn=segmento_falso_fn(tmp_path, dur=8.0))
    assert not r.ok
    assert ajeno.read_bytes() == b"video de otro" and ajeno_plan.is_file()


def test_hacer_compilado_con_excepcion_inesperada_es_descarte_y_borra_lo_temporal(repo, tmp_path):
    def roto(candidato, ficha, ctx):
        raise KeyError("boom")
    r = formatos.hacer_compilado(repo, "mejores-momentos", candidatos_falsos(), fichas_falsas(), AHORA,
                                 hacer_segmento_fn=roto)
    assert not r.ok and r.mp4 is None
    r2 = formatos.hacer_compilado(repo, "mejores-momentos", candidatos_falsos(), fichas_falsas(), AHORA,
                                  hacer_segmento_fn=lambda *a: 1 / 0)
    assert not r2.ok and "error inesperado: ZeroDivisionError" in r2.motivos[0]


@necesita_ffmpeg
def test_duraciones_que_no_son_multiplo_de_un_cuadro_no_corren_las_uniones(tmp_path, marca, cfg):
    """10.37, 12.41 y 11.55 s con metraje que se mueve mucho: los cuadros del compilado son la suma de los de los
    segmentos (sin el cuadro extra del audio más largo) y el corte seco sale en verde."""
    fm = formatos.formato_con_limites(cfg, "mejores-momentos")
    segs = segmentos_sinteticos(tmp_path, formato=fm, duraciones=(10.37, 12.41, 11.55),
                                patrones=("testsrc2", "mandelbrot", "testsrc2"))
    plan, puntos = compilado.compilar(marca, fm, segs, tmp_path / "salida" / "c.mp4", trabajo=tmp_path / "trabajo")
    assert rojos(puntos) == [], puntos
    assert punto_de(puntos, "Cuadros = suma")[1]
    segs_mp4 = compilado.rutas_segmentos(tmp_path / "trabajo", 3)
    assert compilado._cuadros(tmp_path / "salida" / "c.mp4") == sum(compilado._cuadros(m) for m in segs_mp4)
    assert plan["segmentos"][2]["t1"] == pytest.approx(compilado._cuadros(tmp_path / "salida" / "c.mp4") / 30, abs=1e-3)

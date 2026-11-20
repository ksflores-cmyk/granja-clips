"""Tarea #17 «revisiones-codigo»: puerta técnica por código (subtítulos, destellos, congelados, ducking, clipping).

Videos sintéticos pequeños (270x480, 3 s) con ffmpeg: cada punto tiene un caso que falla a propósito y uno limpio.
Todo sin red.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import RAIZ_REAL
from fabrica import informativa, validar
from fabrica.editar import estilo, mezcla, tecnica, toma
from test_n4 import (AHORA, Transporte, abrir_con, candidato, ficha_propia, respuesta_grabada, sobre,
                     video20)  # noqa: F401 (video20 es un fixture de sesión)

CFG = tecnica.cargar(RAIZ_REAL)
PLAN_VIDEO = {"plantilla": "v2-pantalla-completa", "lineas": [], "audio": {"tipo": "original"}}
PLAN_SIN_VIDEO = {"lineas": [], "audio": {"tipo": "silencio"}}
FUENTE = "testsrc2=size=270x480:rate=30:duration=3"
SENO = "sine=frequency=440:duration=3:sample_rate=48000"
CAJA_NEGRA = "drawbox=x=0:y=0:w=iw:h=ih:color={c}:t=fill:enable='{cuando}'"


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)], check=True)


def clip(ruta: Path, *, vf=None, complejo=None, fuente=FUENTE, audio=SENO) -> Path:
    """MP4 sintético H.264 crf 20 (con o sin audio). `complejo`: filter_complex que termina en [v]."""
    cmd = ["-f", "lavfi", "-i", fuente]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio]
    if complejo:
        cmd += ["-filter_complex", complejo, "-map", "[v]"] + (["-map", "1:a"] if audio else [])
    elif vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30"]
    cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"] if audio else ["-an"]
    ffmpeg(*cmd, "-t", "3", ruta)
    return ruta


def punto(puntos, fragmento):
    """(ok, detalle) del único punto cuyo nombre trae `fragmento`."""
    hallados = [(ok, det) for n, ok, det in puntos if fragmento in n]
    assert len(hallados) == 1, [n for n, _, _ in puntos]
    return hallados[0]


@pytest.fixture(scope="module")
def carpeta(tmp_path_factory):
    return tmp_path_factory.mktemp("tecnica")


@pytest.fixture(scope="module")
def limpio(carpeta):
    return clip(carpeta / "limpio.mp4")


# ---------------------------------------------------------------- configuración

def test_cargar_lee_el_archivo_del_repo():
    assert CFG["destellos"]["cuadros_max"] == 2 and CFG["congelados"]["max_s"] == 0.5
    assert CFG["ducking"]["sidechain"]["ratio"] > 1 and CFG["analisis"] == {"ancho": 135, "alto": 240}


def test_cargar_sin_archivo_usa_los_valores_por_omision(tmp_path):
    assert tecnica.cargar(tmp_path) == tecnica.POR_OMISION
    assert tecnica.POR_OMISION["subtitulos"] == {"tolerancia_s": 0.15, "ventana_s": 1.5, "parecido_min": 0.75,
                                                 "exigir_transcripcion": False}


def test_cargar_fusiona_un_archivo_parcial(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tecnica.yml").write_text("congelados:\n  max_s: 0.9\n", encoding="utf-8")
    c = tecnica.cargar(tmp_path)
    assert c["congelados"]["max_s"] == 0.9 and c["congelados"]["dif_max"] == 3.0 and c["clipping"]["umbral"] == 0.99
    (tmp_path / "config" / "tecnica.yml").write_text("{ roto: [", encoding="utf-8")
    assert tecnica.cargar(tmp_path) == tecnica.POR_OMISION


# ---------------------------------------------------------------- limpio: los 5 puntos pasan

def test_un_clip_limpio_pasa_los_cinco_puntos(limpio):
    puntos = tecnica.revisar(limpio, PLAN_VIDEO, CFG)
    assert len(puntos) == 5 and all(n.startswith("[técnica] ") for n, _, _ in puntos)
    assert all(ok for _, ok, _ in puntos), puntos
    assert "no aplica" in punto(puntos, "Subtítulo")[1] and "no aplica" in punto(puntos, "ducking")[1]


def test_la_pieza_sin_video_pasa_y_dice_que_los_congelados_no_aplican(carpeta):
    # un cuadro quieto durante 3 s: en una pieza sin video no es un congelado (lo mide la puerta de estilo)
    ruta = clip(carpeta / "quieta.mp4", fuente="color=c=0xf4ecd8:s=270x480:r=30:d=3")
    puntos = tecnica.revisar(ruta, PLAN_SIN_VIDEO, CFG)
    assert all(ok for _, ok, _ in puntos), puntos
    assert punto(puntos, "congelados")[1].startswith("no aplica (pieza sin video")


# ---------------------------------------------------------------- 2. destellos

@pytest.mark.parametrize("color,cuando,donde", [("black", "eq(n,45)", "cuadro 45"), ("white", "eq(n,45)", "cuadro 45"),
                                                ("black", "eq(n,0)", "cuadro 0")])
def test_un_destello_de_un_cuadro_falla_con_el_segundo_y_el_cuadro(carpeta, color, cuando, donde):
    ruta = clip(carpeta / f"destello-{color}-{donde.replace(' ', '')}.mp4", vf=CAJA_NEGRA.format(c=color, cuando=cuando))
    ok, det = punto(tecnica.revisar(ruta, PLAN_VIDEO, CFG), "destellos")
    assert not ok and f"destello {'negro' if color == 'black' else 'blanco'} de 1 cuadro" in det and donde in det
    assert ("1,50 s" if donde == "cuadro 45" else "0,00 s") in det


def test_dos_cuadros_siguen_siendo_destello_y_tres_son_un_fundido(carpeta):
    dos = clip(carpeta / "dos.mp4", vf=CAJA_NEGRA.format(c="black", cuando="between(n,45,46)"))
    ok, det = punto(tecnica.revisar(dos, PLAN_VIDEO, CFG), "destellos")
    assert not ok and "de 2 cuadros" in det and "cuadros 45 a 46" in det
    tres = clip(carpeta / "tres.mp4", vf=CAJA_NEGRA.format(c="black", cuando="between(n,45,47)"))
    assert punto(tecnica.revisar(tres, PLAN_VIDEO, CFG), "destellos")[0]


def test_una_escena_oscura_larga_y_un_negro_al_final_no_son_destello(carpeta):
    oscura = clip(carpeta / "oscura.mp4", vf=CAJA_NEGRA.format(c="black", cuando="between(n,30,70)"))
    assert punto(tecnica.revisar(oscura, PLAN_VIDEO, CFG), "destellos")[0]
    hasta_el_final = clip(carpeta / "negro-final.mp4", vf=CAJA_NEGRA.format(c="black", cuando="gte(n,30)"))
    assert punto(tecnica.revisar(hasta_el_final, PLAN_VIDEO, CFG), "destellos")[0]


# ---------------------------------------------------------------- 3. congelados

def congelado(carpeta, nombre, primero, ultimo) -> Path:
    return clip(carpeta / nombre, complejo=f"[0:v]split[a][b];[a][b]freezeframes=first={primero}:last={ultimo}:replace=30[v]")


def test_un_congelado_de_un_segundo_falla_con_el_tramo(carpeta):
    ruta = congelado(carpeta, "congelado.mp4", 30, 59)
    ok, det = punto(tecnica.revisar(ruta, PLAN_VIDEO, CFG), "congelados")
    assert not ok and "congelado de 1,00 a 2,00 s" in det and "cuadros 30 a 59" in det


def test_un_tramo_quieto_corto_pasa(carpeta):
    ruta = congelado(carpeta, "corto.mp4", 30, 39)      # 10 cuadros = 0,33 s
    ok, det = punto(tecnica.revisar(ruta, PLAN_VIDEO, CFG), "congelados")
    assert ok and "0,33 s" in det


def test_el_movimiento_normal_y_un_objeto_pequeno_en_movimiento_no_son_congelado(carpeta):
    fondo = carpeta / "fondo.png"
    ffmpeg("-f", "lavfi", "-i", FUENTE, "-frames:v", "1", fondo)
    # un cuadro del 1 % del área (36x36 de 270x480) cruza una escena quieta
    ruta = carpeta / "objeto.mp4"
    ffmpeg("-loop", "1", "-framerate", "30", "-i", fondo, "-f", "lavfi", "-i", "color=c=white:s=36x36:r=30:d=3",
           "-filter_complex", "[0][1]overlay=x='20+t*60':y='200+t*20'", "-c:v", "libx264", "-preset", "ultrafast",
           "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-an", "-t", "3", ruta)
    ok, det = punto(tecnica.revisar(ruta, PLAN_VIDEO, CFG), "congelados")
    assert ok, det
    # el mismo fondo sin el objeto sí es un congelado
    quieto = carpeta / "fondo.mp4"
    ffmpeg("-loop", "1", "-framerate", "30", "-i", fondo, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", "30", "-an", "-t", "3", quieto)
    ok, det = punto(tecnica.revisar(quieto, PLAN_VIDEO, CFG), "congelados")
    assert not ok and "congelado de 0,00 a 3,00 s" in det


def test_los_congelados_no_aplican_sin_video(carpeta):
    ruta = congelado(carpeta, "congelado-sin-video.mp4", 30, 59)
    ok, det = punto(tecnica.revisar(ruta, PLAN_SIN_VIDEO, CFG), "congelados")
    assert ok and det.startswith("no aplica (pieza sin video")


def test_un_compilado_cuenta_como_pieza_con_video(carpeta):
    ruta = congelado(carpeta, "congelado-compilado.mp4", 30, 59)
    assert not punto(tecnica.revisar(ruta, {"tipo": "compilado", "segmentos": []}, CFG), "congelados")[0]


# ---------------------------------------------------------------- 5. clipping

def test_un_audio_recortado_falla_con_los_instantes(carpeta):
    ruta = clip(carpeta / "recorte.mp4", audio="aevalsrc='clip(2.5*sin(2*PI*220*t),-1,1)':s=48000:d=3")
    ok, det = punto(tecnica.revisar(ruta, PLAN_VIDEO, CFG), "clipping")
    assert not ok and "recortes" in det and " s" in det and "0,0" in det


def test_un_audio_fuerte_pero_sin_recorte_pasa(carpeta):
    # con entrada suave: un seno que arranca en seco a 0,9 hace que el AAC sobrepase 1,0 en el primer cuadro de audio
    ruta = clip(carpeta / "fuerte.mp4", audio="aevalsrc='0.9*sin(2*PI*220*t)*min(1,t*20)':s=48000:d=3")
    ok, det = punto(tecnica.revisar(ruta, PLAN_VIDEO, CFG), "clipping")
    assert ok and "pico 0.90" in det


def test_sin_audio_el_clipping_no_aplica(carpeta):
    ruta = clip(carpeta / "mudo.mp4", audio=None)
    ok, det = punto(tecnica.revisar(ruta, PLAN_VIDEO, CFG), "clipping")
    assert ok and det.startswith("no aplica")


def test_clipping_cuenta_muestras_seguidas_del_mismo_canal():
    import numpy as np
    x = np.zeros((1000, 2), dtype=np.float32)
    x[100:102, 0] = 1.0                       # sólo 2 seguidas: no es recorte
    x[200:204, 1] = -1.0                      # 4 seguidas: sí
    x[300:302, 0] = 1.0
    x[302:304, 1] = 1.0                       # seguidas pero de canales distintos: no
    tramos, instantes = tecnica.clipping(x, 1000, CFG)
    assert tramos == [(1, 200)] and instantes == ["0,20 s"]


# ---------------------------------------------------------------- 4. ducking

VOZ = "aevalsrc='0.15*sin(2*PI*220*t)*sin(2*PI*3*t)*gt(sin(2*PI*0.7*t),-0.2)*1.4':s=48000:d=4"
MUSICA = "anoisesrc=color=pink:amplitude=0.3:d=4:r=48000"


@pytest.fixture(scope="module")
def fuentes_de_mezcla(carpeta):
    ffmpeg("-f", "lavfi", "-i", VOZ, "-ar", "48000", carpeta / "voz.wav")
    ffmpeg("-f", "lavfi", "-i", MUSICA, "-ac", "2", carpeta / "musica.wav")
    return carpeta / "voz.wav", carpeta / "musica.wav"


def test_el_filtro_de_ducking_sale_de_la_configuracion():
    f = mezcla.filtro_ducking(CFG)
    s = CFG["ducking"]["sidechain"]
    assert f.startswith("sidechaincompress=") and f"threshold={s['threshold']:g}" in f and f"ratio={s['ratio']:g}" in f
    orden = " ".join(mezcla.orden_mezcla(Path("v.wav"), Path("m.wav"), Path("s.wav"), duracion=4, cfg=CFG))
    assert f in orden and "[1:a]" in orden and "amix" in orden


def test_el_ducking_deja_la_musica_12_db_bajo_la_voz(carpeta, limpio, fuentes_de_mezcla):
    voz, musica = fuentes_de_mezcla
    pistas = mezcla.mezclar(voz, musica, carpeta / "mezcla.wav", duracion=4, cfg=CFG)
    assert all(Path(p).is_file() for p in pistas.values())
    plan = {"lineas": [], "audio": {"tipo": "mezcla"}}
    ok, det = punto(tecnica.revisar(limpio, plan, CFG, pistas={"voz": pistas["voz"], "musica": pistas["musica"]}),
                    "ducking")
    assert ok, det
    tramos, con_voz, malas = tecnica.ducking(tecnica._mono(pistas["voz"]), tecnica._mono(pistas["musica"]), 16000, CFG)
    assert tramos == [] and con_voz > 10 and malas == 0


def test_la_mezcla_sin_ducking_a_igual_sonoridad_sale_roja(carpeta, limpio, fuentes_de_mezcla):
    voz, musica = fuentes_de_mezcla
    fuerte = {**CFG, "ducking": {**CFG["ducking"], "musica_base_db": 0}}      # la música tan fuerte como la voz
    pistas = mezcla.mezclar(voz, musica, carpeta / "sin-ducking.wav", duracion=4, cfg=fuerte, ducking=False)
    ok, det = punto(tecnica.revisar(limpio, {"lineas": [], "audio": {"tipo": "mezcla"}}, CFG,
                                    pistas={"voz": pistas["voz"], "musica": pistas["musica"]}), "ducking")
    assert not ok and "menos de 12 dB de la voz en" in det and " s" in det and "ventanas con voz" in det


def test_una_mezcla_sin_sus_pistas_es_rojo_y_sin_mezcla_no_aplica(limpio):
    ok, det = punto(tecnica.revisar(limpio, {"lineas": [], "audio": {"tipo": "mezcla"}}, CFG), "ducking")
    assert not ok and "pistas separadas" in det
    ok, det = punto(tecnica.revisar(limpio, PLAN_VIDEO, CFG), "ducking")
    assert ok and det.startswith("no aplica (la pieza no mezcla música con voz)")


def test_ducking_ignora_el_borde_de_la_frase_pero_no_un_tramo_largo_fuerte():
    import numpy as np
    sr, w = 16000, 1600
    voz = np.zeros(sr * 2, dtype=np.float32)
    voz[sr // 2: sr + sr // 2] = 0.1 * np.sin(np.arange(sr) * 0.3)
    musica = np.full_like(voz, 0.001)
    musica[sr // 2: sr // 2 + w] = 0.1         # la música aún sin bajar en la primera ventana: es el ataque
    assert tecnica.ducking(voz, musica, sr, CFG)[2] == 0
    musica[sr // 2: sr + sr // 2] = 0.1        # fuerte en toda la frase
    tramos, _, malas = tecnica.ducking(voz, musica, sr, CFG)
    assert malas > 5 and tramos and tecnica._ducking_malo(tramos, 9, malas, CFG, 0.1)


# ---------------------------------------------------------------- 1. subtítulos

def plan_con(lineas, palabras=None):
    p = {"lineas": lineas, "audio": {"tipo": "original"}}
    if palabras is not None:
        p["transcripcion"] = {"palabras": palabras}
    return p


def linea(t0, frase="Cuidado atrás", original="Watch out behind"):
    return {"t0": t0, "t1": t0 + 2, "nombre": "ANA", "frase": frase, "original": original}


def test_el_subtitulo_a_tiempo_pasa():
    ok, det = tecnica.subtitulos(plan_con([linea(3.10)], [{"texto": "Watch", "t0": 3.0, "t1": 3.3}]), CFG)
    assert ok and "desfase máximo 0,10 s" in det


def test_el_subtitulo_tarde_falla_con_cuanto_y_donde():
    ok, det = tecnica.subtitulos(plan_con([linea(3.10)], [{"texto": "watch", "t0": 2.68, "t1": 2.9}]), CFG)
    assert not ok and "línea 1 «Cuidado atrás»: empieza +0,42 s después de «Watch» (2,68 s)" in det


def test_el_subtitulo_temprano_falla_y_la_tolerancia_es_de_150_ms():
    p = lambda t: tecnica.subtitulos(plan_con([linea(t)], [{"texto": "Watch", "t0": 3.0, "t1": 3.3}]), CFG)
    assert not p(2.80)[0] and "antes de" in p(2.80)[1]
    assert p(3.14)[0] and not p(3.17)[0]


def test_palabra_ausente_o_lejana_falla_con_el_instante():
    ok, det = tecnica.subtitulos(plan_con([linea(3.10)], [{"texto": "otra", "t0": 3.1, "t1": 3.3}]), CFG)
    assert not ok and "no se encontró «Watch» cerca de 3,10 s" in det
    ok, det = tecnica.subtitulos(plan_con([linea(3.10)], [{"texto": "watch", "t0": 5.0, "t1": 5.3}]), CFG)
    assert not ok and "no se encontró «Watch» cerca de 3,10 s" in det


def test_la_palabra_se_compara_sin_acentos_ni_puntuacion_y_gana_la_mas_cercana():
    palabras = [{"texto": "cuidado", "t0": 2.2, "t1": 2.4}, {"texto": "Cuidado", "t0": 3.05, "t1": 3.3}]
    ok, det = tecnica.subtitulos(plan_con([linea(3.10, original="¡Cuídado!")], palabras), CFG)
    assert ok and "0,05 s" in det


def test_sin_lineas_con_voz_o_sin_transcripcion_no_aplica():
    assert tecnica.subtitulos(plan_con([]), CFG)[1].startswith("no aplica (la pieza no trae líneas con voz)")
    sin_original = plan_con([{"t0": 1, "t1": 2, "nombre": "A", "frase": "Hola"}], [{"texto": "x", "t0": 1, "t1": 2}])
    assert tecnica.subtitulos(sin_original, CFG)[1].startswith("no aplica (la pieza no trae líneas")
    ok, det = tecnica.subtitulos(plan_con([linea(1.0)]), CFG)
    assert ok and det.startswith("no aplica (sin transcripción por palabra")


def test_exigir_transcripcion_pone_en_rojo_la_pieza_con_voz_sin_transcripcion():
    estricta = {**CFG, "subtitulos": {**CFG["subtitulos"], "exigir_transcripcion": True}}
    ok, det = tecnica.subtitulos(plan_con([linea(1.0)]), estricta)
    assert not ok and "no trae transcripción" in det
    assert tecnica.subtitulos(plan_con([]), estricta)[0]          # sin voz no hay nada que exigir


def test_con_originales_devuelve_una_copia_con_el_texto_original():
    from fabrica.editar.componer import Linea
    plan = {"lineas": [{"t0": 0, "t1": 1, "frase": "Hola"}, {"t0": 1, "t1": 2, "frase": "Adiós"}]}
    nuevo = tecnica.con_originales(plan, [Linea(0, 1, "A", "Hola", None, "Hello"), Linea(1, 2, "A", "Adiós")])
    assert nuevo["lineas"][0]["original"] == "Hello" and "original" not in nuevo["lineas"][1]
    assert "original" not in plan["lineas"][0]


def test_revisar_usa_la_transcripcion_del_plan(limpio):
    plan = {**PLAN_VIDEO, "lineas": [linea(1.0)], "transcripcion": {"palabras": [{"texto": "watch", "t0": 0.5, "t1": 0.8}]}}
    ok, det = punto(tecnica.revisar(limpio, plan, CFG), "Subtítulo")
    assert not ok and "empieza +0,50 s después de «Watch» (0,50 s)" in det


# ---------------------------------------------------------------- CLI

def test_la_linea_de_comandos_sale_0_en_verde_y_1_en_rojo(carpeta, limpio, capsys):
    plan = carpeta / "plan.json"
    plan.write_text(json.dumps(PLAN_VIDEO), encoding="utf-8")
    assert tecnica.main([str(limpio), str(plan), "--raiz", str(RAIZ_REAL)]) == 0
    assert "[técnica] Sin destellos" in capsys.readouterr().out
    rojo = clip(carpeta / "cli-rojo.mp4", vf=CAJA_NEGRA.format(c="black", cuando="eq(n,45)"))
    assert tecnica.main([str(rojo), str(plan)]) == 1
    assert "NO: [técnica] Sin destellos" in capsys.readouterr().out


def test_la_linea_de_comandos_con_pistas(carpeta, limpio, fuentes_de_mezcla):
    voz, musica = fuentes_de_mezcla
    pistas = mezcla.mezclar(voz, musica, carpeta / "cli.wav", duracion=4, cfg=CFG)
    plan = carpeta / "plan-mezcla.json"
    plan.write_text(json.dumps({"lineas": [], "audio": {"tipo": "mezcla"}}), encoding="utf-8")
    assert tecnica.main([str(limpio), str(plan), "--voz", str(pistas["voz"]), "--musica", str(pistas["musica"])]) == 0
    with pytest.raises(SystemExit):
        tecnica.main([str(limpio), str(plan), "--voz", str(pistas["voz"])])


# ---------------------------------------------------------------- conexión: hacer_toma, informativa

CLAVE = {"GEMINI_API_KEY": "clave-de-prueba"}


class ComponerQueCopia:
    """Reemplaza `componer`: deja en `salida` un MP4 ya hecho y devuelve el plan dado."""

    def __init__(self, origen: Path, plan: dict):
        self.origen, self.plan, self.llamadas = origen, plan, 0

    def __call__(self, marca, t, lineas, salida, **kw):
        self.llamadas += 1
        shutil.copy(self.origen, salida)
        return json.loads(json.dumps(self.plan))


def hacer(repo, tmp_path, video):
    return toma.hacer_toma(repo, candidato(), ficha_propia(), AHORA, salida=tmp_path / "s", abrir=abrir_con(video),
                           transporte=Transporte((200, sobre(respuesta_grabada("valida")))), entorno=CLAVE,
                           espera=lambda s: None)


def estilo_prohibido(*a, **k):
    raise AssertionError("la puerta de estilo no debía correr")


def test_una_tecnica_roja_descarta_la_toma_sin_correr_la_de_estilo(repo, tmp_path, video20, carpeta, monkeypatch):
    plan = {**PLAN_VIDEO, "duracion": 3.0}
    monkeypatch.setattr(toma, "componer", ComponerQueCopia(congelado(carpeta, "toma-congelada.mp4", 30, 59), plan))
    monkeypatch.setattr(estilo, "revisar", estilo_prohibido)
    r = hacer(repo, tmp_path, video20)
    assert not r.ok and r.gastar is True and r.mp4 is None
    assert r.motivos and all(m.startswith("puerta técnica: [técnica] ") for m in r.motivos)
    assert "congelado de 1,00 a 2,00 s" in r.motivos[0] and "Sin congelados" in r.motivos[0]
    assert r.puntos and not all(ok for _, ok, _ in r.puntos)
    assert not list((tmp_path / "s").glob("*.mp4"))              # el MP4 descartado no queda


def test_una_tecnica_en_verde_deja_pasar_a_la_puerta_de_estilo(repo, tmp_path, video20, limpio, monkeypatch):
    monkeypatch.setattr(toma, "componer", ComponerQueCopia(limpio, {**PLAN_VIDEO, "duracion": 3.0}))
    llamadas = []
    monkeypatch.setattr(estilo, "revisar", lambda *a, **k: llamadas.append(1) or [("[plan] Algo", False, "mal")])
    r = hacer(repo, tmp_path, video20)
    assert not r.ok and llamadas == [1] and r.motivos == ["puerta de estilo: [plan] Algo (mal)"]
    assert [n for n, _, _ in r.puntos][:5] == [n for n, _, _ in tecnica.revisar(limpio, PLAN_VIDEO, CFG)]


def test_la_informativa_con_un_destello_se_descarta_con_puerta_tecnica(repo, tmp_path, carpeta, monkeypatch):
    destello = clip(carpeta / "informativa-destello.mp4", vf=CAJA_NEGRA.format(c="black", cuando="eq(n,45)"))
    monkeypatch.setattr(informativa, "componer", ComponerQueCopia(destello, {**PLAN_SIN_VIDEO, "duracion": 3.0}))
    monkeypatch.setattr(informativa.mod_musica, "elegir", lambda raiz, n: {"id": "pista"})
    monkeypatch.setattr(estilo, "revisar", estilo_prohibido)
    entrada = {"id": "dato-1", "gancho": "Un dato nuevo", "palabra_lapiz": "dato", "datos": ["Uno", "Dos"]}
    r = informativa.armar(repo, entrada, AHORA, tmp_path / "inf")
    assert not r.ok and r.motivos[0].startswith("puerta técnica: [técnica] Sin destellos de 1 cuadro")
    assert "cuadro 45" in r.motivos[0] and not list((tmp_path / "inf").glob("*.mp4"))


def test_rojos_distingue_la_puerta_de_cada_punto():
    puntos = [("[técnica] A", False, "x"), ("[plan] B", False, ""), ("[técnica] C", True, "")]
    assert tecnica.rojos(puntos) == ["puerta técnica: [técnica] A (x)", "puerta de estilo: [plan] B"]


# ---------------------------------------------------------------- validación de config/tecnica.yml

def errores(repo):
    return validar.validar(repo).errores


def cambiar(repo, editar_yaml, fn):
    editar_yaml(repo, "config/tecnica.yml", fn)


def test_el_repo_con_tecnica_valida_pasa(repo):
    assert errores(repo) == []


@pytest.mark.parametrize("bloque,clave,valor,mensaje", [
    ("destellos", "fraccion_min", 1.5, "destellos.fraccion_min debe ser un número mayor que 0"),
    ("destellos", "cuadros_max", 0, "destellos.cuadros_max"),
    ("destellos", "cuadros_max", 1.5, "destellos.cuadros_max debe ser un entero"),
    ("destellos", "negro_max", 300, "destellos.negro_max"),
    ("congelados", "max_s", -1, "congelados.max_s"),
    ("congelados", "dif_percentil", 20, "congelados.dif_percentil"),
    ("subtitulos", "tolerancia_s", "mucha", "subtitulos.tolerancia_s"),
    ("subtitulos", "exigir_transcripcion", "sí", "subtitulos.exigir_transcripcion debe ser true o false"),
    ("ducking", "margen_db", True, "ducking.margen_db"),
    ("ducking", "voz_activa_db", 5, "ducking.voz_activa_db"),
    ("clipping", "umbral", 2, "clipping.umbral"),
    ("clipping", "muestras_seguidas", 0, "clipping.muestras_seguidas"),
    ("analisis", "ancho", 2, "analisis.ancho"),
])
def test_un_valor_malo_de_tecnica_da_error(repo, editar_yaml, bloque, clave, valor, mensaje):
    cambiar(repo, editar_yaml, lambda d: d[bloque].__setitem__(clave, valor) or d)
    assert any("config/tecnica.yml" in e and mensaje in e for e in errores(repo)), errores(repo)


def test_un_valor_malo_del_sidechain_da_error(repo, editar_yaml):
    cambiar(repo, editar_yaml, lambda d: d["ducking"]["sidechain"].__setitem__("ratio", 50) or d)
    assert any("ducking.sidechain.ratio" in e for e in errores(repo))
    cambiar(repo, editar_yaml, lambda d: d["ducking"]["sidechain"].pop("attack") and d)
    assert any("ducking.sidechain sin la clave attack" in e for e in errores(repo))


def test_falta_un_bloque_o_el_archivo_da_error(repo, editar_yaml):
    cambiar(repo, editar_yaml, lambda d: d.pop("clipping") and d)
    assert any("falta el bloque clipping" in e for e in errores(repo))
    (repo / "config" / "tecnica.yml").unlink()
    assert any("config/tecnica.yml: no existe" in e for e in errores(repo))


def test_negro_max_debe_ser_menor_que_blanco_min(repo, editar_yaml):
    cambiar(repo, editar_yaml, lambda d: d["destellos"].update(negro_max=240, blanco_min=200) or d)
    assert any("negro_max debe ser menor que blanco_min" in e for e in errores(repo))


def test_la_puerta_de_validar_corre_tecnica_desde_la_linea_de_comandos(repo, editar_yaml):
    cambiar(repo, editar_yaml, lambda d: d["clipping"].__setitem__("umbral", 5) or d)
    r = subprocess.run(["python", "-m", "fabrica.validar", "--raiz", str(repo)], capture_output=True, text=True,
                       cwd=RAIZ_REAL)
    assert r.returncode == 1 and "config/tecnica.yml: clipping.umbral" in r.stdout

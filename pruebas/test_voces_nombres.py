"""#19 + #24: los subtítulos por voz también respetan la puerta de nombres (nombre sólo de la ficha u OTRA VOZ,
y nunca un nombre de config/vetados.yml)."""
from conftest import RAIZ_REAL
from fabrica import voces
from fabrica.editar import nombres
from fabrica.editar.componer import Linea
from fabrica.editar.lienzo import Toma
from fabrica.editar.marca import Marca
from fabrica.voces import Cfg, Transcripcion

TOMA = Toma(numero=1, gancho="Gol de último minuto", palabra_lapiz="Gol", lapiz="círculo",
            escena="INT. STREAM DE SEBASBERON", credito="Con: sebasberon")


def _lineas(palabras_voz):
    marca = Marca.cargar(RAIZ_REAL)
    palabras = [{"texto": w, "t0": i * 0.4, "t1": i * 0.4 + 0.3, "p": 0.9} for i, (w, _) in enumerate(palabras_voz)]
    tr = Transcripcion("es", 1.0, [{"t0": 0, "t1": 8, "texto": "", "no_speech_prob": 0, "avg_logprob": 0,
                                    "palabras": palabras}], "base", 0.1)
    turnos = [{"t0": i * 0.4, "t1": i * 0.4 + 0.4, "voz": v} for i, (_, v) in enumerate(palabras_voz)]
    res = voces.lineas_desde_transcripcion(tr, turnos, marca, "twitch.tv/sebasberon", Cfg(nombre_otra="WESTCOL"))
    return marca, [Linea(t0=r["t0"], t1=r["t1"], nombre=r["hablante"], frase=r["es"], original=r["es"], voz=r["voz"])
                   for r in res]


def test_los_subtitulos_por_voz_solo_llevan_el_nombre_de_la_ficha_u_otra_voz():
    marca, lineas = _lineas([("vamos", "streamer"), ("con", "streamer"), ("todo", "otra"), ("ya", "otra")])
    assert {ln.nombre for ln in lineas} == {"SEBASBERON", "OTRA VOZ"}       # aunque la config diga otro nombre
    assert nombres.revisar_nombres(TOMA, lineas, "sebasberon", RAIZ_REAL, marca) == []


def test_un_nombre_vetado_dicho_en_la_transcripcion_se_rechaza():
    marca, lineas = _lineas([("mira", "streamer"), ("a", "streamer"), ("westcol", "streamer"), ("ahora", "streamer")])
    motivos = nombres.revisar_nombres(TOMA, lineas, "sebasberon", RAIZ_REAL, marca)
    assert any("westcol" in m for m in motivos)


def test_un_rotulo_inventado_o_vetado_en_las_lineas_de_voces_se_rechaza():
    marca, lineas = _lineas([("hola", "streamer")])
    for malo in ("WESTCOL", "JUGADOR"):
        lineas[0].nombre = malo
        assert nombres.revisar_nombres(TOMA, lineas, "sebasberon", RAIZ_REAL, marca)


def test_el_rotulo_recortado_a_la_columna_sigue_siendo_el_del_creador():
    marca = Marca.cargar(RAIZ_REAL)
    largo = "creador_con_un_nombre_larguisimo_de_prueba_xxxxxxxxxxxxxxxxxxxx"
    rotulo = voces.nombre_visible(largo, marca)
    toma = Toma(numero=1, gancho="Gol", palabra_lapiz="Gol", lapiz="círculo", escena="EXT. CALLE", credito="Con: x")
    assert nombres.revisar_nombres(toma, [Linea(t0=0, t1=2, nombre=rotulo, frase="hola")], largo, RAIZ_REAL, marca) == []


def test_las_frases_de_whisper_pasan_el_filtro_de_spoilers_de_la_temporada():
    # Revisión de Codex (22 nov): la transcripción de Whisper reemplaza las líneas de Gemini después de
    # validar_respuesta; sin esto, un spoiler que Gemini había omitido podía publicarse en los subtítulos.
    from types import SimpleNamespace as L
    from fabrica.editar import toma as mod_toma
    temporada = {"palabras_evitar": ["final", "Lucía muere"]}
    limpias = [L(nombre="TAISONTV", frase="qué locura de persecución", acotacion="")]
    sucias = [L(nombre="TAISONTV", frase="no me digas el FINAL del juego", acotacion=""),
              L(nombre="OTRA VOZ", frase="ya vi que Lucia muere", acotacion="")]
    assert mod_toma._spoilers_en_lineas(limpias, temporada) == []
    motivos = mod_toma._spoilers_en_lineas(sucias, temporada)
    assert len(motivos) == 2 and "spoiler" in motivos[0] and "línea 2" in motivos[1]

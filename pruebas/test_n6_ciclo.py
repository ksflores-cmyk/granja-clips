"""N6: el ciclo (fabrica.ciclo) y el contrato del workflow fabrica.yml. Sin red ni claves reales."""
import csv
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import falsos_n5 as F
from fabrica import ciclo, informativa, publicar, registro
from fabrica.detectar import Candidato
from fabrica.editar.toma import Resultado
from fabrica.pausa import ZONA
from fabrica.simulacion import BufferSimulado, SupabaseSimulado

CLAVE = "bfr_clave-inventada-n6"            # EJEMPLO-FALSO
SUPA_URL = "https://proyecto-falso.supabase.invalid"
SUPA_CLAVE = "sb_secret_inventada-n6"       # EJEMPLO-FALSO
AHORA = datetime(2026, 11, 18, 16, 7, tzinfo=timezone.utc)      # 10:07 en Guatemala; el hueco de las 11:00 cae a 53 min
ENV = {"BUFFER_API_KEY": CLAVE, "SUPABASE_URL": SUPA_URL, "SUPABASE_SERVICE_KEY": SUPA_CLAVE}
RAIZ_REAL = Path(__file__).resolve().parent.parent
VERDE = lambda raiz: SimpleNamespace(errores=[], avisos=[])  # noqa: E731

FICHA = """
- id: fuente-prueba
  tipo: A
  creador: "@ejemplo"
  origen: drive
  canal_id: null
  permiso_url: "https://ejemplo.invalid/permiso"
  permiso_leido: 2026-11-17
  reglas:
    duracion_s: [7, 30]
    plataformas: [shorts, tiktok, reels]
    etiquetas_obligatorias: ["#GTA6", "#Gaming", "#Clips"]
    divulgacion: "ninguna"
    audiencia: "sin restricción"
  pilares: [primeras_veces, momentos]
  campana: null
  estado: vigente
  uso: produccion
  publicable: true
"""


@pytest.fixture
def r(repo):
    """Copia del repo sin PAUSA, en vivo con shorts activo, con los canales confirmados contra el Buffer simulado."""
    (repo / "PAUSA").unlink()
    (repo / "config" / "canales.yml").write_text(
        "".join(f"{p}: {{servicio: {s}, nombre_esperado: guionvivo, buffer_channel_id: null, buffer_org_id: null}}\n"
                for p, s in (("shorts", "youtube"), ("tiktok", "tiktok"), ("reels", "instagram"))),
        encoding="utf-8")
    publicar.guardar_ids_de_canales(
        repo, {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"},
        dict.fromkeys(("shorts", "tiktok", "reels"), "org-ejemplo-001"))
    cambiar_modo(repo, "vivo", ["shorts"])
    with open(repo / "fuentes.yml", "a", encoding="utf-8") as f:
        f.write(FICHA)
    return repo


def cambiar_modo(repo, modo, activas):
    ruta = repo / "config" / "modo.yml"
    d = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    d.update(modo=modo, plataformas_activas=activas)
    ruta.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")


def cand(id_origen="clip-1", pilar=None, hace_min=10):
    return Candidato(fuente_id="fuente-prueba", origen="brief", id_origen=id_origen,
                     url=f"https://ejemplo.invalid/{id_origen}", titulo="t", creador="@ejemplo", idioma="es",
                     juego_id=None, duracion_s=None, vistas=0, vistas_por_hora=0.0,
                     creado_en=AHORA - timedelta(minutes=hace_min), vod_offset=None, pilar=pilar)


def mp4_falso(carpeta: Path, nombre="toma.mp4") -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / nombre
    ruta.write_bytes(b"video-inventado")
    ruta.with_suffix(".plan.json").write_text("{}", encoding="utf-8")
    return ruta


class Hacer:
    """hacer_toma falso: espía los intentos y devuelve ok o descarte."""

    def __init__(self, repo, oks=(True,), motivo="ojos: descartado", gemini=None):
        self.repo, self.oks, self.motivo, self.gemini, self.llamadas = repo, list(oks), motivo, gemini, []

    def __call__(self, raiz, c, ficha, ahora, salida=None, **kw):
        self.llamadas.append(c.id_origen)
        ok = self.oks[min(len(self.llamadas), len(self.oks)) - 1]
        if not ok:
            return Resultado(False, [self.motivo], gemini=self.gemini)
        n = registro.contador_toma(raiz)
        fila = {"toma": n, "fecha": "2026-11-18", "hora": "10:07", "pilar": c.pilar or "momentos",
                "fuente_id": ficha.get("id"), "campana": None, "gancho": f"Gancho de prueba {n}",
                "duracion_s": 12.0, "credito": "Con: @ejemplo"}
        return Resultado(True, [], mp4_falso(Path(salida)), {}, None, None, [("[plan] algo", True, "")], fila)


def armar_falso(raiz, entrada, ahora, salida):
    n = registro.contador_toma(raiz)
    fila = {"toma": n, "pilar": "informativa", "fuente_id": informativa.FICHA_ID, "gancho": entrada["gancho"],
            "duracion_s": 11.0, "credito": informativa.CREDITO}
    return Resultado(True, [], mp4_falso(Path(salida)), {}, None, None, [("[plan] algo", True, "")], fila)


def correr(repo, *, env=ENV, ahora=AHORA, cands=(), hacer=None, buffer=None, almacen=None, validar_fn=VERDE, **kw):
    sal = []
    cod = ciclo.ejecutar(repo, ahora=ahora, env=env, imprimir=sal.append, validar_fn=validar_fn,
                         detectar_fn=lambda raiz, a, c: (list(cands), []),
                         hacer_toma_fn=hacer or Hacer(repo),
                         transporte_buffer=buffer if buffer is not None else BufferSimulado(),
                         transporte_almacen=almacen if almacen is not None else SupabaseSimulado(), **kw)
    return cod, "\n".join(sal)


def filas(repo):
    with open(repo / "marcador.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def anotar(repo, **fila):
    base = {"id_clip": f"x-{len(filas(repo))}", "toma": 90, "fecha": "2026-11-18", "hora": "11:00",
            "plataforma": "shorts", "pilar": "momentos", "fuente_id": "fuente-prueba"}
    registro.anotar_fila(repo, {**base, **fila})


def espias():
    return SimpleNamespace(buffer=F.Espia([F.resp(200, "{}")]), almacen=F.Espia([F.resp(200, "{}")]),
                           detectar=[], hacer=[])


def correr_con_espias(repo, ahora=AHORA, **kw):
    e = espias()
    sal = []
    cod = ciclo.ejecutar(repo, ahora=ahora, env=ENV, imprimir=sal.append, validar_fn=VERDE,
                         detectar_fn=lambda *a: e.detectar.append(a) or ([], []),
                         hacer_toma_fn=lambda *a, **k: e.hacer.append(a) or Resultado(False, ["x"]),
                         transporte_buffer=e.buffer, transporte_almacen=e.almacen, **kw)
    return cod, "\n".join(sal), e


def sin_llamadas(e):
    return e.buffer.llamadas == [] and e.almacen.llamadas == [] and e.detectar == [] and e.hacer == []


# ---------------------------------------------------------------- 1. PAUSA y ensayo forzado

def test_con_pausa_no_hay_ninguna_llamada(r):
    (r / "PAUSA").write_text("motivo: prueba\ndesde: 2026-11-17T16:30:00-06:00\nautor: x\n", encoding="utf-8")
    cod, sal, e = correr_con_espias(r, ahora=AHORA)
    assert cod == 0 and sal.startswith("PAUSA: prueba") and sin_llamadas(e)


def test_con_pausa_no_se_llama_a_validar_ni_a_la_cadencia_ni_al_tope_ni_al_hueco_ni_a_la_red(r, monkeypatch):
    (r / "PAUSA").write_text("motivo: prueba\n", encoding="utf-8")
    llamado = []
    for nombre in ("en_cadencia", "minutos_del_dia", "buscar_hueco", "hay_mantenimiento", "elegir_candidatos",
                   "publicar_en_vivo", "mantenimiento"):
        monkeypatch.setattr(ciclo, nombre, lambda *a, _n=nombre, **k: llamado.append(_n))
    e = espias()
    for decidir in (False, True):
        sal = []
        cod = ciclo.ejecutar(r, ahora=AHORA, decidir=decidir, env=ENV, imprimir=sal.append,
                             validar_fn=lambda raiz: llamado.append("validar"),
                             detectar_fn=lambda *a: e.detectar.append(a) or ([], []),
                             hacer_toma_fn=lambda *a, **k: e.hacer.append(a),
                             transporte_buffer=e.buffer, transporte_almacen=e.almacen)
        assert cod == 0 and "PAUSA: prueba" in sal[0]
    assert llamado == [] and sin_llamadas(e)


def test_decidir_con_pausa_escribe_trabajo_no(r, tmp_path):
    (r / "PAUSA").write_text("motivo: prueba\n", encoding="utf-8")
    salida = tmp_path / "gh_output"
    cod, sal = correr(r, decidir=True, env={**ENV, "GITHUB_OUTPUT": str(salida)})
    assert cod == 0 and salida.read_text() == "trabajo=no\n" and "trabajo=no" in sal


def test_ensayo_forzado_con_modo_vivo_se_niega(r):
    cod, sal, e = correr_con_espias(r, ensayo_forzado=True)
    assert cod == 2 and "se niega" in sal and sin_llamadas(e)


def test_ensayo_forzado_con_modo_vivo_y_pausa_tambien_se_niega(r):
    (r / "PAUSA").write_text("motivo: prueba\n", encoding="utf-8")
    cod, sal, e = correr_con_espias(r, ensayo_forzado=True)
    assert cod == 2 and sin_llamadas(e)


def test_ensayo_forzado_corre_con_pausa_sin_publicar_ni_tocar_el_repo(r, tmp_path, monkeypatch):
    (r / "PAUSA").write_text("motivo: prueba\n", encoding="utf-8")
    cambiar_modo(r, "ensayo", [])
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    buf, supa = BufferSimulado(), SupabaseSimulado()
    antes = {p: (r / p).read_bytes() for p in ("marcador.csv", "registro.md", "marca.yml", "informativas.yml")}
    cod, sal = correr(r, ensayo_forzado=True, buffer=buf, almacen=supa, salida_dir=tmp_path / "s", env={})
    assert cod == 0 and "carga válida" in sal and "NO válida" not in sal
    assert list((tmp_path / "s").glob("*.mp4"))
    assert "Puerta de estilo: VERDE" in (tmp_path / "s" / "informe-puerta.txt").read_text(encoding="utf-8")
    assert not buf.hubo("createPost") and supa.llamadas == []
    assert (r / "PAUSA").exists()
    assert {p: (r / p).read_bytes() for p in antes} == antes


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
@pytest.mark.slow
def test_ensayo_forzado_de_punta_a_punta_produce_la_informativa_real(repo, tmp_path):
    r = repo                                        # copia del repo real: con PAUSA y en ensayo
    assert (r / "PAUSA").exists()
    sal = []
    cod = ciclo.ejecutar(r, ahora=AHORA, ensayo_forzado=True, salida_dir=tmp_path / "s", env={},
                         imprimir=sal.append, detectar_fn=lambda *a: ([], []))
    assert cod == 0, "\n".join(sal)
    informe = (tmp_path / "s" / "informe-puerta.txt").read_text(encoding="utf-8")
    assert informe.startswith("Puerta de estilo: VERDE") and "ROJO" not in informe
    assert list((tmp_path / "s").glob("toma-*.mp4")) and list((tmp_path / "s").glob("toma-*.plan.json"))


def test_validar_en_rojo_sale_con_1_sin_publicar(r):
    e = espias()
    sal = []
    cod = ciclo.ejecutar(r, ahora=AHORA, env=ENV, imprimir=sal.append,
                         validar_fn=lambda raiz: SimpleNamespace(errores=["algo roto"], avisos=[]),
                         detectar_fn=lambda *a: e.detectar.append(a) or ([], []),
                         transporte_buffer=e.buffer, transporte_almacen=e.almacen)
    assert cod == 1 and "fabrica.validar en rojo" in "\n".join(sal) and sin_llamadas(e)


# ---------------------------------------------------------------- 2. cadencia y tope

@pytest.mark.parametrize("dia", [18, 19, 20])
@pytest.mark.parametrize("hora_gt", range(24))
def test_del_18_al_20_corre_cada_hora(dia, hora_gt):
    ahora = datetime(2026, 11, dia, hora_gt, 7, tzinfo=ZONA)
    assert ciclo.en_cadencia(ahora)


@pytest.mark.parametrize("hora_gt", range(24))
def test_desde_el_21_sólo_en_horas_multiplo_de_3(hora_gt):
    assert ciclo.en_cadencia(datetime(2026, 11, 21, hora_gt, 7, tzinfo=ZONA)) == (hora_gt % 3 == 0)
    assert ciclo.en_cadencia(datetime(2026, 12, 3, hora_gt, 7, tzinfo=ZONA)) == (hora_gt % 3 == 0)


def test_la_cadencia_cuenta_el_dia_en_hora_de_guatemala_no_en_utc():
    # 21 nov 05:30 UTC = 20 nov 23:30 GT: aún es del 18 al 20 (cada hora) aunque la hora 23 no sea múltiplo de 3.
    assert ciclo.en_cadencia(datetime(2026, 11, 21, 5, 30, tzinfo=timezone.utc)) is True
    # 21 nov 06:30 UTC = 21 nov 00:30 GT: ya es el 21 y la hora 0 es múltiplo de 3.
    assert ciclo.en_cadencia(datetime(2026, 11, 21, 6, 30, tzinfo=timezone.utc)) is True
    assert ciclo.en_cadencia(datetime(2026, 11, 21, 7, 30, tzinfo=timezone.utc)) is False      # 01:30 GT


def test_fuera_de_cadencia_sin_hueco_sale_sin_llamadas_ni_mantenimiento(r):
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(
        json.dumps([{"id": "a", "estado": "incierto", "plataforma": "shorts", "toma": 3}]), encoding="utf-8")
    cod, sal, e = correr_con_espias(r, ahora=datetime(2026, 11, 21, 11, 7, tzinfo=ZONA))   # sin hueco y 11 no es múltiplo de 3
    assert cod == 0 and "fuera de cadencia" in sal and sin_llamadas(e)


def test_en_hora_multiplo_de_3_sin_hueco_si_hace_el_mantenimiento(r, monkeypatch):
    llamadas = []
    monkeypatch.setattr(publicar.Publicador, "limpiar_enviados", lambda self: llamadas.append(1) or [])
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(
        json.dumps([{"id": "a", "estado": "creado", "ruta": "tomas/x.mp4", "plataforma": "shorts"}]), encoding="utf-8")
    cod, sal = correr(r, ahora=datetime(2026, 11, 21, 12, 7, tzinfo=ZONA))
    assert "sin hueco" in sal and llamadas == [1]


@pytest.mark.parametrize("corrida_gt,hueco", [((10, 7), "11:00"), ((14, 7), "15:00"), ((18, 7), "19:00")])
def test_desde_el_21_los_tres_huecos_normales_se_cubren(r, corrida_gt, hueco):
    ahora = datetime(2026, 11, 21, *corrida_gt, tzinfo=ZONA)
    h = ciclo.buscar_hueco(r, ahora, ["shorts"])
    assert h is not None and h.hora == hueco
    cod, sal = correr(r, ahora=ahora, hacer=Hacer(r, oks=(False,)), cands=[cand("c1")])
    assert "fuera de cadencia" not in sal and "sin hueco" not in sal


def test_desde_el_21_el_decidir_dice_si_en_cada_hueco_y_no_en_las_demas_horas(r, tmp_path):
    for hh, esperado in ((10, "si"), (11, "no"), (12, "no"), (14, "si"), (18, "si"), (19, "no")):
        salida = tmp_path / f"gh{hh}"
        correr(r, decidir=True, ahora=datetime(2026, 11, 21, hh, 7, tzinfo=ZONA),
               env={**ENV, "GITHUB_OUTPUT": str(salida)})
        assert salida.read_text() == f"trabajo={esperado}\n", hh


def minutos(r, **datos):
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "actions-minutos.json").write_text(json.dumps(datos), encoding="utf-8")


# AHORA es 10:07 GT: ese día ya corrieron 11 `decidir` de 1 minuto (los de las 0:07 a las 10:07).
def test_el_tope_exige_20_minutos_libres_de_los_60(r):
    minutos(r, **{"2026-11-18": 30})                       # 30 + 11 de decidir = 41: quedan 19
    cod, sal, e = correr_con_espias(r)
    assert cod == 0 and "tope diario de Actions" in sal and sin_llamadas(e)
    minutos(r, **{"2026-11-18": 60})
    assert "tope diario" in correr_con_espias(r)[1]


def test_con_29_minutos_y_11_de_decidir_quedan_20_y_aun_corre(r):
    minutos(r, **{"2026-11-18": 29, "2026-11-17": 500})    # otro día no cuenta
    cod, sal = correr(r, hacer=Hacer(r, oks=(False,)), cands=[cand()])
    assert "tope diario" not in sal and "sin hueco" not in sal


def test_cada_decidir_cuenta_un_minuto():
    assert ciclo.minutos_de_decidir(datetime(2026, 11, 18, 0, 7, tzinfo=ZONA)) == 1
    assert ciclo.minutos_de_decidir(datetime(2026, 11, 18, 23, 7, tzinfo=ZONA)) == 24


def test_un_producir_que_murio_sin_cerrar_cobra_20_minutos_a_la_siguiente(r):
    ciclo.marcar_inicio(r, AHORA - timedelta(hours=1), "111")
    assert ciclo.minutos_del_dia(r, AHORA, run_id="222") == 20          # otra corrida: se cobra
    assert ciclo.minutos_del_dia(r, AHORA, run_id="111") == 0           # la propia corrida no se cobra a sí misma
    minutos_antes = ciclo.minutos_usados(r, AHORA, "222")
    assert minutos_antes == 20 + 11
    cod, sal, e = correr_con_espias(r)                                  # 31 + 20 = 51 <= 60: aún entra
    assert "tope diario" not in sal
    ciclo.sumar_minutos(r, AHORA, 5, run_id="222")                      # el cierre de la siguiente: cobra la ajena
    datos = json.loads((r / "estado" / "actions-minutos.json").read_text())
    assert "_en_curso" not in datos and datos["2026-11-18"] == 25


def test_el_cierre_normal_borra_la_marca_sin_cobrar_de_mas(r):
    ciclo.marcar_inicio(r, AHORA, "111")
    ciclo.sumar_minutos(r, AHORA, 4, run_id="111")
    datos = json.loads((r / "estado" / "actions-minutos.json").read_text())
    assert datos == {"2026-11-18": 4}


def test_la_marca_de_inicio_vieja_entra_en_la_admision(r):
    ciclo.marcar_inicio(r, AHORA - timedelta(hours=1), "111")
    minutos_json = json.loads((r / "estado" / "actions-minutos.json").read_text())
    minutos_json["2026-11-18"] = 10
    (r / "estado" / "actions-minutos.json").write_text(json.dumps(minutos_json))
    assert ciclo.minutos_usados(r, AHORA, "222") == 41            # 10 anotados + 20 de la marca + 11 de decidir
    assert ciclo.admite_producir(r, AHORA, "222") is False        # 41 + 20 > 60


def test_sumar_minutos_acumula_por_dia_y_poda(r):
    assert ciclo.sumar_minutos(r, AHORA, 3) == 3
    assert ciclo.sumar_minutos(r, AHORA, 4.5) == 7.5
    ruta = r / "estado" / "actions-minutos.json"
    datos = json.loads(ruta.read_text())
    datos["2026-10-01"] = 9
    ruta.write_text(json.dumps(datos))
    ciclo.sumar_minutos(r, AHORA, 1)
    assert "2026-10-01" not in json.loads(ruta.read_text())
    assert ciclo.minutos_del_dia(r, AHORA) == 8.5


# ---------------------------------------------------------------- 3. huecos

def test_hueco_de_estreno_dentro_de_la_ventana(r):
    h = ciclo.buscar_hueco(r, AHORA, ["shorts", "tiktok"])
    assert (h.fecha, h.hora, h.plataformas) == ("2026-11-18", "11:00", ["shorts", "tiktok"])


def test_el_hueco_de_estreno_de_las_13_no_existe_fuera_de_la_ventana(r):
    # 22 nov, 12:07 GT: el 13:00 es hueco de estreno y la ventana terminó el 20; el 15:00 queda a más de 75 min.
    assert ciclo.buscar_hueco(r, datetime(2026, 11, 22, 12, 7, tzinfo=ZONA), ["shorts"]) is None
    # y el 11:00 normal sí cuenta fuera de la ventana
    h = ciclo.buscar_hueco(r, datetime(2026, 11, 22, 10, 7, tzinfo=ZONA), ["shorts"])
    assert (h.fecha, h.hora) == ("2026-11-22", "11:00")


def test_un_hueco_normal_no_cuenta_dentro_de_la_ventana_si_no_esta_en_estreno(r, editar_yaml):
    editar_yaml(r, "config/horarios.yml", lambda d: {**d, "huecos_estreno": ["13:00"], "huecos_normales": ["11:00"]})
    assert ciclo.buscar_hueco(r, AHORA, ["shorts"]) is None        # 11:00 normal, pero estamos en estreno


def test_hueco_ocupado_en_el_marcador_no_cuenta_para_esa_plataforma(r):
    anotar(r, plataforma="shorts")
    assert ciclo.buscar_hueco(r, AHORA, ["shorts"]) is None
    assert ciclo.buscar_hueco(r, AHORA, ["shorts", "tiktok"]).plataformas == ["tiktok"]


@pytest.mark.parametrize("hh,mm,esperado", [(10, 44, True), (10, 45, True), (10, 46, False), (9, 45, True),
                                            (9, 44, False), (9, 46, True), (10, 50, False)])
def test_ventana_de_15_a_75_minutos(r, hh, mm, esperado):
    # El hueco es 11:00 GT; "ahora" va de 9:44 (76 min) a 10:50 (10 min).
    ahora = datetime(2026, 11, 18, hh, mm, tzinfo=ZONA)
    # En 9:46 el siguiente hueco antes de las 11:00 sería ninguno (el de las 11:00 está a 74 min).
    assert (ciclo.buscar_hueco(r, ahora, ["shorts"]) is not None) == esperado


def test_sin_plataformas_activas_no_hay_hueco(r):
    assert ciclo.buscar_hueco(r, AHORA, []) is None


def test_decidir_escribe_si_o_no_en_github_output(r, tmp_path):
    salida = tmp_path / "gh"
    cod, sal = correr(r, decidir=True, env={**ENV, "GITHUB_OUTPUT": str(salida)})
    assert cod == 0 and salida.read_text() == "trabajo=si\n" and "trabajo=si" in sal
    salida2 = tmp_path / "gh2"
    cod, sal = correr(r, decidir=True, ahora=datetime(2026, 11, 18, 11, 7, tzinfo=ZONA),
                      env={**ENV, "GITHUB_OUTPUT": str(salida2)})
    assert salida2.read_text() == "trabajo=no\n" and "sin hueco" in sal


def test_decidir_no_hace_llamadas(r):
    cod, sal, e = correr_con_espias(r, decidir=True)
    assert cod == 0 and sin_llamadas(e)


def test_decidir_dice_si_cuando_hay_envios_pendientes_aunque_no_haya_hueco(r, tmp_path):
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(
        json.dumps([{"id": "a", "estado": "incierto", "plataforma": "shorts", "toma": 3}]), encoding="utf-8")
    salida = tmp_path / "gh"
    correr(r, decidir=True, ahora=datetime(2026, 11, 18, 11, 7, tzinfo=ZONA), env={**ENV, "GITHUB_OUTPUT": str(salida)})
    assert salida.read_text() == "trabajo=si\n"


# ---------------------------------------------------------------- 4. candidatos y respaldo

def test_orden_por_peso_del_pilar_y_luego_el_mas_reciente():
    t = yaml.safe_load((RAIZ_REAL / "temporada.yml").read_text(encoding="utf-8"))
    a = cand("momentos-nuevo", "momentos", 5)
    b = cand("primeras-viejo", "primeras_veces", 500)
    c = cand("primeras-nuevo", "primeras_veces", 20)
    d = cand("dato", "dato_util", 1)
    assert [x.id_origen for x in ciclo.ordenar([a, b, c, d], t)] == [
        "primeras-nuevo", "primeras-viejo", "momentos-nuevo", "dato"]


def test_el_pilar_sale_de_la_ficha_si_el_candidato_no_lo_trae(r):
    hacer = Hacer(r)
    correr(r, cands=[cand("c1")], hacer=hacer)
    assert filas(r)[0]["pilar"] == "primeras_veces"


def test_prueba_hasta_3_candidatos_y_cae_al_respaldo(r, monkeypatch):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    hacer = Hacer(r, oks=(False,))
    cod, sal = correr(r, cands=[cand(f"c{i}") for i in range(5)], hacer=hacer)
    assert len(hacer.llamadas) == 3
    assert filas(r)[0]["pilar"] == "informativa"


def test_el_primer_candidato_ok_es_la_toma_y_no_se_siguen_probando(r):
    hacer = Hacer(r, oks=(False, True))
    cod, sal = correr(r, cands=[cand("c1", hace_min=30), cand("c2", hace_min=20), cand("c3", hace_min=10)],
                      hacer=hacer)
    assert hacer.llamadas == ["c3", "c2"]       # del más reciente al más viejo; el segundo ya sirve
    assert filas(r)[0]["pilar"] == "primeras_veces"


def test_sin_candidatos_usa_la_informativa_de_respaldo(r, monkeypatch):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    cod, sal = correr(r)
    assert cod == 0
    f = filas(r)[0]
    assert f["pilar"] == "informativa" and f["fuente_id"] == "propio-tipografico" and f["plataforma"] == "shorts"


def test_regla_del_10_por_ciento_y_la_primera_toma_puede_ser_informativa(r):
    ok, motivo = informativa.cabe_en_el_tope(r, AHORA)
    assert ok and "primera toma" in motivo
    for n in range(1, 9):                                   # 8 tomas no informativas en los últimos 7 días
        anotar(r, id_clip=f"y-{n}", toma=n, fecha="2026-11-18", hora="08:00", pilar="momentos")
    assert informativa.cabe_en_el_tope(r, AHORA)[0] is False        # 1/9 = 11 %: no pasa
    anotar(r, id_clip="y-9", toma=9, fecha="2026-11-18", hora="08:00", pilar="momentos")
    assert informativa.cabe_en_el_tope(r, AHORA)[0] is True         # 1/10 = 10 %: cabe justo
    for n in range(10, 20):
        anotar(r, id_clip=f"y-{n}", toma=n, fecha="2026-11-18", hora="08:00", pilar="momentos")
    assert informativa.cabe_en_el_tope(r, AHORA)[0] is True         # 1/20 = 5 %


def test_la_regla_del_10_por_ciento_no_cuenta_lo_programado_a_futuro(r):
    for n in range(1, 9):                                   # 8 tomas programadas para más tarde hoy y mañana
        anotar(r, id_clip=f"f-{n}", toma=n, fecha="2026-11-18" if n < 5 else "2026-11-19",
               hora="11:00" if n < 5 else "08:00", pilar="momentos")
    ok, motivo = informativa.cabe_en_el_tope(r, AHORA)
    assert ok and "primera toma" in motivo                  # nada publicado todavía: no hay base
    anotar(r, id_clip="p-1", toma=20, fecha="2026-11-18", hora="10:07", pilar="momentos")   # justo ahora: ya cuenta
    anotar(r, id_clip="p-2", toma=21, fecha="2026-11-18", hora="10:08", pilar="momentos")   # un minuto después: no
    assert informativa.cabe_en_el_tope(r, AHORA) == (False, informativa.cabe_en_el_tope(r, AHORA)[1])


def test_la_regla_del_10_por_ciento_impide_el_respaldo(r, monkeypatch):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    anotar(r, id_clip="z-1", toma=1, hora="09:00", plataforma="tiktok", pilar="momentos")
    cod, sal = correr(r)
    assert "sin toma" in sal and "10%" in sal
    assert "sin toma" in (r / "registro.md").read_text(encoding="utf-8")
    assert len(filas(r)) == 1


@pytest.mark.parametrize("campo,valor", [("usada", True), ("vigente_hasta", "2026-11-18T09:00:00-06:00"),
                                         ("vigente_desde", "2026-11-18T12:00:00-06:00")])
def test_entrada_vencida_o_usada_no_sirve_de_respaldo(r, monkeypatch, campo, valor):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    ruta = r / "informativas.yml"
    d = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    d["informativas"][0][campo] = valor
    ruta.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    cod, sal = correr(r)
    assert "no hay informativa vigente" in sal and "sin toma" in sal and filas(r) == []


def vistos(r):
    ruta = r / "estado" / "vistos.txt"
    return ruta.read_text(encoding="utf-8").split() if ruta.exists() else []


def test_un_descarte_definitivo_de_gemini_se_anota_como_visto(r, monkeypatch):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    hacer = Hacer(r, oks=(False,), motivo="ojos: es una cinemática", gemini={"gancho": "x"})
    correr(r, cands=[cand("c1"), cand("c2", hace_min=20)], hacer=hacer)
    assert {"c1", "c2"} <= set(vistos(r))
    hacer2 = Hacer(r, oks=(False,))
    correr(r, ahora=datetime(2026, 11, 18, 12, 7, tzinfo=ZONA), cands=[cand("c1"), cand("c2")], hacer=hacer2)
    assert hacer2.llamadas == []                                    # ya vistos: no se vuelven a procesar


@pytest.mark.parametrize("motivo,gemini", [("ojos: sin GEMINI_API_KEY", None), ("ojos: Gemini respondió 429", None),
                                           ("descarga: fallo de conexión", None),
                                           ("puerta de estilo: algo", {"gancho": "x"})])
def test_un_fallo_de_red_cuota_o_estilo_no_se_anota_como_visto(r, monkeypatch, motivo, gemini):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    correr(r, cands=[cand("c1")], hacer=Hacer(r, oks=(False,), motivo=motivo, gemini=gemini))
    assert "c1" not in vistos(r)


# ---------------------------------------------------------------- 5. vivo y ensayo

def test_en_vivo_programa_registra_y_avanza_el_numero_de_toma(r):
    sim, supa = BufferSimulado(), SupabaseSimulado()
    antes = registro.contador_toma(r)
    cod, sal = correr(r, cands=[cand("c1")], buffer=sim, almacen=supa)
    assert cod == 0 and "shorts: programada" in sal
    assert sim.hubo("createPost")
    f = filas(r)
    assert len(f) == 1 and (f[0]["fecha"], f[0]["hora"], f[0]["plataforma"]) == ("2026-11-18", "11:00", "shorts")
    assert registro.contador_toma(r) == antes + 1
    assert "c1" in (r / "estado" / "vistos.txt").read_text(encoding="utf-8")
    assert "programada en shorts" in (r / "registro.md").read_text(encoding="utf-8")
    assert any(x[0] == "POST" and "/object/" in x[1] for x in supa.llamadas)


def test_en_vivo_la_carga_sale_a_la_hora_exacta_del_hueco(r):
    sim = BufferSimulado()
    correr(r, cands=[cand("c1")], buffer=sim)
    cargas = [v["input"] for v in sim.variables if "input" in v and "dueAt" in v["input"]]
    assert cargas and cargas[0]["dueAt"] == "2026-11-18T17:00:00.000Z"


class BufferParcial(BufferSimulado):
    """El primer createPost sale bien y el siguiente lo rechaza Buffer."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.creados = 0

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        if cuerpo and b"createPost" in cuerpo:
            self.creados += 1
            if self.creados > 1:
                self.consultas.append("createPost")
                return F.resp(200, {"data": {"createPost": {"__typename": "MutationError",
                                                            "message": "El video no cumple el formato"}}})
        return super().__call__(metodo, url, encabezados, cuerpo, tiempo)


def test_un_fallo_parcial_gasta_la_toma_y_la_siguiente_corrida_publica_en_las_tres(r):
    cambiar_modo(r, "vivo", ["shorts", "tiktok", "reels"])
    antes = registro.contador_toma(r)
    cod, sal = correr(r, cands=[cand("c1")], buffer=BufferParcial(), almacen=SupabaseSimulado())
    assert "shorts: programada" in sal and "tiktok: descartada" in sal
    assert registro.contador_toma(r) == antes + 1                      # una quedó programada: la toma se gastó
    assert [f["plataforma"] for f in filas(r)] == ["shorts"]
    # Segunda corrida, dos horas después (hueco de las 13:00): toma siguiente, en las tres plataformas.
    cod, sal = correr(r, ahora=datetime(2026, 11, 18, 12, 7, tzinfo=ZONA), cands=[cand("c2")],
                      buffer=BufferSimulado(), almacen=SupabaseSimulado())
    assert sal.count("programada") == 3 and "ya hay un envío" not in sal
    nuevas = [f for f in filas(r) if f["hora"] == "13:00"]
    assert sorted(f["plataforma"] for f in nuevas) == ["reels", "shorts", "tiktok"]
    assert {f["toma"] for f in nuevas} == {str(antes + 1)} and registro.contador_toma(r) == antes + 2


def test_si_ninguna_plataforma_se_programa_la_toma_no_se_gasta(r):
    antes = registro.contador_toma(r)
    correr(r, cands=[cand("c1")], buffer=BufferCuentaRechaza())
    assert registro.contador_toma(r) == antes and filas(r) == []


class BufferCuentaRechaza(BufferSimulado):
    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        if cuerpo and b"createPost" in cuerpo:
            return F.resp(200, {"data": {"createPost": {"__typename": "MutationError", "message": "Formato no válido"}}})
        return super().__call__(metodo, url, encabezados, cuerpo, tiempo)


class BufferIncierto(BufferSimulado):
    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        if cuerpo and b"createPost" in cuerpo:
            return F.resp(500, "error")
        return super().__call__(metodo, url, encabezados, cuerpo, tiempo)


def test_un_resultado_incierto_tambien_gasta_la_toma_para_no_bloquear_el_canal(r):
    antes = registro.contador_toma(r)
    cod, sal = correr(r, cands=[cand("c1")], buffer=BufferIncierto())
    assert "INCIERTO" in sal and registro.contador_toma(r) == antes + 1


def test_en_vivo_con_la_informativa_la_marca_usada(r, monkeypatch):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    correr(r)
    d = yaml.safe_load((r / "informativas.yml").read_text(encoding="utf-8"))
    assert d["informativas"][0]["usada"] is True


def test_en_ensayo_no_hay_createpost_ni_subida_y_queda_una_linea(r):
    cambiar_modo(r, "ensayo", ["shorts", "tiktok", "reels"])
    sim, supa = BufferSimulado(), SupabaseSimulado()
    antes = registro.contador_toma(r)
    cod, sal = correr(r, cands=[cand("c1")], buffer=sim, almacen=supa)
    assert cod == 0 and sal.count("carga válida") == 3
    assert not sim.hubo("createPost") and supa.llamadas == [] and filas(r) == []
    assert registro.contador_toma(r) == antes
    assert "ciclo: ensayo, toma" in (r / "registro.md").read_text(encoding="utf-8")


def test_sin_claves_el_ensayo_se_simula(r):
    cambiar_modo(r, "ensayo", ["shorts"])
    cod, sal = correr(r, env={}, cands=[cand("c1")])
    assert cod == 0 and "ensayo simulado" in sal and "pendiente de Kevin" in sal


def test_en_vivo_sin_claves_no_publica(r):
    cod, sal = correr(r, env={}, cands=[cand("c1")])
    assert "no se publica" in sal and filas(r) == []


def test_cada_ciclo_en_vivo_limpia_los_enviados(r, monkeypatch):
    llamadas = []
    monkeypatch.setattr(publicar.Publicador, "limpiar_enviados", lambda self: llamadas.append(1) or [])
    correr(r, cands=[cand("c1")])
    assert llamadas == [1]


def test_sin_hueco_pero_con_envios_abiertos_hace_el_mantenimiento(r, monkeypatch):
    llamadas = []
    monkeypatch.setattr(publicar.Publicador, "limpiar_enviados", lambda self: llamadas.append(1) or [])
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(
        json.dumps([{"id": "a", "estado": "creado", "ruta": "tomas/x.mp4", "plataforma": "shorts"}]), encoding="utf-8")
    cod, sal = correr(r, ahora=datetime(2026, 11, 18, 11, 7, tzinfo=ZONA))
    assert "sin hueco" in sal and llamadas == [1]


def test_sin_hueco_y_sin_pendientes_no_hay_llamadas(r):
    cod, sal, e = correr_con_espias(r, ahora=datetime(2026, 11, 18, 11, 7, tzinfo=ZONA))
    assert "sin hueco" in sal and sin_llamadas(e)


# ---------------------------------------------------------------- 6. avisos de envíos

def test_avisos_de_envios_inciertos_o_abandonados_uno_por_envio_sin_repetir(r):
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(json.dumps([
        {"id": "shorts|3|x", "estado": "incierto", "plataforma": "shorts", "toma": 3, "dueAt": "d1"},
        {"id": "tiktok|3|x", "estado": "abandonado", "plataforma": "tiktok", "toma": 3, "dueAt": "d1"},
        {"id": "reels|3|x", "estado": "creado", "plataforma": "reels", "toma": 3, "dueAt": "d1"}]), encoding="utf-8")
    ordenes = []

    def ejecutor(cmd, **kw):
        ordenes.append(cmd)
        return SimpleNamespace(returncode=0)

    assert len(ciclo.abrir_avisos(r, ejecutor)) == 2
    assert all(o[:3] == ["gh", "issue", "create"] for o in ordenes)
    assert ciclo.abrir_avisos(r, ejecutor) == [] and len(ordenes) == 2          # no se repite
    ruta = r / "estado" / "buffer-envios.json"
    libro = json.loads(ruta.read_text())
    libro[0]["estado"] = "abandonado"                                           # incierto -> abandonado: aviso nuevo
    ruta.write_text(json.dumps(libro))
    assert len(ciclo.abrir_avisos(r, ejecutor)) == 1


def test_si_gh_falla_el_aviso_queda_pendiente(r):
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(
        json.dumps([{"id": "a", "estado": "incierto", "plataforma": "shorts", "toma": 3}]), encoding="utf-8")
    assert ciclo.abrir_avisos(r, lambda cmd, **kw: SimpleNamespace(returncode=1)) == []
    assert len(ciclo.avisos_pendientes(r)) == 1


# ---------------------------------------------------------------- 7. alerta roja

def comprobar_alerta(repo, fragmento):
    assert (repo / "PAUSA").exists()
    a = json.loads((repo / "estado" / "alerta.json").read_text(encoding="utf-8"))
    assert set(a) == {"motivo", "cuando"} and fragmento in a["motivo"]
    todo = (repo / "estado" / "alerta.json").read_text(encoding="utf-8") + (repo / "PAUSA").read_text(encoding="utf-8")
    assert CLAVE not in todo and SUPA_CLAVE not in todo
    return a


def test_alerta_canal_que_no_es_el_nuestro(r):
    sim = BufferSimulado(canales="canales_ajeno.json")
    cod, sal = correr(r, cands=[cand("c1")], buffer=sim)
    assert cod == 0 and not sim.hubo("createPost")
    comprobar_alerta(r, "canal de")
    assert filas(r) == []


def test_alerta_cola_pausada(r):
    cambiar_modo(r, "vivo", ["shorts", "tiktok", "reels"])      # el archivo pausa la cola de Instagram
    cod, sal = correr(r, cands=[cand("c1")], buffer=BufferSimulado(canales="canales_pausada.json"))
    comprobar_alerta(r, "pausada")


def test_alerta_con_401_de_buffer(r):
    cod, sal = correr(r, cands=[cand("c1")], buffer=F.Espia([F.resp(401, {"errors": [{"message": "no autorizado"}]})]))
    comprobar_alerta(r, "401")


def test_alerta_con_403_de_buffer(r):
    cod, sal = correr(r, cands=[cand("c1")], buffer=F.Espia([F.resp(403, "{}")]))
    comprobar_alerta(r, "403")


class BufferCuentaSuspendida(BufferSimulado):
    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        if cuerpo and b"createPost" in cuerpo:
            return F.resp(200, {"data": {"createPost": {"__typename": "MutationError",
                                                        "message": "Account suspended"}}})
        return super().__call__(metodo, url, encabezados, cuerpo, tiempo)


def test_alerta_con_mutationerror_de_cuenta_suspendida(r):
    cod, sal = correr(r, cands=[cand("c1")], buffer=BufferCuentaSuspendida())
    comprobar_alerta(r, "cuenta")
    assert registro.contador_toma(r) == 1


def test_un_mutationerror_normal_no_es_alerta(r):
    cod, sal = correr(r, cands=[cand("c1")], buffer=BufferParcial())
    assert not (r / "PAUSA").exists()


def test_alerta_por_aviso_de_retiro_en_las_fuentes(r):
    ruta = r / "fuentes.yml"
    ruta.write_text(ruta.read_text(encoding="utf-8").replace(
        "  id: fuente-prueba", "  id: fuente-prueba") + "  aviso_retiro: \"el creador pidió quitar un clip\"\n",
        encoding="utf-8")
    cod, sal, e = correr_con_espias(r)
    comprobar_alerta(r, "retiro")
    assert sin_llamadas(e)


def test_alerta_si_una_clave_aparece_en_el_log(r):
    def hacer_que_filtra(raiz, c, ficha, ahora, salida=None, **kw):
        return Resultado(False, [f"error con {CLAVE} adentro"])
    cod, sal = correr(r, cands=[cand("c1")], hacer=hacer_que_filtra)
    comprobar_alerta(r, "BUFFER_API_KEY")
    assert CLAVE not in sal


def test_tras_una_alerta_el_siguiente_ciclo_no_hace_nada(r):
    correr(r, cands=[cand("c1")], buffer=BufferSimulado(canales="canales_ajeno.json"))
    cod, sal, e = correr_con_espias(r)
    assert "PAUSA:" in sal and sin_llamadas(e)


def test_las_lineas_impresas_no_llevan_claves():
    sal = []
    s = ciclo.Salida(sal.append, ENV)
    s(f"hola {CLAVE} y {SUPA_CLAVE}")
    assert sal == ["hola *** y ***"] and s.fuga(ENV) == "BUFFER_API_KEY"


# ---------------------------------------------------------------- 8. contrato del workflow

WORKFLOW = (RAIZ_REAL / ".github" / "workflows" / "fabrica.yml")


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_cron_y_disparo_manual(wf):
    on = wf[True]
    assert on["schedule"] == [{"cron": "7 */1 * * *"}]   # cada hora, minuto 7
    entrada = on["workflow_dispatch"]["inputs"]["ensayo_forzado"]
    assert entrada["type"] == "boolean" and entrada["default"] is False


def test_workflow_concurrencia_y_permisos(wf):
    assert wf["concurrency"] == {"group": "fabrica", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "write", "issues": "write"}


def test_workflow_tiempos_maximos_y_dependencia(wf):
    assert wf["jobs"]["decidir"]["timeout-minutes"] == 3
    p = wf["jobs"]["producir"]
    assert p["timeout-minutes"] == 20 and p["needs"] == "decidir"
    assert "needs.decidir.outputs.trabajo == 'si'" in p["if"] and "inputs.ensayo_forzado" in p["if"]
    assert wf["jobs"]["decidir"]["outputs"]["trabajo"]


def test_workflow_decidir_el_ultimo_paso_tras_checkout_y_setup_es_el_de_decidir(wf):
    pasos = wf["jobs"]["decidir"]["steps"]
    assert "checkout" in pasos[0]["uses"] and "setup-python" in pasos[1]["uses"]
    assert pasos[2]["run"].startswith("pip install")
    assert len(pasos) == 4 and pasos[3]["run"].strip() == "python -m fabrica.ciclo --decidir" and pasos[3]["id"] == "decidir"


def test_workflow_los_secretos_entran_por_env_nunca_dentro_de_run(wf):
    crudo = WORKFLOW.read_text(encoding="utf-8")
    for job in wf["jobs"].values():
        for paso in job["steps"]:
            assert "secrets." not in paso.get("run", "")
    env = wf["jobs"]["producir"]["env"]
    for k in ("BUFFER_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "TWITCH_CLIENT_ID",
              "TWITCH_CLIENT_SECRET", "GEMINI_API_KEY"):
        assert env[k] == "${{ secrets." + k + " }}"
    assert "echo ${{" not in crudo


def test_workflow_commit_skip_ci_y_reintentos(wf):
    cierre = next(p for p in wf["jobs"]["producir"]["steps"] if "Cerrar el ciclo" in p.get("name", ""))
    run = cierre["run"]
    assert "[skip ci] ciclo" in run and "git pull --rebase" in run and "for intento in 1 2 3" in run
    for ruta in ("marcador.csv", "registro.md", "estado", "config/canales.yml", "informativas.yml"):
        assert ruta in run
    assert "inputs.ensayo_forzado" in cierre["if"]


def cierre_run(wf):
    return next(p for p in wf["jobs"]["producir"]["steps"] if "Cerrar el ciclo" in p.get("name", ""))["run"]


def test_workflow_alerta_abre_issue_y_hace_commit_de_pausa(wf):
    run = cierre_run(wf)
    assert 'gh issue create --title "ALERTA ROJA: ' in run and "estado/alerta.json" in run and "PAUSA" in run


def test_workflow_el_issue_de_alerta_se_abre_despues_de_confirmar_el_push(wf):
    run = cierre_run(wf)
    assert run.index("git push") < run.index('guardado" != "si"') < run.index("gh issue create")
    assert run.count("gh issue create") == 1


def permitidos(wf):
    run = cierre_run(wf)
    return re.compile(re.search(r"PERMITIDOS='([^']+)'", run).group(1))


def test_workflow_el_commit_sólo_admite_los_archivos_permitidos(wf):
    run = cierre_run(wf)
    ok = permitidos(wf)
    for bueno in ("marcador.csv", "registro.md", "estado/buffer-envios.json", "estado/vistos.txt",
                  "config/canales.yml", "informativas.yml", "marca.yml", "PAUSA"):
        assert ok.match(bueno), bueno
    for malo in ("fabrica/ciclo.py", ".github/workflows/fabrica.yml", "fuentes.yml", "config/modo.yml",
                 "marcador.csv.bak", "estadoX", "temporada.yml", "config/horarios.yml"):
        assert not ok.match(malo), malo
    # la comprobación corre antes de añadir, de hacer commit y de empujar
    assert run.index("grep -Ev") < run.index("git add -A") < run.index("git commit") < run.index("git push")
    assert 'echo "Archivos fuera de la lista permitida' in run and run.index("grep -Ev") < run.index("exit 1")
    assert "--sumar-minutos" in run and "--abrir-avisos" in run


def test_workflow_ensayo_forzado_sube_artefacto_de_3_dias(wf):
    pasos = wf["jobs"]["producir"]["steps"]
    subir = next(p for p in pasos if p.get("uses", "").startswith("actions/upload-artifact"))
    assert subir["with"]["retention-days"] == 3 and "inputs.ensayo_forzado" in subir["if"]
    ciclo_paso = next(p for p in pasos if p.get("name") == "Ciclo")
    assert "--ensayo-forzado --salida" in ciclo_paso["run"]


def test_workflow_instala_ffmpeg_con_reintentos_y_tope(wf):
    paso = next(p for p in wf["jobs"]["producir"]["steps"] if "ffmpeg" in p.get("name", ""))
    assert paso["uses"] == "./.github/actions/ffmpeg" and paso["timeout-minutes"] == 12
    accion = (RAIZ_REAL / ".github/actions/ffmpeg/action.yml").read_text(encoding="utf-8")
    # apt con tope propio, segundo espejo firmado y verificación de los dos binarios
    assert "timeout --kill-after=10" in accion and "archive.ubuntu.com" in accion
    assert "ffprobe -hide_banner -version" in accion
    assert "/etc/apt/apt-mirrors.txt" in accion and "--error-on=any" in accion
    assert "pkill" not in accion
    assert "curl" not in accion and "tar " not in accion  # nada de binarios sin firma


FLUJOS_CON_FFMPEG = {"fabrica.yml", "muestras.yml", "ojos-prueba.yml", "publicar-ensayo.yml", "puerta.yml"}


def test_todos_los_flujos_usan_la_accion_de_ffmpeg():
    con_paso = set()
    for f in sorted((RAIZ_REAL / ".github/workflows").glob("*.yml")):
        texto = f.read_text(encoding="utf-8")
        assert "apt-get" not in texto, f.name
        for job in (yaml.safe_load(texto)["jobs"] or {}).values():
            for paso in job.get("steps", []):
                if "ffmpeg" in paso.get("name", "").lower():
                    assert paso.get("uses") == "./.github/actions/ffmpeg", f.name
                    assert paso.get("timeout-minutes") == 12, f.name
                    con_paso.add(f.name)
    assert FLUJOS_CON_FFMPEG <= con_paso


# ---------------------------------------------------------------- 9. nombres de canal

def test_los_nombres_de_canal_comparan_sin_espacios_ni_signos():
    assert publicar._norm("Guion Vivo") == publicar._norm("guionvivo") == publicar._norm("@guion_vivo") == "guionvivo"


def test_confirmar_canales_acepta_guion_vivo_con_espacio_pero_sigue_exigiendo_servicio_e_id():
    cfg = {"shorts": {"servicio": "youtube", "nombre_esperado": "Guion Vivo",
                      "buffer_channel_id": "canal-yt-001", "buffer_org_id": "org-ejemplo-001"}}
    api = [{"id": "canal-yt-001", "name": "Guion Vivo", "service": "youtube", "isQueuePaused": False,
            "_org": "org-ejemplo-001"}]
    assert publicar.confirmar_canales(api, cfg, None, plataformas=["shorts"], escribir=False,
                                      exigir_guardado=True) == {"shorts": "canal-yt-001"}
    for malo in ({**api[0], "service": "tiktok"}, {**api[0], "id": "otro"}, {**api[0], "name": "Otro Canal"}):
        with pytest.raises(publicar.AlertaRoja):
            publicar.confirmar_canales([malo], cfg, None, plataformas=["shorts"], escribir=False,
                                       exigir_guardado=True)


# ---------------------------------------------------------------- 10. ronda 2

def test_la_alerta_en_la_segunda_plataforma_conserva_lo_de_la_primera(r):
    """Shorts sale y tiktok dispara alerta roja: la toma avanza, el candidato queda visto y la informativa usada."""
    cambiar_modo(r, "vivo", ["shorts", "tiktok"])

    class BufferAlertaEnLaSegunda(BufferSimulado):
        creados = 0

        def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
            if cuerpo and b"createPost" in cuerpo:
                type(self).creados += 1
                if type(self).creados > 1:
                    return F.resp(200, {"data": {"createPost": {"__typename": "MutationError",
                                                                "message": "Account restricted"}}})
            return super().__call__(metodo, url, encabezados, cuerpo, tiempo)

    antes = registro.contador_toma(r)
    cod, sal = correr(r, cands=[cand("c1")], buffer=BufferAlertaEnLaSegunda())
    comprobar_alerta(r, "cuenta")
    assert [f["plataforma"] for f in filas(r)] == ["shorts"]
    assert registro.contador_toma(r) == antes + 1 and "c1" in vistos(r)
    cerrados = json.loads((r / "estado" / "huecos-cerrados.json").read_text())
    assert {"fecha": "2026-11-18", "hora": "11:00", "plataforma": "shorts"} in cerrados


def test_la_alerta_en_la_segunda_conserva_la_informativa_usada(r, monkeypatch):
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    cambiar_modo(r, "vivo", ["shorts", "tiktok"])

    class B(BufferSimulado):
        n = 0

        def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
            if cuerpo and b"createPost" in cuerpo:
                B.n += 1
                if B.n > 1:
                    return F.resp(200, {"data": {"createPost": {"__typename": "MutationError",
                                                                "message": "Account suspended"}}})
            return super().__call__(metodo, url, encabezados, cuerpo, tiempo)

    correr(r, buffer=B())
    assert yaml.safe_load((r / "informativas.yml").read_text(encoding="utf-8"))["informativas"][0]["usada"] is True
    assert (r / "PAUSA").exists()


def test_una_clave_en_el_error_de_elegir_candidatos_corta_antes_del_respaldo_y_de_publicar(r, monkeypatch):
    monkeypatch.setattr(ciclo.informativa, "armar", lambda *a, **k: pytest.fail("no debe armar la informativa"))
    sim, supa = BufferSimulado(), SupabaseSimulado()

    def detectar_roto(raiz, ahora, cliente):
        raise RuntimeError(f"fallo con {CLAVE} en la URL")

    sal = []
    cod = ciclo.ejecutar(r, ahora=AHORA, env=ENV, imprimir=sal.append, validar_fn=VERDE, detectar_fn=detectar_roto,
                         hacer_toma_fn=Hacer(r), transporte_buffer=sim, transporte_almacen=supa)
    assert cod == 0 and CLAVE not in "\n".join(sal)
    comprobar_alerta(r, "BUFFER_API_KEY")
    assert sim.consultas == [] and supa.llamadas == [] and filas(r) == []


def test_un_hueco_cerrado_por_plataforma_no_se_reabre_aunque_fallara(r):
    cambiar_modo(r, "vivo", ["shorts", "tiktok"])
    assert ciclo.buscar_hueco(r, AHORA, ["shorts", "tiktok"]).plataformas == ["shorts", "tiktok"]
    ciclo.cerrar_hueco(r, ciclo.buscar_hueco(r, AHORA, ["shorts"]), "tiktok")
    assert ciclo.buscar_hueco(r, AHORA, ["shorts", "tiktok"]).plataformas == ["shorts"]
    ciclo.cerrar_hueco(r, ciclo.buscar_hueco(r, AHORA, ["shorts"]), "shorts")
    assert ciclo.buscar_hueco(r, AHORA, ["shorts", "tiktok"]) is None
    assert ciclo.buscar_hueco(r, datetime(2026, 11, 18, 12, 7, tzinfo=ZONA), ["shorts"]).hora == "13:00"


def test_una_plataforma_que_fallo_no_vuelve_a_intentar_ese_hueco_en_la_siguiente_corrida(r):
    cod, sal = correr(r, cands=[cand("c1")], buffer=BufferCuentaRechaza())
    assert "shorts: descartada" in sal and filas(r) == []
    assert ciclo.buscar_hueco(r, AHORA, ["shorts"]) is None        # cerrado aunque no haya fila en el marcador


def test_con_modo_ensayo_forzado_por_la_rama_nunca_hay_createpost(r):
    sim = BufferSimulado()
    cod, sal = correr(r, cands=[cand("c1")], buffer=sim, solo_ensayo=True)
    assert cod == 0 and not sim.hubo("createPost") and filas(r) == []


def test_ensayo_forzado_falla_si_no_entrega_mp4_plan_e_informe(r, tmp_path, monkeypatch):
    cambiar_modo(r, "ensayo", [])

    def armar_sin_plan(raiz, entrada, ahora, salida):
        res = armar_falso(raiz, entrada, ahora, salida)
        res.mp4.with_suffix(".plan.json").unlink()
        return res

    monkeypatch.setattr(ciclo.informativa, "armar", armar_sin_plan)
    cod, sal = correr(r, ensayo_forzado=True, salida_dir=tmp_path / "s", env={})
    assert cod == 1 and "no entregó" in sal and "plan" in sal


def test_ensayo_forzado_sin_toma_falla(r, tmp_path):
    cambiar_modo(r, "ensayo", [])
    ruta = r / "informativas.yml"
    d = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    d["informativas"][0]["usada"] = True
    ruta.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    cod, sal = correr(r, ensayo_forzado=True, salida_dir=tmp_path / "s", env={})
    assert cod == 1 and "sin toma" in sal


def test_workflow_cada_corrida_parte_de_main_al_dia_y_fuera_de_main_solo_ensayo(wf):
    pasos = wf["jobs"]["producir"]["steps"]
    nombres = [p.get("name", p.get("uses", "")) for p in pasos]
    reset = next(p for p in pasos if p.get("name") == "Partir de main al día")
    assert reset["run"] == "git fetch origin main && git reset --hard origin/main"
    assert reset["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert nombres.index("Partir de main al día") == 1                       # justo después del checkout
    ciclo_paso = next(p for p in pasos if p.get("name") == "Ciclo")
    assert pasos.index(reset) < pasos.index(ciclo_paso)
    assert 'extra="--modo-ensayo"' in ciclo_paso["run"] and 'refs/heads/main' in ciclo_paso["run"]
    cierre = next(p for p in pasos if "Cerrar el ciclo" in p.get("name", ""))
    assert "github.ref == 'refs/heads/main'" in cierre["if"]


def test_workflow_la_marca_de_inicio_se_escribe_al_empezar_y_el_cierre_la_cierra(wf):
    pasos = wf["jobs"]["producir"]["steps"]
    marca = next(p for p in pasos if "por si la corrida muere" in p.get("name", ""))
    assert "--marcar-inicio" in marca["run"] and "git push" in marca["run"] and "refs/heads/main" in marca["run"]
    assert pasos.index(marca) < pasos.index(next(p for p in pasos if p.get("name") == "Ciclo"))
    assert '--run-id "$GITHUB_RUN_ID"' in cierre_run(wf)


def test_workflow_el_artefacto_falla_si_no_hay_archivos(wf):
    subir = next(p for p in wf["jobs"]["producir"]["steps"] if p.get("uses", "").startswith("actions/upload-artifact"))
    assert subir["with"]["if-no-files-found"] == "error"

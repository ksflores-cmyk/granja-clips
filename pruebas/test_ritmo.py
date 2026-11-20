"""Tarea #9 `ritmo`: 6 clips al día por red sin amontonar, sin pasar la cola de Buffer gratis y sin repetir clips.

Pruebas de calendario: sin red ni claves reales; usan las ayudas de test_n6_ciclo (repo temporal y Buffer simulado)."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

import falsos_n5 as F  # noqa: F401  (el módulo de transportes falsos es el mismo de N5/N6)
from fabrica import ciclo, puertas, validar
from fabrica.pausa import ZONA
from fabrica.simulacion import BufferSimulado
from test_n6_ciclo import (ENV, Hacer, armar_falso, cambiar_modo, cand, correr, filas, minutos, poner_tope,  # noqa: F401
                           r)

HUECOS = ["09:00", "11:00", "13:00", "16:00", "19:00", "22:00"]
REDES = ["shorts", "tiktok", "reels"]
WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "fabrica.yml"


def gt(dia, hh, mm=23, mes=11):
    return datetime(2026, mes, dia, hh, mm, tzinfo=ZONA)


def libro(r, entradas):
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(json.dumps(entradas), encoding="utf-8")


def envio(plat, due: datetime, estado="creado", toma=None, claves=None, n=1):
    d = due.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    e = {"id": f"{plat}|{n}|{d}", "plataforma": plat, "toma": 900 + n, "dueAt": d, "estado": estado,
         "post_id": None, "registrado": True}
    if claves is not None:
        e["datos"] = {"toma": {"numero": 900 + n, "claves": claves, "id_origen": claves[0] if claves else None}}
    return e


def correr_dia(r, dia, minuto=23, redes=REDES, horas=range(24)):
    """Una corrida por hora; devuelve {hora: salida}. Cada una trae un candidato nuevo."""
    cambiar_modo(r, "vivo", redes)
    salidas = {}
    for hh in horas:
        ahora = gt(dia, hh, minuto)
        cod, sal = correr(r, ahora=ahora, hacer=Hacer(r), cands=[cand(f"clip-{dia}-{hh:02d}")])
        assert cod == 0, sal
        if "programada" in sal:
            ciclo.sumar_minutos(r, ahora, 8)
        salidas[hh] = sal
    return salidas


# ---------------------------------------------------------------- 1. un día entero

@pytest.mark.parametrize("dia", [20, 25])        # 20 nov: estreno; 25 nov: día normal
def test_un_dia_entero_cada_hueco_lo_toma_una_sola_corrida_la_de_la_hora_anterior(r, dia):
    salidas = correr_dia(r, dia)
    quien = {h: [hh for hh, sal in salidas.items() if f"hueco 2026-11-{dia} {h}" in sal] for h in HUECOS}
    assert quien == {h: [int(h[:2]) - 1] for h in HUECOS}
    for plat in REDES:
        horas = sorted(f["hora"] for f in filas(r) if f["plataforma"] == plat and f["fecha"] == f"2026-11-{dia}")
        assert horas == HUECOS
    assert not any("(relleno" in sal for sal in salidas.values())
    assert not any("tope diario" in salidas[h] for h in (8, 10, 12, 15, 18, 21))      # cada hueco cabe en el tope


def test_un_dia_entero_con_el_disparador_a_otro_minuto_tambien_cubre_los_seis(r):
    salidas = correr_dia(r, 25, minuto=5)
    for plat in REDES:
        horas = sorted(f["hora"] for f in filas(r) if f["plataforma"] == plat and f["fecha"] == "2026-11-25")
        assert horas == HUECOS
    assert not any("(relleno" in sal for sal in salidas.values())


def test_dos_corridas_en_la_misma_hora_la_segunda_no_repite(r):
    cambiar_modo(r, "vivo", ["shorts"])
    cod, sal = correr(r, ahora=gt(25, 10, 23), hacer=Hacer(r), cands=[cand("a")])
    assert "shorts: programada" in sal
    h2 = Hacer(r)
    cod, sal = correr(r, ahora=gt(25, 10, 31), hacer=h2, cands=[cand("b")])
    assert h2.llamadas == [] and len(filas(r)) == 1 and "programada" not in sal


# ---------------------------------------------------------------- 2. relleno

def test_corrida_perdida_se_rellena_a_ahora_mas_28_redondeado_y_el_normal_gana(r):
    h = ciclo.buscar_hueco(r, gt(25, 9, 23), ["shorts"])          # la de las 8:23 se perdió
    assert (h.hora, h.relleno, h.publicar_en) == ("09:00", True, gt(25, 9, 55))
    assert h.programar_en == gt(25, 9, 55) and h.fecha == "2026-11-25"
    h = ciclo.buscar_hueco(r, gt(25, 10, 23), ["shorts"])         # el normal de las 11:00 gana al relleno
    assert (h.hora, h.relleno) == ("11:00", False)


def test_el_relleno_siempre_sobrevive_a_validar_futuro_al_publicar(r):
    """Contrato: aunque el ciclo dure LIMITE_CICLO_S, el relleno sigue cayendo a >= 10 min cuando se publica."""
    from fabrica import plazo, publicar
    vistos = 0
    for minutos_dia in range(8 * 60, 23 * 60, 7):
        ahora = gt(25, minutos_dia // 60, minutos_dia % 60)
        h = ciclo.buscar_hueco(r, ahora, ["shorts"])
        if h is not None and h.relleno:
            vistos += 1
            publicar.validar_futuro(h.publicar_en, ahora + timedelta(seconds=plazo.LIMITE_CICLO_S))
    assert vistos > 0


def test_el_relleno_no_se_amontona_con_los_huecos_del_dia(r):
    ciclo.cerrar_hueco(r, ciclo.Hueco(gt(25, 11, 0), ["shorts"]), "shorts")   # el 11:00 ya salió
    assert ciclo.buscar_hueco(r, gt(25, 11, 23), ["shorts"]) is None          # 11:55 queda a 55 min del 11:00
    h = ciclo.buscar_hueco(r, gt(25, 12, 23), ["shorts"])                    # gana el normal de las 13:00
    assert (h.hora, h.relleno) == ("13:00", False)
    ciclo.cerrar_hueco(r, ciclo.Hueco(gt(25, 13, 0), ["shorts"]), "shorts")   # el 13:00 ya salió
    assert ciclo.buscar_hueco(r, gt(25, 13, 23), ["shorts"]) is None          # 13:55 queda a 55 min del 13:00
    h = ciclo.buscar_hueco(r, gt(25, 14, 23), ["shorts"])
    assert (h.hora, h.relleno, h.publicar_en) == ("09:00", True, gt(25, 14, 55))


def test_el_relleno_tampoco_se_amontona_con_lo_ya_programado_de_esa_red(r):
    ciclo.cerrar_hueco(r, ciclo.Hueco(gt(25, 11, 0), ["shorts"]), "shorts")
    libro(r, [envio("shorts", gt(25, 14, 0))])                    # algo ya programado a las 14:00
    assert ciclo.buscar_hueco(r, gt(25, 14, 23), ["shorts"]) is None
    h = ciclo.buscar_hueco(r, gt(25, 14, 23), ["shorts", "tiktok"])          # tiktok no tiene nada: sí se rellena
    assert h.plataformas == ["tiktok"] and h.relleno


def test_el_relleno_nunca_pasa_de_las_23_ni_cae_al_dia_siguiente(r):
    h = ciclo.buscar_hueco(r, gt(25, 22, 23), ["shorts"])
    assert h.hora == "22:00" and h.publicar_en == gt(25, 22, 55)               # 22:55 < 23:00
    assert ciclo.buscar_hueco(r, gt(25, 22, 50), ["shorts"]) is None           # 23:20 pasa de las 23:00
    assert ciclo.buscar_hueco(r, gt(25, 23, 50), ["shorts"]) is None           # 00:20 ya es otro día
    assert ciclo.buscar_hueco(r, gt(25, 23, 23), ["shorts"]) is None


def test_el_relleno_respeta_el_maximo_de_intentos_por_hueco(r):
    hueco = ciclo.Hueco(gt(25, 9, 0), ["shorts"])
    ciclo.sumar_intento(r, hueco)
    assert ciclo.buscar_hueco(r, gt(25, 9, 23), ["shorts"]).relleno            # 1 de 2: aún
    assert ciclo.sumar_intento(r, hueco) == 2
    assert ciclo.buscar_hueco(r, gt(25, 9, 23), ["shorts"]) is None            # 2 de 2: se acabó


def test_el_hueco_normal_tambien_cuenta_su_intento_y_los_intentos_se_podan(r):
    cambiar_modo(r, "vivo", ["shorts"])
    correr(r, ahora=gt(25, 10, 23), hacer=Hacer(r, oks=(False,)), cands=[cand("a")])
    datos = json.loads((r / "estado" / "huecos-intentos.json").read_text())
    assert datos == [{"fecha": "2026-11-25", "hora": "11:00", "intentos": 1}]
    for i in range(120):
        ciclo.sumar_intento(r, ciclo.Hueco(datetime(2027, 1, 1, 9, 0, tzinfo=ZONA) + timedelta(days=i), ["shorts"]))
    assert len(json.loads((r / "estado" / "huecos-intentos.json").read_text())) == 100


def test_un_relleno_cerrado_no_vuelve(r):
    h = ciclo.buscar_hueco(r, gt(25, 9, 23), ["shorts"])
    ciclo.cerrar_hueco(r, h, "shorts")                             # se anota el hueco ORIGINAL (09:00)
    assert json.loads((r / "estado" / "huecos-cerrados.json").read_text()) == [
        {"fecha": "2026-11-25", "hora": "09:00", "plataforma": "shorts"}]
    assert ciclo.buscar_hueco(r, gt(25, 9, 23), ["shorts"]) is None
    assert ciclo.buscar_hueco(r, gt(25, 15, 23), ["shorts"]).hora == "16:00"


def test_un_relleno_en_vivo_programa_a_la_hora_del_relleno_y_cierra_el_hueco_original(r):
    cambiar_modo(r, "vivo", ["shorts"])
    cod, sal = correr(r, ahora=gt(25, 9, 23), hacer=Hacer(r), cands=[cand("a")])
    assert "hueco 2026-11-25 09:00 (relleno a las 09:55)" in sal and "shorts: programada" in sal
    assert [(f["fecha"], f["hora"]) for f in filas(r)] == [("2026-11-25", "09:55")]
    assert ciclo.buscar_hueco(r, gt(25, 9, 23), ["shorts"]) is None


# ---------------------------------------------------------------- 3. cola de Buffer

class BufferConCola(BufferSimulado):
    """Responde a la consulta de posts `scheduled` con n posts (o con un error de GraphQL si n es None)."""

    def __init__(self, n):
        super().__init__()
        self.n = n

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode("utf-8"))["query"]
        if "posts(" in q and "scheduled" in q:
            self.consultas.append(q)
            if self.n is None:
                return F.resp(200, {"errors": [{"message": "no disponible"}]})
            nodos = [{"node": {"id": f"p{i}"}} for i in range(self.n)]
            return F.resp(200, {"data": {"posts": {"edges": nodos}}})
        return super().__call__(metodo, url, encabezados, cuerpo, tiempo)


def test_la_cola_llena_en_el_libro_salta_la_red(r):
    cambiar_modo(r, "vivo", ["shorts"])
    libro(r, [envio("shorts", gt(25, 12, 0) + timedelta(hours=i), n=i) for i in range(10)])
    buf = BufferConCola(0)
    cod, sal = correr(r, ahora=gt(25, 10, 23), cands=[cand("a")], buffer=buf)
    assert "shorts: cola de Buffer llena (10/10): se salta" in sal
    assert not buf.hubo("createPost") and filas(r) == []


def test_la_cola_llena_en_buffer_salta_la_red(r):
    cambiar_modo(r, "vivo", ["shorts"])
    buf = BufferConCola(10)
    cod, sal = correr(r, ahora=gt(25, 10, 23), cands=[cand("a")], buffer=buf)
    assert "cola de Buffer llena (10/10)" in sal and not buf.hubo("createPost")


def test_con_la_cola_a_medias_se_programa(r):
    cambiar_modo(r, "vivo", ["shorts"])
    libro(r, [envio("shorts", gt(25, 12, 0) + timedelta(hours=i), n=i) for i in range(9)])
    buf = BufferConCola(9)
    cod, sal = correr(r, ahora=gt(25, 10, 23), cands=[cand("a")], buffer=buf)
    assert "cola de Buffer llena" not in sal and "shorts: programada" in sal


def test_si_la_consulta_a_buffer_falla_vale_el_libro(r):
    cambiar_modo(r, "vivo", ["shorts"])
    libro(r, [envio("shorts", gt(25, 12, 0) + timedelta(hours=i), n=i) for i in range(10)])
    cod, sal = correr(r, ahora=gt(25, 10, 23), cands=[cand("a")], buffer=BufferConCola(None))
    assert "cola de Buffer llena (10/10)" in sal and "la consulta a Buffer falló, vale el libro" in sal


def test_los_envios_pasados_o_rechazados_o_de_otra_red_no_cuentan_en_la_cola(r):
    cambiar_modo(r, "vivo", ["shorts"])
    ya = [envio("shorts", gt(25, 1, 0) + timedelta(minutes=i), n=i) for i in range(5)]               # ya salieron
    rech = [envio("shorts", gt(25, 14, 0) + timedelta(hours=i), "rechazado", n=20 + i) for i in range(3)]
    otra = [envio("tiktok", gt(25, 14, 0) + timedelta(hours=i), n=40 + i) for i in range(5)]
    libro(r, ya + rech + otra)
    cod, sal = correr(r, ahora=gt(25, 10, 23), cands=[cand("a")], buffer=BufferConCola(0))
    assert "cola de Buffer llena" not in sal and "shorts: programada" in sal


# ---------------------------------------------------------------- 4. no repetir en 24 h

def test_una_clave_repetida_en_24_h_descarta_el_candidato_antes_de_editar(r):
    cambiar_modo(r, "vivo", ["shorts"])
    libro(r, [envio("shorts", gt(25, 11, 0) - timedelta(hours=20), claves=["clip-viejo"])])
    hacer = Hacer(r)
    cod, sal = correr(r, ahora=gt(25, 10, 23), hacer=hacer, cands=[cand("clip-viejo"), cand("clip-nuevo")])
    assert hacer.llamadas == ["clip-nuevo"] and "clip-viejo: repetido en 24 h" in sal
    assert "shorts: programada" in sal


def test_a_las_25_h_la_clave_ya_no_choca(r):
    cambiar_modo(r, "vivo", ["shorts"])
    libro(r, [envio("shorts", gt(25, 11, 0) - timedelta(hours=25), claves=["clip-viejo"])])
    hacer = Hacer(r)
    cod, sal = correr(r, ahora=gt(25, 10, 23), hacer=hacer, cands=[cand("clip-viejo")])
    assert hacer.llamadas == ["clip-viejo"] and "repetido" not in sal


def test_una_clave_de_otra_red_choca_si_esa_red_esta_en_el_hueco(r):
    cambiar_modo(r, "vivo", ["shorts", "tiktok"])
    libro(r, [envio("tiktok", gt(25, 11, 0) - timedelta(hours=3), claves=["clip-viejo"])])
    assert ciclo.claves_recientes(r, "shorts", gt(25, 11, 0), 24) == set()
    assert ciclo.claves_recientes(r, "tiktok", gt(25, 11, 0), 24) == {"clip-viejo"}
    hacer = Hacer(r)
    correr(r, ahora=gt(25, 10, 23), hacer=hacer, cands=[cand("clip-viejo")])
    assert hacer.llamadas == []


def test_los_rechazados_no_cuentan_para_no_repetir(r):
    libro(r, [envio("shorts", gt(25, 11, 0), "rechazado", claves=["x"])])
    assert ciclo.claves_recientes(r, "shorts", gt(25, 11, 0), 24) == set()


def test_la_toma_lleva_id_origen_y_claves_ordenadas_y_se_guardan_en_el_libro(r):
    cambiar_modo(r, "vivo", ["shorts"])
    c = cand("clip-b")
    c.ids_origen = ["clip-a", "clip-b"]
    correr(r, ahora=gt(25, 10, 23), cands=[c])
    toma = json.loads((r / "estado" / "buffer-envios.json").read_text())[0]["datos"]["toma"]
    assert toma["id_origen"] == "clip-b" and toma["claves"] == ["clip-a", "clip-b"]


def test_la_comprobacion_final_por_red_descarta_la_red_con_clave_repetida(r, monkeypatch):
    """El respaldo informativo no pasa por el filtro de candidatos: lo para la comprobación previa a subir."""
    cambiar_modo(r, "vivo", ["shorts", "tiktok"])
    entrada = {"id": "inf-x", "gancho": "Un dato", "titular": "Un dato", "datos": ["uno", "dos"]}
    monkeypatch.setattr(ciclo.informativa, "elegir", lambda raiz, ahora: entrada)
    monkeypatch.setattr(ciclo.informativa, "cabe_en_el_tope", lambda raiz, ahora: (True, ""))
    monkeypatch.setattr(ciclo.informativa, "armar", armar_falso)
    libro(r, [envio("tiktok", gt(25, 11, 0) - timedelta(hours=2), claves=["informativa:inf-x"])])
    cod, sal = correr(r, ahora=gt(25, 10, 23), cands=[])
    assert "tiktok: descartada, repetido en 24 h" in sal and "shorts: programada" in sal
    assert [f["plataforma"] for f in filas(r)] == ["shorts"]


# ---------------------------------------------------------------- 5. cupo

def test_los_cupos_son_6_en_estreno_y_fuera(r):
    assert puertas.cupo_del_dia(r, gt(20, 10), "shorts") == 6
    assert puertas.cupo_del_dia(r, gt(25, 10), "shorts") == 6


def test_el_cupo_con_suplentes(r):
    from test_n6_ciclo import anotar
    for i in range(5):
        anotar(r, fecha="2026-11-25", hora=f"{9 + i}:00", plataforma="shorts", id_clip=f"x{i}")
    ok, no = puertas.filtrar(r, [cand("a"), cand("b")], gt(25, 10), "shorts")
    assert [c.id_origen for c in ok] == ["a", "b"]                  # el segundo es suplente, no otra publicación
    anotar(r, fecha="2026-11-25", hora="16:00", plataforma="shorts", id_clip="x5")
    ok, no = puertas.filtrar(r, [cand("a"), cand("b")], gt(25, 10), "shorts")
    assert ok == [] and all("cupo del día lleno en shorts (6/6)" in m for _, m in no)


# ---------------------------------------------------------------- 6. tope de Actions

def test_el_tope_de_90_deja_pasar_un_dia_de_6_producir_de_8_minutos(r):
    assert ciclo.tope_minutos(r) == 90
    # A las 21:23 ya hubo 5 producir (40 min) y 22 decidir: 62 usados, y 62 + 20 <= 90 admite el sexto.
    minutos(r, **{"2026-11-25": 40})
    assert ciclo.admite_producir(r, gt(25, 21, 23)) is True
    poner_tope(r, 60)
    assert ciclo.admite_producir(r, gt(25, 21, 23)) is False


# ---------------------------------------------------------------- 7. workflow

def test_el_cron_del_workflow_es_el_minuto_23_de_cada_hora():
    texto = WORKFLOW.read_text(encoding="utf-8")
    assert re.findall(r'^\s*- cron: "([^"]+)"', texto, re.M) == ["23 * * * *"]


# ---------------------------------------------------------------- 8. validar

def errores(repo):
    return validar.validar(repo).errores


def hay(repo, fragmento):
    return any(fragmento in e for e in errores(repo))


def test_el_repo_real_pasa_la_validacion():
    assert validar.validar().errores == []


def editar(repo, editar_yaml, **cambios):
    editar_yaml(repo, "config/horarios.yml", lambda d: d.update(cambios))


@pytest.mark.parametrize("cambios,fragmento", [
    ({"cola_buffer_max": 11}, "cola_buffer_max"),
    ({"cola_buffer_max": 0}, "cola_buffer_max"),
    ({"cola_buffer_max": "10"}, "cola_buffer_max"),
    ({"huecos": ["11:00", "09:00"]}, "ordenada"),
    ({"huecos": ["09:00", "09:00"]}, "sin repetidos"),
    ({"huecos": ["9am"]}, "hora inválida"),
    ({"huecos": ["25:00"]}, "hora inválida"),
    ({"huecos": []}, "lista no vacía"),
    ({"huecos": "09:00"}, "lista no vacía"),
    ({"huecos": [f"{h:02d}:00" for h in range(9)]}, "8 como máximo"),
    ({"no_repetir_horas": 23}, "no_repetir_horas"),
    ({"margen_min_minutos": 9}, "margen_min_minutos no puede ser menor"),
    ({"relleno": {"separacion_min_minutos": "x"}}, "separacion_min_minutos"),
    ({"relleno": {"max_intentos_por_hueco": 0}}, "max_intentos_por_hueco"),
    ({"relleno": {"ultima_hora": "25:99"}}, "ultima_hora"),
    ({"actions": {"tope_minutos_dia": 29}}, "tope_minutos_dia"),
    ({"actions": {"tope_minutos_dia": 101}}, "tope_minutos_dia"),
    ({"actions": {"tope_minutos_dia": "90"}}, "tope_minutos_dia"),
])
def test_validar_rechaza_horarios_malos(repo, editar_yaml, cambios, fragmento):
    editar(repo, editar_yaml, **cambios)
    assert hay(repo, fragmento)


def test_validar_rechaza_horarios_sin_huecos(repo, editar_yaml):
    editar_yaml(repo, "config/horarios.yml", lambda d: {k: v for k, v in d.items() if k != "huecos"})
    assert hay(repo, "huecos")


@pytest.mark.parametrize("clave", ["por_dia_por_plataforma", "estreno_max_por_dia_por_plataforma"])
def test_validar_rechaza_un_cupo_mayor_que_los_huecos(repo, editar_yaml, clave):
    editar_yaml(repo, "temporada.yml", lambda d: d["cupos"].update({clave: 7}))
    assert hay(repo, "pasa del número de huecos")
    editar_yaml(repo, "temporada.yml", lambda d: d["cupos"].update({clave: 6}))
    assert errores(repo) == []


def test_los_valores_opcionales_de_horarios_tienen_defecto(r):
    editar = yaml.safe_load((r / "config" / "horarios.yml").read_text(encoding="utf-8"))
    (r / "config" / "horarios.yml").write_text(
        yaml.safe_dump({"zona": editar["zona"], "huecos": editar["huecos"]}), encoding="utf-8")
    hz = ciclo._cfg_horarios(r)
    assert (hz["margen_min_s"], hz["margen_max_s"], hz["separacion_s"], hz["ultima_hora"], hz["max_intentos"],
            hz["cola_max"], hz["no_repetir_horas"], hz["tope_minutos_dia"]) == (900, 4500, 3600, "23:00", 2, 10, 24, 60)


def test_no_quedan_las_listas_viejas_de_huecos_en_el_codigo():
    raiz = Path(__file__).resolve().parent.parent
    for rel in ("fabrica/ciclo.py", "fabrica/validar.py", "config/horarios.yml"):
        texto = (raiz / rel).read_text(encoding="utf-8")
        for viejo in ("huecos_" + "normales", "huecos_" + "estreno"):
            assert viejo not in texto, rel

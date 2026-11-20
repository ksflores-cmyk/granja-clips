"""Tarea #10: reclamos y retiros vetan solos. Transporte falso de Buffer con la forma real de la API; sin red."""
import json
import re
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import falsos_n5 as F  # noqa: E402
import test_buscador_t as bt  # noqa: E402
import test_n6_ciclo as n6  # noqa: E402
from test_n6_ciclo import r  # noqa: E402,F401 (la copia del repo en vivo, sin PAUSA)
from fabrica import ciclo, detectar, fuentes, publicar, retiros, validar, vetados  # noqa: E402

AHORA = bt.AHORA                      # 20 nov 2026, 10:00 en Guatemala
CLAVE = n6.CLAVE
ORG = "org-ejemplo-001"
CANALES = {"shorts": "canal-yt-001", "tiktok": "canal-tt-001", "reels": "canal-ig-001"}


def campo(nombre, tipo="String", kind="SCALAR"):
    return {"name": nombre, "type": {"name": tipo, "kind": kind, "ofType": None}}


CAMPOS_ESCALAR = [campo(n) for n in ("id", "status", "dueAt", "text", "error")]


def error_post(pid, mensaje, texto="Clip con crédito", canal="canal-yt-001"):
    return {"id": pid, "status": "error", "dueAt": "2026-11-19T17:00:00.000Z", "text": texto,
            "channelId": canal, "error": mensaje}


class BufferFalso:
    """Transporte GraphQL: introspección de `Post`, subcampos de `error` y `posts` paginado (por cursor c1, c2...)."""

    def __init__(self, paginas=(), campos=None, subcampos=(), estado=200):
        self.paginas = list(paginas)
        self.campos = CAMPOS_ESCALAR if campos is None else campos
        self.subcampos = list(subcampos)
        self.estado = estado
        self.consultas = []

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode("utf-8"))["query"]
        self.consultas.append(q)
        if self.estado != 200:
            return F.resp(self.estado, {"errors": [{"message": "fallo"}]}, retry_after="0")
        if '__type(name: "Post")' in q:
            return F.resp(200, {"data": {"__type": {"fields": self.campos}}})
        if "__type(name:" in q:
            return F.resp(200, {"data": {"__type": {"fields": [{"name": n} for n in self.subcampos]}}})
        if "posts(" in q:
            m = re.search(r'after: "c(\d+)"', q)
            i = int(m.group(1)) if m else 0
            pagina = self.paginas[i] if i < len(self.paginas) else []
            hay = i + 1 < len(self.paginas)
            return F.resp(200, {"data": {"posts": {
                "edges": [{"node": p} for p in pagina],
                "pageInfo": {"hasNextPage": hay, "endCursor": f"c{i + 1}" if hay else None}}}})
        return F.resp(200, {"errors": [{"message": "consulta no simulada"}]})

    def posts_pedidos(self):
        return [q for q in self.consultas if "posts(" in q]


def cliente_de(tmp_path, transporte):
    return publicar.ClienteBuffer(CLAVE, transporte, tmp_path / "cuota", dormir=lambda s: None)


def revisar(r, tmp_path, posts, ahora=AHORA, **kw):
    """Una revisión con el transporte dado (lista de posts = una sola página). Devuelve (informe, líneas, buffer)."""
    buf = posts if isinstance(posts, BufferFalso) else BufferFalso([posts])
    sal = []
    inf = retiros.revisar(r, cliente_de(tmp_path, buf), CANALES, ORG, ahora, sal.append, **kw)
    return inf, sal, buf


def libro(r, *entradas):
    """Libro de envíos con (post_id, ficha_id)."""
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "buffer-envios.json").write_text(json.dumps([
        {"id": f"shorts|{i}", "plataforma": "shorts", "toma": i, "estado": "creado", "post_id": pid,
         "datos": {"ficha_id": ficha, "toma": {"fuente_id": ficha}}} for i, (pid, ficha) in enumerate(entradas, 1)
    ]), encoding="utf-8")


def guardar_fichas_t(r):
    _, _, fichas = bt.correr_t(r, bt.mundo())
    detectar.guardar_fichas_t(r, fichas)


def filas_retiros(r):
    return vetados.filas_retiros(r)


def alertas(r):
    ruta = r / "estado" / "alertas-retiro.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else []


def vistos(r):
    ruta = r / "estado" / "retiros-vistos.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else []


def fila_a_mano(r, dias_atras, fuente="t-otro"):
    fecha = (AHORA.astimezone(detectar.ZONA).date() - timedelta(days=dias_atras)).isoformat()
    with open(r / "retiros.md", "a", encoding="utf-8") as f:
        f.write(f"| {fecha} | {fuente} | a mano | correo de YouTube | reclamo | vetado |\n")


def arbol(raiz):
    return {str(p.relative_to(raiz)): p.read_bytes() for p in raiz.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- 1 a 3: un reclamo

def test_reclamo_de_una_ficha_t_del_libro_veta_al_streamer(r, tmp_path):
    guardar_fichas_t(r)
    libro(r, ("p1", "t-ana"))
    antes = (r / "config" / "vetados.yml").read_text(encoding="utf-8")
    inf, sal, _ = revisar(r, tmp_path, [error_post("p1", "Copyright claim | por Sony\nContent ID")])
    assert inf.reclamos == 1 and inf.posts[0]["login"] == "ana" and inf.posts[0]["clase"] == "reclamo"
    fila = [f for f in filas_retiros(r)]
    assert len(fila) == 1
    fecha, fuente, publicacion, quien, motivo, accion = fila[0]
    assert fecha == "2026-11-20" and fuente == "t-ana" and publicacion == "shorts p1"
    assert quien == "shorts (Buffer)" and "|" not in motivo and accion == "vetado twitch.tv/ana; ficha T pausada"
    nuevo = (r / "config" / "vetados.yml").read_text(encoding="utf-8")
    assert nuevo.splitlines()[0] == antes.splitlines()[0] and "equipos_twitch: [youplanet]" in nuevo
    assert "juanguarnizo, ana]" in nuevo and nuevo.count("#") == antes.count("#")
    assert "ana" in vetados.logins_vetados(r)
    ficha = next(f for f in fuentes.cargar_fuentes(r) if f["id"] == "t-ana")
    assert ficha["estado"] == "vetada" and ficha["publicable"] is False
    en_disco = next(f for f in yaml.safe_load((r / "estado" / "fichas-t.yml").read_text(encoding="utf-8"))
                    if f["id"] == "t-ana")
    assert en_disco["estado"] == "vetada"
    assert len(alertas(r)) == 1 and "ALERTA ROJA" in alertas(r)[0]["titulo"] and alertas(r)[0]["avisada"] is False
    assert not (r / "PAUSA").exists() and not (r / "estado" / "pausa-t.yml").exists()
    assert "t-ana" in "\n".join(sal)


def test_el_mismo_post_en_un_segundo_ciclo_no_repite_nada(r, tmp_path):
    guardar_fichas_t(r)
    libro(r, ("p1", "t-ana"))
    revisar(r, tmp_path, [error_post("p1", "copyright claim")])
    antes = arbol(r)
    inf, _, _ = revisar(r, tmp_path, [error_post("p1", "copyright claim")])
    assert inf.posts == [] and len(filas_retiros(r)) == 1 and len(alertas(r)) == 1 and vistos(r) == ["p1"]
    assert {k: v for k, v in arbol(r).items() if "cuota" not in k} == {k: v for k, v in antes.items() if "cuota" not in k}


def test_el_login_sale_del_texto_cuando_el_post_no_esta_en_el_libro(r, tmp_path):
    inf, _, _ = revisar(r, tmp_path, [error_post("p7", "DMCA takedown", texto="GTA 6\n\nCon: twitch.tv/Beto_Juega")])
    assert inf.posts[0]["login"] == "beto_juega" and inf.posts[0]["fuente"] == "t-beto-juega"
    assert filas_retiros(r)[0][1] == "t-beto-juega" and "twitch.tv/beto_juega" in filas_retiros(r)[0][5]
    assert "beto_juega" in vetados.logins_vetados(r)
    assert "beto_juega]" in (r / "config" / "vetados.yml").read_text(encoding="utf-8").splitlines()[1]


def test_sin_streamer_ni_fuente_se_anota_igual_como_desconocida(r, tmp_path):
    inf, _, _ = revisar(r, tmp_path, [error_post("p8", "copyright", texto="sin crédito")])
    assert inf.posts[0]["fuente"] == "desconocida" and filas_retiros(r)[0][1] == "desconocida"
    assert (r / "config" / "vetados.yml").read_text(encoding="utf-8").count("rubius") == 1


def test_vetados_sin_lista_en_linea_no_se_toca_y_basta_la_fila(r, tmp_path):
    (r / "config" / "vetados.yml").write_text("logins:\n  - rubius\n", encoding="utf-8")
    revisar(r, tmp_path, [error_post("p1", "copyright", texto="twitch.tv/ana")])
    assert (r / "config" / "vetados.yml").read_text(encoding="utf-8") == "logins:\n  - rubius\n"
    assert "ana" in vetados.logins_vetados(r)


# ---------------------------------------------------------------- 4 a 6: dos reclamos en 7 días

def test_dos_reclamos_pausan_la_ficha_t_y_nada_mas(r, tmp_path):
    guardar_fichas_t(r)
    libro(r, ("p1", "t-ana"))
    inf, _, _ = revisar(r, tmp_path, [error_post("p1", "copyright claim"),
                                      error_post("p2", "Content ID", texto="twitch.tv/beto")])
    assert inf.pausa_t is True and (r / "estado" / "pausa-t.yml").exists()
    pausa = (r / "estado" / "pausa-t.yml").read_text(encoding="utf-8")
    assert "motivo:" in pausa and "desde:" in pausa and "autor: retiros" in pausa
    assert not (r / "PAUSA").exists()
    assert [a["titulo"] for a in alertas(r)].count("ALERTA ROJA: PAUSA de la ficha T") == 1 and len(alertas(r)) == 3

    def twitch_prohibido(*a, **k):
        raise AssertionError("con la ficha T en pausa no se consulta Twitch")

    cands, saltos, fichas, _ = detectar.detectar_t_con_descartes(r, AHORA, bt.cliente(twitch_prohibido))
    assert cands == [] and saltos == [f"ficha T en PAUSA: {retiros.pausa_t_activa(r)[1]}"]
    por_id = {f["id"]: f for f in fuentes.cargar_fuentes(r)}
    assert por_id["t-caro"]["estado"] == "pausada" and por_id["t-caro"]["publicable"] is False
    assert por_id["t-ana"]["estado"] == "vetada"           # el veto manda sobre la pausa
    assert por_id["fuente-prueba"]["estado"] == "vigente" and por_id["fuente-prueba"]["publicable"] is True
    assert fuentes.es_publicable(por_id["fuente-prueba"], AHORA.date(), "shorts")[0]


def test_un_reclamo_hoy_y_otro_de_hace_8_dias_no_pausan(r, tmp_path):
    fila_a_mano(r, 8)
    inf, _, _ = revisar(r, tmp_path, [error_post("p1", "copyright claim", texto="twitch.tv/ana")])
    assert inf.pausa_t is False and not (r / "estado" / "pausa-t.yml").exists()


def test_el_borde_de_la_ventana_son_7_dias_con_hoy_incluido(r, tmp_path):
    fila_a_mano(r, 6)
    revisar(r, tmp_path, [error_post("p1", "copyright", texto="twitch.tv/ana")])
    assert (r / "estado" / "pausa-t.yml").exists()


def test_fila_a_mano_mas_un_reclamo_de_buffer_el_mismo_dia_pausan(r, tmp_path):
    fila_a_mano(r, 0)
    revisar(r, tmp_path, [error_post("p1", "copyright claim", texto="twitch.tv/ana")])
    assert (r / "estado" / "pausa-t.yml").exists() and not (r / "PAUSA").exists()


def test_la_pausa_t_sin_motivo_es_error_de_configuracion(r):
    (r / "estado").mkdir(exist_ok=True)
    (r / "estado" / "pausa-t.yml").write_text("# sólo un comentario\ndesde: hoy\n", encoding="utf-8")
    inf = validar.Informe()
    validar.validar_pausa_t(r, inf)
    assert any("pausa-t.yml" in e for e in inf.errores)
    (r / "estado" / "pausa-t.yml").write_text("motivo: dos reclamos\n", encoding="utf-8")
    inf = validar.Informe()
    validar.validar_pausa_t(r, inf)
    assert inf.errores == []


# ---------------------------------------------------------------- 7 y 8: cuenta y otros errores

def test_aviso_de_cuenta_lanza_alerta_roja_y_deja_el_post_visto(r, tmp_path):
    with pytest.raises(publicar.AlertaRoja):
        revisar(r, tmp_path, [error_post("p9", "Your account was suspended")])
    assert vistos(r) == ["p9"] and filas_retiros(r) == []


def test_cuenta_y_reclamo_a_la_vez_anota_la_fila_antes_de_la_alerta(r, tmp_path):
    with pytest.raises(publicar.AlertaRoja):
        revisar(r, tmp_path, [error_post("p9", "Account restricted: copyright strike", texto="twitch.tv/ana")])
    assert len(filas_retiros(r)) == 1 and len(alertas(r)) == 1


def test_el_ciclo_crea_la_pausa_global_con_un_aviso_de_cuenta(r):
    buf = BufferFalso([[error_post("p9", "Account suspended by the network")]])
    cod, sal = n6.correr(r, buffer=buf)
    assert cod == 0 and (r / "PAUSA").exists() and "suspended" in (r / "PAUSA").read_text(encoding="utf-8")
    assert vistos(r) == ["p9"] and CLAVE not in sal
    assert not any("createPost" in q for q in buf.consultas)


def test_error_de_formato_solo_se_anota_en_el_registro(r, tmp_path):
    antes = {n: (r / n).read_text(encoding="utf-8") for n in ("retiros.md", "config/vetados.yml")}
    inf, _, _ = revisar(r, tmp_path, [error_post("p3", "Video too short for this channel")])
    assert inf.posts[0]["clase"] == "otro" and inf.reclamos == 0 and inf.cuenta is None
    assert {n: (r / n).read_text(encoding="utf-8") for n in antes} == antes
    assert alertas(r) == [] and not (r / "PAUSA").exists()
    assert "Video too short" in (r / "registro.md").read_text(encoding="utf-8").splitlines()[-1]
    assert vistos(r) == ["p3"]


@pytest.mark.parametrize("mensaje,clase", [
    ("Copyright claim", "reclamo"), ("reclamo de derechos de autor", "reclamo"), ("DMCA", "reclamo"),
    ("video removed by YouTube", "reclamo"), ("Community Guidelines", "reclamo"),
    ("propiedad intelectual", "reclamo"), ("cuenta bloqueada: bloquead", "cuenta"), ("account terminated", "cuenta"),
    ("banned", "cuenta"), ("Account disabled and copyright", "reclamo+cuenta"), ("", "otro"),
    ("conexión del canal caducada", "otro"),
])
def test_clasificacion_por_el_mensaje(mensaje, clase):
    assert retiros.clasificar(mensaje) == clase


# ---------------------------------------------------------------- 9: --seco

def test_seco_no_escribe_ningun_archivo(r, tmp_path):
    guardar_fichas_t(r)
    libro(r, ("p1", "t-ana"))
    buf = BufferFalso([[error_post("p1", "copyright claim"), error_post("p2", "copyright", texto="twitch.tv/beto")]])
    antes = arbol(r)
    sal = []
    cod = retiros.main(["--revisar", "--seco", "--raiz", str(r)], env={"BUFFER_API_KEY": CLAVE}, transporte=buf,
                       ahora=AHORA, salida=sal.append)
    assert cod == 0 and arbol(r) == antes
    texto = "\n".join(sal)
    assert "reclamo" in texto and "twitch.tv/ana" in texto and "pausa" in texto and "seco" in texto
    assert CLAVE not in texto and buf.posts_pedidos()


def test_seco_via_revisar_informa_lo_que_haria(r, tmp_path):
    antes = arbol(r)
    inf, _, _ = revisar(r, tmp_path, [error_post("p1", "copyright", texto="twitch.tv/ana"),
                                      error_post("p2", "copyright", texto="twitch.tv/beto")], escribir=False)
    assert inf.reclamos == 2 and inf.pausa_t is True and arbol(r) == antes


def test_main_sin_clave_se_omite(r):
    sal = []
    assert retiros.main(["--revisar", "--seco", "--raiz", str(r)], env={}, salida=sal.append) == 0
    assert "falta BUFFER_API_KEY" in "\n".join(sal)


# ---------------------------------------------------------------- 10: Buffer falla

def test_buffer_500_no_frena_el_ciclo_y_la_regla_de_dos_en_siete_dias_corre(r):
    fila_a_mano(r, 0, "t-uno")
    fila_a_mano(r, 2, "t-dos")
    buf = BufferFalso(estado=500)
    cod, sal = n6.correr(r, ahora=AHORA, buffer=buf)
    assert cod == 0 and "retiros:" in sal and not (r / "PAUSA").exists() and alertas(r) != []
    assert (r / "estado" / "pausa-t.yml").exists()           # las filas a mano cuentan aunque Buffer no responda
    assert vistos(r) == []


def test_buffer_500_sin_nada_mas_no_crea_alerta_ni_pausa(r):
    cod, sal = n6.correr(r, buffer=BufferFalso(estado=500))
    assert cod == 0 and not (r / "PAUSA").exists() and alertas(r) == []
    assert not (r / "estado" / "pausa-t.yml").exists()


def test_buffer_401_en_la_lectura_es_alerta_roja(r):
    cod, sal = n6.correr(r, buffer=BufferFalso(estado=401))
    assert cod == 0 and (r / "PAUSA").exists() and "401" in (r / "PAUSA").read_text(encoding="utf-8")


def test_sin_clave_el_ciclo_igual_evalua_la_regla_sin_llamar_a_buffer(r):
    fila_a_mano(r, 0, "t-uno")
    fila_a_mano(r, 1, "t-dos")
    buf = BufferFalso()
    cod, _ = n6.correr(r, ahora=AHORA, env={}, buffer=buf)
    assert (r / "estado" / "pausa-t.yml").exists() and buf.consultas == []


def test_el_ensayo_forzado_nunca_revisa_ni_escribe(repo, monkeypatch):
    fila_a_mano(repo, 0, "t-uno")
    fila_a_mano(repo, 1, "t-dos")
    monkeypatch.setattr(ciclo.retiros, "revisar", lambda *a, **k: pytest.fail("el ensayo no revisa retiros"))
    sal = []
    ciclo.ejecutar(repo, ahora=n6.AHORA, ensayo_forzado=True, env={}, imprimir=sal.append, validar_fn=n6.VERDE,
                   detectar_fn=lambda *a: ([], []), hacer_toma_fn=n6.Hacer(repo), salida_dir=repo / "salida")
    assert not (repo / "estado" / "pausa-t.yml").exists()


# ---------------------------------------------------------------- 11: la consulta

def test_post_sin_campo_error_no_lo_pide_y_no_falla(r, tmp_path):
    campos = [campo(n) for n in ("id", "status", "dueAt", "text")]
    buf = BufferFalso([[error_post("p1", None, texto="twitch.tv/ana")]], campos=campos)
    inf, _, _ = revisar(r, tmp_path, buf)
    q = buf.posts_pedidos()[0]
    assert "node { id status dueAt text }" in q and "status: [error]" in q and 'channelIds: ["canal-yt-001"' in q
    assert inf.posts[0]["clase"] == "otro" and inf.posts[0]["motivo"] == ""      # sin mensaje: sólo se anota


def test_error_escalar_se_pide_tal_cual(r, tmp_path):
    buf = BufferFalso([[]])
    revisar(r, tmp_path, buf)
    assert "node { id status dueAt text error }" in buf.posts_pedidos()[0]


def test_error_objeto_pide_solo_los_subcampos_que_existen(r, tmp_path):
    campos = [campo(n) for n in ("id", "status", "dueAt", "text")] + [campo("error", "PostError", "OBJECT")]
    buf = BufferFalso([[error_post("p1", {"message": "Copyright claim", "code": "X1", "otro": "no"},
                                   texto="twitch.tv/ana")]], campos=campos, subcampos=("message", "code", "foo"))
    inf, _, _ = revisar(r, tmp_path, buf)
    assert "error { message code }" in buf.posts_pedidos()[0]
    assert inf.posts[0]["clase"] == "reclamo" and "Copyright claim" in inf.posts[0]["motivo"]


def test_pagina_hasta_tres_veces(r, tmp_path):
    paginas = [[error_post(f"a{i}", "formato")] for i in range(5)]
    buf = BufferFalso(paginas)
    inf, _, _ = revisar(r, tmp_path, buf)
    assert len(buf.posts_pedidos()) == 3 and inf.leidos == 3 and 'after: "c1"' in buf.posts_pedidos()[1]


def test_ignora_posts_que_no_estan_en_error(r, tmp_path):
    inf, _, _ = revisar(r, tmp_path, [{"id": "ok", "status": "sent", "dueAt": "x", "text": "copyright"}])
    assert inf.leidos == 0 and filas_retiros(r) == []


# ---------------------------------------------------------------- 12: issues

def gh_falso(codigo_issue=0, codigo_label=0):
    llamadas = []

    def ejecutor(cmd, **kw):
        llamadas.append(cmd)
        return SimpleNamespace(returncode=codigo_label if cmd[1] == "label" else codigo_issue, stdout="", stderr="")

    ejecutor.llamadas = llamadas
    return ejecutor


def test_abrir_avisos_usa_la_etiqueta_y_marca_avisada(r, tmp_path):
    revisar(r, tmp_path, [error_post("p1", "copyright", texto="twitch.tv/ana")])
    gh = gh_falso()
    assert retiros.abrir_avisos(r, gh) == ["reclamo|p1"]
    label, issue = gh.llamadas
    assert label[:3] == ["gh", "label", "create"] and "alerta-roja" in label and "--force" in label
    assert issue[:3] == ["gh", "issue", "create"] and issue[issue.index("--label") + 1] == "alerta-roja"
    assert alertas(r)[0]["avisada"] is True
    assert retiros.abrir_avisos(r, gh) == [] and len(gh.llamadas) == 2      # nada pendiente: ni siquiera llama


def test_si_gh_falla_la_alerta_queda_pendiente(r, tmp_path):
    revisar(r, tmp_path, [error_post("p1", "copyright", texto="twitch.tv/ana")])
    assert retiros.abrir_avisos(r, gh_falso(codigo_issue=1, codigo_label=1)) == []
    assert alertas(r)[0]["avisada"] is False
    assert retiros.abrir_avisos(r, gh_falso()) == ["reclamo|p1"]


def test_si_gh_no_existe_queda_pendiente(r, tmp_path):
    revisar(r, tmp_path, [error_post("p1", "copyright", texto="twitch.tv/ana")])

    def sin_gh(cmd, **kw):
        raise FileNotFoundError("gh")

    assert retiros.abrir_avisos(r, sin_gh) == [] and alertas(r)[0]["avisada"] is False


# ---------------------------------------------------------------- 13: fuentes que no son T

def test_retiro_de_una_ficha_no_t_la_deja_vetada(r, tmp_path):
    libro(r, ("p5", "sega-fm27"))
    inf, _, _ = revisar(r, tmp_path, [error_post("p5", "Copyright claim")])
    assert filas_retiros(r)[0][1] == "sega-fm27" and "twitch.tv" not in filas_retiros(r)[0][5]
    ficha = next(f for f in fuentes.cargar_fuentes(r) if f["id"] == "sega-fm27")
    assert ficha["estado"] == "vetada" and ficha["publicable"] is False
    assert not fuentes.es_publicable(ficha, AHORA.date(), "shorts")[0]
    assert (r / "config" / "vetados.yml").read_text(encoding="utf-8").count("sega") == 0


def test_una_fila_a_mano_con_el_id_de_una_ficha_la_veta(r):
    (r / "retiros.md").write_text(
        "| Fecha | Fuente | Publicación | Quién pidió | Motivo | Acción |\n|---|---|---|---|---|---|\n"
        "| 2026-11-20 | Sega-FM27 | x | ellos | pidieron | vetada |\n", encoding="utf-8")
    assert next(f for f in fuentes.cargar_fuentes(r) if f["id"] == "sega-fm27")["estado"] == "vetada"


# ---------------------------------------------------------------- 14: la clave

def test_la_clave_no_aparece_en_ninguna_salida_archivo_ni_issue(r, tmp_path):
    guardar_fichas_t(r)
    libro(r, ("p1", "t-ana"))
    mensaje = f"Copyright claim, token {CLAVE} en https://youtube.invalid/firmada?sig=abc123"
    inf, sal, buf = revisar(r, tmp_path, [error_post("p1", mensaje), error_post("p2", f"formato {CLAVE}")])
    gh = gh_falso()
    retiros.abrir_avisos(r, gh)
    todo = "\n".join(sal) + json.dumps(gh.llamadas) + json.dumps(inf.posts)
    for p in r.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".yml", ".json", ".csv", ".txt"):
            todo += p.read_text(encoding="utf-8", errors="ignore")
    assert CLAVE not in todo and "firmada?sig" not in todo and "[url]" in todo


# ---------------------------------------------------------------- el ciclo

def test_el_veto_vale_en_el_mismo_ciclo_antes_de_detectar(r):
    guardar_fichas_t(r)
    libro(r, ("p1", "t-ana"))
    visto = []

    def detectar_espia(raiz, ahora, cliente):
        visto.append("ana" in vetados.logins_vetados(raiz))
        return [], []

    sal = []
    ciclo.ejecutar(r, ahora=n6.AHORA, env=n6.ENV, imprimir=sal.append, validar_fn=n6.VERDE,
                   detectar_fn=detectar_espia, hacer_toma_fn=n6.Hacer(r),
                   transporte_buffer=BufferFalso([[error_post("p1", "copyright claim")]]),
                   transporte_almacen=n6.SupabaseSimulado())
    assert visto == [True] and "[etapa] retiros: inicio" in "\n".join(sal)


def test_sin_hueco_con_clave_tambien_revisa_retiros(r):
    buf = BufferFalso([[error_post("p1", "copyright claim", texto="twitch.tv/ana")]])
    cod, sal = n6.correr(r, ahora=n6.datetime(2026, 11, 18, 11, 7, tzinfo=n6.ZONA), buffer=buf)
    assert "sin hueco" in sal and "ana" in vetados.logins_vetados(r)


# ---------------------------------------------------------------- los workflows

RAIZ = Path(__file__).resolve().parent.parent


def test_fabrica_yml_permite_los_archivos_y_abre_issues_con_etiqueta():
    wf = yaml.safe_load((RAIZ / ".github" / "workflows" / "fabrica.yml").read_text(encoding="utf-8"))
    run = next(p for p in wf["jobs"]["producir"]["steps"] if "Cerrar el ciclo" in p.get("name", ""))["run"]
    permitidos = re.compile(re.search(r"PERMITIDOS='([^']+)'", run).group(1))
    assert permitidos.match("retiros.md") and permitidos.match("config/vetados.yml")
    assert permitidos.match("estado/alertas-retiro.json") and not permitidos.match("config/modo.yml")
    agregar = re.search(r"for ruta in ([^;]+); do", run).group(1).split()
    assert "retiros.md" in agregar and "config/vetados.yml" in agregar
    assert "python -m fabrica.retiros --abrir-avisos || true" in run
    assert run.index("fabrica.retiros --abrir-avisos") < run.index("git commit")
    assert "gh label create alerta-roja --color B60205 --force" in run
    assert re.search(r'gh issue create --title "ALERTA ROJA: [^\n]*--label alerta-roja', run)


def test_diagnostico_corre_el_paso_seco_con_python_instalado():
    wf = yaml.safe_load((RAIZ / ".github" / "workflows" / "diagnostico.yml").read_text(encoding="utf-8"))
    pasos = wf["jobs"]["diagnostico"]["steps"]
    i = next(i for i, p in enumerate(pasos) if "retiros" in p.get("name", "").lower())
    assert pasos[i]["run"].strip() == "python -m fabrica.retiros --revisar --seco"
    assert any(p.get("uses", "").startswith("actions/setup-python") for p in pasos[:i])
    assert any("pip install" in p.get("run", "") for p in pasos[:i])
    assert wf["jobs"]["diagnostico"]["env"]["BUFFER_API_KEY"]

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
    cod, sal = n6.correr(r, ahora=n6.datetime(2026, 11, 18, 3, 7, tzinfo=n6.ZONA), buffer=buf)
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


# ---------------------------------------------------------------- revisión del 20 nov (fusión con main)

@pytest.mark.parametrize("mensaje,clase", [
    ("Your video has been blocked due to a copyright claim", "reclamo"),
    ("Este video fue eliminado por infringir las normas de la comunidad", "reclamo"),
    ("Request blocked: rate limit exceeded, try again later", "otro"),
    ("Too many requests", "otro"), ("Internal server error (502)", "otro"),
    ("Media removed from the queue", "otro"), ("Video violates the maximum file size", "otro"),
    ("Could not reclaim the upload slot", "otro"), ("Uploads disabled for this video format", "otro"),
    ("Your channel is restricted", "cuenta"), ("Account temporarily suspended", "cuenta"),
    ("Copyright check failed: service unavailable, try again", "dudoso"),
    ("Account suspended? 503, please retry", "dudoso"),
])
def test_clasificacion_estricta_un_error_pasajero_nunca_veta(mensaje, clase):
    assert retiros.clasificar(mensaje) == clase


def test_un_error_pasajero_de_buffer_en_el_post_no_veta_ni_pausa(r, tmp_path):
    antes = {n: (r / n).read_text(encoding="utf-8") for n in ("retiros.md", "config/vetados.yml")}
    inf, _, _ = revisar(r, tmp_path, [error_post("p1", "Request blocked: rate limit, try again", texto="twitch.tv/ana"),
                                      error_post("p2", "503 Service Unavailable", texto="twitch.tv/beto")])
    assert [p["clase"] for p in inf.posts] == ["otro", "otro"] and inf.reclamos == 0 and inf.cuenta is None
    assert {n: (r / n).read_text(encoding="utf-8") for n in antes} == antes and alertas(r) == []
    assert not (r / "PAUSA").exists() and not (r / "estado" / "pausa-t.yml").exists()
    assert "ana" not in vetados.logins_vetados(r)


def test_un_reclamo_con_error_pasajero_es_dudoso_avisa_y_no_veta(r, tmp_path):
    fila_a_mano(r, 0)                                 # con otra fila hoy, un reclamo de verdad pausaría la ficha T
    inf, _, _ = revisar(r, tmp_path, [error_post("p1", "Copyright check failed: try again", texto="twitch.tv/ana")])
    assert inf.dudosos == 1 and inf.reclamos == 0 and len(filas_retiros(r)) == 1
    assert "ana" not in vetados.logins_vetados(r) and not (r / "estado" / "pausa-t.yml").exists()
    assert [a["titulo"] for a in alertas(r)] == ["REVISAR: posible reclamo en shorts (fuente t-ana)"]
    assert "sin veto" in (r / "registro.md").read_text(encoding="utf-8").splitlines()[-1]
    assert vistos(r) == ["p1"]


class IntrospeccionCaida(BufferFalso):
    """La introspección responde 500 (dos veces, con el reintento); el resto, como BufferFalso."""

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode("utf-8"))["query"]
        if "__type" in q:
            self.consultas.append(q)
            return F.resp(500, {"errors": [{"message": "fallo"}]}, retry_after="0")
        return super().__call__(metodo, url, encabezados, cuerpo, tiempo)


def test_si_la_introspeccion_falla_no_se_lee_sin_el_campo_error(r, tmp_path):
    buf = IntrospeccionCaida([[error_post("p1", "copyright claim", texto="twitch.tv/ana")]])
    with pytest.raises(publicar.ErrorBuffer):
        revisar(r, tmp_path, buf)
    assert buf.posts_pedidos() == [] and vistos(r) == [] and filas_retiros(r) == []
    inf, _, _ = revisar(r, tmp_path, BufferFalso([[error_post("p1", "copyright claim", texto="twitch.tv/ana")]]))
    assert inf.reclamos == 1                          # al volver Buffer, el reclamo se ve y se veta


def test_buffer_que_no_responde_no_cierra_ni_gasta_nada_en_el_ciclo(r):
    (r / "estado").mkdir(exist_ok=True)
    envios = r / "estado" / "buffer-envios.json"
    if not envios.exists():
        envios.write_text("[]", encoding="utf-8")
    antes = {n: (r / n).read_bytes() for n in ("estado/buffer-envios.json", "retiros.md", "config/vetados.yml")}
    for estado in (429, 500, 503):
        cod, sal = n6.correr(r, buffer=BufferFalso(estado=estado))
        assert cod == 0 and not (r / "PAUSA").exists() and "retiros:" in sal
    assert {n: (r / n).read_bytes() for n in antes} == antes and vistos(r) == [] and alertas(r) == []


def test_la_cuota_justa_no_lee_buffer_pero_la_regla_corre(r, tmp_path):
    fila_a_mano(r, 0, "t-uno")
    fila_a_mano(r, 1, "t-dos")
    buf = BufferFalso([[error_post("p1", "copyright claim", texto="twitch.tv/ana")]])
    cliente = cliente_de(tmp_path, buf)
    for _ in range(int(publicar.TOPES["dia"] * retiros.UMBRAL_CUOTA_RETIROS)):
        cliente.cuota.registrar()
    sal = []
    inf = retiros.revisar(r, cliente, CANALES, ORG, AHORA, sal.append)
    assert buf.consultas == [] and inf.leidos == 0 and "la publicación va primero" in "\n".join(sal)
    assert inf.pausa_t is True and (r / "estado" / "pausa-t.yml").exists()


def test_cada_llamada_lleva_tope_y_el_presupuesto_corta_la_lectura(r, tmp_path):
    tiempos = []
    buf = BufferFalso([[error_post("a1", "formato")]] * 5)

    def medido(metodo, url, encabezados, cuerpo=None, tiempo=60):
        tiempos.append(tiempo)
        return buf(metodo, url, encabezados, cuerpo, tiempo)

    retiros.revisar(r, cliente_de(tmp_path, medido), CANALES, ORG, AHORA, lambda t: None)
    assert tiempos and max(tiempos) <= retiros.TOPE_LLAMADA_S
    cliente = cliente_de(tmp_path, buf)
    transporte_original = cliente.transporte
    n = len(buf.consultas)
    with pytest.raises(publicar.ErrorBuffer, match="presupuesto"):
        retiros.revisar(r, cliente, CANALES, ORG, AHORA, lambda t: None, limite_s=0.0)
    assert len(buf.consultas) == n and cliente.transporte is transporte_original     # ni una llamada; se restaura


def test_el_ciclo_le_da_a_retiros_un_plazo_propio(r, monkeypatch):
    visto = {}
    monkeypatch.setattr(ciclo.retiros, "revisar", lambda *a, **k: visto.update(k))
    n6.correr(r, buffer=BufferFalso([[]]))
    assert 0 < visto["limite_s"] <= retiros.PRESUPUESTO_S and n6.SUPA_CLAVE in visto["secretos"]


def test_el_esquema_se_guarda_y_se_relee_si_cambia(r, tmp_path):
    buf = BufferFalso([[]])
    revisar(r, tmp_path, buf)
    revisar(r, tmp_path, buf)
    assert sum('__type(name: "Post")' in q for q in buf.consultas) == 1 and len(buf.posts_pedidos()) == 2

    class EsquemaViejo(BufferFalso):
        def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
            q = json.loads(cuerpo.decode("utf-8"))["query"]
            if "posts(" in q:
                self.consultas.append(q)
                return F.resp(200, {"errors": [{"message": "Cannot query field error on type Post"}]})
            return super().__call__(metodo, url, encabezados, cuerpo, tiempo)

    with pytest.raises(publicar.ErrorBuffer):
        revisar(r, tmp_path, EsquemaViejo([[]]))
    assert not (r / "estado" / "retiros-esquema.json").exists()


def test_la_pausa_t_dice_el_motivo_y_los_reclamos_y_es_yaml_valido(r, tmp_path):
    fila_a_mano(r, 3, "t-uno: #raro")
    revisar(r, tmp_path, [error_post("p1", "copyright claim", texto="twitch.tv/ana")])
    texto = (r / "estado" / "pausa-t.yml").read_text(encoding="utf-8")
    datos = yaml.safe_load(texto)
    assert datos["autor"] == "retiros" and "2 reclamos en 7 días" in datos["motivo"]
    assert "t-ana" in datos["motivo"] and "t-uno" in datos["motivo"] and "2026-11-17" in datos["motivo"]
    assert retiros.pausa_t_activa(r) == (True, datos["motivo"])
    inf = validar.Informe()
    validar.validar_pausa_t(r, inf)
    assert inf.errores == [] and not (r / "PAUSA").exists()
    assert datos["motivo"] in next(a["cuerpo"] for a in alertas(r) if a["clave"].startswith("pausa-t"))


def test_otras_claves_y_tokens_no_salen_en_filas_ni_issues(r, tmp_path):
    otra = "sb_secret_otra-clave-larga-123"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.firma-falsa"
    mensaje = f"Copyright claim (service_key={otra}, jwt {jwt}, access_token: abc123def)"
    inf, sal, _ = revisar(r, tmp_path, [error_post("p1", mensaje, texto="twitch.tv/ana")], secretos=[otra])
    gh = gh_falso()
    retiros.abrir_avisos(r, gh)
    todo = "\n".join(sal) + json.dumps(gh.llamadas) + json.dumps(inf.posts) + (r / "retiros.md").read_text()
    todo += (r / "estado" / "alertas-retiro.json").read_text()
    assert otra not in todo and jwt not in todo and "abc123def" not in todo and inf.reclamos == 1


def test_un_id_de_post_ilegible_se_salta(r, tmp_path):
    inf, sal, _ = revisar(r, tmp_path, [error_post("p1; rm -rf /", "copyright claim", texto="twitch.tv/ana")])
    assert inf.posts == [] and filas_retiros(r) == [] and vistos(r) == [] and "ilegible" in "\n".join(sal)


def test_la_lectura_de_retiros_nunca_manda_mutaciones(r, tmp_path):
    buf = BufferFalso([[error_post("p1", "copyright claim", texto="twitch.tv/ana")]] * 2)
    revisar(r, tmp_path, buf)
    assert buf.consultas and all(q.lstrip().startswith("query") and "mutation" not in q for q in buf.consultas)


def test_si_la_etiqueta_no_existe_el_aviso_sale_igual_sin_ella(r, tmp_path):
    revisar(r, tmp_path, [error_post("p1", "copyright", texto="twitch.tv/ana")])
    llamadas = []

    def gh(cmd, **kw):
        llamadas.append(cmd)
        return SimpleNamespace(returncode=1 if "--label" in cmd or cmd[1] == "label" else 0)

    assert retiros.abrir_avisos(r, gh) == ["reclamo|p1"] and alertas(r)[0]["avisada"] is True
    assert "--label" not in llamadas[-1] and llamadas[-1][:3] == ["gh", "issue", "create"]


# ---------------------------------------------------------------- --diagnostico (sólo lectura)

class BufferDiag(BufferFalso):
    """BufferFalso más organizaciones y canales, y posts en error por canal."""

    def __init__(self, por_canal=None, **kw):
        super().__init__(**kw)
        self.por_canal = por_canal or {}

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        q = json.loads(cuerpo.decode("utf-8"))["query"]
        if self.estado == 200 and "organizations" in q:
            self.consultas.append(q)
            return F.resp(200, {"data": {"account": {"organizations": [{"id": ORG, "name": "Org"}]}}})
        if self.estado == 200 and "channels(" in q:
            self.consultas.append(q)
            return F.resp(200, {"data": {"channels": [
                {"id": CANALES["shorts"], "name": "guionvivo", "displayName": "G", "service": "youtube",
                 "isQueuePaused": False},
                {"id": CANALES["tiktok"], "name": "guionvivo", "displayName": "G", "service": "tiktok",
                 "isQueuePaused": False},
                {"id": CANALES["reels"], "name": "guionvivo", "displayName": "G", "service": "instagram",
                 "isQueuePaused": False}]}})
        if self.estado == 200 and "posts(" in q:
            self.consultas.append(q)
            for canal, posts in self.por_canal.items():
                if f'"{canal}"' in q:
                    return F.resp(200, {"data": {"posts": {"edges": [{"node": p} for p in posts],
                                                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
            return F.resp(200, {"data": {"posts": {"edges": [], "pageInfo": {"hasNextPage": False}}}})
        return super().__call__(metodo, url, encabezados, cuerpo, tiempo)


def diag(r, buf):
    sal = []
    cod = retiros.main(["--diagnostico", "--raiz", str(r)], env={"BUFFER_API_KEY": CLAVE}, transporte=buf,
                       ahora=AHORA, salida=sal.append)
    return cod, "\n".join(sal)


def test_diagnostico_lista_vacia(r):
    antes = arbol(r)
    cod, texto = diag(r, BufferDiag())
    assert cod == 0 and arbol(r) == antes
    assert "3 canal(es) visto(s)" in texto and "selector de error elegido: error" in texto
    assert texto.count("0 publicación(es) en error") >= 3


def test_diagnostico_una_en_error_sólo_dice_red_fecha_y_clase(r):
    antes = arbol(r)
    largo = f"copyright claim por {CLAVE} " + "x" * 300
    buf = BufferDiag({CANALES["shorts"]: [error_post("p1", largo)]})
    cod, texto = diag(r, buf)
    assert cod == 0 and arbol(r) == antes          # no escribe retiros.md, vetados, PAUSA, cuota ni vistos
    assert CLAVE not in texto and "shorts: 1 publicación(es) en error" in texto
    # Registros públicos: ni el texto del error, ni el id de la publicación, ni nombres o ids de canales.
    assert "copyright claim" not in texto and "p1" not in texto
    assert not any(c in texto for c in CANALES.values())
    linea = next(l for l in texto.splitlines() if l.strip().startswith("shorts |"))
    assert "2026-11-19" in linea and linea.split(" | ")[2] == retiros.clasificar(largo)


def test_diagnostico_fallo_de_la_api_da_1_sin_secretos(r):
    antes = arbol(r)
    cod, texto = diag(r, BufferDiag(estado=500))
    assert cod == 1 and arbol(r) == antes
    assert "falló la consulta a Buffer (" in texto and CLAVE not in texto and "500" not in texto


def test_diagnostico_sin_clave_da_1(r):
    sal = []
    assert retiros.main(["--diagnostico", "--raiz", str(r)], env={}, salida=sal.append) == 1

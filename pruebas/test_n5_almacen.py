"""N5 sección 1: almacén temporal (Supabase Storage). Sin red: transporte falso con respuestas grabadas."""
import hashlib
import json
import shutil

import pytest
import yaml

import falsos_n5 as F
from fabrica import validar
from fabrica.almacen import Almacen, ErrorAlmacen, crear_mp4_de_prueba

URL = "https://proyecto-falso.supabase.invalid"
CLAVE = "sb_secret_clave-inventada-n5"   # EJEMPLO-FALSO
CONFIG = {"bucket": "clips", "max_mb": 50, "prefijo": "tomas"}
URL_PUBLICA = f"{URL}/storage/v1/object/public/clips/tomas/2026-11-18/toma-012-"


def almacen(reglas, **kw):
    esp = F.Espia(reglas)
    a = Almacen(URL, CLAVE, kw.pop("config", CONFIG), esp, dormir=lambda s: None)
    return a, esp


def mp4_falso(tmp_path, contenido=b"video-inventado", nombre="clip.mp4"):
    p = tmp_path / nombre
    p.write_bytes(contenido)
    return p


def test_bucket_existente_no_se_crea():
    a, esp = almacen([F.resp(200, F.leer("supabase", "bucket_existe.json"))])
    assert a.asegurar_bucket() is False
    assert esp.rutas() == [("GET", "/storage/v1/bucket/clips")]


def test_bucket_inexistente_se_crea_con_el_cuerpo_de_la_especificacion():
    a, esp = almacen([F.resp(404, F.leer("supabase", "bucket_404.json")),
                      F.resp(200, F.leer("supabase", "bucket_creado.json"))])
    assert a.asegurar_bucket() is True
    assert esp.rutas() == [("GET", "/storage/v1/bucket/clips"), ("POST", "/storage/v1/bucket")]
    assert json.loads(esp.llamadas[1]["cuerpo"]) == {
        "id": "clips", "name": "clips", "public": True,
        "file_size_limit": 52428800, "allowed_mime_types": ["video/mp4"]}


def test_encabezados_apikey_y_bearer_con_el_mismo_valor():
    a, esp = almacen([F.resp(200, "{}")])
    a.asegurar_bucket()
    h = esp.llamadas[0]["encabezados"]
    assert h["apikey"] == CLAVE and h["Authorization"] == f"Bearer {CLAVE}"


def test_subida_correcta_ruta_url_y_encabezados(tmp_path):
    mp4 = mp4_falso(tmp_path)
    sha = hashlib.sha256(mp4.read_bytes()).hexdigest()[:8]
    a, esp = almacen([F.resp(200, F.leer("supabase", "subida_ok.json"))])
    ruta, url = a.subir(mp4, 12, fecha="2026-11-18")
    assert ruta == f"tomas/2026-11-18/toma-012-{sha}.mp4" and " " not in ruta
    assert url == f"{URL}/storage/v1/object/public/clips/{ruta}"
    c = esp.llamadas[0]
    assert c["metodo"] == "POST" and c["ruta"] == f"/storage/v1/object/clips/{ruta}"
    assert c["encabezados"]["Content-Type"] == "video/mp4"
    assert c["encabezados"]["x-upsert"] == "true"
    assert c["cuerpo"] == mp4.read_bytes()


def test_archivo_de_mas_de_50_mb_se_rechaza_antes_de_subir_con_el_tope_real(tmp_path):
    from fabrica.config import cargar_yaml
    real = cargar_yaml("config/almacen.yml")
    assert real["max_mb"] == 50
    mp4 = tmp_path / "grande.mp4"
    with open(mp4, "wb") as f:               # archivo disperso: pesa 50 MB + 1 byte sin ocupar disco
        f.truncate(50 * 1024 * 1024 + 1)
    a, esp = almacen([F.resp(200, "{}")], config=real)
    with pytest.raises(ErrorAlmacen, match="no se sube"):
        a.subir(mp4, 1)
    assert esp.llamadas == []


def test_archivo_de_exactamente_el_tope_si_sube(tmp_path):
    mp4 = mp4_falso(tmp_path, b"x" * (1024 * 1024))
    a, esp = almacen([F.resp(200, F.leer("supabase", "subida_ok.json"))], config={**CONFIG, "max_mb": 1})
    a.subir(mp4, 1, fecha="2026-11-18")
    assert len(esp.llamadas) == 1


@pytest.mark.parametrize("estado", [200, 206])
def test_url_publica_con_video_pasa_con_200_y_con_206(estado):
    a, esp = almacen([F.resp(estado, b"\x00", "video/mp4")])
    a.comprobar(URL_PUBLICA + "ab12cd34.mp4")
    c = esp.llamadas[0]
    assert c["metodo"] == "GET" and c["encabezados"]["Range"] == "bytes=0-0"


def test_url_publica_con_tipo_que_no_es_video_falla():
    a, _ = almacen([F.resp(200, b"<html>", "text/html")])
    with pytest.raises(ErrorAlmacen, match="no video"):
        a.comprobar(URL_PUBLICA + "ab12cd34.mp4")


def test_url_publica_404_despues_de_borrar():
    a, _ = almacen([F.resp(404, F.leer("supabase", "objeto_404.json"))])
    a.confirmar_ausente(URL_PUBLICA + "ab12cd34.mp4")
    with pytest.raises(ErrorAlmacen, match="código 404"):
        a.comprobar(URL_PUBLICA + "ab12cd34.mp4")


def test_400_de_storage_con_objeto_no_encontrado_tambien_cuenta_como_ausente():
    a, _ = almacen([F.resp(400, F.leer("supabase", "objeto_404.json"))])
    a.confirmar_ausente(URL_PUBLICA + "ab12cd34.mp4")


@pytest.mark.parametrize("estado,cuerpo", [(403, "{}"), (503, "{}"), (404, "<html>no es de Storage</html>"),
                                           (400, '{"error":"otra cosa"}')])
def test_otros_errores_tras_borrar_no_cuentan_como_ausente(estado, cuerpo):
    a, _ = almacen([F.resp(estado, cuerpo)])
    with pytest.raises(ErrorAlmacen, match="no se pudo confirmar|código"):
        a.confirmar_ausente(URL_PUBLICA + "ab12cd34.mp4")


def test_la_comprobacion_de_la_url_publica_es_anonima():
    a, esp = almacen([F.resp(206, b"\x00", "video/mp4"), F.resp(404, F.leer("supabase", "objeto_404.json"))])
    a.comprobar(URL_PUBLICA + "ab12cd34.mp4")
    a.confirmar_ausente(URL_PUBLICA + "ab12cd34.mp4")
    for c in esp.llamadas:
        bajos = {k.lower() for k in c["encabezados"]}
        assert not ({"apikey", "authorization"} & bajos)
        assert CLAVE not in json.dumps(c["encabezados"])


def test_redireccion_a_otro_origen_con_clave_se_rechaza():
    import urllib.request
    from fabrica import red
    m = red.ManejadorRedireccion()
    req = urllib.request.Request("https://a.invalid/x", headers={"apikey": CLAVE})
    for destino in ("https://otro.invalid/x", "http://a.invalid/x", "https://a.invalid:8443/x"):
        with pytest.raises(red.ErrorRed, match="otro origen") as e:
            m.redirect_request(req, None, 302, "Found", {}, destino)
        assert CLAVE not in str(e.value)
    assert m.redirect_request(req, None, 302, "Found", {}, "https://a.invalid/y") is not None


def test_redireccion_sin_clave_si_se_sigue():
    import urllib.request
    from fabrica import red
    req = urllib.request.Request("https://a.invalid/x", headers={"Range": "bytes=0-0"})
    assert red.ManejadorRedireccion().redirect_request(req, None, 302, "Found", {}, "https://cdn.invalid/x")


def test_una_mutacion_no_se_reintenta():
    from fabrica import red
    esp = F.Espia([F.resp(500, "{}"), F.resp(200, "{}")])
    estado, _, _ = red.solicitar(esp, "POST", "https://x.invalid/", {}, b"", dormir=lambda s: None,
                                 reintentar=False)
    assert estado == 500 and len(esp.llamadas) == 1


def test_borrar_manda_los_prefijos():
    a, esp = almacen([F.resp(200, F.leer("supabase", "borrado_ok.json"))])
    a.borrar("tomas/2026-11-18/toma-012-ab12cd34.mp4")
    c = esp.llamadas[0]
    assert c["metodo"] == "DELETE" and c["ruta"] == "/storage/v1/object/clips"
    assert json.loads(c["cuerpo"]) == {"prefixes": ["tomas/2026-11-18/toma-012-ab12cd34.mp4"]}


def test_error_5xx_se_reintenta_una_vez_y_luego_falla_sin_la_clave(tmp_path):
    a, esp = almacen([F.resp(500, F.leer("supabase", "error_500.json"))])
    with pytest.raises(ErrorAlmacen) as e:
        a.subir(mp4_falso(tmp_path), 3, fecha="2026-11-18")
    assert len(esp.llamadas) == 2
    assert "500" in str(e.value) and "/storage/v1/object/clips/" in str(e.value)
    assert CLAVE not in str(e.value) and URL not in str(e.value)


def test_429_con_reintento_que_funciona(tmp_path):
    a, esp = almacen([F.resp(429, "{}"), F.resp(200, F.leer("supabase", "subida_ok.json"))])
    a.subir(mp4_falso(tmp_path), 3, fecha="2026-11-18")
    assert len(esp.llamadas) == 2


def test_error_4xx_no_se_reintenta(tmp_path):
    a, esp = almacen([F.resp(403, "{}")])
    with pytest.raises(ErrorAlmacen, match="403"):
        a.subir(mp4_falso(tmp_path), 3, fecha="2026-11-18")
    assert len(esp.llamadas) == 1


def test_sin_secretos_no_se_construye():
    with pytest.raises(ErrorAlmacen, match="falta SUPABASE"):
        Almacen("", "", CONFIG, F.Espia([F.resp()]))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_ida_y_vuelta_completa_con_almacen_en_memoria():
    objetos = {}

    def reglas(metodo, url, cab, cuerpo):
        ruta = url.split(".invalid", 1)[1]
        if ruta == "/storage/v1/bucket/clips":
            return F.resp(200, F.leer("supabase", "bucket_existe.json"))
        if metodo == "POST":
            objetos[ruta.replace("/object/clips/", "/object/public/clips/")] = cuerpo
            return F.resp(200, F.leer("supabase", "subida_ok.json"))
        if metodo == "DELETE":
            objetos.clear()
            return F.resp(200, F.leer("supabase", "borrado_ok.json"))
        if ruta in objetos:
            return F.resp(206, b"\x00", "video/mp4")
        return F.resp(404, F.leer("supabase", "objeto_404.json"))

    a, esp = almacen(reglas)
    r = a.ida_y_vuelta()
    assert r["subida"] and r["url_comprobada"] and r["borrado"] and r["bytes"] > 1000
    assert [m for m, _ in esp.rutas()].count("DELETE") == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_ida_y_vuelta_falla_si_la_url_sigue_viva_tras_borrar():
    def reglas(metodo, url, cab, cuerpo):
        if "/bucket/" in url:
            return F.resp(200, "{}")
        if metodo == "GET":
            return F.resp(206, b"\x00", "video/mp4")
        return F.resp(200, "[]")
    a, _ = almacen(reglas)
    with pytest.raises(ErrorAlmacen, match="sigue respondiendo"):
        a.ida_y_vuelta()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="sin ffmpeg")
def test_mp4_de_prueba_dura_un_segundo(tmp_path):
    p = crear_mp4_de_prueba(tmp_path / "t.mp4")
    assert p.stat().st_size > 1000


# ---- validar config/almacen.yml ----

def test_validar_acepta_almacen_real(repo):
    assert validar.validar(repo).errores == []


@pytest.mark.parametrize("cambio,fragmento", [
    ({"max_mb": 80}, "max_mb"),
    ({"bucket": "Clips Mal"}, "bucket"),
    ({"prefijo": "/tomas/"}, "prefijo"),
])
def test_validar_rechaza_almacen_malo(repo, editar_yaml, cambio, fragmento):
    editar_yaml(repo, "config/almacen.yml", lambda d: {**d, **cambio})
    assert any(fragmento in e for e in validar.validar(repo).errores)


def test_validar_exige_el_archivo_de_almacen(repo):
    (repo / "config" / "almacen.yml").unlink()
    assert any("almacen.yml" in e for e in validar.validar(repo).errores)


@pytest.mark.parametrize("estado,cuerpo", [
    (404, "<html>404 Not Found</html>"),
    (404, '{"statusCode":"404","error":"Bucket not found","message":"Bucket not found"}'),
    (400, "[]"), (404, "")])
def test_html_o_bucket_no_encontrado_no_cuentan_como_objeto_ausente(estado, cuerpo):
    a, _ = almacen([F.resp(estado, cuerpo)])
    with pytest.raises(ErrorAlmacen, match="no se pudo confirmar"):
        a.confirmar_ausente(URL_PUBLICA + "ab12cd34.mp4")


@pytest.mark.parametrize("cuerpo", ['{"statusCode":"404","error":"not_found","message":"Object not found"}',
                                    '{"error":"Object not found"}'])
def test_el_json_de_objeto_no_encontrado_de_storage_si_cuenta(cuerpo):
    a, _ = almacen([F.resp(404, cuerpo)])
    a.confirmar_ausente(URL_PUBLICA + "ab12cd34.mp4")


@pytest.mark.parametrize("cuerpo", ['{"error":"not_found","message":"Route not found"}',
                                    '{"error":"not_found"}',
                                    '{"error":404,"message":"Object not found"}',
                                    '{"error":"not_found","message":["Object not found"]}'])
def test_not_found_sin_indicacion_de_objeto_o_con_tipos_raros_no_cuenta(cuerpo):
    a, _ = almacen([F.resp(404, cuerpo)])
    with pytest.raises(ErrorAlmacen, match="no se pudo confirmar"):
        a.confirmar_ausente(URL_PUBLICA + "ab12cd34.mp4")


def test_la_url_de_supabase_copiada_con_rest_v1_se_normaliza():
    from fabrica.almacen import normalizar_url
    base = "https://abcd1234.supabase.co"
    for variante in (base, base + "/", base + "/rest/v1", base + "/rest/v1/", " " + base + "/REST/V1/ ",
                     base + "/storage/v1", base + "/auth/v1"):
        assert normalizar_url(variante) == base


def test_una_url_que_no_es_de_supabase_se_rechaza_antes_de_pedir_nada():
    from fabrica.almacen import Almacen, ErrorAlmacen
    llamadas = []

    def transporte(*a, **k):
        llamadas.append(a)
        raise AssertionError("no debía haber red")

    cfg = {"bucket": "clips", "max_mb": 50, "prefijo": "tomas"}
    for mala in ("https://otro.example/rest/v1", "http://abcd.supabase.co", "https://abcd.supabase.co.evil.io",
                 "https://abcd.supabase.co/otra/ruta", "https://u@abcd.supabase.co", "https://@abcd.supabase.co",
                 "https://abcd.supabase.co#x", "https://abcd.supabase.co?", "https://abcd.supabase.co?a=1"):
        with pytest.raises(ErrorAlmacen):
            Almacen(mala, "clave-falsa", cfg, transporte=transporte)
    assert llamadas == []
    assert Almacen("https://abcd.supabase.co/rest/v1/", "clave-falsa", cfg, transporte=transporte).url \
        == "https://abcd.supabase.co"

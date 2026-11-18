"""Transportes simulados para el ensayo sin claves (y para las pruebas).

Sirven las respuestas grabadas de `pruebas/datos/buffer/` y un almacén en memoria con la forma de
Supabase Storage. Nada sale a la red. Son inventados: lo que demuestran es la lógica de la fábrica, no
la cuenta real; por eso el ensayo simulado queda anotado como "pendiente de Kevin".
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

DATOS = Path(__file__).resolve().parent.parent / "pruebas" / "datos"


def _json(estado, cuerpo, tipo="application/json"):
    datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode("utf-8")
    return estado, {"content-type": tipo}, datos


class BufferSimulado:
    """Transporte GraphQL que enruta por el texto de la consulta. Guarda las consultas recibidas."""

    def __init__(self, esquema="esquema.json", canales="canales.json", crear="createpost_ok.json",
                 estado_post="post_enviado.json", borrar="deletepost_ok.json", datos: Path = DATOS):
        self.dir = Path(datos) / "buffer"
        self.archivos = {"esquema": esquema, "canales": canales, "crear": crear, "estado": estado_post,
                         "borrar": borrar}
        self.consultas: list[str] = []
        self.variables: list[dict] = []

    def _leer(self, nombre):
        return json.loads((self.dir / nombre).read_text(encoding="utf-8"))

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        peticion = json.loads(cuerpo.decode("utf-8"))
        q, v = peticion["query"], peticion.get("variables") or {}
        self.consultas.append(q)
        self.variables.append(v)
        if "__type(name: $n)" in q:
            esquema = self._leer(self.archivos["esquema"])["tipos"]
            return _json(200, {"data": {"__type": esquema.get(v.get("n"))}})
        if "__type(name: \"Query\")" in q:
            return _json(200, {"data": {"__type": self._leer(self.archivos["esquema"])["Query"]}})
        if "__type(name: \"Post\")" in q:
            return _json(200, {"data": {"__type": {"fields": [{"name": n} for n in ("id", "status", "dueAt", "text")]}}})
        if "createPost" in q:
            return _json(200, self._leer(self.archivos["crear"]))
        if "deletePost" in q:
            return _json(200, self._leer(self.archivos["borrar"]))
        if "organizations" in q:
            return _json(200, self._leer("organizacion.json"))
        if "channels(" in q:
            return _json(200, self._leer(self.archivos["canales"]))
        if "posts(" in q:
            return _json(200, self._leer("posts_enviados.json"))
        if "post(" in q:
            return _json(200, self._leer(self.archivos["estado"]))
        return _json(200, {"errors": [{"message": "consulta no simulada"}]})

    def hubo(self, texto: str) -> bool:
        return any(texto in q for q in self.consultas)


class SupabaseSimulado:
    """Almacén en memoria: bucket, subida, lectura pública y borrado."""

    def __init__(self):
        self.objetos: dict[str, bytes] = {}
        self.bucket = False
        self.llamadas: list[tuple[str, str]] = []

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        ruta = urlsplit(url).path
        self.llamadas.append((metodo, ruta))
        if ruta.startswith("/storage/v1/bucket"):
            if metodo == "GET":
                return _json(200, {"id": "clips"}) if self.bucket else _json(404, {"error": "Bucket not found"})
            self.bucket = True
            return _json(200, {"name": "clips"})
        if ruta.startswith("/storage/v1/object/public/"):
            if ruta in self.objetos:
                return 206, {"content-type": "video/mp4"}, b"\x00"
            return _json(404, {"error": "Object not found"})
        if ruta.startswith("/storage/v1/object/") and metodo == "POST":
            self.objetos[ruta.replace("/object/", "/object/public/", 1)] = cuerpo or b""
            return _json(200, {"Key": ruta})
        if ruta.startswith("/storage/v1/object/") and metodo == "DELETE":
            for p in json.loads(cuerpo.decode("utf-8"))["prefixes"]:
                self.objetos.pop("/storage/v1/object/public/clips/" + p, None)
            return _json(200, [])
        return _json(400, {"error": "ruta no simulada"})

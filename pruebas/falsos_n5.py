"""Transportes falsos para las pruebas de N5: sirven respuestas grabadas y espían las llamadas. Sin red."""
import json
from pathlib import Path
from urllib.parse import urlsplit

DATOS = Path(__file__).parent / "datos"


def leer(sub, nombre):
    return (DATOS / sub / nombre).read_text(encoding="utf-8")


def cargar(sub, nombre):
    return json.loads(leer(sub, nombre))


def resp(estado=200, cuerpo=b"", tipo="application/json", **cab):
    """Respuesta (estado, encabezados, bytes). `cuerpo` puede ser texto, bytes u objeto JSON."""
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode("utf-8")
    elif not isinstance(cuerpo, bytes):
        cuerpo = json.dumps(cuerpo).encode("utf-8")
    h = {"content-type": tipo}
    h.update({k.replace("_", "-"): v for k, v in cab.items()})
    return estado, h, cuerpo


class Espia:
    """Transporte falso. `reglas` es una función (metodo, url, encabezados, cuerpo) -> respuesta
    o una lista de respuestas que se sirven en orden (la última se repite)."""

    def __init__(self, reglas):
        self.reglas = reglas
        self.llamadas = []

    def __call__(self, metodo, url, encabezados, cuerpo=None, tiempo=60):
        self.llamadas.append({"metodo": metodo, "url": url, "encabezados": dict(encabezados),
                              "cuerpo": cuerpo, "ruta": urlsplit(url).path})
        if callable(self.reglas):
            return self.reglas(metodo, url, encabezados, cuerpo)
        r = self.reglas[0] if len(self.reglas) == 1 else self.reglas.pop(0)
        return r

    def rutas(self):
        return [(c["metodo"], c["ruta"]) for c in self.llamadas]

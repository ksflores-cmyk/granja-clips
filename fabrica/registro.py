"""Marcador, registro y contador de tomas."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import raiz_repo

ZONA = ZoneInfo("America/Guatemala")

CABECERA_MARCADOR = [
    "id_clip", "toma", "fecha", "hora", "plataforma", "pilar", "fuente_id", "campana",
    "gancho", "duracion_s", "credito", "url", "vistas_12h", "vistas_24h", "vistas_72h",
    "likes", "compartidos", "comentarios", "guardados", "seguidores", "prueba",
    "estado_campana", "motivo_rechazo", "pago_usd", "notas",
]

CABECERA_ENVIOS = ["fecha", "toma", "plataforma", "url", "campana", "estado", "notas"]


def anotar_fila(raiz, fila: dict) -> None:
    """Añade una fila a marcador.csv. Rechaza claves desconocidas; lo ausente queda vacío."""
    desconocidas = sorted(set(fila) - set(CABECERA_MARCADOR))
    if desconocidas:
        raise ValueError(f"Columnas desconocidas en el marcador: {', '.join(desconocidas)}")
    ruta = raiz_repo(raiz) / "marcador.csv"
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["" if fila.get(c) is None else fila.get(c) for c in CABECERA_MARCADOR])


def anotar_registro(raiz, texto: str) -> str:
    """Añade una línea con fecha y hora de Guatemala a registro.md."""
    sello = datetime.now(ZONA).strftime("%Y-%m-%d %H:%M")
    linea = f"- {sello} GT · {' '.join(str(texto).split())}"
    ruta = raiz_repo(raiz) / "registro.md"
    actual = ruta.read_text(encoding="utf-8") if ruta.exists() else ""
    if actual and not actual.endswith("\n"):
        actual += "\n"
    ruta.write_text(actual + linea + "\n", encoding="utf-8")
    return linea


_LINEA_CONTADOR = re.compile(r"^(\s+contador:\s*)(\d+)(.*)$")


def _localizar_contador(lineas: list[str]) -> int:
    lineas = [l.rstrip("\r") for l in lineas]
    """Índice de la línea `contador:` dentro del bloque `toma:`."""
    dentro = False
    for i, linea in enumerate(lineas):
        if re.match(r"^toma:\s*$", linea):
            dentro = True
            continue
        if dentro:
            if linea.strip() and not linea.startswith((" ", "\t")):
                break
            if _LINEA_CONTADOR.match(linea):
                return i
    raise ValueError("No se encontró toma.contador en marca.yml")


def contador_toma(raiz=None) -> int:
    lineas = (raiz_repo(raiz) / "marca.yml").read_text(encoding="utf-8").splitlines()
    return int(_LINEA_CONTADOR.match(lineas[_localizar_contador(lineas)]).group(2))


def avanzar_toma(raiz=None) -> int:
    """Sube toma.contador en 1 cambiando sólo esa línea. Devuelve el nuevo valor."""
    ruta = raiz_repo(raiz) / "marca.yml"
    # newline="" conserva los fines de línea originales (LF o CRLF).
    with open(ruta, encoding="utf-8", newline="") as f:
        texto = f.read()
    lineas = texto.split("\n")
    i = _localizar_contador(lineas)
    cr = "\r" if lineas[i].endswith("\r") else ""
    m = _LINEA_CONTADOR.match(lineas[i][: len(lineas[i]) - len(cr)])
    nuevo = int(m.group(2)) + 1
    lineas[i] = f"{m.group(1)}{nuevo}{m.group(3)}{cr}"
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lineas))
    return nuevo

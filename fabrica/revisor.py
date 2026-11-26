"""Revisor en la nube para la auto-fusión (decisión de Kevin «Auto-fusión», 25 nov 2026).

Lo llama `.github/workflows/auto-fusion.yml` cuando la puerta de una PR de una rama `claude/*` sale en verde. No hace
checkout del código de la PR: lee la lista de archivos y el diff por la API de GitHub.

Reglas fijas, por código (sin modelo), que mandan a Kevin en vez de fusionar:
- toca `.github/` (workflows con secretos o permisos), `PAUSA`, `retiros.md` o `config/divulgacion.yml`;
- el diff trae algo con forma de clave (Google, GitHub, Supabase, Buffer, bloque PRIVATE KEY);
- borra pruebas sin añadir otras (más de 20 líneas de `pruebas/` borradas y ninguna añadida).

Si pasa eso, un revisor con Gemini (API gratuita, mismos modelos que `config/ojos.yml`) lee el diff y devuelve
{"veredicto": "aprobado" | "rechazado", "motivos": [...]}. Sin respuesta válida de ningún modelo: no se fusiona.

Uso: python -m fabrica.revisor --archivos archivos.json --diff pr.diff --salida veredicto.json
Sale 0 si aprueba, 1 si no (el motivo queda en `veredicto.json`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from .config import cargar_yaml

TOPE_DIFF = 180_000            # caracteres del diff que se le mandan al modelo
PROTEGIDOS = (".github/", "PAUSA", "retiros.md", "config/divulgacion.yml")
CLAVES = [re.compile(p) for p in (
    r"AIza[0-9A-Za-z_\-]{30,}",                    # Google
    r"gh[pousr]_[0-9A-Za-z]{30,}",                 # GitHub
    r"github_pat_[0-9A-Za-z_]{40,}",
    r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}",   # JWT (Supabase service key)
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

INSTRUCCION = """Eres el revisor de código de la Granja de Clips (repo público granja-clips: fábrica de clips con
GitHub Actions, Gemini, Buffer y Supabase). Revisa este diff de una PR que se fusionará SOLA si apruebas.
Rechaza si ves cualquiera de estas cosas:
1. Un error que rompa la producción (excepción segura, lógica invertida, archivo o función que falta).
2. Algo que exponga datos o la sesión de Kevin en un repo público (logs, artefactos, commits con enlaces privados).
3. Que se salte o debilite una regla de seguridad del proyecto: lista blanca de fuentes, PAUSA, retiros, vetos,
   divulgación de campañas, topes de gasto, cupos de publicación.
4. Pruebas borradas o debilitadas para que pase la puerta.
Los detalles de estilo o de nombres NO son motivo de rechazo. Responde sólo JSON:
{"veredicto": "aprobado" o "rechazado", "motivos": ["..."]} (motivos vacío si apruebas; en español).
"""


def reglas_fijas(archivos: list[dict], diff: str) -> list[str]:
    """Motivos por código para no fusionar sin Kevin (lista vacía si no hay)."""
    m = []
    for a in archivos:
        nombre = str(a.get("filename", ""))
        if any(nombre == p or nombre.startswith(p) for p in PROTEGIDOS):
            m.append(f"toca {nombre}: lo fusiona Kevin")
    for patron in CLAVES:
        if patron.search(diff):
            m.append("el diff trae algo con forma de clave")
            break
    borradas = sum(int(a.get("deletions") or 0) for a in archivos if str(a.get("filename", "")).startswith("pruebas/"))
    nuevas = sum(int(a.get("additions") or 0) for a in archivos if str(a.get("filename", "")).startswith("pruebas/"))
    if borradas > 20 and nuevas == 0:
        m.append(f"borra {borradas} líneas de pruebas sin añadir ninguna")
    return m


def _modelos(raiz=None) -> list[str]:
    cfg = cargar_yaml("config/ojos.yml", raiz) or {}
    return [m for m in [cfg.get("modelo"), *(cfg.get("modelos_respaldo") or [])] if isinstance(m, str) and m]


def leer_veredicto(texto: str) -> dict | None:
    """El JSON del modelo, o None si no trae un veredicto válido."""
    try:
        d = json.loads(texto)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", texto or "", re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return None
    if not isinstance(d, dict) or d.get("veredicto") not in ("aprobado", "rechazado"):
        return None
    motivos = d.get("motivos")
    d["motivos"] = [str(x) for x in motivos] if isinstance(motivos, list) else []
    return d


def preguntar(diff: str, clave: str, modelos: list[str], abrir=urllib.request.urlopen) -> tuple[dict | None, list[str]]:
    """(veredicto, notas). Prueba los modelos en orden hasta un veredicto válido."""
    notas = []
    cuerpo = {"contents": [{"parts": [{"text": INSTRUCCION + "\n\nDIFF:\n" + diff[:TOPE_DIFF]}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    for modelo in modelos:
        req = urllib.request.Request(URL.format(modelo=modelo), data=json.dumps(cuerpo).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": clave})
        try:
            with abrir(req, timeout=90) as r:
                datos = json.loads(r.read().decode("utf-8"))
            texto = datos["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:  # noqa: BLE001 — se prueba el siguiente modelo
            notas.append(f"{modelo}: {type(e).__name__}")
            continue
        v = leer_veredicto(texto)
        if v is not None:
            v["modelo"] = modelo
            return v, notas
        notas.append(f"{modelo}: respuesta sin veredicto")
    return None, notas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archivos", required=True)
    ap.add_argument("--diff", required=True)
    ap.add_argument("--salida", required=True)
    a = ap.parse_args(argv)
    archivos = json.loads(Path(a.archivos).read_text(encoding="utf-8"))
    diff = Path(a.diff).read_text(encoding="utf-8", errors="replace")
    fijos = reglas_fijas(archivos, diff)
    if fijos:
        out = {"veredicto": "kevin", "motivos": fijos}
    else:
        clave = os.environ.get("GEMINI_API_KEY", "")
        v, notas = preguntar(diff, clave, _modelos()) if clave else (None, ["sin GEMINI_API_KEY"])
        out = v or {"veredicto": "sin_revisor", "motivos": ["ningún modelo dio un veredicto: " + "; ".join(notas)]}
    Path(a.salida).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("veredicto") == "aprobado" else 1


if __name__ == "__main__":
    sys.exit(main())

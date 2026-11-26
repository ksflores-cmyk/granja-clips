"""Lector de briefs de Content Rewards con la sesión de Kevin (#23). No envía nada ni se une a campañas.

La página pública de una campaña oculta las reglas completas y los «Reference materials» («Visible once you
join»). Con la sesión del secreto `CR_SESION` (la misma del robot de envío, nunca se imprime) este lector abre
cada campaña, guarda su texto, una captura y los enlaces útiles (Google Docs, Drive, YouTube, Dropbox…), y
baja el texto de los Google Docs públicos del brief. Con eso se completa la ficha y se copia el material
oficial a un release privado.

Uso (workflow `brief-cr`):
  python -m fabrica.brief_cr --campana ID [--campana ID ...] --salida DIR
Sale 0 si leyó todas, 1 si alguna falló y 3 si la sesión venció.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .enviar_cr import BASE, SALIDA_SESION, SesionVencida, cargar_sesion

DOMINIOS = ("docs.google.com", "drive.google.com", "drive.usercontent.google.com", "youtube.com", "youtu.be",
            "dropbox.com", "frame.io", "wetransfer.com", "we.tl", "mega.nz", "box.com", "reachclipping.com",
            "twitch.tv", "kick.com")
RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RE_DOC = re.compile(r"^https://docs\.google\.com/document/d/([\w-]{20,})")
TOPE_DOC = 400_000          # bytes de texto por documento


def enlaces_utiles(hrefs) -> list[str]:
    """Enlaces de material o brief, sin repetir y en orden; quita la navegación del propio sitio."""
    out, vistos = [], set()
    for h in hrefs or []:
        if not isinstance(h, str) or not h.startswith("https://"):
            continue
        host = (urlparse(h).hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in DOMINIOS):
            continue
        if h not in vistos:
            vistos.add(h)
            out.append(h)
    return out


def url_texto_doc(url: str) -> str | None:
    """`export?format=txt` de un Google Doc (sólo funciona si el documento es público)."""
    m = RE_DOC.match(url or "")
    return f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt" if m else None


def bajar_doc(url: str, abrir=urllib.request.urlopen) -> str:
    with abrir(urllib.request.Request(url, headers={"User-Agent": "granja-clips"}), timeout=30) as r:
        tipo = r.headers.get("Content-Type", "")
        if "text/plain" not in tipo:
            raise ValueError(f"no es texto ({tipo or 'sin tipo'}): el documento no es público")
        return r.read(TOPE_DOC).decode("utf-8", "replace")


def leer(page, campaign_id: str, salida: Path, abrir=urllib.request.urlopen, texto_pagina: bool = False) -> dict:
    page.goto(BASE + campaign_id, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    if re.search(r"/(login|sign-?in|auth)", page.url) or "whop.com/login" in page.url:
        raise SesionVencida(f"redirigido a {page.url.split('?')[0]}")
    cuerpo = page.inner_text("body")
    # «Join to unlock» puede quedar en una parte (p. ej. un requisito) aun estando unido: se anota y se sigue.
    bloqueada = bool(re.search(r"Join to unlock|Visible once you join", cuerpo, re.I))
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    # Repo público (21 nov): ni captura ni texto completo de la página (pueden traer datos de la cuenta de Kevin).
    # Al artefacto sólo van los enlaces útiles y el texto de los Google Docs públicos del brief.
    enlaces = enlaces_utiles(hrefs)
    if texto_pagina:           # sólo con --texto-pagina, y el workflow lo cifra antes de subirlo (nunca en claro)
        (salida / f"{campaign_id}-pagina.txt").write_text(cuerpo[:TOPE_DOC], encoding="utf-8")
        (salida / f"{campaign_id}-enlaces.txt").write_text("\n".join(enlaces) + "\n", encoding="utf-8")
    docs = {}
    for u in enlaces:
        ex = url_texto_doc(u)
        if not ex:
            continue
        nombre = f"{campaign_id}-doc-{len(docs) + 1}.txt"
        try:
            (salida / nombre).write_text(bajar_doc(ex, abrir), encoding="utf-8")
            docs[u] = nombre
        except Exception as e:  # noqa: BLE001
            docs[u] = f"error: {type(e).__name__}: {str(e)[:120]}"
    titulo = (page.title() or "").strip()     # el título de la pestaña, no la primera línea (puede ser la cuenta)
    return {"cr_campaign_id": campaign_id, "titulo": titulo, "bloqueada": bloqueada, "pausada": bool(
        re.search(r"Pausada por la marca|Paused by the brand", cuerpo, re.I)), "enlaces": enlaces, "docs": docs}


def _resumen_paso(r: dict) -> None:
    """Una fila en el resumen del paso de Actions (sin abrir el artefacto)."""
    ruta = os.environ.get("GITHUB_STEP_SUMMARY")
    if not ruta:
        return
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(f"| {r['cr_campaign_id']} | {r['titulo'][:60]} | {len(r['enlaces'])} | {len(r['docs'])} | "
                f"{'sí' if r['pausada'] else 'no'} | {'sí' if r['bloqueada'] else 'no'} |\n")


def principal(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fabrica.brief_cr")
    ap.add_argument("--campana", action="append", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--texto-pagina", action="store_true",
                    help="guarda también el texto de la página y los enlaces (sólo si el workflow cifra la salida)")
    a = ap.parse_args(argv)
    ids = [c.strip().lower() for c in a.campana if c.strip()]
    malos = [c for c in ids if not RE_UUID.match(c)]
    if malos or not ids:
        print(f"cr_campaign_id inválido: {', '.join(malos) or '(ninguno)'}")
        return 2
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    try:
        sesion = cargar_sesion()
    except SesionVencida as e:
        print(f"sesión: {e}")
        return SALIDA_SESION
    from playwright.sync_api import sync_playwright  # sólo aquí: las pruebas no necesitan Playwright
    codigo, resumen = 0, []
    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True)
        ctx = nav.new_context(storage_state=sesion, locale="es-ES", viewport={"width": 1280, "height": 900})
        try:
            for cid in ids:
                page = ctx.new_page()
                try:
                    r = leer(page, cid, salida, texto_pagina=a.texto_pagina)
                    resumen.append(r)
                    print(f"{cid}: {r['titulo'][:80]} — {len(r['enlaces'])} enlaces, {len(r['docs'])} documentos"
                          + (" — PAUSADA" if r["pausada"] else "") + (" — BLOQUEADA" if r["bloqueada"] else ""))
                    _resumen_paso(r)
                except SesionVencida as e:
                    print(f"{cid}: sesión vencida ({e})")
                    return SALIDA_SESION
                except Exception as e:  # noqa: BLE001
                    codigo = 1
                    resumen.append({"cr_campaign_id": cid, "error": f"{type(e).__name__}: {str(e)[:160]}"})
                    print(f"{cid}: error {type(e).__name__}")
                finally:
                    page.close()
        finally:
            (salida / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
            ctx.close()
            nav.close()
    return codigo


if __name__ == "__main__":
    sys.exit(principal())

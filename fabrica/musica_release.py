"""Sube al release privado `musica` las pistas de `marca/musica/pistas.yml`, comprobando su licencia.

Corre sólo dentro de GitHub Actions (workflow `musica.yml`): la sesión de Claude no llega a los sitios
de origen. Para cada pista no descartada:

1. Lee la página de origen (`pagina`) y saca el campo "License(s):", el autor y los enlaces de "File(s):".
2. Si la licencia leída no está en `licencias_permitidas` (CC0), la pista se rechaza.
3. Baja `archivo` (tope de bytes y de tiempo); si es .zip, extrae el primer miembro que termine en
   `zip_miembro`. Comprueba con ffprobe que es audio y que dura al menos `DURACION_MIN_S`.
4. Calcula el sha256 del asset; si `pistas.yml` ya trae uno distinto, la pista se rechaza.
5. Escribe el asset en `--salida` y anota la lectura (licencia, autor, enlace, fecha) en `lecturas.json`.

Sube los assets y `lecturas.json` con `gh release upload --clobber` sólo si `--subir`.
Sale con 1 si quedan menos de `MINIMO` pistas verificadas. Nunca escribe en el repo.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
MANIFIESTO = RAIZ / "marca" / "musica" / "pistas.yml"
MINIMO = 6
DURACION_MIN_S = 12.0
TOPE_BYTES = 60 * 1024 * 1024
TIEMPO_S = 60
AGENTE = "granja-clips-musica/1.0 (+https://github.com/ksflores-cmyk/granja-clips)"


def _bajar(url: str, tope: int = TOPE_BYTES) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=TIEMPO_S) as r:
        datos = r.read(tope + 1)
    if len(datos) > tope:
        raise ValueError(f"{url}: pasa de {tope} bytes")
    return datos


def _texto(fragmento: str) -> str:
    sin = re.sub(r"<[^>]+>", " ", fragmento)
    return re.sub(r"\s+", " ", html.unescape(sin)).strip()


def _bloque(pagina: str, campo: str) -> str:
    """El HTML del campo Drupal `field-name-<campo>` hasta el siguiente campo."""
    i = pagina.find(f"field-name-{campo}")
    if i < 0:
        return ""
    j = pagina.find("field-name-", i + len(campo) + 11)
    return pagina[i: j if j > 0 else i + 6000]


def leer_pagina(pagina: str, base: str) -> dict:
    """Licencias, autor y enlaces de archivos de una página de OpenGameArt (función pura)."""
    bl = _bloque(pagina, "field-art-licenses")
    nombres = re.findall(r"class=['\"]license-name['\"][^>]*>([^<]+)<", bl)
    licencias = sorted({html.unescape(n).strip() for n in nombres if n.strip()})
    if not licencias and "publicdomain/zero" in bl:
        licencias = ["CC0"]
    autor_bl = _bloque(pagina, "author-submitter")
    autor = _texto(autor_bl.split(">", 1)[1] if ">" in autor_bl else autor_bl)
    autor = re.sub(r"^.*?Author:\s*", "", autor).strip()
    archivos = {}
    for href in re.findall(r"href=['\"]([^'\"]*/sites/default/files/[^'\"]+)['\"]", _bloque(pagina, "field-art-files")):
        url = urllib.parse.urljoin(base, html.unescape(href))
        archivos[urllib.parse.unquote(url.rsplit("/", 1)[-1])] = url
    return {"licencias": licencias, "autor": autor, "archivos": archivos,
            "texto_licencia": _texto(bl)[:200]}


def _duracion(ruta: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "format=duration", "-of", "default=nw=1:nk=1", str(ruta)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or not out.stdout.strip():
        raise ValueError(f"ffprobe no lee audio en {ruta.name}: {out.stderr.strip()[:200]}")
    return float(out.stdout.strip())


def procesar(p: dict, permitidas: set[str], salida: Path) -> dict:
    lectura = {"id": p["id"], "pagina": p["pagina"], "leido_el": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    pag = _bajar(p["pagina"], 5 * 1024 * 1024).decode("utf-8", "replace")
    info = leer_pagina(pag, p["pagina"])
    lectura.update(licencias_leidas=info["licencias"], autor_leido=info["autor"],
                   texto_licencia=info["texto_licencia"])
    if not info["licencias"] or not set(info["licencias"]) & permitidas:
        raise ValueError(f"licencia leída {info['licencias'] or 'ninguna'}; se exige una de {sorted(permitidas)}")
    url = info["archivos"].get(p["archivo"])
    if not url:
        raise ValueError(f"'{p['archivo']}' no está en File(s): {sorted(info['archivos'])}")
    lectura["archivo_url"] = url
    datos = _bajar(url)
    lectura["sha256_origen"] = hashlib.sha256(datos).hexdigest()
    if p.get("zip_miembro"):
        tmp = salida / f".{p['id']}.zip"
        tmp.write_bytes(datos)
        with zipfile.ZipFile(tmp) as z:
            miembros = [n for n in z.namelist() if n.lower().endswith(p["zip_miembro"].lower())
                        and not n.startswith("__MACOSX")]
            if not miembros:
                raise ValueError(f"el zip no trae ningún *{p['zip_miembro']}: {z.namelist()}")
            lectura["zip_miembro"] = miembros[0]
            datos = z.read(miembros[0])
        tmp.unlink()
    destino = salida / p["asset"]
    destino.write_bytes(datos)
    dur = _duracion(destino)
    if dur < DURACION_MIN_S:
        raise ValueError(f"dura {dur:.1f} s (mínimo {DURACION_MIN_S:g})")
    lectura["duracion_s"] = round(dur, 2)
    lectura["sha256"] = hashlib.sha256(datos).hexdigest()
    if p.get("sha256") and p["sha256"] != lectura["sha256"]:
        destino.unlink()
        raise ValueError(f"sha256 {lectura['sha256']} distinto del anotado {p['sha256']}")
    lectura["asset"] = p["asset"]
    return lectura


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.musica_release")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--subir", action="store_true", help="sube los assets al release (gh)")
    a = ap.parse_args(argv)
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    datos = yaml.safe_load(MANIFIESTO.read_text(encoding="utf-8"))
    permitidas = set(datos["licencias_permitidas"])
    lecturas, rechazos = [], []
    for p in datos["pistas"]:
        if p.get("estado") == "descartada":
            continue
        try:
            lecturas.append(procesar(p, permitidas, salida))
            print(f"OK    {p['id']}: {lecturas[-1]['licencias_leidas']} {lecturas[-1]['duracion_s']} s "
                  f"sha256={lecturas[-1]['sha256']}", flush=True)
        except Exception as e:      # una pista mala no tumba las demás
            rechazos.append({"id": p["id"], "motivo": f"{type(e).__name__}: {e}"})
            print(f"NO    {p['id']}: {type(e).__name__}: {e}", flush=True)
    informe = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "verificadas": lecturas, "rechazadas": rechazos}
    (salida / "lecturas.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(lecturas)} verificadas, {len(rechazos)} rechazadas (mínimo {MINIMO})")
    if len(lecturas) < MINIMO:
        return 1
    if a.subir:
        rel = datos["release"]
        if subprocess.run(["gh", "release", "view", rel], capture_output=True).returncode != 0:
            subprocess.run(["gh", "release", "create", rel, "--title", "Música CC0 de Guion Vivo",
                            "--notes", "Pistas CC0 verificadas por musica.yml. Licencias: marca/musica/<id>/LICENCIA.md.",
                            "--prerelease"], check=True)
        archivos = [str(salida / x["asset"]) for x in lecturas] + [str(salida / "lecturas.json")]
        subprocess.run(["gh", "release", "upload", rel, *archivos, "--clobber"], check=True, timeout=600)
    return 0


if __name__ == "__main__":
    sys.exit(main())

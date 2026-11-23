"""Sube a un release privado las pistas (`musica`) o efectos (`sfx`) de un catálogo, comprobando su licencia.

Por defecto lee `marca/musica/pistas.yml` y sube al release de su clave `release`; con `--manifiesto` y
`--release` sirve a otros catálogos (`marca/sfx/efectos.yml`). La lista puede llamarse `pistas` o `efectos`.
Claves opcionales del catálogo: `duracion_min_s` (12), `duracion_max_s` (sin máximo), `minimo` (6).
Varias entradas pueden salir del mismo zip: se baja una sola vez por URL.

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
import io
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
DURACION_MAX_S = None
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
    return re.sub(r"<[^>]*$", "", pagina[i: j if j > 0 else i + 6000])


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
    # Drupal guarda el archivo con sufijo (_0, _1...) si el nombre ya existía: vale el nombre del enlace
    # tal como lo muestra "File(s):" o el del archivo guardado.
    patron = r"<a\s[^>]*href=['\"]([^'\"]*/sites/default/files/[^'\"]+)['\"][^>]*>([^<]*)</a>"
    for href, texto in re.findall(patron, _bloque(pagina, "field-art-files")):
        url = urllib.parse.urljoin(base, html.unescape(href))
        if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(base).netloc:
            continue            # sólo archivos del mismo sitio que la página leída
        archivos[urllib.parse.unquote(url.rsplit("/", 1)[-1])] = url
        if html.unescape(texto).strip():
            archivos.setdefault(html.unescape(texto).strip(), url)
    return {"licencias": licencias, "autor": autor, "archivos": archivos,
            "texto_licencia": _texto(bl)[:200]}


def _duracion(ruta: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "format=duration", "-of", "default=nw=1:nk=1", str(ruta)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or not out.stdout.strip():
        raise ValueError(f"ffprobe no lee audio en {ruta.name}: {out.stderr.strip()[:200]}")
    return float(out.stdout.strip())


def _miembro_zip(nombres: list[str], pedido: str) -> str | None:
    """Prefiere el miembro con ese nombre exacto (ruta o nombre de archivo); si no, el primero que termine así."""
    validos = [n for n in nombres if not n.startswith("__MACOSX") and not n.endswith("/")]
    pl = pedido.lower().replace("\\", "/")
    for n in validos:
        nl = n.lower()
        if nl == pl or nl.rsplit("/", 1)[-1] == pl.rsplit("/", 1)[-1] and nl.endswith(pl):
            return n
    for n in validos:
        if n.lower().endswith(pl):
            return n
    return None


def procesar(p: dict, permitidas: set[str], salida: Path, dur_min: float = DURACION_MIN_S,
             dur_max: float | None = DURACION_MAX_S, cache: dict | None = None) -> dict:
    cache = cache if cache is not None else {}
    lectura = {"id": p["id"], "pagina": p["pagina"], "leido_el": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if ("pag", p["pagina"]) not in cache:
        cache[("pag", p["pagina"])] = _bajar(p["pagina"], 5 * 1024 * 1024).decode("utf-8", "replace")
    info = leer_pagina(cache[("pag", p["pagina"])], p["pagina"])
    lectura.update(licencias_leidas=info["licencias"], autor_leido=info["autor"],
                   texto_licencia=info["texto_licencia"])
    if not info["licencias"] or not set(info["licencias"]) & permitidas:
        raise ValueError(f"licencia leída {info['licencias'] or 'ninguna'}; se exige una de {sorted(permitidas)}")
    url = info["archivos"].get(p["archivo"])
    if not url:
        raise ValueError(f"'{p['archivo']}' no está en File(s): {sorted(info['archivos'])}")
    lectura["archivo_url"] = url
    if ("url", url) not in cache:
        cache[("url", url)] = _bajar(url)
    datos = cache[("url", url)]
    lectura["sha256_origen"] = hashlib.sha256(datos).hexdigest()
    if p.get("zip_miembro"):
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            miembro = _miembro_zip(z.namelist(), p["zip_miembro"])
            if not miembro:
                raise ValueError(f"el zip no trae ningún *{p['zip_miembro']}; miembros del zip "
                                 f"({len(z.namelist())}): {z.namelist()}")
            if z.getinfo(miembro).file_size > TOPE_BYTES:
                raise ValueError(f"{miembro} descomprimido pasa de {TOPE_BYTES} bytes")
            lectura["zip_miembro"] = miembro
            with z.open(miembro) as f:
                datos = f.read(TOPE_BYTES + 1)
            if len(datos) > TOPE_BYTES:
                raise ValueError(f"{miembro} descomprimido pasa de {TOPE_BYTES} bytes")
    destino = salida / p["asset"]
    destino.write_bytes(datos)
    dur = _duracion(destino)
    if dur < dur_min or (dur_max is not None and dur > dur_max):
        destino.unlink()
        raise ValueError(f"dura {dur:.2f} s (mínimo {dur_min:g}"
                         + (f", máximo {dur_max:g}" if dur_max is not None else "") + ")")
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
    ap.add_argument("--manifiesto", default=str(MANIFIESTO), help="catálogo YAML (por defecto, el de música)")
    ap.add_argument("--release", default=None, help="release destino (por defecto, la clave `release` del catálogo)")
    ap.add_argument("--subir", action="store_true", help="sube los assets al release (gh)")
    a = ap.parse_args(argv)
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    datos = yaml.safe_load(Path(a.manifiesto).read_text(encoding="utf-8"))
    permitidas = set(datos["licencias_permitidas"])
    dur_min = float(datos.get("duracion_min_s", DURACION_MIN_S))
    dur_max = datos.get("duracion_max_s", DURACION_MAX_S)
    dur_max = float(dur_max) if dur_max is not None else None
    minimo = int(datos.get("minimo", MINIMO))
    lista = datos.get("pistas") if datos.get("pistas") is not None else datos.get("efectos") or []
    release = a.release or datos["release"]
    lecturas, rechazos, cache = [], [], {}
    for p in lista:
        if p.get("estado") == "descartada":
            continue
        try:
            lecturas.append(procesar(p, permitidas, salida, dur_min, dur_max, cache))
            print(f"OK    {p['id']}: {lecturas[-1]['licencias_leidas']} {lecturas[-1]['duracion_s']} s "
                  f"{lecturas[-1].get('zip_miembro', '')} sha256={lecturas[-1]['sha256']}", flush=True)
        except Exception as e:      # una pista mala no tumba las demás
            rechazos.append({"id": p["id"], "motivo": f"{type(e).__name__}: {e}"})
            print(f"NO    {p['id']}: {type(e).__name__}: {e}", flush=True)
    informe = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "verificadas": lecturas, "rechazadas": rechazos}
    (salida / "lecturas.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(lecturas)} verificadas, {len(rechazos)} rechazadas (mínimo {minimo})")
    if len(lecturas) < minimo:
        return 1
    if a.subir:
        rel = release
        if subprocess.run(["gh", "release", "view", rel], capture_output=True).returncode != 0:
            titulo = "Música CC0 de Guion Vivo" if rel == "musica" else f"{rel} CC0 de Guion Vivo"
            subprocess.run(["gh", "release", "create", rel, "--title", titulo,
                            "--notes", f"Audio CC0 verificado por el workflow {rel}.yml. Licencias: LICENCIA.md de cada id en marca/.",
                            "--prerelease"], check=True)
        archivos = [str(salida / x["asset"]) for x in lecturas] + [str(salida / "lecturas.json")]
        subprocess.run(["gh", "release", "upload", rel, *archivos, "--clobber"], check=True, timeout=600)
    return 0


if __name__ == "__main__":
    sys.exit(main())

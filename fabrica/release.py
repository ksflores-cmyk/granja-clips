"""Material de campaña en un release privado de este repo (adiós a la cuota de Google Drive).

Un workflow baja UNA vez los videos de Drive y los sube al release `fuentes-fm27`; la fábrica los
descarga de ahí con `GITHUB_TOKEN`. Este módulo concentra todo para no tocar `descargar.py` más que
con un desvío:

- `es_url_release` / `partes`: la URL `https://github.com/<dueño>/<repo>/releases/download/<tag>/<nombre>`.
- `resolver`: pide al API de GitHub la URL firmada temporal del asset (token sólo hacia api.github.com).
- `bajar`: baja esa URL firmada, SIN token (es otro host), con tope de bytes y de tiempo.
- CLI `python -m fabrica.release faltan|subir|verificar --ficha ID --tag TAG`.

El token nunca se imprime ni entra en un mensaje de error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import descargar, fuentes
from .config import raiz_repo
from .descargar import ErrorDescarga

HOSTS_FIRMADOS = {"objects.githubusercontent.com", "release-assets.githubusercontent.com",
                  "github-releases.githubusercontent.com"}
API = "https://api.github.com"
TIEMPO_API_S = 30
TOPE_TOTAL_S = 600
RE_SEGMENTO = re.compile(r"[A-Za-z0-9._-]+")
RE_REPO = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
TIPOS_OK = ("application/octet-stream",)


# ---------------------------------------------------------------- URL del release

def partes(url) -> tuple[str, str, str]:
    """(`dueño/repo`, tag, nombre) de una URL de release; ErrorDescarga si no tiene la forma exacta."""
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        raise ErrorDescarga("no es una URL de release de github.com")
    p = urlparse(url)
    if p.query or p.fragment or p.params or p.username or p.password or p.port or p.hostname != "github.com":
        raise ErrorDescarga("la URL del release no admite query, fragmento ni credenciales")
    seg = p.path.split("/")
    # ['', dueño, repo, 'releases', 'download', tag, nombre]
    if len(seg) != 7 or seg[0] != "" or seg[3] != "releases" or seg[4] != "download":
        raise ErrorDescarga("la URL no tiene la forma /dueño/repo/releases/download/tag/nombre")
    for s in (seg[1], seg[2], seg[5], seg[6]):
        if not RE_SEGMENTO.fullmatch(s) or ".." in s:
            raise ErrorDescarga("la URL del release trae caracteres no permitidos")
    return f"{seg[1]}/{seg[2]}", seg[5], seg[6]


def es_url_release(url) -> bool:
    try:
        partes(url)
    except ErrorDescarga:
        return False
    return True


def token(entorno=None) -> str | None:
    e = os.environ if entorno is None else entorno
    return e.get("GITHUB_TOKEN") or e.get("GH_TOKEN") or None


def _sin_token(texto: str, tok: str | None) -> str:
    return texto.replace(tok, "***") if tok else texto


# ---------------------------------------------------------------- API de GitHub

def _transporte_urllib(metodo: str, url: str, encabezados: dict):
    """(estado, encabezados, cuerpo). No sigue redirecciones: la Location se devuelve tal cual."""
    req = urllib.request.Request(url, headers=encabezados, method=metodo)
    try:
        with descargar._opener().open(req, timeout=TIEMPO_API_S) as r:   # noqa: S310 (https comprobado)
            return r.status, dict(r.headers), r.read(4_000_000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(4_000_000)
    except OSError as e:
        raise ErrorDescarga(f"fallo de conexión con api.github.com: {type(e).__name__}") from None


def _encabezados(tok: str, aceptar: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Accept": aceptar, "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "granja-clips-release"}


def _comprobar_repo(repo: str, entorno) -> str:
    e = os.environ if entorno is None else entorno
    actual = e.get("GITHUB_REPOSITORY")
    if actual and actual != repo:
        raise ErrorDescarga(f"el release es de {repo} y este repo es {actual}: no se usa el token con otro repo")
    tok = token(entorno)
    if not tok:
        raise ErrorDescarga("sin GITHUB_TOKEN: no se puede bajar del release (el repo es privado)")
    return tok


def _release(repo: str, tag: str, tok: str, transporte) -> dict | None:
    """El release por tag, o None si no existe."""
    estado, _, cuerpo = transporte("GET", f"{API}/repos/{repo}/releases/tags/{tag}",
                                   _encabezados(tok, "application/vnd.github+json"))
    if estado == 404:
        return None
    if estado != 200:
        raise ErrorDescarga(f"api.github.com respondió HTTP {estado} al pedir el release {tag}")
    try:
        datos = json.loads(cuerpo.decode("utf-8"))
    except ValueError:
        raise ErrorDescarga("api.github.com devolvió algo que no es JSON") from None
    if not isinstance(datos, dict):
        raise ErrorDescarga("api.github.com devolvió un release ilegible")
    return datos


def _assets(release: dict) -> dict[str, dict]:
    """Assets ya subidos (state == uploaded) por nombre."""
    return {a["name"]: a for a in release.get("assets") or []
            if isinstance(a, dict) and a.get("state") == "uploaded" and isinstance(a.get("name"), str)}


def assets_subidos(repo: str, tag: str, *, entorno=None, transporte=None) -> set[str] | None:
    """Nombres de los assets subidos del release; None si el release aún no existe."""
    tok = _comprobar_repo(repo, entorno)
    r = _release(repo, tag, tok, transporte or _transporte_urllib)
    return None if r is None else set(_assets(r))


@dataclass
class Asset:
    url_firmada: str
    bytes: int
    nombre: str


def resolver(url: str, *, entorno=None, transporte=None) -> Asset:
    """URL firmada temporal del asset. El token sólo viaja a api.github.com."""
    repo, tag, nombre = partes(url)
    tok = _comprobar_repo(repo, entorno)
    transporte = transporte or _transporte_urllib
    rel = _release(repo, tag, tok, transporte)
    if rel is None:
        raise ErrorDescarga(f"el release {tag} aún no existe")
    a = _assets(rel).get(nombre)
    if a is None or not isinstance(a.get("id"), int) or isinstance(a.get("id"), bool):
        raise ErrorDescarga(f"{nombre} aún no está en el release {tag}")
    estado, enc, _ = transporte("GET", f"{API}/repos/{repo}/releases/assets/{a['id']}",
                                _encabezados(tok, "application/octet-stream"))
    if estado not in (301, 302, 307):
        raise ErrorDescarga(f"api.github.com respondió HTTP {estado} al pedir {nombre} (se esperaba una redirección)")
    loc = {k.lower(): v for k, v in enc.items()}.get("location", "")
    p = urlparse(loc)
    if p.scheme != "https" or (p.hostname or "").lower() not in HOSTS_FIRMADOS:
        raise ErrorDescarga(f"la redirección de {nombre} apunta a un host no permitido ({p.hostname or 'ninguno'})")
    tam = a.get("size")
    if isinstance(tam, bool) or not isinstance(tam, int) or tam <= 0:
        raise ErrorDescarga(f"{nombre} no trae un tamaño válido en el release")
    return Asset(loc, tam, nombre)


def bajar(url: str, destino: Path, *, entorno=None, transporte=None, abrir=None,
          tope: int = descargar.TOPE_BYTES, tope_s: float = TOPE_TOTAL_S) -> Path:
    """Baja el asset del release a `destino`. Cualquier fallo borra `destino` y lanza ErrorDescarga."""
    destino = Path(destino)
    tok = token(entorno)
    try:
        asset = resolver(url, entorno=entorno, transporte=transporte)
        if asset.bytes > tope:
            raise ErrorDescarga(f"{asset.nombre} pesa {asset.bytes} bytes y el tope es {tope}")
        abrir = abrir or descargar._abrir_urllib      # sin Authorization: la URL firmada es de otro host
        limite = time.monotonic() + tope_s
        total = 0
        with abrir(asset.url_firmada) as r:
            tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not (tipo.startswith("video/") or tipo in TIPOS_OK):
                raise ErrorDescarga(f"Content-Type {tipo or 'ausente'!r}: no es video")
            with open(destino, "wb") as f:
                while True:
                    bloque = r.read(descargar.BLOQUE)
                    if not bloque:
                        break
                    total += len(bloque)
                    if total > tope:
                        raise ErrorDescarga(f"la descarga pasó el tope de {tope} bytes")
                    if time.monotonic() > limite:
                        raise ErrorDescarga(f"la descarga pasó el tope de {tope_s:.0f} s")
                    f.write(bloque)
        if total != asset.bytes:
            raise ErrorDescarga(f"{asset.nombre}: bajaron {total} bytes y el release dice {asset.bytes}")
    except ErrorDescarga as e:
        destino.unlink(missing_ok=True)
        raise ErrorDescarga(_sin_token(str(e), tok)) from None
    except urllib.error.HTTPError as e:
        destino.unlink(missing_ok=True)
        raise ErrorDescarga(f"HTTP {e.code} al bajar del release") from None
    except OSError as e:
        destino.unlink(missing_ok=True)
        raise ErrorDescarga(f"fallo de conexión: {type(e).__name__}") from None
    return destino


# ---------------------------------------------------------------- CLI

def _ahora_s() -> float:
    return time.monotonic()


def _entradas(ficha_id: str, tag: str, raiz, *, con_drive: bool) -> list[dict]:
    """Entradas de `material` de la ficha cuya url es de release con ese tag (y con drive_url si se pide)."""
    ficha = next((f for f in fuentes.cargar_fuentes(raiz) if f.get("id") == ficha_id), None)
    if ficha is None:
        raise ErrorDescarga(f"la ficha {ficha_id} no existe en fuentes.yml")
    salida = []
    for m in ficha.get("material") or []:
        if not isinstance(m, dict) or not es_url_release(m.get("url")) or partes(m["url"])[1] != tag:
            continue
        if con_drive and not m.get("drive_url"):
            continue
        salida.append(m)
    return salida


def _rotar(lista: list, desplazamiento: int) -> list:
    if not lista:
        return []
    d = desplazamiento % len(lista)
    return lista[d:] + lista[:d]


def _desplazamiento(entorno, ahora=None) -> int:
    try:
        return int(entorno["GITHUB_RUN_NUMBER"])
    except (KeyError, ValueError):
        return (ahora or datetime.now(timezone.utc)).hour


def _ejecutor_gh(orden: list[str]):
    return subprocess.run(orden, capture_output=True, text=True, timeout=1800)


def _gh(ejecutor, orden: list[str], tok: str | None) -> str | None:
    """None si salió bien; si no, el motivo (sin token)."""
    try:
        r = ejecutor(orden)
    except (OSError, subprocess.SubprocessError) as e:
        return f"gh no corrió ({type(e).__name__})"
    if r.returncode != 0:
        linea = (getattr(r, "stderr", "") or "").strip().splitlines()
        return _sin_token(f"gh salió con código {r.returncode}" + (f": {linea[-1][:200]}" if linea else ""), tok)
    return None


def _sondear_duracion(ruta: Path) -> float:
    from .editar.componer import sondear_video
    return float(sondear_video(ruta)["duracion"])


def _escribir(entorno, clave: str, texto: str) -> None:
    ruta = entorno.get(clave)
    if ruta:
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(texto)


def orden_faltan(ficha_id, tag, *, raiz=None, entorno=None, transporte=None) -> int:
    t0 = _ahora_s()
    entradas = _entradas(ficha_id, tag, raiz_repo(raiz), con_drive=False)
    if not entradas:
        print(f"la ficha {ficha_id} no tiene material en el release {tag}", flush=True)
        return 1
    repo = partes(entradas[0]["url"])[0]
    subidos = assets_subidos(repo, tag, entorno=entorno, transporte=transporte) or set()
    faltan = [partes(m["url"])[2] for m in entradas if partes(m["url"])[2] not in subidos]
    print(f"release {tag}: faltan {len(faltan)} de {len(entradas)}: {', '.join(faltan) or 'ninguno'} "
          f"({_ahora_s() - t0:.1f} s)", flush=True)
    return 0


def orden_subir(ficha_id, tag, *, raiz=None, entorno=None, transporte=None, ejecutor=None,
                bajar_drive=None, sondear=None, carpeta=None, ahora=None) -> int:
    entorno = os.environ if entorno is None else entorno
    ejecutor = ejecutor or _ejecutor_gh
    bajar_drive = bajar_drive or descargar.bajar_https
    sondear = sondear or _sondear_duracion
    tok = token(entorno)
    entradas = _entradas(ficha_id, tag, raiz_repo(raiz), con_drive=True)
    if not entradas:
        print(f"::error::la ficha {ficha_id} no tiene material con drive_url en el release {tag}", flush=True)
        return 1
    repo = partes(entradas[0]["url"])[0]
    subidos = assets_subidos(repo, tag, entorno=entorno, transporte=transporte)
    existe = subidos is not None
    subidos = set(subidos or ())
    total = len(entradas)
    filas: list[tuple[str, str, str, str]] = []
    hay_error_gh = False
    for m in entradas:
        n = partes(m["url"])[2]
        if n in subidos:
            filas.append((n, "ya estaba", "", ""))
    pendientes = [m for m in _rotar(entradas, _desplazamiento(entorno, ahora))
                  if partes(m["url"])[2] not in subidos]
    print(f"release {tag}: {len(subidos)} de {total} ya subidos; faltan {len(pendientes)}", flush=True)

    if pendientes and not existe:
        t0 = _ahora_s()
        motivo = _gh(ejecutor, ["gh", "release", "create", tag, "--repo", repo, "--title",
                                "Fuentes FM27 (material oficial del brief)", "--notes",
                                "Copia única del material oficial de la campaña SEGA FM27 (carpeta de Drive del brief). "
                                "Privado: sólo lo lee la fábrica con GITHUB_TOKEN.", "--latest=false"], tok)
        print(f"crear release {tag}: {'ok' if motivo is None else 'falló'} ({_ahora_s() - t0:.1f} s)", flush=True)
        if motivo:
            print(f"::error::no se pudo crear el release {tag}: {motivo}", flush=True)
            return 1

    base = Path(carpeta) if carpeta else Path(tempfile.mkdtemp(prefix="granja-release-"))
    base.mkdir(parents=True, exist_ok=True)
    try:
        for m in pendientes:
            n = partes(m["url"])[2]
            ruta = base / n
            t0 = _ahora_s()
            try:
                bajar_drive(m["drive_url"], ruta)
                t1 = _ahora_s()
                print(f"{n}: Drive bajó {ruta.stat().st_size} bytes en {t1 - t0:.1f} s", flush=True)
                try:
                    dur = sondear(ruta)
                except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, OSError) as e:
                    raise ErrorDescarga(f"ffprobe no pudo medir el archivo ({type(e).__name__})") from None
                if not dur > 0 or dur != dur:
                    raise ErrorDescarga("ffprobe devolvió una duración no válida")
                tam = ruta.stat().st_size
                h = _sha256(ruta)
            except ErrorDescarga as e:
                print(f"::warning::{n}: {e} (se reintenta en la próxima corrida)", flush=True)
                filas.append((n, f"Drive falló: {e}", "", ""))
                ruta.unlink(missing_ok=True)
                continue
            t2 = _ahora_s()
            motivo = _gh(ejecutor, ["gh", "release", "upload", tag, str(ruta), "--repo", repo, "--clobber"], tok)
            ruta.unlink(missing_ok=True)
            if motivo:
                hay_error_gh = True
                print(f"::error::{n}: no se pudo subir al release ({motivo})", flush=True)
                filas.append((n, f"gh falló: {motivo}", str(tam), h))
                continue
            subidos.add(n)
            filas.append((n, "subido", str(tam), h))
            print(f"{n}: subido {tam} bytes, {dur:.1f} s de video, sha256 {h} ({_ahora_s() - t2:.1f} s de subida)",
                  flush=True)
    finally:
        if not carpeta:
            shutil.rmtree(base, ignore_errors=True)

    completo = len(subidos) == total
    print(f"release {tag}: {len(subidos)} de {total} subidos", flush=True)
    resumen = [f"### release {tag}: {len(subidos)} de {total} subidos", "",
               "| nombre | estado | bytes | sha256 |", "|---|---|---|---|"]
    resumen += [f"| {n} | {e.replace('|', '/')} | {b} | {h} |" for n, e, b, h in filas]
    _escribir(entorno, "GITHUB_STEP_SUMMARY", "\n".join(resumen) + "\n")
    _escribir(entorno, "GITHUB_OUTPUT", f"completo={'si' if completo else 'no'}\n")
    return 1 if hay_error_gh else 0


def _sha256(ruta: Path) -> str:
    from .editar.componer import sha256_archivo
    return sha256_archivo(ruta)


def orden_verificar(ficha_id, tag, *, raiz=None, entorno=None, transporte=None, abrir=None, sondear=None) -> int:
    sondear = sondear or _sondear_duracion
    entradas = _entradas(ficha_id, tag, raiz_repo(raiz), con_drive=False)
    if not entradas:
        print(f"::error::la ficha {ficha_id} no tiene material en el release {tag}", flush=True)
        return 1
    malos = 0
    base = Path(tempfile.mkdtemp(prefix="granja-verificar-"))
    try:
        for m in entradas:
            n = partes(m["url"])[2]
            ruta = base / n
            t0 = _ahora_s()
            try:
                bajar(m["url"], ruta, entorno=entorno, transporte=transporte, abrir=abrir)
                t1 = _ahora_s()
                try:
                    dur = sondear(ruta)
                except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, OSError) as e:
                    raise ErrorDescarga(f"ffprobe no pudo medir el archivo ({type(e).__name__})") from None
                print(f"{n}: {ruta.stat().st_size} bytes, {dur:.1f} s de video, sha256 {_sha256(ruta)} "
                      f"(bajada {t1 - t0:.1f} s)", flush=True)
            except ErrorDescarga as e:
                malos += 1
                print(f"::error::{n}: {e}", flush=True)
            finally:
                ruta.unlink(missing_ok=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(f"release {tag}: {len(entradas) - malos} de {len(entradas)} verificados", flush=True)
    return 1 if malos else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.release",
                                 description="Material de campaña en el release privado de este repo.")
    ap.add_argument("orden", choices=["faltan", "subir", "verificar"])
    ap.add_argument("--ficha", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--raiz", default=None)
    a = ap.parse_args(argv)
    try:
        if a.orden == "faltan":
            return orden_faltan(a.ficha, a.tag, raiz=a.raiz)
        if a.orden == "subir":
            return orden_subir(a.ficha, a.tag, raiz=a.raiz)
        return orden_verificar(a.ficha, a.tag, raiz=a.raiz)
    except ErrorDescarga as e:
        print(f"::error::{_sin_token(str(e), token())}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

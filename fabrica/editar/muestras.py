"""Las 3 muestras de estilo: `python -m fabrica.editar.muestras --salida CARPETA`.

Descarga cada fuente (verifica sha256; si no coincide, falla), renderiza los 3 MP4, guarda 3
fotogramas PNG por muestra (15 %, 50 % y 95 % de la duración), corre la puerta de estilo y escribe
`manifiesto.json` y `puerta-estilo-auto.md`. La carpeta de salida no puede estar dentro del repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

from . import estilo
from .componer import Linea, componer
from .config_ruta import RAIZ
from .lienzo import Toma
from .marca import Marca

CUADROS = (0.15, 0.50)
FIN_S = 0.2   # el último fotograma se saca 0.2 s antes del final, ya dentro del cierre


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def descargar(url: str, esperado: str, destino: Path) -> Path:
    """Descarga `url` en `destino` y verifica el sha256. Lanza RuntimeError si no coincide."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not url.startswith("https://raw.githubusercontent.com/"):
        raise RuntimeError(f"fuente fuera de la lista permitida: {url}")
    if not (destino.exists() and sha256(destino) == esperado):
        req = urllib.request.Request(url, headers={"User-Agent": "granja-clips-muestras"})
        with urllib.request.urlopen(req, timeout=120) as r, open(destino, "wb") as f:
            shutil.copyfileobj(r, f)
    real = sha256(destino)
    if real != esperado:
        destino.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 no coincide para {url}: esperado {esperado}, real {real}")
    return destino


def cargar_muestras(ruta: Path | None = None) -> list[dict]:
    ruta = ruta or RAIZ / "marca" / "muestras" / "muestras.yml"
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))["muestras"]


def _dentro_del_repo(salida: Path) -> bool:
    try:
        salida.resolve().relative_to(RAIZ.resolve())
        return True
    except ValueError:
        return False


def fotogramas(mp4: Path, duracion: float, salida: Path, base: str) -> list[str]:
    nombres = []
    tiempos = [(f"{int(p * 100):02d}", duracion * p) for p in CUADROS] + [("fin", max(0.0, duracion - FIN_S))]
    for etiqueta, seg in tiempos:
        nombre = f"{base}-{etiqueta}.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seg:.3f}",
                        "-i", str(mp4), "-frames:v", "1", str(salida / nombre)], check=True)
        nombres.append(nombre)
    return nombres


def generar(salida: Path, marca: Marca, muestras: list[dict], descargas: Path) -> dict:
    manifiesto = {"muestras": []}
    informe = ["# Puerta de estilo automática", "",
               "Revisión sin LLM (`python -m fabrica.editar.estilo`) sobre cada MP4 y su plan.", ""]
    for m in muestras:
        mid = m["id"]
        t = Toma(**m["toma"])
        lineas = [Linea(**ln) for ln in m["lineas"]]
        vid = m.get("video")
        ruta_video = None
        extra_src = {}
        if vid:
            ruta_video = descargar(vid["url"], vid["sha256"], descargas / Path(vid["url"]).name)
            extra_src = {"url": vid["url"], "sha256": vid["sha256"]}
            subs = m.get("subtitulos")
            if subs:
                descargar(subs["url"], subs["sha256"], descargas / Path(subs["url"]).name)
                extra_src["subtitulos"] = {"url": subs["url"], "sha256": subs["sha256"]}
            inicio, duracion = float(m["recorte"]["inicio"]), float(m["recorte"]["duracion"])
        else:
            inicio, duracion = 0.0, float(m["duracion"])
        mp4 = salida / f"{mid}.mp4"
        plan_ruta = salida / f"{mid}.plan.json"
        with tempfile.TemporaryDirectory(prefix=f"trabajo-{mid}-") as tmp:
            plan = componer(marca, t, lineas, mp4, duracion=duracion, trabajo=Path(tmp),
                            fuente_video=ruta_video, inicio=inicio, plan_salida=plan_ruta)
        puntos = estilo.revisar(mp4, plan, marca)
        cuadros = fotogramas(mp4, duracion, salida, mid)
        ok = all(p[1] for p in puntos)
        informe.append(estilo.informe_md(f"{mid} ({'EN VERDE' if ok else 'EN ROJO'})", puntos))
        manifiesto["muestras"].append({
            "id": mid,
            "archivo": mp4.name,
            "plan": plan_ruta.name,
            "duracion_s": round(estilo.sondear(mp4)["duracion"], 3),
            "sha256_mp4": sha256(mp4),
            "fotogramas": cuadros,
            "pilar": m["pilar"],
            "fuente_id": m["fuente_id"],
            "fuente": extra_src or {"propio": "tipográfico, sin metraje"},
            "recorte": m.get("recorte"),
            "licencia": m["licencia"],
            "enlace": m.get("enlace_licencia"),
            "lineas": [{"t0": ln.t0, "t1": ln.t1, "nombre": ln.nombre, "frase": ln.frase,
                        "acotacion": ln.acotacion, "original": ln.original} for ln in lineas],
            "puerta_estilo": {"en_verde": ok,
                              "puntos": [{"punto": n, "ok": o, "detalle": d} for n, o, d in puntos]},
        })
    todas = all(x["puerta_estilo"]["en_verde"] for x in manifiesto["muestras"])
    manifiesto["puerta_estilo_en_verde"] = todas
    informe.append(f"## Resultado: {'EN VERDE' if todas else 'EN ROJO'}\n")
    (salida / "manifiesto.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")
    (salida / "puerta-estilo-auto.md").write_text("\n".join(informe), encoding="utf-8")
    return manifiesto


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.editar.muestras")
    ap.add_argument("--salida", default=str(RAIZ.parent / "salida-muestras"),
                    help="carpeta de salida, fuera del repo (por defecto ../salida-muestras)")
    ap.add_argument("--descargas", default=None, help="carpeta para las descargas (por defecto temporal)")
    args = ap.parse_args(argv)
    salida = Path(args.salida).resolve()
    if _dentro_del_repo(salida):
        print(f"ERROR: la salida {salida} está dentro del repo; no se escribe video en el árbol.", file=sys.stderr)
        return 2
    salida.mkdir(parents=True, exist_ok=True)
    marca = Marca.cargar()
    muestras = cargar_muestras()
    with tempfile.TemporaryDirectory(prefix="descargas-") as tmp:
        descargas = Path(args.descargas) if args.descargas else Path(tmp)
        if _dentro_del_repo(descargas):
            print("ERROR: las descargas no pueden estar dentro del repo.", file=sys.stderr)
            return 2
        manifiesto = generar(salida, marca, muestras, descargas)
    for m in manifiesto["muestras"]:
        print(f"{m['id']}: {m['archivo']} {m['duracion_s']} s, puerta {'EN VERDE' if m['puerta_estilo']['en_verde'] else 'EN ROJO'}")
    return 0 if manifiesto["puerta_estilo_en_verde"] else 1


if __name__ == "__main__":
    sys.exit(main())

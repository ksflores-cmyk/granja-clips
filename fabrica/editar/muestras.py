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
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

from .. import fuentes as mod_fuentes
from ..registro import ZONA
from . import estilo
from . import historia as mod_historia
from . import musica as mod_musica
from . import sfx as mod_sfx
from .componer import Linea, componer, sondear_video
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


def cargar_muestras_historia(ruta: Path | None = None) -> list[dict]:
    """Las muestras de la historia (#20): `marca/muestras/historia.yml` (lista vacía si no existe)."""
    ruta = ruta or RAIZ / "marca" / "muestras" / "historia.yml"
    if not ruta.is_file():
        return []
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


def autorizar(m: dict, fichas: list[dict], hoy) -> None:
    """Lista blanca: consulta fuentes.yml ANTES de abrir cualquier conexión (video y subtítulos).

    Lanza RuntimeError con el motivo si la ficha no autoriza la descarga para muestras.
    """
    ficha = next((f for f in fichas if f.get("id") == m["fuente_id"]), None)
    urls = []
    if m.get("video"):
        urls.append(m["video"]["url"])
        if m.get("subtitulos"):
            urls.append(m["subtitulos"]["url"])
    for u in urls:
        ok, motivo = mod_fuentes.autoriza_descarga(ficha, hoy, "muestras", u)
        if not ok:
            raise RuntimeError(f"descarga no autorizada para {m['id']} ({m['fuente_id']}): {motivo}")


ID_CON_MUSICA = "informativa-con-musica"
TEXTOS_NEUTROS = {"gancho": "Prueba de música y movimiento", "palabra_lapiz": "MOVIMIENTO",
                  "datos": ["Pieza de prueba sin datos reales.", "La música es CC0 y viene del release privado."],
                  "credito": "Fuente: prueba de estilo"}


def _entrada_de_prueba(raiz, hoy) -> tuple[dict, str]:
    """Entrada de `informativas.yml` (la vigente, o cualquiera) o textos fijos neutros, sin datos inventados."""
    from .. import informativa
    ahora = datetime.now(ZONA)
    validas = [e for e in informativa.cargar(raiz) if all(e.get(k) for k in ("id", "gancho", "palabra_lapiz", "datos"))]
    entrada = next((e for e in validas if informativa.vigente(e, ahora)), None) or (validas[0] if validas else None)
    if entrada is not None:
        return entrada, f"informativas.yml:{entrada['id']}"
    return {"id": "neutra", **{k: v for k, v in TEXTOS_NEUTROS.items() if k != "credito"}}, "textos fijos neutros"


def _muestra_con_musica(salida: Path, marca: Marca, registrar, omitir, hoy) -> None:
    """4.ª muestra: informativa con música CC0 y movimiento de entrada de texto (se omite sin pista disponible)."""
    from .. import informativa
    numero = 4
    pista = mod_musica.elegir(marca.raiz, numero)
    if pista is None:
        omitir(ID_CON_MUSICA, "sin pista de música CC0 disponible "
                              f"(GRANJA_MUSICA_DIR={mod_musica.carpeta() or ''})")
        return
    entrada, origen = _entrada_de_prueba(marca.raiz, hoy)
    credito = informativa.CREDITO if origen.startswith("informativas.yml") else TEXTOS_NEUTROS["credito"]
    t = Toma(numero=numero, gancho=entrada["gancho"].strip(), palabra_lapiz=entrada["palabra_lapiz"].strip(),
             lapiz="subrayado", escena=str(entrada.get("escena") or informativa.ESCENA).upper(), credito=credito)
    lineas = informativa._lineas(entrada)
    mp4 = salida / f"{ID_CON_MUSICA}.mp4"
    plan_ruta = salida / f"{ID_CON_MUSICA}.plan.json"
    with tempfile.TemporaryDirectory(prefix=f"trabajo-{ID_CON_MUSICA}-") as tmp:
        plan = componer(marca, t, lineas, mp4, duracion=informativa.DURACION_S, trabajo=Path(tmp),
                        fuente_video=None, plan_salida=plan_ruta, musica=pista, movimiento=True)
    puntos = estilo.revisar(mp4, plan, marca)
    registrar(ID_CON_MUSICA, mp4, plan_ruta, informativa.DURACION_S, puntos, lineas, {
        "pilar": "informativa", "fuente_id": informativa.FICHA_ID, "fuente": {"propio": f"tipográfico: {origen}"},
        "recorte": None, "licencia": f"propio (tipográfico); música CC0: {pista['id']} ({pista['autor']})",
        "enlace": pista["pagina"]}, plan)


def resolver_sfx(plan: dict, sfx: dict) -> tuple[dict, list[str]]:
    """El plan fijo de una muestra con `sfx: {tipo: ...}` resuelto contra los efectos disponibles. Sin efecto de ese
    tipo, el momento pasa a zoom_suave (si el plan aún admite otro zoom) o a ninguno. Devuelve (plan, avisos)."""
    avisos, momentos = [], []
    for i, mo in enumerate(plan["momentos"], 1):
        mo = dict(mo)
        if isinstance(mo.get("sfx"), dict):
            tipo = mo["sfx"].get("tipo")
            e = next((sfx[k] for k in sorted(sfx) if sfx[k].get("tipo") == tipo), None)
            if e is not None:
                mo["sfx"] = e["id"]
            else:
                mo["sfx"], mo["tratamiento"] = None, "zoom_suave"
                avisos.append(f"momento {i}: sin efecto de tipo {tipo!r} disponible; el tratamiento sfx pasa a zoom_suave")
        momentos.append(mo)
    return {**plan, "momentos": momentos}, avisos


def memoria_hijos_mb() -> float:
    """Memoria máxima (MB) de cualquier proceso hijo ya terminado (ffmpeg incluido); es un máximo acumulado del
    proceso. En Linux `ru_maxrss` viene en KB; 0.0 donde no hay `resource`."""
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)
    except (ImportError, OSError):
        return 0.0


def _muestras_historia(salida: Path, marca: Marca, muestras: list[dict], descargas: Path, registrar, fichas: list[dict],
                       descargador, sfx: dict) -> None:
    """Muestras con historia: el plan sale del eslabón sin IA (`eslabon: sin-ia`) o es el JSON fijo del YAML."""
    cfg = {**mod_historia.cargar_config(marca.raiz), "activo": True}
    for m in muestras:
        mid, vid = m["id"], m["video"]
        ruta_video = descargador(vid["url"], vid["sha256"], descargas / Path(vid["url"]).name)
        inicio, duracion = float(m["recorte"]["inicio"]), float(m["recorte"]["duracion"])
        fin = round(inicio + duracion, 3)
        ficha = next((f for f in fichas if f.get("id") == m["fuente_id"]), None)
        t = Toma(**m["toma"])
        lineas = [Linea(**ln) for ln in m["lineas"]]
        eslabon = (m.get("historia") or {}).get("eslabon", "sin-ia")
        avisos: list[str] = []
        if eslabon == "fijo":
            plan_h, avisos = resolver_sfx(m["historia"]["plan"], sfx)
            ids = sorted(sfx)
            ok, motivos = mod_historia.validar_plan(plan_h, (inicio, fin), estilo.rango_duracion({}, ficha), ids, ficha, cfg)
            if not ok:
                raise RuntimeError(f"el plan fijo de {mid} no valida: " + "; ".join(motivos))
            hist = mod_historia.Planificacion(plan_h, "gemini", ["plan fijo del YAML, como si lo devolviera el modelo"]
                                              + avisos)
        else:
            hist = mod_historia.planificar(raiz=marca.raiz, ficha=ficha, recorte=(inicio, fin),
                                           rango_duracion=estilo.rango_duracion({}, ficha), sfx=sfx,
                                           ruta_video=ruta_video, lineas=[], cfg=cfg)
            if hist.plan is None:
                raise RuntimeError(f"el eslabón sin IA no dio plan para {mid}: {hist.motivo}; {hist.intentos}")
        mp4 = salida / f"{mid}.mp4"
        plan_ruta = salida / f"{mid}.plan.json"
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix=f"trabajo-{mid}-") as tmp:
            plan = componer(marca, t, lineas, mp4, duracion=duracion, trabajo=Path(tmp), fuente_video=ruta_video,
                            inicio=inicio, plan_salida=plan_ruta, ficha=ficha, historia=hist, sfx=sfx,
                            config_historia=cfg)
        t_render = time.monotonic() - t0
        puntos = estilo.revisar(mp4, plan, marca, ficha)
        t_total = time.monotonic() - t0
        memoria = memoria_hijos_mb()
        h = plan.get("historia") or {}
        registrar(mid, mp4, plan_ruta, float(plan["duracion"]), puntos,
                  [Linea(**{k: v for k, v in ln.items() if k in ("t0", "t1", "nombre", "frase", "acotacion")})
                   for ln in plan["lineas"]], {
            "pilar": m["pilar"], "fuente_id": m["fuente_id"], "fuente": {"url": vid["url"], "sha256": vid["sha256"]},
            "recorte": m.get("recorte"), "licencia": m["licencia"], "enlace": m.get("enlace_licencia"),
            "historia": {"proveedor": h.get("proveedor"), "plan": h.get("plan"), "mapa": h.get("mapa"),
                         "intentos": h.get("intentos"), "motivo": h.get("motivo"), "avisos": avisos,
                         "duracion_salida_s": plan["duracion"], "detector": hist.detector,
                         "memoria_max_hijos_mb": memoria,
                         "efectos": (plan.get("audio") or {}).get("efectos") or []},
            "tiempos_s": {"render": round(t_render, 1), "puerta_de_estilo": round(t_total - t_render, 1),
                          "render_y_puerta": round(t_total, 1)}}, plan)


def generar(salida: Path, marca: Marca, muestras: list[dict], descargas: Path, *,
            fichas: list[dict] | None = None, hoy=None, descargador=descargar,
            historia: list[dict] | None = None, sfx: dict | None = None) -> dict:
    if fichas is None:
        fichas = mod_fuentes.cargar_fuentes()
    if hoy is None:
        hoy = datetime.now(ZONA).date()
    for m in [*muestras, *(historia or [])]:          # todo se autoriza antes de la primera conexión
        autorizar(m, fichas, hoy)
    manifiesto = {"muestras": [], "omitidas": []}
    informe = ["# Puerta de estilo automática", "",
               "Revisión sin LLM (`python -m fabrica.editar.estilo`) sobre cada MP4 y su plan.", ""]

    def registrar(mid, mp4, plan_ruta, duracion, puntos, lineas, datos, plan):
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
            **datos,
            "lineas": [{"t0": ln.t0, "t1": ln.t1, "nombre": ln.nombre, "frase": ln.frase,
                        "acotacion": ln.acotacion, "original": ln.original} for ln in lineas],
            "audio": plan.get("audio"),
            "puerta_estilo": {"en_verde": ok,
                              "puntos": [{"punto": n, "ok": o, "detalle": d} for n, o, d in puntos]},
        })

    def omitir(mid, motivo):
        manifiesto["omitidas"].append({"id": mid, "motivo": motivo})
        informe.append(f"### {mid} (OMITIDA)\n\n- {motivo}\n")

    for m in muestras:
        mid = m["id"]
        t = Toma(**m["toma"])
        lineas = [Linea(**ln) for ln in m["lineas"]]
        vid = m.get("video")
        ruta_video = None
        extra_src = {}
        if vid:
            ruta_video = descargador(vid["url"], vid["sha256"], descargas / Path(vid["url"]).name)
            extra_src = {"url": vid["url"], "sha256": vid["sha256"]}
            subs = m.get("subtitulos")
            if subs:
                descargador(subs["url"], subs["sha256"], descargas / Path(subs["url"]).name)
                extra_src["subtitulos"] = {"url": subs["url"], "sha256": subs["sha256"]}
            inicio, duracion = float(m["recorte"]["inicio"]), float(m["recorte"]["duracion"])
        else:
            inicio, duracion = 0.0, float(m["duracion"])
        # Música CC0 sólo si la pieza no trae audio propio (video mudo o sin video); el movimiento, sin video.
        pista = None
        if mod_musica.lleva_musica(None, bool(ruta_video) and sondear_video(ruta_video)["audio"]):
            pista = mod_musica.elegir(marca.raiz, t.numero)
            if pista is None:
                omitir(mid, "pieza sin audio propio y sin pista de música CC0 disponible "
                            f"(GRANJA_MUSICA_DIR={mod_musica.carpeta() or ''})")
                continue
        mp4 = salida / f"{mid}.mp4"
        plan_ruta = salida / f"{mid}.plan.json"
        with tempfile.TemporaryDirectory(prefix=f"trabajo-{mid}-") as tmp:
            plan = componer(marca, t, lineas, mp4, duracion=duracion, trabajo=Path(tmp),
                            fuente_video=ruta_video, inicio=inicio, plan_salida=plan_ruta,
                            musica=pista, movimiento=ruta_video is None)
        puntos = estilo.revisar(mp4, plan, marca)
        registrar(mid, mp4, plan_ruta, duracion, puntos, lineas, {
            "pilar": m["pilar"], "fuente_id": m["fuente_id"],
            "fuente": extra_src or {"propio": "tipográfico, sin metraje"},
            "recorte": m.get("recorte"), "licencia": m["licencia"], "enlace": m.get("enlace_licencia")}, plan)
    _muestra_con_musica(salida, marca, registrar, omitir, hoy)
    if historia:
        _muestras_historia(salida, marca, historia, descargas, registrar, fichas, descargador,
                           sfx if sfx is not None else mod_sfx.disponibles(marca.raiz))
    # Una muestra omitida (p. ej. sin pista de música del release) no cuenta como verde.
    todas = (all(x["puerta_estilo"]["en_verde"] for x in manifiesto["muestras"])
             and not manifiesto["omitidas"])
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
    muestras, historia = cargar_muestras(), cargar_muestras_historia()
    with tempfile.TemporaryDirectory(prefix="descargas-") as tmp:
        descargas = Path(args.descargas) if args.descargas else Path(tmp)
        if _dentro_del_repo(descargas):
            print("ERROR: las descargas no pueden estar dentro del repo.", file=sys.stderr)
            return 2
        try:
            manifiesto = generar(salida, marca, muestras, descargas, historia=historia)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    for m in manifiesto["muestras"]:
        extra = ""
        if m.get("historia"):       # #20: qué eslabón armó el plan, con qué detector y cuánto tardó (render + puerta)
            extra = (f"; historia {m['historia']['proveedor']} ({m['historia']['detector']}), render "
                     f"{m['tiempos_s']['render']} s + puerta {m['tiempos_s']['puerta_de_estilo']} s"
                      f"; memoria máx. de ffmpeg (acumulada) {m['historia'].get('memoria_max_hijos_mb')} MB")
            extra += "".join(f"; {a}" for a in m["historia"].get("avisos") or [])
        print(f"{m['id']}: {m['archivo']} {m['duracion_s']} s, puerta {'EN VERDE' if m['puerta_estilo']['en_verde'] else 'EN ROJO'}{extra}")
    return 0 if manifiesto["puerta_estilo_en_verde"] else 1


if __name__ == "__main__":
    sys.exit(main())

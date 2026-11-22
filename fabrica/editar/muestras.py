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
from datetime import datetime
from pathlib import Path

import yaml

from .. import fuentes as mod_fuentes
from ..registro import ZONA
from . import dividida as mod_dividida
from . import estilo
from . import musica as mod_musica
from . import stream_sintetico
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


def _dibujar_deteccion(video: Path, seg: float, det: dict, destino: Path) -> None:
    """PNG de un fotograma del compuesto con las cajas detectadas (cara, facecam, estorbos y juego)."""
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="deteccion-") as tmp:
        cuadro = Path(tmp) / "cuadro.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seg:.3f}", "-i", str(video),
                        "-frames:v", "1", str(cuadro)], check=True)
        mod_dividida.dibujar(Image.open(cuadro), det, destino)


def autorizar(m: dict, fichas: list[dict], hoy) -> None:
    """Lista blanca: consulta fuentes.yml ANTES de abrir cualquier conexión (video y subtítulos).

    Lanza RuntimeError con el motivo si la ficha no autoriza la descarga para muestras.
    """
    pares = []                      # (id de la ficha, url)
    if m.get("video"):
        pares.append((m["fuente_id"], m["video"]["url"]))
        if m.get("subtitulos"):
            pares.append((m["fuente_id"], m["subtitulos"]["url"]))
    if m.get("juego"):              # muestra dividida: el «juego» del compuesto es otra fuente, también autorizada
        pares.append((m["juego"]["fuente_id"], m["juego"]["video"]["url"]))
    for fuente_id, u in pares:
        ficha = next((f for f in fichas if f.get("id") == fuente_id), None)
        ok, motivo = mod_fuentes.autoriza_descarga(ficha, hoy, "muestras", u)
        if not ok:
            raise RuntimeError(f"descarga no autorizada para {m['id']} ({fuente_id}): {motivo}")


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
    credito = str(entrada.get("credito") or informativa.CREDITO) if origen.startswith("informativas.yml") else TEXTOS_NEUTROS["credito"]
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


def generar(salida: Path, marca: Marca, muestras: list[dict], descargas: Path, *,
            fichas: list[dict] | None = None, hoy=None, descargador=descargar) -> dict:
    if fichas is None:
        fichas = mod_fuentes.cargar_fuentes()
    if hoy is None:
        hoy = datetime.now(ZONA).date()
    for m in muestras:          # todo se autoriza antes de la primera conexión
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
            if m.get("juego"):      # muestra dividida: compuesto 1920x1080 en la carpeta temporal (nunca en el repo)
                jv = m["juego"]["video"]
                ruta_juego = descargador(jv["url"], jv["sha256"], descargas / Path(jv["url"]).name)
                extra_src["juego"] = {"fuente_id": m["juego"]["fuente_id"], "url": jv["url"], "sha256": jv["sha256"]}
                st = m["stream"]
                ruta_video = stream_sintetico.componer_stream(
                    marca, ruta_juego, ruta_video, descargas / f"stream-{mid}.mp4", descargas / f"trabajo-stream-{mid}",
                    duracion=inicio + duracion, juego_inicio=float(m["juego"]["inicio"]),
                    tramo_cara=tuple(st["tramo_cara"]), esquina_facecam=st["esquina_facecam"],
                    esquina_logo=st["esquina_logo"], chat=st.get("chat"), alerta_s=st.get("alerta_s"))
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
        esperada = m.get("disposicion", "completa" if ruta_video else None)
        extra = {}
        if esperada:      # cada muestra declara su disposición: las de siempre, completa; las nuevas, dividida
            real = (plan.get("geometria") or {}).get("disposicion")
            puntos.append((f"[plan] Disposición {esperada} (la esperada de la muestra)", real == esperada,
                           f"real {real}: {((plan.get('geometria') or {}).get('deteccion') or {}).get('motivo', '')}"))
        if esperada == mod_dividida.DIVIDIDA:
            png = f"deteccion-{mid}.png"
            _dibujar_deteccion(ruta_video, inicio + duracion / 2, plan["geometria"]["deteccion"], salida / png)
            extra["deteccion_png"] = png
            extra["deteccion"] = plan["geometria"]["deteccion"]
        registrar(mid, mp4, plan_ruta, duracion, puntos, lineas, {
            "pilar": m["pilar"], "fuente_id": m["fuente_id"],
            "fuente": extra_src or {"propio": "tipográfico, sin metraje"},
            "recorte": m.get("recorte"), "licencia": m["licencia"], "enlace": m.get("enlace_licencia"),
            "disposicion": (plan.get("geometria") or {}).get("disposicion"), **extra}, plan)
    _muestra_con_musica(salida, marca, registrar, omitir, hoy)
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
    muestras = cargar_muestras()
    with tempfile.TemporaryDirectory(prefix="descargas-") as tmp:
        descargas = Path(args.descargas) if args.descargas else Path(tmp)
        if _dentro_del_repo(descargas):
            print("ERROR: las descargas no pueden estar dentro del repo.", file=sys.stderr)
            return 2
        try:
            manifiesto = generar(salida, marca, muestras, descargas)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    for m in manifiesto["muestras"]:
        print(f"{m['id']}: {m['archivo']} {m['duracion_s']} s, puerta {'EN VERDE' if m['puerta_estilo']['en_verde'] else 'EN ROJO'}")
    return 0 if manifiesto["puerta_estilo_en_verde"] else 1


if __name__ == "__main__":
    sys.exit(main())

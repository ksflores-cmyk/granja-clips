"""CLI del video largo semanal.

    python -m fabrica.largo armar [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--carpeta DIR] [--sin-memes]
    python -m fabrica.largo subir --carpeta DIR
    python -m fabrica.largo memes            # lista los memes que pasarían el filtro esta semana

`armar` deja en la carpeta `episodio.mp4`, `miniatura.png` y `episodio.json` (plan + puerta) y anota el episodio en
`estado/largos.json`. `subir` sólo sube si la puerta salió entera en verde.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import recetas as mod_recetas
from ..config import raiz_repo
from ..pausa import ZONA, esta_pausada
from ..editar.marca import Marca
from . import armar as mod_armar
from . import horizontal, memes as mod_memes, puerta, seleccion
from . import youtube as mod_youtube


def _imprimir(texto: str) -> None:
    print(texto, flush=True)


def _guardar_episodio(raiz, entrada: dict) -> None:
    ruta = raiz_repo(raiz) / seleccion.LARGOS
    lista = [e for e in seleccion.episodios(raiz) if e.get("episodio") != entrada.get("episodio")]
    lista.append(entrada)
    lista.sort(key=lambda e: int(e.get("episodio") or 0))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(lista, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def numero_episodio(raiz) -> int:
    hechos = [int(e.get("episodio") or 0) for e in seleccion.episodios(raiz) if e.get("estado") in ("subido", "publicado")]
    return (max(hechos) if hechos else 0) + 1


def desde_por_defecto(raiz, hoy: date) -> date:
    hastas = [e["hasta"] for e in seleccion.episodios(raiz)
              if e.get("estado") in ("subido", "publicado") and isinstance(e.get("hasta"), str)]
    if hastas:
        return date.fromisoformat(max(hastas)) + timedelta(days=1)
    return hoy - timedelta(days=6)


def receta_retroactiva(raiz, e: seleccion.Elegida, ahora: datetime, salida=_imprimir) -> dict | None:
    """Tomas publicadas antes de que existieran las recetas: se rehace la toma (descarga, ojos, componer) para tener
    el tramo y los subtítulos. La ficha vencida se reabre sólo para esto (vetos y retiros ya se revisaron)."""
    from ..detectar import Candidato
    from ..editar import toma as mod_toma
    from ..fuentes import cargar_fuentes
    ficha = next((dict(f) for f in cargar_fuentes(raiz) if f.get("id") == e.ficha_id), None)
    if ficha is None or ficha.get("estado") in ("vetada", "retirada"):
        salida(f"toma {e.numero}: sin ficha {e.ficha_id} vigente para rehacerla")
        return None
    hoy = ahora.astimezone(ZONA).date()
    if str(ficha.get("vence") or "") < hoy.isoformat():
        ficha["vence"] = (hoy + timedelta(days=1)).isoformat()
        ficha["estado"] = "vigente"
    login = e.creador.rsplit("/", 1)[-1]
    cand = Candidato(fuente_id=e.ficha_id, origen="twitch", id_origen=e.id_origen,
                     url=f"https://clips.twitch.tv/{e.id_origen}", titulo=e.gancho, creador=login, idioma="es",
                     juego_id=None, duracion_s=None, vistas=0, vistas_por_hora=0.0, creado_en=None, vod_offset=None,
                     pilar=None, medir_al_descargar=True)
    r = mod_toma.hacer_toma(raiz, cand, ficha, ahora)
    if not r.ok or not r.plan:
        salida(f"toma {e.numero}: no se pudo rehacer ({'; '.join(r.motivos)[:200]})")
        return None
    toma = {"numero": e.numero, "gancho": e.gancho, "caption": e.caption, "hablante": e.hablante,
            "creador": e.creador, "id_origen": e.id_origen, "fuente_id": e.ficha_id,
            "duracion_s": r.plan.get("duracion")}
    rec = mod_recetas.receta_de(toma, ficha, r.plan, cand, e.fecha)
    rec["retroactiva"] = True
    mod_recetas.guardar(raiz, rec)
    return rec


def _url_fuente(receta: dict, e: seleccion.Elegida) -> str:
    o = receta.get("origen") or {}
    return o.get("url") if o.get("tipo") == "twitch" and o.get("url") else f"https://clips.twitch.tv/{e.id_origen}"


def ultimo_subido(raiz) -> date | None:
    fechas = []
    for e in seleccion.episodios(raiz):
        if e.get("estado") in ("subido", "publicado") and e.get("subido"):
            try:
                fechas.append(datetime.fromisoformat(str(e["subido"])).date())
            except ValueError:
                continue
    return max(fechas) if fechas else None


def armar(raiz, carpeta: Path, desde: date | None, hasta: date | None, con_memes: bool = True,
          ahora: datetime | None = None, env=None, salida=_imprimir, forzar: bool = False) -> int:
    from ..descargar import bajar_twitch
    env = os.environ if env is None else env
    raiz = raiz_repo(raiz)
    cfg = seleccion.config(raiz)
    if not cfg.get("activo"):
        salida("largo: apagado en config/largo.yml")
        return 0
    if esta_pausada(raiz)[0]:
        salida("largo: PAUSA activa, no se arma")
        return 0
    ahora = ahora or datetime.now(ZONA)
    hoy = ahora.astimezone(ZONA).date()
    reciente = ultimo_subido(raiz)
    if reciente is not None and (hoy - reciente).days < 5 and not forzar:
        salida(f"largo: ya salió un episodio el {reciente}; el siguiente, la próxima semana")
        return 0
    hasta = hasta or hoy
    desde = desde or desde_por_defecto(raiz, hoy)
    episodio = numero_episodio(raiz)
    elegidas, fuera = seleccion.candidatas(raiz, desde, hasta)
    for f in fuera:
        salida(f"fuera: {f}")
    salida(f"largo #{episodio}: {len(elegidas)} tomas sin Short entre {desde} y {hasta}")
    if len(elegidas) < int(cfg.get("min_clips", 6)):
        salida(f"largo: menos de {cfg.get('min_clips', 6)} tomas; el episodio espera a la próxima semana")
        return 0
    elegidas = seleccion.ordenar(elegidas, cfg)
    if len(elegidas) > int(cfg.get("max_clips", 30)):
        mejores = sorted(elegidas, key=lambda x: -x.vistas)[:int(cfg["max_clips"])]
        elegidas = [x for x in elegidas if x in mejores]
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = Marca.cargar(raiz)
    marca_h = horizontal.marca_horizontal(marca, cfg)
    clips: list[mod_armar.Clip] = []
    crudos: list[tuple[seleccion.Elegida, Path, dict]] = []
    total = len(elegidas)
    for i, e in enumerate(elegidas):
        receta = e.receta or receta_retroactiva(raiz, e, ahora, salida)
        if receta is None:
            continue
        trabajo = carpeta / f"t{e.numero:03d}"
        try:
            fuente = bajar_twitch(_url_fuente(receta, e), trabajo / "fuente")
            plan = horizontal.render_segmento(marca_h, receta, e.numero, e.creador, fuente, trabajo / "crudo.mp4",
                                              trabajo / "placas", cfg, puesto=total - i)
        except Exception as ex:  # noqa: BLE001 — un clip que falla no tumba el episodio
            salida(f"toma {e.numero}: no se pudo montar ({type(ex).__name__}: {str(ex)[:160]})")
            continue
        finally:
            shutil.rmtree(trabajo / "fuente", ignore_errors=True)
        crudos.append((e, trabajo / "crudo.mp4", plan))
    if len(crudos) < int(cfg.get("min_clips", 6)):
        salida(f"largo: sólo {len(crudos)} clips montados; el episodio espera")
        return 0
    # puestos definitivos (por si alguno se cayó): cuenta atrás de n a 1
    n = len(crudos)
    biblioteca: list[mod_memes.Meme] = []
    cfg_m = cfg.get("memes") or {}
    clave = env.get("GEMINI_API_KEY", "")
    modelos = mod_memes._modelos(raiz)
    if con_memes and cfg_m.get("activo") and clave:
        cand = mod_memes.populares(list(cfg_m.get("paises") or ["gt"]), salida=salida)[:int(cfg_m.get("max_candidatos", 80))]
        elegidos = mod_memes.filtrar(cand, clave, modelos, int(cfg_m.get("max_biblioteca", 16)), salida=salida)
        biblioteca = mod_memes.bajar(elegidos, carpeta / "memes", cfg_m, salida=salida)
        salida(f"memes: {len(biblioteca)} en la biblioteca de la semana: " + ", ".join(m.nombre for m in biblioteca))
    textos = [{"texto": f"{e.gancho}. {e.caption}"} for e, _, _ in crudos]
    asignados = mod_memes.asignar(textos, biblioteca, clave, modelos, salida=salida) if biblioteca else {}
    inicios, t = [], 0.0
    for _, _, p in crudos:
        inicios.append(t)
        t += float(p["duracion"])
    asignados = mod_memes.espaciar(asignados, inicios, float(cfg_m.get("separacion_min_s", 20)))
    for k, (e, crudo, p) in enumerate(crudos):
        dur = float(p["duracion"])
        pico = horizontal.pico_de_audio(crudo, dur * 0.35, dur - 0.6)
        final = crudo.with_name("clip.mp4")
        meme = mod_armar.preparar_clip(crudo, final, dur, pico, asignados.get(k), cfg_m)
        crudo.unlink(missing_ok=True)
        clips.append(mod_armar.Clip(numero=e.numero, puesto=n - k, mp4=final, duracion=dur, creador=e.creador,
                                    nombre=e.hablante or e.creador.rsplit("/", 1)[-1], caption=e.caption or e.gancho,
                                    pico=pico, meme=meme))
    # intro: avances de los 3 mejores (los últimos de la cuenta atrás) y el título
    ci = cfg.get("intro") or {}
    partes, intro_s = [], 0.0
    for c in clips[::-1][:int(ci.get("avances", 3))]:
        av = carpeta / f"avance_{c.numero:03d}.mp4"
        desde_av = (c.pico if c.pico is not None else c.duracion / 2) - float(ci.get("avance_s", 1.0)) * 0.4
        mod_armar.subprocess.run(mod_armar.orden_avance(c.mp4, desde_av, float(ci.get("avance_s", 1.0)), av),
                                 check=True, capture_output=True)
        partes.append(av)
        intro_s += float(ci.get("avance_s", 1.0))
    from ..editar import sfx as mod_sfx
    claqueta = (mod_sfx.disponibles(raiz).get("claqueta") or {}).get("ruta")
    png = carpeta / "titulo.png"
    mod_armar.tarjeta_titulo(marca_h, str(ci.get("titulo", "LA SEMANA EN GTA 6")), desde, hasta, episodio).save(png)
    titulo_mp4 = carpeta / "titulo.mp4"
    mod_armar.subprocess.run(mod_armar.orden_imagen(png, float(ci.get("titulo_s", 3.0)), titulo_mp4,
                                                    Path(claqueta) if claqueta else None),
                             check=True, capture_output=True)
    partes.append(titulo_mp4)
    intro_s += float(ci.get("titulo_s", 3.0))
    partes += [c.mp4 for c in clips]
    co = cfg.get("outro") or {}
    creditos = sorted({c.creador for c in clips})
    png_o = carpeta / "outro.png"
    mod_armar.tarjeta_outro(marca_h, str(co.get("texto", "")), str(co.get("llamado", "")), creditos).save(png_o)
    outro_mp4 = carpeta / "outro.mp4"
    mod_armar.subprocess.run(mod_armar.orden_imagen(png_o, float(co.get("duracion_s", 12)), outro_mp4,
                                                    Path(claqueta) if claqueta else None),
                             check=True, capture_output=True)
    partes.append(outro_mp4)
    mp4 = carpeta / "episodio.mp4"
    mod_armar.unir(marca_h, partes, mp4)
    caps = mod_armar.capitulos(intro_s, clips)
    cy = cfg.get("youtube") or {}
    titulo = str(cy.get("titulo", "{episodio}")).format(episodio=episodio)
    lineas_cap = [f"{mod_armar.mmss(t)} {s}" for t, s in caps]
    desc = "\n".join([
        f"Los momentos de streamers jugando GTA 6 que no salieron en nuestros Shorts esta semana "
        f"({mod_armar.rango_fechas(desde, hasta)}). Cuenta atrás del #{n} al #1.", "",
        *lineas_cap, "", "Clips de (sigue a cada streamer):",
        *[f"🎥 https://{c}" for c in creditos], "",
        "Guion Vivo · clips de videojuegos y streamers en español.", "#GTA6 #GTAVI #GrandTheftAutoVI"])
    dur_esperada = intro_s + sum(c.duracion for c in clips) + float(co.get("duracion_s", 12))
    plan = {"episodio": episodio, "desde": desde.isoformat(), "hasta": hasta.isoformat(), "titulo": titulo,
            "descripcion": desc, "capitulos": caps, "duracion_esperada": round(dur_esperada, 2),
            "clips": [{"toma": c.numero, "puesto": c.puesto, "duracion": c.duracion, "creador": c.creador,
                       "meme": c.meme} for c in clips],
            "memes_semana": [m.registro() for m in biblioteca]}
    # miniatura: el cuadro del golpe del #1 con el título encima
    mini = carpeta / "miniatura.png"
    try:
        mejor = clips[-1]
        mod_armar.subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{(mejor.pico or mejor.duracion / 2):g}",
                                  "-i", str(mejor.mp4), "-frames:v", "1", "-vf", "scale=1280:720", str(mini)],
                                 check=True, capture_output=True)
    except Exception:  # noqa: BLE001
        mini.unlink(missing_ok=True)
    puntos = puerta.revisar(mp4, plan, cfg, raiz)
    plan["puerta"] = [{"punto": p, "ok": ok, "detalle": d} for p, ok, d in puntos]
    verde = all(ok for _, ok, _ in puntos)
    (carpeta / "episodio.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    for p, ok, d in puntos:
        salida(f"{'bien' if ok else 'ROJO'}: {p} ({d})")
    _guardar_episodio(raiz, {"episodio": episodio, "desde": plan["desde"], "hasta": plan["hasta"],
                             "tomas": [c.numero for c in clips], "estado": "armado" if verde else "rojo",
                             "duracion_s": round(dur_esperada, 1), "titulo": titulo,
                             "memes": [c.meme["nombre"] for c in clips if c.meme],
                             "armado": ahora.astimezone(ZONA).isoformat(timespec="minutes")})
    salida(f"largo #{episodio}: {len(clips)} clips, {dur_esperada / 60:.1f} min, puerta {'en verde' if verde else 'en ROJO'}")
    return 0 if verde else 1


def subir(raiz, carpeta: Path, env=None, salida=_imprimir) -> int:
    raiz = raiz_repo(raiz)
    plan = json.loads((carpeta / "episodio.json").read_text(encoding="utf-8"))
    if not all(p["ok"] for p in plan.get("puerta") or [{"ok": False}]):
        salida("subir: la puerta no está en verde; no se sube")
        return 1
    if esta_pausada(raiz)[0]:
        salida("subir: PAUSA activa; no se sube")
        return 0
    cfg = seleccion.config(raiz)
    cy = cfg.get("youtube") or {}
    try:
        cli = mod_youtube.Cliente(env)
        canal = cli.canal()
        salida(f"subir: canal {canal['titulo']}")
        vid = cli.subir(carpeta / "episodio.mp4", mod_youtube.snippet_de(plan["titulo"], plan["descripcion"], cy),
                        mod_youtube.status_de(cy))
        st = cli.estado(vid)
        if cy.get("miniatura") and (carpeta / "miniatura.png").is_file():
            try:
                cli.miniatura(vid, carpeta / "miniatura.png")
            except mod_youtube.ErrorYouTube as e:
                salida(f"subir: sin miniatura propia ({e}); ¿falta la verificación del teléfono?")
    except mod_youtube.ErrorYouTube as e:
        salida(f"subir: {e}")
        return 1
    privacidad = (st.get("status") or {}).get("privacyStatus")
    url = f"https://youtu.be/{vid}"
    salida(f"subir: {url} ({privacidad}, {(st.get('status') or {}).get('uploadStatus')})")
    if privacidad != cy.get("privacidad", "public"):
        print(f"::warning::YouTube dejó el episodio en {privacidad}: proyecto sin auditar o revisión pendiente")
    entrada = next((e for e in seleccion.episodios(raiz) if e.get("episodio") == plan["episodio"]), {})
    _guardar_episodio(raiz, {**entrada, "estado": "subido", "video_id": vid, "url": url, "privacidad": privacidad,
                             "subido": datetime.now(ZONA).isoformat(timespec="minutes")})
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.largo")
    sub = ap.add_subparsers(dest="orden", required=True)
    a = sub.add_parser("armar")
    a.add_argument("--desde", type=date.fromisoformat)
    a.add_argument("--hasta", type=date.fromisoformat)
    a.add_argument("--carpeta", type=Path)
    a.add_argument("--sin-memes", action="store_true")
    a.add_argument("--forzar", action="store_true", help="arma aunque haya salido un episodio hace menos de 5 días")
    s = sub.add_parser("subir")
    s.add_argument("--carpeta", type=Path, required=True)
    sub.add_parser("memes")
    args = ap.parse_args(argv)
    raiz = raiz_repo(None)
    if args.orden == "armar":
        carpeta = args.carpeta or Path(tempfile.mkdtemp(prefix="largo-"))
        return armar(raiz, carpeta, args.desde, args.hasta, con_memes=not args.sin_memes, forzar=args.forzar)
    if args.orden == "subir":
        return subir(raiz, args.carpeta)
    cfg_m = seleccion.config(raiz).get("memes") or {}
    cand = mod_memes.populares(list(cfg_m.get("paises") or ["gt"]))[:int(cfg_m.get("max_candidatos", 80))]
    elegidos = mod_memes.filtrar(cand, os.environ.get("GEMINI_API_KEY", ""), mod_memes._modelos(raiz),
                                 int(cfg_m.get("max_biblioteca", 16)))
    for m in elegidos:
        print(f"{m.categoria:9} {m.nombre} ({', '.join(m.paises)}) {m.pagina}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Prueba real de los ojos: `python -m fabrica.editar.prueba_ojos --salida CARPETA`.

Toma la muestra `dialogo-sintel` de `marca/muestras/muestras.yml` (CC BY; sólo muestras), le pide a Gemini el
JSON de verdad, lo valida con nuestro código, renderiza la toma con la traducción de Gemini, corre la puerta
de estilo y deja MP4, PNG, el JSON de Gemini y el informe en la carpeta de salida. No publica nada y la clave
(`GEMINI_API_KEY`) nunca se imprime ni se guarda. Sin clave: lo dice y sale 0 sin hacer nada.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .. import fuentes as mod_fuentes
from .. import ojos
from ..config import cargar_yaml
from ..registro import ZONA, contador_toma
from . import criticos, estilo, muestras, tecnica, toma
from .componer import componer, sondear_video
from .config_ruta import RAIZ
from .marca import Marca

ID_MUESTRA = "dialogo-sintel"
JUEGO_MUESTRA = "Sintel, cortometraje de Blender (no es un videojuego: aquí juego_correcto significa que es Sintel)"

NOTA_MUESTRA = (
    "NOTA PARA ESTA MUESTRA (anula lo anterior sólo en este punto): el video es una muestra autorizada, un "
    "cortometraje con licencia CC BY. Aquí `cinematica` se evalúa como \"escena sin transformación editorial\": un "
    "fragmento de diálogo del corto NO cuenta como cinemática aislada, así que pon `cinematica: false` salvo que "
    "sea una escena sin diálogo ni acción que aporte contexto. El resto de las reglas sigue igual."
)


def _informe_modelos(clave: str, transporte, transporte_lista) -> list[str]:
    """Qué modelos aparecen en ListModels (transporte real, salvo que se inyecte uno). Nunca rompe la prueba."""
    if transporte is not None and transporte_lista is None:
        return []
    try:
        nombres = sorted(m for m in ojos.modelos_disponibles_en_cache(clave, transporte_lista) if m.startswith("gemini-"))
    except ojos.ErrorOjos as e:
        return [f"- ListModels: {e}", ""]
    return [f"- ListModels: {len(nombres)} modelos gemini con generateContent", *[f"  - {n}" for n in nombres], ""]


def _informe_criticos(mp4: Path, plan: dict, marca: Marca, env, transporte, transporte_lista,
                      sin_red: bool = False) -> list[str]:
    """Tarea #21: los críticos con la cadena real (Gemini y, si falla, sin IA) sobre la toma de Sintel. Nunca
    imprime la clave; deja las 3 críticas, el juez, el proveedor de cada una, las peticiones y el tiempo."""
    cfg = criticos.cargar_config(RAIZ)
    if not cfg or cfg.get("activo") is not True:
        return ["## Críticos", "", "- apagados (`config/criticos.yml`: activo: false)"]
    try:
        v = criticos.revisar(mp4, plan, marca, raiz=RAIZ, entorno=env, transporte=transporte, config=cfg,
                             transporte_lista=transporte_lista, modo="sin_ia" if sin_red else None)
    except Exception as e:  # noqa: BLE001 (el informe nunca rompe la prueba de los ojos)
        return ["## Críticos", "", f"- error: {type(e).__name__}: {e}"]
    return ["## Críticos (cadena real)", "", criticos.informe_md("Sintel", v), *[f"- intento: {i}" for i in v.intentos], ""]


def correr(salida: Path, *, entorno=None, transporte=None, transporte_lista=None, transporte_criticos=None) -> int:
    env = os.environ if entorno is None else entorno
    if not env.get("GEMINI_API_KEY"):
        print("sin GEMINI_API_KEY: la prueba de los ojos no hace nada (no se llamó a Gemini).")
        return 0
    marca = Marca.cargar()
    temporada = cargar_yaml("temporada.yml")
    m = next(x for x in muestras.cargar_muestras() if x["id"] == ID_MUESTRA)
    fichas = mod_fuentes.cargar_fuentes()
    hoy = datetime.now(ZONA).date()
    try:
        muestras.autorizar(m, fichas, hoy)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    ficha = next(f for f in fichas if f["id"] == m["fuente_id"])
    salida.mkdir(parents=True, exist_ok=True)
    informe = ["# Prueba de los ojos (Gemini)", "", f"Muestra: {ID_MUESTRA} ({m['licencia']}). No se publica nada.", ""]
    with tempfile.TemporaryDirectory(prefix="ojos-") as tmp:
        tmp = Path(tmp)
        vid = m["video"]
        ruta = muestras.descargar(vid["url"], vid["sha256"], tmp / Path(vid["url"]).name)
        dur_src = sondear_video(ruta)["duracion"]
        informe += _informe_modelos(env["GEMINI_API_KEY"], transporte, transporte_lista)
        copia = toma.copia_para_ojos(ruta, RAIZ, tmp)       # a Gemini va la copia ligera; se compone con el original
        mb = copia.stat().st_size / 1048576
        informe += [f"- Enviado a Gemini: copia ligera: {mb:.1f} MB" + (" (el original ya cabía)" if copia == ruta else ""), ""]
        mirada = ojos.mirar(copia, marca, temporada, duracion=dur_src, entorno=env, transporte=transporte,
                            juego=JUEGO_MUESTRA, nota=NOTA_MUESTRA, transporte_lista=transporte_lista,
                            raiz=RAIZ, registrar_cupos=transporte is None)   # sólo en la corrida real
        informe += [f"- Modelo que respondió: {mirada.modelo}", *[f"- Intento: {i}" for i in mirada.intentos]]
        if mirada.datos is None:
            informe += ["", f"RESULTADO: sin datos de Gemini ({mirada.motivo})."]
            (salida / "informe.md").write_text("\n".join(informe) + "\n", encoding="utf-8")
            print(f"ERROR: {mirada.motivo}", file=sys.stderr)
            return 1
        (salida / "gemini.json").write_text(json.dumps({"modelo": mirada.modelo, "respuesta": mirada.datos},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
        ok, motivos = ojos.validar_respuesta(mirada.datos, marca, temporada, duracion=dur_src, juego=JUEGO_MUESTRA)
        informe += ["", f"## Validación de la respuesta: {'OK' if ok else 'DESCARTE'}", *[f"- {x}" for x in motivos]]
        if not ok:
            (salida / "informe.md").write_text("\n".join(informe) + "\n", encoding="utf-8")
            print("DESCARTE: " + "; ".join(motivos), file=sys.stderr)
            return 1
        t, lineas, inicio, dur = toma.armar(mirada.datos, marca, ficha, contador_toma(), temporada=temporada)
        mp4 = salida / "ojos-sintel.mp4"
        plan_ruta = salida / "ojos-sintel.plan.json"
        with tempfile.TemporaryDirectory(prefix="trabajo-ojos-") as trabajo:
            plan = componer(marca, t, lineas, mp4, duracion=dur, trabajo=Path(trabajo), fuente_video=ruta,
                            inicio=inicio, plan_salida=plan_ruta)
        puntos = (tecnica.revisar(mp4, tecnica.con_originales(plan, lineas), tecnica.cargar(marca.raiz))
                  + estilo.revisar(mp4, plan, marca))
        muestras.fotogramas(mp4, dur, salida, "ojos-sintel")
        verde = all(p[1] for p in puntos)
        informe += ["", estilo.informe_md(f"Puerta de estilo ({'EN VERDE' if verde else 'EN ROJO'})", puntos),
                    "## Traducción de Gemini", ""]
        informe += [f"- {ln.t0:g}-{ln.t1:g} s {ln.nombre}: {ln.frase} (original: {ln.original})" for ln in lineas]
        informe += ["", *_informe_criticos(mp4, plan, marca, env, transporte_criticos, transporte_lista,
                                           sin_red=transporte is not None and transporte_criticos is None)]
    (salida / "informe.md").write_text("\n".join(informe) + "\n", encoding="utf-8")
    print(f"puerta de estilo {'EN VERDE' if verde else 'EN ROJO'}; modelo {mirada.modelo}")
    return 0 if verde else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fabrica.editar.prueba_ojos")
    ap.add_argument("--salida", required=True, help="carpeta de salida, fuera del repo")
    args = ap.parse_args(argv)
    salida = Path(args.salida).resolve()
    if muestras._dentro_del_repo(salida):
        print(f"ERROR: la salida {salida} está dentro del repo.", file=sys.stderr)
        return 2
    return correr(salida)


if __name__ == "__main__":
    sys.exit(main())

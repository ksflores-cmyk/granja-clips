"""Los críticos (tarea #21): tres críticos que no vieron el plan miran el MP4 final y un juez decide.

- `fotogramas` saca 6 JPEG del MP4 (8 % a 92 %); `transcripcion` sólo trae lo que se oye y se lee (las líneas de
  diálogo): del plan editorial no viaja nada (gancho, escena, lápiz, recorte, motivos, historia, geometría).
- Cada crítico (`historia`, `subtitulos`, `tecnica`) tiene su instrucción en `fabrica/criticos_prompt/` y su propia
  llamada a la cadena (`fabrica/cadena.py`); si la cadena no da una crítica válida, lo resuelve el crítico sin IA
  (determinista, sólo con el MP4 y la transcripción). La respuesta la valida el código (`validar_critica`).
- El juez recibe sólo las 3 críticas. La regla (sin IA) es el respaldo y el piso: el juez con modelo no puede dar
  «pasa» donde la regla da «todavía no» por un puntaje de 2 o menos.
- `decidir` lo usa `toma.hacer_toma`: nunca gasta una fuente por fallos de la máquina; un «todavía no» de la
  ronda 1 con corte de Gemini vuelve a los ojos una vez con la nota del juez; el descarte final es del material.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from .. import cadena, plazo
from ..config import cargar_yaml, raiz_repo
from ..registro import ZONA
from . import estilo
from .marca import Marca

NOMBRES = ("historia", "subtitulos", "tecnica")
RUTA_PROMPTS = Path(__file__).resolve().parent.parent / "criticos_prompt"
MAX_LINEAS_JSONL = 300
VEREDICTOS = ("pasa", "todavia_no")
MIN_TIEMPO_REVISION_S = 20.0          # con menos plazo que esto (más el margen) los críticos no corren
PUNTAJE_SIN_IA_PASA, PUNTAJE_SIN_IA_UNA_FALLA, PUNTAJE_SIN_IA_DOS_FALLAS = 4, 3, 2


@dataclass
class Cuadro:
    t: float
    jpeg: bytes


@dataclass
class Critica:
    nombre: str
    proveedor: str                    # gemini | segundo | sin-ia
    modelo: str | None
    veredicto: str
    puntaje: int
    motivos: list[str]
    arreglo: str

    def como_dict(self) -> dict:
        return {"nombre": self.nombre, "proveedor": self.proveedor, "veredicto": self.veredicto,
                "puntaje": self.puntaje, "motivos": list(self.motivos)}


@dataclass
class Veredicto:
    decision: str | None              # "pasa" | "todavia_no" | None (los críticos no pudieron correr)
    motivos: list[str] = field(default_factory=list)
    para_el_plan: str = ""
    criticas: list[Critica] = field(default_factory=list)
    juez: dict = field(default_factory=dict)          # {proveedor, decision}
    llamadas: dict = field(default_factory=dict)      # peticiones gastadas por proveedor
    segundos: float = 0.0
    motivo: str | None = None                         # por qué no hubo decisión
    intentos: list[str] = field(default_factory=list)


@dataclass
class Decision:
    accion: str                       # "seguir" | "replantear" | "descartar"
    bloque: dict | None = None        # lo que se guarda en plan["criticos"]
    motivos: list[str] = field(default_factory=list)
    nota: str = ""
    segundos: float = 0.0


# ------------------------------------------------------------------ lo que ven los críticos

def fotogramas(mp4, n: int = 6, lado_max: int = 512, duracion: float | None = None) -> list[Cuadro]:
    """`n` fotogramas JPEG del MP4, entre el 8 % y el 92 % de la duración, lado mayor <= `lado_max`.
    Una sola orden de ffmpeg por cuadro, con el tiempo que deja el plazo del ciclo."""
    mp4 = Path(mp4)
    dur = duracion if duracion is not None else estilo.sondear(mp4)["duracion"]
    tiempos = [dur * (0.08 + 0.84 * i / max(n - 1, 1)) for i in range(n)]
    cuadros = []
    for t in tiempos:
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(mp4),
                            "-frames:v", "1", "-vf", f"scale={lado_max}:{lado_max}:force_original_aspect_ratio=decrease",
                            "-q:v", "4", "-f", "image2pipe", "-c:v", "mjpeg", "-"],
                           capture_output=True, check=True, timeout=plazo.tiempo_para(60))
        if not r.stdout:
            raise RuntimeError(f"ffmpeg no sacó el fotograma de {t:.1f} s")
        cuadros.append(Cuadro(round(t, 3), r.stdout))
    return cuadros


def transcripcion(plan: dict) -> list[dict]:
    """Sólo lo que se oye y se lee: las líneas de diálogo del plan (tiempo, nombre, frase). Una acotación sin
    frase se lee en pantalla y entra como texto. Nada del plan editorial."""
    sal = []
    for ln in plan.get("lineas") or []:
        if not isinstance(ln, dict):
            continue
        texto = ln.get("frase") or ln.get("acotacion")
        if not texto:
            continue
        sal.append({"t0": round(float(ln.get("t0", 0.0)), 2), "t1": round(float(ln.get("t1", 0.0)), 2),
                    "quien": ln.get("nombre") if ln.get("frase") else "ACOTACIÓN", "texto": str(texto)})
    return sal


def quejas_de_kevin(raiz=None) -> list[str]:
    """Notas de `reglas-kevin.yml` (si existe) con `estado: vigente` y `revision: null`. Sin el archivo, nada."""
    ruta = raiz_repo(raiz) / "reglas-kevin.yml"
    if not ruta.is_file():
        return []
    try:
        d = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    reglas = d.get("reglas") if isinstance(d, dict) else d
    notas = []
    for r in reglas if isinstance(reglas, list) else []:
        if isinstance(r, dict) and r.get("estado") == "vigente" and r.get("revision") is None:
            t = next((r[k] for k in ("texto", "regla", "queja", "descripcion", "nota") if isinstance(r.get(k), str)), None)
            if t and t.strip():
                notas.append(" ".join(t.split()))
    return notas


def _zonas(marca: Marca) -> dict:
    pc = marca.datos["pantalla_completa"]
    return {"segura": pc["zona_segura"], "accion": pc["zona_accion"],
            "columna": marca.datos["maqueta"]["columna_dialogo"]}


def instruccion(nombre: str, marca: Marca, trans: list[dict], n: int, cfg: dict, raiz=None) -> str:
    """La instrucción del crítico: su archivo `.md`, la transcripción y las quejas de Kevin. Sin plan editorial."""
    z = _zonas(marca)
    quejas = quejas_de_kevin(raiz)
    texto = (RUTA_PROMPTS / f"{nombre}.md").read_text(encoding="utf-8")
    cambios = {
        "{{N}}": str(n),
        "{{TRANSCRIPCION}}": json.dumps(trans, ensure_ascii=False) if trans else "(sin diálogo)",
        "{{QUEJAS}}": ("\nQuejas de Kevin que no mide el código (tenlas en cuenta):\n" + "\n".join(f"- {q}" for q in quejas))
        if quejas else "",
        "{{CPS}}": f"{(cfg.get('sin_ia') or {}).get('subtitulos', {}).get('max_cps', 17):g}",
        "{{ZONA_UTIL}}": f"x de {z['segura']['x0']} a {z['segura']['x1']}, y de {z['segura']['y0']} a {z['segura']['y1']}; "
                         f"el diálogo va en la columna x de {z['columna'][0]} a {z['columna'][1]}",
        "{{X_BOTONES}}": str(z["columna"][1]), "{{Y_ACCION}}": str(z["accion"]["y0"]),
        "{{Y_SEGURA}}": str(z["segura"]["y1"]),
    }
    for k, v in cambios.items():
        texto = texto.replace(k, v)
    return texto + "\nResponde SOLO con el JSON pedido, en español."


# ------------------------------------------------------------------ validación (código nuestro, no del modelo)

def _entero(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def validar_critica(d) -> list[str]:
    """Errores de una crítica. Vacía = válida. Clave extra, tipo erróneo, fuera de rango o incoherente = inválida."""
    if not isinstance(d, dict):
        return ["la crítica no es un objeto"]
    e = []
    extra = sorted(set(d) - {"veredicto", "puntaje", "motivos", "arreglo"})
    if extra:
        e.append(f"claves de más: {', '.join(map(str, extra))}")
    ver, pun = d.get("veredicto"), d.get("puntaje")
    if ver not in VEREDICTOS or not isinstance(ver, str):
        e.append("veredicto debe ser pasa o todavia_no")
    if not _entero(pun) or not 1 <= pun <= 5:
        e.append("puntaje debe ser un entero de 1 a 5")
    mot = d.get("motivos")
    if not (isinstance(mot, list) and len(mot) <= 4 and all(isinstance(m, str) and len(m) <= 200 for m in mot)):
        e.append("motivos debe ser una lista de hasta 4 textos de 200 caracteres como máximo")
    arr = d.get("arreglo")
    if not (isinstance(arr, str) and len(arr) <= 200):
        e.append("arreglo debe ser un texto de 200 caracteres como máximo")
    if not e:
        if ver == "pasa" and pun <= 2:
            e.append("incoherente: pasa con puntaje de 2 o menos")
        if ver == "todavia_no" and pun >= 4:
            e.append("incoherente: todavia_no con puntaje de 4 o más")
    return e


def validar_juez(d) -> list[str]:
    if not isinstance(d, dict):
        return ["la decisión del juez no es un objeto"]
    e = []
    extra = sorted(set(d) - {"decision", "motivos", "para_el_plan"})
    if extra:
        e.append(f"claves de más: {', '.join(map(str, extra))}")
    if not isinstance(d.get("decision"), str) or d.get("decision") not in VEREDICTOS:
        e.append("decision debe ser pasa o todavia_no")
    mot = d.get("motivos")
    if not (isinstance(mot, list) and 1 <= len(mot) <= 4 and all(isinstance(m, str) and len(m) <= 200 for m in mot)):
        e.append("motivos debe ser una lista de 1 a 4 textos de 200 caracteres como máximo")
    p = d.get("para_el_plan")
    if not (isinstance(p, str) and len(p) <= 300):
        e.append("para_el_plan debe ser un texto de 300 caracteres como máximo")
    return e


def esquema_critica() -> dict:
    return {"type": "OBJECT", "properties": {
        "veredicto": {"type": "STRING", "enum": list(VEREDICTOS)},
        "puntaje": {"type": "INTEGER"},
        "motivos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "arreglo": {"type": "STRING"}},
        "required": ["veredicto", "puntaje", "motivos", "arreglo"]}


def esquema_juez() -> dict:
    return {"type": "OBJECT", "properties": {
        "decision": {"type": "STRING", "enum": list(VEREDICTOS)},
        "motivos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "para_el_plan": {"type": "STRING"}},
        "required": ["decision", "motivos", "para_el_plan"]}


# ------------------------------------------------------------------ medidas sin IA (deterministas)

def _imagen(c: Cuadro) -> Image.Image:
    return Image.open(io.BytesIO(c.jpeg)).convert("RGB")


def _gris(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float64)


def nitidez(gris: np.ndarray) -> float:
    """Varianza del laplaciano (núcleo 4-vecinos) de una imagen en gris."""
    if gris.shape[0] < 3 or gris.shape[1] < 3:
        return 0.0
    c = gris[1:-1, 1:-1]
    lap = gris[:-2, 1:-1] + gris[2:, 1:-1] + gris[1:-1, :-2] + gris[1:-1, 2:] - 4 * c
    return float(lap.var())


def cambio_medio(a: Image.Image, b: Image.Image) -> float:
    """Diferencia media absoluta (0-255) entre miniaturas grises de 32 x 56."""
    ga = np.asarray(a.convert("L").resize((32, 56), Image.BILINEAR), dtype=np.float64)
    gb = np.asarray(b.convert("L").resize((32, 56), Image.BILINEAR), dtype=np.float64)
    return float(np.abs(ga - gb).mean())


def volumen(mp4, hasta_s: float | None = None) -> tuple[float | None, float | None]:
    """(volumen medio dB, pico dB) con volumedetect; (None, None) si no hay audio. -inf = silencio digital."""
    orden = ["ffmpeg", "-hide_banner", "-nostats"] + (["-t", f"{hasta_s:g}"] if hasta_s else []) + \
            ["-i", str(mp4), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(orden, capture_output=True, text=True, timeout=plazo.tiempo_para(120))

    def leer(clave):
        m = re.search(rf"{clave}:\s*(-?[0-9.]+|-inf)\s*dB", r.stderr)
        return None if not m else (float("-inf") if m.group(1) == "-inf" else float(m.group(1)))
    return leer("mean_volume"), leer("max_volume")


def _sin_audio_propio(plan: dict) -> bool:
    a = plan.get("audio")
    return isinstance(a, dict) and a.get("tipo") == "silencio"


def _critica_sin_ia(nombre: str, fallas: list[str]) -> Critica:
    if not fallas:
        return Critica(nombre, "sin-ia", None, "pasa", PUNTAJE_SIN_IA_PASA, [], "")
    p = PUNTAJE_SIN_IA_UNA_FALLA if len(fallas) == 1 else PUNTAJE_SIN_IA_DOS_FALLAS
    return Critica(nombre, "sin-ia", None, "todavia_no", p, [f[:200] for f in fallas[:4]],
                   ("; ".join(f.split(":")[0] for f in fallas))[:200])


def sin_ia_historia(mp4, plan: dict, marca: Marca, cuadros: list[Cuadro], cfg: dict) -> Critica:
    u = (cfg.get("sin_ia") or {}).get("historia") or {}
    fallas = []
    if not _sin_audio_propio(plan):
        _, pico = volumen(mp4, float(u.get("audio_inicio_s", 2)))
        if pico is None or pico < float(u.get("silencio_db", -50)):
            fallas.append(f"sin gancho inmediato: los primeros {u.get('audio_inicio_s', 2):g} s son silencio")
    imgs = [_imagen(c) for c in cuadros]
    pares = [cambio_medio(a, b) for a, b in zip(imgs, imgs[1:])]
    con_cambio = sum(1 for x in pares if x >= float(u.get("cambio_min", 2.0)))
    if pares and con_cambio < float(u.get("min_pares_con_cambio", 0.5)) * len(pares):
        fallas.append(f"ritmo: sólo {con_cambio} de {len(pares)} pares de fotogramas cambian (imagen casi fija)")
    return _critica_sin_ia("historia", fallas)


def papel_en_botones(cuadro: Cuadro, marca: Marca, tolerancia: int) -> float:
    """Fracción de píxeles de color de papel de marca en la franja de botones (abajo de `zona_segura.y1` y a la
    derecha de `columna_dialogo[1]`, entre `zona_accion.y0` y `zona_segura.y1`)."""
    z = _zonas(marca)
    img = _imagen(cuadro)
    a = np.asarray(img, dtype=np.int16)
    h, w = a.shape[:2]
    s = w / float(marca.ancho)
    y_seg, y_acc, x_col = int(z["segura"]["y1"] * s), int(z["accion"]["y0"] * s), int(z["columna"][1] * s)
    papel = np.array([int(marca.datos["colores"]["papel"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)
    mascara = np.zeros((h, w), dtype=bool)
    mascara[y_seg:, :] = True
    mascara[y_acc:y_seg, x_col:] = True
    es_papel = (np.abs(a - papel) <= tolerancia).all(axis=2)
    total = int(mascara.sum())
    return float((es_papel & mascara).sum()) / total if total else 0.0


def sin_ia_subtitulos(mp4, plan: dict, marca: Marca, cuadros: list[Cuadro], cfg: dict) -> Critica:
    u = (cfg.get("sin_ia") or {}).get("subtitulos") or {}
    fallas = []
    tope = float(u.get("max_cps", 17))
    rapidas = []
    for ln in transcripcion(plan):
        dur = ln["t1"] - ln["t0"]
        if dur <= 0 or len(ln["texto"]) / dur > tope:
            rapidas.append(f"«{ln['texto'][:30]}» ({len(ln['texto'])} caracteres en {max(dur, 0):.1f} s)")
    if rapidas:
        fallas.append(f"lectura: más de {tope:g} caracteres por segundo en {len(rapidas)} línea(s): {rapidas[0]}")
    tol, maxp = int(u.get("tolerancia_papel", 10)), float(u.get("max_papel_en_botones", 0.06))
    peor = max((papel_en_botones(c, marca, tol) for c in cuadros), default=0.0)
    if peor > maxp:
        fallas.append(f"botones: {peor:.0%} de la franja de los botones de la app es papel de marca (máximo {maxp:.0%})")
    return _critica_sin_ia("subtitulos", fallas)


def sin_ia_tecnica(mp4, plan: dict, marca: Marca, cuadros: list[Cuadro], cfg: dict) -> Critica:
    u = (cfg.get("sin_ia") or {}).get("tecnica") or {}
    fallas = []
    grises = [_gris(_imagen(c)) for c in cuadros]
    medias = [float(g.mean()) for g in grises]
    negros = [c.t for c, m in zip(cuadros, medias) if m < float(u.get("negro_max", 12))]
    blancos = [c.t for c, m in zip(cuadros, medias) if m > float(u.get("blanco_min", 248))]
    if negros or blancos:
        fallas.append("fallas visibles: fotograma casi " + ("negro" if negros else "blanco")
                      + f" entero en {(negros or blancos)[0]:.1f} s")
    nit = [nitidez(g) for g in grises]
    nitidos = sum(1 for x in nit if x >= float(u.get("min_nitidez", 15.0)))
    if nitidos < int(u.get("min_cuadros_nitidos", 4)):
        fallas.append(f"encuadre: sólo {nitidos} de {len(cuadros)} fotogramas son nítidos")
    if not _sin_audio_propio(plan):
        medio, _ = volumen(mp4)
        lo, hi = float(u.get("vol_min_db", -45)), float(u.get("vol_max_db", -3))
        if medio is None or not lo <= medio <= hi:
            fallas.append("sonido: volumen medio " + ("ausente" if medio is None else f"de {medio:.0f} dB")
                          + f" fuera de {lo:g} a {hi:g} dB")
    return _critica_sin_ia("tecnica", fallas)


SIN_IA = {"historia": sin_ia_historia, "subtitulos": sin_ia_subtitulos, "tecnica": sin_ia_tecnica}


# ------------------------------------------------------------------ el juez

def juez_regla(criticas: list[Critica]) -> tuple[str, list[str], str, bool]:
    """(decision, motivos, para_el_plan, por_piso). «Todavía no» si algún crítico dio todavia_no con puntaje <= 2,
    o si 2 o más dieron todavia_no. `por_piso` es True si lo causa un puntaje <= 2 (el juez con modelo no lo cambia)."""
    no = [c for c in criticas if c.veredicto == "todavia_no"]
    piso = any(c.puntaje <= 2 for c in no)
    if piso or len(no) >= 2:
        motivos = [f"{c.nombre}: {m}" for c in no for m in (c.motivos or ["sin motivo escrito"])][:4]
        arreglo = " ".join(c.arreglo.strip() for c in no if c.arreglo.strip())[:300]
        return "todavia_no", motivos, arreglo, piso
    return "pasa", [], "", False


def _texto_criticas(criticas: list[Critica]) -> str:
    return json.dumps([{"nombre": c.nombre, "proveedor": c.proveedor, "veredicto": c.veredicto, "puntaje": c.puntaje,
                        "motivos": c.motivos, "arreglo": c.arreglo} for c in criticas], ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ la revisión

def revisar(mp4, plan: dict, marca: Marca, *, raiz=None, entorno=None, transporte=None, espera=time.sleep,
            config: dict | None = None, modo: str | None = None, transporte_lista=None, cuadros=None,
            ahora=None) -> Veredicto:
    """Pasa el MP4 final por los tres críticos y el juez. Nunca lanza por un fallo de IA: lo que la cadena no
    resuelva lo resuelve el crítico sin IA. `SinTiempo` y los errores de ffmpeg suben a quien llama."""
    cfg = config if config is not None else cargar_yaml("config/criticos.yml", raiz)
    inicio = time.monotonic()
    v = Veredicto(None)
    tope = float(cfg.get("tope_s", 120))
    libre = plazo.restante() - plazo.MARGEN_S
    if libre < MIN_TIEMPO_REVISION_S:
        v.motivo = f"sin tiempo de plazo para los críticos (quedan {max(libre, 0):.0f} s)"
        return v
    tope = min(tope, libre)
    modo = modo or cfg.get("modo", "cadena")
    fg = cfg.get("fotogramas") or {}
    cuadros = cuadros if cuadros is not None else fotogramas(mp4, int(fg.get("n", 6)), int(fg.get("lado_max", 512)))
    trans = transcripcion(plan)
    imagenes = [c.jpeg for c in cuadros]
    caida = modo == "sin_ia"                  # cuando la cadena falla una vez, los demás van directo a sin IA
    llamadas: dict = {}

    def restante() -> float:
        return tope - (time.monotonic() - inicio)

    def pedir(instr, imgs, esquema, validar):
        nonlocal caida
        if caida or restante() < cadena.MINIMO_LLAMADA_S + 1:
            return None
        r = cadena.pedir_json(instr, imgs, esquema, raiz=raiz, entorno=entorno, transporte=transporte,
                              plazo_s=restante(), validar=validar, espera=espera, transporte_lista=transporte_lista,
                              config=cfg, ahora=ahora)
        for k, n in r.peticiones.items():
            llamadas[k] = llamadas.get(k, 0) + n
        v.intentos += r.intentos
        if r.datos is None:
            caida = True
            return None
        return r

    for nombre in NOMBRES:
        r = pedir(instruccion(nombre, marca, trans, len(cuadros), cfg, raiz), imagenes, esquema_critica(), validar_critica)
        if r is not None:
            d = r.datos
            v.criticas.append(Critica(nombre, r.proveedor, r.modelo, d["veredicto"], d["puntaje"],
                                      list(d["motivos"]), d["arreglo"]))
        else:
            v.criticas.append(SIN_IA[nombre](mp4, plan, marca, cuadros, cfg))
    decision, motivos, arreglo, piso = juez_regla(v.criticas)
    juez = {"proveedor": "regla", "decision": decision}
    if (cfg.get("juez") or {}).get("con_modelo") and not caida:
        instr = (RUTA_PROMPTS / "juez.md").read_text(encoding="utf-8").replace("{{CRITICAS}}", _texto_criticas(v.criticas))
        r = pedir(instr, [], esquema_juez(), validar_juez)
        if r is not None:
            d = r.datos
            if piso and d["decision"] == "pasa":          # piso de seguridad: manda la regla
                juez = {"proveedor": "regla (piso)", "decision": decision}
            else:
                decision, motivos, arreglo = d["decision"], list(d["motivos"]), d["para_el_plan"]
                juez = {"proveedor": r.proveedor, "decision": decision}
    v.decision, v.motivos, v.para_el_plan, v.juez = decision, motivos, arreglo, juez
    v.llamadas = llamadas
    v.segundos = round(time.monotonic() - inicio, 2)
    return v


def bloque_plan(v: Veredicto, ronda: int) -> dict:
    return {"ronda": ronda, "decision": v.decision, "motivos": list(v.motivos),
            "criticas": [c.como_dict() for c in v.criticas], "juez": dict(v.juez),
            "llamadas": dict(v.llamadas), "segundos": v.segundos}


def informe_md(titulo: str, v: Veredicto, llamadas_y_tiempo: bool = True) -> str:
    """Las 3 críticas y el juez en Markdown (para `puerta-estilo-auto.md` y el informe de `ojos-prueba`)."""
    if v.decision is None:
        return f"### Críticos: {titulo} (SIN DECISIÓN)\n\n- {v.motivo}\n"
    sal = [f"### Críticos: {titulo} ({'PASA' if v.decision == 'pasa' else 'TODAVÍA NO'})", ""]
    for c in v.criticas:
        modelo = f", {c.modelo}" if c.modelo else ""
        sal.append(f"- {c.nombre} [{c.proveedor}{modelo}]: {c.veredicto}, puntaje {c.puntaje}"
                   + (f"; {' / '.join(c.motivos)}" if c.motivos else "") + (f"; arreglo: {c.arreglo}" if c.arreglo else ""))
    sal.append(f"- juez [{v.juez.get('proveedor')}]: {v.decision}"
               + (f"; {' / '.join(v.motivos)}" if v.motivos else "") + (f"; para el plan: {v.para_el_plan}" if v.para_el_plan else ""))
    if llamadas_y_tiempo:
        sal.append(f"- peticiones gastadas: {v.llamadas or 0}; tiempo: {v.segundos:.1f} s")
    return "\n".join(sal) + "\n"


def anotar(raiz, fuente, ronda: int, v: Veredicto, ahora=None) -> None:
    """Una línea en estado/criticos.jsonl (se conservan las últimas 300). Nunca rompe la toma."""
    try:
        ruta = raiz_repo(raiz) / "estado" / "criticos.jsonl"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        previas = ruta.read_text(encoding="utf-8").splitlines() if ruta.exists() else []
        linea = {"fecha": (ahora or datetime.now(ZONA)).isoformat(timespec="seconds"), "fuente": str(fuente),
                 "ronda": ronda, "decision": v.decision, "motivos": list(v.motivos),
                 "proveedores": sorted(({c.proveedor for c in v.criticas} | {v.juez.get("proveedor", "")}) - {""})}
        previas.append(json.dumps(linea, ensure_ascii=False))
        ruta.write_text("\n".join(previas[-MAX_LINEAS_JSONL:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def cargar_config(raiz=None) -> dict | None:
    """`config/criticos.yml` o None si falta o está roto (los críticos entonces no corren)."""
    try:
        c = cargar_yaml("config/criticos.yml", raiz)
    except (OSError, yaml.YAMLError):
        return None
    return c if isinstance(c, dict) else None


def decidir(raiz, mp4, plan: dict, marca: Marca, *, ronda: int = 1, con_corte_gemini: bool = True, fuente="",
            entorno=None, transporte=None, espera=time.sleep, modo: str | None = None, transporte_lista=None,
            config: dict | None = None, ahora=None) -> Decision:
    """Lo que `hacer_toma` necesita saber: seguir (con o sin bloque), replantear (ronda 1, corte de Gemini) o
    descartar. Los críticos nunca gastan una fuente por la máquina: sin tiempo o error inesperado = seguir."""
    try:
        cfg = config if config is not None else cargar_yaml("config/criticos.yml", raiz)
    except (OSError, yaml.YAMLError):
        return Decision("seguir")
    if not isinstance(cfg, dict) or cfg.get("activo") is not True or not plan.get("origen"):
        return Decision("seguir")
    try:
        with plazo.etapa("críticos"):
            v = revisar(mp4, plan, marca, raiz=raiz, entorno=entorno, transporte=transporte, espera=espera,
                        config=cfg, modo=modo, transporte_lista=transporte_lista, ahora=ahora)
    except plazo.SinTiempo:
        raise
    except Exception as e:  # noqa: BLE001 (un error de los críticos nunca descarta ni gasta la fuente)
        v = Veredicto(None, motivo=f"error de los críticos: {type(e).__name__}: {e}")
    if v.decision is None:
        print(f"AVISO: críticos sin decisión ({v.motivo}); la toma sale como si no existieran")
        return Decision("seguir", {"decision": None, "motivo": v.motivo})
    plazo.log(f"críticos: ronda {ronda}, decisión {v.decision}, {v.segundos:.1f} s, peticiones {v.llamadas or 0}")
    bloque = bloque_plan(v, ronda)
    anotar(raiz, fuente, ronda, v, ahora)
    if v.decision == "pasa":
        return Decision("seguir", bloque, segundos=v.segundos)
    if ronda == 1 and con_corte_gemini:
        nota = "Correcciones del juez: " + (v.para_el_plan or "; ".join(v.motivos)) + \
               ". Sólo son una guía: las reglas de siempre mandan."
        return Decision("replantear", bloque, list(v.motivos), nota, v.segundos)
    return Decision("descartar", bloque, ["críticos: " + m for m in v.motivos] or ["críticos: todavía no"],
                    segundos=v.segundos)

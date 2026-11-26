"""Placas de papel de la plantilla v2 (pantalla completa): la marca va encima del clip, ajustada al texto.

Cada placa es un recorte de papel opaco (el color `papel` de marca.yml, con el mismo grano de la página) con tinta y
Courier Prime. Todo sale de `marca.yml` (clave `pantalla_completa`): zona segura, zona de acción, tamaños, posiciones
y tiempos. La puerta de estilo reconstruye las mismas placas desde el plan y las compara con los fotogramas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import agua as mod_agua
from . import dialogo as mod_dialogo
from . import lapiz
from . import lienzo
from .lienzo import PUNTUACION, Toma, _limpia, partir
from .marca import Marca

TIPOS = ("cabecera", "alerta", "fila", "mosca", "dialogo", "credito", "cierre", "marca_agua")


@dataclass
class Placa:
    tipo: str
    x: int
    y: int
    t0: float
    t1: float
    imagen: Image.Image
    regiones: dict = field(default_factory=dict)      # nombre -> (x0, y0, x1, y1) absolutas (cierre: CORTE. y crédito)

    @property
    def ancho(self) -> int:
        return self.imagen.width

    @property
    def alto(self) -> int:
        return self.imagen.height

    def caja(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.ancho, self.y + self.alto)

    def capa(self) -> dict:
        """Entrada de `geometria.capas` del plan."""
        return {"tipo": self.tipo, "x": int(self.x), "y": int(self.y), "ancho": int(self.ancho),
                "alto": int(self.alto), "t0": round(float(self.t0), 3), "t1": round(float(self.t1), 3)}


def lado_izquierdo(marca: Marca) -> bool:
    """True si el encabezado va a la izquierda (la mosca, a la derecha); si la mosca va a la izquierda, se espeja."""
    return marca.mosca["esquina"] != "superior_izquierda"


def _lineas_dialogo(linea) -> tuple:
    if isinstance(linea, dict):
        return (linea.get("nombre"), linea.get("frase"), linea.get("acotacion"), float(linea["t0"]), float(linea["t1"]),
                linea.get("voz"))
    return linea.nombre, linea.frase, linea.acotacion, float(linea.t0), float(linea.t1), getattr(linea, "voz", None)


def _par(n: float) -> int:
    """Entero par por arriba: ffmpeg redondea a par la posición de una capa en yuv420, así que medidas y posiciones
    de las placas van en pares para que el video las ponga exactamente donde el plan dice."""
    n = int(-(-n // 1))
    return n + n % 2


def _par_abajo(n: float) -> int:
    n = int(n // 1)
    return n - n % 2


def _alto_fuente(f) -> tuple[int, int]:
    a, d = f.getmetrics()
    return int(a), int(d)


def _x_placa(marca: Marca, ancho: int) -> int:
    seg = marca.pc["zona_segura"]
    return int(seg["x0"]) if lado_izquierdo(marca) else int(seg["x1"]) - int(ancho)


# ---------------------------------------------------------------- fila mínima: TOMA nnn y crédito

def placa_fila(marca: Marca, t: Toma, duracion: float, y: int | None = None) -> Placa:
    cfg, rel = marca.pc["fila"], int(marca.pc["relleno_placa"])
    f1, f2 = marca.fuente("bold", cfg["tam_toma"]), marca.fuente("regular", cfg["tam_credito"])
    txt1, txt2 = f"TOMA {t.numero:03d}", t.credito.strip()
    a1, d1 = _alto_fuente(f1)
    a2, d2 = _alto_fuente(f2)
    ancho = f1.getlength(txt1) + (cfg["separacion"] + f2.getlength(txt2) if txt2 else 0)
    w, h = _par(ancho + 2 * rel), _par(max(a1, a2) + max(d1, d2) + 2 * rel)
    if w > marca.pc["ancho_max_encabezado"]:
        raise ValueError(f"la fila de crédito mide {w} px y caben {marca.pc['ancho_max_encabezado']}: {txt2!r}")
    x = _x_placa(marca, w)
    y = int(marca.pc["zona_segura"]["y0"]) if y is None else int(y)
    img = lienzo.papel_region(marca, x, y, w, h)
    d = ImageDraw.Draw(img)
    base = rel + max(a1, a2)
    tinta = marca.color("tinta")
    d.text((rel, base), txt1, font=f1, fill=tinta, anchor="ls")
    if txt2:
        d.text((rel + f1.getlength(txt1) + cfg["separacion"], base), txt2, font=f2, fill=tinta, anchor="ls")
    return Placa("fila", x, y, 0.0, float(duracion), img)


# ---------------------------------------------------------------- encabezado: gancho con lápiz y escena

def preparar_gancho(marca: Marca, gancho: str, palabra_lapiz: str) -> list[str]:
    """Líneas del gancho (mayúsculas) a la medida de la placa. ValueError con el motivo si no valen."""
    cfg, rel = marca.pc["cabecera"], int(marca.pc["relleno_placa"])
    palabras = gancho.split()
    if not palabras:
        raise ValueError("el gancho está vacío")
    if len(palabras) > marca.max_palabras_gancho:
        raise ValueError(f"gancho de {len(palabras)} palabras (máximo {marca.max_palabras_gancho}): {gancho!r}")
    ancho_texto = marca.pc["ancho_max_encabezado"] - 2 * rel - 2 * marca.pc["lapiz"]["reserva"]
    lineas = partir(gancho.upper(), marca.fuente("bold", cfg["tam_gancho"]), ancho_texto)
    if len(lineas) > 2:
        raise ValueError(f"el gancho no cabe en 2 líneas: {gancho!r}")
    objetivo = _limpia(palabra_lapiz)
    if not objetivo or objetivo not in [_limpia(p) for p in palabras]:
        raise ValueError(f"la palabra del lápiz {palabra_lapiz!r} no está en el gancho {gancho!r}")
    return lineas


def placa_cabecera(marca: Marca, t: Toma, y: int, t1: float) -> Placa:
    cfg, rel = marca.pc["cabecera"], int(marca.pc["relleno_placa"])
    lineas = preparar_gancho(marca, t.gancho, t.palabra_lapiz)
    fg = marca.fuente("bold", cfg["tam_gancho"])
    fe = marca.fuente("regular", cfg["tam_escena"])
    medida = Image.new("L", (4, 4))
    dm = ImageDraw.Draw(medida)
    objetivo = _limpia(t.palabra_lapiz)
    caja = None
    for i, linea in enumerate(lineas):
        if caja is not None:
            break
        cursor = 0
        for palabra in linea.split(" "):
            if _limpia(palabra) == objetivo:
                nucleo = palabra.strip(PUNTUACION)
                x0 = fg.getlength(linea[:cursor + palabra.find(nucleo)])
                bb = dm.textbbox((x0, i * cfg["alto_linea_gancho"]), nucleo, font=fg)
                caja = (x0, bb[1], x0 + fg.getlength(nucleo), bb[3])
                break
            cursor += len(palabra) + 1
    x0, y0, x1, y1 = caja
    pcm = marca.pc_marca()
    tr = lapiz.trazo_para(pcm, t.lapiz, t.numero, x1 - x0, y1 - y0)
    if lapiz.normalizar_tipo(t.lapiz) == "subrayado":
        izq, bajo = tr.info["sobresale_izq"], pcm.m("subrayado")["bajo_palabra"]
        pos = (int(round(x0 - izq - lapiz.RELLENO)), int(round(y1 + bajo - tr.height / 2)))
    else:
        pos = (int(round((x0 + x1) / 2 - tr.width / 2)), int(round((y0 + y1) / 2 - tr.height / 2)))
    alto_texto = len(lineas) * cfg["alto_linea_gancho"]
    ancho_texto = max(fg.getlength(ln) for ln in lineas)
    if t.escena:
        escena = t.escena.upper()
        alto_texto += cfg["espacio_escena"] + cfg["alto_escena"]
        ancho_texto = max(ancho_texto, fe.getlength(escena))
    # La placa envuelve el texto y el trazo del lápiz (que se puede asomar un poco).
    ux0, uy0 = min(0, pos[0]), min(0, pos[1])
    ux1, uy1 = max(ancho_texto, pos[0] + tr.width), max(alto_texto, pos[1] + tr.height)
    w, h = _par(ux1 - ux0 + 2 * rel), _par(uy1 - uy0 + 2 * rel)
    if w > marca.pc["ancho_max_encabezado"]:
        raise ValueError(f"el encabezado mide {w} px y caben {marca.pc['ancho_max_encabezado']}: {t.gancho!r}")
    ox, oy = rel - ux0, rel - uy0
    px = _x_placa(marca, w)
    img = lienzo.papel_region(marca, px, int(y), w, h)
    d = ImageDraw.Draw(img)
    tinta = marca.color("tinta")
    for i, linea in enumerate(lineas):
        d.text((ox, oy + i * cfg["alto_linea_gancho"]), linea, font=fg, fill=tinta)
    if t.escena:
        d.text((ox, oy + len(lineas) * cfg["alto_linea_gancho"] + cfg["espacio_escena"]), t.escena.upper(),
               font=fe, fill=tinta)
    img.alpha_composite(tr, (int(round(ox + pos[0])), int(round(oy + pos[1]))))
    return Placa("cabecera", px, int(y), 0.0, float(t1), img)


# ---------------------------------------------------------------- avisos al inicio (spoiler, cinemática, mods)

def texto_aviso(marca: Marca, avisos: list[str]) -> str:
    """Texto de la placa: el de cada aviso en `marca.yml` (`avisos.textos`), en orden fijo y unidos con « · ». Del segundo
    en adelante se quita la palabra inicial que comparten con el primero («ALERTA SPOILER · CINEMÁTICA · MODS»)."""
    textos = marca.avisos["textos"]
    partes = [str(textos[a]).strip().upper() for a in ("spoiler", "cinematica", "mods") if a in avisos]
    if not partes:
        raise ValueError("no hay avisos que dibujar")
    inicio = partes[0].split(" ", 1)[0] + " "
    return " · ".join([partes[0]] + [p[len(inicio):] if p.startswith(inicio) else p for p in partes[1:]])


def placa_aviso(marca: Marca, avisos: list[str], y: int, t1: float) -> Placa:
    """Aviso simple al inicio: placa de papel con el texto (`texto_aviso`) en Courier Prime negrita al tamaño de la fila
    (pequeño: que se vea poco) y color lapiz_rojo, en el lugar del encabezado (bajo la fila) y de 0 a `t1` s. Corte seco."""
    rel = int(marca.pc["relleno_placa"])
    txt = texto_aviso(marca, avisos)
    f = marca.fuente("bold", marca.pc["fila"]["tam_toma"])
    a, d = _alto_fuente(f)
    w, h = _par(f.getlength(txt) + 2 * rel), _par(a + d + 2 * rel)
    if w > marca.pc["ancho_max_encabezado"]:
        raise ValueError(f"el aviso mide {w} px y caben {marca.pc['ancho_max_encabezado']}: {txt!r}")
    x = _x_placa(marca, w)
    img = lienzo.papel_region(marca, x, int(y), w, h)
    ImageDraw.Draw(img).text((rel, rel + a), txt, font=f, fill=marca.color("lapiz_rojo"), anchor="ls")
    return Placa("alerta", x, int(y), 0.0, float(t1), img)


# ---------------------------------------------------------------- mosca

def placa_mosca(marca: Marca, duracion: float) -> Placa:
    cfg, rel = marca.pc["mosca"], int(marca.pc["mosca"]["relleno"])
    f = marca.fuente("bold", cfg["tam"])
    txt = marca.mosca["texto"]
    a, d = _alto_fuente(f)
    w, h = _par(f.getlength(txt) + 2 * rel), _par(a + d + 2 * rel)
    seg = marca.pc["zona_segura"]
    x = int(seg["x1"]) - w if lado_izquierdo(marca) else int(seg["x0"])
    y = int(seg["y0"])
    img = lienzo.papel_region(marca, x, y, w, h)
    alfa = int(round(255 * float(marca.m("opacidad_mosca"))))
    capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(capa).text((rel, rel + a), txt, font=f, fill=marca.color("tinta") + (alfa,), anchor="ls")
    img.alpha_composite(capa)
    return Placa("mosca", x, y, 0.0, float(duracion), img)


# ---------------------------------------------------------------- diálogo

def placa_dialogo(marca: Marca, indice: int, linea) -> Placa:
    nombre, frase, acotacion, t0, t1, voz = _lineas_dialogo(linea)
    cfg = marca.pc["dialogo"]
    if cfg.get("estilo") == "contorno":
        return _dialogo_contorno(marca, indice, nombre, frase, acotacion, t0, t1, voz, cfg)
    lineas = mod_dialogo.validar_bloque(marca, nombre, frase, acotacion, cfg)
    rx, ry = int(cfg["relleno_x"]), int(cfg["relleno_y"])
    fn, fa, ff = (marca.fuente("regular", cfg["tam_nombre"]), marca.fuente("italic", cfg["tam_acotacion"]),
                  marca.fuente("regular", cfg["tam_frase"]))
    filas: list[tuple[str, object, int]] = []        # (texto, fuente, alto de la fila)
    if nombre:
        filas.append((nombre.upper(), fn, cfg["alto_nombre"]))
    if acotacion:
        filas.append((mod_dialogo.texto_acotacion(acotacion), fa, cfg["alto_acotacion"]))
    filas += [(ln, ff, cfg["alto_linea_frase"]) for ln in lineas]
    w = _par(max(f.getlength(txt) for txt, f, _ in filas) + 2 * rx)
    h = _par(sum(alto for _, _, alto in filas) + 2 * ry)
    centro = (cfg["columna_dialogo"][0] + cfg["columna_dialogo"][1]) / 2
    x, y = _par_abajo(centro - w / 2), _par_abajo(cfg["borde_inferior"]) - h
    img = lienzo.papel_region(marca, x, y, w, h)
    d = ImageDraw.Draw(img)
    tinta, yy = marca.color_voz(voz), ry          # sin voz: tinta, como siempre
    for txt, f, alto in filas:
        d.text((w / 2 - f.getlength(txt) / 2, yy), txt, font=f, fill=tinta)
        yy += alto
    return Placa(f"dialogo_{indice}", x, y, t0, t1, img)


def _dialogo_contorno(marca: Marca, indice: int, nombre, frase, acotacion, t0, t1, voz, cfg: dict) -> Placa:
    """Estilo v3, punto 1 (Kevin, 25 nov: «Sin caja, con contorno»): sin placa de papel; texto con contorno de tinta y
    sombra suave sobre el video, más abajo. La voz del streamer va en papel; la otra, en lápiz rojo (como hasta ahora);
    el nombre del hablante, pequeño y en lápiz rojo."""
    lineas = mod_dialogo.validar_bloque(marca, nombre, frase, acotacion, cfg)
    c = cfg["contorno"]
    borde, sx, sy = int(c["grosor"]), int(c["sombra_px"]), int(c["sombra_px"])
    alfa_sombra = int(round(255 * float(c["sombra_opacidad"])))
    rx, ry = int(cfg["relleno_x"]), int(cfg["relleno_y"])
    fn, fa, ff = (marca.fuente("bold", cfg["tam_nombre"]), marca.fuente("italic", cfg["tam_acotacion"]),
                  marca.fuente("bold", cfg["tam_frase"]))
    color = marca.color_voz(voz)
    if color == marca.color("tinta"):
        color = marca.color("papel")                 # sobre el video, la voz principal va clara
    rojo, tinta = marca.color("lapiz_rojo"), marca.color("tinta")
    filas: list[tuple[str, object, int, tuple]] = []
    if nombre:
        filas.append((nombre.upper(), fn, cfg["alto_nombre"], rojo))
    if acotacion:
        filas.append((mod_dialogo.texto_acotacion(acotacion), fa, cfg["alto_acotacion"], color))
    filas += [(ln, ff, cfg["alto_linea_frase"], color) for ln in lineas]
    w = _par(max(f.getlength(txt) for txt, f, _, _ in filas) + 2 * rx + 2 * borde + sx)
    h = _par(sum(alto for _, _, alto, _ in filas) + 2 * ry + 2 * borde + sy)
    centro = (cfg["columna_dialogo"][0] + cfg["columna_dialogo"][1]) / 2
    x, y = _par_abajo(centro - w / 2), _par_abajo(cfg["borde_inferior"]) - h
    sombra = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ds, d = ImageDraw.Draw(sombra), ImageDraw.Draw(img)
    yy = ry + borde
    for txt, f, alto, col in filas:
        xx = (w - sx) / 2 - f.getlength(txt) / 2
        ds.text((xx + sx, yy + sy), txt, font=f, fill=(0, 0, 0, alfa_sombra), stroke_width=borde,
                stroke_fill=(0, 0, 0, alfa_sombra))
        d.text((xx, yy), txt, font=f, fill=col + (255,), stroke_width=borde, stroke_fill=tinta + (255,))
        yy += alto
    sombra = sombra.filter(ImageFilter.GaussianBlur(max(1, sx // 2 + 1)))
    sombra.alpha_composite(img)
    return Placa(f"dialogo_{indice}", x, y, t0, t1, sombra)


# ---------------------------------------------------------------- cierre

def placa_cierre(marca: Marca, t: Toma, duracion: float) -> Placa:
    cfg = marca.pc["cierre"]
    rx, ry = int(cfg["relleno_x"]), int(cfg["relleno_y"])
    fc, fk = marca.fuente("bold", cfg["tam_corte"]), marca.fuente("regular", cfg["tam_credito"])
    credito = t.credito.strip()
    ancho = max(fc.getlength("CORTE."), fk.getlength(credito) if credito else 0)
    w = _par(ancho + 2 * rx)
    seg = marca.pc["zona_segura"]
    if w > seg["x1"] - seg["x0"]:
        raise ValueError(f"el cierre mide {w} px y caben {seg['x1'] - seg['x0']}: {credito!r}")
    h = _par(ry + int(cfg["alto_corte"]) + (int(cfg["alto_credito"]) if credito else 0) + ry)
    x, y = _par_abajo((marca.ancho - w) / 2), _par_abajo(cfg["borde_inferior"]) - h
    img = lienzo.papel_region(marca, x, y, w, h)
    d = ImageDraw.Draw(img)
    tinta = marca.color("tinta")
    d.text((w / 2 - fc.getlength("CORTE.") / 2, ry), "CORTE.", font=fc, fill=tinta)
    regiones = {"CORTE.": (x, y, x + w, y + ry + int(cfg["alto_corte"]))}
    if credito:
        d.text((w / 2 - fk.getlength(credito) / 2, ry + cfg["alto_corte"]), credito, font=fk, fill=tinta)
        regiones["crédito"] = (x, y + ry + int(cfg["alto_corte"]), x + w, y + h)
    ini = max(0.0, float(duracion) - float(marca.m("cierre_s")))
    return Placa("cierre", x, y, ini, float(duracion), img, regiones)


# ---------------------------------------------------------------- marca de agua de campaña

def placa_agua(marca: Marca, ruta_logo: Path, duracion: float) -> Placa:
    img = mod_agua.logo(Path(ruta_logo), marca)
    c = mod_agua.caja_pantalla(marca, img.size)
    return Placa("marca_agua", c["x"], c["y"], 0.0, float(duracion), img)


# ---------------------------------------------------------------- todo junto

def construir(marca: Marca, t: Toma, lineas: list, duracion: float, logo: Path | None = None,
              posicion: str = "estandar") -> list[Placa]:
    """Todas las placas de una toma, en el orden en que se pintan. ValueError con el motivo si algo no cabe.

    `posicion`: "estandar" (la de siempre) o "alterna" (pantalla dividida: la fila, y con ella la cabecera, bajan a
    `dividida.placas_alterna.y_fila` de marca.yml, sobre la costura entre los paneles).
    """
    if posicion not in ("estandar", "alterna"):
        raise ValueError(f"posición de placas desconocida: {posicion!r}")
    y_fila = int(marca.datos["dividida"]["placas_alterna"]["y_fila"]) if posicion == "alterna" else None
    fila = placa_fila(marca, t, duracion, y_fila)
    tope_cab = float(duracion) - float(marca.m("cierre_s"))
    t1_cab = max(0.0, min(float(marca.pc["gancho_s"]), tope_cab))
    placas = [fila]
    if t.avisos:
        # El aviso abre la toma en el lugar del gancho (0 a `duracion_s`, sin alargar la toma ni tapar la fila, la mosca ni
        # la zona de acción); el gancho entra al salir el aviso y se ve los mismos `gancho_s` de siempre.
        dur_a = float(marca.avisos["duracion_s"])
        if dur_a > tope_cab - 1.0:
            raise ValueError(f"la toma dura {duracion:g} s: no cabe el aviso de {dur_a:g} s")
        placas.append(placa_aviso(marca, t.avisos, fila.y + fila.alto, dur_a))
        cab = placa_cabecera(marca, t, fila.y + fila.alto, max(0.0, min(dur_a + float(marca.pc["gancho_s"]), tope_cab)))
        cab.t0 = dur_a
        placas.append(cab)
    else:
        placas.append(placa_cabecera(marca, t, fila.y + fila.alto, t1_cab))
    placas.append(placa_mosca(marca, duracion))
    if logo is not None:
        placas.append(placa_agua(marca, logo, duracion))
    tope = float(duracion) - float(marca.m("cierre_s"))        # un diálogo no convive con la placa de cierre
    for i, ln in enumerate([] if t.subtitulos_del_clip else lineas, 1):      # subtítulos ya quemados: sin bloque nuestro
        p = placa_dialogo(marca, i, ln)
        p.t1 = min(p.t1, tope)
        if p.t0 >= p.t1:
            raise ValueError(f"el diálogo {i} empieza en {p.t0:g} s, dentro del cierre (desde {tope:g} s)")
        placas.append(p)
    if cierre_con_placa(marca):
        placas.append(placa_cierre(marca, t, duracion))
    return placas


def cierre_con_placa(marca: Marca) -> bool:
    """Estilo v3, punto 2: con `pantalla_completa.cierre.estilo: destello` ya no va la placa «CORTE.»."""
    return marca.pc["cierre"].get("estilo", "placa") != "destello"


def problemas_capas(capas: list[dict], marca: Marca, accion: dict | None = None) -> list[str]:
    """Reglas de pantalla completa sobre `geometria.capas`: zona segura, zona de acción y tope de área.

    `accion`: otra zona de acción (la de la pantalla dividida, `dividida.zona_accion`); por omisión, la de siempre.
    """
    seg, acc = marca.pc["zona_segura"], accion or marca.pc["zona_accion"]
    malas: list[str] = []
    for c in capas:
        try:
            x0, y0 = float(c["x"]), float(c["y"])
            x1, y1 = x0 + float(c["ancho"]), y0 + float(c["alto"])
        except (KeyError, TypeError, ValueError):
            malas.append(f"capa mal formada: {c!r}")
            continue
        nombre = str(c.get("tipo"))
        if not (x0 >= seg["x0"] and y0 >= seg["y0"] and x1 <= seg["x1"] and y1 <= seg["y1"]):
            malas.append(f"{nombre} sale de la zona segura")
        if x0 < acc["x1"] and x1 > acc["x0"] and y0 < acc["y1"] and y1 > acc["y0"]:
            malas.append(f"{nombre} toca la zona de acción")
        if nombre == "cierre" and y0 < acc["y1"]:
            malas.append("el cierre no va en el tercio inferior")
    for i, a in enumerate(capas):
        for b in capas[i + 1:]:
            try:
                en_tiempo = float(a["t0"]) < float(b["t1"]) and float(b["t0"]) < float(a["t1"])
                en_espacio = (a["x"] < b["x"] + b["ancho"] and b["x"] < a["x"] + a["ancho"]
                              and a["y"] < b["y"] + b["alto"] and b["y"] < a["y"] + a["alto"])
            except (KeyError, TypeError, ValueError):
                continue
            if en_tiempo and en_espacio:
                malas.append(f"{a.get('tipo')} y {b.get('tipo')} se solapan")
    tope = float(marca.pc["tope_capas"]) * marca.ancho * marca.alto
    for t in sorted({float(c.get("t0", 0)) for c in capas if isinstance(c, dict)}):
        area = sum(float(c["ancho"]) * float(c["alto"]) for c in capas
                   if float(c.get("t0", 0)) <= t < float(c.get("t1", 0)))
        if area > tope:
            malas.append(f"en {t:g} s las placas suman {area / (marca.ancho * marca.alto):.1%} del cuadro "
                         f"(tope {marca.pc['tope_capas']:.0%})")
    return malas

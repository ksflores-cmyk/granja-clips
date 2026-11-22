"""«Stream» sintético para las muestras de la pantalla dividida (tarea #16).

Arma, sólo con ffmpeg y Pillow y en la carpeta de trabajo, un video 1920x1080 que imita un clip de stream: un
«juego» de fondo (Big Buck Bunny, CC BY 3.0), una «facecam» con marco (Sintel, CC BY 3.0, en vaivén para que la cara
se vea todo el tiempo), un logo fijo, una columna de «chat» con texto en Courier Prime que se mueve y, si se pide, una
alerta de 1 s. El compuesto vive sólo en la carpeta temporal: nunca entra al repo ni se publica. El audio es el del
«juego».
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .. import plazo

ANCHO, ALTO = 1920, 1080
FACECAM = (640, 272)          # tamaño del video de la facecam dentro del marco
MARCO = 8                     # grosor del marco de la facecam
MARGEN = 24                   # separación de la facecam, el logo y el chat al borde
ANCHO_CHAT = 340
ALTO_CHAT = 1100              # alto de una vuelta de la columna de chat (más que el cuadro, para que nunca se vea el corte)
ALERTA = (620, 150)
ESQUINAS = ("superior_izquierda", "superior_derecha", "inferior_izquierda", "inferior_derecha")

MENSAJES = [
    ("ana_gg", "ese salto estuvo limpio"), ("tito99", "jajaja no puede ser"), ("lu_ma", "clip clip clip"),
    ("pablo", "otra vez lo mismo"), ("nere", "que buen momento"), ("sol_17", "lo vi venir"),
    ("kiko", "increible la camara"), ("mora", "pausa para el cafe"), ("dani", "ya van tres"),
    ("vale", "subo el volumen"), ("rafa", "esto va a salir mal"), ("irene", "me quedo a ver"),
    ("jose", "quien sigue en vivo"), ("lola", "que paisaje"), ("beto", "gg ez"), ("ceci", "mira el fondo"),
]


def _esquina(esquina: str, w: int, h: int, margen: int = MARGEN) -> tuple[int, int]:
    if esquina not in ESQUINAS:
        raise ValueError(f"esquina desconocida: {esquina!r}")
    x = margen if esquina.endswith("izquierda") else ANCHO - margen - w
    y = margen if esquina.startswith("superior") else ALTO - margen - h
    return x, y


def _marco(ruta: Path) -> None:
    w, h = FACECAM[0] + 2 * MARCO, FACECAM[1] + 2 * MARCO
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w - 1, h - 1), outline=(235, 235, 235, 255), width=MARCO)
    d.rectangle((MARCO - 2, MARCO - 2, w - MARCO + 1, h - MARCO + 1), outline=(200, 40, 70, 255), width=2)
    img.save(ruta)


def _logo(ruta: Path, fuente) -> None:
    img = Image.new("RGBA", (220, 120), (15, 15, 25, 235))
    d = ImageDraw.Draw(img)
    for i in range(0, 220, 20):
        d.rectangle((i, 0, i + 9, 119), fill=(240, 200, 40, 255) if (i // 20) % 2 == 0 else (40, 40, 60, 255))
    d.rectangle((28, 34, 192, 86), fill=(15, 15, 25, 255))
    d.text((110, 60), "GRANJA TV", font=fuente, fill=(255, 255, 255, 255), anchor="mm")
    d.rectangle((0, 0, 219, 119), outline=(255, 255, 255, 255), width=3)
    img.save(ruta)


def _chat(ruta: Path, fuente) -> None:
    img = Image.new("RGBA", (ANCHO_CHAT, 2 * ALTO_CHAT), (10, 10, 14, 200))
    d = ImageDraw.Draw(img)
    paso = 34
    for vuelta in range(2):
        for i in range(ALTO_CHAT // paso):
            usuario, texto = MENSAJES[i % len(MENSAJES)]
            y = vuelta * ALTO_CHAT + i * paso + 4
            d.text((12, y), f"{usuario}:", font=fuente, fill=(150, 200, 255, 255))
            d.text((12 + 11 * (len(usuario) + 1), y), texto, font=fuente, fill=(245, 245, 245, 255))
    img.save(ruta)


def _alerta(ruta: Path, fuente_g, fuente_p) -> None:
    img = Image.new("RGBA", ALERTA, (20, 20, 30, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, ALERTA[0] - 1, ALERTA[1] - 1), outline=(240, 200, 40, 255), width=6)
    d.text((ALERTA[0] // 2, 52), "NUEVO SEGUIDOR", font=fuente_g, fill=(255, 255, 255, 255), anchor="mm")
    d.text((ALERTA[0] // 2, 106), "gracias por pasar por aqui", font=fuente_p, fill=(200, 200, 220, 255), anchor="mm")
    img.save(ruta)


def componer_stream(marca, juego: Path, camara: Path, salida: Path, trabajo: Path, *, duracion: float,
                    juego_inicio: float, tramo_cara: tuple[float, float], esquina_facecam: str,
                    esquina_logo: str, chat: str | None, alerta_s: float | None) -> Path:
    """Escribe el compuesto 1920x1080 de `duracion` s en `salida`. `tramo_cara`: (inicio, largo) del tramo de la
    cámara con cara; se reproduce de ida y vuelta. `chat`: "izquierda", "derecha" o None. `alerta_s`: segundo en que
    aparece la alerta de 1 s (arriba, al centro) o None."""
    trabajo = Path(trabajo)
    trabajo.mkdir(parents=True, exist_ok=True)
    def fuente(tam: int):
        return ImageFont.truetype(str(marca.ruta_fuente("bold")), tam)
    marco, logo, tira, aviso = (trabajo / n for n in ("marco.png", "logo.png", "chat.png", "alerta.png"))
    _marco(marco)
    _logo(logo, fuente(26))
    if chat:
        _chat(tira, fuente(20))
    if alerta_s is not None:
        _alerta(aviso, fuente(40), fuente(22))
    ini, largo = tramo_cara
    vaiven = trabajo / "vaiven.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ini:g}", "-t", f"{largo:g}",
                    "-i", str(camara), "-an", "-filter_complex",
                    f"[0:v]fps=30,scale={FACECAM[0]}:{FACECAM[1]}:flags=lanczos,setsar=1,split[a][b];[b]reverse[r];"
                    f"[a][r]concat=n=2:v=1:a=0[o]", "-map", "[o]", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "14", "-pix_fmt", "yuv420p", str(vaiven)], check=True, timeout=plazo.tiempo_para(120))
    d = f"{duracion:g}"
    entradas = ["-ss", f"{juego_inicio:g}", "-t", d, "-i", str(juego), "-stream_loop", "-1", "-i", str(vaiven),
                "-i", str(marco), "-i", str(logo)]
    filtros = [f"[0:v]fps=30,scale={ANCHO}:{ALTO}:flags=bicubic,setsar=1[f0]"]
    fx, fy = _esquina(esquina_facecam, FACECAM[0] + 2 * MARCO, FACECAM[1] + 2 * MARCO)
    lx, ly = _esquina(esquina_logo, 220, 120)
    filtros.append(f"[f0][1:v]overlay={fx + MARCO}:{fy + MARCO}:shortest=1[f1]")
    filtros.append(f"[f1][2:v]overlay={fx}:{fy}[f2]")
    filtros.append(f"[f2][3:v]overlay={lx}:{ly}[f3]")
    actual, idx = "f3", 4
    if chat:
        entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", str(tira)]
        x = MARGEN if chat == "izquierda" else ANCHO - MARGEN - ANCHO_CHAT
        filtros.append(f"[{actual}][{idx}:v]overlay={x}:'-mod(t*70,{ALTO_CHAT})':eval=frame[f4]")
        actual, idx = "f4", idx + 1
    if alerta_s is not None:
        entradas += ["-loop", "1", "-framerate", "30", "-t", d, "-i", str(aviso)]
        filtros.append(f"[{actual}][{idx}:v]overlay={(ANCHO - ALERTA[0]) // 2}:30:"
                       f"enable='between(t,{alerta_s:g},{alerta_s + 1:g})'[f5]")
        actual = "f5"
    filtros.append(f"[{actual}]format=yuv420p[out]")
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entradas, "-filter_complex",
                    ";".join(filtros), "-map", "[out]", "-map", "0:a:0", "-t", d, "-r", "30", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-ar", "48000", "-movflags", "+faststart", str(salida)],
                   check=True, timeout=plazo.tiempo_para(300))
    return Path(salida)

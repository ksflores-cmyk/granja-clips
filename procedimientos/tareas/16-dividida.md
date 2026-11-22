# Tarea #16 `dividida`: pantalla dividida automática (rama `claude/clips-dividida`)

Plan: `procedimientos/plan-noche-2026-11-20.md` (decisión de Kevin del 20 nov, 1:21 a. m.: «Sí: todo», idea 1 del tweet). Base: plantilla v2 de la #5 (`procedimientos/tareas/05-pantalla-completa.md`), que sigue mandando cuando no hay facecam.

## Qué cambia

Hoy (`fabrica/editar/componer.py` → `_componer_pantalla_completa`) una toma con video llena el 9:16 con **un** recorte fijo (`encuadre.elegir_recorte`). En un clip de stream eso deja la cara del streamer fuera o diminuta. Con la #16:

1. **Detección** (módulo nuevo `fabrica/editar/dividida.py`, determinista, sin red ni modelos descargados):
   - **Facecam**: rostro con OpenCV (`opencv-python-headless==4.13.0.92`, Haar `haarcascade_frontalface_default.xml` que trae el paquete en `cv2.data.haarcascades`; OpenCV 5.x ya no trae esos XML). Se miran N fotogramas (`marca.yml`) del recorte de tiempo con ffmpeg (como `encuadre._fotogramas_grises`). Es facecam si: hay cara en al menos `min_fraccion_cara` de los fotogramas, la posición es estable (dispersión de centros ≤ `max_dispersion` del ancho), la cara es pequeña respecto del cuadro (área ≤ `max_area_cara`) y está fuera del centro (no es una cinemática ni un stream de pura cámara). La caja de la facecam se busca expandiendo desde la cara hasta el borde persistente más cercano (gradiente que se mantiene en el tiempo mientras el juego cambia) o, si no lo hay, con un factor de expansión fijo, siempre dentro del cuadro.
   - **Estorbos** (alertas, chat, logos del stream): celdas de una rejilla en las bandas exteriores del cuadro con muchos bordes que (a) no cambian mientras el resto sí (logos, marcos, metas de subs) o (b) aparecen de golpe en unos pocos fotogramas (alertas), y columnas laterales con densidad de texto alta (chat). Si el juego casi no se mueve, no se marcan estorbos estáticos (no se puede distinguir).
   - **Zona del juego**: el recorte con proporción 1080:1248 (panel de abajo) de mayor energía de acción (reusar la idea de `encuadre`: movimiento + algo de bordes) que **no toca la facecam** y evita los estorbos (penalización por área tapada). Si no cabe un recorte de altura ≥ `min_alto_juego` de la fuente sin tocar la facecam, no hay división.
   - `detectar(ruta, inicio, duracion, ancho_v, alto_v, *, ajustes, detector=None) -> dict`: `{"modo": "dividida"|"completa", "motivo": str, "cara": caja|None, "facecam": caja|None, "estorbos": [caja...], "juego": caja|None}` en píxeles de la fuente. `detector` inyectable (función gris→lista de cajas) para las pruebas; por omisión, Haar. Si `cv2` no se puede importar, si falla la lectura o si se acaba el plazo (`plazo.tiempo_para`): `modo: "completa"` con su motivo (nunca rompe la toma).
2. **Formato 9:16 dividido, sin franjas**: panel de facecam arriba de `alto_facecam` px (≈ 35 % = 672) y panel del juego abajo (≈ 65 % = 1248). Cada panel es un recorte fijo de la fuente que **cubre** su destino (misma regla de escala que `encuadre.escala_cubrir`, en pares). El recorte de la facecam va dentro de la caja de la facecam (si es más chica que la proporción del panel, se centra en la cara y se recorta dentro del cuadro) con la cara entera y centrada en el panel según `cara_centro_y`. Sin zoom ni paneo (`zoom_automatico: false` sigue).
3. **Sin facecam → la pantalla completa de la #5**, idéntica a hoy (mismo plan, mismo MP4). Interruptores: `marca.yml` `dividida.activa` y, por ficha, `reglas.dividida: false`.
4. **Placas** (las mismas de la #5, mismos textos): ninguna toca la cara (caja de la cara llevada a coordenadas del cuadro, con margen) ni la `zona_accion` de la división (`dividida.zona_accion`, dentro del panel del juego). Si con la posición de siempre la cabecera o la fila tocan la cara, van a la posición alternativa declarada en `marca.yml` (p. ej. sobre la costura entre paneles); si no hay posición válida, no hay división (pantalla completa) y el plan dice por qué. Todas las medidas en `marca.yml`; nada escrito a mano en el código.
5. **Plan JSON**: misma `plantilla: "v2-pantalla-completa"`, `geometria.cuerpo` a cuadro completo y además `geometria.disposicion: "dividida"|"completa"`; en `dividida`, `geometria.paneles`: `[{nombre: "facecam"|"juego", recorte: {x,y,ancho,alto,escala}, destino: {x,y,ancho,alto}}]`, `geometria.cara` (en el cuadro) y `geometria.deteccion` (lo de `detectar`). `geometria.recorte` sigue existiendo en `completa` (sin cambios).
6. **Orden de ffmpeg**: `orden_ffmpeg_pantalla_completa` acepta paneles (`split` del video, un `crop`+`scale` por panel, `vstack` o `overlay` a 1080x1920) con el audio original de siempre. Función pura, con prueba de la orden.
7. **Puerta de estilo** (`fabrica/editar/estilo.py`, sin LLM):
   - `_fuente_esperada` compone los paneles del plan (así `[fotograma] Cuerpo coincide con el origen` y `[fotograma] Bordes coinciden con la fuente` valen igual en las dos disposiciones).
   - `[plan] Pantalla completa…` acepta la disposición dividida: los paneles cubren el cuadro entero sin huecos ni solapes, cada recorte cubre su destino dentro de la fuente.
   - Punto nuevo `[plan] Pantalla dividida (facecam arriba, juego abajo)`: sólo en `dividida`: facecam arriba con alto `alto_facecam` ± 2 %, juego abajo, el recorte del juego no toca la facecam de la fuente, ninguna placa toca la cara.
   - Puntos de siempre intactos.
8. **Dependencia nueva** (aprobada en el plan del 20 nov): `opencv-python-headless==4.13.0.92` en `requirements.txt`; anotar en la PR los segundos que suma la instalación (medido en la corrida de `puerta`).

## Muestras

`marca/muestras/muestras.yml` sigue con las 3 de pantalla completa (sin facecam: deben salir `completa`) y suma **3 muestras divididas** armadas en la nube a partir de las fuentes ya autorizadas (`sintel-cc-by` como «facecam», `bbb-cc-by` como «juego», CC BY 3.0, crédito a Blender Foundation): ffmpeg compone un «stream» 1920x1080 con BBB de fondo, Sintel en una caja de facecam con marco (esquina distinta en cada una), un logo fijo y una columna de «chat» con texto en Courier Prime (y en una, una alerta de 1 s). Sintel tiene cara detectable en ~29.5–32.5 s y ~41–45.5 s (Haar, 2 fps): usar esos tramos (en vaivén si hace falta). El compuesto vive sólo en la carpeta temporal del runner; nunca entra al repo ni se publica.
- Las 3 divididas salen `dividida` con la puerta de estilo en verde; las 3 de siempre siguen `completa` y en verde.
- **3 fotogramas de muestra**: uno por compuesto, con las cajas detectadas dibujadas (cara, facecam, estorbos, juego), en el artefacto `muestras` (`deteccion-<id>.png`).

## Criterios de aceptación (binarios)

1. `detectar` con un video sintético sin cara (detector falso que no devuelve nada) → `completa`; con cara estable pequeña en una esquina (detector falso) → `dividida` con la facecam en esa esquina y el juego sin tocarla; con cara grande en el centro → `completa`; con cara que salta de sitio → `completa`; sin `cv2` → `completa` con motivo.
2. Estorbos: en un video sintético con un logo fijo de muchos bordes en una esquina, una columna de texto que se mueve en un lado y un recuadro que aparece 1 s arriba, los tres salen en `estorbos` y el recorte del juego no los toca cuando cabe sin ellos.
3. Con facecam: MP4 1080x1920, sin franjas, facecam arriba en 0–`alto_facecam` y juego abajo; con prueba de que el panel de arriba coincide con la facecam de la fuente y el de abajo con la zona del juego (diferencia baja en píxeles).
4. Sin facecam: plan y orden de ffmpeg idénticos a los de `main` (prueba de no-regresión) y todas las pruebas de la #5 en verde.
5. Audio original intacto en las dos disposiciones (correlación > 0.9 con tono sintético).
6. Ninguna placa toca la cara ni la `zona_accion` de la división; el área de placas activas ≤ `tope_capas`.
7. La puerta de estilo sale **en verde** en el dividido correcto y **en rojo** con: (a) paneles con hueco o franja, (b) el panel de arriba con otra zona de la fuente (no la facecam), (c) una placa sobre la cara, (d) los casos rojos de la #5 (un cambio de una letra, audio en silencio).
8. `python -m pytest -q` entero en verde (base: lo que pasa hoy en `main`).
9. `python -m fabrica.editar.muestras --salida /tmp/x` genera 6 MP4 (3 `completa`, 3 `dividida`) y 3 `deteccion-*.png`, con la puerta en verde; corrida real de `muestras` en verde con el artefacto.
10. Nada de la lista prohibida de la sección 5B; ningún video ni binario entra al repo; sin red ni descargas de modelos en el ciclo.

# Tarea #5 `pantalla-completa`: plantilla v2 (rama `claude/clips-pantalla-completa`)

Plan: `procedimientos/plan-noche-2026-11-19.md`. Decisión de Kevin (19 nov, 8:00 p. m.): a Kevin no le gustó la toma 1 (muy simple). **Si es video: se deja el audio del video y el clip ocupa todo el cuadro 9:16.** La marca Guion Vivo va como capa encima, discreta. Esa decisión manda sobre `marca.yml` y la sección 5B del playbook.

## Qué cambia

Hoy (`fabrica/editar/componer.py`) el video se escala para caber entero en una caja bajo la cabecera de papel: franjas de papel enormes arriba y abajo y el clip chiquito. La v2, sólo para tomas **con video**:

1. **Cuerpo a pantalla completa.** La fuente se escala para *cubrir* 1080x1920 (escala = max(1080/ancho, 1920/alto)) y se recorta a 1080x1920. Nada de franjas, marcos ni caja: el cuerpo es `{x:0, y:0, ancho:1080, alto:1920}`.
2. **Encuadre.** Recorte fijo para toda la toma (sin paneo ni zoom: `zoom_automatico: false`). Módulo nuevo `fabrica/editar/encuadre.py` con `elegir_recorte(ruta, inicio, duracion, ancho_v, alto_v, ancho_salida, alto_salida) -> dict` que devuelve `{x, y, ancho, alto, escala, modo}` en píxeles de la fuente. `modo: "accion"` sigue la acción: saca con ffmpeg unos 10 a 16 fotogramas en gris a baja resolución dentro del recorte de tiempo, suma por columna (o por fila si la fuente es más alta que 9:16) la energía de movimiento (|diferencia| entre fotogramas seguidos) y algo de bordes, y elige la ventana de mayor energía. Si esa ventana no supera a la centrada por un margen (p. ej. 15 %), o si falla la lectura, `modo: "centro"`. Determinista. **Sin dependencias nuevas** (sólo numpy, Pillow y ffmpeg; nada de OpenCV ni descargas de modelos).
3. **Audio original.** El audio es siempre el del video de origen, tal cual (ya lo hace `orden_ffmpeg`; que siga así). Sin audio de origen: silencio. Nunca se añade audio en tomas con video.
4. **Marca encima, discreta.** Placas de papel opaco (`#F2EDE3`) con tinta (`#151515`) y Courier Prime de `marca.yml`, ajustadas al texto (no a lo ancho de la página), con relleno pequeño:
   - **Encabezado de toma compacto** arriba, dentro de la zona segura: `TOMA nnn` + el gancho (6 palabras o menos, 2 líneas como máximo, con el lápiz rojo sobre la palabra) + la línea de escena si hay, en tamaños menores que la página v1. El gancho y la escena se ven los primeros `gancho_s` segundos (≈ 3 s); después sólo queda una fila mínima con `TOMA nnn` y el crédito corto.
   - **Mosca** `GUION VIVO` en la esquina superior derecha, pequeña, fija toda la toma, con su opacidad de `marca.yml`.
   - **Diálogo**: NOMBRE, (acotación) y frase como en el guion, centrado en la columna, en una placa sobre el tercio inferior, con su borde inferior encima de la franja de interfaz de las apps (los 320 px de abajo).
   - **Crédito**: siempre en pantalla (fila mínima) y en el cierre.
   - **Cierre**: los últimos `cierre_s` (0.5 s) una placa con `CORTE.` y el crédito **encima del video**, en el tercio inferior (el video sigue debajo; ya no es una página de papel completa).
   - **Marca de agua de campaña** (si la ficha la trae): en su esquina, dentro de la zona segura (no bajo los botones de las apps), sin tocar la zona de acción ni las demás placas.
5. **Valores en `marca.yml`.** Bloque nuevo `pantalla_completa:` con todas las medidas (zona segura, zona de acción, tope de área de capas, tamaños, posiciones, `gancho_s`, relleno de placa) y `plantilla_video: pantalla_completa`. La fábrica no lleva medidas ni colores escritos a mano. Actualiza el comentario de `lienzo:` (ya no es "video dentro de la página" para tomas con video).
6. **Tomas sin video** (informativa tipográfica): **no cambian** en esta tarea (las toca la #6 con música y movimiento).
7. **Plan JSON**: `plantilla: "v2-pantalla-completa"`, `geometria.cuerpo` a cuadro completo, `geometria.recorte` (lo de `elegir_recorte`) y `geometria.capas`: lista de `{tipo, x, y, ancho, alto, t0, t1}` de cada placa (cabecera, fila, mosca, diálogo n, crédito, cierre, marca de agua).

## Puerta de estilo (`fabrica/editar/estilo.py`), regla de pantalla completa

Para tomas con origen de video, puntos nuevos o adaptados (sin LLM):

- `[plan] Pantalla completa (cuerpo 1080x1920, sin franjas)`: cuerpo = cuadro entero; el recorte cubre el cuadro (escala ≥ la de cubrir, recorte dentro de la fuente).
- `[fotograma] Bordes coinciden con la fuente (sin franjas)`: en 2 fotogramas (30 % y 70 %), las 4 franjas del borde (≈ 24 px), fuera de las placas, coinciden con la fuente recortada y escalada como dice el plan. Un video con franjas negras o de papel sale en rojo; una escena oscura de verdad no.
- `[fotograma] Cuerpo coincide con el origen`: ahora contra la fuente recortada a cuadro completo, fuera de las placas activas en ese instante.
- `[plan] Capas fuera de la zona de acción`: ninguna placa (salvo el cierre en su medio segundo, que va en el tercio inferior igual) toca `zona_accion`; todas dentro de la zona segura; el área sumada de placas en cualquier instante ≤ `tope_capas` del cuadro.
- `[fotograma] Audio original del clip`: si la fuente tiene audio, el audio del MP4 correlaciona con el de la fuente (reusar `audio_original`); ya no sólo cuando `sin_musica`.
- Cabecera, diálogo, cierre, mosca y marca de agua visibles: se comprueban **dentro de sus placas** (lo esperado = la placa dibujada desde el plan), con las mismas tolerancias calibradas (`K_BLOQUE`, etc.). Un cambio de una letra sigue saliendo en rojo.
- `logo_extra_en_esquina` adaptado al cuadro completo (la mosca declarada no cuenta como logo extra).
- Los puntos de siempre siguen (resolución, códec, duración, faststart, gancho, emojis, ganchos prohibidos, colores, fuente, crédito).

## Muestras

`marca/muestras/muestras.yml`: **3 muestras con video** de las fichas ya autorizadas (`sintel-cc-by`, `bbb-cc-by`; la tercera, otro momento de BBB o de Sintel con movimiento), con su sha256 de siempre. Las 3 en v2 y con la puerta de estilo en verde. (La informativa tipográfica sale del set de muestras; su prueba sigue en pytest.) El workflow `muestras` las sube al artefacto `muestras` como siempre.

## Criterios de aceptación (binarios)

1. Con una fuente 16:9 y con una 9:16 (sintéticas en pruebas), el MP4 sale 1080x1920 y `geometria.cuerpo == {x:0,y:0,ancho:1080,alto:1920}`; el recorte cubre el cuadro.
2. `elegir_recorte` con una fuente sintética 16:9 cuyo movimiento está en el tercio derecho devuelve `modo: "accion"` y una ventana que contiene ese movimiento; con una fuente estática o uniforme devuelve `modo: "centro"` y ventana centrada.
3. El audio del MP4 es el de la fuente (prueba con tono sintético: correlación > 0.9); sin audio de origen, silencio.
4. Ninguna placa toca `zona_accion` y el área sumada de placas activas ≤ `tope_capas` (prueba sobre `geometria.capas`).
5. La puerta de estilo nueva sale **en verde** en el video correcto y **en rojo** con: (a) el video v1 de franjas (o un video con franjas negras arriba y abajo), (b) una placa que invade la zona de acción, (c) un cambio de una letra en gancho, diálogo o crédito, (d) el cuerpo de otra fuente, (e) audio reemplazado por silencio cuando la fuente tiene audio.
6. Las tomas sin video producen el mismo resultado que antes (pruebas existentes de la informativa en verde).
7. `python -m pytest -q` entero en verde (base: lo que pasa hoy en `main`), con las pruebas viejas del diseño de caja adaptadas a v2 o retiradas sólo si prueban lo que Kevin pidió quitar.
8. `python -m fabrica.editar.muestras --salida /tmp/x` genera 3 MP4 con video y la puerta en verde en local.
9. Nada de la lista prohibida de la sección 5B (sin degradados, sin barras de progreso, sin subtítulos palabra por palabra, sin emojis, sin zoom ni transiciones).
10. Sin dependencias nuevas en `requirements.txt`; ningún video ni binario entra al repo.

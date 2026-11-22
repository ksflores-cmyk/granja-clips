# Tarea #18 `reglas-kevin` (rama `claude/clips-reglas-kevin`)

Plan: `procedimientos/plan-noche-2026-11-20.md` (idea «cada queja se vuelve regla», aprobada por Kevin con «Sí: todo» el 20 nov, 1:21 a. m.). Reglas: `procedimientos/plan-noche-2026-11-19.md` (decisión 5, estilo). Puerta de estilo: `fabrica/editar/estilo.py`.

## Qué se pide

Un archivo `reglas-kevin.yml` en la raíz del repo donde cada queja de Kevin queda escrita con sus palabras, la regla que sale de ella y, si se puede medir, la revisión de código que la hace cumplir en la puerta de estilo. La puerta lee el archivo en cada toma: una regla con revisión pone un punto sí/no; una regla sin revisión no frena nada y queda como nota para los críticos (tarea #21). Añadir una queja nueva es editar el archivo, sin tocar código, salvo que se quiera medir.

## Quejas de hoy

| id | Queja (Kevin) | Regla | Revisión |
|---|---|---|---|
| `muy-simple` | «muy simple» (toma 1 informativa, 19 nov: texto solo, sin audio ni movimiento) | Ninguna toma sin movimiento ni audio: toda toma se mueve y se oye | `con_movimiento_y_audio` |
| `sin-musica` | «sin música» (19 nov, decisión 5) | Toda pieza sin audio propio lleva pista CC0 verificada (salvo campaña con `sin_musica: true`) | `musica_cc0_sin_audio_propio` |
| `ocupa-todo` | «que el clip ocupe todo» (19 nov, decisión 5) | El clip llena el 9:16: sin franjas | `sin_franjas` |

## Criterios de aceptación (sí o no)

1. Existe `reglas-kevin.yml` en la raíz con `version: 1`, un bloque de comentarios al principio con el **procedimiento para añadir una queja nueva** (pasos numerados) y una lista `reglas:` con las 3 de la tabla. Cada regla trae `id` (minúsculas, números y guiones, único), `fecha` (AAAA-MM-DD), `queja` (palabras de Kevin, no vacía), `regla` (no vacía), `revision` (id del registro de código o `null`) y `estado` (`vigente` o `retirada`); `contexto` es opcional.
2. `fabrica/editar/reglas_kevin.py`: `cargar(raiz)` lee y valida el archivo y lanza `ValueError` con el motivo exacto (falta el archivo, YAML roto, campo que falta o vacío, id repetido o mal formado, fecha inválida, estado desconocido, `revision` que no está en el registro); `REVISIONES` es el registro `{id: función}`; `puntos(video, plan, marca, ficha)` devuelve `[(nombre, ok, detalle)]` sólo para las reglas `vigente` con revisión, con nombre `[regla-kevin] <id>: <regla>`; `notas_para_criticos(raiz)` devuelve las reglas `vigente` sin revisión como texto (`<id>: <regla> (queja: «…», fecha)`).
3. La puerta falla cerrada: si `cargar` lanza, `puntos` devuelve un solo punto en rojo `[regla-kevin] reglas-kevin.yml válido` con el motivo. Una `revision` desconocida es error de carga (rojo), nunca se salta en silencio. Una regla `retirada` no pone punto ni nota.
4. `estilo.revisar` añade al final los puntos de `reglas_kevin.puntos(...)`; `estilo.main` imprime después del veredicto las notas para los críticos (si hay). Ningún otro punto de la puerta cambia.
5. Revisión `con_movimiento_y_audio`, medida sobre el MP4:
   - **Audio**: con `volumedetect`, pico por encima de `UMBRAL_SILENCIO_DB` (-45 dB) y volumen medio por encima de -55 dB. Excepción única: ficha o campaña con `sin_musica: true` **y** fuente sin audio → el audio «no aplica» (manda el brief), pero el movimiento se mide igual.
   - **Movimiento**: con video (`plan.origen`), 6 instantes repartidos entre 0,3 s y `duracion - cierre_s - 0,2`; para cada par consecutivo, diferencia media en gris de miniaturas de 64 px de ancho, fuera de las placas activas (`_mascara_placas` en v2); pasa si el mayor par supera `UMBRAL_MOVIMIENTO_TOMA` (calibrado y anotado en el código con medidas: imagen fija con ruido de compresión frente a `testsrc2` o metraje real). Sin video: se usa `estilo.movimiento_visible` (entrada del texto) y además debe cambiar el cuadro entre la primera y la última línea del diálogo, o fallar con detalle.
   - El detalle dice qué falló y dónde (instantes y valores).
6. Revisión `musica_cc0_sin_audio_propio`: reutiliza `estilo.musica_en_plan`; si el plan declara música, además el MP4 se oye (pico > -45 dB).
7. Revisión `sin_franjas`: con video, `estilo.pantalla_completa` (plan) y, en v2, `estilo.bordes_coinciden`; en todas las piezas, `cropdetect` de ffmpeg sobre el MP4 entero (`reset=0`): rojo si la caja detectada deja una franja de más de 16 px en cualquier borde. El detalle da la caja medida.
8. `fabrica/validar.py` valida `reglas-kevin.yml` con el mismo `cargar` (error si falla) y avisa de cuántas reglas quedan como notas.
9. `pruebas/test_reglas_kevin.py` con videos sintéticos que fallan a propósito: toma de imagen fija (rojo por movimiento), toma muda sin `sin_musica` (rojo por audio), toma muda con `sin_musica` y fuente muda (audio no aplica), pieza sin audio propio sin pista (rojo por música), video con franjas negras arriba y abajo (rojo por `cropdetect`), y una toma buena en verde en las 3; más los casos del archivo: sin archivo, YAML roto, id repetido, revisión desconocida, regla `retirada`, regla nueva sin revisión → nota y ningún punto.
10. `python -m pytest -q` entero en verde (CI `puerta`); las muestras de `muestras` siguen en verde con las reglas puestas (corrida real).

## Fuera de alcance

Los críticos con modelo (tarea #21) sólo consumen `notas_para_criticos`; esta tarea no los construye.

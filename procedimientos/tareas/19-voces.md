# Tarea #19 `voces`: transcripción propia, voces por color, silencios y golpes (rama `claude/clips-voces`)

Plan: `procedimientos/plan-noche-2026-11-20.md` (decisión de Kevin del 20 nov, 1:21 a. m.: «Sí: todo»; cadena de modelos Q0; «No todo en Gemini»). Reglas y ciclo: `procedimientos/plan-noche-2026-11-19.md`. Herramientas aprobadas: `faster-whisper` (con su VAD Silero integrado). Auto-Editor **no** se añade: su lógica de umbral y margen se reimplementa en 40 líneas de numpy (no arrastra dependencias ni corta risas, que tienen energía).

Meta: que el texto y los tiempos de los subtítulos no dependan de Gemini, que se vea quién habla y que no quede aire muerto.

## Criterios de aceptación (binarios)

1. **Transcripción sin Gemini**: `fabrica/voces.py` transcribe con `faster-whisper` en CPU (`int8`, modelo de `config/voces.yml`, `base` por omisión), `word_timestamps=True` y `vad_filter=True`. Si la biblioteca o el modelo no están, devuelve «sin voces» con el motivo y la toma sigue como hoy (nunca rompe el ciclo).
2. **Respaldo o principal** según `config/voces.yml` `modo`: `principal` = si el idioma detectado es español, las líneas salen de la transcripción (la acotación de Gemini se conserva); `respaldo` = sólo cuando Gemini no dio líneas (o el corte fue sin IA). Idioma distinto del español: nunca se usan sus líneas (no hay traducción sin IA). El modo elegido se justifica con tiempos y calidad medidos en una corrida real (bitácora).
3. **Hablantes**: separación sin dependencias nuevas (rasgos MFCC + tono con numpy, k-medias de 2). La voz con más tiempo hablado es la del streamer (la de la facecam); si las voces no se separan con claridad, todo es del streamer. Cada línea lleva `voz: streamer|otra`; la placa de diálogo pinta el texto del color de `marca.yml` `voces` (`streamer: tinta`, `otra: lapiz_rojo`; nombres de colores ya existentes, sin colores nuevos). La puerta de estilo lo comprueba porque redibuja las placas desde el plan.
4. **Silencios**: se cortan los tramos de más de 0,6 s sin voz y con audio por debajo del umbral (corte seco, dejando `margen_s` a cada lado). El corte nunca deja la toma por debajo del mínimo de duración de la ficha (si lo haría, no se corta). Los subtítulos, los golpes y la transcripción se remapean al tiempo de la toma.
5. **Golpes**: picos de audio marcados en `plan["voces"]["golpes"]` (`[{t, db}]`, tiempo de la toma), para la #20.
6. **Contrato con la #17**: `plan["transcripcion"] = {motor, modelo, idioma, palabras: [{texto, t0, t1}]}` en el tiempo de la toma.
7. **Puerta de estilo con cortes**: `plan["origen"]["tramos"]` (lista `[a, b]` en segundos relativos a `origen.inicio`); la comparación de fotogramas y de audio con la fuente usa ese mapa. Sin `tramos`, todo como hoy.
8. **Plazo**: la etapa `voces` corre dentro de `plazo.etapa("voces")` con tope propio (`tope_s`); si no queda tiempo, se salta con log. Ciclo completo dentro de 13 min, demostrado con una corrida real de `fabrica` (ensayo forzado) y con la muestra.
9. **Pruebas** con audio sintético (dos voces de tono y timbre distintos, silencios y un golpe) y transcriptor falso; `python -m pytest -q` entero en verde.
10. **Muestra**: el artefacto de `muestras` incluye una muestra con voces (Sintel, dos personajes: dos colores, silencios cortados, golpes) y `voces.json` con el tiempo de transcripción medido de `base` y `small`.

## Fuera de alcance

- Traducir (sigue siendo Gemini). Unir la voz con la cara de la facecam de la #16 (seguimiento: mover la boca en la facecam como desempate).

# Tarea #20 `historia` (rama `claude/clips-historia`)

Decisión de Kevin (20 nov, 1:21 a. m., «Sí: todo»): el edit como historia. Abre con el desenlace (apertura en frío) y cada momento lleva un sentimiento y un tratamiento. Plan de la noche: `procedimientos/plan-noche-2026-11-20.md`, fila #20.

## Comprobación previa (21 nov 2026, jefe de Clips): GitHub Models ya no existe

La documentación oficial de GitHub dice hoy: «As of July 30, 2026, GitHub Models has been fully retired. The playground, model catalog, inference API, and bring your own key (BYOK) are no longer available to any customer» (docs.github.com/en/github-models/…/prototyping-with-ai-models y docs.github.com/en/billing/concepts/product-billing/github-models; changelog github.blog 2026-07-30 «GitHub Models is now retired»). No hay límites gratis que comprobar ni permiso `models: read` que pedir: `https://models.github.ai/inference/chat/completions` no se usa.

Consecuencia para esta tarea: la cadena queda **Gemini → segundo proveedor (hueco vacío, apagado) → sin IA**. El segundo eslabón se programa como cliente genérico compatible con chat/completions de OpenAI (URL, modelo y nombre del secreto en la config), **apagado** (`activo: false`) con el motivo escrito. Elegir y dar de alta otro proveedor gratis es una decisión de Kevin (regla 7: altas juntas, un solo «Sí»); esta tarea no crea cuentas ni pide claves. Ningún workflow pide `models: read`.

## Criterios de aceptación (binarios)

### A. El plan por momentos (`fabrica/editar/historia.py`, `config/historia.yml`)

1. Esquema JSON del plan (sin ningún otro campo):
   ```json
   {"apertura": {"t0": 21.4, "t1": 23.6},
    "momentos": [{"t0": 9.0, "t1": 12.5, "sentimiento": "tension", "tratamiento": "zoom_suave", "sfx": null},
                 {"t0": 21.0, "t1": 22.2, "sentimiento": "sorpresa", "tratamiento": "camara_lenta", "sfx": null}]}
   ```
   Tiempos en segundos del archivo enviado (como `recorte` de los ojos). `sentimiento` ∈ {tension, sorpresa, risa, triunfo, frustracion, alivio, asombro}; `tratamiento` ∈ {ninguno, zoom_suave, camara_lenta, sfx, pausa}; `sfx` es un id del catálogo verificado o null (sólo con `tratamiento: sfx`).
2. **Sin texto inventado.** `validar_plan` rechaza cualquier clave que no esté en el esquema y cualquier texto que no sea uno de los valores permitidos. Nada del plan de historia se dibuja: las placas salen sólo de gancho, escena, crédito y líneas de los ojos. Prueba: las placas (textos) de una toma son idénticas con y sin historia.
3. `validar_plan(plan, recorte, rango_duracion, sfx_disponibles, ficha) -> (ok, motivos)` exige, con un motivo legible por falla:
   - apertura de `apertura_min_s` a `apertura_max_s` (1.0 a 3.0) dentro del recorte y en su último 40 % (es el desenlace);
   - de 2 a 4 momentos, ordenados, sin solaparse entre sí, dentro del recorte, cada uno de 0.8 s o más;
   - al menos un momento con tratamiento distinto de `ninguno`;
   - `camara_lenta`: como máximo 1 por plan y su momento dura 1.5 s o menos de fuente (sale al doble);
   - `pausa`: como máximo 1 por plan; congela el último cuadro del momento `pausa_s` (0.4 s, por debajo del tope de congelado de 0.5 s de la #17) con silencio;
   - `zoom_suave`: como máximo 2; de 1.00 a `zoom_max` (1.12) a lo largo del momento, centrado;
   - `sfx`: como máximo 2; el id debe estar verificado y disponible; **prohibido** si la ficha tiene `reglas.sin_musica` o es de campaña (tipo A con `cr_campaign_id` o `permiso_url` de campaña) mientras `sfx_en_campanas: false`;
   - la duración de salida (recorte + apertura + lo que agrega la cámara lenta + pausas) cae dentro de `rango_duracion` de la ficha (7 a 30 s, o `reglas.duracion_s`).
4. `mapa(plan, inicio, fin) -> list[Tramo]` (función pura): tramos de salida `{s0, s1, f0, f1, velocidad (1.0 | 0.5 | 0.0 congelado), zoom (None | (desde, hasta)), sfx (None | id)}`. Primero la apertura (de `apertura.t0` a `apertura.t1`), luego todo el recorte de `inicio` a `fin` partido en los momentos con su tratamiento. `fuente_en(mapa, t) -> (t_fuente, zoom)`; `duracion_salida(mapa)`.
5. `remapear_lineas(lineas, mapa)`: cada línea (tiempos relativos al recorte) pasa al tiempo de salida en el tramo principal; si una línea cae en la ventana de la apertura, se repite recortada a la apertura (mismo texto, sin inventar). Las líneas de salida no se solapan; una línea más corta que `MIN_LINEA_S` se descarta.
6. `config/historia.yml` validado en `fabrica/validar.py`: `activo`, límites de arriba, `sfx_en_campanas`, `ganancia_sfx_db`, y el bloque `segundo` (`activo: false`, `nombre`, `motivo: "GitHub Models retirado el 30 jul 2026"`, `url`, `modelo`, `secreto`, `tiempo_s`, `max_por_ciclo`). Con `activo: false` la toma sale como hoy.

### B. La cadena de modelos (Q0)

7. **Gemini**: el plan viaja en la MISMA llamada de los ojos (no gasta otra petición del cupo gratis): `ojos_prompt.md` gana la sección «9. historia» y `esquema_respuesta()` el campo `historia` (opcional, no en `required`). `ojos.validar_respuesta` no rechaza la toma por la historia: la historia se valida aparte. Si viene y valida → `proveedor: gemini`.
8. **Segundo proveedor** (`historia.segundo`): cliente genérico chat/completions con transporte inyectable, 6 fotogramas JPEG pequeños (≤ 512 px de lado) en base64, las líneas transcritas y las señales de audio y escena; pide `response_format: json_object`; tiempo límite propio y recortado por `plazo`; 429/5xx/red = pasajero; nunca imprime el secreto. Con `activo: false` deja el renglón «segundo: apagado (GitHub Models retirado el 30 jul 2026)» y sigue. Pruebas con transporte falso (200 válido, 200 inválido, 429, sin secreto).
9. **Sin IA**: reglas deterministas con las señales del video (RMS por ventana de `sin_ia.analizar_video` y cambios de escena con PySceneDetect `ContentDetector` si está instalado; si no, el filtro `scene` de ffmpeg): apertura = 2.0 s alrededor del pico más alto del último 40 % del recorte; 2 o 3 momentos en los picos y cambios de escena más fuertes; tratamiento por regla (pico más fuerte → `sfx` golpe si está permitido y hay efecto, si no `zoom_suave`; un pico corto cerca del final → `camara_lenta`; el instante antes del remate → `pausa` si cabe). El mismo video da el mismo plan.
10. `planificar(...) -> Planificacion(plan, proveedor, intentos)`: prueba en orden Gemini → segundo → sin IA; el primero que valida gana. Si ninguno valida, `plan = None` y la toma se renderiza como hoy. **La historia nunca descarta ni gasta una fuente.** El plan de la toma guarda `historia: {proveedor, plan, mapa, intentos}` (o `{proveedor: null, motivo}`), y la fila del marcador lleva la columna `historia` con el proveedor (aporte de las #2 y #17: medir qué eslabón rinde).

### C. Render (`fabrica/editar/componer.py`)

11. `componer(..., historia=Planificacion|None)` sólo en tomas v2 con video y con audio en la fuente (sin audio: historia apagada con motivo). Una sola orden de ffmpeg con `trim/atrim` por tramo, `setpts=2*PTS` + `atempo=0.5` en la cámara lenta (antes de `fps=30`, para que una fuente de 60 fps quede fluida), `tpad=stop_mode=clone` + silencio en la pausa, zoom suave animado por tramo (`zoompan` o `scale`+`crop` por expresión, centrado, sin franjas), `concat` de video y audio sin fundidos, y los efectos con `adelay` + `amix=normalize=0` a `ganancia_sfx_db`. La orden es una función pura y se prueba sin correr ffmpeg.
12. `plan.duracion` = duración de salida; `origen` conserva `inicio` y `duracion` del recorte de fuente; las placas (`placas.construir`) reciben las líneas remapeadas y la duración de salida (cabecera, fila, mosca, diálogo y cierre como siempre).

### D. Puerta de estilo (`fabrica/editar/estilo.py`)

13. Con `plan.historia.plan`, los puntos que comparan contra la fuente (cuerpo, bordes, placas y audio original) usan `fuente_en(mapa, t)` y miden en instantes de **tramos planos** (velocidad 1, sin zoom, sin efecto); el audio original se compara en el tramo plano más largo sin efecto (≥ 2 s; correlación > 0.9 como hoy). Sin historia, la puerta queda exactamente igual (las pruebas viejas no cambian).
14. Puntos nuevos con historia: «[plan] Historia válida» (`validar_plan` otra vez sobre el plan guardado), «[plan] Historia sin texto propio» (el plan no trae claves ni textos fuera del esquema), «[fotograma] Apertura en frío = desenlace» (el cuadro de salida en la mitad de la apertura coincide con la fuente en `apertura` mejor que con la fuente en `inicio`), «[fotograma] Duración igual al mapa».

### E. Efectos de sonido CC0 (`marca/sfx/`)

15. `marca/sfx/efectos.yml` con **6 a 10 efectos** de packs CC0 de Kenney en OpenGameArt, leídos el 21 nov 2026 con «License(s): CC0»: «50 RPG sound effects» (https://opengameart.org/node/21999, `RPGsounds_Kenney.zip`) y «Sci-Fi Sounds» (https://opengameart.org/content/sci-fi-sounds, `sci-fi_sounds.zip`). Cada efecto con `id`, `tipo` (golpe, whoosh, boom, tic, …), `pagina`, `archivo`, `zip_miembro`, `licencia: CC0`, `asset`, `sha256`, `estado`, y su `marca/sfx/<id>/LICENCIA.md` (fuente, autor, licencia, enlace, corrida que lo leyó, sha256).
16. El audio no entra al repo: workflow `sfx.yml` (push a `main` y a `claude/clips-historia*` con cambios en `marca/sfx/**`, más `workflow_dispatch`) que reutiliza `fabrica/musica_release.py` generalizado (`--manifiesto`, `--release`, duración mínima y máxima por catálogo; la música sigue igual y sus pruebas en verde) y sube al release privado `sfx`. Si un `zip_miembro` no existe, el resumen lista los miembros del zip. Corrida real en verde con 6 o más efectos verificados.
17. `fabrica/editar/sfx.py` (como `editar/musica.py`): sólo efectos `verificada` con LICENCIA.md y sha256 del archivo bajado (`GRANJA_SFX_DIR`); sin efectos disponibles, la historia no usa `sfx`. `fabrica.yml` y `muestras.yml` bajan el release `sfx` (sin romper si falta: aviso visible).

### F. Muestra y pruebas

18. `muestras` renderiza 2 muestras nuevas con historia sobre material CC BY ya autorizado: `historia-bbb` (plan del eslabón sin IA) y `historia-sintel` (plan JSON fijo, como si lo devolviera el modelo, con cámara lenta, pausa, zoom suave y un efecto). Las dos pasan la puerta de estilo y quedan en el artefacto `muestras` con sus fotogramas.
19. `pruebas/test_historia.py`: esquema y validación (cada regla con su caso rojo), `mapa`/`fuente_en`/`remapear_lineas`, sin IA determinista con señales sintéticas, cadena con transportes falsos, orden de ffmpeg, render real corto con `testsrc` + seno que pasa la puerta, sin texto inventado, `sin_musica` sin efectos, historia rota → toma como hoy sin gastar. Suite entera en verde (`python -m pytest -q`) y `puerta` en verde en la PR.
20. Dependencia nueva aprobada en el plan: `scenedetect[opencv-headless]` con versión fija en `requirements.txt`; anotar en la PR cuánto suma a la instalación. Tiempo de la toma con historia medido en la muestra (render + puerta) y dentro del plazo de 13 min del ciclo.

## Fuera de alcance

Críticos y juez (#21), transcripción propia (#19), pantalla dividida (#16), puerta técnica (#17). Si alguna se fusiona antes, se rebasa; la pausa de 0.4 s y el congelado ya respetan la #17.

# Plan de la noche del 20 al 21 nov 2026: edición de nivel editor (idea del tweet de @everestchris6)

Lo escribe el jefe de Clips el 20 nov, 1:30 a. m. GT. Cada tarea programada de esta noche lee este archivo y también `procedimientos/plan-noche-2026-11-19.md` (reglas, ciclo de cada tarea y bitácora de la noche anterior). Meta: que cada clip se vea editado por una persona, no por una plantilla.

## Decisiones de Kevin (20 nov, 1:21 a. m.)

- **"Sí: todo"** a las 7 ideas del tweet (la versión que analizó el jefe): pantalla dividida automática, subtítulos por quién habla, cortes por audio (golpes y silencios), el edit como historia (abre con el desenlace; cada momento con un sentimiento y un tratamiento), revisiones por código antes de publicar, críticos que no vieron el plan más un juez, y "cada queja se vuelve regla".
- **No todo en Gemini.** Cadena de modelos, siempre Q0:
  1. Gemini (API gratis; el que responda según `diagnostico.yml`).
  2. **GitHub Models** (modelos de OpenAI con cupo gratis incluido en GitHub Pro; Kevin tiene Pro por el Student Pack). Desde Actions con `GITHUB_TOKEN` y `permissions: models: read`. Comprobar en la documentación oficial los límites gratis y qué modelos aceptan imágenes antes de usarlo. Sirve para el plan por momentos y para los críticos (sobre fotogramas y transcripción).
  3. Sin IA (reglas de código: picos de audio, silencios, cambios de escena).
  - La API de Claude y la de OpenAI directa cuestan dinero: rompen Q0 y no se usan en el ciclo diario. Claude sólo actúa en las tareas programadas de la noche, como juez de las muestras. Antigravity es un editor interactivo, no una API: no entra en el ciclo.
- Lo que no se copia del tweet: subtítulos dorados palabra por palabra (los prohíbe la sección 5B), música con derechos (sólo CC0), clips de Speed fuera de su campaña.
- Instagram: Meta lo rehabilitó el 20 nov (perfil público visible a la 1:25 a. m.) pero sigue **desconectado en Buffer** hasta que Kevin lo reconecte. Al volver, va con calentamiento (ver la decisión pendiente en el reporte).

## Herramientas para usar (estudio del tweet de @e1genqq, 20 nov 2:30 a. m.)

El tweet lista 20 repos libres para un «estudio de clips de GTA 6»; el video es una maqueta de interfaz y la cifra de US$10K no está comprobada. Lo que sirve, por tarea. Todo corre en GitHub Actions, sin pago, y con caché de pip para no gastar minutos. Preferir lo liviano: nada que arrastre `torch` si hay una opción sin él.

| Herramienta | Para qué nos sirve | Tarea | Nota |
|---|---|---|---|
| MediaPipe (detección de rostro) + OpenCV | Encontrar la facecam y seguir la acción para el recorte | #16 `dividida` | `mediapipe` es CPU y liviano; OpenCV `-headless` |
| faster-whisper (con `word_timestamps=True` y su filtro VAD Silero integrado) | Transcripción local y tiempos por palabra; detectar dónde hay voz | #19 `voces`, #17 | Sin `torch` (CTranslate2). Modelo `base` o `small`; medir tiempo dentro del plazo de 13 min |
| WhisperX | Alineación por palabra más fina | #17 (sólo si faster-whisper no llega a ±150 ms) | Pesado (`torch`): último recurso |
| Silero VAD | Segmentos de voz para cortar silencios y separar hablantes | #19 | Ya viene dentro de faster-whisper |
| Auto-Editor | Quitar silencios y aire muerto | #19 | Usar su lógica o su CLI con margen; cuidar que no corte risas o reacciones |
| PySceneDetect | Cambios de escena más fiables que el filtro de ffmpeg | #20 `historia`, corte sin IA de la #2 | `scenedetect[opencv-headless]` |
| TwitchDownloader (CLI) | Bajar la repetición del chat: los picos de mensajes y emotes marcan los mejores momentos | Nueva, después de esta noche (radar de momentos) | Sólo para fuentes autorizadas; para clips de Twitch ya tenemos las vistas como señal |
| Remotion | Plantillas animadas para informativas | Seguimiento | Licencia gratis para personas y equipos pequeños; pide Chrome en Actions (más minutos) |
| ClipsAI / FunClip | Partir videos largos en clips por transcripción | Seguimiento (campañas con VOD largo, p. ej. Speed) | ClipsAI usa pyannote (pide token de Hugging Face); FunClip está pensado para chino |
| yt-dlp, FFmpeg | Ya los usamos | — | — |
| OBS, Streamlink, Subtitle Edit, LosslessCut, OpenCut, MoviePy | No sirven para la fábrica (son de escritorio, graban directos sin permiso o duplican ffmpeg) | — | Descartados |

Dependencias nuevas aprobadas para esta noche dentro del «Sí: todo» de Kevin (gratis y sólo en la nube): `mediapipe`, `opencv-python-headless`, `faster-whisper`, `scenedetect`, `auto-editor`. Cada tarea las añade a `requirements.txt` con versión fija y anota cuánto suman al tiempo de instalación.

## Tareas (todas: PR lista para revisión contra `main`, nunca borrador; el jefe fusiona)

| # | Hora GT (20 nov) | Rama `claude/clips-…` | Criterios de aceptación |
|---|---|---|---|
| 16 | 7:45 p. m. | `dividida` | Detección de la facecam (rostro con ffmpeg/OpenCV sin dependencias de pago; si se añade `opencv-python-headless`, anotarlo como dependencia nueva), zona del juego y estorbos (alertas, chat, logos del stream). Formato 9:16 con facecam arriba (≈35 %) y juego abajo (≈65 %), sin franjas; si no hay facecam, la pantalla completa de la tarea #5. Pruebas con 3 fotogramas de muestra y 3 muestras renderizadas en el artefacto de `muestras` |
| 17 | 8:30 p. m. | `revisiones-codigo` | Puerta técnica por código antes de cualquier crítico: subtítulo dentro de ±150 ms de su palabra; ningún destello de 1 fotograma (negro o blanco) entre cortes; ningún congelado de más de 0.5 s; la música queda al menos 12 dB debajo de las voces (ducking con `sidechaincompress`); sin recortes de audio (clipping). Cada falla dice qué y dónde. Pruebas con videos sintéticos que fallan a propósito |
| 18 | 9:15 p. m. | `reglas-kevin` | `reglas-kevin.yml`: cada queja de Kevin con su regla y, si se puede medir, su revisión en la puerta (por ahora: «muy simple» → ninguna toma sin movimiento ni audio; «sin música» → toda pieza sin audio propio lleva pista CC0; «que el clip ocupe todo» → sin franjas). La puerta lee el archivo; una regla nueva sin revisión queda como nota para los críticos. Procedimiento en el archivo para añadir quejas nuevas |
| 19 | 10:00 p. m. | `voces` | Transcripción que no depende de Gemini: `faster-whisper` (modelo `small` o `base`, CPU) dentro de Actions como respaldo o principal, según tiempo y calidad medidos; separación de hablantes (el streamer de la facecam en un color de `marca.yml`, otras voces en otro); se cortan los silencios de más de 0.6 s y se marcan los golpes (picos de audio). Tiempo total del ciclo dentro del plazo de 13 min. Pruebas y una muestra |
| 20 | 10:45 p. m. | `historia` | El modelo (cadena Gemini → GitHub Models → sin IA) devuelve un plan por momentos en JSON: apertura en frío con el desenlace, 2 a 4 momentos, cada uno con sentimiento y tratamiento (zoom suave, cámara lenta, efecto de sonido CC0, pausa); el código valida el JSON y renderiza; sin texto inventado. `marca/sfx/` con 6 a 10 efectos CC0 con licencia. Muestra en el artefacto |
| 21 | 11:30 p. m. | `criticos` | Tras la puerta técnica, 3 críticos que no ven el plan (historia: gancho y ritmo; subtítulos: legibles y fuera de los botones de la app; técnica: encuadre, sonido, fallas) sobre 6 fotogramas + transcripción, con la cadena de modelos; un juez decide «pasa» o «todavía no» con motivos; «todavía no» vuelve al plan una vez, después se descarta y se anota. Cupos gratis respetados y medidos. Pruebas con respuestas falsas |
| 22 | 21 nov 12:30 a. m. | `ensayo-v3` | Ensayo de punta a punta con 2 fichas T reales y 1 FM27 usando todo lo de esta noche; MP4 en el artefacto; Claude (esta sesión) mira 4 fotogramas por clip y actúa como juez final; lo que no pase vuelve a la cola de seguimiento |

## Bitácora de la noche

- 20 nov 1:30 a. m. — jefe: plan escrito y tareas #16 a #22 programadas.
- 20 nov 2:30 a. m. — jefe: estudio de herramientas del tweet de @e1genqq añadido (tabla de arriba).

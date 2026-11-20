# Plan de la noche del 19 al 20 nov 2026: Granja autónoma desde el 20 nov

Lo escribe el jefe de Clips el 19 nov, 8:30 p. m. GT. Cada tarea programada de esta noche empieza leyendo este archivo. Meta única: **desde el viernes 20 nov la Granja publica sola 6 clips buenos al día por red, de streamers de GTA 6 (y después de cualquier juego), sin que Kevin intervenga.**

## Estado al empezar (19 nov, 8:20 p. m.)

- Publicado en total: 1 toma (Informativa GTA 6, 18 nov 7:00). Kevin pidió **borrarla** (no le gustó: muy simple). La borra el jefe desde el navegador; la fábrica no la vuelve a subir.
- El cron de GitHub disparó 3 veces en 24 h. La corrida 44609108976 (19 nov, 2:28 p. m.) se colgó 20 min sin log, GitHub la canceló y gastó el video ERIC ZZETA de FM27 en `estado/vistos.txt`.
- Gemini (ping del 19 nov, 7:20 p. m., corrida 44652467169): `gemini-3.8-flash` sin respuesta en 40 s; `gemini-3.7-flash` HTTP 503; **`gemini-3-flash-preview` HTTP 200 en 1 s**; las variantes `-lite` y `2.5` dan 404.
- `ojos-prueba` falla siempre: su muestra pesa 21.3 MB y el tope en línea es 14 MB.
- Twitch: claves presentes; id de Grand Theft Auto VI = `1867279146`.
- 847 pruebas en verde en `main`.
- **Instagram: el canal está DESCONECTADO en Buffer** (`isDisconnected: true`, 19 nov 7:28 p. m.) y el perfil @guionvivo sale «no disponible» sin sesión. Hasta que Kevin revise la cuenta y reconecte en Buffer, la fábrica publica sólo en Shorts y TikTok: ninguna tarea intenta reconectar ni crea cuentas. Si sigue así a las 6:45, va como alerta en el reporte.
- `PAUSA` puesta el 19 nov 7:30 p. m. para que no salga nada con el estilo viejo. La quita la tarea #13.
- Toma 1: borrada de YouTube el 19 nov 7:26 p. m. (Studio, Guion Vivo). TikTok e Instagram: el navegador interno no tiene sesión; la borra Kevin desde la app.

## Decisiones de Kevin (19 nov, 8:00 p. m.)

1. **Disparador: cron-job.org.** Kevin crea el token fino (sólo Actions de granja-clips) y lo pega él en cron-job.org. Nunca se pide una clave por el chat. Mientras no exista, el Vigilante lanza la fábrica con `gh workflow run`.
2. **Gemini: "Lite + sin IA"** y probar primero el modelo que responde hoy. Si ningún modelo responde, el corte se elige sin IA (picos de audio y cambios de escena con ffmpeg). Q0, sin pago.
3. **Ficha T (sin permiso, con crédito): sólo streamers medianos y pequeños**, en español, de 1,000 a 300,000 seguidores. Lista negra fija: Rubius, AuronPlay, Ibai, Spreen, Westcol, Juan Guarnizo y cualquier canal de YouPlanet o agencia que reclame Content ID. Ante un reclamo: retiro el mismo día, veto del streamer, alerta roja, y se sigue con los demás.
4. **Ritmo: 6 clips al día por red**, "bien hechos y editados".
5. **Estilo (manda sobre `marca.yml` y la sección 5B del playbook):**
   - Si es video: **se deja el audio del video** y **el clip ocupa todo el cuadro 9:16** (nada de franjas grandes ni marcos que achiquen el clip). La marca Guion Vivo queda como capa encima, discreta.
   - Si es foto o texto (informativa): **lleva audio o canción** y movimiento. Música sólo con licencia libre comprobable (CC0 o equivalente con uso comercial y sin Content ID), con la licencia anotada en el repo. En campañas que digan `sin_musica: true`, no lleva música.
   - Se borra la toma 1 de las tres redes.

## Reglas que no cambian

- Presupuesto Q0. Nada de pago ni pruebas con tarjeta. Ningún video toca la PC de Kevin.
- Lista blanca de `fuentes.yml`: tipos A, B, C, D y ahora **T** (decisión de Kevin del 18 nov, 00:20, acotada el 19 nov). Nunca filtraciones, spoilers, finales, cinemáticas aisladas, trucos ni la radio del juego.
- Sin puerta de estilo en verde no se publica. Nada que parezca plantilla ni hecho con IA (lista prohibida de la sección 5B).
- Alerta roja (lo único que interrumpe a Kevin): retiro, strike, aviso de cuenta, pagos, clave expuesta. Se avisa con PushNotification si está disponible y se anota en `retiros.md` o `registro.md`.
- No se automatiza el envío de enlaces a Content Rewards ni Clipeamos (riesgo de bloqueo). Los deja `envios-pendientes.csv`.

## Ciclo de cada tarea

1. Preparación: `add_repo` de `ksflores-cmyk/granja-clips` (push) y `ksflores-cmyk/cuartel-general` (push); clonar con `--depth 50`. `gh` funciona por el proxy aunque `gh auth status` diga que el token no vale.
2. Leer este archivo, `PLAYBOOK.md` (secciones 3, 5B, 8 y 12), `aprendizajes.md` y la sección "Bitácora de la noche" de abajo.
3. Rama `claude/clips-<tema>` en granja-clips. Si otra rama `claude/clips-*` abierta toca los mismos archivos, rebasar sobre `main` antes de fusionar.
4. Constructor: subagente (Sonnet). Criterios de aceptación binarios en el encabezado de la tarea.
5. Puerta: `python -m pytest -q` entero en verde, más las pruebas nuevas de la tarea. Sin LLM.
6. Revisores: subagente `revisor` (u otro modelo) y **Codex en la PC de Kevin** si responde (Desktop Commander: `cd C:\Users\Kevin\Documents\GitHub\granja-clips; git fetch; git diff origin/main...origin/claude/clips-<tema> > $env:TEMP\granja\diff.txt; Get-Content $env:TEMP\granja\diff.txt -Raw | codex.cmd exec --sandbox read-only "Revisa este diff..."`). Si la PC no responde, segundo revisor en la nube con otro modelo. Cada uno cierra con `OPINIÓN:` y `APORTE EXTRA:`.
7. Máximo 2 rondas. Puerta verde y sólo detalles: se fusiona a `main` (rebase), se borra la rama remota y los detalles van a "Seguimiento". Si algo rompe, expone datos o es de seguridad: no se fusiona y se anota.
8. Prueba con corrida real: lo que toque la fábrica se demuestra con una corrida de `fabrica` o `publicar-ensayo` en verde (enlace a la corrida). Nada de "debería funcionar".
9. Cierre: añadir una entrada a "Bitácora de la noche" de este archivo (hora, tarea, rama, corrida, resultado, opinión y aporte) con commit directo a `main` (`[skip ci]` en el mensaje).

## Tareas

| # | Hora GT | Tema (rama `claude/clips-…`) | Criterios de aceptación |
|---|---|---|---|
| 1 | 19 nov 7:45 p. m. | `blindaje` | Cada llamada externa (Drive, Gemini, yt-dlp, Buffer, Supabase, Twitch) con tiempo límite propio; log de inicio y fin de cada etapa con segundos; el ciclo completo termina solo antes de 15 min; una fuente sólo se anota en `vistos.txt` cuando la toma se programó o el fallo es del material (no por red ni tiempo); se quita ERIC ZZETA de `vistos.txt`; prueba que simula un cuelgue y comprueba que la fuente no se gasta |
| 2 | 8:30 p. m. | `gemini` | Orden de modelos según el ping (`gemini-3-flash-preview` primero); `ListModels` al empezar para descartar los que no existen; tiempo límite por intento; si todos fallan, `fabrica/editar` elige el corte sin IA (picos de volumen y cambios de escena de ffmpeg) y la toma sigue por la puerta de estilo; `ojos-prueba` usa la copia ligera (≤14 MB) y sale en verde con una corrida real |
| 3 | 9:15 p. m. | `release-fm27` | Workflow que baja una vez los 5 videos de FM27 de Drive y los sube a un release privado `fuentes-fm27` del repo; `fuentes.yml` apunta al release y la descarga usa `GITHUB_TOKEN`; el orden de los 5 rota; si Drive vuelve a dar cuota, el workflow reintenta más tarde sin romper nada; corrida real en verde con los 5 en el release |
| 4 | 10:00 p. m. | `buscador-t` | Detector por juego: streams en vivo y clips de las últimas 24 h de `game_id` 1867279146, `language=es`; seguidores del canal entre 1,000 y 300,000; `config/vetados.yml` con la lista negra de Kevin; ficha T generada con crédito (`twitch.tv/<login>`), `origen: twitch`, `estado: vigente`, `uso: produccion`; cupo de ficha T por día y por streamer (máximo 2 por streamer al día); `retiros.md` veta solo; pruebas con transporte falso; corrida de diagnóstico real que lista 10 candidatos |
| 5 | 10:45 p. m. | `pantalla-completa` | Plantilla v2: el clip llena el 9:16 (recorte centrado o con detección de rostro/acción, nunca franjas grandes); audio original del clip; capas Guion Vivo encima (encabezado de toma compacto, diálogo, crédito y mosca) sin tapar la acción; puerta de estilo actualizada con la regla de pantalla completa; 3 muestras renderizadas como artefacto de la corrida `muestras` |
| 6 | 11:30 p. m. | `musica` | `marca/musica/` con 6 a 10 pistas de licencia libre comprobada, cada una con su `LICENCIA.md` (fuente, autor, licencia, enlace); la informativa y toda pieza sin audio llevan pista y movimiento (paneo o entrada de texto, sin plantilla); `sin_musica: true` lo respeta; volumen normalizado; muestra en artefacto |
| 7 | 20 nov 12:15 a. m. | `formatos` | Formatos "Mejores momentos" y "Mejores reacciones": 2 o 3 clips del mismo día, de 30 a 60 s, corte seco entre ellos, crédito a cada streamer en pantalla y en el texto; pilar nuevo en `temporada.yml`; pruebas; muestra en artefacto |
| 8 | 1:00 a. m. | `textos` | Título, descripción y hashtags por red en español; crédito siempre; `#Publicidad` y etiquetas del brief en campañas; ganchos fuera de la lista prohibida; límites de caracteres de cada red; pruebas por red |
| 9 | 1:45 a. m. | `ritmo` | 6 huecos al día por red (9, 11, 13, 16, 19 y 22 h GT) en `config/horarios.yml`; cupos en `temporada.yml`; cola de Buffer de 10 o menos por canal; no se repite el mismo clip en 24 h; si un hueco queda vacío, el siguiente ciclo lo rellena; cambio del cron de GitHub a un minuto impar (respaldo de cron-job.org) |
| 10 | 2:30 a. m. | `retiros` | Cada ciclo revisa en Buffer los posts con error o retirados; un retiro o reclamo veta al streamer en `config/vetados.yml`, anota en `retiros.md` y abre un aviso de alerta roja (issue con etiqueta `alerta-roja`); pruebas |
| 11 | 3:15 a. m. | `ensayo` | Corrida en modo ensayo de punta a punta con 1 ficha T real, 1 FM27 y 1 informativa con música; las 3 pasan la puerta de estilo; MP4 en el artefacto de la corrida y enlace en la bitácora |
| 12 | 4:00 a. m. | `revision` | Codex y un revisor leen todo lo fusionado esta noche (`git diff` desde `3fa9695`); se corrigen los hallazgos que rompen algo (máx 2 rondas); 847 + nuevas en verde |
| 13 | 4:45 a. m. | `encendido` | Quita el `PAUSA` que puso el jefe el 19 nov (sólo si el estilo v2 de las tareas #5 y #6 está fusionado y su ensayo en verde; si no, lo deja y lo dice) y deja sin `PAUSA`, `modo: vivo`; corrida real de `fabrica` que programa el hueco de las 9:00 en las 3 redes; comprobación del disparador (cron-job.org si Kevin lo creó; si no, lo dice); cola de Buffer con las tomas de 9:00 a 13:00 |
| 14 | 5:30 a. m. | `informativas` | 3 informativas del día de GTA 6 con datos de fuentes verificadas en `hechos.md` (con enlace y fecha), con música y movimiento; nada de spoilers ni filtraciones |
| 15 | 6:45 a. m. | reporte | Reporte corto para Kevin: qué se publicó (enlaces), qué falló, qué decide él (palabras clave). Único aviso de la mañana |
| 16 | cada 3 h | vigilante | Si van más de 3 h sin publicar en horario (9 a 22 h), diagnostica con los logs, repara lo pequeño, lanza `fabrica` con `gh workflow run`; nunca más de 1 lanzamiento por pasada |
| 17 | diario 10:13 a. m. | radar | Campañas nuevas con material autorizado (Content Rewards, Clipeamos, Whop) para GTA 6 y streamers en español; streamers medianos nuevos; reclamos conocidos para la lista negra. Anota en `fuentes-candidatas.md`; unirse a una campaña lo hace Kevin |
| 18 | diario 11:41 p. m. | cierre | Marcador con vistas (Buffer), aprendizajes, estado en `cuartel-general` rama `claude/clips-diario` (`estado/proyectos/clips.md`) |

## Seguimiento

(detalles que no bloquean, uno por línea)

## Bitácora de la noche

- 19 nov 8:20 p. m. — jefe: ping de Gemini añadido a `diagnostico.yml` (corrida 44652467169). Plan escrito y 18 tareas programadas (ids `trig_01UG3M…` a `trig_01VCGM…`).
- 19 nov 7:28 p. m. — jefe: toma 1 borrada de YouTube; Instagram desconectado en Buffer (ver Estado).

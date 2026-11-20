# Tarea #17 `revisiones-codigo`: puerta técnica por código (rama `claude/clips-revisiones-codigo`)

Plan: `procedimientos/plan-noche-2026-11-20.md` (decisión de Kevin del 20 nov, 1:21 a. m.: «Sí: todo» a las 7 ideas del tweet; una es «revisiones por código antes de publicar»). Reglas y ciclo: `procedimientos/plan-noche-2026-11-19.md`.

Meta: antes de cualquier crítico con IA (#21), una puerta **sin LLM** mide sobre el MP4 lo que un editor humano nunca dejaría pasar. Cada falla dice **qué** y **dónde** (segundo y cuadro).

## Criterios de aceptación (binarios)

1. Subtítulo a ±150 ms de su palabra: cada línea con voz (`frase` y `original` no vacíos) empieza a ≤ 0,15 s del inicio de su primera palabra en la transcripción por palabra del plan. Falla con «línea N «texto»: empieza +0,42 s después de «palabra» (3,10 s)» o «no se encontró «palabra» cerca de 3,10 s».
2. Ningún destello de 1 cuadro (negro o blanco) entre cortes: falla con el segundo y el número de cuadro.
3. Ningún congelado de más de 0,5 s en piezas con video: falla con el tramo (inicio–fin en s).
4. Música al menos 12 dB debajo de las voces, con ducking por `sidechaincompress`: falla con los tramos donde no se cumple.
5. Sin recortes de audio (clipping): falla con los instantes.
6. La puerta corre en `hacer_toma`, en la informativa, en el compilado entero, en `muestras` y en `prueba_ojos`; un punto rojo descarta la toma con el motivo «puerta técnica: …».
7. Pruebas con videos sintéticos que fallan a propósito (y su versión limpia que pasa) para cada punto. `python -m pytest -q` entero en verde.

## Qué se construye

1. **`config/tecnica.yml`** (umbrales, todos con comentario), validado en `fabrica/validar.py` (`validar_tecnica`):
   - `subtitulos: {tolerancia_s: 0.15, ventana_s: 1.5, parecido_min: 0.75, exigir_transcripcion: false}`. `exigir_transcripcion: false` mientras no entre la #19 (`voces`): sin transcripción el punto sale «no aplica» con ese motivo; con `true`, una pieza con voz sin transcripción sale roja.
   - `destellos: {negro_max: 32, blanco_min: 224, fraccion_min: 0.6, salto_min: 0.4, cuadros_max: 2}`.
   - `congelados: {max_s: 0.5, dif_percentil: 99.5, dif_max: 3.0}` (en gris 0-255).
   - `ducking: {margen_db: 12, ventana_s: 0.1, voz_activa_db: -40, tolerancia_fraccion: 0.02, max_seguidas: 3, sidechain: {threshold, ratio, attack, release, makeup}, musica_base_db}`.
   - `clipping: {umbral: 0.99, muestras_seguidas: 3}`.
   - `analisis: {ancho: 135, alto: 240}`.
2. **`fabrica/editar/tecnica.py`** (sólo ffmpeg/ffprobe, numpy y la biblioteca estándar; nada nuevo en `requirements.txt`):
   - `cargar(raiz) -> dict` con los umbrales.
   - Una sola pasada de ffmpeg para los cuadros (`scale=ancho:alto,format=gray`, `-f rawvideo` por tubería, leídos cuadro por cuadro sin guardar todo el video en memoria) y una para el audio (`f32le`, canales nativos). Tiempo límite con `plazo.tiempo_para`.
   - `destellos(...)`: un tramo de 1 a `cuadros_max` cuadros seguidos con `fraccion_min` o más de píxeles ≤ `negro_max` (o ≥ `blanco_min`) cuyos vecinos de los dos lados (o del único lado, en el primer y el último cuadro) tienen al menos `salto_min` menos de esa fracción. Un fundido a negro de 3 cuadros o más y una escena oscura larga pasan.
   - `congelados(...)`: pares de cuadros seguidos cuyo percentil `dif_percentil` de |diferencia| es ≤ `dif_max`; un tramo de k cuadros iguales dura k/fps; rojo si dura más de `max_s`. Sólo en piezas con video (`plan.origen` o plantilla v2); sin video: «no aplica (pieza sin video: su movimiento lo mide la puerta de estilo)».
   - `clipping(...)`: tramos de `muestras_seguidas` o más muestras seguidas del mismo canal con |x| ≥ `umbral`; se informan los primeros 5 instantes y el total.
   - `ducking(voz, musica, cfg)`: sobre las pistas separadas (voz y música ya procesada), ventanas de `ventana_s`; voz activa = RMS ≥ `voz_activa_db`; rojo si más de `tolerancia_fraccion` de las ventanas con voz tienen la música a menos de `margen_db` debajo de la voz, o si hay `max_seguidas` ventanas malas seguidas. En el plan: `audio.tipo == "mezcla"` exige las pistas (`pistas={"voz": Path, "musica": Path}`); sin mezcla de música y voz: «no aplica (la pieza no mezcla música con voz)».
   - `subtitulos(plan, cfg)`: lee `plan["transcripcion"]["palabras"]` = `[{"texto", "t0", "t1"}]` en el tiempo de la toma (contrato para la #19). Primera palabra de `original` normalizada (sin acentos, minúsculas, sin puntuación), parecido con `difflib` ≥ `parecido_min` entre las palabras con `t0` dentro de ±`ventana_s` del inicio de la línea; la más cercana.
   - `revisar(video, plan, cfg, *, pistas=None) -> list[tuple[str, bool, str]]` con los 5 puntos, cada nombre empieza con `[técnica]`.
   - CLI `python -m fabrica.editar.tecnica VIDEO PLAN [--voz W --musica W] [--raiz R]`: imprime los puntos y sale 0 (verde) o 1 (rojo).
3. **`fabrica/editar/mezcla.py`**: `filtro_ducking(cfg)` y `mezclar(voz, musica, salida, *, duracion, cfg) -> dict` que mezcla la música debajo de la voz con `sidechaincompress` (la voz es la cadena lateral) y deja también la pista de música ya comprimida para que la puerta la mida. Hoy ninguna pieza mezcla música con voz (`componer` lo prohíbe); queda lista para los efectos de sonido de la #20 y cualquier mezcla futura.
4. **Conexión**: `toma._hacer_toma` corre la puerta técnica justo después de `componer` y antes de la puerta de estilo; si sale roja, descarta sin correr la de estilo (ahorra ~70 s), `gastar=True` igual que la puerta de estilo. `informativa.armar`, `compilado.compilar` (sobre el compilado entero) y `muestras` / `prueba_ojos` suman sus puntos a los de la puerta de estilo.

## Fuera de alcance

- Transcribir (eso es la #19); críticos con IA (#21); arreglar el material (un clip T que ya viene recortado se descarta con su motivo).

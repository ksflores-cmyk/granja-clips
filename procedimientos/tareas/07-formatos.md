# Tarea #7 `formatos`: «Mejores momentos» y «Mejores reacciones» (rama `claude/clips-formatos`)

Plan: `procedimientos/plan-noche-2026-11-19.md`. Pedido de Kevin: formatos con valor añadido. Un **compilado** junta 2 o 3 clips del **mismo día** (fichas T o A vigentes), dura de **30 a 60 s**, pasa de un clip a otro con **corte seco** (sin transición), da **crédito a cada streamer en pantalla y en el texto**, y tiene **pilar propio** en `temporada.yml` con su peso.

## Plantilla

La plantilla de pantalla completa (tarea #5, rama `claude/clips-pantalla-completa`) **no está fusionada** al escribir esto (20 nov, 12:30 a. m. GT). Por eso el compilado **no dibuja nada propio**: cada segmento se renderiza con `fabrica.editar.componer.componer()` (la plantilla vigente en `main`) y pasa por `fabrica.editar.estilo.revisar()` (la puerta vigente). La firma de `componer` es la misma en `main` y en la rama #5, así que al fusionar la #5 el compilado sale en pantalla completa sin tocar este código. El plan del compilado anota qué plantilla usó (`plan["plantilla"]` del segmento, o `"v1-pagina"` si no trae).

## Qué se construye

1. **`config/formatos.yml`** (nuevo): los dos formatos y sus límites. Sin números escritos a mano en el código.
   ```yaml
   pilar: compilados
   duracion_s: [30, 60]          # del compilado entero
   segmentos: [2, 3]
   segmento_s: [10, 25]          # recorte que se pide a Gemini por segmento (dentro de 7-30 de la puerta)
   max_por_creador: 2            # segmentos del mismo creador en un compilado
   max_por_dia_por_plataforma: 1
   formatos:
     mejores-momentos:
       nombre: "Mejores momentos"
       pilar_segmento: momentos
       gancho: "Mejores momentos del día"
       palabra_lapiz: "MOMENTOS"
       hashtags: ["#MejoresMomentos", "#Streamers"]
     mejores-reacciones:
       nombre: "Mejores reacciones"
       pilar_segmento: primeras_veces
       gancho: "Mejores reacciones del día"
       palabra_lapiz: "REACCIONES"
       hashtags: ["#Reacciones", "#Streamers"]
   ```
   `formatos.cargar_config(raiz)` la lee y la valida (tipos, rangos, gancho de 6 palabras o menos, `palabra_lapiz` dentro del gancho, gancho fuera de los ganchos genéricos de `marca.yml`, `pilar` presente en `temporada.yml`); lanza `ValueError` con el motivo.

2. **`temporada.yml`**: pilar nuevo `compilados: 0.1` (comentario: «Mejores momentos / Mejores reacciones: 2-3 clips del día, 30-60 s»); `primeras_veces` baja de 0.6 a 0.5 para que la suma siga en 1.0. Tocar sólo el bloque `pilares:` (la rama #4 añade `hashtags:` arriba; que el rebase no choque).

3. **`fabrica/formatos.py`** (nuevo), lógica sin video:
   - `@dataclass Segmento`: `fuente: Path`, `inicio`, `duracion`, `toma: Toma` (numero, gancho, palabra_lapiz, lapiz, escena, credito), `lineas: list[Linea]`, `ficha: dict`, `creador: str`, `fecha: date` (día local GT del clip), `id_origen: str`.
   - `apto(ficha, hoy, pilar_segmento, muestra=False) -> (bool, motivo)`: tipo `T` o `A`; `es_publicable(ficha, hoy, None, pilar_segmento)`; una ficha `A` sólo si declara `reglas.permite_compilados: true` (hoy ninguna: FM27 queda fuera por su brief de 10-30 s). Con `muestra=True` (sólo el comando de muestras) se aceptan fichas `uso: muestras` y nunca se publica.
   - `elegir_candidatos(candidatos, fichas, formato, ahora, cfg) -> list`: sólo candidatos aptos con `creado_en` del mismo día local (zona `America/Guatemala`) que `ahora`, por `vistas_por_hora` de mayor a menor, máximo `max_por_creador` por creador, máximo `segmentos[1]` elegidos; sin `id_origen` repetido. Si quedan menos de `segmentos[0]`, lista vacía.
   - `combinar(segmentos, cfg) -> list[Segmento] | None`: en el orden dado, el primer subconjunto (prefijo de 3, luego de 2) cuya suma de duraciones cae en `duracion_s`; `None` si no hay. Todos del mismo día; si no, `None`.
   - `toma_de_segmento(...)`: el segmento 1 lleva el **gancho del formato** y su `palabra_lapiz`; los demás, su propio gancho (el de Gemini). Todos con el mismo número de toma, su línea de escena y `credito = "Con: <creador>"` (en ficha T, `twitch.tv/<login>`). Así cada streamer sale en pantalla en el cierre de su segmento (`CORTE.` + crédito) y, en la v2, en la fila mínima todo su segmento.
   - `textos_compilado(compilado, plataforma, temporada, cfg, raiz=None, hoy=None) -> {texto, titulo, credito, etiquetas}`: reutiliza de `fabrica.publicar` `Descartada`, `contar_emojis`, `MAX_TEXTO`, `_titulo_youtube`, `_limpio` y `_comprobar_repetido` (sin modificar `publicar.py`: la tarea #8 lo está cambiando). Formato:
     ```
     Toma 012. Mejores momentos del día en GTA 6

     [sólo Reels: «3 momentos de hoy, de 3 streamers.»]

     🎥 twitch.tv/a · twitch.tv/b · twitch.tv/c
     [divulgación de la ficha si la hay]
     #GTA6 #MejoresMomentos #Streamers
     ```
     Un solo emoji. **Cada creador aparece en la línea del crédito** (si falta uno, `Descartada`). Hashtags: obligatorias de las fichas, luego `temporada.hashtags` si existe, luego las del formato; Shorts/TikTok hasta 3, Reels de 3 a 5. El nombre del juego sale de `temporada.nombre` (canal permanente). Límite `MAX_TEXTO`.
   - `fila_marcador(compilado, ahora) -> dict`: como `toma.fila_marcador`, con `pilar: compilados`, `fuente_id` = ids unidos con `+`, `credito` = los créditos unidos con ` · `, `duracion_s` total.
   - `hacer_compilado(raiz, formato_id, candidatos, fichas, ahora, *, salida=None, hacer_segmento_fn=None, ...) -> Resultado` (el `Resultado` de `fabrica.editar.toma`): elegir → preparar cada segmento (por defecto `preparar_segmento`: descarga con `descargar.descargar` dentro de un `ExitStack` para que las fuentes vivan hasta componer, `ojos.mirar` con `recorte` = `segmento_s` ∩ el rango de la ficha, `ojos.validar_respuesta`, `toma.armar`) → combinar → `compilado.compilar` → puertas. Cualquier NO es descarte con todos sus motivos; nada sube sin control (como `hacer_toma`). Nunca publica ni sube el contador. **No se conecta al ciclo en esta tarea** (lo hará una tarea de cola cuando #4 y #9 estén fusionadas); pruebas con `hacer_segmento_fn` falso.

4. **`fabrica/editar/compilado.py`** (nuevo), el video:
   - `compilar(marca, formato, segmentos, salida, *, trabajo) -> (plan, puntos)`: renderiza cada segmento con `componer()` a `trabajo/seg-N.mp4` y corre `estilo.revisar(seg, plan_seg, marca, ficha=seg.ficha)`; si algún segmento sale en rojo, `ValueError` con los motivos. Después concatena con **un solo** ffmpeg (`concat` del filtro, `n=N:v=1:a=1`), re-codificado a la misma salida de siempre: 1080x1920, H.264 high yuv420p 30 fps `-preset veryfast -crf 20`, AAC 128 kb/s 48 kHz, `+faststart`. **Sin fundidos, sin xfade, sin acrossfade**: corte seco. Audio: el de cada segmento (el original de su clip). Escribe `plan_compilado.json`.
   - Plan del compilado: `{tipo: "compilado", formato, pilar, numero, fecha, duracion, plantilla, creditos: [...], segmentos: [{n, t0, t1, duracion, fuente_id, creador, credito, id_origen, sha256_mp4, plan, puerta_ok}]}`.
   - `revisar_compilado(mp4, plan, segmentos_mp4, cfg) -> list[(punto, ok, detalle)]`, sin LLM:
     - `[compilado] 2 o 3 segmentos` (de `cfg`).
     - `[compilado] Duración de 30 a 60 s` y `[compilado] Duración igual a la suma de los segmentos` (tolerancia 0.15 s por segmento).
     - `[fotograma] 1080x1920`, `H.264 + AAC`, `30 fps`, `faststart` (reutilizar `estilo.sondear` y `estilo.faststart`).
     - `[compilado] Mismo día` (todas las fechas iguales).
     - `[compilado] Crédito de cada segmento` (el `credito` del plan de cada segmento contiene a su creador, y la lista `creditos` los tiene a todos).
     - `[compilado] Puerta de estilo de cada segmento en verde`.
     - `[fotograma] Corte seco en cada unión`: en cada unión `t_b`, el fotograma de `t_b − 2/30` coincide con el último de su segmento y el de `t_b + 1/30` con el primero del siguiente (diferencia media de gris en miniatura por debajo de un umbral calibrado); además un fotograma a la mitad de cada segmento coincide con su segmento. Un fundido o un fotograma de otra cosa sale en rojo.
     - `[audio] Audio de cada segmento`: el MP4 tiene una sola pista AAC y, en el centro de cada segmento, su audio correlaciona con el del segmento (> 0.9) cuando el segmento tiene audio.
     - `[compilado] Nada de transiciones`: el plan no declara transiciones y `marca.yml` sigue con `movimiento.transiciones: ninguna`.
   - CLI de muestras: `python -m fabrica.editar.compilado --muestras --salida DIR` lee `marca/muestras/formatos.yml`, baja cada fuente con `muestras.descargar` (sha256 verificado), renderiza, corre las puertas y escribe en `DIR`: los MP4, `plan_compilado.json` de cada uno, 3 fotogramas por compilado (en una unión, a la mitad, al final), `textos.md` (el texto de cada red generado por `textos_compilado`, con `raiz=None`) y `puerta-formatos.md` (todos los puntos, segmentos y compilado). Sale 1 si algo sale en rojo. La carpeta de salida no puede estar dentro del repo.

5. **`marca/muestras/formatos.yml`** (nuevo): 2 compilados con material CC BY ya autorizado para muestras (`bbb-cc-by`, `sintel-cc-by`; mismas URL y sha256 que `muestras.yml`). Créditos distintos por fuente: `Con: Blender Foundation (Big Buck Bunny)` y `Con: Blender Foundation (Sintel)`.
   - `mejores-momentos` (3 segmentos, ≈ 45 s, número de toma 4): BBB 40.0–54.0 (gancho del formato; acotación `(sale sin prisa)` 7.5–13.4; escena `EXT. PRADERA — MAÑANA`); Sintel 18.2–33.2 (gancho `El chamán la regaña`, lápiz `CHAMÁN`, escena `INT. CABAÑA DEL CHAMÁN — NOCHE`; líneas de CHAMÁN relativas: 0.5–3.3 «Esta espada tiene un pasado oscuro.», 10.8–14.2 «Eres un necio por viajar sola y sin nada preparado.» — si una frase no cabe en 2 líneas, acortarla); Sintel 39.6–56.6 (gancho `Ella busca a un dragón`, lápiz `DRAGÓN`, las 4 líneas de la muestra `dialogo-sintel`).
   - `mejores-reacciones` (2 segmentos, ≈ 31 s, toma 5): Sintel 39.6–56.6 (gancho del formato) y BBB 40.0–54.0 (gancho `El conejo sale a la luz`, lápiz `LUZ`).
   - `fecha`: el día de la corrida para todos (mismo día). `muestra: true`.

6. **`.github/workflows/formatos.yml`** (nuevo): `workflow_dispatch` y `push` a `claude/clips-formatos` y `main` con paths `fabrica/**`, `config/formatos.yml`, `marca/**`, `marca.yml`, `temporada.yml`, el propio workflow; `permissions: contents: read`; ffmpeg con `./.github/actions/ffmpeg`; `pip install -r requirements.txt`; `python -m fabrica.editar.compilado --muestras --salida "$RUNNER_TEMP/formatos"`; resumen de `puerta-formatos.md` y `textos.md` al `GITHUB_STEP_SUMMARY`; artefacto `formatos` (3 días, `if-no-files-found: error`); `timeout-minutes: 15`. Igual que `muestras.yml`.

## Criterios de aceptación (binarios)

1. `config/formatos.yml` existe y `cargar_config` lo acepta; con gancho de 7 palabras, `palabra_lapiz` fuera del gancho, gancho genérico, `segmentos: [1, 3]` o pilar ausente de `temporada.yml`, lanza `ValueError` (una prueba por caso).
2. `temporada.yml` tiene `pilares.compilados` > 0 y la suma de pilares es 1.0 (prueba); `python -m fabrica.validar` sale 0.
3. `elegir_candidatos`: descarta otro día (zona GT, prueba con un clip de las 23:30 GT del día anterior), ficha vencida, `uso: muestras`, tipo B/C/D, ficha A sin `permite_compilados`, pilar que no está en la ficha y un 3.º segmento del mismo creador; ordena por `vistas_por_hora`; con menos de 2 aptos devuelve `[]`.
4. `combinar`: 3 de 15 s → los 3 (45 s); 3 de 25 s → los 2 primeros (50 s); 2 de 12 s → `None` (24 s); 3 de 28 s con el prefijo de 2 en 56 s → 2; mezcla de días → `None`.
5. Con fuentes sintéticas (testsrc2 + tono, como las pruebas existentes), `compilar` produce un MP4 1080x1920 H.264/AAC 30 fps con faststart, de duración = suma (±0.15 s por segmento), y `revisar_compilado` sale **todo en verde**.
6. `revisar_compilado` sale **en rojo** con: (a) un compilado hecho con `xfade` de 0.5 s entre segmentos (corte no seco), (b) 1 solo segmento, (c) 25 s en total, (d) un segmento con crédito vacío o que no nombra a su creador, (e) fechas distintas, (f) el audio de un segmento reemplazado por silencio.
7. `textos_compilado` para shorts, tiktok y reels: cada creador aparece en la línea del crédito, un solo emoji, `Toma nnn.` al inicio, el nombre del juego de `temporada.nombre`, Shorts/TikTok ≤ 3 hashtags, Reels de 3 a 5, ≤ `MAX_TEXTO`; Shorts trae `titulo`. Si se quita un creador de la línea, `Descartada`.
8. `hacer_compilado` con `hacer_segmento_fn` falso (fuentes sintéticas): ok con 3 segmentos y `fila` con `pilar: compilados`; descarte con motivo cuando sólo hay 1 apto, cuando la suma no cae en 30-60 y cuando un segmento sale rojo en la puerta de estilo; nunca deja MP4 en un descarte.
9. `python -m fabrica.editar.compilado --muestras --salida /tmp/x` en local genera los 2 compilados con todo en verde (sale 0) y `textos.md` con los créditos.
10. `python -m pytest -q` entero en verde (base: 847 en `main`) más las nuevas. Sin dependencias nuevas, ningún video ni binario en el repo, ningún archivo de `main` reescrito salvo `temporada.yml` (bloque `pilares`). Nada de la lista prohibida de la sección 5B (sin fundidos, zoom ni barras).

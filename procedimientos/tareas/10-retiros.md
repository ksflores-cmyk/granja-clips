# Tarea #10 `retiros`: reclamos y retiros vetan solos (rama `claude/clips-retiros`)

Plan: `procedimientos/plan-noche-2026-11-19.md`. Decisión de Kevin (19 nov, 8:00 p. m.): ficha T sólo con streamers medianos y pequeños; **ante un reclamo: retiro el mismo día, veto del streamer, alerta roja, y se sigue con los demás.** Con la ficha T el riesgo real son los reclamos (3 strikes en YouTube borran el canal).

## Hechos comprobados (20 nov, 2:32 a. m., lectura con la API de Buffer)

- `Post.status` vale `draft`, `needs_approval`, `scheduled`, `sending`, `sent` o `error`. Un post en `error` trae el campo `error` con el mensaje de la falla; en los demás `error` es `null`.
- **Buffer no se entera de lo que la red borra después**: la toma 1, borrada de YouTube y TikTok el 19 nov, sigue `sent` con `error: null` en Buffer. Un retiro posterior a la publicación sólo llega por (a) un post que Buffer marca `error` con un mensaje de derechos/reclamo, o (b) una fila que Kevin o el jefe escriben a mano en `retiros.md` (por ejemplo, tras el correo de YouTube a guionvivo.clips).
- Organización única `6afd22e4dfdc69bf66537a76`; canales en `config/canales.yml`.

## Qué se construye

1. **Módulo nuevo `fabrica/retiros.py`** (sólo urllib vía `publicar.ClienteBuffer`; nada nuevo en `requirements.txt`):
   - `revisar(raiz, cliente, canales: dict, org_id, ahora, out, escribir=True) -> Informe`: una consulta paginada (máx. 3 páginas de 50) `posts(input: { organizationId, filter: { status: [error], channelIds: [...] } })` con `id status dueAt text` y el campo de error que muestre la introspección (`error` escalar, o `error { message … }` si es objeto: se seleccionan sólo los subcampos que existan entre `message`, `rawError`, `details`, `code`). Si `Post` no tiene `error`, se lee sin él y el motivo queda vacío.
   - Cada post se procesa **una sola vez**: `estado/retiros-vistos.json` guarda los `post_id` ya clasificados (se anota antes de abrir aviso o lanzar alerta, para no repetir).
   - Clasificación por el mensaje (sin LLM, regex sin mayúsculas):
     - **reclamo**: copyright, derechos de autor, content id, dmca, infring/infracción, takedown, claim/reclamo, strike, removed/retirad/eliminad, violat, community guidelines/normas de la comunidad, intellectual property/propiedad intelectual.
     - **cuenta**: suspend, restrict, bloquead, blocked, disabled, deshabilitad, banned, terminated (la misma familia que `ciclo.RE_CUENTA`).
     - **otro**: cualquier otro error (formato, conexión del canal…). Sólo se anota en `registro.md`; no es alerta roja.
   - Streamer del post: primero por el libro `estado/buffer-envios.json` (`post_id` → `datos.ficha_id` o `datos.toma.fuente_id` → ficha de `cargar_fuentes` → `twitch_login`); si no, el primer `twitch.tv/<login>` del texto del post (el crédito va siempre en el texto). Si no hay ninguno, se anota igual con la fuente que se sepa o `desconocida`.
   - **Un reclamo** (`escribir=True`):
     1. Fila en `retiros.md` con el formato de la tabla: `| AAAA-MM-DD | <fuente_id> | <plataforma> <post_id> | <plataforma> (Buffer) | <motivo corto, sin saltos ni barras> | vetado twitch.tv/<login>; ficha T pausada |`. La celda de fuente lleva `t-<login>` para fichas T (lo que ya lee `vetados._de_retiros`) y la acción lleva `twitch.tv/<login>`.
     2. Veto en `config/vetados.yml`: añade el login a la lista `logins` (en minúsculas, sin duplicar, conservando los comentarios del archivo). Si el archivo no tiene una lista `logins` legible, no se toca y basta la fila de `retiros.md` (que ya veta).
     3. La ficha T de ese streamer queda pausada: `cargar_fuentes` ya la marca `vetada` y no publicable por el veto; además se escribe `estado: vetada` en su entrada de `estado/fichas-t.yml` si existe.
     4. Una alerta pendiente en `estado/alertas-retiro.json` (`{clave, titulo, cuerpo}` sin claves ni URL firmadas) para que el workflow abra el issue con la etiqueta `alerta-roja`.
   - **Dos reclamos en 7 días** (contando las filas de `retiros.md` con fecha en los últimos 7 días GT, hoy incluido, sean de Buffer o escritas a mano): crea `estado/pausa-t.yml` (`motivo`, `desde`, `autor: retiros`) y una alerta pendiente más («PAUSA de la ficha T»). La evaluación corre en cada ciclo aunque Buffer no responda, para que las filas a mano también cuenten. Nunca crea el `PAUSA` global: las campañas A, FM27 y las informativas siguen.
   - **Aviso de cuenta**: tras anotar el post como visto (y la fila de `retiros.md` si además es reclamo), lanza `publicar.AlertaRoja(motivo)` → el ciclo crea `PAUSA` global y su issue, como hoy.
   - `pausa_t_activa(raiz) -> tuple[bool, str]` lee `estado/pausa-t.yml`.
   - CLI `python -m fabrica.retiros --revisar [--seco]`: con `BUFFER_API_KEY` y los ids de `config/canales.yml`, revisa y escribe; con `--seco` sólo lee e imprime lo que haría (clasificación, streamer, si saltaría la PAUSA de la ficha T) sin escribir nada. Nunca imprime la clave. `--abrir-avisos`: abre con `gh issue create --label alerta-roja` cada alerta pendiente (crea la etiqueta con `gh label create alerta-roja --color B60205 --force` una vez por corrida, sin fallar si ya existe) y la marca avisada en `estado/alertas-retiro.json`; si `gh` falla, la alerta queda pendiente para la próxima corrida.
2. **Pausa de la ficha T**:
   - `detectar.detectar_t_con_descartes`: con `estado/pausa-t.yml` presente no consulta Twitch y devuelve el salto «ficha T en PAUSA: <motivo>».
   - `fuentes.cargar_fuentes`: con `pausa-t.yml`, toda ficha T sale `estado: pausada`, `publicable: False` (las de `fuentes.yml` no cambian).
   - Se quita borrando `estado/pausa-t.yml` (lo decide Kevin). Lo dice el comentario del archivo.
3. **Retiros de otras fuentes**: `fuentes.cargar_fuentes` marca `vetada` y no publicable cualquier ficha cuyo `id` aparezca en la columna «Fuente» de una fila de `retiros.md` (protocolo de `retiros.md`: «la fuente pasa a vetada hasta tener permiso nuevo por escrito»). Las fichas T siguen además vetadas por login.
4. **Ciclo** (`fabrica/ciclo.py`): en `_producir` (antes de `detectar`, para que el veto valga en este mismo ciclo) y en `_solo_mantenimiento`, si no es ensayo forzado y hay `BUFFER_API_KEY`: `retiros.revisar(...)` dentro de `plazo.etapa("retiros")`. Un `ErrorBuffer` o de red se anota y el ciclo sigue (salvo 401/403, que ya es alerta roja por `_motivo_cuenta`); `AlertaRoja` sube y la maneja `_con_alerta`. Siempre se evalúa la regla de 2 en 7 días.
5. **Workflow `fabrica.yml`**, paso «Cerrar el ciclo»:
   - `PERMITIDOS` incluye `retiros\.md` y `config/vetados\.yml`, y se añaden al commit.
   - `python -m fabrica.retiros --abrir-avisos || true` antes del commit (igual que `--abrir-avisos` del ciclo).
   - El issue de la alerta roja global también lleva `--label alerta-roja` (con la etiqueta creada antes con `--force`).
6. **Workflow `diagnostico.yml`**: paso nuevo «Buffer, retiros (seco)» que corre `python -m fabrica.retiros --revisar --seco` (instala Python y `requirements.txt` si hace falta). Es la corrida real de la tarea.
7. **`fabrica/validar.py`**: si existe `estado/pausa-t.yml` debe tener `motivo` (si no, error de configuración).

## Pruebas (`pruebas/test_retiros.py`, transporte falso de Buffer, sin red)

Respuestas falsas con la forma real de la API (`posts.edges[].node` con `id status dueAt text error`, `pageInfo`), y de introspección de `Post` con `error` escalar y con `error` objeto.

## Criterios de aceptación (binarios)

1. `python -m pytest -q` entero en verde en la rama, más `pruebas/test_retiros.py` con al menos estos casos en verde:
   1. Post `error` con «copyright claim» de una ficha T del libro → fila en `retiros.md` con `t-<login>` y `twitch.tv/<login>`; login añadido a `logins` de `config/vetados.yml` sin perder los comentarios; `logins_vetados` lo incluye; `cargar_fuentes` devuelve esa ficha T `vetada` y no publicable; una alerta pendiente con «alerta roja».
   2. El mismo post en un segundo ciclo no añade otra fila ni otra alerta (idempotente).
   3. Login sacado del texto (`twitch.tv/<login>`) cuando el post no está en el libro.
   4. Dos reclamos (de streamers distintos) en 7 días → `estado/pausa-t.yml` creado, `detectar_t_con_descartes` no llama a Twitch (transporte que falla si se llama) y devuelve el salto; `cargar_fuentes` pone toda ficha T `pausada`; una ficha tipo A sigue publicable; **no** existe `PAUSA`.
   5. Un reclamo hoy y otro de hace 8 días → sin `pausa-t.yml`.
   6. Fila escrita a mano en `retiros.md` (sin Buffer) + un reclamo de Buffer el mismo día → `pausa-t.yml`.
   7. Error «account suspended» → `AlertaRoja`, el post queda en `retiros-vistos.json`; el ciclo crea `PAUSA` global.
   8. Error «video too short» → sin fila, sin veto, sin alerta; sólo una línea en `registro.md`.
   9. `--seco` no escribe ningún archivo (se compara el árbol antes y después).
   10. Buffer responde 500 en la lectura → el ciclo sigue, sin PAUSA ni alerta; la regla de 2 en 7 días sí se evalúa.
   11. `Post` sin campo `error` en la introspección → la consulta no lo pide y no falla.
   12. `--abrir-avisos` con un `gh` falso: llama `gh issue create` con `--label alerta-roja`, marca avisado; si `gh` devuelve 1, queda pendiente.
   13. Retiro de una ficha no T (por ejemplo `sega-fm27`) → fila con su id y `cargar_fuentes` la devuelve vetada.
   14. La clave de Buffer no aparece en ninguna salida, archivo ni cuerpo de issue.
2. `fabrica.yml` permite `retiros.md` y `config/vetados.yml` en el commit y abre los issues con `--label alerta-roja` (prueba que lee el YAML del workflow).
3. Corrida real de `diagnostico` en la rama en verde con el paso «Buffer, retiros (seco)» leyendo Buffer de verdad.

## Fuera de alcance

- Leer el correo de YouTube/TikTok o el estado del video en cada red (Buffer no lo da). Queda como propuesta.
- Borrar posts en Buffer o en las redes de forma automática.

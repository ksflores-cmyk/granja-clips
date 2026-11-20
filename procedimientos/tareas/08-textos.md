# Tarea #8 `textos` (rama `claude/clips-textos`)

Plan: `procedimientos/plan-noche-2026-11-19.md`. Playbook: sección 10 (texto de cada publicación), 5B (lista prohibida), 11 (Buffer).

## Qué se pide

Título, descripción y hashtags por red (Shorts, TikTok, Reels) en español, con crédito siempre, `#Publicidad` y etiquetas del brief en campañas, ganchos concretos y fuera de la lista prohibida, y los límites de cada red y del metadata de Buffer.

## Límites comprobados (20 nov 2026)

Fuentes: <https://developers.buffer.com/guides/character-limits.md>, <https://support.buffer.com/articles/using-youtube-shorts-with-buffer-Jl8iR6jIck.md>, y el límite de 5 hashtags de Instagram (vigente desde el 18-19 dic 2025, <https://www.socialmediatoday.com/news/instagram-implements-new-limits-on-hashtag-use/808309/>).

| Red | Texto (descripción) | Título | Hashtags | Conteo |
|---|---|---|---|---|
| Shorts (YouTube) | 5000 | 100 | Shorts/TikTok: 3 o menos (los nuestros; YouTube muestra los 3 primeros) | UTF-16 (emoji = 2) |
| TikTok con video | 2200 | — | 3 o menos | UTF-16 |
| Reels (Instagram) | 2196 | — | de 3 a 5 (Instagram corta en 5) | UTF-16 y cada salto de línea cuenta 2 |

YouTube: título y descripción **sin `<` ni `>`** (Buffer los rechaza). El metadata de YouTube va con `categoryId: "20"` (Gaming). En campaña, `#Publicidad` cuenta dentro del tope de hashtags de cada red sólo en Reels (5 en total); en Shorts y TikTok va `#Publicidad` + hasta 3 relacionados (como hoy).

## Criterios de aceptación (sí o no)

1. Existe `config/textos.yml` con: límites por red de la tabla de arriba (`max_texto`, `max_titulo`, `max_hashtags`, `min_hashtags`, `salto_cuenta`), `genericos` (frases de gancho genérico además de las de `marca.yml`: al menos «no vas a creer», «esto cambia todo», «el final te sorprenderá», «mira esto», «no te lo pierdas», «lo que pasó», «momento épico», «increíble», «locura total», «parte 2»), `vacias` (palabras que no cuentan como contenido: artículos, preposiciones, «clip», «stream», «momento», «gta», «6», «vi»…), `ingles` (palabras de relleno en inglés: the, and, when, this, with, is, you, my, what, of) y `siglas` que se conservan en mayúsculas (GTA, NPC, FM27, PS5, FIFA, DLC, IA).
2. `fabrica/textos.py` concentra la lógica; `publicar.textos(toma, ficha, plataforma, raiz=None, hoy=None)` sigue existiendo con la misma firma (delegando) y devuelve como hoy `texto`, `titulo`, `credito`, `etiquetas`, y además `descripcion` (= `texto`), `hashtags` (lista) y `largo` (UTF-16 según la red).
3. **Crédito siempre.**
   - Ficha T (`tipo: T`): la línea de crédito es exactamente `🎥 twitch.tv/<login>` (de `ficha.credito`, o `ficha.creador`, o `ficha.twitch_login`); sin login, `Descartada("sin crédito")`.
   - Varios creadores (`toma.creadores`, lista, para «Mejores momentos» de la tarea #7): `🎥 twitch.tv/a · twitch.tv/b` (un solo emoji); en campaña, la etiqueta de cada uno para esa red, en el orden de la lista; si falta la de alguno, se descarta.
   - Campaña con brief (A, SEGA FM27): orden y texto del brief tal cual: `@footballmanager`, etiqueta del creador para esa red (`material[].etiquetas`), `#Publicidad` en su propia línea y primer hashtag, hasta 3 hashtags relacionados.
   - Informativa: `🎥 @RockstarGames`.
   - El crédito y `#Publicidad` nunca se recortan para caber.
4. **Gancho concreto.** Se descarta (con motivo) si: contiene una frase de `marca.yml` `prohibido` (ganchos genéricos) o de `config/textos.yml` `genericos`, comparando sin acentos ni mayúsculas; quedan menos de 2 palabras con contenido tras quitar `vacias`, el login o nombre del creador, el nombre del juego y los hashtags (p. ej. «gta6», «ibai», «GTA 6 clip»); tiene 2 o más palabras de `ingles`; o el texto entero contiene alguna de `ficha.reglas.palabras_evitar`.
5. Un gancho que llega todo en mayúsculas pasa a tipo oración conservando las `siglas` («EL NPC LE CONTESTA» → «El NPC le contesta»). El juego se escribe «GTA 6» en el texto, pero el hashtag `#GTA6` nunca se convierte en `#GTA 6`. Los hashtags incrustados en el gancho se quitan del gancho.
6. Los emojis del gancho, el valor, el titular y los datos se quitan (no descartan la toma); el texto final lleva exactamente 1 emoji (🎥) fuera de campaña y 0 o 1 en campaña.
7. Hashtags: formato válido (`#` + letras, números o `_`, con acentos y ñ; sin espacios ni signos), sin duplicados (sin distinguir mayúsculas), topes de la tabla; Reels con menos de 3 se descarta como hoy.
8. Shorts: `titulo` de 100 o menos UTF-16, sin emoji, sin hashtags y sin `<` `>`; `<` y `>` se quitan también de la descripción de Shorts (y de todos los textos). `armar_carga` de Shorts sigue mandando `categoryId: "20"`.
9. Si el texto pasa el límite de su red: primero se recorta el valor (Reels) o los datos (informativa) por frases enteras; si aún no cabe, `Descartada` con el largo y el límite.
10. Sigue vigente «no se repite el mismo texto en 7 días» (`_comprobar_repetido`).
11. `pruebas/test_textos.py`: para cada red (shorts, tiktok, reels) un caso T, un caso A con la ficha real `sega-fm27` de `fuentes.yml` y un caso informativa, con el texto exacto esperado; más casos negativos de los puntos 3 a 9 (gancho prohibido, gancho vacío, inglés, palabra evitada, sin crédito, emoji quitado, `<>`, tope de 5 en Reels, conteo UTF-16 con emoji y saltos de línea en Reels, recorte y descarte por largo, varios creadores).
12. `python -m pytest -q` entero en verde (CI `puerta`), con las pruebas que cambien de comportamiento actualizadas y explicadas.
13. `python -m fabrica.publicar --ensayo` imprime, sin publicar, los 9 textos de muestra (T, A, informativa × 3 redes) con su largo y límite, y arma cada carga contra el esquema de Buffer (`armar_carga`). Corrida real `publicar-ensayo` en verde.

## Fuera de alcance

No se toca la edición del video, ni `marca.yml` (lo cambia la #5), ni el envío a Content Rewards o Clipeamos.

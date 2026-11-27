# Especificación: modo inglés de campaña + material de SEGA Black Friday

> **Cancelada el 26 nov 2026 (0:38 GT):** Kevin soltó SEGA. Ninguna de las 3 redes acepta la divulgación nativa por
> Buffer (aviso A-20261125-4) y él no la pondrá a mano. Queda como referencia si vuelve una campaña en inglés.

Diseño del Project de Clips, 25 nov 2026 (avisos A-20261125-1 y A-20261125-2). Lo construyen Codex y Antigravity;
revisa el Project. **Plazo duro:** la oferta de Steam termina el **30 nov a las 12:00 p. m. GT**; sin esto antes del
29 nov, la ficha `sega-black-friday` se queda en `pausada` y se cierra.

## Por qué

Brief completo leído el 25 nov con `brief-cr` (salida cifrada). SEGA pide:

- Contenido **sólo en inglés**: texto en pantalla y caption. Hoy la fábrica escribe en español y la puerta de textos
  descarta ganchos en inglés (`fabrica/textos.py`, «gancho en inglés»).
- **Sólo metraje de la carpeta oficial** (Frame.io, 12 archivos: 9x16, 16x9 y 1x1, de 15 y 30 s). Nada de fuera.
- Sin música añadida (sólo el audio oficial), sin voces IA, sin precios ni descuentos que no salgan en el video.
- Caption: `@sega_west` (TikTok y Shorts) o `@sega` (Reels); `#Ad` en su línea, justo después del texto, como primer
  hashtag; hasta 3 hashtags más, relacionados.
- 10 s como mínimo; arriba 30 días; likes visibles; el mismo video no más de 5 veces.
- **Audiencia: 50 % de EE. UU. y Canadá** (la página o la publicación). Riesgo de rechazo: Guion Vivo es
  latinoamericana. Kevin ya decidió clips en inglés y un estilo propio para campañas de EE. UU. dentro de Guion Vivo.

## Tareas (criterios binarios)

### 1. Modo inglés por ficha (`reglas.idioma: en`)

- [ ] `fabrica/ojos.py` y su prompt: con `idioma: en`, `gancho`, `escena` y `acotacion` salen en inglés; `lineas[].es`
      repite `original` (no se traduce al español). Prueba con un transporte falso.
- [ ] `fabrica/textos.py`: el filtro «gancho en inglés» no se aplica a fichas `idioma: en`; sí sigue para las demás.
- [ ] Caption en inglés: gancho propio (nunca copiado de los ejemplos del brief), etiqueta de la red
      (`etiquetas_por_red`), línea sola con `divulgacion`, y como mucho `max_hashtags_extra` hashtags después.
      Prueba: los 3 textos de muestra de SEGA pasan `python -m fabrica.publicar --ensayo-textos`.
- [ ] Sin música ni efectos si `reglas.sin_musica: true`; la puerta de estilo lo comprueba.
- [ ] El sistema visual de `marca.yml` se mantiene (sólo cambia el idioma).

### 2. Material oficial en la nube

- [ ] Workflow `material-sega` (a mano): Playwright abre la carpeta de Frame.io (enlace en el secreto
      `SEGA_BF_CARPETA`, nunca en el repo: es sólo para miembros), baja **sólo** los 4 archivos `9x16` y los sube a
      un bucket **privado** de Supabase (`fuentes`). Nada en releases ni artefactos: el repo es público.
- [ ] La fábrica baja de ese bucket con URL firmada de corta duración (service key). Prueba sin red.
- [ ] `material:` de la ficha lista los 4 archivos por nombre (sin URL pública).

### 3. Puertas de la campaña

- [ ] Una toma SEGA nunca lleva subtítulos traducidos, música ni texto con precios inventados (puerta por código).
- [ ] Si Content Rewards rechaza 3 clips de SEGA por audiencia, la ficha pasa sola a `pausada` con ese motivo
      (no es alerta roja salvo rechazo en bloque).
- [ ] Ensayo `enviar-cr` sin el interruptor real, con una toma SEGA recién publicada: llega al diálogo y lo acepta
      dentro de los 30 min.

## Cuando las 3 estén en verde

`estado: vigente` y `publicable: true` en la ficha, con `vence: 2026-11-30`. El cupo de Shorts ya da prioridad a las
tomas de campaña (`fabrica/shorts.py`).

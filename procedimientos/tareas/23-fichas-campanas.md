# #23 `fichas-campanas`: fichas de las 3 campañas de Content Rewards y FM27 pausada

Rama `claude/clips-fichas-campanas`, 21 nov 2026 (sesión programada de la madrugada).

## Qué se pidió

1. Ficha tipo A por cada campaña a la que Kevin está unido sin ficha: «SEGA - Steam Black Friday Sale
   Clipping», «Call of Duty - MW4 DMZ Intel Briefing Clipping» y la de Expedia x IShowSpeed (Reach), con
   reglas, tarifa, mínimo, tope, presupuesto y material sólo de la fuente oficial (copiado a un release
   privado). Si el brief choca con la marca o el material no se puede bajar desde Actions: `estado: pausada`
   con el motivo.
2. `sega-fm27` a `pausada` («Pausada por la marca», 19 nov) sin debilitar sus pruebas.
3. Pruebas de las fichas y una corrida de ensayo de `fabrica` con una campaña nueva.
4. Motivo de rechazo de `enviar_cr.py`: preferir «Publicado hace…/hasta 30 minutos».

## Qué se hizo

- **Fichas** (`fuentes.yml`), leídas de las páginas públicas el 21 nov 2026 (~3:40 GMT):
  - `sega-black-friday` (1391fb95-…): IG US$3,50/1K (mín. 3,50), TikTok y YouTube hasta US$2/1K (mín. 4;
    no pagan PK, IN, BD ni sin dato de país), tope US$800, US$11,933 de 12,000. `#Ad`, `@sega_west` en
    TikTok/YouTube y `@sega` en Instagram. **Pausada**: el video oficial es el «1 file» bloqueado tras la sesión.
  - `cod-mw4-dmz-intel` (a1cefc3b-…): sólo TikTok (hasta US$2, mín. 6) e Instagram (US$1,50, mín. 4,50),
    tope US$1,200, US$13,866 de 30,000. **Pausada**: material y reglas de texto en la carpeta bloqueada.
  - `reach-expedia-speed` (03bda4fd-…, nombre exacto «$8,000 IShowSpeed x Expedia Clipping Campaign»):
    US$1/1K en las 3 redes, mín. 10, tope 500, **quedan US$25 de 8,000**. **Pausada**: presupuesto agotado y
    choques con la marca (audiencia ≥ 50 % angloparlante nativa, cuenta del nicho streamer/viajes, prohibición
    de contenido generado con IA con expulsión de todas las campañas de Reach, fechas del stream que no
    coinciden: 14 oct en Content Rewards, 6 nov en Reach).
- **FM27**: `estado: pausada`, `publicable: false`, `motivo_pausa`. Las pruebas del camino de campaña usan una
  copia vigente (`como_vigente` y la fixture `repo_fm27` de `conftest.py`); una prueba nueva comprueba que con
  la ficha real el detector no trae nada de FM27.
- **Validación** (`fabrica/validar.py`): `cr_campaign_id` uuid y único, con `campana` y `permiso_url` de esa
  campaña; ficha A pausada exige `motivo_pausa`; `reglas.etiquetas_por_red` y `tarifa_por_red` dentro de
  `reglas.plataformas`; `presupuesto_restante` ≤ `presupuesto_total`.
- **Lector de briefs** (`fabrica/brief_cr.py`, workflow `brief-cr`): con `CR_SESION` abre cada campaña unida,
  guarda texto, captura, enlaces de material y el texto de los Google Docs públicos en el artefacto
  `briefs-cr`. No envía ni se une a nada. Es el paso que desbloquea SEGA y DMZ.
- **Robot**: `motivo_rechazo()` con prioridad (línea del post > cuenta no vinculada > «no se puede…»).

## Para reanudar SEGA o DMZ

1. Actions → `brief-cr` → Run workflow (por defecto lee las 3).
2. Del artefacto: enlace oficial del video, duración, divulgación y etiquetas. Completar la ficha.
3. Copiar el video una vez a un release privado (`fuentes-sega-black-friday`, `fuentes-cod-dmz`) como hizo
   `release-fm27`, y poner `material` con `url` del release y `etiquetas` por red (`@sega_west`/`@sega`).
4. `estado: vigente` y `publicable: true`; ensayo de `fabrica` con artefacto MP4.

## Bloqueos de esta corrida

- GitHub Actions no arranca ningún job desde el 20 nov 8:30 p. m. GT («recent account payments have failed
  or your spending limit needs to be increased»). Sin puerta de CI, sin `brief-cr` y sin ensayo de `fabrica`.
  La puerta se corrió entera en la nube de la sesión (ver la bitácora).

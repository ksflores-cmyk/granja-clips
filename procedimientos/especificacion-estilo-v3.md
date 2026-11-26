# Especificación: estilo v3 de las tomas con video

Pedido de Kevin, 25 nov 2026, 8:16 p. m. GT, con capturas de las tomas 013, 014, 016 y 017. Decisiones tomadas con
él en el chat (claves entre comillas). **Sólo para las tomas nuevas:** no se borra ni se cambia nada ya publicado.
Diseño del Project; construyen Codex y Antigravity; revisa el Project. Cada punto cambia `marca.yml`, las placas
(`fabrica/editar/placas.py`) y la puerta de estilo (`fabrica/editar/estilo.py`): la puerta se actualiza en la misma
PR, nunca se apaga.

## 1. Subtítulos más abajo y sin caja — «Sin caja, con contorno»

Hoy: placa de papel de 2 a 4 filas con el borde de abajo en y = 1560 (toma 017: tapa casi un tercio del panel).

- [x] Sin placa de papel: Courier Prime en color papel (`#F2EDE3`) con contorno de tinta de 3 px y sombra suave
      (desplazamiento 2 px, 40 % de opacidad). Nombre del hablante pequeño (24 px), en `lapiz_rojo` con contorno, encima.
- [x] Frase de 44 px, como mucho 2 líneas; borde de abajo del bloque en **y = 1590** (sobre la franja de las apps, de
      1600 a 1920). En pantalla dividida, igual: dentro del panel del juego.
- [x] El color por voz (`voces:`) sigue: la otra voz va en `lapiz_rojo`.
- [ ] Puerta: contraste medido (contorno visible sobre fondo claro y oscuro con 2 muestras), bloque dentro de la zona
      segura y fuera de la zona de acción, área del bloque ≤ 8 % del cuadro.
- [ ] Muestras nuevas en `marca/muestras/` para que Kevin las vea antes de producción.

## 2. Final sin «CORTE.» — «Claqueta + destello»

Hoy: placa «CORTE. / Con: …» encima del último medio segundo (toma 016).

- [x] Se quita la placa de cierre. El video corre hasta su último cuadro; el crédito ya está en la fila de arriba
      («TOMA nnn Con: …») y en el texto de la publicación.
- [x] Último 0,2 s: destello color papel que sube de 0 a 70 % y baja (no corta el clip ni lo alarga).
- [ ] Sonido corto de claqueta (< 0,4 s, CC0, verificado como los de `marca/sfx/`) mezclado en el final, a −6 dB bajo
      el audio del clip. Nunca en fichas con `reglas.sin_musica` (campañas): ahí sólo el destello.
- [x] Puerta: `cierre_visible` pasa a comprobar el destello y el sonido; ya no exige «CORTE.».
- [ ] `marca.yml`: `movimiento.transiciones` admite `destello_final`; `sonido.sello_sonoro: claqueta`.

## 3. Menos zoom y «movimientos de cámara» horizontales

Hoy: encuadre fijo por toma (`fabrica/editar/encuadre.py`, «sin zoom ni paneo»): de un 16:9 se ve un 32 % del ancho.
Lo vertical ya va entero (la escala cubre el alto); en la toma 014 el zoom viene además del propio clip de Twitch.

- [ ] Paneo horizontal suave: la ventana sigue el centro de la acción (energía de movimiento y bordes, como hoy), con
      posiciones cada 0,5 s suavizadas (media móvil de 2 s) y velocidad máxima de 4 % del ancho de la fuente por
      segundo. En el cuadro 0, la mejor posición (centro si empata).
- [ ] Sin zoom: la escala no cambia durante la toma; nada vertical se recorta.
- [ ] Filtro `crop` de ffmpeg con `x` por expresión (interpolación lineal entre puntos), sin saltos.
- [ ] Puerta: desplazamiento entre cuadros ≤ 0,2 % del ancho y la cara de la facecam (si la hay) no sale del cuadro.
- [ ] En pantalla dividida, el paneo sólo en el panel del juego; la facecam queda fija.

## 4. Clips con subtítulos quemados — «Evitarlos y, si no hay otro, tapar»

Hoy: Gemini marca `subtitulos_quemados` y la fábrica sólo omite su propio bloque; los subtítulos del clip (morados
palabra por palabra en la 013, que además son un estilo que `marca.yml` prohíbe) se ven tal cual.

- [x] El buscador baja al final de la lista los candidatos marcados con subtítulos quemados (con el veredicto guardado
      por id de clip en `estado/`); sólo se usan si no queda otro en ese hueco.
- [x] Gemini devuelve también la franja vertical de esos subtítulos (`subtitulos_franja: [y0, y1]` en fracción del
      alto). Si se usa el clip: esa franja se difumina (desenfoque gaussiano fuerte, ancho completo) y van nuestros
      subtítulos del punto 1 encima, con la transcripción de Whisper o de Gemini.
- [x] Sin franja fiable (falta, o mide más del 25 % del alto): el clip se descarta y se anota el motivo.
- [ ] Prueba con la toma 013 como caso de regresión (clip y veredicto guardados sin el video en el repo).

Punto 2 en la PR `claude/clips-estilo-v3b` (26 nov): el destello sube hasta el último cuadro (no baja: funciona como
fundido a papel); una pasada extra de ffmpeg sobre el MP4 terminado, así vale también con historia. Claqueta: efecto
`claqueta` (Kenney, CC0) en candidata hasta que el workflow `sfx` lo verifique; mientras, sólo el destello.

Punto 1 en la PR `claude/clips-estilo-v3b` (26 nov): nombre a 28 px (a 24 se leía poco sobre fondo oscuro); la puerta
de estilo compara la placa con transparencia. Falta la medida de contraste por muestras.

Punto 4 en la PR `claude/clips-estilo-v3a` (26 nov): además, si el primer candidato trae subtítulos quemados queda de
reserva y se prueba otro; la historia se apaga en las tomas tapadas (lee tiempos de la fuente original).
Falta la prueba de regresión con la toma 013.

## Orden y criterio de cierre

4 → 1 → 2 → 3 (lo que más se nota primero). Cada punto en su PR, con su puerta en verde y una muestra que Kevin vea.
Se da por hecho con la primera toma publicada que lo lleve (enlace) y una fila del marcador.

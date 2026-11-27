# Video largo semanal (YouTube)

Pedido de Kevin del 27 nov 2026: un video largo a la semana en YouTube que recapitule **sólo las tomas que no
salieron como Short** (cupo de 2 Shorts al día de Buffer o Short rechazado), con edición: cortes, efectos de memes,
intro y outro. Decisiones del mismo día: **«T en largo»** (las fichas T entran), **memes famosos pero actuales** (nada
viejo, de tío ni cringe) y **«Sí API»** (Buffer sólo sube Shorts; se sube con la API de YouTube).

## Cómo sale

1. **Domingo** (`.github/workflows/largo.yml`, 4 intentos de cron entre 10:17 y 18:17 GT; no repite si ya salió un
   episodio en los últimos 5 días). También a mano con *Run workflow*.
2. **Selección** (`fabrica/largo/seleccion.py`): tomas del libro de envíos sin Short creado desde el último episodio;
   fuera las de campaña, tipos no permitidos (`config/largo.yml → tipos_fichas`), creadores vetados, fuentes
   retiradas y tomas ya usadas. Mínimo 6 tomas; si no, espera a la semana siguiente.
3. **Receta** (`fabrica/recetas.py`): desde el 27 nov el ciclo guarda en `estado/recetas.jsonl` el tramo, los
   silencios cortados y los subtítulos de cada toma publicada. Las anteriores se rehacen una vez (descarga + ojos).
4. **Horizontal** (`fabrica/largo/horizontal.py`): cada toma otra vez desde su clip de Twitch a 1920x1080, con el
   cuadro 16:9 entero (sin zoom), 1 s más de contexto a cada lado, subtítulos con contorno, fila TOMA nnn + crédito,
   mosca, el puesto de la cuenta atrás («#7») y el cierre de marca (destello papel + claqueta) como corte.
5. **Memes** (`fabrica/largo/memes.py`): se leen los «Populares en …» de Myinstants (GT, MX, ES, US), Gemini descarta
   los viejos, cringe, vulgares u ofensivos y clasifica el resto; luego asigna a cada clip el que remata (o ninguno).
   Uno por clip como mucho, 2,5 s máximo, uno cada 20 s, justo después del golpe de volumen. Los MP3 se bajan en el
   runner y se borran: nunca entran al repo ni al almacén. **Riesgo avisado a Kevin:** casi todos tienen derechos; un
   reclamo de Content ID puede quedarse con lo que gane el episodio.
6. **Armado** (`fabrica/largo/armar.py`): 3 avances de 1 s de los mejores, tarjeta de título (3 s), la cuenta atrás
   (de menos a más vistas en TikTok/Reels), outro de 12 s con los créditos, sonoridad pareja (-16 LUFS por clip).
   Descripción con capítulos (los de menos de 10 s se juntan) y enlace a cada streamer.
7. **Puerta** (`fabrica/largo/puerta.py`): 1920x1080, 30 fps, AAC, duración de clips, duración igual al armado,
   sonoridad entre -20 y -12 LUFS, capítulos válidos, todos los créditos en la descripción, sin vetados, título.
   Con un rojo no se sube.
8. **Subida** (`fabrica/largo/youtube.py`): subida reanudable con el refresh token de los secretos `YT_CLIENT_ID`,
   `YT_CLIENT_SECRET` y `YT_REFRESH_TOKEN`; miniatura propia si el canal tiene la verificación de teléfono. Tras subir
   se lee el estado real: si YouTube lo dejó privado (proyecto sin auditar), sale un aviso en la corrida.
9. **Registro**: `estado/largos.json` (episodio, tomas, memes, enlace, privacidad).

## Pendiente

- Que Kevin termine el alta (app «En producción» y los 3 secretos) y fusione la PR del workflow (toca `.github/`).
- Primer episodio: domingo 29 nov. Se da por hecho con el enlace publicado y una fila en el marcador.
- Comprobar en el canal que «creado» en Buffer es de verdad un Short publicado (las tomas 017 y 018 no aparecieron):
  la selección v2 debería leer los Shorts del canal con la API.

# Procedimiento: prueba de retención corta-10-14

Config: `config/prueba-retencion.yml` (activa desde el 22 nov 2026). Corre sola; no se pregunta nada a Kevin.

## Qué se prueba
- Motivo: Buffer mostró 4,7 y 5,4 s vistos en TikTok (tomas de 17-20 s) y 7-8 s en Reels.
- Brazo `corta`: recorte de 10 a 14 s, con el gancho visual o sonoro en el primer segundo. Brazo `control`: la toma como hoy.
- Asignación: por número de toma (par = corta, impar = control). La columna `prueba` del marcador dice `corta-10-14:corta` o `corta-10-14:control` en cada fila nueva.
- Si una toma par no puede ser corta (la fuente dura menos de 10 s, o la ficha pide otro rango) pasa a `control`, queda así en `prueba` y la razón va en `notas` (`prueba: ...`) y en el plan JSON.
- Si Gemini devuelve un recorte fuera de 10-14 s, `toma.py` lo ajusta: recorta el final, nunca estira el tiempo.
- En el brazo corta la historia (apertura en frío) va apagada: añade 2-3 s y sacaría la toma del rango. El rótulo del gancho (`gancho_s: 3.0` en `marca.yml`) no se toca; en 10 s ocupa el 30 % de la toma (en 20 s, el 15 %).

## Qué se mide
Por red (shorts, tiktok, reels) y por brazo:
1. **% visto** = `visto_medio_s` / `duracion_s` x 100 (tiempo medio visto de Buffer entre la duración de la toma). Se promedia entre las tomas del brazo.
2. **Vistas a 24 h** (`vistas_24h`): media por toma.
3. Mínimo 10 tomas con dato por brazo y por red (`min_tomas_por_brazo`). Con menos, el informe dice «faltan N» y la decisión es seguir.

## Decisión
- Gana `corta` si su % visto es al menos 20 % mayor que el del control y sus vistas a 24 h no caen más de 10 % frente al control.
- Gana `control` en el caso simétrico.
- Cualquier otro resultado es empate: seguir hasta tener más tomas. Se decide por red; no se mezclan redes.
- Ganador de la prueba: se propone el cambio a Kevin (no se cambia `marca.yml` solo).

## Informe
`python -m fabrica.retencion --informe` lee `marcador.csv` y imprime la tabla por red y brazo con tomas, tomas con dato, % visto, vistas a 24 h, «faltan N» y el veredicto.

## Dónde se llena `visto_medio_s`
Columna nueva al final de `marcador.csv` (vacía en las filas viejas). El **cierre diario** debe escribirla en la misma lectura de Buffer que llena `vistas_24h`: a las 24 h de cada post, el tiempo medio visto en segundos (número con punto). Sin dato, vacío; nunca estimado. Hasta que el cierre la llene, el informe sale con «faltan 10» en todo.

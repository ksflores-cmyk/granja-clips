# Tarea #21 `criticos` (rama `claude/clips-criticos`)

Decisión de Kevin (20 nov, 1:21 a. m., «Sí: todo»): críticos que no vieron el plan, más un juez. Plan de la noche: `procedimientos/plan-noche-2026-11-20.md`, fila #21. Se construye sobre `main` (af81d8b): la #20 `historia` sigue en WIP sin PR, así que su cadena no existe todavía en `main`; esta tarea trae su propia cadena mínima con la misma forma (Gemini → segundo proveedor apagado → sin IA) y, cuando entre la #20, se unifican (seguimiento).

## Comprobación previa (21 nov 2026): GitHub Models está retirado

El changelog oficial de GitHub («GitHub Models is now retired», 30 jul 2026) confirma lo que encontró la #20: no hay API ni cupo gratis. El segundo eslabón queda **apagado** (`activo: false`) con el motivo escrito; elegir otro proveedor gratis es una decisión de Kevin (regla 7). Ningún workflow pide `models: read`. Los cupos gratis de Gemini no tienen cifra pública fija (la página de límites remite a AI Studio por proyecto), así que los topes de la config son conservadores y se **miden** en `estado/modelos-cuota.json`.

## Dónde entra

En `hacer_toma`, **después** de la puerta de estilo en verde (y por tanto después de la puerta técnica de la #17, que corre antes de la de estilo cuando se fusione) y antes de escribir el plan final. Sólo en tomas con video (las informativas no pasan por críticos en esta tarea).

## Criterios de aceptación (binarios)

### A. Lo que ven los críticos (`fabrica/editar/criticos.py`)

1. `fotogramas(mp4, n=6, lado_max=512) -> list[Cuadro(t, jpeg: bytes)]`: 6 fotogramas JPEG del MP4 final, repartidos entre el 8 % y el 92 % de la duración, lado mayor ≤ 512 px. Una sola orden de ffmpeg por cuadro con tiempo de `plazo.tiempo_para`.
2. `transcripcion(plan) -> list[{t0, t1, quien, texto}]`: sólo lo que se **oye y se lee** en el MP4 (las líneas de diálogo de `plan["lineas"]`: tiempo, nombre y frase). **No viaja nada del plan editorial**: ni `toma.gancho`, `palabra_lapiz`, `escena`, `recorte`, motivos de Gemini, `historia` ni geometría. Prueba: el texto que se envía a cada crítico no contiene el gancho ni la escena del plan cuando no aparecen en las líneas, ni las claves `gancho`, `palabra_lapiz`, `escena`, `recorte`, `historia`.
3. Tres críticos, cada uno con su instrucción propia en `fabrica/criticos_prompt/<nombre>.md` y su propia llamada (no se ven entre sí):
   - `historia`: gancho en los primeros segundos y ritmo (¿pasa algo?, ¿se entiende sin contexto?, ¿aire muerto?).
   - `subtitulos`: legibles (tamaño, contraste, tiempo de lectura) y fuera de los botones de la app (abajo y a la derecha, según `marca.yml` `pantalla_completa.zona_segura` y `maqueta.columna_dialogo`, que se describen en la instrucción en píxeles).
   - `tecnica`: encuadre (cara o acción cortada, franjas), sonido (según la transcripción y lo que se ve) y fallas visibles (cuadros negros, congelados, artefactos).
   Las notas de `reglas-kevin.yml` (si el archivo existe) con `estado: vigente` y `revision: null` se añaden a la instrucción de los tres como «quejas de Kevin que no mide el código». Sin el archivo, no pasa nada.
4. Respuesta de cada crítico, validada por código (`validar_critica`): `{"veredicto": "pasa"|"todavia_no", "puntaje": entero 1-5, "motivos": [texto, ≤ 4, cada uno ≤ 200 caracteres], "arreglo": texto ≤ 200}`. Cualquier clave extra, tipo erróneo o valor fuera de rango = crítica inválida (se trata como fallo del eslabón y se prueba el siguiente). `veredicto: pasa` con `puntaje ≤ 2` o `todavia_no` con `puntaje ≥ 4` = incoherente = inválida.

### B. El juez

5. El juez recibe sólo las 3 críticas validadas (nombre, proveedor, veredicto, puntaje, motivos, arreglo); tampoco ve el plan ni los fotogramas. Responde `{"decision": "pasa"|"todavia_no", "motivos": [texto, 1 a 4], "para_el_plan": texto ≤ 300}` validado por código.
6. Juez sin IA (regla, siempre disponible y respaldo del juez con modelo): «todavía no» si algún crítico dio `todavia_no` con puntaje ≤ 2, o si 2 o más dieron `todavia_no`; si no, «pasa». Sus motivos son los de los críticos que dijeron «todavía no». `para_el_plan` = los `arreglo` de esos críticos unidos.
7. Con `juez.con_modelo: true` el juez pide a la cadena; si la respuesta no valida o no hay cupo, decide la regla. El juez con modelo **no puede** dar «pasa» si la regla da «todavía no» por un puntaje ≤ 2 (piso de seguridad, probado).

### C. La cadena de modelos (Q0) y los cupos

8. `fabrica/cadena.py`: `pedir_json(instruccion, imagenes, esquema, *, raiz, entorno, transporte, plazo_s) -> Respuesta(datos, proveedor, modelo, intentos, pasajero)`.
   - **Gemini**: `generateContent` con `inline_data` `image/jpeg` + texto, `responseMimeType: application/json` y `responseSchema`; modelos y respaldos de `config/ojos.yml` (reutiliza `ojos.modelos_disponibles_en_cache` para saltar los que no existen); clave sólo de `GEMINI_API_KEY`, nunca impresa; tiempo por llamada `criticos.tiempo_por_llamada_s` recortado por `plazo`; 429/5xx/red = pasajero, 404 = siguiente modelo, 401/403 = sin Gemini para el resto del ciclo.
   - **Segundo proveedor** (`criticos.segundo`): cliente genérico chat/completions compatible con OpenAI (`url`, `modelo`, `secreto` = nombre de la variable de entorno), imágenes como `data:image/jpeg;base64`, `response_format: json_object`. `activo: false` con `motivo: "GitHub Models retirado el 30 jul 2026"`: deja el renglón «segundo: apagado (…)» en `intentos` y no llama. Pruebas con transporte falso con `activo: true` (200 válido, 200 inválido, 429, sin secreto).
   - **Sin IA**: lo resuelve `criticos.py` (punto 10), no la cadena.
9. `fabrica/cupos.py` + `estado/modelos-cuota.json`: por proveedor, ventana de 60 s, cuenta del día (hora de Guatemala) y tokens del día (de `usageMetadata` de Gemini cuando venga). `hay_cupo(proveedor, n)` mira los topes de `config/criticos.yml` (`cupos.gemini.rpm`, `rpd`, con una reserva `reserva_ojos` de peticiones del día que los críticos nunca usan, para que los ojos sigan teniendo cupo). Si la ventana de un minuto está llena, espera hasta `espera_max_s` (inyectable) o cae a sin IA. `ojos.mirar` también registra cada petición que envía (una línea, sin cambiar su lógica), para que la cuenta del día sea real. Cada toma guarda en su plan `criticos.llamadas` (cuántas peticiones gastó, por proveedor) y la cuenta del día queda medida en el archivo. Si el archivo está roto, se reinicia sin romper la toma.
10. Críticos **sin IA** (cuando la cadena no da una crítica válida, o `criticos.modo: sin_ia`), sólo con el MP4 y la transcripción, deterministas (mismo MP4 → misma crítica), con umbrales en `config/criticos.yml`:
    - `historia`: el audio de los primeros 2 s no es silencio (gancho inmediato) y hay cambio visible entre fotogramas consecutivos (diferencia media en gris de miniaturas) en al menos la mitad de los pares; si no, `todavia_no`.
    - `subtitulos`: ninguna línea se lee a más de `max_cps` (17) caracteres por segundo, y en los 6 fotogramas no hay píxeles de papel de marca (`colores.papel`, tolerancia) en la franja de botones (abajo de `zona_segura.y1` y a la derecha de `maqueta.columna_dialogo[1]` entre `zona_accion.y0` y `zona_segura.y1`) por encima de `max_papel_en_botones`.
    - `tecnica`: ningún fotograma casi negro o casi blanco entero, nitidez (varianza del laplaciano) sobre `min_nitidez` en al menos 4 de 6, y volumen medio del audio entre `vol_min_db` y `vol_max_db` (mezcla con audio).
    Las 3 muestras con video de `muestras` pasan los 3 críticos sin IA (calibración documentada en la config).
11. Interruptores: `criticos.activo: false` → la toma sale como hoy (sin críticos, plan sin clave `criticos`). `criticos.modo`: `cadena` (por defecto) o `sin_ia`. `config/criticos.yml` validado en `fabrica/validar.py`.

### D. «Todavía no» vuelve al plan una vez

12. `revisar(mp4, plan, marca, ...) -> Veredicto(decision, motivos, para_el_plan, criticas, juez, llamadas, segundos)`; el plan de la toma guarda `criticos: {ronda, decision, motivos, criticas: [{nombre, proveedor, veredicto, puntaje, motivos}], juez: {proveedor, decision}, llamadas, segundos}`.
13. Ronda 1 «todavía no» con corte de Gemini: la toma vuelve a los ojos **una vez** con la misma fuente (sin volver a descargarla si se puede) y una nota `para_el_plan` del juez añadida a la instrucción («Correcciones del juez: …», sólo como guía; las reglas de siempre mandan). Se recompone, pasa otra vez las puertas y los críticos (ronda 2). Con corte sin IA no hay replanteo posible: se descarta directo.
14. Ronda 2 «todavía no» (o ronda 1 con corte sin IA): descarte con motivos `críticos: …`, `gastar=True` (es del material), y una línea en `estado/criticos.jsonl` (fecha, fuente, ronda, decisión, motivos, proveedores; se conservan las últimas 300). Una toma que pasa también deja su línea (para medir).
15. **Los críticos nunca gastan una fuente por la máquina**: sin tiempo de plazo para los críticos, error inesperado del módulo o cupo agotado sin sin-IA posible → la toma **sale como hoy** con `criticos: {decision: null, motivo}` y una línea `AVISO`. `plazo.SinTiempo` sigue subiendo al ciclo como siempre.
16. Presupuesto de tiempo: `criticos.tope_s` (por defecto 120 s por ronda) recortado por `plazo`; lo que no quepa cae a sin IA. Se mide y se imprime el tiempo de los críticos por toma.

### E. Pruebas y muestra

17. `pruebas/test_criticos.py` con **respuestas falsas** (transportes inyectados): crítica válida, inválida (clave extra, puntaje 9, incoherente), 429 → sin IA, 404 → siguiente modelo, sin clave → sin IA, cupo agotado → sin IA, reserva de ojos respetada, ventana de minuto con espera inyectada, juez regla (cada caso), juez con modelo y su piso, que los críticos no reciben el plan, ronda 2 con nota del juez (con `hacer_toma` y ojos falsos), descarte final con `gastar=True` y línea en `criticos.jsonl`, error del módulo → toma como hoy sin gastar, `activo: false` igual que hoy, segundo proveedor con transporte falso, sin IA determinista sobre un MP4 sintético bueno (pasa) y uno malo (negro y mudo: todavía no). Suite entera en verde (`python -m pytest -q`) y `puerta` en verde en la PR.
18. `muestras` corre los críticos (sin clave en ese workflow: sin IA) sobre las muestras con video y escribe su veredicto en `puerta-estilo-auto.md`; las muestras pasan. `ojos-prueba` (con `GEMINI_API_KEY`) corre los críticos con la cadena real sobre la toma de Sintel y deja en `informe.md` las 3 críticas, el juez, el proveedor de cada una, las peticiones gastadas y el tiempo; nunca imprime la clave. Una corrida real de cada una en la PR.

## Fuera de alcance

Plan por momentos (#20), transcripción propia (#19), pantalla dividida (#16), puerta técnica (#17), reglas de Kevin (#18). Si alguna se fusiona antes, se rebasa. Los críticos no tocan informativas ni compilados en esta tarea (seguimiento).

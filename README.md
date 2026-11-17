# Granja de Clips

Canal permanente de clips de videojuegos y streamers en español (marca "Guion Vivo"), 100 % en la nube y con presupuesto Q0. Temporada 1: GTA 6 (19 nov 2026). Este repo es la fábrica; el procedimiento completo está en `PLAYBOOK.md` (v1.2) y manda sobre este README.

Bloque N1: repo base, puertas de configuración y pruebas. Todavía no hay detección, edición ni publicación (llegan en bloques siguientes).

## Estructura

| Ruta | Qué es |
|---|---|
| `PLAYBOOK.md` | Copia literal del playbook v1.2 |
| `temporada.yml` | Juego, fechas, ventana de estreno, palabras a evitar, cupos, pilares |
| `fuentes.yml` | Lista blanca de fuentes (sección 3). Sin ficha vigente no se descarga ni se publica |
| `fuentes-candidatas.md` | Campañas vistas sin alta todavía |
| `marca.yml`, `marca/` | Sistema visual [Guion]; `marca/` se llena en N2 |
| `config/` | `horarios.yml`, `modo.yml`, `canales.yml`, `prueba-del-dia.yml` |
| `procedimientos/` | `director-diario.md`, `revision-semanal.md` |
| `fabrica/` | `config.py`, `pausa.py`, `registro.py`, `validar.py` |
| `pruebas/` | pytest, con copias temporales del repo |
| `marcador.csv`, `registro.md`, `hechos.md`, `banco-de-ideas.md`, `aprendizajes.md`, `retiros.md`, `envios-pendientes.csv` | Bucle de aprendizaje y trazabilidad |
| `.github/workflows/puerta.yml` | CI: validar y probar en cada push y pull request (Python 3.12) |

## Comandos (la puerta)

```
pip install -r requirements.txt
python -m fabrica.validar     # sale 0 sin errores, 1 con errores
python -m pytest -q
```

`validar` acepta `--raiz RUTA` para revisar otra copia del repo.

## PAUSA

- Si existe el archivo `PAUSA`, la fábrica no publica, sin importar su campo `hasta` (que es sólo informativo).
- Formato: una clave por línea (`motivo`, `desde`, `hasta`, `autor`). `motivo` es obligatorio.
- Se crea con `fabrica.pausa.crear_pausa(raiz, motivo, autor)` ante una alerta roja (sección 12). Los agentes automáticos nunca la borran: sólo la quita el jefe de Clips o Kevin.
- Ahora mismo hay una PAUSA de arranque hasta el 18 nov 2026 11:00 GT.

## Modo

`config/modo.yml`: `ensayo` (no publica nada) o `vivo`. `plataformas_activas` lista las plataformas con publicación real (shorts, tiktok, reels); vacía por ahora. Pasar a `vivo` requiere puertas en verde y quitar la PAUSA.

## Decisiones conservadoras tomadas en N1

- `marca.yml` queda con `confirmado: false`: Kevin aún no confirma el nombre "Guion Vivo".
- La ficha única `propio-tipografico` es tipo C, sin metraje ajeno, sin campaña y sin cobro.
- El validador exige `pilares: [informativa]` exacto para el tipo D (sólo informativa) y trata `encabezado_max_palabras` y `subtitulo_max_lineas` como enteros de 1 hasta el máximo del playbook.
- `validar` revisa en canales sólo `servicio` y `nombre_esperado`; `buffer_channel_id` puede ser null hasta que Kevin conecte los canales.
- Fechas de `hechos.md` y de la tabla horaria son las de la especificación; la tabla de hora de Guatemala es cálculo propio, no dato oficial.
- Detector de claves: los patrones se aplican por línea; en `pruebas/` una línea con la marca `EJEMPLO-FALSO` se ignora. Las pruebas generan sus claves falsas en tiempo de ejecución, así que ningún archivo contiene una.
- `.venv`, `__pycache__`, `.pytest_cache` y `.git` se ignoran en las búsquedas de media y claves.
- Las versiones de `requirements.txt` están fijadas a las instaladas al construir (PyYAML 6.0.3, pytest 9.1.1, tzdata 2025.2).
- Los commits llevan la atribución indicada por la sesión (Claude Sonnet 5.5), que difiere de la mencionada en la especificación (Opus 5.5).
- No se hizo push ni se creó remoto; la fusión a `main` la hace el jefe.

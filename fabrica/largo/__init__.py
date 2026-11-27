"""Video largo semanal de YouTube: las tomas de la semana que no salieron como Short, en horizontal.

- `seleccion`: qué tomas entran (libro de envíos, recetas, vetos y retiros) y en qué orden.
- `horizontal`: cada toma otra vez desde su fuente, a 1920x1080, con la marca de `config/largo.yml`.
- `memes`: biblioteca de la semana (populares de Myinstants filtrados por Gemini) y dónde va cada uno.
- `armar`: intro, avances, outro, unión y capítulos.
- `puerta`: lo que se revisa antes de subir.
- `youtube`: subida con la API de YouTube (refresh token en los secretos de GitHub).

CLI: `python -m fabrica.largo --help`.
"""

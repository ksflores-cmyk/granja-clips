# marca/

Todo lo visual de la marca [Guion]. Los valores (colores, fuente, medidas de la página, mosca, contador de tomas) viven en `../marca.yml`; el código de `fabrica/editar/` no lleva ningún color, fuente ni medida escrita a mano. Sistema de diseño legible para personas y agentes: el artefacto "Guion Vivo" (sistema de diseño) del Project.

## Qué hay

- `fuentes/`: Courier Prime Regular, Bold, Italic y BoldItalic, de `google/fonts` (`ofl/courierprime`), con `OFL.txt`. Licencia SIL OFL 1.1: uso comercial permitido. Sumas sha256 en `../marca.yml` (`fuente.archivos`). Nota: la suma abreviada de BoldItalic en la especificación de N2 tenía una errata; el archivo es el original de google/fonts.
- `trazos/`: 10 trazos de lápiz rojo en PNG transparente (5 `subrayado-NN.png` y 5 `circulo-NN.png`), generados por código con semillas fijas. Se regeneran con `python -m fabrica.editar.lapiz --generar` y salen idénticos (lo comprueba una prueba). La toma elige el trazo con `numero % 5` dentro de su tipo, para no repetir el mismo dos tomas seguidas. El círculo usa una superelipse con el exponente más bajo que no pisa la caja de la palabra (una elipse con esos radios cortaba las esquinas).
- `muestras/muestras.yml`: las 3 muestras de la puerta de estilo, con su fuente, licencia, sha256, recorte y textos exactos.

## Muestras

- En la nube: el workflow `muestras` las renderiza en GitHub Actions y las deja como artefacto por 3 días. No escribe en ninguna rama: el video nunca entra al repo.
- A mano (fuera de la PC de Kevin): `python -m fabrica.editar.muestras --salida <carpeta fuera del repo>`. Descarga sólo de `raw.githubusercontent.com` y verifica el sha256 de cada archivo.
- Fuentes de las muestras: *Sintel* y *Big Buck Bunny*, © Blender Foundation, licencia CC BY 3.0 (copias públicas en los repos de MDN y MediaElement); los subtítulos en inglés de *Sintel* son los públicos de MDN y la traducción al español es nuestra. Están en `../fuentes.yml` con `uso: muestras` y `publicable: false`: sirven para revisar el estilo y nunca se publican.

## Página de toma (resumen)

Lienzo 1080×1920 color papel. Fila superior a 178 px con `TOMA nnn` a la izquierda y la mosca `GUION VIVO` arriba a la derecha (abajo la tapan los botones y el texto de las apps). Gancho de hasta 6 palabras en 2 líneas, con el lápiz rojo sobre una palabra; regla; línea de escena; el video a todo el ancho, sin recortar; diálogo centrado en la columna de 120 a 960 px (2 líneas como máximo); cierre de medio segundo con `CORTE.` y el crédito. Sólo corte seco, sin música de biblioteca ni voz sintética.

No guardar video ni audio en esta carpeta.

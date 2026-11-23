# Decisiones del gemelo (y lo que se descartó)

## 1. Motor: MapLibre GL, no three.js ni Cesium

| Opción | Coste real | Veredicto |
|---|---|---|
| **MapLibre GL** (`fill-extrusion` + `raster-dem`) | 0 assets, ~2 MB de librería, fluido en móvil, capas de datos encima gratis | **elegido** |
| three.js a mano | hay que hornear huellas, DEM y ortofoto por km² (~2 MB/km²) y escribir el encaje | se usará solo cuando haga falta detalle propio (LOD2/LOD3, interiores) |
| Cesium / 3D Tiles | el motor correcto para **territorio** y fotogrametría, pero pesa y pide teselas 3D Tiles que aquí no existen | descartado por ahora |
| Google Photorealistic | no es gratis ni se puede cachear | descartado |

La razón de fondo: **el techo de calidad lo pone el dato, no el motor**. Con altura de OSM, el
resultado es LOD1 en cualquier motor; cambiar de motor no añade metros de detalle.

## 2. De dónde sale la altura (y por qué no la calculamos nosotros)

OpenFreeMap publica el esquema **OpenMapTiles** y en su capa `building` ya vienen calculados
`render_height`, `render_min_height`, `colour` y `hide_3d` (minzoom 13, maxzoom 14). Verificado
decodificando una tesela real de Madrid (z14 / x=8023 / y=6177, 1,0 MB):

- **227 edificios, 227 con `render_height`** → mediana **20 m**, p90 **74 m**, máximo **147 m**
- **31 con `hide_3d = true`** → no se extruyen (curaduría del editor, se respeta)

Reproducirlo a mano con `height` → `building:levels × 4,26` → Catastro daría **peor** resultado en
España, donde `height` casi no existe, y obligaría a mantener un pipeline de alturas propio.

## 3. Qué se copia del análisis de otra app (y qué no)

Se estudió una app de referencia de mapas 3D (Provenance Online) para aprender **el método**, no
para reutilizar su código ni sus datos:

- **Sí:** el patrón `render_height` + `fill-extrusion` a partir de z14, el terreno `terrarium` de AWS,
  el picking en **fuente GeoJSON aparte** (no repintar la base) y la API **por bounding box** para los
  objetos 3D del entorno.
- **No:** sus capas de tráfico (en España no tienen cobertura: **0 de 2.509 features**), sus datos de
  cámaras (son de la DGT, que es el productor) ni su licencia de cables submarinos (© TeleGeography).

Análisis completo, con las cifras medidas: `MasterMind/notes/2026-11-21-provenance-online-analisis.md`.

## 4. Trampas ya pagadas en este proyecto

- **El estilo se recarga al cambiar de tema y `style.load` llega antes que sus capas.** La escena se
  prepara en tres puertas (`load`, `style.load`, `idle`) y la función es idempotente. Sin la de
  `idle`, con el tema claro la capa de edificios del propio estilo seguía visible y la nuestra se
  quedaba a medias: `capas3d: ["building-3d","edificios-3d"]` y contador a 0.
- **No medir contra un mapa a medio cargar**: `contar()` empieza con `if (!mapa.loaded()) return`.
- **Ocultar las capas de edificios del estilo** (`building`, `building-3d`, `tn-buildings-3d`) antes de
  añadir la propia: si no, se dibuja dos veces la misma geometría y se paga el doble por frame.
- **Tema claro por defecto.** Con el estilo oscuro las etiquetas y los volúmenes se fundían: la
  lectura del callejero se perdía. El oscuro queda como interruptor, no como default.
- **`vision_analyze` no decide**: dos pasadas del mismo PNG pueden decir «no hay edificios» y «hay 425».
  Lo que manda es `queryRenderedFeatures` sobre la capa real.

## 5. Metro bajo la ciudad: capa 3D propia (prueba con la L10)

El terreno de MapLibre **no admite opacidad ni recorte**: lo que quede por debajo, desaparece. Medido
en un banco de pruebas con una caja a −40 m y una torre de control sobre el suelo: con `setTerrain()`
**0 px** de túnel y **453 px** de torre; sin terreno, **3.944 px** de túnel.

De ahí la decisión: el metro **no es una capa vectorial**, es una capa `custom` con three.js dentro del
contexto WebGL de MapLibre, y el visor tiene **dos modos**:

- **superficie** — terreno y edificios normales, metro oculto;
- **subsuelo** — `setTerrain(null)`, edificios y corteza semitransparentes, y la red a su cota real.

Trampas pagadas aquí:

- **MapLibre 5 cambió la firma de `render()`**: llega `args.defaultProjectionData.mainMatrix`, no la
  matriz suelta. Con la firma antigua salen NaN y cero píxeles sin ningún error en consola.
- **`frustumCulled = false`** en toda la geometría: la matriz está en mercator (≈0,5) y los vértices en
  metros, así que three descarta la escena entera sin avisar.
- **El datum se elige por modo**: con terreno, altitud absoluta (660,5 m en Sol); sin terreno, cotas
  relativas, que es lo que mira la cámara de MapLibre.
- **El GTFS no trae cota de vía.** Sin perfil, el túnel se dibuja a ras de calle y el «modo subsuelo»
  se lee como una cinta pintada. Se interpola la profundidad entre las dos estaciones más cercanas:
  resultado medido, **−20,4 m de mediana** respecto al terreno (rango −9,0 a −26,6).

### El dato: el catálogo está en OTRO MARCO, no desplazado

El catálogo viene de un visor de referencia (`explorar.html`) con su propio origen declarado:

```
origin_epsg25830 = [440979.959, 4475676]     # 689 m al E y 1.421 m al N de la Puerta del Sol
```

Por eso sus x/z parecían estar «1,5 km al suroeste»: se estaban leyendo como relativas a Sol. **No era
un error del dato, era un cambio de marco.** Con el origen correcto, las 31 estaciones de la L10 caen a
59-263 m de su posición real (el catálogo declara `APPROXIMATE`, rms ~20 m; no es un levantamiento).

Ese mismo visor publica **poses rígidas ya ajustadas** por modelo (`matrix_xz`, `translation_xz`,
`translation_y`, `scale_xyz`). El horno `scripts/hornear_metro_v19.py` las aplica directamente a los
GLB originales y pasa todo al marco del gemelo; la traza son los 60 tramos dirigidos del propio
catálogo (2.889 puntos, 38,24 km). Recibo: **centro de estación a traza, mediana 2,5 m** (máx 85 m).

La lección: **antes de declarar «el dato está mal», busca su origen y su marco**. Y si el proveedor
publica poses, úsalas: recalcularlas por PCA es peor que no girar nada.

## 6. Siguiente escalón, con su coste

- **LOD2** — LiDAR **MDT02** del IGN (2 m) para altura por vértice y tejado inclinado, más
  `numberOfFloorsAboveGround` del Catastro (WFS INSPIRE, gratis y sin clave). Es el salto que cambia
  la **silueta**, que es la mayor parte de la percepción.
- **Fachadas** — PNOA oblicua (descarga por vuelo, no hay WMS) o Mapillary.
- **Datos encima** — red eléctrica y agua de `OpenInfraES`, GTFS de Metro y Cercanías.

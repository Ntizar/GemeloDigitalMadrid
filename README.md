# Gemelo Digital Madrid

Madrid en 3D **desde datos abiertos**, en el navegador y sin assets propios: ni un `.glb`, ni un
horneado, ni una GPU dedicada. Se abre, se inclina y se recorre la ciudad.

- **En vivo:** <https://ntizar.github.io/GemeloDigitalMadrid/>
- Navegación libre con el hash en la URL (zoom, giro e inclinación viajan en el enlace, se puede compartir).

## Qué se ve y de dónde sale

| Elemento | Fuente | Cómo |
|---|---|---|
| Huellas y **altura** de los edificios | **OpenStreetMap** (etiquetas `height` y `building:levels`) servido por **OpenFreeMap** (esquema OpenMapTiles) | las teselas vectoriales ya traen `render_height`, `render_min_height`, `colour` y `hide_3d` calculados |
| Volumetría | **MapLibre GL** | una capa `fill-extrusion` de **6 líneas** (abajo) |
| Terreno | **AWS Terrain Tiles** (SRTM/NED, encoding `terrarium`, maxzoom 14) | `map.setTerrain()` |
| Mapa base | OpenFreeMap `dark` / `liberty` | teselas vectoriales, sin clave |

La capa que hace todo el trabajo, tal cual está en `index.html`:

```js
{ id:"edificios-3d", type:"fill-extrusion", source:"openmaptiles", "source-layer":"building",
  minzoom: 13.5,
  paint: { "fill-extrusion-height": ["coalesce", ["get","render_height"], 8],
           "fill-extrusion-base":   ["coalesce", ["get","render_min_height"], 0],
           "fill-extrusion-color":  RAMPA,          // color por altura
           "fill-extrusion-opacity": 0.94 } }
```

Cuando un edificio trae `hide_3d = true` no se extruye: se pinta en gris neutro. Es curaduría del
propio dato (no se inventa una altura para lo que el editor marcó como no volumétrico).

## Qué es y qué NO es

- **Es** un **LOD1**: huella real + altura de la etiqueta, tejado plano. Sirve para leer la ciudad,
  la densidad, los corredores y los hitos.
- **No es** un modelo medido edificio a edificio ni un gemelo fotorrealista. En una tesela z14 de
  Madrid: **227 edificios, 227 con altura**, mediana **20 m**, p90 **74 m**, máximo **147 m**.

## Cifras medidas (no prometidas)

- Vista inicial (Puerta del Sol, z15.2, pitch 55): **~425 edificios** en pantalla, máximo **147 m**.
- Cuatro vistas de un clic: Sol, Castellana, El Retiro, Barajas.
- El contador de la cabecera **se cuenta del dato dibujado** (`queryRenderedFeatures`), no de un
  array en memoria: si no hay edificios en la vista, pone «—», no un cero mintiendo.

## Metro bajo tierra (prueba con la L10)

**`Metro L10 bajo tierra`** en el panel: apaga el terreno, deja **edificios y corteza translúcidos** y
dibuja la línea a su cota real. Dos deslizadores (edificios y suelo) abren o cierran el «rayos X».

| Pieza | Fuente | Cifra |
|---|---|---|
| Traza | GTFS oficial del CRTM (`shapes.txt`), simplificada a 4 m | 4 trazas · 350 puntos · **38,23 km** |
| Anclaje de estaciones | `stops.txt` por nombre | 31/31 · error mediano **17 m** al eje |
| Estaciones 3D | modelos del catálogo, trasladados a su parada | **118.103 triángulos** en 1 llamada |
| Andenes | catálogo funcional | **64** andenes |
| Corteza | DEM terrarium + ortofoto PNOA recortada | 172 × 222 cotas · 1 MB de textura |
| Perfil de vía | interpolado entre las dos estaciones más cercanas | **−20,4 m** de mediana bajo el terreno |

Tres cosas que hay que saber antes de tocar esto:

- **El metro no es una capa vectorial.** El terreno de MapLibre no se puede hacer translúcido, así que
  la línea va como capa `custom` con three.js dentro del contexto WebGL del mapa. Con terreno activo,
  el subsuelo no se ve (0 px medidos); el modo subsuelo apaga el terreno y pone su propia corteza.
- **El GTFS no trae cota de vía.** Se interpola la profundidad entre estaciones; sin eso el túnel se
  dibuja a ras de calle y parece una cinta pintada.
- **Se regenera con** `python scripts/hornear_metro.py 10` (lee de `../metro-gemelo`; `METRO_GEMELO=`
  para otra ruta). Ojo: el `red.json` de `metro-gemelo` está **~1,5 km desplazado** respecto al GTFS
  oficial, por eso el horneador reconstruye la traza desde `shapes.txt` y ancla por `stops.txt`.
  Detalle y cifras: [`docs/DECISIONES.md`](docs/DECISIONES.md) §5.

## Hoja de ruta

| Fase | Qué | Qué cambia |
|---|---|---|
| 1 | LOD1 con OpenFreeMap + Terrarium (**esto**) | la ciudad se lee, gratis y en móvil |
| 2 | **LOD2**: LiDAR MDT02 del IGN (2 m) + `numberOfFloorsAboveGround` del Catastro (WFS INSPIRE) | silueta real: tejados inclinados, áticos, patios |
| 3 | Fachadas: PNOA oblicua o Mapillary | deja de ser «papel pintado» |
| 4 | Capas de datos encima: red eléctrica y agua de **OpenInfraES**, transporte GTFS | el gemelo pasa a cuadro de mando · **transporte: L10 bajo tierra hecho** |

Detalle de las decisiones y de los descartes: [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Atribución y licencias

Mapa y edificios: © OpenStreetMap contributors (**ODbL 1.0**), teselas de
[OpenFreeMap](https://openfreemap.org) · esquema [OpenMapTiles](https://openmaptiles.org).
Terreno: [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) (dominio público).
Motor: [MapLibre GL JS](https://maplibre.org) 5.24.1 (BSD-3).

Hecho con ❤️ por David Antizar

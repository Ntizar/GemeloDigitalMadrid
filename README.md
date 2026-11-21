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

## Hoja de ruta

| Fase | Qué | Qué cambia |
|---|---|---|
| 1 | LOD1 con OpenFreeMap + Terrarium (**esto**) | la ciudad se lee, gratis y en móvil |
| 2 | **LOD2**: LiDAR MDT02 del IGN (2 m) + `numberOfFloorsAboveGround` del Catastro (WFS INSPIRE) | silueta real: tejados inclinados, áticos, patios |
| 3 | Fachadas: PNOA oblicua o Mapillary | deja de ser «papel pintado» |
| 4 | Capas de datos encima: red eléctrica y agua de **OpenInfraES**, transporte GTFS | el gemelo pasa a cuadro de mando |

Detalle de las decisiones y de los descartes: [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Atribución y licencias

Mapa y edificios: © OpenStreetMap contributors (**ODbL 1.0**), teselas de
[OpenFreeMap](https://openfreemap.org) · esquema [OpenMapTiles](https://openmaptiles.org).
Terreno: [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) (dominio público).
Motor: [MapLibre GL JS](https://maplibre.org) 5.24.1 (BSD-3).

Hecho con ❤️ por David Antizar

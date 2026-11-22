/* Metro de Madrid bajo el Gemelo Digital — Línea 10 (prueba).
 *
 * La red se dibuja como CAPA 3D PROPIA (three.js dentro del contexto WebGL de
 * MapLibre), no como capa vectorial: MapLibre no deja ver debajo del terreno,
 * así que el modo subsuelo apaga el terreno y pone su propia corteza
 * semitransparente con la ortofoto.
 *
 * Marco: el mismo del horneado — origen Puerta del Sol, x/z en metros UTM 30N,
 * y = cota relativa a la calle de Sol. Sin terreno, el plano 0 de MapLibre es
 * ese mismo 0, así que todo encaja sin offsets.
 */

import * as THREE from 'three';

const LINEA = '10';
const BASE = 'datos/l10/';
const ORIGEN = { lng: -3.70379, lat: 40.41678 };   // Puerta del Sol

const maplibregl = window.maplibregl;
const mapa = window.GDM.mapa;

const estado = {
  activo: false,
  edificios: 0.14,
  suelo: 0.78,
  estaciones: true,
  tuneles: true,
  andenes: true,
  croquis: false,
  etiquetas: true,
};

let escena, camara, renderer, transform;
let corteza, grupoTuneles, grupoEstaciones, grupoAndenes, grupoPozos, grupoEstacionesTraza;
let datos = null;          // red.json
let meta = null;           // meta.json
let cab = null;            // cabecera MMG1
let etiquetas = [];        // {el, lonlat, nombre}
let capaLista = false;

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------ carga de datos ---------- */
async function cargar() {
  const [red, m, cabTxt, terrenoJson] = await Promise.all([
    fetch(BASE + 'red.json?v=1').then((r) => r.json()),
    fetch(BASE + 'meta.json?v=1').then((r) => r.json()),
    fetch(BASE + 'estaciones.bin?v=1').then((r) => r.arrayBuffer()),
    fetch(BASE + 'terreno.json?v=1').then((r) => r.json()),
  ]);
  datos = red;
  meta = m;
  cab = leerMMG(cabTxt);
  return { terrenoJson };
}

async function cargarCorteza(terrenoJson) {
  const [cotas, tex] = await Promise.all([
    fetch(BASE + 'terreno.bin?v=1').then((r) => r.arrayBuffer()),
    new Promise((ok, mal) => {
      const t = new THREE.TextureLoader().load(BASE + 'terreno.jpg?v=1', ok, undefined, mal);
      t.colorSpace = THREE.SRGBColorSpace;
    }),
  ]);
  const { nx, nz, x0, x1, z0, z1 } = terrenoJson;
  const h = new Float32Array(cotas);
  const verts = new Float32Array(nx * nz * 3);
  const uvs = new Float32Array(nx * nz * 2);
  for (let j = 0; j < nz; j++) {
    const z = z0 + (z1 - z0) * (j / (nz - 1));
    for (let i = 0; i < nx; i++) {
      const x = x0 + (x1 - x0) * (i / (nx - 1));
      const k = j * nx + i;
      verts[k * 3] = x;
      verts[k * 3 + 1] = 0;   // modo plano: el suelo comparte el 0 de los edificios y del metro
      verts[k * 3 + 2] = z;
      uvs[k * 2] = i / (nx - 1);
      uvs[k * 2 + 1] = 1 - j / (nz - 1);     // fila 0 de la imagen = z0
    }
  }
  const ind = new Uint32Array((nx - 1) * (nz - 1) * 6);
  let p = 0;
  for (let j = 0; j < nz - 1; j++) {
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      ind[p++] = a; ind[p++] = c; ind[p++] = b;
      ind[p++] = b; ind[p++] = c; ind[p++] = d;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(verts, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geo.setIndex(new THREE.BufferAttribute(ind, 1));
  geo.computeVertexNormals();

  const mat = new THREE.MeshStandardMaterial({
    map: tex, color: 0x9aacbf, transparent: true, opacity: estado.suelo,
    depthWrite: false, side: THREE.DoubleSide, roughness: 1, metalness: 0,
  });
  const malla = new THREE.Mesh(geo, mat);
  malla.renderOrder = 20;
  malla.frustumCulled = false;
  return malla;
}

/* ------------------------------------------------ binario MMG1 ------------ */
function leerMMG(buf) {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(...new Uint8Array(buf, 0, 4));
  if (magic !== 'MMG1') throw new Error('binario MMG1 no reconocido');
  const hlen = dv.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, hlen)));
  let off = 8 + hlen;
  const V = header.vertices, T = header.triangulos;
  const pos = new Float32Array(buf.slice(off, off + V * 12)); off += V * 12;
  const col = new Uint8Array(buf.slice(off, off + V * 3)); off += V * 3;
  const idx = new Uint32Array(buf.slice(off, off + T * 12));
  return { header, pos, col, idx };
}

/* ------------------------------------------------ geometría --------------- */
function tuboPorRuta(puntos, radio) {
  // tubo de sección hexagonal a lo largo de la polilínea, sin splines que se salgan
  const n = puntos.length;
  const rad = 6;
  const pos = new Float32Array(n * rad * 3);
  const idx = [];
  const arriba = new THREE.Vector3(0, 1, 0);
  const tan = new THREE.Vector3(), lat = new THREE.Vector3(), ver = new THREE.Vector3();
  for (let i = 0; i < n; i++) {
    const p = puntos[i];
    const q = puntos[Math.min(i + 1, n - 1)];
    const o = puntos[Math.max(i - 1, 0)];
    tan.set(q[0] - o[0], q[1] - o[1], q[2] - o[2]);
    if (tan.lengthSq() < 1e-9) tan.set(1, 0, 0);
    tan.normalize();
    lat.crossVectors(arriba, tan).normalize();
    if (lat.lengthSq() < 1e-9) lat.set(1, 0, 0);
    ver.crossVectors(tan, lat).normalize();
    for (let k = 0; k < rad; k++) {
      const a = (k / rad) * Math.PI * 2;
      const c = Math.cos(a) * radio, s = Math.sin(a) * radio;
      const j = (i * rad + k) * 3;
      pos[j] = p[0] + lat.x * c + ver.x * s;
      pos[j + 1] = p[1] + lat.y * c + ver.y * s;
      pos[j + 2] = p[2] + lat.z * c + ver.z * s;
    }
  }
  for (let i = 0; i < n - 1; i++) {
    for (let k = 0; k < rad; k++) {
      const a = i * rad + k, b = i * rad + (k + 1) % rad;
      const c = (i + 1) * rad + k, d = (i + 1) * rad + (k + 1) % rad;
      idx.push(a, c, b, b, c, d);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return geo;
}

function montarRed(color) {
  const g = new THREE.Group();
  const col = new THREE.Color(color);
  const material = new THREE.MeshStandardMaterial({
    color: col, emissive: col.clone().multiplyScalar(0.12), emissiveIntensity: 0.2,
    roughness: 0.38, metalness: 0.2, side: THREE.DoubleSide,
  });
  let tris = 0;
  for (const r of datos.rutas) {
    const pts = [];
    for (let i = 0; i < r.p.length; i += 2) pts.push(r.p[i]);   // 1 punto de cada 2
    if (pts.length < 2) continue;
    const geo = tuboPorRuta(pts, 3.0);
    const malla = new THREE.Mesh(geo, material);
    malla.frustumCulled = false;
    g.add(malla);
    tris += geo.index.count / 3;
  }
  g.userData.tris = tris;
  return g;
}

function montarAndenes() {
  const seg = [];
  for (const e of datos.estaciones) {
    for (const a of e.andenes) {
      for (let i = 0; i < a.path.length - 1; i++) seg.push(...a.path[i], ...a.path[i + 1]);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(seg), 3));
  const m = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color: 0xd8e6f7, transparent: true, opacity: 0.55,
  }));
  m.frustumCulled = false;
  m.renderOrder = 5;
  return m;
}

function montarEstaciones(mm) {
  const g = new THREE.Group();
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(mm.pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(new Uint8Array(mm.col), 3, true));
  geo.setIndex(new THREE.BufferAttribute(mm.idx, 1));
  geo.computeVertexNormals();
  const malla = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide,
    emissive: 0x0d1626, emissiveIntensity: 0.6,
  }));
  malla.frustumCulled = false;
  g.add(malla);

  // pozos: de la calle (cota del DEM) al techo del vestíbulo del modelo
  const pozos = new Float32Array(mm.header.estaciones.length * 6);
  const discos = [];
  mm.header.estaciones.forEach((e, i) => {
    const [x, z] = e.pozo;                 // pozo = [x, z] (2 valores, no 3)
    const calle = e.c[1];
    pozos.set([x, calle + 0.4, z, x, e.pozo_y, z], i * 6);
    discos.push(x, calle + 0.6, z);
  });
  const gp = new THREE.BufferGeometry();
  gp.setAttribute('position', new THREE.BufferAttribute(pozos, 3));
  const pozosMalla = new THREE.LineSegments(gp, new THREE.LineBasicMaterial({
    color: 0x8fb4d8, transparent: true, opacity: 0.5,
  }));
  pozosMalla.frustumCulled = false;
  g.add(pozosMalla);

  const gd = new THREE.BufferGeometry();
  gd.setAttribute('position', new THREE.BufferAttribute(new Float32Array(discos), 3));
  const puntos = new THREE.Points(gd, new THREE.PointsMaterial({
    color: 0x9ad7ff, size: 22, sizeAttenuation: true, transparent: true, opacity: 0.9,
  }));
  puntos.frustumCulled = false;
  g.add(puntos);
  return g;
}


/* Estaciones construidas SOBRE la traza: encaje por construcción.
   Los modelos del catálogo son croquis de intercambiador casi cuadrados
   (ratio 0,9, sin eje dominante), así que girarlos no los alinea: se ofrecen
   como capa aparte ("croquis") y aquí se dibuja una estación a escala real. */
function fusionar(geos) {
  let nv = 0, ni = 0;
  for (const g of geos) { nv += g.attributes.position.count; ni += g.index.count; }
  const pos = new Float32Array(nv * 3), nor = new Float32Array(nv * 3);
  const idx = new Uint32Array(ni);
  let vo = 0, io = 0;
  for (const g of geos) {
    pos.set(g.attributes.position.array, vo * 3);
    nor.set(g.attributes.normal.array, vo * 3);
    const gi = g.index.array;
    for (let i = 0; i < gi.length; i++) idx[io + i] = gi[i] + vo;
    vo += g.attributes.position.count;
    io += gi.length;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  geo.computeBoundingSphere();
  return geo;
}

function cajaEstacion(w, h, d, x, y, z, theta) {
  const g = new THREE.BoxGeometry(w, d, h);   // X largo, Y alto, Z ancho -> se gira en Y
  g.rotateY(theta);
  g.translate(x, y, z);
  return g;
}

function montarEstacionesTraza() {
  const andenes = [], naves = [], pozos = [];
  for (const e of datos.estaciones) {
    if (!e.tan || !e.xz) continue;
    const [x, z] = e.xz;
    const y = Number.isFinite(e.anden_y) ? e.anden_y : -16;
    const theta = Math.atan2(-e.tan[1], e.tan[0]);   // eje X de la caja -> tangente
    andenes.push(cajaEstacion(110, 1.0, 8, x, y, z, theta));
    naves.push(cajaEstacion(118, 5.0, 18, x, y + 3.2, z, theta));
    pozos.push(cajaEstacion(6, Math.abs(y) + 6, 6, x, (y + 6) / 2, z, theta));
  }
  const g = new THREE.Group();
  const matAnden = new THREE.MeshStandardMaterial({
    color: 0xd6e4f4, emissive: 0x1d2f45, emissiveIntensity: 0.5, roughness: 0.6, metalness: 0.05,
  });
  const matNave = new THREE.MeshStandardMaterial({
    color: 0x8fb6d9, emissive: 0x14243a, emissiveIntensity: 0.55, roughness: 0.5, metalness: 0.15,
  });
  const matPozo = new THREE.MeshStandardMaterial({
    color: 0x6f8aa6, emissive: 0x0e1a28, emissiveIntensity: 0.4, roughness: 0.7,
  });
  for (const [geos, mat] of [[andenes, matAnden], [naves, matNave], [pozos, matPozo]]) {
    if (!geos.length) continue;
    const m = new THREE.Mesh(fusionar(geos), mat);
    m.frustumCulled = false;
    m.renderOrder = 3;
    g.add(m);
  }
  return g;
}

/* ------------------------------------------------ capa de MapLibre -------- */
function capaMetro() {
  const mc = maplibregl.MercatorCoordinate.fromLngLat(ORIGEN, 0);
  const s = mc.meterInMercatorCoordinateUnits();
  transform = new THREE.Matrix4()
    .makeTranslation(mc.x, mc.y, mc.z)
    .scale(new THREE.Vector3(s, -s, s))
    .multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2));

  return {
    id: 'metro3d',
    type: 'custom',
    renderingMode: '3d',
    onAdd(m, gl) {
      if (!renderer) {
        renderer = new THREE.WebGLRenderer({ canvas: m.getCanvas(), context: gl, antialias: true });
        renderer.autoClear = false;
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        camara = new THREE.Camera();
      }
      escena = new THREE.Scene();
      escena.add(new THREE.HemisphereLight(0xdfeaff, 0x1a2028, 2.4));
      const sol = new THREE.DirectionalLight(0xffffff, 1.5);
      sol.position.set(-8000, 12000, 6000);
      escena.add(sol);

      grupoTuneles = montarRed(datos.color);
      grupoAndenes = montarAndenes();
      grupoEstaciones = montarEstaciones(cab);
      grupoEstacionesTraza = montarEstacionesTraza();
      escena.add(grupoTuneles, grupoAndenes, grupoEstacionesTraza, grupoEstaciones);
      if (corteza) escena.add(corteza);
      capaLista = true;
      aplicarVisibilidad();
    },
    render(gl, args) {
      if (!estado.activo) return;
      const mArr = (args && args.defaultProjectionData) ? args.defaultProjectionData.mainMatrix : args;
      const m = new THREE.Matrix4().fromArray(mArr);
      camara.projectionMatrix = m.multiply(transform);
      camara.projectionMatrixInverse.copy(camara.projectionMatrix).invert();
      renderer.resetState();
      renderer.render(escena, camara);
      mapa.triggerRepaint();
    },
  };
}

function asegurarCapa() {
  if (!datos) return;
  if (mapa.getLayer('metro3d')) return;
  if (!mapa.isStyleLoaded()) return;
  mapa.addLayer(capaMetro());
}

function aplicarVisibilidad() {
  if (!capaLista) return;
  grupoTuneles.visible = estado.tuneles;
  grupoAndenes.visible = estado.andenes;
  grupoEstaciones.visible = estado.croquis;
  grupoEstacionesTraza.visible = estado.estaciones;
  if (corteza) corteza.visible = estado.suelo > 0.02;
  const cont = $('etiquetas');
  if (cont) cont.hidden = !(estado.activo && estado.etiquetas);
  mapa.triggerRepaint();
}

/* etiquetas del mapa base: en modo subsuelo estorban (POIs encima del túnel) */
let capasSimbolo = [];
function ocultarEtiquetasBase(ocultar) {
  if (!mapa.isStyleLoaded()) return;
  if (ocultar) {
    capasSimbolo = [];
    for (const l of mapa.getStyle().layers) {
      if (l.type === 'symbol' && mapa.getLayoutProperty(l.id, 'visibility') !== 'none') {
        capasSimbolo.push(l.id);
        mapa.setLayoutProperty(l.id, 'visibility', 'none');
      }
    }
  } else {
    for (const id of capasSimbolo) {
      if (mapa.getLayer(id)) mapa.setLayoutProperty(id, 'visibility', 'visible');
    }
    capasSimbolo = [];
  }
}

/* ------------------------------------------------ modo subsuelo ----------- */
function entrar() {
  if (!capaLista) return false;
  estado.activo = true;
  mapa.setTerrain(null);
  ocultarEtiquetasBase(true);
  if (mapa.getLayer('edificios-3d')) {
    mapa.setPaintProperty('edificios-3d', 'fill-extrusion-opacity', estado.edificios);
  }
  aplicarVisibilidad();
  volar('l10');
  return true;
}

function salir() {
  estado.activo = false;
  ocultarEtiquetasBase(false);
  const quiereTerreno = $('chk-terreno') ? $('chk-terreno').checked : false;
  if (quiereTerreno && mapa.getSource('terreno-dem')) {
    mapa.setTerrain({ source: 'terreno-dem', exaggeration: 1.25 });
  }
  if (mapa.getLayer('edificios-3d')) {
    mapa.setPaintProperty('edificios-3d', 'fill-extrusion-opacity', 0.94);
  }
  aplicarVisibilidad();
}

const VISTAS = {
  l10: { center: [-3.6935, 40.4495], zoom: 13.6, pitch: 62, bearing: -22 },
  nm: { center: [-3.6922, 40.4465], zoom: 16.0, pitch: 66, bearing: -38 },
  tribunal: { center: [-3.7013, 40.4255], zoom: 16.4, pitch: 68, bearing: -55 },
  chamartin: { center: [-3.6822, 40.4722], zoom: 16.2, pitch: 66, bearing: 20 },
};

function volar(clave) {
  const v = VISTAS[clave];
  if (!v) return;
  mapa.flyTo({ ...v, duration: 1800, essential: true });
}

/* ------------------------------------------------ etiquetas --------------- */
function crearEtiquetas() {
  const cont = $('etiquetas');
  if (!cont || etiquetas.length) return;
  for (const e of datos.estaciones) {
    const el = document.createElement('div');
    el.className = 'etq-est';
    el.textContent = e.nombre;
    cont.appendChild(el);
    etiquetas.push({ el, lonlat: lonlatDe(e.xz[0], e.xz[1]), nombre: e.nombre });
  }
  mapa.on('move', pintarEtiquetas);
  mapa.on('idle', pintarEtiquetas);
  pintarEtiquetas();
}

function lonlatDe(x, z) {
  // mismo mapeo que la matriz de la capa: X = mc.x + x·s ; Y = mc.y + z·s
  const mc = maplibregl.MercatorCoordinate.fromLngLat(ORIGEN, 0);
  const s = mc.meterInMercatorCoordinateUnits();
  const p = new maplibregl.MercatorCoordinate(mc.x + x * s, mc.y + z * s, 0);
  const ll = p.toLngLat();
  return [ll.lng, ll.lat];
}

function pintarEtiquetas() {
  if (!estado.activo || !estado.etiquetas) return;
  for (const e of etiquetas) {
    const p = mapa.project(e.lonlat);
    const dentro = p.x > -80 && p.x < mapa.getCanvas().clientWidth + 80 &&
                   p.y > -30 && p.y < mapa.getCanvas().clientHeight + 30;
    e.el.style.display = dentro ? 'block' : 'none';
    if (dentro) e.el.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px)`;
  }
}

/* ------------------------------------------------ arranque --------------- */
async function arrancar() {
  try {
    const { terrenoJson } = await cargar();
    corteza = await cargarCorteza(terrenoJson);
    crearEtiquetas();
    const panel = $('metro-cifras');
    if (panel) {
      panel.innerHTML = `<b>${meta.estaciones}</b> estaciones · <b>${meta.andenes}</b> andenes · ` +
        `<b>${meta.longitud_km} km</b> · ${(meta.triangulos / 1000).toFixed(0)} k triángulos · ` +
        `${(meta.puntos_traza).toLocaleString('es-ES')} puntos de traza`;
    }
    asegurarCapa();
    mapa.on('style.load', () => { capaLista = false; asegurarCapa(); });
    mapa.on('load', asegurarCapa);
    mapa.on('idle', asegurarCapa);
    // tercer intento: el estilo puede tardar en estar "cargado" con teselas en vuelo
    const reintento = setInterval(() => {
      asegurarCapa();
      if (mapa.getLayer('metro3d')) clearInterval(reintento);
    }, 400);
    if ($('chk-metro')) $('chk-metro').disabled = false;
    window.METRO = {
      estado, meta, datos,
      entrar: () => { if ($('chk-metro')) $('chk-metro').checked = true; entrar(); },
      salir: () => { if ($('chk-metro')) $('chk-metro').checked = false; salir(); },
      volar,
      diag: () => {
        const b = new THREE.Box3().setFromObject(grupoEstaciones);
        const b2 = new THREE.Box3().setFromObject(grupoTuneles);
        const b3 = corteza ? new THREE.Box3().setFromObject(corteza) : null;
        const r = (v) => v.toArray().map((n) => +n.toFixed(1));
        return {
          estaciones: { min: r(b.min), max: r(b.max) },
          tuneles: { min: r(b2.min), max: r(b2.max) },
          corteza: b3 ? { min: r(b3.min), max: r(b3.max) } : null,
          estacionPx: (nombre) => {
            const e = datos.estaciones.find((x) => x.nombre.toUpperCase().includes(nombre.toUpperCase()));
            if (!e) return null;
            const ll = lonlatDe(e.xz[0], e.xz[1]);
            const p = mapa.project(ll);
            return { nombre: e.nombre, lonlat: ll, px: [+p.x.toFixed(0), +p.y.toFixed(0)] };
          },
        };
      },
      info: () => ({
        activo: estado.activo, capa: !!mapa.getLayer('metro3d'),
        tuneles: grupoTuneles ? grupoTuneles.children.length : 0,
        estacionesTraza: grupoEstacionesTraza ? grupoEstacionesTraza.children.length : 0,
        estaciones: cab ? cab.header.estaciones.length : 0,
        tris: renderer ? renderer.info.render.triangles : 0,
        llamadas: renderer ? renderer.info.render.calls : 0,
        etiquetas: etiquetas.length,
        terreno: !!mapa.getTerrain(),
        opacidadEdificios: mapa.getLayer('edificios-3d')
          ? mapa.getPaintProperty('edificios-3d', 'fill-extrusion-opacity') : null,
      }),
    };
  } catch (e) {
    console.error('metro:', e);
    const n = $('metro-nota');
    if (n) { n.textContent = 'No se pudo cargar la L10: ' + e.message; n.classList.add('error'); }
  }
}

/* ------------------------------------------------ controles -------------- */
function conectar() {
  const chk = $('chk-metro');
  if (chk) chk.addEventListener('change', () => { chk.checked ? entrar() : salir(); });

  const redif = $('r-edif');
  if (redif) redif.addEventListener('input', () => {
    estado.edificios = Number(redif.value) / 100;
    $('v-edif').textContent = Math.round(estado.edificios * 100) + ' %';
    if (estado.activo && mapa.getLayer('edificios-3d')) {
      mapa.setPaintProperty('edificios-3d', 'fill-extrusion-opacity', estado.edificios);
    }
  });

  const rsuelo = $('r-suelo');
  if (rsuelo) rsuelo.addEventListener('input', () => {
    estado.suelo = Number(rsuelo.value) / 100;
    $('v-suelo').textContent = Math.round(estado.suelo * 100) + ' %';
    if (corteza) corteza.material.opacity = estado.suelo;
    aplicarVisibilidad();
  });

  for (const [id, clave] of [['chk-est', 'estaciones'], ['chk-tun', 'tuneles'], ['chk-and', 'andenes'], ['chk-etq', 'etiquetas'], ['chk-croquis', 'croquis']]) {
    const el = $(id);
    if (el) el.addEventListener('change', () => { estado[clave] = el.checked; aplicarVisibilidad(); pintarEtiquetas(); });
  }
  for (const [id, vista] of [['b-l10', 'l10'], ['b-nm', 'nm'], ['b-tribunal', 'tribunal'], ['b-chamartin', 'chamartin']]) {
    const el = $(id);
    if (el) el.addEventListener('click', () => volar(vista));
  }
}

conectar();
if (window.GDM && window.GDM.mapa) arrancar();
else {
  const t = setInterval(() => {
    if (window.GDM && window.GDM.mapa) { clearInterval(t); arrancar(); }
  }, 200);
}

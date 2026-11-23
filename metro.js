/* Metro de Madrid bajo el Gemelo Digital — red completa (catálogo V19 + accesos GTFS).
 *
 * - Trazas y modelos: catálogo V19 con poses rígidas, ya en el marco del gemelo.
 * - Accesos: 482 bocas/ascensores del GTFS del CRTM, en su coordenada exacta.
 * - Sin terreno: el suelo es el propio mapa (plano), y el metro va a su cota real.
 */

import * as THREE from 'three';

const BASE = 'datos/';
const ORIGEN = { lng: -3.70379, lat: 40.41678 };   // Puerta del Sol
const maplibregl = window.maplibregl;
const mapa = window.GDM.mapa;

const estado = {
  activo: false, edificios: 0.10, linea: null,
  estaciones: true, tuneles: true, accesos: true, etiquetas: true,
};

let escena, camara, renderer, transform;
let grupoRed, grupoEstaciones, grupoAccesos;
let datos = null, meta = null, cab = null;
let porLinea = new Map();
let etiquetas = [];
let capaLista = false;
const $ = (id) => document.getElementById(id);

/* ------------------------------------------------ carga ---------- */
function leerMMG(buf) {
  const dv = new DataView(buf);
  const hlen = dv.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, hlen)));
  let off = 8 + hlen;
  const V = header.vertices, T = header.triangulos;
  const pos = new Float32Array(buf.slice(off, off + V * 12)); off += V * 12;
  const col = new Uint8Array(buf.slice(off, off + V * 3)); off += V * 3;
  const idx = new Uint32Array(buf.slice(off, off + T * 12));
  return { header, pos, col, idx };
}

async function cargar() {
  const [red, m, cabTxt] = await Promise.all([
    fetch(BASE + 'red.json?v=1').then((r) => r.json()),
    fetch(BASE + 'meta.json?v=1').then((r) => r.json()),
    fetch(BASE + 'estaciones.bin?v=1').then((r) => r.arrayBuffer()),
  ]);
  datos = red; meta = m; cab = leerMMG(cabTxt);
}

/* ------------------------------------------------ geometría ---------- */
function tuboPorRuta(puntos, radio) {
  const n = puntos.length, rad = 6;
  const pos = new Float32Array(n * rad * 3), idx = [];
  const arriba = new THREE.Vector3(0, 1, 0);
  const tan = new THREE.Vector3(), lat = new THREE.Vector3(), ver = new THREE.Vector3();
  for (let i = 0; i < n; i++) {
    const p = puntos[i];
    const q = puntos[Math.min(i + 1, n - 1)], o = puntos[Math.max(i - 1, 0)];
    tan.set(q[0] - o[0], q[1] - o[1], q[2] - o[2]);
    if (tan.lengthSq() < 1e-9) tan.set(1, 0, 0);
    tan.normalize();
    lat.crossVectors(arriba, tan).normalize();
    if (lat.lengthSq() < 1e-9) lat.set(1, 0, 0);
    ver.crossVectors(tan, lat).normalize();
    for (let k = 0; k < rad; k++) {
      const a = (k / rad) * Math.PI * 2, c = Math.cos(a) * radio, s = Math.sin(a) * radio;
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
    vo += g.attributes.position.count; io += gi.length;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  return geo;
}

function montarRed() {
  const g = new THREE.Group();
  const colores = new Map(datos.lineas.map((l) => [l.ref, l.color]));
  const porL = new Map();
  for (const r of datos.rutas) {
    if (!r.p || r.p.length < 2) continue;
    if (!porL.has(r.l)) porL.set(r.l, []);
    porL.get(r.l).push(tuboPorRuta(r.p, 2.6));
  }
  let tris = 0;
  for (const [ref, geos] of porL) {
    const col = new THREE.Color(colores.get(ref) || '#8a92a0');
    const mat = new THREE.MeshStandardMaterial({
      color: col, emissive: col.clone().multiplyScalar(0.10), emissiveIntensity: 0.22,
      roughness: 0.4, metalness: 0.2,
    });
    const geo = fusionar(geos);
    const m = new THREE.Mesh(geo, mat);
    m.frustumCulled = false;
    const o = new THREE.Group();
    o.name = 'L' + ref;
    o.add(m);
    g.add(o);
    tris += geo.index.count / 3;
  }
  g.userData.tris = tris;
  return g;
}

function montarEstaciones(mm) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(mm.pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(new Uint8Array(mm.col), 3, true));
  geo.setIndex(new THREE.BufferAttribute(mm.idx, 1));
  geo.computeVertexNormals();
  const malla = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide,
    emissive: 0x0c1524, emissiveIntensity: 0.5,
  }));
  malla.frustumCulled = false;
  const g = new THREE.Group();
  g.add(malla);
  return g;
}

function montarAccesos() {
  const pts = [], nombres = [];
  for (const e of datos.estaciones) {
    for (const a of (e.accesos || [])) {
      pts.push(a.xz[0], 1.5, a.xz[1]);
      nombres.push(e.nombre);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pts), 3));
  const puntos = new THREE.Points(geo, new THREE.PointsMaterial({
    color: 0xffcf6a, size: 14, sizeAttenuation: true, transparent: true, opacity: 0.95,
  }));
  puntos.frustumCulled = false;
  puntos.renderOrder = 6;
  const g = new THREE.Group();
  g.add(puntos);
  g.userData.total = nombres.length;
  return g;
}

/* ------------------------------------------------ capa MapLibre ---------- */
function capaMetro() {
  const mc = maplibregl.MercatorCoordinate.fromLngLat(ORIGEN, 0);
  const s = mc.meterInMercatorCoordinateUnits();
  transform = new THREE.Matrix4()
    .makeTranslation(mc.x, mc.y, mc.z)
    .scale(new THREE.Vector3(s, -s, s))
    .multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2));

  return {
    id: 'metro3d', type: 'custom', renderingMode: '3d',
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
      grupoRed = montarRed();
      grupoEstaciones = montarEstaciones(cab);
      grupoAccesos = montarAccesos();
      escena.add(grupoRed, grupoEstaciones, grupoAccesos);
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
  if (!datos || mapa.getLayer('metro3d') || !mapa.isStyleLoaded()) return;
  mapa.addLayer(capaMetro());
}

function aplicarVisibilidad() {
  if (!capaLista) return;
  grupoEstaciones.visible = estado.estaciones;
  grupoAccesos.visible = estado.accesos;
  for (const o of grupoRed.children) {
    const ref = o.name.replace('L', '');
    const atenuar = estado.linea && ref !== estado.linea;
    o.visible = estado.tuneles;
    o.traverse((h) => {
      if (!h.material) return;
      h.material.transparent = true;
      h.material.opacity = atenuar ? 0.05 : 1;
    });
  }
  if ($('etiquetas')) $('etiquetas').hidden = !(estado.activo && estado.etiquetas);
  mapa.triggerRepaint();
}

/* ------------------------------------------------ modos ---------- */
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
    for (const id of capasSimbolo) if (mapa.getLayer(id)) mapa.setLayoutProperty(id, 'visibility', 'visible');
    capasSimbolo = [];
  }
}

function entrar() {
  if (!capaLista) return false;
  estado.activo = true;
  mapa.setTerrain(null);
  ocultarEtiquetasBase(true);
  if (mapa.getLayer('edificios-3d')) {
    mapa.setPaintProperty('edificios-3d', 'fill-extrusion-opacity', estado.edificios);
  }
  aplicarVisibilidad();
  volar();
  return true;
}

function salir() {
  estado.activo = false;
  ocultarEtiquetasBase(false);
  if ($('chk-terreno') && $('chk-terreno').checked && mapa.getSource('terreno-dem')) {
    mapa.setTerrain({ source: 'terreno-dem', exaggeration: 1.25 });
  }
  if (mapa.getLayer('edificios-3d')) {
    mapa.setPaintProperty('edificios-3d', 'fill-extrusion-opacity', 0.94);
  }
  aplicarVisibilidad();
}

const VISTAS = {
  red: { center: [-3.6935, 40.4300], zoom: 12.9, pitch: 60, bearing: -22 },
  nm: { center: [-3.6922, 40.4465], zoom: 16.2, pitch: 64, bearing: -38 },
  tribunal: { center: [-3.7013, 40.4262], zoom: 16.6, pitch: 66, bearing: -55 },
  castilla: { center: [-3.6890, 40.4663], zoom: 16.4, pitch: 60, bearing: -18 },
};
function volar(clave = 'red') {
  const v = VISTAS[clave];
  if (v) mapa.flyTo({ ...v, duration: 1700, essential: true });
}

/* ------------------------------------------------ etiquetas ---------- */
function lonlatDe(x, z) {
  const mc = maplibregl.MercatorCoordinate.fromLngLat(ORIGEN, 0);
  const s = mc.meterInMercatorCoordinateUnits();
  const ll = new maplibregl.MercatorCoordinate(mc.x + x * s, mc.y + z * s, 0).toLngLat();
  return [ll.lng, ll.lat];
}

function crearEtiquetas() {
  const cont = $('etiquetas');
  if (!cont || etiquetas.length) return;
  for (const e of datos.estaciones) {
    const el = document.createElement('div');
    el.className = 'etq-est';
    el.textContent = e.nombre;
    cont.appendChild(el);
    etiquetas.push({ el, lonlat: lonlatDe(e.xz[0], e.xz[1]) });
  }
  mapa.on('move', pintarEtiquetas);
  mapa.on('idle', pintarEtiquetas);
  pintarEtiquetas();
}

function pintarEtiquetas() {
  if (!estado.activo || !estado.etiquetas) return;
  if (mapa.getZoom() < 14.2) { for (const e of etiquetas) e.el.style.display = 'none'; return; }
  const W = mapa.getCanvas().clientWidth, H = mapa.getCanvas().clientHeight;
  for (const e of etiquetas) {
    const p = mapa.project(e.lonlat);
    const dentro = p.x > -80 && p.x < W + 80 && p.y > -30 && p.y < H + 30;
    e.el.style.display = dentro ? 'block' : 'none';
    if (dentro) e.el.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px)`;
  }
}

/* ------------------------------------------------ arranque ---------- */
async function arrancar() {
  try {
    await cargar();
    crearEtiquetas();
    const c = $('metro-cifras');
    if (c) c.innerHTML = `<b>${meta.estaciones}</b> estaciones · <b>${meta.accesos}</b> accesos · ` +
      `<b>${meta.tramos}</b> tramos · ${meta.lineas.length} líneas`;
    const chips = $('chips-linea');
    if (chips && !chips.childElementCount) {
      for (const l of [{ ref: null, color: '#8ea0b5' }, ...datos.lineas]) {
        const b = document.createElement('button');
        b.className = 'chip-l';
        b.dataset.ref = l.ref || '';
        b.textContent = l.ref ? 'L' + l.ref : 'Todas';
        b.style.borderColor = l.color + '66';
        if (l.ref) b.style.setProperty('--c', l.color);
        b.addEventListener('click', () => elegirLinea(l.ref));
        chips.appendChild(b);
      }
      chips.querySelector('.chip-l').classList.add('is-on');
    }
    asegurarCapa();
    mapa.on('style.load', () => { capaLista = false; asegurarCapa(); });
    mapa.on('load', asegurarCapa);
    mapa.on('idle', asegurarCapa);
    const r = setInterval(() => { asegurarCapa(); if (mapa.getLayer('metro3d')) clearInterval(r); }, 400);
    if ($('chk-metro')) $('chk-metro').disabled = false;
    window.METRO = {
      estado, meta, datos,
      entrar: () => { if ($('chk-metro')) $('chk-metro').checked = true; entrar(); },
      salir: () => { if ($('chk-metro')) $('chk-metro').checked = false; salir(); },
      volar,
      info: () => ({
        activo: estado.activo, capa: !!mapa.getLayer('metro3d'), linea: estado.linea,
        tramos: datos.rutas.length, estaciones: cab.header.estaciones.length,
        accesos: grupoAccesos ? grupoAccesos.userData.total : 0,
        tris: renderer ? renderer.info.render.triangles : 0,
        llamadas: renderer ? renderer.info.render.calls : 0, terreno: !!mapa.getTerrain(),
      }),
      accesosDe: (nombre) => {
        const e = datos.estaciones.find((x) => x.nombre.toUpperCase().includes(nombre.toUpperCase()));
        return e ? e.accesos : null;
      },
    };
  } catch (e) {
    console.error('metro:', e);
    const n = $('metro-nota');
    if (n) { n.textContent = 'No se pudo cargar la red: ' + e.message; n.classList.add('error'); }
  }
}

function elegirLinea(ref) {
  estado.linea = ref;
  document.querySelectorAll('.chip-l').forEach((b) => b.classList.toggle('is-on', (b.dataset.ref || null) === ref));
  aplicarVisibilidad();
}

/* ------------------------------------------------ controles ---------- */
function conectar() {
  const chk = $('chk-metro');
  if (chk) chk.addEventListener('change', () => { chk.checked ? entrar() : salir(); });
  const re = $('r-edif');
  if (re) re.addEventListener('input', () => {
    estado.edificios = Number(re.value) / 100;
    $('v-edif').textContent = Math.round(estado.edificios * 100) + ' %';
    if (estado.activo && mapa.getLayer('edificios-3d')) {
      mapa.setPaintProperty('edificios-3d', 'fill-extrusion-opacity', estado.edificios);
    }
  });
  for (const [id, clave] of [['chk-est', 'estaciones'], ['chk-tun', 'tuneles'],
                             ['chk-acc', 'accesos'], ['chk-etq', 'etiquetas']]) {
    const el = $(id);
    if (el) el.addEventListener('change', () => { estado[clave] = el.checked; aplicarVisibilidad(); pintarEtiquetas(); });
  }
  for (const [id, v] of [['b-red', 'red'], ['b-nm', 'nm'], ['b-tribunal', 'tribunal'], ['b-castilla', 'castilla']]) {
    const el = $(id);
    if (el) el.addEventListener('click', () => volar(v));
  }
}

conectar();
if (window.GDM && window.GDM.mapa) arrancar();
else {
  const t = setInterval(() => { if (window.GDM && window.GDM.mapa) { clearInterval(t); arrancar(); } }, 200);
}

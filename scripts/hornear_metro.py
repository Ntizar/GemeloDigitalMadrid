"""Hornea UNA línea de metro para el Gemelo Digital Madrid.

Fuentes (todas oficiales o ya resueltas en `metro-gemelo`):
  - GTFS del CRTM (`datos/gtfs/metro.zip`): shapes.txt y stops.txt -> traza y anclaje
  - modelos de estación ya fusionados (`datos/estaciones.bin`, MMG1)
  - corteza DEM terrarium (`datos/terreno.png`) y ortofoto PNOA (`datos/ortofoto.jpg`)

AVISO IMPORTANTE (medido, no supuesto): las coordenadas del catálogo
`metro-gemelo/datos/red.json` están desplazadas ~1,5 km al suroeste respecto al
GTFS oficial y tienen ±200 m de dispersión propia. Por eso este horneador NO usa
sus x/z para georreferenciar: la traza se reconstruye del shapes.txt y cada
estación se ancla a su parada de stops.txt por nombre. Los andenes y los modelos
del catálogo se trasladan con el delta de SU estación, así conservan su
geometría interna.

Marco local (el mismo del gemelo):
    x = E - 440.291,28      z = -(N - 4.474.254,60)      y = cota relativa a Sol

Salidas en datos/l<n>/:
  red.json        traza oficial simplificada + estaciones con andenes
  estaciones.bin  modelos MMG1 de la línea, anclados a su parada
  terreno.bin     cotas de la corteza decimadas (float32)
  terreno.jpg     ortofoto recortada
  meta.json       cifras y errores medidos

Uso:
  python scripts/hornear_metro.py 10
  METRO_GEMELO=/ruta/a/metro-gemelo python scripts/hornear_metro.py 10
"""

import csv
import io
import json
import math
import os
import struct
import sys
import unicodedata
import zipfile

import numpy as np
from PIL import Image

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUENTE = os.environ.get("METRO_GEMELO", os.path.join(os.path.dirname(RAIZ), "metro-gemelo"))
DATOS_F = os.path.join(FUENTE, "datos")

EREF, NREF = 440291.28, 4474254.60
A = 6378137.0
F = 1.0 / 298.257223563
E2 = F * (2.0 - F)
EP2 = E2 / (1.0 - E2)
K0 = 0.9996
LON0 = math.radians(-3.0)

COLORES = {
    "1": "#2DBEF0", "2": "#ED1C24", "3": "#FFD000", "4": "#B65518", "5": "#8FD400",
    "6": "#A0A5AB", "7": "#EE7518", "8": "#EC82B1", "9": "#A60084", "10": "#4A9FE6",
    "11": "#009B3A", "12": "#A49800", "R": "#8AB4E8",
}

MARGEN_CAJA = 2500.0
PASO_MALLA = 130.0
TOLERANCIA_SIMPLIFICAR = 4.0     # metros


# ------------------------------------------------------------------ geodesia
def latlon_a_utm(lat, lon):
    phi, lam = math.radians(lat), math.radians(lon)
    N = A / math.sqrt(1.0 - E2 * math.sin(phi) ** 2)
    T = math.tan(phi) ** 2
    C = EP2 * math.cos(phi) ** 2
    Aa = math.cos(phi) * (lam - LON0)
    M = A * ((1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256) * phi
             - (3 * E2 / 8 + 3 * E2 ** 2 / 32 + 45 * E2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * E2 ** 2 / 256 + 45 * E2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * E2 ** 3 / 3072) * math.sin(6 * phi))
    E = K0 * N * (Aa + (1 - T + C) * Aa ** 3 / 6
                  + (5 - 18 * T + T ** 2 + 72 * C - 58 * EP2) * Aa ** 5 / 120) + 500000.0
    Nn = K0 * (M + N * math.tan(phi) * (Aa ** 2 / 2
                + (5 - T + 9 * C + 4 * C ** 2) * Aa ** 4 / 24
                + (61 - 58 * T + T ** 2 + 600 * C - 330 * EP2) * Aa ** 6 / 720))
    return E, Nn


def a_local(lat, lon):
    E, N = latlon_a_utm(lat, lon)
    return E - EREF, -(N - NREF)


def norm(s):
    s = unicodedata.normalize("NFD", s.upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def douglas_peucker(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0], dtype=float), np.array(pts[-1], dtype=float)
    ab = b - a
    n = np.linalg.norm(ab)
    p = np.array(pts, dtype=float)
    if n < 1e-9:
        d = np.linalg.norm(p - a, axis=1)
    else:
        v = p - a
        d = np.abs(ab[0] * v[:, 1] - ab[1] * v[:, 0]) / n
    i = int(np.argmax(d))
    if d[i] > tol:
        return douglas_peucker(pts[:i + 1], tol)[:-1] + douglas_peucker(pts[i:], tol)
    return [pts[0], pts[-1]]


# ------------------------------------------------------------------ MMG1
def leer_mmg(ruta):
    b = open(ruta, "rb").read()
    if b[:4] != b"MMG1":
        raise SystemExit("binario no reconocido: " + ruta)
    hlen = struct.unpack("<I", b[4:8])[0]
    cab = json.loads(b[8:8 + hlen].decode("utf-8"))
    off = 8 + hlen
    V, T = cab["vertices"], cab["triangulos"]
    pos = np.frombuffer(b, dtype=np.float32, count=V * 3, offset=off).reshape(-1, 3).copy()
    off += V * 12
    col = np.frombuffer(b, dtype=np.uint8, count=V * 3, offset=off).reshape(-1, 3).copy()
    off += V * 3
    idx = np.frombuffer(b, dtype=np.uint32, count=T * 3, offset=off).copy()
    return cab, pos, col, idx


def escribir_mmg(ruta, cab, pos, col, idx):
    head = json.dumps(cab, ensure_ascii=False).encode("utf-8")
    with open(ruta, "wb") as f:
        f.write(b"MMG1")
        f.write(struct.pack("<I", len(head)))
        f.write(head)
        f.write(pos.astype(np.float32).tobytes())
        f.write(col.astype(np.uint8).tobytes())
        f.write(idx.astype(np.uint32).tobytes())


def leer_cotas_png(ruta):
    a = np.asarray(Image.open(ruta).convert("RGB")).astype(np.uint16)
    v = (a[:, :, 0].astype(np.uint16) << 8) + a[:, :, 1]
    return (v.astype(np.float64) / 10.0) - 1200.0


# ------------------------------------------------------------------ GTFS
def leer_gtfs(ruta):
    z = zipfile.ZipFile(ruta)

    def filas(nombre):
        with z.open(nombre) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))

    rutas = {r["route_id"]: r for r in filas("routes.txt")}
    viajes = filas("trips.txt")
    shapes = {}
    with z.open("shapes.txt") as f:
        for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            shapes.setdefault(r["shape_id"], []).append(
                (int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
    for v in shapes.values():
        v.sort()
    return rutas, viajes, shapes, filas("stops.txt")


def main(linea):
    salida = os.path.join(RAIZ, "datos", "l" + linea if linea != "R" else "ramal")
    os.makedirs(salida, exist_ok=True)

    rutas_g, viajes, shapes, stops = leer_gtfs(os.path.join(DATOS_F, "gtfs", "metro.zip"))
    ids_ruta = [k for k, v in rutas_g.items() if v.get("route_short_name") == linea]
    if not ids_ruta:
        raise SystemExit(f"línea {linea} no encontrada en routes.txt")
    shapes_linea = {t["shape_id"] for t in viajes if t["route_id"] in ids_ruta}
    color = "#" + (rutas_g[ids_ruta[0]].get("route_color") or "888888")

    # ---- traza: una polilínea por shape, en metros locales y simplificada ----
    rutas = []
    for sid in sorted(shapes_linea):
        pts = [a_local(lat, lon) for (_, lat, lon) in shapes[sid]]
        simple = douglas_peucker(pts, TOLERANCIA_SIMPLIFICAR)
        rutas.append({"l": linea, "shape": sid, "d": sid,
                      "p": [[round(x, 1), 0.0, round(z, 1)] for (x, z) in simple]})

    # ---- estaciones del catálogo: nombre -> parada oficial ----
    red = json.load(open(os.path.join(DATOS_F, "red.json"), encoding="utf-8"))
    estaciones = [e for e in red["estaciones"] if linea in (e.get("l") or [])]
    por_nombre = {}
    for s in stops:
        por_nombre.setdefault(norm(s["stop_name"]), []).append(s)

    sin_par = []
    for e in estaciones:
        cand = por_nombre.get(norm(e["nombre"]))
        if not cand:
            sin_par.append(e["nombre"])
            e["_delta"] = (0.0, 0.0)
            continue
        mejor = min(cand, key=lambda s: (a_local(float(s["stop_lat"]), float(s["stop_lon"]))[0] - e["xz"][0]) ** 2
                                       + (a_local(float(s["stop_lat"]), float(s["stop_lon"]))[1] - e["xz"][1]) ** 2)
        ax, az = a_local(float(mejor["stop_lat"]), float(mejor["stop_lon"]))
        e["_delta"] = (ax - e["xz"][0], az - e["xz"][1])
        e["xz"] = [round(ax, 2), round(az, 2)]
        e["stop"] = mejor["stop_id"]

    print(f"estaciones: {len(estaciones)}  sin parada oficial: {sin_par}")

    # ---- corteza ----
    tc = json.load(open(os.path.join(DATOS_F, "terreno.json"), encoding="utf-8"))
    cotas = leer_cotas_png(os.path.join(DATOS_F, "terreno.png"))
    nx_t, nz_t = tc["nx"], tc["nz"]

    def cota_vec(xyz):
        xyz = np.asarray(xyz, dtype=np.float64)
        out = xyz.copy()
        if len(xyz) == 0:
            return out.astype(np.float32)
        fx = np.clip((xyz[:, 0] - tc["x0"]) / tc["ancho"], 0, 1) * (nx_t - 1)
        fz = np.clip((xyz[:, 2] - tc["z0"]) / tc["alto"], 0, 1) * (nz_t - 1)
        i0 = np.clip(fx.astype(int), 0, nx_t - 2)
        j0 = np.clip(fz.astype(int), 0, nz_t - 2)
        dx, dz = fx - i0, fz - j0
        a, b = cotas[j0, i0], cotas[j0, i0 + 1]
        c, d = cotas[j0 + 1, i0], cotas[j0 + 1, i0 + 1]
        out[:, 1] += (a * (1 - dx) + b * dx) * (1 - dz) + (c * (1 - dx) + d * dx) * dz
        return out.astype(np.float32)

    # ---- modelos: subconjunto + traslado al ancla de SU estación ----
    cab, pos, col, idx = leer_mmg(os.path.join(DATOS_F, "estaciones.bin"))
    por_code = {e["code"]: e for e in estaciones}
    partes_p, partes_c, partes_i, nuevas = [], [], [], []
    vo = io_ = 0
    for m in cab["estaciones"]:
        e = por_code.get(m["code"])
        if not e:
            continue
        dx, dz = e.get("_delta", (0.0, 0.0))
        p = pos[m["v0"]:m["v0"] + m["vn"]].astype(np.float64)
        p[:, 0] += dx
        p[:, 2] += dz
        p = cota_vec(p) if len(p) else p
        partes_p.append(p.astype(np.float32))
        partes_c.append(col[m["v0"]:m["v0"] + m["vn"]])
        partes_i.append(idx[m["i0"]:m["i0"] + m["in"]] - m["v0"] + vo)
        m2 = dict(m)
        m2["v0"], m2["i0"] = vo, io_
        cota_centro = float(cota_vec(np.array([[m["c"][0] + dx, 0.0, m["c"][2] + dz]]))[0][1])
        m2["c"] = [round(float(m["c"][0] + dx), 2), round(cota_centro, 2), round(float(m["c"][2] + dz), 2)]
        m2["pozo"] = [round(float(m["pozo"][0] + dx), 2), round(float(m["pozo"][1] + dz), 2)]
        m2["pozo_y"] = round(float(m["pozo_y"] + cota_centro), 2)
        nuevas.append(m2)
        vo += m["vn"]
        io_ += m["in"]

    pos2 = np.concatenate(partes_p) if partes_p else np.zeros((0, 3), np.float32)
    col2 = np.concatenate(partes_c) if partes_c else np.zeros((0, 3), np.uint8)
    idx2 = np.concatenate(partes_i) if partes_i else np.zeros(0, np.uint32)

    # ---- andenes: trasladados con el delta de su estación + cota del DEM ----
    for e in estaciones:
        dx, dz = e.get("_delta", (0.0, 0.0))
        cota_est = float(cota_vec(np.array([[e["xz"][0], 0.0, e["xz"][1]]]))[0][1])
        nuevos = []
        for a in e["andenes"]:
            if a.get("l") != linea:
                continue
            path = np.asarray(a["path"], dtype=np.float64)
            path[:, 0] += dx
            path[:, 2] += dz
            path = cota_vec(path)
            a["path"] = path.round(2).tolist()
            a["y"] = round(float(a["y"] + cota_est), 2)
            nuevos.append(a)
        e["andenes"] = nuevos
        e.pop("_delta", None)

    # ---- error de anclaje medido: cada estación contra la traza oficial ----
    def dist_a_traza(x, z):
        mejor = 1e9
        q = np.array([x, z])
        for r in rutas:
            p = np.asarray([[t[0], t[2]] for t in r["p"]])
            a = p[:-1]
            ab = p[1:] - a
            largo2 = (ab ** 2).sum(1)
            largo2[largo2 == 0] = 1e-9
            t = np.clip(((q - a) * ab).sum(1) / largo2, 0, 1)
            proj = a + ab * t[:, None]
            mejor = min(mejor, float(np.linalg.norm(proj - q, axis=1).min()))
        return mejor

    errores = [dist_a_traza(e["xz"][0], e["xz"][1]) for e in estaciones if "stop" in e]

    # ---- caja y corteza ----
    xs = [p[0] for r in rutas for p in r["p"]]
    zs = [p[2] for r in rutas for p in r["p"]]
    caja = {"x0": min(xs) - MARGEN_CAJA, "x1": max(xs) + MARGEN_CAJA,
            "z0": min(zs) - MARGEN_CAJA, "z1": max(zs) + MARGEN_CAJA}
    nx = max(2, int(round((caja["x1"] - caja["x0"]) / PASO_MALLA)) + 1)
    nz = max(2, int(round((caja["z1"] - caja["z0"]) / PASO_MALLA)) + 1)
    xs_m = np.linspace(caja["x0"], caja["x1"], nx)
    zs_m = np.linspace(caja["z0"], caja["z1"], nz)
    fx = np.clip((xs_m - tc["x0"]) / tc["ancho"], 0, 1) * (nx_t - 1)
    fz = np.clip((zs_m - tc["z0"]) / tc["alto"], 0, 1) * (nz_t - 1)
    malla = cotas[np.ix_(np.clip(np.round(fz).astype(int), 0, nz_t - 1),
                         np.clip(np.round(fx).astype(int), 0, nx_t - 1))].astype(np.float32)
    open(os.path.join(salida, "terreno.bin"), "wb").write(malla.tobytes())
    meta_terreno = {"nx": nx, "nz": nz, "x0": caja["x0"], "x1": caja["x1"],
                    "z0": caja["z0"], "z1": caja["z1"], "h_ref": tc["h_ref"],
                    "cota_min": float(malla.min()), "cota_max": float(malla.max()),
                    "paso_m": round((caja["x1"] - caja["x0"]) / (nx - 1), 1),
                    "fuente": tc["fuente_dem"]}

    # ---- traza con cota del DEM ----
    for r in rutas:
        r["p"] = cota_vec(np.asarray(r["p"], dtype=np.float64)).round(1).tolist()

    # ---- perfil: el túnel va BAJO la calle, interpolando la profundidad de las
    #      dos estaciones más cercanas (el GTFS no trae cota de vía) ----
    anclas = []
    for e in estaciones:
        if not e.get("andenes"):
            continue
        calle = float(cota_vec(np.array([[e["xz"][0], 0.0, e["xz"][1]]]))[0][1])
        prof = min(a["y"] for a in e["andenes"]) - calle
        anclas.append((e["xz"][0], e["xz"][1], prof, e["nombre"]))
    for r in rutas:
        for p in r["p"]:
            d = sorted(((math.hypot(p[0] - a[0], p[2] - a[1]), a[2]) for a in anclas),
                       key=lambda t: t[0])[:2]
            if not d:
                p[1] = -18.0
                continue
            w = [(1.0 / (t[0] + 40.0), t[1]) for t in d]
            tot = sum(x[0] for x in w)
            prof = sum(x[0] * x[1] for x in w) / tot
            calle = float(cota_vec(np.array([[p[0], 0.0, p[2]]]))[0][1])
            p[1] = round(calle + prof - 3.5, 1)      # 3,5 m bajo el andén

    long_km = sum(
        sum(math.dist((r["p"][i][0], r["p"][i][2]), (r["p"][i - 1][0], r["p"][i - 1][2]))
            for i in range(1, len(r["p"]))) for r in rutas) / 2 / 1000

    sal = {"linea": linea, "color": color, "rutas": rutas, "estaciones": estaciones,
           "caja": caja, "datum": "GTFS_CRTM_OFICIAL", "relieve": True,
           "anclaje": "parada oficial de stops.txt por nombre"}
    json.dump(sal, open(os.path.join(salida, "red.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    cab2 = {"origen": cab["origen"], "datum": cab["datum"], "estaciones": nuevas,
            "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3),
            "linea": linea, "fuente": "metro-gemelo/datos/estaciones.bin + ancla GTFS"}
    escribir_mmg(os.path.join(salida, "estaciones.bin"), cab2, pos2, col2, idx2)

    json.dump(meta_terreno, open(os.path.join(salida, "terreno.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # ---- ortofoto recortada ----
    orto = Image.open(os.path.join(DATOS_F, "ortofoto.jpg")).convert("RGB")
    W, H = orto.size
    px0 = int((caja["x0"] - tc["x0"]) / tc["ancho"] * W)
    px1 = int((caja["x1"] - tc["x0"]) / tc["ancho"] * W)
    pz0 = int((caja["z0"] - tc["z0"]) / tc["alto"] * H)
    pz1 = int((caja["z1"] - tc["z0"]) / tc["alto"] * H)
    recorte = orto.crop((max(0, px0), max(0, pz0), min(W, px1), min(H, pz1)))
    recorte = recorte.resize((2048, int(recorte.height * 2048 / recorte.width)), Image.LANCZOS)
    recorte.save(os.path.join(salida, "terreno.jpg"), quality=82, optimize=True)

    meta = {
        "linea": linea, "color": color,
        "estaciones": len(estaciones), "estaciones_con_modelo": len(nuevas),
        "andenes": sum(len(e["andenes"]) for e in estaciones),
        "rutas": len(rutas), "puntos_traza": sum(len(r["p"]) for r in rutas),
        "longitud_km": round(long_km, 2),
        "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3),
        "terreno": meta_terreno, "relieve": True,
        "datum": "GTFS CRTM oficial (shapes/stops)",
        "anclaje_error_m": {"mediana": round(float(np.median(errores)), 1),
                            "p90": round(float(np.percentile(errores, 90)), 1),
                            "max": round(float(max(errores)), 1)},
        "sin_parada_oficial": sin_par,
        "cota_anden_m": [round(min(a["y"] for e in estaciones for a in e["andenes"]), 1),
                         round(max(a["y"] for e in estaciones for a in e["andenes"]), 1)]
        if estaciones else None,
        "fuente": "CRTM GTFS + modelos 20-nov-2026 (croquis) + DEM terrarium z13 + PNOA",
    }
    json.dump(meta, open(os.path.join(salida, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    for f in ("red.json", "estaciones.bin", "terreno.bin", "terreno.jpg", "meta.json"):
        print(f"  {f:16s} {os.path.getsize(os.path.join(salida, f))/1024:8.1f} KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "10")

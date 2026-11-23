"""Hornea TODA la red de metro (V19 + accesos del GTFS) para el Gemelo Digital.

Fuentes:
  - catalogo V19 (poses por modelo, estaciones, 560 tramos) — marco origen [440979.959, 4475676]
  - GTFS CRTM: stops con location_type=2 -> 520 ACCESOS (bocas, ascensores, intercambiador)
  - GLB originales de metro-gemelo

Salida:
  datos/red.json        lineas + tramos + estaciones con sus accesos
  datos/estaciones.bin  los 240 modelos con su pose V19
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

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUENTE = os.environ.get("METRO_GEMELO", os.path.join(os.path.dirname(RAIZ), "metro-gemelo"))
DATOS_F = os.path.join(FUENTE, "datos")
V19 = os.environ.get("METRO_V19", os.path.join(DATOS_F, "catalogo_v19.json"))

EREF, NREF = 440291.28, 4474254.60
A = 6378137.0
F = 1.0 / 298.257223563
E2 = F * (2.0 - F)
EP2 = E2 / (1.0 - E2)
K0 = 0.9996
LON0 = math.radians(-3.0)
sys.path.insert(0, os.path.join(FUENTE, "scripts"))

COLORES = {"1": "#2DBEF0", "2": "#ED1C24", "3": "#FFD000", "4": "#B65518", "5": "#8FD400",
           "6": "#A0A5AB", "7": "#EE7518", "8": "#EC82B1", "9": "#A60084", "10": "#4A9FE6",
           "11": "#009B3A", "12": "#A49800", "R": "#8AB4E8"}


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


def norm(s):
    s = unicodedata.normalize("NFD", s.upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def escribir_mmg(ruta, cab, pos, col, idx):
    head = json.dumps(cab, ensure_ascii=False).encode("utf-8")
    with open(ruta, "wb") as f:
        f.write(b"MMG1")
        f.write(struct.pack("<I", len(head)))
        f.write(head)
        f.write(pos.astype(np.float32).tobytes())
        f.write(col.astype(np.uint8).tobytes())
        f.write(idx.astype(np.uint32).tobytes())


def main():
    D = json.load(open(V19, encoding="utf-8"))
    OX, OY = D["origin_epsg25830"]
    dx0, dz0 = OX - EREF, -(OY - NREF)

    def al_gemelo(x, z):
        return float(x + dx0), float(z + dz0)

    # ---- accesos del GTFS (bocas, ascensores, intercambiadores) ----
    z = zipfile.ZipFile(os.path.join(DATOS_F, "gtfs", "metro.zip"))
    with z.open("stops.txt") as f:
        stops = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    nombre_padre = {s["stop_id"]: s["stop_name"] for s in stops if s.get("location_type") == "1"}
    accesos = {}
    for s in stops:
        if s.get("location_type") != "2":
            continue
        padre = nombre_padre.get(s.get("parent_station"))
        if not padre:
            continue
        E, N = latlon_a_utm(float(s["stop_lat"]), float(s["stop_lon"]))
        accesos.setdefault(norm(padre), []).append(
            {"nombre": s["stop_name"], "xz": [round(E - EREF, 2), round(-(N - NREF), 2)],
             "id": s["stop_id"]})
    print(f"accesos GTFS: {sum(len(v) for v in accesos.values())} en {len(accesos)} estaciones")

    # ---- estaciones y tramos del V19 ----
    est = []
    for s in D["stations"]:
        mx, mz = al_gemelo(s["cartographic_xz"][0], s["cartographic_xz"][1])
        est.append({"id": s["id"], "nombre": s["name"], "modelo": s["model_id"],
                    "l": [str(x) for x in s.get("lines", [])], "xz": [round(mx, 2), round(mz, 2)],
                    "accesos": accesos.get(norm(s["name"]), [])})

    rutas = []
    for r in D["routes"]:
        pts = [al_gemelo(p[0], p[2]) for p in r["points"]]
        if len(pts) < 2:
            continue
        rutas.append({"l": str(r.get("line")), "p": [[round(x, 1), round(p[1], 1), round(z_, 1)]
                                                    for (x, z_), p in zip(pts, r["points"])]})
    lineas = sorted({r["l"] for r in rutas}, key=lambda v: (len(v), v))
    print(f"estaciones: {len(est)}  tramos: {len(rutas)}  lineas: {lineas}")

    # ---- perfil de vía: profundidad interpolada entre estaciones de cada tramo ----
    def prof_en(x, zz):
        d = sorted(((math.hypot(x - e["xz"][0], zz - e["xz"][1]), -18.0) for e in est),
                   key=lambda t: t[0])[:2]
        if not d:
            return -18.0
        w = [(1.0 / (t[0] + 40.0), t[1]) for t in d]
        return sum(a * b for a, b in w) / sum(a for a, _ in w)

    for r in rutas:
        for p in r["p"]:
            p[1] = round(prof_en(p[0], p[2]) - 3.5, 1)

    # ---- modelos con su pose ----
    from hornear import glb_a_triangulos
    modelos = {m["id"]: m for m in D["models"]}
    by_model = {}
    for e in est:
        by_model.setdefault(e["modelo"], []).append(e)
    partes_p, partes_c, partes_i, cab_est = [], [], [], []
    vo = io_ = 0
    sin_glb = []
    for mid, m in modelos.items():
        ruta_glb = os.path.join(DATOS_F, "estaciones", mid + ".glb")
        if not os.path.exists(ruta_glb):
            sin_glb.append(mid)
            continue
        pzs = glb_a_triangulos(ruta_glb)
        pp, cc, ii, off = [], [], [], 0
        for (pz, iz, cz, _n) in pzs:
            pp.append(pz); ii.append(np.asarray(iz).ravel() + off); off += len(pz)
            cc.append(np.tile(np.asarray(cz, dtype=float), (len(pz), 1)))
        if not pp:
            continue
        pos = np.vstack(pp); colr = np.vstack(cc); idx = np.concatenate(ii)
        M = np.array(m["matrix_xz"], dtype=np.float64)
        t = np.array(m["translation_xz"], dtype=np.float64)
        esc = np.array(m.get("scale_xyz", [1, 1, 1]), dtype=np.float64)
        Q = pos.astype(np.float64) * esc
        x = Q[:, 0] * M[0, 0] + Q[:, 2] * M[0, 1] + t[0] + dx0
        zz = Q[:, 0] * M[1, 0] + Q[:, 2] * M[1, 1] + t[1] + dz0
        y = Q[:, 1] + float(m.get("translation_y", 0.0))
        P = np.stack([x, y, zz], 1).astype(np.float32)
        partes_p.append(P)
        partes_c.append((np.clip(colr, 0, 1) * 255).astype(np.uint8))
        partes_i.append(np.asarray(idx, dtype=np.uint32) + vo)
        e0 = (by_model.get(mid) or [{"xz": [0, 0], "nombre": mid}])[0]
        cab_est.append({"modelo": mid, "nombre": e0["nombre"], "v0": vo, "vn": len(P),
                        "i0": io_, "in": int(np.asarray(idx).size),
                        "c": [e0["xz"][0], round(float(np.median(y)), 2), e0["xz"][1]],
                        "pozo": e0["xz"], "pozo_y": round(float(np.percentile(y, 2)), 2)})
        vo += len(P)
        io_ += int(np.asarray(idx).size)
    pos2 = np.concatenate(partes_p)
    col2 = np.concatenate(partes_c)
    idx2 = np.concatenate(partes_i)
    print(f"modelos horneados: {len(cab_est)}  vértices: {len(pos2)}  sin GLB: {len(sin_glb)}")

    salida = os.path.join(RAIZ, "datos")
    os.makedirs(salida, exist_ok=True)
    json.dump({"lineas": [{"ref": l, "color": COLORES.get(l, "#8a92a0")} for l in lineas],
               "rutas": rutas, "estaciones": est,
               "origen_v19": [OX, OY],
               "fuente": "catalogo V19 (poses) + GTFS CRTM (accesos)"},
              open(os.path.join(salida, "red.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    escribir_mmg(os.path.join(salida, "estaciones.bin"),
                 {"origen": [EREF, NREF], "datum": "V19_POSES", "estaciones": cab_est,
                  "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3)},
                 pos2, col2, idx2)

    n_acc = sum(len(e["accesos"]) for e in est)
    meta = {"estaciones": len(est), "modelos": len(cab_est), "tramos": len(rutas),
            "lineas": lineas, "accesos": n_acc,
            "estaciones_con_acceso": sum(1 for e in est if e["accesos"]),
            "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3),
            "bytes": {"red": os.path.getsize(os.path.join(salida, "red.json")),
                      "bin": os.path.getsize(os.path.join(salida, "estaciones.bin"))}}
    json.dump(meta, open(os.path.join(salida, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

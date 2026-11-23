"""Hornea UNA línea usando el catálogo V19 con POSES (fuente: explorar.html).

El catálogo V19 trae, por modelo, una pose rígida ya ajustada contra la trama
cartográfica: matrix_xz (2x2), translation_xz, translation_y, scale_xyz. Y por
estación, su `cartographic_xz`. Todo en SU marco, cuyo origen es
`origin_epsg25830 = [440979.959, 4475676]` — NO la Puerta del Sol.

Eso explica el famoso "desplazamiento": las coordenadas de metro-gemelo estaban
en ese marco y se leían como si fueran relativas a Sol (689 m al este y 1.421 m
al norte de diferencia).

Aquí se aplica la pose del V19 a cada GLB original y se pasa todo al marco del
gemelo (origen Sol), de modo que croquis, andenes y traza salen de la MISMA
transformación y no pueden discrepar.

Salidas en datos/l<n>/: red.json, estaciones.bin, terreno.bin, terreno.json,
terreno.jpg, meta.json.
"""

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
V19 = os.environ.get("METRO_V19", os.path.join(DATOS_F, "catalogo_v19.json"))

EREF, NREF = 440291.28, 4474254.60
MARGEN_CAJA = 2200.0
PASO_MALLA = 130.0
TOL = 1.5
PROF_TUNEL = 3.5

sys.path.insert(0, os.path.join(FUENTE, "scripts"))


def leer_mmg(ruta):
    b = open(ruta, "rb").read()
    hlen = struct.unpack("<I", b[4:8])[0]
    cab = json.loads(b[8:8 + hlen].decode("utf-8"))
    return cab


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


def douglas_peucker(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0], float), np.array(pts[-1], float)
    ab = b - a
    n = np.linalg.norm(ab)
    p = np.array(pts, float)
    v = p - a
    d = (np.linalg.norm(v, axis=1) if n < 1e-9
         else np.abs(ab[0] * v[:, 1] - ab[1] * v[:, 0]) / n)
    i = int(np.argmax(d))
    if d[i] > tol:
        return douglas_peucker(pts[:i + 1], tol)[:-1] + douglas_peucker(pts[i:], tol)
    return [pts[0], pts[-1]]


def main(linea):
    salida = os.path.join(RAIZ, "datos", "l" + linea if linea != "R" else "ramal")
    os.makedirs(salida, exist_ok=True)

    D = json.load(open(V19, encoding="utf-8"))
    OX, OY = D["origin_epsg25830"]
    dx0 = OX - EREF                 # paso del marco V19 al marco del gemelo
    dz0 = -(OY - NREF)

    def al_gemelo(x, z):
        return float(x + dx0), float(z + dz0)

    estaciones = [s for s in D["stations"] if linea in s.get("lines", [])]
    modelos = {m["id"]: m for m in D["models"]}
    rutas_v19 = [r for r in D["routes"] if str(r.get("line")) == str(linea)]

    # ---- traza: los tramos del catálogo (mismo marco que las poses) ----
    # No se encadena en una sola polilínea: el V19 trae 60 tramos dirigidos de
    # ~460 m (uno por par de puertos). Dibujarlos tal cual reproduce la línea
    # completa sin inventar empalmes y sin cruces.
    tramos = []
    for r in rutas_v19:
        pts = [al_gemelo(p[0], p[2]) for p in r["points"]]
        if len(pts) >= 2:
            tramos.append({"id": r["id"], "pts": pts})
    print(f"trazos: {len(tramos)} tramos, {sum(len(t['pts']) for t in tramos)} puntos")
    traza = np.asarray([q for t in tramos for q in t["pts"]], dtype=float)

    def dist_linea(x, z):
        a1 = traza[:-1]
        ab1 = traza[1:] - a1
        l21 = (ab1 ** 2).sum(1)
        l21[l21 == 0] = 1e-9
        t = np.clip(((np.array([x, z]) - a1) * ab1).sum(1) / l21, 0, 1)
        proj = a1 + ab1 * t[:, None]
        return float(np.linalg.norm(proj - np.array([x, z]), axis=1).min())

    # ---- estaciones + modelos con SU pose ----
    from hornear import glb_a_triangulos      # parser GLB del repo de metro-gemelo

    partes_p, partes_c, partes_i, nuevas = [], [], [], []
    vo = io_ = 0
    resid_centro, resid_and, sin_glb = [], [], []
    pos_est = {}
    for s in estaciones:
        mx, mz = al_gemelo(s["cartographic_xz"][0], s["cartographic_xz"][1])
        pos_est[s["id"]] = (mx, mz)
        m = modelos.get(s["model_id"])
        if not m:
            continue
        ruta_glb = os.path.join(DATOS_F, "estaciones", m["id"] + ".glb")
        if not os.path.exists(ruta_glb):
            sin_glb.append(m["id"])
            continue
        pzs = glb_a_triangulos(ruta_glb)
        pp, cc, ii, off = [], [], [], 0
        for (pz, iz, cz, _n) in pzs:
            pp.append(pz); ii.append(np.asarray(iz).ravel() + off); off += len(pz)
            cc.append(np.tile(np.asarray(cz, dtype=float), (len(pz), 1)))
        pos = np.vstack(pp); colr = np.vstack(cc); idx = np.concatenate(ii)
        P = np.asarray(pos, dtype=np.float64).copy()
        M = np.array(m["matrix_xz"], dtype=np.float64)
        t = np.array(m["translation_xz"], dtype=np.float64)
        esc = np.array(m.get("scale_xyz", [1, 1, 1]), dtype=np.float64)
        P *= esc
        x = P[:, 0] * M[0, 0] + P[:, 2] * M[0, 1] + t[0]
        z = P[:, 0] * M[1, 0] + P[:, 2] * M[1, 1] + t[1]
        y = P[:, 1] + float(m.get("translation_y", 0.0))
        Q = np.stack([x, y, z], 1)
        Q[:, 0] += dx0
        Q[:, 2] += dz0

        partes_p.append(Q.astype(np.float32))
        c8 = (np.clip(np.asarray(colr, dtype=np.float64), 0, 1) * 255).astype(np.uint8)
        partes_c.append(c8)
        partes_i.append(np.asarray(idx, dtype=np.uint32).ravel() + vo)
        cx, cz = pos_est[s["id"]]
        resid_centro.append(dist_linea(cx, cz))
        nuevas.append({"code": str(s.get("source_station_codes", [""])[0]), "id": s["id"],
                       "nombre": s["name"], "c": [round(cx, 2), round(float(y.mean()), 2), round(cz, 2)],
                       "pozo": [round(cx, 2), round(cz, 2)], "pozo_y": round(float(np.percentile(y, 2)), 2),
                       "tri": int(len(idx)), "r": round(float(np.max(np.linalg.norm(Q - np.array([cx, 0, cz]), axis=1))), 1),
                       "grupos": len(s.get("lines", [])), "modelo": m["id"],
                       "pose": m.get("pose_kind", "")})
        vo += len(Q)
        io_ += int(np.asarray(idx).size)

    pos2 = np.concatenate(partes_p) if partes_p else np.zeros((0, 3), np.float32)
    col2 = np.concatenate(partes_c) if partes_c else np.zeros((0, 3), np.uint8)
    idx2 = np.concatenate(partes_i) if partes_i else np.zeros(0, np.uint32)
    print(f"modelos: {len(nuevas)}  vértices: {len(pos2)}  triángulos: {len(idx2)//3}  sin GLB: {sin_glb}")

    # ---- andenes: puertos del catálogo (logical_links) alineados con la traza ----
    est_out = []
    for s in estaciones:
        e = {"code": str(s.get("source_station_codes", [""])[0]), "nombre": s["name"],
             "l": s.get("lines", []), "xz": list(pos_est[s["id"]]), "andenes": []}
        for lk in D.get("logical_links", []):
            if lk.get("station_id") != s["id"]:
                continue
            pts = [al_gemelo(p[0], p[2]) for p in lk["points"]]
            if len(pts) >= 2:
                e["andenes"].append({"id": lk.get("port_id"), "l": lk.get("line"),
                                     "y": round(float(lk["points"][0][1]), 2),
                                     "path": [[round(pts[0][0], 2), round(float(lk["points"][0][1]), 2), round(pts[0][1], 2)],
                                              [round(pts[-1][0], 2), round(float(lk["points"][-1][1]), 2), round(pts[-1][1], 2)]]})
                resid_and.append(dist_linea(pts[0][0], pts[0][1]))
                resid_and.append(dist_linea(pts[-1][0], pts[-1][1]))
        e["anden_y"] = round(float(min((a["y"] for a in e["andenes"]), default=-18.0)), 2)
        mejor = int(np.argmin(np.hypot(traza[:, 0] - e["xz"][0], traza[:, 1] - e["xz"][1])))
        j = min(mejor + 1, len(traza) - 1)
        vx, vz = traza[j][0] - traza[mejor][0], traza[j][1] - traza[mejor][1]
        n = math.hypot(vx, vz) or 1.0
        e["tan"] = [round(vx / n, 4), round(vz / n, 4)]
        est_out.append(e)

    # ---- perfil del túnel ----
    def prof_en(x, z):
        d = sorted(((math.hypot(x - e["xz"][0], z - e["xz"][1]), e["anden_y"]) for e in est_out),
                   key=lambda t: t[0])[:2]
        if not d:
            return -18.0
        w = [(1.0 / (t[0] + 40.0), t[1]) for t in d]
        return sum(a * b for a, b in w) / sum(a for a, _ in w)

    rutas_out = []
    for t in tramos:
        rutas_out.append({"l": linea, "id": t["id"],
                          "p": [[round(x, 1), round(prof_en(x, z) - PROF_TUNEL, 1), round(z, 1)]
                                for (x, z) in t["pts"]]})

    # ---- corteza y ortofoto ----
    xs = [p[0] for r in rutas_out for p in r["p"]]
    zs = [p[2] for r in rutas_out for p in r["p"]]
    caja = {"x0": min(xs) - MARGEN_CAJA, "x1": max(xs) + MARGEN_CAJA,
            "z0": min(zs) - MARGEN_CAJA, "z1": max(zs) + MARGEN_CAJA}
    tc = json.load(open(os.path.join(DATOS_F, "terreno.json"), encoding="utf-8"))
    cotas = leer_cotas_png(os.path.join(DATOS_F, "terreno.png"))
    nx_t, nz_t = tc["nx"], tc["nz"]
    nx = max(2, int(round((caja["x1"] - caja["x0"]) / PASO_MALLA)) + 1)
    nz = max(2, int(round((caja["z1"] - caja["z0"]) / PASO_MALLA)) + 1)
    fx = np.clip((np.linspace(caja["x0"], caja["x1"], nx) - tc["x0"]) / tc["ancho"], 0, 1) * (nx_t - 1)
    fz = np.clip((np.linspace(caja["z0"], caja["z1"], nz) - tc["z0"]) / tc["alto"], 0, 1) * (nz_t - 1)
    malla = cotas[np.ix_(np.clip(np.round(fz).astype(int), 0, nz_t - 1),
                         np.clip(np.round(fx).astype(int), 0, nx_t - 1))].astype(np.float32)
    open(os.path.join(salida, "terreno.bin"), "wb").write(malla.tobytes())
    json.dump({"nx": nx, "nz": nz, "x0": caja["x0"], "x1": caja["x1"], "z0": caja["z0"], "z1": caja["z1"],
               "h_ref": tc["h_ref"], "cota_min": float(malla.min()), "cota_max": float(malla.max()),
               "paso_m": round((caja["x1"] - caja["x0"]) / (nx - 1), 1), "modo": "plano",
               "fuente": tc["fuente_dem"]},
              open(os.path.join(salida, "terreno.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    orto = Image.open(os.path.join(DATOS_F, "ortofoto.jpg")).convert("RGB")
    W, H = orto.size
    px0 = int((caja["x0"] - tc["x0"]) / tc["ancho"] * W); px1 = int((caja["x1"] - tc["x0"]) / tc["ancho"] * W)
    pz0 = int((caja["z0"] - tc["z0"]) / tc["alto"] * H); pz1 = int((caja["z1"] - tc["z0"]) / tc["alto"] * H)
    rec = orto.crop((max(0, px0), max(0, pz0), min(W, px1), min(H, pz1)))
    rec = rec.resize((2048, int(rec.height * 2048 / rec.width)), Image.LANCZOS)
    rec.save(os.path.join(salida, "terreno.jpg"), quality=82, optimize=True)

    long_km = sum(math.dist((r["p"][i][0], r["p"][i][2]), (r["p"][i - 1][0], r["p"][i - 1][2]))
                  for r in rutas_out for i in range(1, len(r["p"]))) / 2 / 1000

    json.dump({"linea": linea, "color": "#005AA9", "rutas": rutas_out,
               "estaciones": est_out, "caja": caja, "datum": "V19_POSES", "plano": True,
               "origen_v19": [OX, OY], "fuente": "catalogo V19 (explorar.html) + GLB originales"},
              open(os.path.join(salida, "red.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    escribir_mmg(os.path.join(salida, "estaciones.bin"),
                 {"origen": [EREF, NREF], "datum": "V19_POSES", "estaciones": nuevas,
                  "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3), "linea": linea},
                 pos2, col2, idx2)

    meta = {"linea": linea, "estaciones": len(est_out), "estaciones_con_modelo": len(nuevas),
            "andenes": sum(len(e["andenes"]) for e in est_out), "rutas_v19": len(rutas_v19),
            "puntos_traza": sum(len(r["p"]) for r in rutas_out), "longitud_km": round(long_km, 2),
            "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3),
            "centro_estacion_a_traza_m": {"mediana": round(float(np.median(resid_centro)), 1),
                                          "max": round(float(max(resid_centro)), 1)} if resid_centro else None,
            "puerto_a_traza_m": {"mediana": round(float(np.median(resid_and)), 1),
                                 "p90": round(float(np.percentile(resid_and, 90)), 1),
                                 "max": round(float(max(resid_and)), 1)} if resid_and else None,
            "fuente": "catalogo V19 con poses rigidas + GLB originales",
            "aviso": "poses APPROXIMATE (rms ~20 m), no levantamiento"}
    json.dump(meta, open(os.path.join(salida, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "10")

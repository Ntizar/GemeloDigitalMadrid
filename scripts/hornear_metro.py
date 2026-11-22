"""Hornea UNA línea de metro para el Gemelo Digital Madrid — v3 (plano relativo).

Reglas que resuelven los tres defectos medidos en la v1:

1. TODO en cotas RELATIVAS a la calle (0 = superficie). El visor dibuja el suelo
   a 0 y los edificios de MapLibre también están a 0: con cotas absolutas el
   metro flotaba 60 m sobre los edificios en Plaza de Castilla.
2. UNA sola traza (la shape más larga). Las 4 shapes del GTFS son dos servicios
   distintos: superpuestas daban 13 cruces.
3. Los modelos se anclan por su CENTRO y se GIRAN por su eje largo (PCA en
   planta) para alinearlo con la tangente de la traza. Anclar el centroide del
   catálogo dejaba estaciones a 100-586 m de la vía.

Fuentes: GTFS CRTM (shapes/stops) + modelos MMG1 de metro-gemelo + PNOA.
Salidas en datos/l<n>/: red.json, estaciones.bin, terreno.bin, terreno.json,
terreno.jpg, meta.json.
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

MARGEN_CAJA = 2200.0
PASO_MALLA = 130.0
TOL = 3.0          # simplificación de la traza, m
PROF_TUNEL = 3.5   # metros bajo el andén
RADIO_RECORTE = 95.0  # m: el modelo se recorta al corredor de la línea


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


# ------------------------------------------------------------------ encaje 2D
def proyectar_en_traza(x, z, traza):
    """Punto de la polilínea más cercano y su tangente (x,z normalizada)."""
    a = traza[:-1]
    ab = traza[1:] - a
    l2 = (ab ** 2).sum(1)
    l2[l2 == 0] = 1e-9
    t = np.clip(((np.array([x, z]) - a) * ab).sum(1) / l2, 0, 1)
    proj = a + ab * t[:, None]
    d = np.linalg.norm(proj - np.array([x, z]), axis=1)
    i = int(np.argmin(d))
    tan = ab[i] / max(1e-9, np.linalg.norm(ab[i]))
    return proj[i], tan, float(d[i])


def rotar_planta(pts, centro, theta):
    """Rota (x,z) alrededor de centro en planta; deja y intacta."""
    c, s = math.cos(theta), math.sin(theta)
    d = pts - centro
    x = d[:, 0] * c - d[:, 2] * s
    z = d[:, 0] * s + d[:, 2] * c
    return np.stack([x, d[:, 1], z], 1) + centro


def eje_largo(pts):
    """Ángulo (x,z) del eje largo del modelo por PCA en planta."""
    q = pts[:, [0, 2]]
    q = q - q.mean(0)
    cov = np.cov(q.T)
    w, v = np.linalg.eigh(cov)
    eje = v[:, int(np.argmax(w))]
    return math.atan2(eje[1], eje[0])


def main(linea):
    salida = os.path.join(RAIZ, "datos", "l" + linea if linea != "R" else "ramal")
    os.makedirs(salida, exist_ok=True)

    rutas_g, viajes, shapes, stops = leer_gtfs(os.path.join(DATOS_F, "gtfs", "metro.zip"))
    ids = [k for k, v in rutas_g.items() if v.get("route_short_name") == linea]
    sids = {t["shape_id"] for t in viajes if t["route_id"] in ids}
    color = "#" + (rutas_g[ids[0]].get("route_color") or "888888")

    # ---- 1) UNA traza continua: encadenar los servicios por sus extremos ----
    # El GTFS parte la L10 en dos servicios (sur y MetroNorte) con ida y vuelta
    # cada uno. Superponerlos daba 13 cruces; encadenados son una sola línea de
    # 38,2 km sin un solo cruce.
    trozos = []
    for s in sids:
        p = [a_local(lat, lon) for (_, lat, lon) in shapes[s]]
        L = sum(math.dist(p[i], p[i - 1]) for i in range(1, len(p)))
        trozos.append((L, s, p))
    trozos.sort(key=lambda t: -t[0])

    def cubierta(p, cadena, tol=40.0):
        c = np.asarray(cadena)
        idx = np.linspace(0, len(p) - 1, max(2, len(p) // 40)).astype(int)
        n = sum(1 for i in idx
                if np.min(np.hypot(c[:, 0] - p[i][0], c[:, 1] - p[i][1])) < tol)
        return n / len(idx)

    cadena = list(trozos[0][2])
    usados = [trozos[0][1]]
    for L, s, p in trozos[1:]:
        if cubierta(p, cadena) > 0.5:
            continue                            # es la vuelta de un tramo ya dibujado
        if math.dist(p[-1], cadena[0]) < 400:
            cadena = p + cadena
        elif math.dist(p[0], cadena[-1]) < 400:
            cadena = cadena + p
        elif math.dist(p[0], cadena[0]) < 400:
            cadena = p[::-1] + cadena
        elif math.dist(p[-1], cadena[-1]) < 400:
            cadena = cadena + p[::-1]
        else:
            continue
        usados.append(s)
    simple = douglas_peucker(cadena, TOL)
    traza = np.asarray(simple, dtype=float)
    print(f"traza: {' + '.join(usados)}  {len(cadena)} pts -> {len(simple)} tras simplificar  "
          f"(descartadas {len(sids) - len(usados)} shapes duplicadas)")

    # ---- 2) estaciones: stop -> punto de la traza ----
    red = json.load(open(os.path.join(DATOS_F, "red.json"), encoding="utf-8"))
    estaciones = [e for e in red["estaciones"] if linea in (e.get("l") or [])]
    por_nombre = {}
    for s in stops:
        por_nombre.setdefault(norm(s["stop_name"]), []).append(s)

    anclas = []
    for e in estaciones:
        cand_s = por_nombre.get(norm(e["nombre"]), [])
        if not cand_s:
            continue
        s = min(cand_s, key=lambda q: abs(a_local(float(q["stop_lat"]), float(q["stop_lon"]))[0] - e["xz"][0]))
        sx, sz = a_local(float(s["stop_lat"]), float(s["stop_lon"]))
        P, tan, dist = proyectar_en_traza(sx, sz, traza)
        e["_P"] = (float(P[0]), float(P[1]))
        e["_tan"] = (float(tan[0]), float(tan[1]))
        e["_stop_dist"] = dist
        e["stop"] = s["stop_id"]
        e["xz"] = [round(float(P[0]), 2), round(float(P[1]), 2)]
        e["tan"] = [round(float(tan[0]), 4), round(float(tan[1]), 4)]
        e["giro"] = round(math.degrees(math.atan2(float(tan[1]), float(tan[0]))), 2)
        anclas.append((e["nombre"], dist))

    # ---- 3) ENCAJE POR ANDÉN: el eje fiable no es el modelo, es su andén ----
    # El modelo de un intercambiador es casi cuadrado (ratio 0,9) y su PCA no
    # apunta a ninguna parte. El andén, en cambio, es una línea: su dirección sí
    # es fiable. Se gira el modelo alrededor del centroide de SU andén para que
    # ese andén caiga sobre la traza, y se prueban los dos sentidos (±180°) más
    # un barrido fino de ±6°.
    def rotar_xy(q, c, th):
        ca, sa = math.cos(th), math.sin(th)
        d = q - c
        return np.stack([d[:, 0] * ca - d[:, 1] * sa, d[:, 0] * sa + d[:, 1] * ca], 1) + c

    def dist_media(q, P, paso=3):
        a = P[:-1]
        ab = P[1:] - a
        l2 = (ab ** 2).sum(1)
        l2[l2 == 0] = 1e-9
        sub = q[::max(1, len(q) // 16)]
        tot = 0.0
        for punto in sub:
            t = np.clip(((punto - a) * ab).sum(1) / l2, 0, 1)
            proj = a + ab * t[:, None]
            tot += float(np.linalg.norm(proj - punto, axis=1).min())
        return tot / len(sub)

    def eje_path(path):
        q = np.array([[p[0], p[2]] for p in path], float)
        q = q - q.mean(0)
        w, v = np.linalg.eigh(np.cov(q.T))
        e = v[:, int(np.argmax(w))]
        return math.atan2(e[1], e[0])

    for e in estaciones:
        if "_P" not in e or not e["andenes"]:
            e["_theta"], e["_ca"] = 0.0, None
            continue
        cand_a = [a for a in e["andenes"] if a.get("l") == linea] or e["andenes"]
        and_ = max(cand_a, key=lambda a: len(a["path"]))
        Pa = np.array([[p[0], p[2]] for p in and_["path"]], float)
        ca = Pa.mean(0)
        ang_anden = eje_path(and_["path"])
        ang_traza = math.atan2(e["_tan"][1], e["_tan"][0])
        mejor = None
        for base in (ang_traza - ang_anden, ang_traza - ang_anden + math.pi):
            for dg in np.arange(-6.0, 6.01, 1.0):
                th = base + math.radians(float(dg))
                dm = dist_media(rotar_xy(Pa, ca, th), traza)
                if mejor is None or dm < mejor[0]:
                    mejor = (dm, th)
        e["_theta"] = mejor[1]
        e["_ca"] = ca
        e["_and_dist"] = mejor[0]

    # ---- 4) modelos y andenes con esa transformación ----
    cab, pos, col, idx = leer_mmg(os.path.join(DATOS_F, "estaciones.bin"))
    por_code = {e["code"]: e for e in estaciones}
    partes_p, partes_c, partes_i, nuevas = [], [], [], []
    vo = io_ = 0
    giros = []
    for m in cab["estaciones"]:
        e = por_code.get(m["code"])
        if not e or "_P" not in e or e.get("_ca") is None:
            continue
        v0, vn = m["v0"], m["vn"]
        pp = pos[v0:v0 + vn].astype(np.float64)
        cx, cz = float(e["_ca"][0]), float(e["_ca"][1])
        centro_rot = np.array([cx, 0.0, cz])
        th = e["_theta"]
        pp = rotar_planta(pp, centro_rot, th)
        dp = np.array([e["_P"][0] - cx, 0.0, e["_P"][1] - cz])
        pp = pp + dp
        giros.append(math.degrees(th))
        partes_p.append(pp.astype(np.float32))
        partes_c.append(col[v0:v0 + vn])
        triloc = (idx[m["i0"]:m["i0"] + m["in"]] - v0).reshape(-1, 3)
        cen = pp[triloc].mean(1)
        # distancia de cada triángulo a la polilínea de la traza
        a1 = traza[:-1]; ab1 = traza[1:] - a1
        l21 = (ab1 ** 2).sum(1); l21[l21 == 0] = 1e-9
        dm = np.full(len(cen), 1e9)
        for k in range(0, len(cen), 512):
            q = cen[k:k + 512][:, [0, 2]]
            t = np.clip(((q[:, None, :] - a1[None]) * ab1[None]).sum(2) / l21[None], 0, 1)
            proj = a1[None] + ab1[None] * t[:, :, None]
            dm[k:k + 512] = np.linalg.norm(proj - q[:, None, :], axis=2).min(1)
        sel = triloc[dm < RADIO_RECORTE].ravel()
        partes_i.append(sel + vo)
        m2 = dict(m)
        m2["v0"], m2["i0"] = vo, io_
        m2["c"] = [round(float(m["c"][0] + dp[0] - cx + cx), 2), round(float(m["c"][1]), 2),
                   round(float(m["c"][2] + dp[2]), 2)]
        m2["c"][0] = round(float(e["_P"][0] + (m["c"][0] - cx) * math.cos(th) - (m["c"][2] - cz) * math.sin(th)), 2)
        m2["c"][2] = round(float(e["_P"][1] + (m["c"][0] - cx) * math.sin(th) + (m["c"][2] - cz) * math.cos(th)), 2)
        m2["pozo"] = [round(float(m2["c"][0]), 2), round(float(m2["c"][2]), 2)]
        m2["pozo_y"] = round(float(m["pozo_y"]), 2)
        m2["giro_deg"] = round(math.degrees(th), 1)
        m2["in"] = int(len(sel))
        nuevas.append(m2)
        vo += vn
        io_ += int(len(sel))

    pos2 = np.concatenate(partes_p) if partes_p else np.zeros((0, 3), np.float32)
    col2 = np.concatenate(partes_c) if partes_c else np.zeros((0, 3), np.uint8)
    idx2 = np.concatenate(partes_i) if partes_i else np.zeros(0, np.uint32)

    for e in estaciones:
        if e.get("_ca") is None:
            e["andenes"] = []
            continue
        cx, cz = float(e["_ca"][0]), float(e["_ca"][1])
        centro_rot = np.array([cx, 0.0, cz])
        dp = np.array([e["_P"][0] - cx, 0.0, e["_P"][1] - cz])
        th = e["_theta"]
        nuevos = []
        for a in e["andenes"]:
            if a.get("l") != linea:
                continue
            path = np.asarray(a["path"], dtype=float)
            path = rotar_planta(path, centro_rot, th) + dp
            path[:, 1] = a["y"]
            a["path"] = path.round(2).tolist()
            a["y"] = round(float(a["y"]), 2)
            nuevos.append(a)
        e["andenes"] = nuevos
        e["anden_y"] = round(float(min((a["y"] for a in nuevos), default=-16.0)), 2)

    # ---- 5) perfil: el túnel va bajo el andén, relativo a la calle ----
    profs = []
    for e in estaciones:
        if e.get("andenes"):
            profs.append(min(a["y"] for a in e["andenes"]))
    prof_por_defecto = float(np.median(profs)) if profs else -18.0
    for e in estaciones:
        e.pop("_tan", None)
        e.pop("_stop_dist", None)

    def prof_en(x, z):
        d = sorted(((math.hypot(x - e["_P"][0], z - e["_P"][1]), (min(a["y"] for a in e["andenes"])
                                                                 if e["andenes"] else prof_por_defecto))
                    for e in estaciones if "_P" in e), key=lambda t: t[0])[:2]
        if not d:
            return prof_por_defecto
        w = [(1.0 / (t[0] + 40.0), t[1]) for t in d]
        return sum(a * b for a, b in w) / sum(a for a, _ in w)

    ruta_p = []
    for (x, z) in simple:
        ruta_p.append([round(x, 1), round(prof_en(x, z) - PROF_TUNEL, 1), round(z, 1)])
    rutas = [{"l": linea, "shape": "cadena", "d": "ida", "p": ruta_p}]

    # ---- 6) caja, corteza y ortofoto (cotas absolutas solo para la textura) ----
    xs = [p[0] for p in ruta_p]
    zs = [p[2] for p in ruta_p]
    caja = {"x0": min(xs) - MARGEN_CAJA, "x1": max(xs) + MARGEN_CAJA,
            "z0": min(zs) - MARGEN_CAJA, "z1": max(zs) + MARGEN_CAJA}
    tc = json.load(open(os.path.join(DATOS_F, "terreno.json"), encoding="utf-8"))
    cotas = leer_cotas_png(os.path.join(DATOS_F, "terreno.png"))
    nx_t, nz_t = tc["nx"], tc["nz"]
    nx = max(2, int(round((caja["x1"] - caja["x0"]) / PASO_MALLA)) + 1)
    nz = max(2, int(round((caja["z1"] - caja["z0"]) / PASO_MALLA)) + 1)
    fx = np.clip((np.linspace(caja["x0"], caja["x1"], nx) - tc["x0"]) / tc["ancho"], 0, 1) * (nx_t - 1)
    fz = np.clip((np.linspace(caja["z0"], caja["z1"], nz) - tc["z0"]) / tc["alto"], 0, 1) * (nz_t - 1)
    malla_abs = cotas[np.ix_(np.clip(np.round(fz).astype(int), 0, nz_t - 1),
                             np.clip(np.round(fx).astype(int), 0, nx_t - 1))].astype(np.float32)
    meta_terreno = {"nx": nx, "nz": nz, "x0": caja["x0"], "x1": caja["x1"],
                    "z0": caja["z0"], "z1": caja["z1"], "h_ref": tc["h_ref"],
                    "cota_min": float(malla_abs.min()), "cota_max": float(malla_abs.max()),
                    "paso_m": round((caja["x1"] - caja["x0"]) / (nx - 1), 1),
                    "modo": "plano", "fuente": tc["fuente_dem"]}
    # el visor dibuja el suelo PLANO a 0; se guardan las cotas para el futuro
    open(os.path.join(salida, "terreno.bin"), "wb").write(malla_abs.tobytes())
    json.dump(meta_terreno, open(os.path.join(salida, "terreno.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    orto = Image.open(os.path.join(DATOS_F, "ortofoto.jpg")).convert("RGB")
    W, H = orto.size
    px0 = int((caja["x0"] - tc["x0"]) / tc["ancho"] * W); px1 = int((caja["x1"] - tc["x0"]) / tc["ancho"] * W)
    pz0 = int((caja["z0"] - tc["z0"]) / tc["alto"] * H); pz1 = int((caja["z1"] - tc["z0"]) / tc["alto"] * H)
    recorte = orto.crop((max(0, px0), max(0, pz0), min(W, px1), min(H, pz1)))
    recorte = recorte.resize((2048, int(recorte.height * 2048 / recorte.width)), Image.LANCZOS)
    recorte.save(os.path.join(salida, "terreno.jpg"), quality=82, optimize=True)

    # ---- 7) validación medida ----
    tr = np.asarray([[p[0], p[2]] for p in ruta_p])
    def dist_linea(x, z):
        a1 = tr[:-1]; ab1 = tr[1:] - a1
        l21 = (ab1 ** 2).sum(1); l21[l21 == 0] = 1e-9
        t = np.clip(((np.array([x, z]) - a1) * ab1).sum(1) / l21, 0, 1)
        proj = a1 + ab1 * t[:, None]
        return float(np.linalg.norm(proj - np.array([x, z]), axis=1).min())

    resid_est = [dist_linea(e["c"][0], e["c"][2]) for e in nuevas]
    and_final = []
    for e in estaciones:
        for a in e["andenes"]:
            for q in a["path"][::max(1, len(a["path"]) // 8)]:
                and_final.append(dist_linea(q[0], q[2]))
    long_km = sum(math.dist((ruta_p[i][0], ruta_p[i][2]), (ruta_p[i - 1][0], ruta_p[i - 1][2]))
                  for i in range(1, len(ruta_p))) / 1000

    and_dists = [float(e.get("_and_dist", 0.0)) for e in estaciones]
    for e in estaciones:
        e.pop("_ca", None); e.pop("_theta", None); e.pop("_and_dist", None)

    sal = {"linea": linea, "color": color, "rutas": rutas, "estaciones": estaciones,
           "caja": caja, "datum": "RELATIVO_CALLE_0", "plano": True,
           "anclaje": "stop proyectado sobre la traza + giro PCA"}
    json.dump(sal, open(os.path.join(salida, "red.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    cab2 = {"origen": cab["origen"], "datum": cab["datum"], "estaciones": nuevas,
            "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3),
            "linea": linea, "modo": "plano, cota relativa a la calle",
            "fuente": "metro-gemelo MMG1 + stop de stops.txt + giro PCA"}
    escribir_mmg(os.path.join(salida, "estaciones.bin"), cab2, pos2, col2, idx2)

    meta = {
        "linea": linea, "color": color, "plano": True,
        "estaciones": len(estaciones), "estaciones_con_modelo": len(nuevas),
        "andenes": sum(len(e["andenes"]) for e in estaciones),
        "rutas": len(rutas), "puntos_traza": len(ruta_p),
        "shapes_descartadas": len(sids) - len(usados),
        "longitud_km": round(long_km, 2),
        "vertices": int(len(pos2)), "triangulos": int(len(idx2) / 3),
        "terreno": meta_terreno,
        "datum": "cotas relativas a la calle (0 = superficie)",
        "centro_modelo_a_traza_m": {"mediana": round(float(np.median(resid_est)), 1),
                                    "max": round(float(max(resid_est)), 1)},
        "giro_modelo_deg": {"mediana": round(float(np.median(np.abs(giros))), 1),
                            "max": round(float(max(np.abs(giros))), 1)},
        "anden_a_traza_m": {"mediana": round(float(np.median(and_final)), 1) if and_final else None,
                            "p90": round(float(np.percentile(and_final, 90)), 1) if and_final else None,
                            "max": round(float(max(and_final)), 1) if and_final else None},
        "stop_a_traza_m": {"mediana": round(float(np.median([d for _, d in anclas])), 1),
                           "max": round(float(max(d for _, d in anclas)), 1)},
        "profundidad_tunel_m": [round(float(min(p[1] for p in ruta_p)), 1),
                                round(float(max(p[1] for p in ruta_p)), 1)],
        "fuente": "CRTM GTFS + modelos 20-nov-2026 (croquis) + PNOA",
    }
    json.dump(meta, open(os.path.join(salida, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: meta[k] for k in ("estaciones", "andenes", "puntos_traza", "longitud_km",
                                           "centro_modelo_a_traza_m", "giro_modelo_deg",
                                           "anden_a_traza_m", "profundidad_tunel_m")},
                     ensure_ascii=False, indent=1))
    for f in ("red.json", "estaciones.bin", "terreno.bin", "terreno.jpg", "meta.json"):
        print(f"  {f:16s} {os.path.getsize(os.path.join(salida, f))/1024:8.1f} KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "10")

#!/usr/bin/env python
"""
Probetas de regresion: construye cuatro piezas que ejercitan las operaciones
de features.py y mecanizado.py y comprueba cada paso por VOLUMEN frente al
valor teorico. Con SOLIDWORKS abierto:

    py scripts\\probetas.py            # todas
    py scripts\\probetas.py 1 3        # solo algunas

P1 revolucion + vaciado + taladro del asistente + matriz circular
P2 matriz lineal + angulo de salida + nervio + simetria de cuerpo +
   plano de referencia + recubrimiento + barrido
P3 chapa: brida base desde perfil abierto + taladro + desarrollo a DXF
P4 placa para fresar (Carvera Air): cajera con redondeos, taladros, chaflan,
   material, ecuaciones, configuraciones, revision DFM y STEP

Tarda mas de 60 s en total: fuera de sw_execute_script, lanzalo como
proceso aparte.
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
from solidworks_mcp import core as c, features as fx, mecanizado as mz  # noqa: E402
from solidworks_mcp.paths import OUT_DIR  # noqa: E402

DIR = os.path.join(OUT_DIR, "probetas")
PI = math.pi
RES = []


def vol(doc):
    return fx.mass_props(doc)["volumen_mm3"]


def check(nombre, real, teorico, tol=0.05):
    ok = abs(real - teorico) <= tol
    RES.append({"paso": nombre, "real": round(real, 3), "teorico": round(teorico, 3), "ok": ok})
    print(("OK  " if ok else "MAL ") + "%-40s %12.3f  (teorico %.3f)" % (nombre, real, teorico), flush=True)
    return ok


def guardar(doc, nombre):
    os.makedirs(DIR, exist_ok=True)
    c.save_as(doc, os.path.join(DIR, nombre + ".SLDPRT"))


# ------------------------------------------------------------------ P1
def p1():
    doc = fx.new_part()

    def perfil(sm, sk):
        pts = [(0, 0), (30, 0), (30, 8), (15, 8), (15, 28), (0, 28)]
        for i in range(len(pts)):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
            sm.CreateLine(c.mm(x0), c.mm(y0), 0.0, c.mm(x1), c.mm(y1), 0.0)
        sm.CreateCenterLine(0.0, c.mm(-5), 0.0, 0.0, c.mm(35), 0.0)
    sk = fx.sketch_on_plane(doc, 0, perfil)
    fx.revolve(doc, sk)
    v_rev = PI * 30 ** 2 * 8 + PI * 15 ** 2 * 20
    check("P1 revolucion", vol(doc), v_rev)
    fx.shell(doc, ((20, -5, 0), (0, 1, 0)), 2.0)
    v_sh = v_rev - (PI * 28 ** 2 * 6 + PI * 13 ** 2 * 20)
    check("P1 vaciado 2 mm", vol(doc), v_sh)
    h = fx.hole_wizard(doc, ((22, 20, 0), (0, -1, 0)), size="M5")
    v_h = PI * 2.75 ** 2 * 2
    check("P1 taladro M5 (holgura 5,5)", vol(doc), v_sh - v_h)
    fx.circular_pattern(doc, [h], ((40, 18, 0), (-1, 0, 0)), 6)
    check("P1 matriz circular x6", vol(doc), v_sh - 6 * v_h)
    guardar(doc, "P1_revolucion")
    return doc


# ------------------------------------------------------------------ P2
def p2():
    doc = fx.new_part()
    c.extrude(doc, [{"type": "rect", "x0": -40, "y0": -20, "x1": 40, "y1": 20}], 0, 5, name="Placa")
    ran = c.extrude(doc, [{"type": "rect", "x0": -17.5, "y0": -8, "x1": -14.5, "y1": 8}], 0, 5,
                    cut=True, through=True, name="Ranura")
    c.extrude(doc, [{"type": "circle", "x": 30, "y": 0, "d": 10}], 5, 20, name="Teton")
    v = 80 * 40 * 5 - 3 * 16 * 5 + PI * 25 * 15
    check("P2 placa + ranura + teton", vol(doc), v)
    fx.linear_pattern(doc, [doc.FeatureByName(ran["feature"])], (0, -20, 5), 5, 8)
    v -= 4 * 3 * 16 * 5
    check("P2 matriz lineal x5", vol(doc), v)
    fx.draft(doc, ((0, 15, 20), (0, 0, -1)), [((45, 0, 12), (-1, 0, 0))], 3.0)
    r2 = 5 - 15 * math.tan(math.radians(3))
    v += PI * 15 / 3 * (25 + 5 * r2 + r2 * r2) - PI * 25 * 15
    check("P2 angulo de salida 3 grados", vol(doc), v)

    def linea(sm, sk):
        a, b = fx.sketch_point(sk, 39.5, 0, 5), fx.sketch_point(sk, 34, 0, 15)
        sm.CreateLine(a[0], a[1], 0.0, b[0], b[1], 0.0)
    fx.rib(doc, fx.sketch_on_plane(doc, 1, linea), 2.0)
    v_rib = vol(doc) - v
    RES.append({"paso": "P2 nervio (sin teorico)", "real": round(v_rib, 3), "ok": v_rib > 0})
    v += v_rib
    fx.mirror_body(doc, c.ref_planes(doc)[2])
    v += (PI * 15 / 3 * (25 + 5 * r2 + r2 * r2)) + v_rib
    check("P2 simetria de cuerpo", vol(doc), v)
    pl = fx.ref_plane_offset(doc, c.ref_planes(doc)[0], 15, invertir=True)
    s1 = fx.sketch_on_plane(doc, 0, lambda sm, sk: sm.CreateCornerRectangle(
        c.mm(22), c.mm(-8), 0.0, c.mm(38), c.mm(8), 0.0))
    s2 = fx.sketch_on_plane(doc, pl, lambda sm, sk: sm.CreateCircleByRadius(c.mm(30), 0.0, 0.0, c.mm(4)))
    fx.loft(doc, [s1, s2])
    v_loft = vol(doc) - v
    RES.append({"paso": "P2 recubrimiento (sin teorico)", "real": round(v_loft, 3), "ok": v_loft > 0})
    v += v_loft

    def arco(sm, sk):
        C, A, B = (fx.sketch_point(sk, x, 0, 5) for x in (0, -20, 20))
        sm.CreateArc(C[0], C[1], 0.0, A[0], A[1], 0.0, B[0], B[1], 0.0, 1)
    tray = fx.sketch_on_plane(doc, 1, arco)
    perf = fx.sketch_on_face(doc, (-20, 0, 40), (0, 0, -1), lambda sm, sk: sm.CreateCircleByRadius(
        *fx.sketch_point(sk, -20, 0, 5), 0.0, c.mm(2)))
    fx.sweep(doc, perf, tray)
    check("P2 barrido (Pappus, +-1,5)", vol(doc), v + PI * 4 * PI * 20, tol=1.5)
    guardar(doc, "P2_matriz_simetria_recubrir_barrer")
    return doc


# ------------------------------------------------------------------ P3
def p3():
    doc = fx.new_part()
    fx.sheet_base_flange(doc, [(0, 30, 0, 0), (0, 0, 60, 0)], 1.5, 1.5, 40)
    v = vol(doc)
    c.extrude(doc, [{"type": "circle", "x": 40, "y": -20, "d": 6}], 0, -5, plane=1, cut=True,
              through=True, name="Taladro6")
    check("P3 taladro en chapa", vol(doc), v - PI * 9 * 1.5)
    guardar(doc, "P3_chapa_L")
    d = fx.export_flat_dxf(doc, os.path.join(DIR, "P3_chapa_L_desarrollo.dxf"))
    largo = 28.5 + 58.5 + PI / 2 * (1.5 + 0.5 * 1.5)       # K = 0,5
    check("P3 desarrollo: largo (mm)", d["tamano"][0], largo, tol=0.01)
    check("P3 desarrollo: ancho (mm)", d["tamano"][1], 40.0, tol=0.01)
    return doc


# ------------------------------------------------------------------ P4
def p4():
    doc = fx.new_part()
    c.extrude(doc, [{"type": "rect", "x0": -30, "y0": -20, "x1": 30, "y1": 20}], 0, 12, name="Placa")
    c.extrude(doc, [{"type": "rect", "x0": -20, "y0": -10, "x1": 20, "y1": 10}], 12, 7, cut=True, name="Cajera")
    c.extrude(doc, [{"type": "circle", "x": x, "y": y, "d": 3.4} for x in (-25, 25) for y in (-15, 15)],
              0, 12, cut=True, through=True, name="Taladros")
    v = 60 * 40 * 12 - 40 * 20 * 5 - 4 * PI * 1.7 ** 2 * 12
    check("P4 placa + cajera + taladros", vol(doc), v)
    vert = [e for e in c.straight_edges(doc)
            if abs(e["p"][0] - e["q"][0]) < 1e-6 and abs(e["p"][1] - e["q"][1]) < 1e-6
            and abs(abs(e["p"][0]) - 20) < 1e-6 and abs(abs(e["p"][1]) - 10) < 1e-6]
    c.fillet_edges(doc, vert, radius_mm=3.0, propagate=False)
    v += 4 * (9 - PI * 9 / 4) * 5
    check("P4 redondeos R3 en la cajera", vol(doc), v)
    top, _ = c.edges_on_plane(doc, 12)
    outer = [e for e in top if (abs(abs(e["p"][0]) - 30) < 1e-6 and abs(abs(e["q"][0]) - 30) < 1e-6)
             or (abs(abs(e["p"][1]) - 20) < 1e-6 and abs(abs(e["q"][1]) - 20) < 1e-6)]
    c.chamfer_edges(doc, outer, dist_mm=0.5)
    check("P4 chaflan 0,5 (esquinas +-0,2)", vol(doc), v - 0.125 * 200, tol=0.2)
    m = fx.set_material(doc)
    check("P4 densidad 6061-T6", m["densidad"], 2700.0, tol=0.5)
    res = fx.add_equations(doc, ['"EspesorPlaca" = 12', '"ProfCajera" = 5',
                                 '"D1@Placa" = "EspesorPlaca"', '"D19@Cajera" = "EspesorPlaca"',
                                 '"D1@Cajera" = "ProfCajera"'])
    RES.append({"paso": "P4 ecuaciones", "real": res, "ok": all(i >= 0 for i in res)})
    fx.add_configuration(doc, "Espesor_15", "Placa de 15 mm", {'"EspesorPlaca"': 15})
    check("P4 config Espesor_15: alto", c.overall_bbox(doc)["size"][2], 15.0, tol=1e-3)
    doc.ShowConfiguration2("Predeterminado")
    doc.EditRebuild3
    check("P4 config Predeterminado: alto", c.overall_bbox(doc)["size"][2], 12.0, tol=1e-3)
    dfm = mz.dfm_3ejes(doc)
    RES.append({"paso": "P4 DFM Carvera Air", "real": dfm, "ok": dfm["cabe"] and dfm["una_atada"]})
    guardar(doc, "P4_placa_carvera")
    RES.append({"paso": "P4 STEP", "real": mz.export_cam(doc, DIR, "P4_placa_carvera_12mm"), "ok": True})
    return doc


if __name__ == "__main__":
    pedidas = [int(a) for a in sys.argv[1:]] or [1, 2, 3, 4]
    t0 = time.time()
    for n in pedidas:
        {1: p1, 2: p2, 3: p3, 4: p4}[n]()
    fallos = [r for r in RES if not r["ok"]]
    print("\n%d pasos, %d fallos, %.1f s" % (len(RES), len(fallos), time.time() - t0))
    with open(os.path.join(DIR, "probetas.json"), "w", encoding="utf-8") as fh:
        json.dump(RES, fh, indent=1, ensure_ascii=False, default=str)
    sys.exit(1 if fallos else 0)

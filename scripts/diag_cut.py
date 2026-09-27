#!/usr/bin/env python
"""
Diagnostico del corte: construye una placa y prueba variantes de FeatureCut4 /
FeatureCut3 sobre ella, una por agujero, informando cual devuelve una operacion.

    py -3.14 diag_cut.py

Cada variante taladra un circulo d2 en una X distinta de la placa (30 x 20 x 4).
Si una variante falla, el croquis se cierra y se pasa a la siguiente, asi que una
sola pasada da el resultado de todas.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()

import sw_core as c

mm = c.mm

# -- argumentos de FeatureCut4 en orden, con el juego base -------------------
# Sd, Flip, Dir, T1, T2, D1, D2, Dchk1, Dchk2, Ddir1, Ddir2, Dang1, Dang2,
# OffsetReverse1, OffsetReverse2, TranslateSurface1, TranslateSurface2,
# NormalCut, UseFeatScope, UseAutoSelect, AssemblyFeatureScope,
# AutoSelectComponents, PropagateFeatureToParts, T0, StartOffset,
# FlipStartOffset, OptimizeGeometry

def cut4(**kw):
    a = dict(Sd=True, Flip=False, Dir=False, T1=0, T2=0, D1=mm(4), D2=0.0,
             Dchk1=False, Dchk2=False, Ddir1=False, Ddir2=False,
             Dang1=0.0, Dang2=0.0, OffsetReverse1=False, OffsetReverse2=False,
             TranslateSurface1=False, TranslateSurface2=False,
             NormalCut=False, UseFeatScope=True, UseAutoSelect=True,
             AssemblyFeatureScope=False, AutoSelectComponents=False,
             PropagateFeatureToParts=False,
             T0=0, StartOffset=0.0, FlipStartOffset=False,
             OptimizeGeometry=False)
    a.update(kw)
    return [a[k] for k in (
        "Sd", "Flip", "Dir", "T1", "T2", "D1", "D2", "Dchk1", "Dchk2",
        "Ddir1", "Ddir2", "Dang1", "Dang2", "OffsetReverse1", "OffsetReverse2",
        "TranslateSurface1", "TranslateSurface2", "NormalCut", "UseFeatScope",
        "UseAutoSelect", "AssemblyFeatureScope", "AutoSelectComponents",
        "PropagateFeatureToParts", "T0", "StartOffset", "FlipStartOffset",
        "OptimizeGeometry")]


VARIANTS = [
    # (etiqueta, metodo, argumentos, AddToDB)
    ("A  cut4 base (ciega +Z)",          "FeatureCut4", cut4(),                       True),
    ("B  cut4 Dir=True",                 "FeatureCut4", cut4(Dir=True),               True),
    ("C  cut4 Flip=True",                "FeatureCut4", cut4(Flip=True),              True),
    ("D  cut4 T1=1 (por todo)",          "FeatureCut4", cut4(T1=1, D1=0.0),           True),
    ("E  cut4 T1=1 Dir=True",            "FeatureCut4", cut4(T1=1, D1=0.0, Dir=True), True),
    ("F  cut4 sin feature scope",        "FeatureCut4", cut4(UseFeatScope=False,
                                                            UseAutoSelect=False),     True),
    ("G  cut4 base, AddToDB=False",      "FeatureCut4", cut4(),                       False),
    ("H  cut4 T1=1, AddToDB=False",      "FeatureCut4", cut4(T1=1, D1=0.0),           False),
    ("I  cut3 base",                     "FeatureCut3", cut4()[:-1],                  True),
    ("J  cut3 T1=1",                     "FeatureCut3", cut4(T1=1, D1=0.0)[:-1],      True),
]


def attempt(doc, plane, x, method, args, add_to_db):
    sm = doc.SketchManager
    doc.ClearSelection2(True)
    plane.Select2(False, 0)
    sm.AddToDB = add_to_db
    sm.DisplayWhenAdded = False
    sm.InsertSketch(True)
    sm.CreateCircleByRadius(mm(x), 0.0, 0.0, mm(1.0))
    try:
        fn = getattr(doc.FeatureManager, method)
    except AttributeError:
        sm.InsertSketch(True)          # cerrar el croquis colgado
        sm.AddToDB = False
        sm.DisplayWhenAdded = True
        return "metodo inexistente"
    try:
        f = fn(*args)
    except Exception as exc:
        sm.InsertSketch(True)
        sm.AddToDB = False
        sm.DisplayWhenAdded = True
        return "excepcion: %s: %s" % (type(exc).__name__, str(exc)[:160])
    sm.AddToDB = False
    sm.DisplayWhenAdded = True
    if f is None:
        sm.InsertSketch(True)
        return "None"
    try:
        return "OK -> %s" % f.Name
    except Exception:
        return "OK"


def main():
    c.co_init()
    app = c.sw_app()
    print("SOLIDWORKS revision:", app.RevisionNumber)

    tpl = app.GetUserPreferenceStringValue(8)
    doc = app.NewDocument(tpl, 0, 0.0, 0.0)
    if doc is None:
        raise SystemExit("NewDocument devolvio None. Plantilla: %r" % tpl)

    c.extrude(doc, [{"type": "rect", "x0": -16, "y0": -10, "x1": 16, "y1": 10}],
              0, 4, name="Placa")
    print("placa 32x20x4 ok\n")

    planes = c.ref_planes(doc)
    print("planos:", [p.Name for p in planes])
    plane = planes[0]

    bb0 = c.overall_bbox(doc)
    print("bbox antes:", bb0["size"], "\n")

    results = []
    x = -13.5
    for label, method, args, add_to_db in VARIANTS:
        r = attempt(doc, plane, x, method, args, add_to_db)
        results.append((label, r))
        print("%-32s %s" % (label, r))
        x += 3.0

    print("\n--- resumen ---")
    ok = [l for l, r in results if r.startswith("OK")]
    print("funcionan:", ok if ok else "NINGUNA")
    print("bbox despues:", c.overall_bbox(doc)["size"])
    print("\nOperaciones en el arbol:")
    for f in c.features_info(doc, 60):
        print("  %-28s %s" % (f["name"], f["type"]))
    print("\nDeja la pieza abierta para que la mires. No se guarda.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()

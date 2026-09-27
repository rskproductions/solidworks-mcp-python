#!/usr/bin/env python
"""
Comprobacion del MCP sin pasar por Claude: ejerce las primitivas contra la
sesion real de SOLIDWORKS. Abre SOLIDWORKS antes de lanzarlo.

    python selftest.py            solo lectura (conecta, lista, no modela)
    python selftest.py --build    ademas construye una pieza de prueba en out\\

La pieza de prueba: placa 30x20x4 con un agujero de 6 y un hexagono pasante,
mas un resalte a partir de z=4. Toca todas las entidades y el corte con
inicio desplazado, que es donde falla la API si algo esta mal.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()

import sw_core as c

from solidworks_mcp.paths import OUT_DIR as OUT  # noqa: E402


def show(title, obj):
    print("\n== %s" % title)
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main():
    c.co_init()
    app = c.sw_app()
    print("SOLIDWORKS revision:", app.RevisionNumber)

    doc = c.active_doc(required=False)
    print("Documento activo:", doc.GetTitle if doc else "(ninguno)")

    if "--build" not in sys.argv:
        if doc is not None:
            show("cuerpos", c.bodies_info(doc))
            show("bbox", c.overall_bbox(doc))
            show("operaciones", c.features_info(doc, 40))
        print("\nOK (solo lectura). Lanza con --build para probar el modelado.")
        return

    tpl = app.GetUserPreferenceStringValue(8)
    doc = app.NewDocument(tpl, 0, 0.0, 0.0)
    if doc is None:
        raise SystemExit("NewDocument devolvio None. Plantilla: %r" % tpl)

    def step(label, profile, z0, z1, **kw):
        """Ejecuta una operacion y comprueba que el solido ha cambiado de verdad."""
        before = c.face_count(doc)
        c.extrude(doc, profile, z0, z1, **kw)
        after = c.face_count(doc)
        flag = "ok" if after != before else "SIN EFECTO (caras %d -> %d)" % (before, after)
        print("  %-42s %s" % (label, flag))
        return after != before

    ok = []
    ok.append(step("placa 30x20x4",
                   [{"type": "rect", "x0": -15, "y0": -10, "x1": 15, "y1": 10}],
                   0, 4, name="Placa"))
    ok.append(step("agujero d6 pasante (corte por todo)",
                   [{"type": "circle", "x": -9, "y": 0, "d": 6}],
                   0, 4, cut=True, through=True, name="Agujero"))
    ok.append(step("hexagono af5 pasante (corte ciego)",
                   [{"type": "hexagon", "cx": 9, "cy": 0, "af": 5}],
                   0, 4, cut=True, name="Hexagono"))
    ok.append(step("resalte z4->9 (inicio desplazado T0=3)",
                   [{"type": "circle", "x": 0, "y": 0, "d": 10}],
                   4, 9, name="Resalte"))
    ok.append(step("corte por polilinea",
                   [{"type": "polyline",
                     "points": [[-14, 9], [-10, 9], [-14, 5]], "close": True}],
                   0, 4, cut=True, through=True, name="CorteEsquina"))

    show("cuerpos", c.bodies_info(doc))
    show("bbox", c.overall_bbox(doc))
    show("operaciones", c.features_info(doc, 40))

    show("guardado", c.save_as(doc, os.path.join(OUT, "MCP_SELFTEST.SLDPRT"),
                               also_step=True))
    print("\ncaras finales:", c.face_count(doc))
    if all(ok):
        print("OK - las 5 operaciones han modificado el solido.")
    else:
        print("FALLO - alguna operacion no ha tocado el solido (ver arriba).")
    print("Esperado: bbox size = [30, 20, 9].")

    print("\n-- guarda de sw_execute_script(readOnly=True) --")
    hits = c.scan_mutating("doc.FeatureManager.FeatureCut4(1,2,3)")
    print("  deberia detectar FeatureCut4:", hits, "ok" if hits else "FALLO")
    hits2 = c.scan_mutating("print(doc.GetTitle)\nx = c.overall_bbox(doc)")
    print("  no deberia detectar nada:", hits2, "ok" if not hits2 else "FALLO")

    print("\n-- sw_screenshot --")
    try:
        shot = c.screenshot(doc, os.path.join(OUT, "selftest_view.png"),
                            width=800, height=600, direction="isometric")
        print("  captura:", shot, "ok" if os.path.exists(shot["path"]) else "FALLO (no se creo el fichero)")
    except Exception as exc:
        print("  FALLO:", type(exc).__name__, exc)
        print("  (si direction falla, prueba direction='current': ver README, IDs de vista no verificados)")


if __name__ == "__main__":
    main()

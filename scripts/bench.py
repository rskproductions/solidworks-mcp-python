#!/usr/bin/env python
"""
Mide donde se va el tiempo. Sin esto, optimizar es adivinar.

    python bench.py

Separa tres cosas que se confunden:
  1. arranque del interprete + import de pywin32   (coste fijo por ejecucion)
  2. conexion COM a SOLIDWORKS                      (coste fijo por proceso)
  3. el trabajo real: recorrer aristas, contar caras, leer el arbol
"""

import sys
import time

T0 = time.perf_counter()
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()

t = time.perf_counter()
import pythoncom
import win32com.client as w
T_IMPORT_PYWIN32 = time.perf_counter() - t

t = time.perf_counter()
import sw_core as c
T_IMPORT_CORE = time.perf_counter() - t


def cron(etiqueta, fn, veces=1):
    t = time.perf_counter()
    for _ in range(veces):
        r = fn()
    dt = (time.perf_counter() - t) / veces
    print("  %-42s %8.1f ms%s" % (etiqueta, dt * 1000,
                                  ("  (media de %d)" % veces) if veces > 1 else ""))
    return r, dt


print("\n== coste fijo ==")
print("  %-42s %8.1f ms" % ("import pywin32", T_IMPORT_PYWIN32 * 1000))
print("  %-42s %8.1f ms" % ("import sw_core", T_IMPORT_CORE * 1000))

print("\n== conexion ==")
cron("co_init()", c.co_init)
app, _ = cron("Dispatch('SldWorks.Application')", c.sw_app)
doc, _ = cron("ActiveDoc", lambda: c.active_doc())
if doc is None:
    print("\nNo hay documento activo: abre una pieza y repite.")
    sys.exit(0)

print("\n== llamadas COM sueltas (round-trip) ==")
cron("doc.GetTitle", lambda: doc.GetTitle, 50)
cron("doc.GetBodies2(0, True)", lambda: doc.GetBodies2(0, True), 20)

bodies = doc.GetBodies2(0, True) or []
if bodies:
    b = bodies[0]
    cron("soft(body,'GetFaceCount')  [ya evaluado]", lambda: c.soft(b, "GetFaceCount"), 50)
    edges = c.soft(b, "GetEdges") or []
    print("     aristas en el primer cuerpo: %d" % len(edges))
    if edges:
        e = edges[0]
        cron("soft(edge,'GetCurve')      [ya evaluado]", lambda: c.soft(e, "GetCurve"), 50)
        cron("  ... el mismo via getattr directo", lambda: getattr(e, "GetCurve"), 50)

print("\n== trabajo real ==")
_, t_edges = cron("c.straight_edges(doc)", lambda: c.straight_edges(doc), 3)
aristas, _ = cron("  (recuento)", lambda: len(c.straight_edges(doc)))
cron("c.face_count(doc)", lambda: c.face_count(doc), 5)
cron("c.features_info(doc, 200)", lambda: c.features_info(doc, 200), 3)

print("\n== total del proceso ==")
print("  %-42s %8.1f ms" % ("desde el arranque", (time.perf_counter() - T0) * 1000))
if aristas:
    print("\n  straight_edges: %.1f ms para %d aristas = %.2f ms/arista"
          % (t_edges * 1000, aristas, t_edges * 1000 / max(aristas, 1)))
    print("  (cada arista son 6 llamadas COM: GetCurve, IsLine, 2 vertices, 2 puntos)")

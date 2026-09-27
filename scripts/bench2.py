#!/usr/bin/env python
"""
Segunda tanda: separa arranque en frio de regimen, y prueba si el enlace
anticipado (makepy) es viable en esta instalacion.

    python bench2.py

Hipotesis a validar: el coste no son las llamadas COM en si, sino que
pywin32 construye la representacion de tipos la primera vez que toca cada
interfaz. Si es asi, (a) la curva de calentamiento se vera, y (b) generar
el wrapper con makepy lo movera a disco y lo compartira entre procesos.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
import pythoncom
import win32com.client as w


def ms(t):
    return "%8.1f ms" % (t * 1000)


print("== curva de calentamiento de straight_edges ==")
import sw_core as c
c.co_init()
doc = c.active_doc()
for i in range(5):
    t = time.perf_counter()
    n = len(c.straight_edges(doc))
    print("  llamada %d %s   (%d aristas)" % (i + 1, ms(time.perf_counter() - t), n))

print("\n== de donde viene el frio ==")
bodies = doc.GetBodies2(0, True)
edges = c.soft(bodies[0], "GetEdges")
e0, e1 = edges[0], edges[1]
for etiqueta, obj in (("1a arista (interfaz nueva)", e0), ("2a arista (ya conocida)", e1)):
    t = time.perf_counter()
    c.soft(obj, "GetCurve")
    print("  GetCurve, %-28s %s" % (etiqueta, ms(time.perf_counter() - t)))
t = time.perf_counter()
for _ in range(20):
    c.soft(e1, "GetCurve")
print("  GetCurve, en regimen (media de 20)     %s" % ms((time.perf_counter() - t) / 20))

print("\n== enlace anticipado: se puede? ==")
try:
    from win32com.client import gencache
    t = time.perf_counter()
    mod = gencache.EnsureModule("{83A33D31-27C5-11CE-BFD4-00400513BB57}", 0, 22, 0)
    print("  EnsureModule OK en %s -> %s" % (ms(time.perf_counter() - t), mod.__name__))
    print("  gen_py en: %s" % os.path.dirname(mod.__file__))
except Exception as exc:
    print("  EnsureModule FALLA: %s: %s" % (type(exc).__name__, exc))
    mod = None

if mod is not None:
    print("\n== mismo trabajo con enlace anticipado ==")
    try:
        t = time.perf_counter()
        app2 = gencache.EnsureDispatch("SldWorks.Application")
        print("  EnsureDispatch %s" % ms(time.perf_counter() - t))
        doc2 = app2.ActiveDoc
        b2 = doc2.GetBodies2(0, True)[0]
        ed2 = b2.GetEdges()
        t = time.perf_counter()
        for x in ed2:
            cur = x.GetCurve()
            if cur.IsLine():
                a, bb = x.GetStartVertex(), x.GetEndVertex()
                if a and bb:
                    a.GetPoint(); bb.GetPoint()
        print("  recorrido de %d aristas %s   <- comparar con la curva de arriba"
              % (len(ed2), ms(time.perf_counter() - t)))
    except Exception as exc:
        print("  falla al usarlo: %s: %s" % (type(exc).__name__, exc))
        print("  (esto es lo que dice dump_api.py que pasa en SOLIDWORKS Connected)")

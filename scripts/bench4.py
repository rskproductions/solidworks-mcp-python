#!/usr/bin/env python
"""Comprueba la mejora y que el resultado no cambia. python bench4.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
import sw_core as c

def cron(etiqueta, fn):
    t = time.perf_counter(); r = fn(); dt = time.perf_counter() - t
    print("  %-44s %8.1f ms" % (etiqueta, dt * 1000)); return r, dt

c.co_init(); doc = c.active_doc()
print("== cuerpo entero ==")
todas, t1 = cron("straight_edges(doc)", lambda: c.straight_edges(doc))
print("     %d aristas rectas" % len(todas))

print("\n== acotando a la cara de Z=0 ==")
caras, t2 = cron("planar_faces(doc, 0.0)", lambda: c.planar_faces(doc, 0.0))
print("     %d cara(s) plana(s) en Z=0" % len(caras))
if caras:
    sub, t3 = cron("straight_edges(doc, face=cara)", lambda: c.straight_edges(doc, face=caras[0]))
    print("     %d aristas de esa cara" % len(sub))
    print("\n  cara + aristas: %.0f ms  frente a  %.0f ms del cuerpo entero  ->  x%.1f"
          % ((t2 + t3) * 1000, t1 * 1000, t1 / max(t2 + t3, 1e-9)))
    en_z0 = [e for e in todas if abs(e["p"][2]) < 1e-3 and abs(e["q"][2]) < 1e-3]
    a = {tuple(round(v,4) for v in e["p"]) for e in sub}
    b = {tuple(round(v,4) for v in e["p"]) for e in en_z0}
    print("  mismas aristas que filtrando el cuerpo entero por z=0: %s" % (a == b))

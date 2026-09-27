#!/usr/bin/env python
"""
Tercera tanda. El diagnostico ya esta: ~30 ms por llamada COM, y
straight_edges hace 6 por arista. Aqui se prueban las tres palancas
posibles, midiendo cada una por separado.

    python bench3.py

  A) menos llamadas: GetCurveParams2 da los dos extremos en UNA llamada,
     asi que 6 por arista bajan a 3.
  B) menos aristas: buscar primero la CARA y recorrer solo sus aristas.
  C) menos trabajo de SOLIDWORKS por llamada: apagar la actualizacion
     grafica y devolver el control al usuario.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
import sw_core as c

MM = 1000.0


def cron(etiqueta, fn):
    t = time.perf_counter()
    r = fn()
    dt = time.perf_counter() - t
    print("  %-46s %8.1f ms" % (etiqueta, dt * 1000))
    return r, dt


c.co_init()
doc = c.active_doc()
body = (doc.GetBodies2(0, True) or [None])[0]
if body is None:
    sys.exit("No hay cuerpos solidos.")
edges = c.soft(body, "GetEdges") or []
print("cuerpo con %d aristas\n" % len(edges))

print("== linea base ==")
base, t_base = cron("c.straight_edges(doc)   [6 llamadas/arista]",
                    lambda: c.straight_edges(doc))
print("     %d aristas rectas" % len(base))


# --- A) GetCurveParams2: extremos en una sola llamada ----------------------
def rectas_rapido():
    out = []
    for e in edges:
        cur = c.soft(e, "GetCurve")            # 1: obligatorio antes de params
        if cur is None:
            continue
        try:
            if not bool(c.soft(cur, "IsLine")):  # 2
                continue
        except Exception:
            continue
        p = c.soft(e, "GetCurveParams2")        # 3: 11 dobles de una tacada
        if not p:
            continue
        out.append({"edge": e,
                    "p": [v * MM for v in p[0:3]],
                    "q": [v * MM for v in p[3:6]]})
    return out


print("\n== A) menos llamadas por arista ==")
ra, t_a = cron("GetCurveParams2         [3 llamadas/arista]", rectas_rapido)
print("     %d aristas rectas  ->  x%.1f mas rapido" % (len(ra), t_base / max(t_a, 1e-9)))
if ra and base:
    m = {tuple(round(v, 4) for v in e["p"]) for e in base}
    n = {tuple(round(v, 4) for v in e["p"]) for e in ra}
    print("     mismos puntos de inicio que la linea base: %s" % (m == n))


# --- B) partir de la cara, no del cuerpo -----------------------------------
def aristas_de_cara_z(z_mm=0.0, tol=1e-3):
    """Aristas de las caras planas cuyo primer vertice esta en z. Recorre
    caras (pocas) en vez de todas las aristas del cuerpo."""
    caras = c.soft(body, "GetFaces") or []
    out = []
    for f in caras:
        fe = c.soft(f, "GetEdges") or []
        for e in fe:
            a = c.soft(e, "GetStartVertex")
            if a is None:
                continue
            pt = c.soft(a, "GetPoint")
            if abs(pt[2] * MM - z_mm) < tol:
                out.append(e)
        if out:
            break                      # con la primera cara que encaja basta
    return out


print("\n== B) partir de la cara ==")
rb, t_b = cron("caras -> aristas de la cara de Z=0", lambda: aristas_de_cara_z(0.0))
print("     %d aristas (frente a %d del cuerpo entero)" % (len(rb), len(edges)))


# --- C) apagar la actualizacion grafica ------------------------------------
print("\n== C) sin actualizacion grafica ==")
app = c.sw_app()
vista = c.soft(doc, "ActiveView")
try:
    app.UserControl = False
    if vista is not None:
        vista.EnableGraphicsUpdate = False
    _, t_c = cron("c.straight_edges(doc)   [graficos apagados]",
                  lambda: c.straight_edges(doc))
    print("     x%.1f frente a la linea base" % (t_base / max(t_c, 1e-9)))
finally:
    if vista is not None:
        vista.EnableGraphicsUpdate = True
    app.UserControl = True
    print("     (restaurado)")

print("\n== resumen ==")
print("  base            %8.1f ms" % (t_base * 1000))
print("  A (3 llamadas)  %8.1f ms" % (t_a * 1000))
print("  C (sin graficos)%8.1f ms" % (t_c * 1000))
print("  A y C se pueden combinar.")

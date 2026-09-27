"""
Comprobaciones de fabricabilidad para fresado de 3 ejes y exportacion para
el CAM. Perfil incluido: Makera Carvera Air (datos de makera.com, sep 2026).

SOLIDWORKS CAM no esta en todas las licencias (en la instalacion de pruebas
no esta; "Mastercam Direct" solo enlaza con Mastercam): aqui no se generan
trayectorias. Se comprueba la geometria y se exporta un STEP para Makera CAM,
que importa STEP/STL (3D) y DXF/SVG (2D).
"""

import math
import os

from . import core as c

MM = 1000.0

CARVERA_AIR = {
    "nombre": "Makera Carvera Air",
    "area_mm": (300.0, 200.0, 130.0),
    "husillo": "200 W, 0-13.000 rpm",
    # pinzas del Air (wiki, Air/Manual/toolkit): 1/8" de serie; 4, 6 y 1/4"
    # opcionales. La de 3 mm solo aparece para la Carvera C1.
    "fresas_mm": {"1/8\"": 3.175, "4 mm": 4.0, "6 mm": 6.0, "1/4\"": 6.35},
    "ld_max": 3.0,        # profundidad/diametro a partir de la cual avisar
    # Wiki de Makera (sep 2026). Cambio de herramienta MANUAL rapido (sin
    # almacen); herramienta < 76 mm de largo total; >= 10 mm entre la escuadra
    # en L y el inicio de la trayectoria; bridas superiores para piezas < 20 mm;
    # en metal la fresa de 3,175 corta 12 mm y se baja 0,1-0,2 mm por pasada.
    "filo_metal_mm": 12.0,
    "margen_escuadra_mm": 10.0,
    "grosor_max_bridas_sup_mm": 20.0,
}


def _cara_cilindrica(f, s):
    """(radio_mm, eje, centro_mm, concava, z0, z1) de una cara cilindrica.
    Concava = el material queda FUERA del cilindro (agujero, redondeo interior).
    OJO: EvaluateAtPoint da la normal en [0:3] y FaceInSurfaceSense=True
    significa normal de cara OPUESTA a la de la superficie."""
    p = list(s.CylinderParams)
    o, ax, r = p[0:3], p[3:6], p[6]
    box = list(f.GetBox)
    cx, cy, cz = [(box[i] + box[i + 3]) / 2 for i in range(3)]
    # direccion radial hacia el centro de la caja de la cara
    d = [cx - o[0], cy - o[1], cz - o[2]]
    k = sum(d[i] * ax[i] for i in range(3))
    d = [d[i] - k * ax[i] for i in range(3)]
    L = math.sqrt(sum(v * v for v in d)) or 1.0
    q = [o[i] + k * ax[i] + d[i] / L for i in range(3)]
    P = list(f.GetClosestPointOn(*q))[:3]
    ns = list(s.EvaluateAtPoint(*P))[0:3]
    sg = -1.0 if c.soft(f, "FaceInSurfaceSense") else 1.0
    rad = [P[i] - o[i] for i in range(3)]
    kk = sum(rad[i] * ax[i] for i in range(3))
    rad = [rad[i] - kk * ax[i] for i in range(3)]
    concava = sum(sg * ns[i] * rad[i] for i in range(3)) < 0
    return (r * MM, ax, [round(v * MM, 3) for v in o], concava,
            round(min(box[2], box[5]) * MM, 3), round(max(box[2], box[5]) * MM, 3))


def dfm_3ejes(doc, maquina=CARVERA_AIR, eje_herramienta=(0.0, 0.0, 1.0)):
    """Revisa la pieza activa para fresar desde +Z en una sola atada.

    Devuelve avisos concretos: tamano frente al area de trabajo, radios
    concavos verticales (fijan la fresa maxima), agujeros frente a fresas,
    esbeltez (profundidad/diametro) y caras que miran hacia abajo (piden
    voltear la pieza o 4.o eje)."""
    bb = c.overall_bbox(doc)
    tam = bb["size"]
    avisos = []
    area = maquina["area_mm"]
    cabe = all(tam[i] <= area[i] for i in range(3))
    if not cabe:
        avisos.append("La pieza (%s) no cabe en el area %s." % (tam, area))

    fresas = maquina["fresas_mm"]
    tz = eje_herramienta
    zmin = bb["min"][2]
    cilindros, abajo = [], []
    for body in doc.GetBodies2(0, False) or []:
        for f in c.soft(body, "GetFaces") or []:
            s = c.soft(f, "GetSurface")
            if c.soft(s, "IsCylinder"):
                r, ax, o, concava, z0, z1 = _cara_cilindrica(f, s)
                paralelo = abs(abs(sum(ax[i] * tz[i] for i in range(3))) - 1) < 1e-6
                cilindros.append({"r_mm": round(r, 3), "centro": o, "concava": concava,
                                  "vertical": paralelo, "z": [z0, z1]})
            elif c.soft(s, "IsPlane"):
                n = list(f.Normal)
                box = list(f.GetBox)
                if sum(n[i] * tz[i] for i in range(3)) < -1e-6 and abs(min(box[2], box[5]) * MM - zmin) > 1e-3:
                    abajo.append({"normal": [round(v, 3) for v in n],
                                  "z_mm": round(min(box[2], box[5]) * MM, 3)})

    conc = [x for x in cilindros if x["concava"] and x["vertical"]]
    rmin = min((x["r_mm"] for x in conc), default=None)
    validas = sorted(k for k, d in fresas.items() if rmin is None or d / 2 <= rmin + 1e-9)
    if rmin is not None and not validas:
        avisos.append("Radio concavo minimo %.2f mm: ninguna fresa disponible cabe." % rmin)

    # esbeltez: la zona concava mas profunda con la fresa mas gruesa que cabe
    if conc and validas:
        dmax = max(fresas[k] for k in validas)
        prof = max(x["z"][1] - x["z"][0] for x in conc)
        if prof / dmax > maquina["ld_max"]:
            avisos.append("Profundidad %.1f mm con fresa de %.2f mm: L/D %.1f > %.1f; "
                          "comprueba la longitud de corte." % (prof, dmax, prof / dmax, maquina["ld_max"]))
    agujeros = {}
    for x in conc:
        agujeros.setdefault(round(x["r_mm"] * 2, 3), []).append(x["centro"])
    for d in sorted(agujeros):
        cab = [k for k, v in fresas.items() if v < d - 1e-9]
        if not cab:
            avisos.append("Concavo de diametro %.2f mm: ninguna fresa es mas fina; taladrar." % d)
        elif max(fresas[k] for k in cab) > d - 0.3:
            avisos.append("Diametro %.2f mm con fresa de %.3f mm: holgura < 0,15 mm por lado; "
                          "mejor fresa mas fina o broca." % (d, max(fresas[k] for k in cab)))
    if tam[2] >= maquina.get("grosor_max_bridas_sup_mm", 1e9):
        avisos.append("Grosor %.1f mm: las bridas superiores del Air son para piezas de menos de "
                      "%.0f mm; usar escuadra en L o calzos." % (tam[2], maquina["grosor_max_bridas_sup_mm"]))
    if conc and maquina.get("filo_metal_mm"):
        prof = max(x["z"][1] - x["z"][0] for x in conc)
        if prof >= maquina["filo_metal_mm"]:
            avisos.append("Zona concava de %.1f mm de profundidad: en metal la fresa de 3,175 corta "
                          "%.0f mm (sin margen); en madera/plastico hay fresas de hasta 42 mm."
                          % (prof, maquina["filo_metal_mm"]))
    if abajo:
        avisos.append("%d cara(s) miran hacia -Z por encima de la base: hace falta "
                      "voltear la pieza (segunda atada) o 4.o eje." % len(abajo))
    no_verticales = [x for x in cilindros if x["concava"] and not x["vertical"]]
    if no_verticales:
        avisos.append("%d cilindro(s) concavo(s) no verticales: no se hacen desde +Z." % len(no_verticales))

    return {
        "maquina": maquina["nombre"],
        "tamano_mm": tam,
        "cabe": cabe,
        "radio_concavo_min_mm": rmin,
        "fresas_que_caben": validas,
        "diametros_concavos_mm": {d: len(v) for d, v in agujeros.items()},
        "caras_hacia_abajo": abajo,
        "una_atada": not abajo and not no_verticales,
        "avisos": avisos,
    }


def export_cam(doc, carpeta, nombre=None):
    """STEP (AP214, lleva las unidades dentro) para Makera CAM."""
    os.makedirs(carpeta, exist_ok=True)
    base = nombre or os.path.splitext(doc.GetTitle)[0]
    ruta = os.path.join(carpeta, base + ".step")
    r = doc.SaveAs3(ruta, 0, 0)
    return {"step": ruta, "resultado": r, "existe": os.path.exists(ruta)}

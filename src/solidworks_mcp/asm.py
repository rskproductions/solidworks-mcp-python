"""
Ayudas de ENSAMBLAJE sobre core: localizar componentes y caras por
geometria (en coordenadas LOCALES de la pieza) y crear relaciones de posicion.

Hechos comprobados (24-09-2026, SOLIDWORKS Connected R2026x, pywin32 tardio):
  - Component2.GetBodies2 da cuerpos cuya geometria (CylinderParams, GetBox
    de cara) esta en coordenadas de la PIEZA, no del ensamblaje: las caras se
    buscan con las cotas del modelo de la pieza.
  - AddMate5: las dos entidades seleccionadas con mark 1 (Select4 sobre el
    handle de la cara); ErrorStatus es [out] -> c.byref_int().
    swAddMateError_e: 1 = sin error.
  - Tras la relacion, el nombre se lee del ultimo subfeature del MateGroup
    (AddMate5 devuelve un Mate2 sin nombre).
"""

import math

from . import core as c

MATE = {"coincident": 0, "concentric": 1, "distance": 5, "gear": 10}
ALIGN = {"aligned": 0, "anti": 1, "closest": 2}
ERR_OK = 1


def components(asm):
    return list(asm.GetComponents(True) or [])


def comp_translation_mm(comp):
    a = list(c.soft(comp.Transform2, "ArrayData"))
    return [v * c.MM for v in a[9:12]]


def comp_at(asm, pieza, x, y, z, tol=0.5):
    """Instancia de `pieza` cuyo origen (traslacion) esta en (x, y, z) mm."""
    mejor, dmin = None, 1e9
    for comp in components(asm):
        ruta = c.soft(comp, "GetPathName") or ""
        if not ruta.lower().endswith(("\\" + pieza + ".sldprt").lower()):
            continue
        t = comp_translation_mm(comp)
        d = math.dist(t, (x, y, z))
        if d < dmin:
            mejor, dmin = comp, d
    if mejor is None or dmin > tol:
        raise LookupError("No hay instancia de %s en (%.3f, %.3f, %.3f) (mas cercana a %.3f mm)"
                          % (pieza, x, y, z, dmin))
    return mejor


_CACHE = {}


def _faces(comp, feature=None):
    """Caras con su geometria ya leida: [(cara, tipo, params, caja_mm)], cacheado.

    Cada llamada COM cuesta ~30 ms: recorrer las ~930 caras de una carcasa
    son minutos. Con feature="Op3" se leen solo las caras de ESA operacion
    (Component2.FeatureByName -> IFeature.GetFaces), que son unas pocas.
    """
    key = (comp.Name2, feature)
    if key in _CACHE:
        return _CACHE[key]
    if feature:
        f = comp.FeatureByName(feature)
        if f is None:
            raise LookupError("%s no tiene la operacion %s" % (comp.Name2, feature))
        caras = list(c.soft(f, "GetFaces") or [])
    else:
        caras = [f for b in comp.GetBodies2(0) or [] for f in (c.soft(b, "GetFaces") or [])]
    out = []
    for f in caras:
        s = c.soft(f, "GetSurface")
        bx = [v * c.MM for v in c.soft(f, "GetBox")]
        if s is not None and bool(c.soft(s, "IsCylinder")):
            out.append((f, "cyl", c.soft(s, "CylinderParams"), bx))
        elif abs(bx[2] - bx[5]) < 1e-6:
            out.append((f, "plane?", None, bx))
        else:
            out.append((f, "otra", None, bx))
    _CACHE[key] = out
    return out


def clear_cache():
    _CACHE.clear()


def cyl_face(comp, r, cx=0.0, cy=0.0, zmin=None, zmax=None, tol=0.01, feature=None):
    """Cara cilindrica de radio r (mm) y eje paralelo a Z por (cx, cy), en
    coordenadas de la pieza. zmin/zmax acotan si hay varias."""
    cand = []
    for f, tipo, p, bx in _faces(comp, feature):
        if tipo != "cyl":
            continue
        if abs(p[6] * c.MM - r) > tol or abs(abs(p[5]) - 1.0) > 1e-6:
            continue
        if math.hypot(p[0] * c.MM - cx, p[1] * c.MM - cy) > tol:
            continue
        if zmin is not None and bx[5] < zmin + tol:
            continue
        if zmax is not None and bx[2] > zmax - tol:
            continue
        cand.append((bx[5] - bx[2], f))
    if not cand:
        raise LookupError("Sin cilindro r=%.3f en (%.3f, %.3f) z[%s, %s] en %s%s"
                          % (r, cx, cy, zmin, zmax, comp.Name2, " / " + feature if feature else ""))
    return max(cand, key=lambda t: t[0])[1]


def plane_face(comp, z, cx=None, cy=None, tol=0.01, feature=None):
    """Cara plana normal a Z en la cota z (mm, coordenadas de la pieza). Si hay
    varias, la de centro de caja mas cercano a (cx, cy); sin (cx, cy), la mayor."""
    cand = []
    for f, tipo, p, bx in _faces(comp, feature):
        if tipo != "plane?" or abs(bx[2] - z) > tol:
            continue
        centro = ((bx[0] + bx[3]) / 2, (bx[1] + bx[4]) / 2)
        area = (bx[3] - bx[0]) * (bx[4] - bx[1])
        d = math.hypot(centro[0] - cx, centro[1] - cy) if cx is not None else -area
        cand.append((d, f))
    if not cand:
        raise LookupError("Sin cara plana en z=%.3f cerca de (%s, %s) en %s%s"
                          % (z, cx, cy, comp.Name2, " / " + feature if feature else ""))
    return min(cand, key=lambda t: t[0])[1]


def last_mate_name(asm):
    ultimo = None
    f = asm.FirstFeature
    while f is not None:
        if f.GetTypeName2 == "MateGroup":
            s = c.soft(f, "GetFirstSubFeature")
            while s is not None:
                ultimo = s.Name
                s = c.soft(s, "GetNextSubFeature")
        f = f.GetNextFeature
    return ultimo


def add_mate(asm, tipo, ent_a, ent_b, align="closest", lock_rotation=False,
             distance_mm=0.0, ratio=(0.0, 0.0), name=None):
    """Relacion de posicion entre dos entidades (caras) ya localizadas."""
    asm.ClearSelection2(True)
    sd = c.soft(asm.SelectionManager, "CreateSelectData")
    sd.Mark = 1
    if not ent_a.Select4(True, sd) or not ent_b.Select4(True, sd):
        asm.ClearSelection2(True)
        raise RuntimeError("Select4 fallo en alguna de las entidades de la relacion %s" % tipo)
    err = c.byref_int()
    d = c.mm(distance_mm)
    mate = asm.AddMate5(MATE[tipo], ALIGN[align], False, d, d, d,
                        float(ratio[0]), float(ratio[1]), 0.0, 0.0, 0.0,
                        False, bool(lock_rotation), 0, err)
    asm.ClearSelection2(True)
    if mate is None or err.value != ERR_OK:
        raise RuntimeError("AddMate5(%s) fallo: ErrorStatus=%s (swAddMateError_e)" % (tipo, err.value))
    nombre = last_mate_name(asm)
    if name and nombre:
        f = asm.FeatureByName(nombre)
        if f is not None:
            try:
                f.Name = name
                nombre = name
            except Exception:
                pass
    return nombre


def set_fixed(asm, comps, fixed):
    asm.ClearSelection2(True)
    sd = c.soft(asm.SelectionManager, "CreateSelectData")
    for comp in comps:
        comp.Select4(True, sd, False)
    c.call_method(asm, "FixComponent" if fixed else "UnfixComponent")
    asm.ClearSelection2(True)


def mate_errors(asm):
    """[(relacion, codigo)] de las relaciones con error (swFeatureError_e)."""
    out = []
    f = asm.FirstFeature
    while f is not None:
        if f.GetTypeName2 == "MateGroup":
            s = c.soft(f, "GetFirstSubFeature")
            while s is not None:
                warn = c.w.VARIANT(c.pythoncom.VT_BOOL | c.pythoncom.VT_BYREF, False)
                code = s.GetErrorCode2(warn)
                if code:
                    out.append((s.Name, code, bool(warn.value)))
                s = c.soft(s, "GetNextSubFeature")
        f = f.GetNextFeature
    return out


def interferences(asm, coincidencia=False, ignorar_ocultos=True):
    """Deteccion de interferencias agrupada por PAREJA de componentes.

    IAssemblyDoc.InterferenceDetectionManager (propiedad; el documento tiene
    que ser el ENSAMBLAJE activo). Sin agrupar sale una entrada por cada
    trocito: en OdomeKron fueron 201, todas de 0,000-0,006 mm3 entre dientes
    de correa y de polea. Tarda: ~40 s con 22 componentes y una carcasa de
    ~930 caras (cuenta con el limite de 60 s de una llamada MCP)."""
    mgr = asm.InterferenceDetectionManager
    mgr.TreatCoincidenceAsInterference = bool(coincidencia)
    mgr.IgnoreHiddenBodies = bool(ignorar_ocultos)
    mgr.TreatSubAssembliesAsComponents = True
    try:
        ints = mgr.GetInterferences or []
        parejas = {}
        for it in ints:
            nombres = tuple(sorted(x.Name2 for x in (it.Components or [])))
            p = parejas.setdefault(nombres, {"componentes": list(nombres), "n": 0,
                                             "volumen_mm3": 0.0, "max_mm3": 0.0})
            v = it.Volume * 1e9
            p["n"] += 1
            p["volumen_mm3"] += v
            p["max_mm3"] = max(p["max_mm3"], v)
    finally:
        mgr.Done()
    out = sorted(parejas.values(), key=lambda p: -p["volumen_mm3"])
    for p in out:
        p["volumen_mm3"] = round(p["volumen_mm3"], 4)
        p["max_mm3"] = round(p["max_mm3"], 4)
    return {"interferencias": len(ints), "parejas": out}

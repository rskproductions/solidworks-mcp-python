"""
Operaciones de pieza mas alla de la extrusion, probadas una a una contra
SOLIDWORKS 2026 (rev 34.3) el 27-09-2026 y verificadas por VOLUMEN frente al
valor teorico (ver scripts/probetas.py). Todo en mm salvo que se diga.

Hechos comprobados que no estan (o no se ven) en la ayuda:
  - La plantilla de pieza por defecto viene en METROS (MKS): un DXF de chapa
    sale en metros y un programa de corte lo leeria 1000 veces pequeno.
    units_mm(doc) lo arregla; new_part() ya lo aplica.
  - Revolucion: con una sola linea constructiva en el croquis, basta
    seleccionar el croquis (marca 0); SOLIDWORKS la toma como eje.
  - Taladro del asistente: HoleWizard5 devolvio None en todas las
    combinaciones probadas. La via buena es CreateDefinition(swFmHoleWzd) +
    InitializeHole + seleccionar la cara con SelectByRay (el punto del rayo
    es la posicion del taladro) + CreateFeature. HoleDiameter lee 0 tras
    InitializeHole: no sirve para comprobar nada.
  - Simetria de operaciones: primero las operaciones (marca 1) y DESPUES el
    plano (marca 2); al reves falla. Un Nervio no se deja simetrizar como
    operacion (None): usar simetria de CUERPO (marca 256, BMirrorBody=True).
  - Croquis en "Planta": x = X del modelo, y = -Z. Usar sketch_point(), que
    pasa por ModelToSketchTransform, en vez de adivinar ejes.
  - IMathUtility.CreatePoint necesita VARIANT(VT_ARRAY|VT_R8); con una lista
    de Python da puntos sin sentido.
  - Ecuaciones: "Espesor" como nombre de variable global falla (Add2 = -1),
    seguramente porque choca con una palabra reservada de chapa en espanol.
  - ISurface.EvaluateAtPoint devuelve la NORMAL en los indices 0..2, y
    IFace2.FaceInSurfaceSense = True significa que cara y superficie apuntan
    en sentidos OPUESTOS (asi lo dice la ayuda, al reves de lo que sugiere
    el nombre).
  - Brida de arista (chapa): CreateDefinition(swFmEdgeFlange) con solo Edge,
    BendAngle y OffsetDistance devuelve None. Pendiente
    (InsertSketchForEdgeFlange + perfil).
"""

import math

import pythoncom
from win32com.client import VARIANT

from . import core as c
from .const import const

MM = 1000.0


def _m(v):
    return float(v) / MM


def _arr(vals, vt=pythoncom.VT_R8):
    return VARIANT(pythoncom.VT_ARRAY | vt, list(vals))


# ------------------------------------------------------------------ documento

def units_mm(doc):
    """Sistema MMGS (mm, g, s): afecta a cotas y a DXF/DWG exportados.
    Solo se fija el sistema: si ademas se fija swUnitsLinear, SOLIDWORKS lo
    pasa a "Personalizado" (swUnitSystem = 4), comprobado."""
    ext = doc.Extension
    ext.SetUserPreferenceInteger(const("swUnitSystem"), 0, const("swUnitSystem_MMGS"))
    return {"sistema": ext.GetUserPreferenceInteger(const("swUnitSystem"), 0),
            "lineal": ext.GetUserPreferenceInteger(const("swUnitsLinear"), 0)}


def new_part(mm_units=True):
    app = c.sw_app()
    tpl = app.GetUserPreferenceStringValue(const("swDefaultTemplatePart"))
    app.NewDocument(tpl, 0, 0.0, 0.0)
    doc = app.ActiveDoc
    if mm_units:
        units_mm(doc)
    return doc


def mass_props(doc):
    """Volumen (mm3), area (mm2), masa (g), densidad (kg/m3), cdg (mm)."""
    mp = c.soft(doc.Extension, "CreateMassProperty2")
    return {
        "volumen_mm3": round(mp.Volume * 1e9, 3),
        "area_mm2": round(mp.SurfaceArea * 1e6, 3),
        "masa_g": round(mp.Mass * 1e3, 3),
        "densidad": round(mp.Density, 3),
        "cdg_mm": [round(v * MM, 4) for v in mp.CenterOfMass],
    }


def set_material(doc, nombre="6061-T6 (SS)", base="SOLIDWORKS Materials", config=None):
    """Nombres tal como estan en el .sldmat (sw.GetMaterialDatabases). En la
    instalacion en espanol hay nombres con acentos ("Aleacion 6061"); los de
    la serie "(SS)" son ASCII y viajan mejor por COM."""
    cfg = config or doc.ConfigurationManager.ActiveConfiguration.Name
    doc.SetMaterialPropertyName2(cfg, base, nombre)
    return mass_props(doc)


def last_feature(doc):
    return doc.FeatureByPositionReverse(0)


def features_of_type(doc, tipo):
    out, f = [], doc.FirstFeature
    while f is not None:
        if f.GetTypeName2 == tipo:
            out.append(f)
        f = f.GetNextFeature
    return out


# ------------------------------------------------------------------ seleccion

def select_face_ray(doc, origen_mm, direccion, append=False, mark=0, radio_mm=0.5):
    """Selecciona la primera CARA que corta el rayo. El punto de impacto queda
    como punto de seleccion (lo usan el asistente de taladro y los croquis)."""
    x, y, z = (_m(v) for v in origen_mm)
    return bool(doc.Extension.SelectByRay(x, y, z, float(direccion[0]), float(direccion[1]),
                                          float(direccion[2]), _m(radio_mm), 2, append, mark, 0))


def sketch_point(sketch, x, y, z):
    """Punto del modelo (mm) -> coordenadas del croquis (m), para CreateLine & co."""
    mu = c.sw_app().GetMathUtility
    p = c.call_method(mu, "CreatePoint", _arr([_m(x), _m(y), _m(z)]))
    p = p.MultiplyTransform(sketch.ModelToSketchTransform)
    return list(p.ArrayData)[:2]


def _close_sketch(doc):
    sm = doc.SketchManager
    sm.AddToDB = False
    sm.InsertSketch(True)
    return last_feature(doc)


def sketch_on_plane(doc, plane, dibujar):
    """Croquis en un plano (indice 0..2 o feature de plano). dibujar(sm, sk)
    crea las entidades; devuelve la feature del croquis."""
    if isinstance(plane, int):
        c.select_plane(doc, plane)
    else:
        doc.ClearSelection2(True)
        plane.Select2(False, 0)
    sm = doc.SketchManager
    sm.AddToDB = True
    sm.InsertSketch(True)
    dibujar(sm, sm.ActiveSketch)
    return _close_sketch(doc)


def sketch_on_face(doc, origen_mm, direccion, dibujar):
    """Croquis sobre la cara que corta el rayo."""
    doc.ClearSelection2(True)
    if not select_face_ray(doc, origen_mm, direccion):
        raise RuntimeError("El rayo %s -> %s no corta ninguna cara." % (origen_mm, direccion))
    sm = doc.SketchManager
    sm.AddToDB = True
    sm.InsertSketch(True)
    dibujar(sm, sm.ActiveSketch)
    return _close_sketch(doc)


def _check(f, que):
    if f is None:
        raise RuntimeError("%s fallida (la API devolvio None)." % que)
    return f


# ------------------------------------------------------------------ operaciones

def revolve(doc, sketch, angulo_deg=360.0, cut=False, merge=True):
    """Revolucion del croquis alrededor de su (unica) linea constructiva."""
    doc.ClearSelection2(True)
    sketch.Select2(False, 0)
    f = doc.FeatureManager.FeatureRevolve2(
        True, True, False, bool(cut), False, False,
        const("swEndCondBlind"), 0, math.radians(angulo_deg), 0.0,
        False, False, 0.0, 0.0, 0, 0.0, 0.0, bool(merge), True, True)
    return _check(f, "Revolucion")


def shell(doc, cara_rayo, espesor_mm, hacia_fuera=False):
    """Vaciado quitando la cara que corta el rayo (origen, direccion)."""
    doc.ClearSelection2(True)
    if not select_face_ray(doc, *cara_rayo):
        raise RuntimeError("El rayo no corta ninguna cara para el vaciado.")
    doc.InsertFeatureShell(_m(espesor_mm), bool(hacia_fuera))
    f = last_feature(doc)
    if f.GetTypeName2 != "Shell":
        raise RuntimeError("Vaciado fallido.")
    return f


def hole_wizard(doc, cara_rayo, size="M5", tipo="swWzdHole", norma="swStandardISO",
                fijacion="swStandardISOScrewClearances", fin="swEndCondThroughAll", prof_mm=None):
    """Taladro del asistente en el punto donde el rayo corta la cara."""
    fm = doc.FeatureManager
    fd = fm.CreateDefinition(const("swFmHoleWzd"))
    fd.InitializeHole(const(tipo), const(norma), const(fijacion), size, const(fin))
    if prof_mm is not None:
        fd.HoleDepth = _m(prof_mm)
    doc.ClearSelection2(True)
    if not select_face_ray(doc, *cara_rayo):
        raise RuntimeError("El rayo no corta ninguna cara para el taladro.")
    return _check(fm.CreateFeature(fd), "Taladro del asistente")


def circular_pattern(doc, feats, eje_rayo, n, angulo_total_deg=360.0):
    """Matriz circular. El eje es la cara cilindrica (o arista) que corta el rayo."""
    doc.ClearSelection2(True)
    if not select_face_ray(doc, *eje_rayo, mark=1):
        raise RuntimeError("El rayo no corta la cara que define el eje.")
    for f in feats:
        f.Select2(True, 4)
    f = doc.FeatureManager.FeatureCircularPattern5(
        int(n), math.radians(angulo_total_deg), False, "NULL", False, True, False, False,
        False, False, 1, 0.0, "NULL", False)
    return _check(f, "Matriz circular")


def linear_pattern(doc, feats, arista_mm, n, paso_mm, invertir=False):
    """Matriz lineal en la direccion de la arista que pasa por arista_mm."""
    doc.ClearSelection2(True)
    x, y, z = (_m(v) for v in arista_mm)
    if not doc.Extension.SelectByID2("", "EDGE", x, y, z, False, 1, c.null_dispatch(), 0):
        raise RuntimeError("No hay arista en %s para la direccion." % (arista_mm,))
    for f in feats:
        f.Select2(True, 4)
    f = doc.FeatureManager.FeatureLinearPattern5(
        int(n), _m(paso_mm), 1, 0.0, bool(invertir), False, "NULL", "NULL", False, False,
        False, False, False, False, False, False, False, False, 0.0, 0.0, False, False)
    return _check(f, "Matriz lineal")


def mirror_features(doc, feats, plano):
    """Simetria de operaciones: operaciones (marca 1) ANTES que el plano (2)."""
    doc.ClearSelection2(True)
    for f in feats:
        f.Select2(True, 1)
    plano.Select2(True, 2)
    return _check(doc.FeatureManager.InsertMirrorFeature2(False, False, False, False, 0),
                  "Simetria de operaciones")


def mirror_body(doc, plano, cuerpo=None, merge=True):
    """Simetria del cuerpo entero (marca 256). Sirve cuando una operacion
    (p.ej. un nervio) no se deja simetrizar."""
    cuerpo = cuerpo or doc.GetBodies2(0, True)[0]
    doc.ClearSelection2(True)
    sd = doc.SelectionManager.CreateSelectData
    sd.Mark = 256
    cuerpo.Select2(False, sd)
    plano.Select2(True, 2)
    return _check(doc.FeatureManager.InsertMirrorFeature2(True, False, bool(merge), False, 0),
                  "Simetria de cuerpo")


def draft(doc, neutro_rayo, caras_rayos, angulo_deg, invertir=False):
    """Angulo de salida: plano neutro (marca 1) y caras a inclinar (marca 2)."""
    doc.ClearSelection2(True)
    if not select_face_ray(doc, *neutro_rayo, mark=1):
        raise RuntimeError("El rayo no corta el plano neutro.")
    for r in caras_rayos:
        if not select_face_ray(doc, *r, append=True, mark=2):
            raise RuntimeError("Un rayo de cara a inclinar no corta nada.")
    f = doc.FeatureManager.InsertMultiFaceDraft(math.radians(angulo_deg), bool(invertir),
                                               False, 0, False, False)
    return _check(f, "Angulo de salida")


def rib(doc, sketch, espesor_mm, dos_lados=True):
    """Nervio desde un croquis abierto (una linea)."""
    doc.ClearSelection2(True)
    sketch.Select2(False, 0)
    doc.FeatureManager.InsertRib(bool(dos_lados), False, _m(espesor_mm), 0, False, False,
                                 False, 0.0, False, False)
    f = last_feature(doc)
    if f.GetTypeName2 != "Rib":
        raise RuntimeError("Nervio fallido.")
    return f


def ref_plane_offset(doc, plano, dist_mm, invertir=False):
    """Plano paralelo a otro a una distancia (invertir = al otro lado)."""
    doc.ClearSelection2(True)
    plano.Select2(False, 0)
    cons = const("swRefPlaneReferenceConstraint_Distance")
    if invertir:
        cons |= const("swRefPlaneReferenceConstraint_OptionFlip")
    return _check(doc.FeatureManager.InsertRefPlane(cons, _m(dist_mm), 0, 0.0, 0, 0.0),
                  "Plano de referencia")


def loft(doc, sketches, merge=True):
    """Recubrimiento entre perfiles (marca 1, en orden)."""
    doc.ClearSelection2(True)
    for i, s in enumerate(sketches):
        s.Select2(i > 0, 1)
    f = doc.FeatureManager.InsertProtrusionBlend2(
        False, True, False, 1.0, 0, 0, 1.0, 1.0, True, True, False, 0.0, 0.0, 0,
        bool(merge), True, True, 0)
    return _check(f, "Recubrimiento")


def sweep(doc, perfil, trayecto, merge=True):
    """Barrido: perfil (marca 1) a lo largo del trayecto (marca 4)."""
    doc.ClearSelection2(True)
    perfil.Select2(False, 1)
    trayecto.Select2(True, 4)
    f = doc.FeatureManager.InsertProtrusionSwept4(
        False, True, 0, False, False, 0, 0, False, 0.0, 0.0, 0, 0, bool(merge), True, True,
        0.0, True, False, 0.0, 0)
    return _check(f, "Barrido")


# ------------------------------------------------------------------ chapa

def sheet_base_flange(doc, lineas_mm, espesor_mm, radio_mm, largo_mm, plano=0):
    """Brida base desde un perfil ABIERTO de lineas [(x0,y0,x1,y1), ...] en
    coordenadas del croquis del plano (mm). El croquis se deja abierto: la
    llamada lo consume."""
    sm, _ = c.begin_sketch(doc, plano)
    for x0, y0, x1, y1 in lineas_mm:
        sm.CreateLine(_m(x0), _m(y0), 0.0, _m(x1), _m(y1), 0.0)
    c.end_sketch(sm)
    f = doc.FeatureManager.InsertSheetMetalBaseFlange2(
        _m(espesor_mm), False, _m(radio_mm), _m(largo_mm), 0.0, False, 0, 0, 1,
        c.null_dispatch(), True, 2, 0.0001, 0.0001, 0.5, True, False, True, True)
    if f is None:
        c.close_dangling_sketch(doc)
    return _check(f, "Brida base")


def export_flat_dxf(doc, ruta_dxf, lineas_pliegue=True):
    """Desarrollo de chapa a DXF (en mm si units_mm se aplico). Devuelve la
    caja del desarrollo leida del propio DXF ($EXTMIN/$EXTMAX)."""
    units_mm(doc)
    modelo = doc.GetPathName
    if not modelo:
        raise RuntimeError("Guarda la pieza antes de exportar el desarrollo.")
    opciones = 1 | (4 if lineas_pliegue else 0)   # bit1 geometria, bit3 lineas de pliegue
    ok = doc.ExportToDWG2(ruta_dxf, modelo, 1, True, _arr([0.0] * 12), False, False,
                          opciones, c.null_dispatch())
    if not ok:
        raise RuntimeError("ExportToDWG2 fallo.")
    txt = open(ruta_dxf, encoding="latin-1").read()

    def cab(nombre):
        i = txt.find(nombre)
        seg = txt[i:i + 200].split("\n")
        return [float(seg[k + 1]) for k in range(len(seg) - 1) if seg[k].strip() in ("10", "20")][:2]
    lo, hi = cab("$EXTMIN"), cab("$EXTMAX")
    return {"dxf": ruta_dxf, "min": lo, "max": hi,
            "tamano": [round(hi[i] - lo[i], 4) for i in range(2)]}


# ------------------------------------------------------------------ datos de ingenieria

def add_equations(doc, ecuaciones):
    """Anade ecuaciones/variables globales ('"L" = 60', '"D1@Placa" = "L"').
    Devuelve el indice de cada una (-1 = rechazada)."""
    em = doc.GetEquationMgr
    res = [em.Add2(-1, e, True) for e in ecuaciones]
    doc.EditRebuild3
    return res


def equations(doc):
    em = doc.GetEquationMgr
    return [(em.Equation(i), round(em.Value(i), 6)) for i in range(em.GetCount)]


def add_configuration(doc, nombre, descripcion="", ecuaciones=None):
    """Crea una configuracion (queda activa) y le fija valores propios de
    variables globales: ecuaciones = {'"L"': 80, ...}."""
    cm = doc.ConfigurationManager
    cfg = _check(cm.AddConfiguration2(nombre, "", "", 0, "", descripcion, True), "Configuracion")
    em = doc.GetEquationMgr
    for var, val in (ecuaciones or {}).items():
        idx = [i for i in range(em.GetCount) if em.Equation(i).split("=")[0].strip() == var]
        if not idx:
            raise KeyError("No hay ecuacion para %s" % var)
        em.SetEquationAndConfigurationOption(idx[0], "%s = %s" % (var, val),
                                             const("swSpecifyConfiguration"),
                                             _arr([nombre], pythoncom.VT_BSTR))
    doc.EditRebuild3
    return cfg

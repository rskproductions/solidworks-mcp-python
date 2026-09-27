"""
Capa COM para SOLIDWORKS (Connected / 3DEXPERIENCE R2026x).

Reglas comprobadas que respeta este módulo:
  - Solo enlace tardío: win32com.client.Dispatch, nunca gencache.EnsureDispatch.
  - Propiedades sin parentesis (ActiveDoc, GetTitle, FirstFeature, IsSuppressed).
  - GetBodyBox() SI es metodo.
  - La API trabaja en METROS; aqui todo entra en mm y se convierte.
  - Nunca pasar enteros donde la API espera double: 0.0, no 0.
  - FeatureExtrusion2 con inicio desplazado: T0=3 + StartOffset.

Ninguna funcion de este modulo escribe en stdout (lo usa el transporte MCP).
"""

import math
import os
import re

import pythoncom
import win32com.client as w

MM = 1000.0


_SOFT_CACHE = {}    # (interfaz, miembro) -> "llamada" | "valor"


def soft(obj, name, *args):
    """Enlace tardio de pywin32: un mismo miembro de cero argumentos llega unas
    veces como metodo y otras ya evaluado. Peor aun, si llega evaluado como
    objeto COM (IEdge.GetCurve, IEdge.GetStartVertex...) sigue siendo callable,
    asi que `callable()` no basta: hay que intentar la llamada y, si COM dice
    que no hay miembro, quedarse con el valor.

        soft(edge, "GetCurve")          en vez de  edge.GetCurve()
        soft(body, "GetEdges") or []    en vez de  body.GetEdges()
    """
    m = getattr(obj, name)
    if not callable(m):
        return m
    if args:
        return m(*args)                 # con argumentos no hay ambiguedad

    # Sin memoria, cada miembro "ya evaluado" costaba una com_error por
    # llamada, y construir esa excepcion en pywin32 no es barato: en un
    # recorrido de aristas son miles. Se recuerda que estrategia funciono
    # para (interfaz, miembro). Si la recordada falla, se prueba la otra y
    # se corrige: la memoria acelera, no decide.
    key = (getattr(obj, "_username_", None) or obj.__class__.__name__, name)
    if _SOFT_CACHE.get(key) == "valor":
        return m
    try:
        r = m()
        _SOFT_CACHE[key] = "llamada"
        return r
    except pythoncom.com_error:
        _SOFT_CACHE[key] = "valor"
        return m


def mm(v):
    """mm -> metros, siempre float."""
    return float(v) / MM


# --------------------------------------------------------------------------
# conexion
# --------------------------------------------------------------------------

_sw = None


def co_init():
    pythoncom.CoInitialize()


def sw_app(create=False):
    """Devuelve la aplicacion SOLIDWORKS. Adjunta a la instancia en marcha.

    Si SOLIDWORKS se cerro o se colgo desde la ultima llamada, el objeto
    cacheado es un puntero COM muerto: cualquier acceso da "El servidor RPC no
    esta disponible" y el servidor MCP se quedaba inutil hasta reiniciarlo
    (visto el 23-09-2026). Se comprueba con una ida y vuelta barata y, si
    falla, se vuelve a adjuntar; Dispatch arranca SOLIDWORKS si no esta abierto.
    """
    global _sw
    if _sw is not None:
        try:
            _sw.RevisionNumber
        except Exception:
            _sw = None
    if _sw is None:
        _sw = w.Dispatch("SldWorks.Application")
        _sw.Visible = True
    return _sw


def call_method(obj, name, *args):
    """Invoca un METODO COM forzando DISPATCH_METHOD.

    El enlace tardio de pywin32 invoca con DISPATCH_METHOD|DISPATCH_PROPERTYGET
    y algunos miembros de SOLIDWORKS lo rechazan con "No se ha encontrado el
    miembro" aunque existan (visto con IMathUtility.CreateTransform, 24-09-2026:
    GetIDsOfNames lo encuentra, la llamada normal falla, Invoke con
    DISPATCH_METHOD funciona). Devuelve un CDispatch si el resultado es un objeto.
    """
    dispid = obj._oleobj_.GetIDsOfNames(name)
    r = obj._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_METHOD, True, *args)
    if hasattr(r, "GetTypeInfoCount"):              # PyIDispatch
        return w.Dispatch(r)
    return r


def put_property(obj, name, *args):
    """Asigna una PROPIEDAD CON PARAMETROS: put_property(tabla, "Text", fila, col, "texto").

    pywin32 tardio no sabe asignar propiedades con argumentos
    (ITableAnnotation.Text(Row, Column) = ... -> AttributeError 'SetText'):
    se invoca DISPATCH_PROPERTYPUT a mano; el ultimo argumento es el valor.
    """
    dispid = obj._oleobj_.GetIDsOfNames(name)
    return obj._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, False, *args)


def math_transform(rot9, trans_m, scale=1.0):
    """IMathTransform desde rotacion (9, convencion SOLIDWORKS: fila = imagen de
    cada eje, p' = p . R + T) y traslacion en METROS."""
    arr = [float(x) for x in rot9] + [float(x) for x in trans_m] + [float(scale), 0.0, 0.0, 0.0]
    mu = soft(sw_app(), "GetMathUtility")
    return call_method(mu, "CreateTransform", w.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, arr))


def byref_int(v=0):
    """Parametro COM [in,out] entero (Errors/Warnings de OpenDoc6, etc.). Con
    enlace tardio un int suelto da "los tipos no coinciden"."""
    return w.VARIANT(pythoncom.VT_I4 | pythoncom.VT_BYREF, int(v))


def null_dispatch():
    """Puntero a interfaz nulo (Callout de SelectByID2...). Un None suelto no
    marshalla como VT_DISPATCH nulo en enlace tardio: "los tipos no coinciden"."""
    return w.VARIANT(pythoncom.VT_DISPATCH, None)


def open_doc(path, silent=True):
    """OpenDoc6 con los [in,out] bien pasados. Devuelve el IModelDoc2."""
    ext = os.path.splitext(path)[1].lower()
    dtype = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}.get(ext)
    if dtype is None:
        raise ValueError("Extension no soportada por OpenDoc6: %s" % ext)
    errs, warns = byref_int(), byref_int()
    doc = sw_app().OpenDoc6(os.path.abspath(path), dtype, 1 if silent else 0, "", errs, warns)
    if doc is None:
        raise RuntimeError("OpenDoc6 devolvio None para %s (errors=%s, warnings=%s; swFileLoadError_e)"
                           % (path, errs.value, warns.value))
    return doc


def active_doc(required=True):
    doc = sw_app().ActiveDoc
    if doc is None and required:
        raise RuntimeError(
            "No hay documento activo en SOLIDWORKS. Usa sw_new_part o sw_open primero."
        )
    return doc


def doc_type(doc):
    # 1 = pieza, 2 = ensamblaje, 3 = dibujo
    try:
        return int(doc.GetType)
    except Exception:
        return -1


# --------------------------------------------------------------------------
# planos y croquis
# --------------------------------------------------------------------------

def ref_planes(doc):
    out = []
    f = doc.FirstFeature
    while f is not None:
        if f.GetTypeName2 == "RefPlane":
            out.append(f)
        f = f.GetNextFeature
    return out


def select_plane(doc, index=0):
    planes = ref_planes(doc)
    if not planes:
        raise RuntimeError("El documento no tiene planos de referencia.")
    if index >= len(planes):
        raise ValueError(
            "plane=%d fuera de rango; el documento tiene %d planos." % (index, len(planes))
        )
    doc.ClearSelection2(True)
    planes[index].Select2(False, 0)
    return planes[index].Name


def begin_sketch(doc, plane=0):
    """Abre croquis en el plano indicado (0 = primero del arbol, XY)."""
    name = select_plane(doc, plane)
    sm = doc.SketchManager
    sm.AddToDB = True           # coordenadas absolutas, sin relaciones automaticas
    sm.DisplayWhenAdded = False
    sm.InsertSketch(True)
    return sm, name


def end_sketch(sm):
    sm.AddToDB = False
    sm.DisplayWhenAdded = True


# --------------------------------------------------------------------------
# entidades de perfil
# --------------------------------------------------------------------------

def _circle(sm, e):
    sm.CreateCircleByRadius(mm(e["x"]), mm(e["y"]), 0.0, mm(float(e["d"]) / 2.0))


def _polyline(sm, e):
    pts = [(float(p[0]), float(p[1])) for p in e["points"]]
    if len(pts) < 2:
        raise ValueError("polyline necesita al menos 2 puntos.")
    close = bool(e.get("close", True))
    n = len(pts)
    last = n if close else n - 1
    for i in range(last):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if abs(x1 - x0) < 1e-9 and abs(y1 - y0) < 1e-9:
            continue
        sm.CreateLine(mm(x0), mm(y0), 0.0, mm(x1), mm(y1), 0.0)


def _rect(sm, e):
    x0, y0 = float(e["x0"]), float(e["y0"])
    x1, y1 = float(e["x1"]), float(e["y1"])
    _polyline(sm, {"points": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "close": True})


def _hexagon(sm, e):
    cx, cy, af = float(e["cx"]), float(e["cy"]), float(e["af"])
    r = (af / 2.0) / math.cos(math.radians(30.0))
    rot = math.radians(float(e.get("rot", 0.0)))
    pts = [
        (cx + r * math.cos(rot + math.radians(60.0 * i)),
         cy + r * math.sin(rot + math.radians(60.0 * i)))
        for i in range(6)
    ]
    _polyline(sm, {"points": pts, "close": True})


_DRAW = {"circle": _circle, "polyline": _polyline, "rect": _rect, "hexagon": _hexagon}


def draw_profile(sm, profile):
    if not profile:
        raise ValueError("profile vacio.")
    for e in profile:
        t = e.get("type")
        if t not in _DRAW:
            raise ValueError(
                "Tipo de entidad desconocido: %r. Validos: %s"
                % (t, ", ".join(sorted(_DRAW)))
            )
        _DRAW[t](sm, e)


# --------------------------------------------------------------------------
# operaciones
# --------------------------------------------------------------------------

def _depth_args(z0, z1):
    """Devuelve (profundidad_m, reverse, T0, start_offset_m) para z0->z1 en mm."""
    z0, z1 = float(z0), float(z1)
    if z0 < 0:
        raise ValueError("z0 debe ser >= 0 (usa el plano o reverse para ir a -Z).")
    if abs(z1 - z0) < 1e-9:
        raise ValueError("z0 y z1 son iguales: la extrusion seria de espesor cero.")
    d = mm(abs(z1 - z0))
    rev = z1 < z0
    t0, off = (0, 0.0) if z0 == 0 else (3, mm(z0))
    return d, rev, t0, off


def close_dangling_sketch(doc):
    """Cierra el croquis que queda activo si una operacion falla.

    Sin esto, el croquis abierto se queda colgando y la siguiente llamada a
    begin_sketch anidaria croquis dentro de croquis.
    """
    try:
        if doc.SketchManager.ActiveSketch is not None:
            doc.SketchManager.InsertSketch(True)
    except Exception:
        pass


def extrude(doc, profile, z0, z1, plane=0, cut=False, merge=True, name=None,
            through=False):
    sm, plane_name = begin_sketch(doc, plane)
    try:
        draw_profile(sm, profile)
    except Exception:
        end_sketch(sm)
        close_dangling_sketch(doc)
        raise
    d, rev, t0, off = _depth_args(z0, z1)
    t1 = 1 if through else 0          # 1 = por todo
    d1 = 0.0 if through else d

    if cut:
        # COMPROBADO en SOLIDWORKS Connected R2026x (rev 34.3.0):
        # FeatureCut4 interpreta Dir al reves que FeatureExtrusion2. Con el
        # mismo valor que usa la extrusion el corte se va al aire y la llamada
        # devuelve None sin lanzar excepcion. De ahi el "not rev".
        f = doc.FeatureManager.FeatureCut4(
            True, False, (not rev),    # Sd, Flip, Dir  <-- invertido a proposito
            t1, 0,                     # T1 (0 ciega, 1 por todo), T2
            d1, 0.0,                   # D1, D2
            False, False, False, False,   # Dchk1/2, Ddir1/2
            0.0, 0.0,                  # Dang1, Dang2
            False, False,              # OffsetReverse1/2
            False, False,              # TranslateSurface1/2
            False,                     # NormalCut
            True, True,                # UseFeatScope, UseAutoSelect
            False, False, False,       # AssemblyFeatureScope, AutoSelectComponents, PropagateToParts
            t0, off, False,            # T0, StartOffset, FlipStartOffset
            False)                     # OptimizeGeometry
    else:
        f = doc.FeatureManager.FeatureExtrusion2(
            True, False, rev,
            t1, 0,
            d1, 0.0,
            False, False, False, False,
            0.0, 0.0,
            False, False,
            False, False,
            bool(merge), True, True,
            t0, off, False)

    end_sketch(sm)
    if f is None:
        close_dangling_sketch(doc)
        raise RuntimeError(
            "%s fallida en %s, z %s -> %s%s. El perfil no cierra un contorno, o "
            "la operacion no encuentra material."
            % ("Cut" if cut else "Extrusion", plane_name, z0, z1,
               " (por todo)" if through else "")
        )
    if name:
        try:
            f.Name = name
        except Exception:
            pass
    return {"feature": (name or f.Name), "plane": plane_name, "cut": bool(cut)}


def thread_cut(doc, cx, cy, r_mayor, z_inicio, altura, pitch_mm, tipo="Inch Die",
               size=None, right_handed=True, name=None):
    """Corta una rosca real (helice + barrido) sobre un resalte cilindrico
    YA modelado como cilindro liso de diametro mayor r_mayor*2.

    z_inicio es la cota Z del extremo LIBRE del resalte (el que no se une
    a otra geometria: ahi esta la arista circular real donde arranca la
    rosca). altura es la longitud roscada, hacia el interior del resalte
    (BlindDepth). OJO: z_inicio NO tiene por que ser el z0 "bajo" de la
    extrusion que creo el resalte -- depende de que extremo quedo libre
    (en Carcasa_Superior el resalte crece hacia +Z y el libre es z1; en
    Carcasa_Inferior crece hacia -Z y el libre es z0).

    Usa la feature nativa "Rosca" de SOLIDWORKS via
    IFeatureManager::CreateDefinition(swFmSweepThread) -> IThreadFeatureData
    (no helice+solevado a mano: SOLIDWORKS ya hace eso por dentro).

    COMPROBADO EMPIRICAMENTE: la seleccion de la arista de arranque via
    IModelDocExtension::SelectByID2 necesita el parametro Callout como
    VARIANT(VT_DISPATCH, None) EXPLICITO -- un None a secas no marshalla
    como puntero nulo en este enlace tardio y la llamada falla con "los
    tipos no coinciden".

    Diameter y Pitch se fuerzan (DiameterOverride/PitchOverride) al valor
    exacto pasado, asi que el perfil Type/Size de la libreria de SOLIDWORKS
    (nominal, no de produccion segun su propia documentacion) solo aporta
    la forma en V de 60 grados; el diametro mayor y el paso reales son los
    correctos para el estandar pedido.
    """
    from . import const as sw_const

    # SelectByID2 por coordenadas (con el fix VARIANT(VT_DISPATCH,None) para
    # Callout) funciona para localizar la arista en piezas sencillas, pero
    # en piezas con muchas caras se ha visto fallar (devuelve False) sobre
    # aristas que SI existen exactamente en ese punto -- probablemente un
    # problema del hit-test por rayo, no de las coordenadas. Via robusta:
    # localizar la cara plana del extremo libre por su cota Z y, dentro de
    # ELLA, la arista circular de radio r_mayor; seleccionarla por su
    # propio handle (Edge.Select4) en vez de por coordenadas.
    caras = planar_faces(doc, z_inicio, tol=0.5)
    edge = None
    for f in caras:
        for e in (soft(f, "GetEdges") or []):
            cur = soft(e, "GetCurve")
            if cur is None:
                continue
            try:
                if not bool(soft(cur, "IsCircle")):
                    continue
            except Exception:
                continue
            par = soft(cur, "CircleParams")
            if not par or len(par) < 7:
                continue
            cx_e, cy_e = par[0] * MM, par[1] * MM
            r_e = par[6] * MM
            if abs(r_e - r_mayor) < 0.02 and abs(cx_e - cx) < 0.05 and abs(cy_e - cy) < 0.05:
                edge = e
                break
        if edge is not None:
            break
    if edge is None:
        raise RuntimeError(
            "No se encontro la arista circular de radio %.4f mm en (%.4f, "
            "%.4f, %.4f) mm (extremo libre del resalte)." % (r_mayor, cx, cy, z_inicio))
    doc.ClearSelection2(True)
    sel_data = soft(doc.SelectionManager, "CreateSelectData")
    if not edge.Select4(False, sel_data):
        raise RuntimeError("Edge.Select4 fallo sobre la arista del resalte ya localizada.")

    fm = doc.FeatureManager
    tfd = fm.CreateDefinition(sw_const.const("swFmSweepThread"))
    if tfd is None:
        raise RuntimeError("CreateDefinition(swFmSweepThread) devolvio None.")
    tfd.InitializeThreadData()
    tfd.Edge = edge
    tfd.Type = tipo
    if size:
        tfd.Size = size
    tfd.ThreadMethod = sw_const.const("swThreadMethod_Cut")
    tfd.EndCondition = sw_const.const("swThreadEndCondition_Blind")
    tfd.BlindDepth = mm(abs(altura))
    tfd.DiameterOverride = True
    tfd.Diameter = mm(r_mayor * 2.0)
    tfd.PitchOverride = True
    tfd.Pitch = mm(pitch_mm)
    tfd.RightHanded = bool(right_handed)
    doc.ClearSelection2(True)

    feat = fm.CreateFeature(tfd)
    if feat is None:
        raise RuntimeError(
            "CreateFeature(ThreadFeatureData) devolvio None: la rosca no "
            "se creo (Type/Size invalidos o seleccion perdida)."
        )
    if name:
        try:
            feat.Name = name
        except Exception:
            pass
    return {"feature": (name or feat.Name)}


def bodies_info(doc):
    out = []
    bodies = doc.GetBodies2(0, True)
    if not bodies:
        return out
    for b in bodies:
        bb = [v * MM for v in b.GetBodyBox()]
        out.append({
            "name": b.Name,
            "bbox_mm": {
                "min": [round(bb[0], 4), round(bb[1], 4), round(bb[2], 4)],
                "max": [round(bb[3], 4), round(bb[4], 4), round(bb[5], 4)],
            },
        })
    return out


def face_count(doc):
    """Numero total de caras. Testigo de que un corte ha quitado material:
    la caja envolvente no cambia con un agujero, el recuento de caras si."""
    bodies = doc.GetBodies2(0, True)
    if not bodies:
        return 0
    n = 0
    for b in bodies:
        v = b.GetFaceCount
        n += int(v() if callable(v) else v)
    return n


def overall_bbox(doc):
    """Caja envolvente en mm. Pieza: union de GetBodyBox de los cuerpos.
    Ensamblaje: IAssemblyDoc.GetBox(0) (GetBodies2 es de IPartDoc y en un
    ensamblaje da AttributeError); la ayuda avisa de que es APROXIMADA."""
    r = lambda v: round(v, 4)
    if doc_type(doc) == 2:
        box = soft(doc, "GetBox", 0)
        if not box:
            return None
        lo = [v * MM for v in box[:3]]
        hi = [v * MM for v in box[3:6]]
        aprox = True
    else:
        bodies = doc.GetBodies2(0, True)
        if not bodies:
            return None
        lo = [1e18, 1e18, 1e18]
        hi = [-1e18, -1e18, -1e18]
        for b in bodies:
            bb = [v * MM for v in b.GetBodyBox()]
            for i in range(3):
                lo[i] = min(lo[i], bb[i])
                hi[i] = max(hi[i], bb[i + 3])
        aprox = False
    out = {
        "min": [r(v) for v in lo],
        "max": [r(v) for v in hi],
        "size": [r(hi[i] - lo[i]) for i in range(3)],
        "center": [r((lo[i] + hi[i]) / 2.0) for i in range(3)],
    }
    if aprox:
        out["nota"] = "ensamblaje: IAssemblyDoc.GetBox, aproximada (puede no ser la mas ajustada)"
    return out


def features_info(doc, limit=200):
    out = []
    f = doc.FirstFeature
    while f is not None and len(out) < limit:
        try:
            out.append({
                "name": f.Name,
                "type": f.GetTypeName2,
                "suppressed": bool(f.IsSuppressed),
            })
        except Exception:
            pass
        f = f.GetNextFeature
    return out


STANDARD_VIEWS = {
    # (nombre, ViewId). El nombre resulta irrelevante en la practica -- solo
    # importa el ViewId -- pero se pasa de todas formas por si acaso.
    #
    # Verificado EMPIRICAMENTE con diag_view.py (barrido nombre x ViewId,
    # 24 capturas comparadas visualmente una a una): swStandardViews_e vive en
    # una typelib de constantes que dump_api.py no llega a volcar, asi que no
    # hay confirmacion por fuente para el numero en si, pero el comportamiento
    # esta contrastado contra imagenes reales, no adivinado de memoria.
    #   ViewId=-1 NO es "usar el nombre": es erratico, depende de la vista que
    #   hubiera activa justo antes. No usar.
    #   front(1) e isometric(7) se compararon directamente contra una captura
    #   manual de SOLIDWORKS hecha por el usuario -- coinciden.
    #   back/left/right/top/bottom/trimetric/dimetric se distinguieron entre si
    #   en el barrido (cada uno dio una imagen distinta y consistente con lo
    #   que cabria esperar) pero no se contrastaron contra una referencia
    #   independiente.
    "front": ("*Front", 1),
    "back": ("*Back", 2),
    "left": ("*Left", 3),
    "right": ("*Right", 4),
    "top": ("*Top", 5),
    "bottom": ("*Bottom", 6),
    "isometric": ("*Isometric", 7),
    "trimetric": ("*Trimetric", 8),
    "dimetric": ("*Dimetric", 9),
}


def set_view(doc, direction):
    """Cambia a una vista estandar y encuadra. Ver STANDARD_VIEWS para el
    origen (empirico, no de la typelib) de los ViewId."""
    if direction == "current":
        return
    entry = STANDARD_VIEWS.get(direction)
    if entry is None:
        raise ValueError(
            "direction=%r desconocida. Validas: current, %s"
            % (direction, ", ".join(sorted(STANDARD_VIEWS)))
        )
    name, view_id = entry
    doc.ShowNamedView2(name, view_id)
    doc.ViewZoomtofit2()
    doc.GraphicsRedraw2()


def screenshot(doc, path, width=1600, height=1200, direction="current"):
    """Guarda la vista activa a PNG. SaveBMP es la unica exportacion de imagen
    confirmada en la typelib (IModelDoc2.SaveBMP); se convierte a PNG despues
    con codigo propio (bmp_to_png), sin depender de Pillow."""
    set_view(doc, direction)
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    bmp_path = os.path.splitext(path)[0] + ".bmp"
    ok = doc.SaveBMP(bmp_path, int(width), int(height))
    if not ok:
        raise RuntimeError("SaveBMP devolvio False para %s" % bmp_path)
    png_path = os.path.splitext(path)[0] + ".png"
    bmp_to_png(bmp_path, png_path)
    os.remove(bmp_path)
    return {"path": png_path, "width": width, "height": height, "direction": direction}


# --------------------------------------------------------------------------
# BMP -> PNG, stdlib puro (sin Pillow: mantiene el servidor sin dependencias)
# --------------------------------------------------------------------------

def _read_bmp(path):
    import struct
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"BM":
        raise ValueError("No es un BMP valido: %r" % path)
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if compression != 0:
        raise ValueError("BMP comprimido (metodo %d) no soportado" % compression)
    if bpp not in (24, 32):
        raise ValueError("BMP de %d bpp no soportado (se esperaba 24 u 32)" % bpp)
    top_down = height < 0
    height = abs(height)
    bytes_per_px = bpp // 8
    row_size = ((bpp * width + 31) // 32) * 4  # filas alineadas a 4 bytes
    rows = []
    for r in range(height):
        off = pixel_offset + r * row_size
        row = data[off:off + width * bytes_per_px]
        rgb = bytearray(width * 3)
        for x in range(width):
            b, g, rr = row[x * bytes_per_px], row[x * bytes_per_px + 1], row[x * bytes_per_px + 2]
            rgb[x * 3:x * 3 + 3] = (rr, g, b)
        rows.append(bytes(rgb))
    if not top_down:
        # BMP bottom-up (el caso normal, height > 0): la fila 0 del fichero es
        # la fila inferior de la imagen. El PNG quiere orden top-down.
        rows.reverse()
    return width, height, rows


def _png_chunk(tag, payload):
    import struct, zlib
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def bmp_to_png(bmp_path, png_path):
    """Conversion BMP (24/32 bpp, sin comprimir) -> PNG, sin dependencias."""
    import struct, zlib
    width, height, rows = _read_bmp(bmp_path)
    raw = bytearray()
    for row in rows:
        raw.append(0)            # filtro "None" por fila
        raw += row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # RGB 8-bit
    idat = zlib.compress(bytes(raw), 9)
    png = (b"\x89PNG\r\n\x1a\n"
           + _png_chunk(b"IHDR", ihdr)
           + _png_chunk(b"IDAT", idat)
           + _png_chunk(b"IEND", b""))
    with open(png_path, "wb") as fh:
        fh.write(png)
    return png_path


# --------------------------------------------------------------------------
# guarda de sw_execute_script(readOnly=True)
# --------------------------------------------------------------------------
# NO es una caja de arena de seguridad: cualquier script con acceso a `doc`
# puede llamar a lo que quiera via COM. Es una red contra el despiste, en dos
# capas: (1) bloquea ANTES de ejecutar si el texto del script contiene una
# llamada a un miembro cuyo nombre empieza por un verbo mutante conocido;
# (2) compara el estado del documento antes/despues y, si algo cambio pese al
# bloqueo (nombre no reconocido), lo reporta como violacion en vez de callarlo.

# --------------------------------------------------------------------------
# aristas y chaflanes
# --------------------------------------------------------------------------

def edge_geom(e):
    """{"p","q","len"} de una arista RECTA, o None si es curva o no se lee.

    Tres llamadas COM, no seis. Cada ida y vuelta a SOLIDWORKS cuesta unos
    30 ms (medido), asi que el numero de llamadas es EL factor de coste:
    GetCurveParams2 devuelve los dos extremos de una tacada, en lugar de
    GetStartVertex + GetEndVertex + GetPoint + GetPoint. Medido sobre una
    pieza de 53 aristas: 7.085 ms -> 2.509 ms, con resultados identicos.

    La ayuda marca GetCurveParams2 como obsoleto frente a GetCurveParams3,
    pero el sustituto devuelve un OBJETO cuyos StartPoint/EndPoint hay que
    leer con dos llamadas mas: seria mas lento. Aqui gana el obsoleto.
    Sus remarks exigen llamar antes a GetCurve, cosa que ya hacemos.
    """
    cur = soft(e, "GetCurve")
    if cur is None:
        return None
    try:
        if not bool(soft(cur, "IsLine")):
            return None
    except Exception:
        return None
    par = soft(e, "GetCurveParams2")
    if par and len(par) >= 6:
        p = [v * MM for v in par[0:3]]
        q = [v * MM for v in par[3:6]]
    else:                                   # respaldo por vertices
        a, b = soft(e, "GetStartVertex"), soft(e, "GetEndVertex")
        if a is None or b is None:
            return None
        p = [v * MM for v in soft(a, "GetPoint")]
        q = [v * MM for v in soft(b, "GetPoint")]
    return {"p": p, "q": q, "len": math.dist(p, q)}


def straight_edges(doc, face=None):
    """Aristas RECTAS con sus extremos en mm.

    Devuelve [{"edge": IEdge, "p": [x,y,z], "q": [x,y,z], "len": mm}, ...].
    Las curvas (agujeros, cilindros) quedan fuera: un chaflan o un redondeo
    por predicado geometrico casi siempre se define sobre rectas.

    face: si se pasa un IFace2, recorre solo SUS aristas. Como el coste va
    con el numero de llamadas COM, acotar la cara antes es la optimizacion
    que mas rinde cuando se sabe donde se va a trabajar (11 aristas frente
    a 53 en la placa del selftest).
    """
    out = []
    if face is not None:
        fuentes = [soft(face, "GetEdges") or []]
    else:
        fuentes = [soft(b, "GetEdges") or [] for b in (doc.GetBodies2(0, True) or [])]
    for aristas in fuentes:
        for e in aristas:
            g = edge_geom(e)
            if g is not None:
                out.append(dict(g, edge=e))
    return out


def planar_faces(doc, z_mm=None, tol=1e-3):
    """Caras planas del documento, opcionalmente solo las de cota z dada.

    Pensado para acotar antes de recorrer aristas: planar_faces(doc, 0.0)
    da la cara trasera de una placa, y straight_edges(doc, face=esa) lee
    solo sus aristas.

    Orden de los filtros: primero el barato. GetBox es UNA llamada COM y
    descarta casi todas las caras; GetSurface + IsPlane son dos mas y solo
    se gastan en las que ya han pasado el filtro de cota. Al reves costaba
    3 llamadas por cara: 1.084 ms frente a los ~350 de ahora.
    """
    out = []
    for body in (doc.GetBodies2(0, True) or []):
        for f in (soft(body, "GetFaces") or []):
            if z_mm is not None:
                caja = soft(f, "GetBox")    # [x1,y1,z1,x2,y2,z2] en metros
                if not caja or len(caja) < 6:
                    continue
                if not (abs(caja[2] * MM - z_mm) < tol and
                        abs(caja[5] * MM - z_mm) < tol):
                    continue               # plana en z pero en otra cota, o curva
            sur = soft(f, "GetSurface")     # solo para las candidatas
            if sur is None:
                continue
            try:
                if not bool(soft(sur, "IsPlane")):
                    continue
            except Exception:
                continue
            out.append(f)
    return out


def describe_edges(edges):
    """Las mismas aristas sin el handle COM, para imprimir o devolver por MCP."""
    return [{"de": [round(v, 3) for v in e["p"]],
             "a": [round(v, 3) for v in e["q"]],
             "largo_mm": round(e["len"], 3)} for e in edges]


def edges_on_plane(doc, z_mm, tol=1e-3):
    """Aristas rectas que estan en el plano Z = z_mm, por la via rapida.

    Localiza primero la cara plana de esa cota (pocas llamadas COM) y lee
    solo SUS aristas. Si no encuentra cara, recorre el cuerpo entero y
    filtra por z: mismo resultado, mas lento. Devuelve (aristas, via).
    """
    caras = planar_faces(doc, z_mm, tol)
    if caras:
        aristas = []
        vistas = set()
        for f in caras:
            for e in straight_edges(doc, face=f):
                k = (tuple(round(v, 6) for v in e["p"]),
                     tuple(round(v, 6) for v in e["q"]))
                if k not in vistas:
                    vistas.add(k)
                    aristas.append(e)
        return aristas, "cara"
    todas = straight_edges(doc)
    return ([e for e in todas
             if abs(e["p"][2] - z_mm) < tol and abs(e["q"][2] - z_mm) < tol],
            "cuerpo entero (no se hallo cara plana en Z=%.3f)" % z_mm)


def chamfer_edges(doc, edges, dist_mm=1.0, angle_deg=45.0, options=4):
    """Chaflan angulo-distancia sobre aristas ya localizadas.

    edges: lista de IEdge, o de dicts de straight_edges().
    options: mascara swFeatureChamferOption_e (4 = propagacion tangente).
    Selecciona con Select4 sobre el handle, no con SelectByID2: la seleccion
    por coordenadas depende del estado de la vista.
    Comprueba el recuento de caras, porque InsertFeatureChamfer devuelve None
    en silencio cuando el chaflan no cabe.
    """
    handles = [e["edge"] if isinstance(e, dict) else e for e in edges]
    if not handles:
        raise ValueError("No hay aristas que chaflanar.")

    doc.ClearSelection2(True)
    sel_data = soft(doc.SelectionManager, "CreateSelectData")
    for i, edge in enumerate(handles):
        if not edge.Select4(i > 0, sel_data):
            raise RuntimeError("Select4 fallo en la arista %d de %d" % (i, len(handles)))

    antes = face_count(doc)
    feat = doc.FeatureManager.InsertFeatureChamfer(
        options,
        1,                          # swChamferAngleDistance
        mm(dist_mm),                # Width, en METROS
        math.radians(angle_deg),    # Angle, en RADIANES
        0, 0, 0, 0,                 # sin uso en angulo-distancia
    )
    doc.ClearSelection2(True)
    if feat is None:
        raise RuntimeError(
            "InsertFeatureChamfer devolvio None: no se creo el chaflan. "
            "Suele ser que %.3f mm no cabe en la arista, o que la seleccion se perdio."
            % dist_mm
        )
    despues = face_count(doc)
    return {
        "operacion": soft(feat, "Name"),
        "aristas": len(handles),
        "caras": {"antes": antes, "despues": despues},
        "caras_nuevas": despues - antes,
    }


def fillet_edges(doc, edges, radius_mm=2.0, propagate=True):
    """Redondeo de radio constante sobre aristas ya localizadas.

    Usa la via moderna: IFeatureManager::CreateDefinition(swFmFillet) ->
    ISimpleFilletFeatureData2 -> CreateFeature. La ayuda oficial avisa de que
    IFeatureManager::FeatureFillet3 quedo OBSOLETO en 2020 para los redondeos
    de radio constante, asi que solo se usa como respaldo si la via moderna
    falla. El resultado dice cual se empleo, en "via".

    Detalle que solo esta en la typelib: ISimpleFilletFeatureData2.Type es de
    SOLO LECTURA; el tipo se fija con Initialize(swFeatureFilletType_Simple).
    """
    handles = [e["edge"] if isinstance(e, dict) else e for e in edges]
    if not handles:
        raise ValueError("No hay aristas que redondear.")

    # Valores numericos y no w.constants: con enlace tardio constants esta
    # vacio (gencache.EnsureDispatch falla aqui, ver dump_api.py).
    FILLET_SIMPLE = 0        # swFeatureFilletType_Simple
    OVERFLOW_DEFAULT = 0     # swFilletOverFlowType_Default
    CONIC_CIRCULAR = 0       # swFeatureFilletProfileType_e.swFeatureFilletCircular
    # swFeatureFilletOptions_e, mascara de bits:
    OPT_PROPAGATE = 1        # swFeatureFilletPropagate
    OPT_UNIFORM = 2          # swFeatureFilletUniformRadius  <- SIN ESTO R1 SE IGNORA
    OPT_ATTACH_EDGES = 64    # swFeatureFilletAttachEdges
    OPT_KEEP_FEATURES = 128  # swFeatureFilletKeepFeatures

    fm = doc.FeatureManager
    antes = face_count(doc)
    feat, via, motivo = None, None, None

    # --- via moderna -----------------------------------------------------
    try:
        from . import const as sw_const
        fm_fillet = sw_const.const("swFmFillet")   # sin valor publicado en la ayuda
        data = fm.CreateDefinition(fm_fillet)
        if data is None:
            raise RuntimeError("CreateDefinition(swFmFillet=%r) devolvio None" % fm_fillet)
        data.Initialize(FILLET_SIMPLE)             # Type es solo-lectura en la typelib
        data.DefaultRadius = mm(radius_mm)
        data.PropagateToTangentFaces = bool(propagate)
        data.OverflowType = OVERFLOW_DEFAULT
        data.IsMultipleRadius = False
        if not data.AccessSelections(doc, None):
            raise RuntimeError("AccessSelections devolvio False")
        try:
            data.Edges = handles
        except Exception:                               # SAFEARRAY explicito
            data.Edges = w.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, handles)
        feat = fm.CreateFeature(data)
        if feat is None:
            soft(data, "ReleaseSelectionAccess")
            raise RuntimeError("CreateFeature devolvio None")
        via = "CreateDefinition + ISimpleFilletFeatureData2"
    except Exception as exc:
        motivo = "%s: %s" % (type(exc).__name__, exc)
        feat = None

    # --- respaldo: FeatureFillet3 (obsoleto desde 2020) ------------------
    if feat is None:
        doc.ClearSelection2(True)
        sel_data = soft(doc.SelectionManager, "CreateSelectData")
        for i, edge in enumerate(handles):
            if not edge.Select4(i > 0, sel_data):
                raise RuntimeError("Select4 fallo en la arista %d" % i)
        # R1 solo es valido si Options incluye swFeatureFilletUniformRadius:
        # sin ese bit el radio se ignora y FeatureFillet3 devuelve None.
        options = OPT_UNIFORM | OPT_ATTACH_EDGES | OPT_KEEP_FEATURES
        if propagate:
            options |= OPT_PROPAGATE          # 195; 194 sin propagacion
        feat = fm.FeatureFillet3(
            options,                             # Options
            mm(radius_mm),                       # R1, en METROS
            0.0,                                 # R2, solo asimetrico
            0.0,                                 # Rho, solo conico
            FILLET_SIMPLE,                       # Ftyp
            OVERFLOW_DEFAULT,                    # OverflowType
            CONIC_CIRCULAR,                      # ConicRhoType
            None, None, None, None, None, None, None,   # arrays de radio variable
        )
        doc.ClearSelection2(True)
        via = "FeatureFillet3 (respaldo)"

    if feat is None:
        raise RuntimeError(
            "No se creo el redondeo por ninguna via. Radio %.3f mm sobre %d "
            "arista(s). La via moderna fallo con -> %s" % (radius_mm, len(handles), motivo)
        )

    despues = face_count(doc)
    out = {
        "operacion": soft(feat, "Name"),
        "aristas": len(handles),
        "radio_mm": radius_mm,
        "via": via,
        "caras": {"antes": antes, "despues": despues},
        "caras_nuevas": despues - antes,
    }
    if motivo and via.startswith("FeatureFillet3"):
        out["via_moderna_fallo"] = motivo
    return out


MUTATING_PREFIXES = (
    "Feature", "Insert", "Create", "Add", "Delete", "Remove", "Set",
    "Save", "Close", "New", "Open", "Edit", "Force", "Suppress",
    "Unsuppress", "Rename", "Move", "Copy", "Merge", "Rebuild",
    "ShowNamedView", "Rollback",
)
_MUTATING_RE = re.compile(r"\.(%s)\w*\s*\(" % "|".join(MUTATING_PREFIXES))
_ASSIGN_RE = re.compile(r"\.\w+\s*=[^=]")   # asignacion a propiedad: doc.X = ...
# Empiezan por un verbo "mutante" pero solo leen o crean objetos auxiliares
# (falsos positivos vistos en uso real, 27-09-2026).
_READ_ONLY_OK = re.compile(r"(FeatureByName|FeatureByPositionReverse|FeatureById|"
                           r"CreateMassProperty2?|CreateSelectData|CreatePoint|"
                           r"CreateTransform|OpenKey|CreateDefinition)\s*\(")


def scan_mutating(script):
    """Nombres de llamada/asignacion que sugieren mutacion. No es exhaustivo:
    solo detecta el patron `.Verbo(` o `.propiedad = `, en texto, sin parsear
    Python de verdad. Una llamada tras una indireccion (getattr, variable con
    el metodo guardado) se le escapa."""
    hits = set(m.group(0) for m in _MUTATING_RE.finditer(script)
               if not _READ_ONLY_OK.match(m.group(0)[1:]))
    if _ASSIGN_RE.search(script):
        hits.add("<asignacion a propiedad>")
    return sorted(hits)


def doc_state(doc):
    """Foto minima del documento para detectar si algo cambio de verdad."""
    if doc is None:
        return {"doc": None}
    try:
        nbodies = len(doc.GetBodies2(0, True) or [])
    except Exception:
        nbodies = None
    try:
        nfaces = face_count(doc)
    except Exception:
        nfaces = None
    try:
        modified = bool(doc.GetSaveFlag)
    except Exception:
        modified = None
    return {"doc": doc.GetTitle, "bodies": nbodies, "faces": nfaces, "modified": modified}


def state_diff(before, after):
    if before.get("doc") != after.get("doc"):
        return ["documento activo cambio: %r -> %r" % (before.get("doc"), after.get("doc"))]
    return [
        "%s: %r -> %r" % (k, before.get(k), after.get(k))
        for k in ("bodies", "faces", "modified")
        if before.get(k) != after.get(k)
    ]


def close_doc(doc):
    """Cierra el documento (por titulo) tras guardarlo con save_as.

    Sin esto cada pieza construida se queda abierta en la sesion de
    SOLIDWORKS: con decenas de piezas eso agota la memoria (visto en la
    practica: 17 documentos abiertos -> error de "poca memoria"). CloseDoc
    cierra sin preguntar si el documento estuviera sucio, pero se llama
    siempre DESPUES de save_as(), asi que no hay nada sin guardar que perder.
    """
    if doc is None:
        return
    titulo = doc.GetTitle
    sw_app().CloseDoc(titulo)


def save_as(doc, path, also_step=False):
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc.ViewZoomtofit2()
    ok = doc.SaveAs3(path, 0, 0)
    written = [path]
    if also_step:
        step = os.path.splitext(path)[0] + ".STEP"
        doc.SaveAs3(step, 0, 0)
        written.append(step)
    return {"saved": written, "result": ok}

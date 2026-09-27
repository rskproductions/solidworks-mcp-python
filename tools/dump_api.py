#!/usr/bin/env python
"""
Vuelca la biblioteca de tipos COM de SOLIDWORKS a un indice de texto.

Fuente autoritativa de la API: no la web, sino la ITypeLib de la instalacion.
Da, para tu version exacta, el nombre y tipo de cada parametro y si el miembro
es propiedad (se lee sin parentesis) o metodo (hay que llamarlo). Que
gencache.EnsureDispatch falle en SOLIDWORKS Connected no impide leer esto:
solo impide que pywin32 genere el wrapper estatico.

    py tools/dump_api.py                    vuelca todo a api_index.txt
    py tools/dump_api.py --find FeatureCut  busca sin volcar
    py tools/dump_api.py --iface IFeatureManager   una interfaz entera

Formato de cada linea del indice:

    IFeatureManager.FeatureCut4(METODO) -> IFeature*
        01 in      Sd                BOOL
        02 in      Flip              BOOL
        ...
"""

import os
import sys

import pythoncom
import win32com.client as w

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
from solidworks_mcp.paths import TYPELIB_TXT as INDEX  # noqa: E402

VTNAME = {}
for _n in dir(pythoncom):
    if _n.startswith("VT_"):
        VTNAME.setdefault(getattr(pythoncom, _n), _n[3:])

INVKIND = {1: "METODO", 2: "PROPIEDAD-GET", 4: "PROPIEDAD-PUT", 8: "PROPIEDAD-PUTREF"}

SKIPPED = []   # miembros que no se pudieron leer, se listan al final

PARAMFLAG = [(1, "in"), (2, "out"), (4, "lcid"), (8, "retval"), (16, "opt")]

TKIND_NAMES = {
    getattr(pythoncom, n): n[6:]
    for n in dir(pythoncom) if n.startswith("TKIND_")
}


# ---------------------------------------------------------------------------
# localizar la typelib
# ---------------------------------------------------------------------------

def typelib_from_live_object():
    """Via preferida: preguntar al objeto vivo por su ITypeInfo."""
    app = w.Dispatch("SldWorks.Application")
    ti = app._oleobj_.GetTypeInfo()
    tl, _idx = ti.GetContainingTypeLib()
    return tl


def typelib_from_registry():
    """Plan B: CLSID de SldWorks.Application -> TypeLib -> LoadRegTypeLib."""
    import winreg

    def val(root, path, name=""):
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]

    clsid = val(winreg.HKEY_CLASSES_ROOT, r"SldWorks.Application\CLSID")
    libid = val(winreg.HKEY_CLASSES_ROOT, r"CLSID\%s\TypeLib" % clsid)
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"TypeLib\%s" % libid) as k:
        versions = []
        i = 0
        while True:
            try:
                versions.append(winreg.EnumKey(k, i))
            except OSError:
                break
            i += 1
    if not versions:
        raise RuntimeError("TypeLib %s sin versiones en el registro" % libid)
    ver = sorted(versions)[-1]
    major, minor = (int(x, 16) for x in ver.split("."))
    return pythoncom.LoadRegTypeLib(libid, major, minor, 0)


def get_typelib():
    errors = []
    for fn in (typelib_from_live_object, typelib_from_registry):
        try:
            tl = fn()
            print("typelib obtenida via %s" % fn.__name__)
            return tl
        except Exception as exc:
            errors.append("%s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    raise RuntimeError("No se pudo obtener la typelib.\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------------------
# tipos
# ---------------------------------------------------------------------------

def type_name(ti, t, depth=0):
    if depth > 8:
        return "?"
    if isinstance(t, (tuple, list)):
        if not t:
            return "VOID"
        base = t[0]
        # El TYPEDESC puede venir anidado: el primer elemento es otra tupla.
        if isinstance(base, (tuple, list)):
            return type_name(ti, base, depth + 1)
        if base == pythoncom.VT_PTR and len(t) > 1:
            return type_name(ti, t[1], depth + 1) + "*"
        if base == pythoncom.VT_SAFEARRAY and len(t) > 1:
            return "SAFEARRAY(%s)" % type_name(ti, t[1], depth + 1)
        if base == pythoncom.VT_CARRAY and len(t) > 1:
            return "ARRAY(%s)" % type_name(ti, t[1], depth + 1)
        if base == pythoncom.VT_USERDEFINED and len(t) > 1:
            try:
                rti = ti.GetRefTypeInfo(t[1])
                return rti.GetDocumentation(-1)[0]
            except Exception:
                return "USERDEFINED"
        # La coma importa: "VT%s" % base con base tupla la desempaqueta y falla.
        return VTNAME.get(base, "VT%s" % (base,))
    return VTNAME.get(t, "VT%s" % (t,))


def arg_flags(arg):
    """Los PARAMFLAGS de un ELEMDESC.

    pywin32 no es consistente: segun version y tipo, arg[1] puede ser un entero,
    None, o una tupla (flags, valor_por_defecto). Normalizamos a entero.
    """
    try:
        f = arg[1]
    except Exception:
        return 0
    while isinstance(f, (tuple, list)):
        if not f:
            return 0
        f = f[0]
    try:
        return int(f)
    except Exception:
        return 0


def flag_names(flags):
    out = [n for bit, n in PARAMFLAG if flags & bit]
    return ",".join(out) if out else "in"


# ---------------------------------------------------------------------------
# volcado
# ---------------------------------------------------------------------------

def iter_members(tl):
    """Genera (interfaz, texto_firma, texto_parametros)."""
    n = tl.GetTypeInfoCount()
    for i in range(n):
        try:
            iface = tl.GetDocumentation(i)[0]
            ti = tl.GetTypeInfo(i)
            attr = ti.GetTypeAttr()
        except Exception:
            continue

        kind = TKIND_NAMES.get(attr.typekind, str(attr.typekind))

        if kind == "ENUM":
            for v in range(attr.cVars):
                try:
                    vd = ti.GetVarDesc(v)
                    vname = ti.GetNames(vd.memid)[0]
                    yield iface, "%s.%s = %s  (ENUM)" % (iface, vname, vd.value), []
                except Exception:
                    continue
            continue

        for f in range(attr.cFuncs):
            # Un miembro raro no debe tumbar el volcado entero: se anota y sigue.
            try:
                fd = ti.GetFuncDesc(f)
                names = ti.GetNames(fd.memid)
                fname = names[0] if names else "?"
                pnames = list(names[1:])

                params = []
                ret = type_name(ti, fd.rettype)
                for idx, arg in enumerate(fd.args or []):
                    atype = type_name(ti, arg[0])
                    aflags = arg_flags(arg)
                    pname = pnames[idx] if idx < len(pnames) else "arg%d" % (idx + 1)
                    # el parametro marcado retval es el valor de retorno real
                    if aflags & 8:
                        ret = atype.rstrip("*")
                        continue
                    params.append((idx + 1, flag_names(aflags), pname, atype))

                sig = "%s.%s(%s) -> %s   [%d args]" % (
                    iface, fname, INVKIND.get(fd.invkind, str(fd.invkind)),
                    ret, len(params))
            except Exception as exc:
                SKIPPED.append("%s func#%d: %s: %s"
                               % (iface, f, type(exc).__name__, exc))
                continue
            yield iface, sig, params


def render(sig, params):
    lines = [sig]
    for num, flags, pname, ptype in params:
        lines.append("    %02d %-8s %-26s %s" % (num, flags, pname, ptype))
    return "\n".join(lines)


def main():
    pythoncom.CoInitialize()
    args = sys.argv[1:]

    find = None
    iface_only = None
    if "--find" in args:
        find = args[args.index("--find") + 1].lower()
    if "--iface" in args:
        iface_only = args[args.index("--iface") + 1].lower()

    tl = get_typelib()
    la = tl.GetLibAttr()
    doc = tl.GetDocumentation(-1)
    print("biblioteca: %s  v%s.%s  guid %s" % (doc[0], la[3], la[4], la[0]))
    print("interfaces/enums: %d" % tl.GetTypeInfoCount())

    if find or iface_only:
        hits = 0
        for iface, sig, params in iter_members(tl):
            if iface_only and iface.lower() != iface_only:
                continue
            if find and find not in sig.lower():
                continue
            print()
            print(render(sig, params))
            hits += 1
            if hits >= 400:
                print("\n... cortado en 400 coincidencias")
                break
        if not hits:
            print("\nsin coincidencias")
        return

    count = 0
    with open(INDEX, "w", encoding="utf-8") as fh:
        fh.write("%s v%s.%s  guid %s\n\n" % (doc[0], la[3], la[4], la[0]))
        for iface, sig, params in iter_members(tl):
            fh.write(render(sig, params))
            fh.write("\n\n")
            count += 1
    print("escritos %d miembros en %s (%.1f MB)"
          % (count, INDEX, os.path.getsize(INDEX) / 1e6))
    if SKIPPED:
        print("miembros ilegibles: %d (primeros 5)" % len(SKIPPED))
        for s in SKIPPED[:5]:
            print("   ", s)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print("\n=== FALLO ===")
        traceback.print_exc()
        print("\npywin32:", getattr(pythoncom, "__file__", "?"))
        print("python :", sys.version)
        sys.exit(1)
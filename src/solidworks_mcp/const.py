#!/usr/bin/env python
"""
Constantes swconst (swEndCondBlind, swDocPART, swSelFACES...) por nombre.

Por que hace falta: con enlace tardio win32com.client.constants esta VACIO.
Solo se puebla cuando pywin32 genera el wrapper estatico via
gencache.EnsureDispatch, y eso falla en esta instalacion (ver el docstring de
dump_api.py). `w.constants.swDocPART` lanza AttributeError.

Dos fuentes, en este orden:

  1. sw_api_index.sqlite  la ayuda oficial 2026: 2.999 constantes con valor.
     Es la via rapida y cubre casi todo.
  2. la biblioteca de tipos  para lo que la ayuda no publica. OJO: los enums
     NO estan en la typelib de SldWorks.Application; viven en una biblioteca
     propia (swconst), que hay que localizar por el registro. La primera
     version de este modulo buscaba en la de SldWorks y solo encontraba las 7
     constantes de stdole.

Unas 2.052 constantes no tienen valor publicado en la ayuda (casi todas de
swUserPreference*_e, y algunas de swFeatureNameID_e como swFmFillet). Si la
fuente 2 tampoco las encuentra, no hay forma de resolverlas por nombre y hay
que usar la API que las acepte de otro modo.

    python -m solidworks_mcp.const                 construye el cache y resume
    python -m solidworks_mcp.const swFmFillet ...  consulta puntual
    python -m solidworks_mcp.const --typelib       ademas escanea el registro (lento)

Uso desde codigo:

    from solidworks_mcp.const import const
    const("swDocPART")           -> 1,  o KeyError con el motivo
    const("swFmFillet", None)    -> None en vez de KeyError
"""

import json
import os
import sqlite3
import sys

from .paths import SWCONST_JSON as CACHE, SWCONST_CHOQUES as CHOQUES, INDEX_DB
META = "__meta__"   # clave reservada dentro del cache: de donde salieron los valores

_consts = None


# --------------------------------------------------------------------------
# fuente 1: el indice de la ayuda
# --------------------------------------------------------------------------

def from_index():
    if not os.path.exists(INDEX_DB):
        return {}
    con = sqlite3.connect(INDEX_DB)
    try:
        rows = con.execute(
            "SELECT member, value FROM enum_members WHERE value IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {m: v for m, v in rows}


def enum_of(name):
    """A que enum pertenece una constante, tenga valor o no."""
    if not os.path.exists(INDEX_DB):
        return None
    con = sqlite3.connect(INDEX_DB)
    try:
        r = con.execute(
            "SELECT enum_name FROM enum_members WHERE member=?", (name,)
        ).fetchone()
    finally:
        con.close()
    return r[0] if r else None


# --------------------------------------------------------------------------
# fuente 2: la biblioteca de tipos de constantes, localizada por el registro
# --------------------------------------------------------------------------

def _candidate_typelibs(verbose=False):
    """Bibliotecas registradas cuyo nombre suena a SOLIDWORKS."""
    import winreg
    import pythoncom

    out = []
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "TypeLib") as root:
        i = 0
        while True:
            try:
                libid = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, libid) as lk:
                    j = 0
                    while True:
                        try:
                            ver = winreg.EnumKey(lk, j)
                        except OSError:
                            break
                        j += 1
                        try:
                            nombre = winreg.QueryValueEx(lk, "")[0] if False else None
                        except Exception:
                            nombre = None
                        try:
                            with winreg.OpenKey(lk, ver) as vk:
                                nombre = winreg.QueryValueEx(vk, "")[0]
                        except Exception:
                            continue
                        if not nombre:
                            continue
                        low = nombre.lower()
                        if "solidworks" not in low and "sldworks" not in low \
                           and "swconst" not in low:
                            continue
                        try:
                            major, minor = (int(x, 16) for x in ver.split("."))
                            tl = pythoncom.LoadRegTypeLib(libid, major, minor, 0)
                        except Exception:
                            continue
                        if verbose:
                            print("  candidata: %s (%s)" % (nombre, ver))
                        out.append((nombre, tl))
            except Exception:
                continue
    return out


def _read_enums(tl):
    import pythoncom
    out = {}
    for i in range(tl.GetTypeInfoCount()):
        try:
            if tl.GetTypeInfoType(i) != pythoncom.TKIND_ENUM:
                continue
            ti = tl.GetTypeInfo(i)
            attr = ti.GetTypeAttr()
        except Exception:
            continue
        for v in range(attr.cVars):
            try:
                vd = ti.GetVarDesc(v)
                value = vd[1]
                if isinstance(value, (int, float)):
                    out[ti.GetNames(vd[0])[0]] = value
            except Exception:
                continue
    return out


# Orden de autoridad al fusionar. swconst es la biblioteca canonica de
# constantes de la API; las demas (Simulation, Commands, PDM...) son de
# subsistemas y pueden reutilizar nombres con otro valor.
def _prioridad(nombre):
    low = nombre.lower()
    if "constant type library" in low:
        return 0                      # swconst: manda
    if "sldworks" in low:
        return 1
    return 2                          # subsistemas


def from_typelib(verbose=False):
    """Fusiona las bibliotecas por orden de autoridad y avisa de colisiones."""
    try:
        cands = _candidate_typelibs(verbose)
    except Exception as exc:
        if verbose:
            print("  (no se pudo recorrer el registro: %s)" % exc)
        return {}

    porlib = []
    for nombre, tl in cands:
        d = _read_enums(tl)
        if verbose:
            print("  %-52s %d constantes" % (nombre[:52], len(d)))
        if d:
            porlib.append((nombre, d))

    porlib.sort(key=lambda t: _prioridad(t[0]))
    found, origen, choques = {}, {}, []
    for nombre, d in porlib:
        for k, v in d.items():
            if k in found:
                if found[k] != v:     # mismo nombre, valor distinto
                    choques.append((k, origen[k], found[k], nombre, v))
                continue              # gana la de mayor autoridad
            found[k], origen[k] = v, nombre

    if choques:
        _guardar_choques(choques)
        if verbose:
            print("  AVISO: %d nombres repetidos con valor distinto entre "
                  "bibliotecas; gana swconst. Detalle en %s"
                  % (len(choques), os.path.basename(CHOQUES)))
    return found


def _guardar_choques(choques):
    filas = [{"constante": k, "usada_de": a, "valor": va,
              "descartada_de": b, "valor_descartado": vb}
             for k, a, va, b, vb in sorted(choques)]
    with open(CHOQUES, "w", encoding="utf-8") as fh:
        json.dump(filas, fh, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------

def build(verbose=False, typelib=True):
    todo = from_index()
    if verbose:
        print("  indice de la ayuda%s%d constantes" % (" " * 36, len(todo)))
    if typelib:
        extra = from_typelib(verbose)
        nuevas = {k: v for k, v in extra.items() if k not in todo}
        todo.update(extra)
        if verbose and nuevas:
            print("  la typelib aporta %d que la ayuda no publica" % len(nuevas))
    todo[META] = {"fuentes": ["indice"] + (["typelib"] if typelib else []),
                  "constantes": len(todo)}
    if len(todo) <= 1:
        raise RuntimeError(
            "No se leyo ninguna constante: falta sw_api_index.sqlite y la "
            "biblioteca de tipos no es accesible."
        )
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(todo, fh, ensure_ascii=False, indent=0, sort_keys=True)
    return todo


def load(refresh=False):
    global _consts
    if _consts is not None and not refresh:
        return _consts
    if not refresh and os.path.exists(CACHE):
        try:
            with open(CACHE, "r", encoding="utf-8") as fh:
                _consts = json.load(fh)
                return _consts
        except Exception:
            pass
    _consts = build(typelib=False)      # arranque rapido: solo el indice
    return _consts


def fuentes():
    """De donde salen los valores del cache: ["indice"] o ["indice","typelib"].
    Lista vacia si no hay cache. Sirve para saber si faltan los ~12.000 valores
    que solo estan en la biblioteca de tipos."""
    if not os.path.exists(CACHE):
        return []
    try:
        with open(CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh).get(META, {}).get("fuentes", ["indice"])
    except Exception:
        return []


_MISSING = object()


def const(name, default=_MISSING):
    """Valor de una constante swconst. KeyError con el motivo si no se puede."""
    try:
        import win32com.client as w
        return getattr(w.constants, name)       # por si hubiera early binding
    except Exception:
        pass
    motivo = None
    try:
        v = load().get(name, _MISSING)
    except Exception as exc:
        v, motivo = _MISSING, "%s: %s" % (type(exc).__name__, exc)
    if v is not _MISSING:
        return v
    if default is not _MISSING:
        return default
    if motivo:
        raise KeyError("No se pudieron cargar las constantes para %s -> %s" % (name, motivo))
    fam = enum_of(name)
    if fam:
        raise KeyError(
            "%s pertenece a %s, pero la ayuda no publica su valor numerico. "
            "Prueba 'python -m solidworks_mcp.const --typelib' con SOLIDWORKS abierto; si "
            "tampoco aparece, esa constante no se puede resolver por nombre."
            % (name, fam)
        )
    raise KeyError(
        "Constante desconocida: %s. No esta ni en el indice de la ayuda ni en "
        "el cache. 'python -m solidworks_mcp.const --typelib' lo regenera." % name
    )


if __name__ == "__main__":
    usar_typelib = "--typelib" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    print("Construyendo cache de constantes...")
    d = build(verbose=True, typelib=usar_typelib)
    if not usar_typelib:
        print("  (solo el indice; usa --typelib para escanear el registro)")
    print("\n%d constantes -> %s" % (len(d), CACHE))
    muestra = ["swDocPART", "swEndCondBlind", "swSelFACES",
               "swFeatureFilletType_Simple", "swFeatureFilletUniformRadius",
               "swFilletOverFlowType_Default", "swFmFillet"] + args
    for n in muestra:
        v = d.get(n)
        if v is None:
            fam = enum_of(n)
            v = "(sin valor publicado; %s)" % fam if fam else "(NO ESTA)"
        print("  %-32s %s" % (n, v))

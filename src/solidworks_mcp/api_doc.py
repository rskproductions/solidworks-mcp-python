"""
Capa de conocimiento de la API para el servidor: une las dos fuentes.

  * api_index.txt      la typelib REAL de esta instalacion (dump_api.py):
                       nombre exacto, orden y tipo COM de cada parametro.
  * sw_api_index.sqlite la ayuda oficial 2026 SP04 (help.solidworks.com):
                       que significa cada parametro, que enum lo gobierna,
                       unidades, remarks, marcas de seleccion, desde que version.

La typelib manda en tipos y orden; la ayuda manda en semantica. Si un miembro
esta en una y no en la otra se devuelve lo que haya y se dice cual falta.
Sin dependencias fuera de la stdlib. Todo se carga perezosamente.
"""

import os
import re

from .paths import TYPELIB_TXT, INDEX_DB

_index = None          # ApiIndex o False si no hay sqlite
_typelib = None        # dict {(iface_lower, member_lower): bloque}
_typelib_ifaces = None # dict {iface_lower: iface}


def _idx():
    global _index
    if _index is None:
        if not os.path.exists(INDEX_DB):
            _index = False
        else:
            from .api_lookup import ApiIndex
            _index = ApiIndex(INDEX_DB)
    return _index


def _tl():
    global _typelib, _typelib_ifaces
    if _typelib is None:
        _typelib, _typelib_ifaces = {}, {}
        if os.path.exists(TYPELIB_TXT):
            with open(TYPELIB_TXT, "r", encoding="utf-8") as fh:
                for b in fh.read().split("\n\n"):
                    b = b.strip("\n")
                    m = re.match(r"^(I?\w+)\.(\w+)\(", b)
                    if not m:
                        continue
                    iface, member = m.group(1), m.group(2)
                    key = (iface.lower(), member.lower())
                    # una propiedad tiene bloque GET y bloque PUT: se muestran los dos
                    _typelib[key] = (_typelib[key] + "\n" + b) if key in _typelib else b
                    _typelib_ifaces.setdefault(iface.lower(), iface)
    return _typelib


def _norm_iface(iface):
    """Acepta 'FeatureManager', 'IFeatureManager', 'ifeaturemanager'."""
    _tl()
    low = iface.lower()
    for cand in (low, "i" + low):
        if cand in _typelib_ifaces:
            return _typelib_ifaces[cand]
    return iface if iface.startswith("I") else "I" + iface


def available():
    return {"typelib": os.path.exists(TYPELIB_TXT), "help_index": os.path.exists(INDEX_DB)}


def doc(iface, member):
    """Bloque de la typelib + ayuda semantica, en texto compacto para el modelo."""
    iface = _norm_iface(iface)
    parts = []
    tl = _tl().get((iface.lower(), member.lower()))
    if tl:
        parts.append("[typelib de esta instalacion]\n" + tl)
    idx = _idx()
    if idx:
        help_txt = idx.signature_help(iface, member)
        if "no está en el índice" not in help_txt:
            parts.append("[ayuda 2026 SP04]\n" + help_txt)
    if not parts:
        # ayuda para encontrar el nombre correcto
        hint = []
        if idx:
            hint += ["%s::%s" % (r["iface"], r["name"]) for r in idx.find_member(member)[:8]]
        near = [k[1] for k in _tl() if k[0] == iface.lower() and member.lower()[:5] in k[1]][:8]
        return (
            "%s::%s no esta ni en la typelib ni en la ayuda.\n"
            "Mismo nombre en otras interfaces: %s\n"
            "Nombres parecidos en %s: %s"
            % (iface, member, ", ".join(hint) or "-", iface, ", ".join(near) or "-")
        )
    if tl is None:
        parts.append("(no esta en la typelib volcada: puede que dump_api.py no llegara a esta interfaz, "
                     "o que el miembro no exista en esta version)")
    if not idx or "no está en el índice" in (idx.signature_help(iface, member) if idx else ""):
        parts.append("(sin entrada en la ayuda: solo tipos, sin semantica)")
    return "\n\n".join(parts)


def _hay_cache_typelib():
    """True solo si el cache se construyo leyendo la biblioteca de tipos.
    Un cache hecho solo con el indice de la ayuda no aporta nada nuevo: los
    ~2.000 valores que la ayuda no publica siguen faltando."""
    try:
        from . import const as sw_const
        return "typelib" in sw_const.fuentes()
    except Exception:
        return False


def _tabla_constantes():
    """swconst.json, o {} si no se ha generado. Nunca revienta."""
    try:
        from . import const as sw_const
        return sw_const.load()
    except Exception:
        return {}


def enum(name):
    idx = _idx()
    if not idx:
        raise FileNotFoundError("No existe %s: genera el indice con tools/index_build (ver README)." % INDEX_DB)
    e = idx.enum(name)
    if e is None:
        hit = idx.enum_value(name)
        if hit:
            e = idx.enum(hit["enum_name"])
            e["note"] = "%s pertenece a %s" % (name, hit["enum_name"])
        else:
            return {"error": "enum no encontrado: %s" % name}

    # La ayuda deja ~2.000 miembros sin valor numerico ("See System Options y
    # Document Properties"). La biblioteca de tipos de ESTA instalacion si los
    # tiene: se rellenan desde swconst.json y se marca su procedencia.
    tabla = _tabla_constantes()
    miembros, rellenados, sin_valor = [], 0, []
    for m in e["members"]:
        v, origen = m["v"], None
        if v is None and m["n"] in tabla:
            v, origen = tabla[m["n"]], "typelib"
            rellenados += 1
        elif v is None:
            sin_valor.append(m["n"])
        d = {"name": m["n"], "value": v, "desc": m["d"]}
        if origen:
            d["origen"] = origen
        miembros.append(d)

    out = {"name": e["name"], "summary": e["summary"], "members": miembros}
    if rellenados:
        out["nota_valores"] = (
            "%d de %d valores no los publica la ayuda; se han leido de la "
            "biblioteca de tipos (marcados con origen=typelib)."
            % (rellenados, len(miembros))
        )
    if sin_valor:
        out["sin_valor"] = sin_valor if len(sin_valor) <= 12 else (
            sin_valor[:12] + ["... y %d mas" % (len(sin_valor) - 12)])
        if not _hay_cache_typelib():
            out["sin_valor_motivo"] = (
                "Solo se han usado los valores que publica la ayuda. Con "
                "SOLIDWORKS abierto, "
                "'python -m solidworks_mcp.const --typelib' lee la biblioteca de tipos y "
                "rellena casi todos estos huecos."
            )
    if e.get("remarks"):
        out["remarks"] = e["remarks"][:3000]
    if e.get("tables"):
        out["tables"] = e["tables"]
    if e.get("note"):
        out["note"] = e["note"]
    return out


def members(iface, kind=None):
    idx = _idx()
    if not idx:
        raise FileNotFoundError("No existe %s: genera el indice con tools/index_build (ver README)." % INDEX_DB)
    iface = _norm_iface(iface)
    rows = idx.members(iface, kind)
    info = idx.iface_info(iface)
    if not rows and not info:
        return {"iface": iface, "count": 0, "note": "interfaz sin entrada en la ayuda; prueba sw_api_search con pattern='^%s\\.'" % iface}
    out = {"iface": iface, "count": len(rows), "members": rows}
    if info:
        out["info"] = {
            "namespace": info.get("ns"), "summary": info.get("summary"), "category": info.get("category"),
            "accessors": info.get("accessors"), "remarks": (info.get("remarks") or "")[:1500],
            "examples": info.get("examples", [])[:10],
        }
    return out


def example(query=None, id=None, iface=None, member=None, limit=8):
    """Ejemplos oficiales de codigo (VBA; VB.NET si no hay VBA). Con id -> el codigo
    completo; con iface/member -> los que enlaza esa interfaz o miembro; con query ->
    busqueda libre sobre titulo, descripcion y codigo."""
    idx = _idx()
    if not idx:
        raise FileNotFoundError("No existe %s: genera el indice con tools/index_build (ver README)." % INDEX_DB)
    if id is not None:
        e = idx.example(id)
        return e or {"error": "no hay ejemplo con id %s" % id}
    if iface:
        hits = idx.examples_for(_norm_iface(iface), member, limit)
    elif query:
        hits = idx.search_examples(query, limit)
    else:
        raise ValueError("Indica query, iface (y opcionalmente member) o id.")
    return {"count": len(hits), "matches": hits,
            "note": "sw_api_example(id=...) devuelve el codigo completo. Es VBA: traducir a Python "
                    "con enlace tardio (c.soft, VARIANT para [in,out]/Callout, metros)."}


def search_text(text, limit=15):
    idx = _idx()
    if not idx:
        raise FileNotFoundError("No existe %s: genera el indice con tools/index_build (ver README)." % INDEX_DB)
    try:
        rows = idx.search(text, limit)
    except Exception as exc:  # sintaxis FTS5
        rows = idx.search('"%s"' % text.replace('"', ""), limit)
    return [{"iface": r["iface"], "name": r["name"], "summary": r["summary"]} for r in rows]


def search_examples(text, limit=5):
    idx = _idx()
    return idx.search_examples(text, limit) if idx else []

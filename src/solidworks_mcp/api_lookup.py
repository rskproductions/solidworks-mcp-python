"""Consulta local del índice semántico de la API de SOLIDWORKS 2026 (help.solidworks.com, 2026 SP04).

Pensado para exponerse como herramientas MCP junto a `execute_python`:

    from solidworks_mcp.api_lookup import ApiIndex
    api = ApiIndex("sw_api_index.sqlite")
    api.lookup("IFeatureManager", "FeatureExtrusion3")   # firma + semántica de cada parámetro
    api.enum("swEndConditions_e")                         # miembros y valores
    api.members("ISketchManager")                         # qué se puede hacer con esa interfaz
    api.search("bounding box")                            # texto libre (FTS5)

Sin dependencias fuera de la stdlib.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class ApiIndex:
    def __init__(self, db_path: str | Path):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    # ---------- núcleo ----------
    def lookup(self, iface: str, member: str) -> dict[str, Any] | None:
        """Firma, parámetros con semántica, retorno, remarks (incl. tablas de marcas de selección), disponibilidad."""
        r = self.db.execute("SELECT * FROM members WHERE iface=? AND name=?", (iface, member)).fetchone()
        if r is None:
            # tolerante a mayúsculas y a la 'I' inicial
            r = self.db.execute(
                "SELECT * FROM members WHERE lower(iface) IN (?,?) AND lower(name)=?",
                (iface.lower(), ("i" + iface).lower(), member.lower()),
            ).fetchone()
        if r is None:
            return None
        d = dict(r)
        for k in ("params_json", "tables_json", "see_json", "examples_json"):
            d[k.replace("_json", "")] = json.loads(d.pop(k) or "null")
        return d

    def enum(self, name: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM enums WHERE name=? OR lower(name)=lower(?)", (name, name)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["members"] = json.loads(d.pop("members_json"))
        d["see"] = json.loads(d.pop("see_json"))
        d["tables"] = json.loads(d.pop("tables_json") or "[]")
        return d

    def select_type_string(self, enum_member: str) -> dict[str, Any] | None:
        """Para SelectByID2: 'swSelFACES' -> {'type_string': 'FACE', 'iface': 'IFace2'} (tabla de swSelectType_e)."""
        e = self.enum("swSelectType_e")
        if not e or not e["tables"]:
            return None
        for row in e["tables"][0][1:]:
            if row and row[0] == enum_member:
                return {"enum": row[0], "type_string": row[1].strip('"') if len(row) > 1 else "", "iface": row[2] if len(row) > 2 else "", "note": row[3] if len(row) > 3 else ""}
        return None

    def enum_value(self, member: str) -> dict[str, Any] | None:
        """Busca un miembro de enum por nombre (p.ej. 'swEndCondBlind') sin saber a qué enum pertenece."""
        r = self.db.execute("SELECT * FROM enum_members WHERE member=?", (member,)).fetchone()
        return dict(r) if r else None

    def members(self, iface: str, kind: str | None = None) -> list[dict[str, Any]]:
        """Lista completa de miembros de una interfaz con su resumen de una línea (para descubrir qué existe)."""
        q = "SELECT name, kind, summary FROM iface_members WHERE lower(iface)=lower(?)"
        args: list[Any] = [iface]
        if kind:
            q += " AND kind=?"; args.append(kind)
        rows = self.db.execute(q + " ORDER BY name", args).fetchall()
        if not rows and not iface.startswith("I"):
            return self.members("I" + iface, kind)
        return [dict(r) for r in rows]

    def search(self, text: str, limit: int = 15) -> list[dict[str, Any]]:
        """Búsqueda de texto libre sobre nombre, resumen, parámetros y remarks."""
        rows = self.db.execute(
            "SELECT iface, name, summary, bm25(fts) AS score FROM fts WHERE fts MATCH ? ORDER BY score LIMIT ?",
            (text, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def interfaces(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT iface FROM iface_members ORDER BY iface")]

    # ---------- indice completo (tablas nuevas; si no existen, devuelven vacio) ----------
    def _has(self, table: str) -> bool:
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None

    def meta(self) -> dict[str, str]:
        if not self._has("meta"):
            return {}
        return {r[0]: r[1] for r in self.db.execute("SELECT k, v FROM meta")}

    def iface_info(self, iface: str) -> dict[str, Any] | None:
        """Ficha de la interfaz: namespace, resumen, remarks, accesores, categoria, ejemplos."""
        if not self._has("ifaces"):
            return None
        for cand in (iface, "I" + iface):
            r = self.db.execute("SELECT * FROM ifaces WHERE lower(iface)=lower(?)", (cand,)).fetchone()
            if r:
                d = dict(r)
                d["examples"] = json.loads(d.pop("examples_json") or "[]")
                return d
        return None

    def search_examples(self, text: str, limit: int = 8) -> list[dict[str, Any]]:
        if not self._has("examples_fts"):
            return []
        q = "SELECT e.id, e.title, e.lang, e.refs_json, bm25(examples_fts) AS score FROM examples_fts " \
            "JOIN examples e ON e.id = examples_fts.rowid WHERE examples_fts MATCH ? ORDER BY score LIMIT ?"
        try:
            rows = self.db.execute(q, (text, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = self.db.execute(q, ('"%s"' % text.replace('"', ""), limit)).fetchall()
        return [{"id": r["id"], "title": r["title"], "lang": r["lang"], "refs": json.loads(r["refs_json"] or "[]")[:8]}
                for r in rows]

    def examples_for(self, iface: str, member: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Ejemplos que enlaza una interfaz o uno de sus miembros."""
        if not self._has("examples"):
            return []
        key = '"%s::%s"' % (iface, member) if member else '"%s' % iface
        rows = self.db.execute("SELECT id, title, lang, refs_json FROM examples WHERE refs_json LIKE ? LIMIT ?",
                               ("%" + key + "%", limit)).fetchall()
        return [{"id": r["id"], "title": r["title"], "lang": r["lang"]} for r in rows]

    def example(self, id_or_title) -> dict[str, Any] | None:
        """Ejemplo completo (codigo incluido) por id o por titulo exacto."""
        if not self._has("examples"):
            return None
        if isinstance(id_or_title, int) or str(id_or_title).isdigit():
            r = self.db.execute("SELECT * FROM examples WHERE id=?", (int(id_or_title),)).fetchone()
        else:
            r = self.db.execute("SELECT * FROM examples WHERE lower(title)=lower(?)", (str(id_or_title),)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["refs"] = json.loads(d.pop("refs_json") or "[]")
        return d

    # ---------- utilidades ----------
    def find_member(self, member: str) -> list[dict[str, Any]]:
        """Todas las interfaces que tienen un miembro con ese nombre (p.ej. 'SelectByID2', 'GetBox')."""
        rows = self.db.execute(
            "SELECT iface, name, kind, summary FROM members WHERE lower(name)=lower(?) ORDER BY iface", (member,)
        ).fetchall()
        return [dict(r) for r in rows]

    def signature_help(self, iface: str, member: str) -> str:
        """Texto compacto listo para meter en el contexto del modelo antes de escribir una llamada."""
        m = self.lookup(iface, member)
        if not m:
            return f"{iface}::{member}: no está en el índice."
        lines = [f"{m['iface']}::{m['name']} [{m['kind']}] -> {m['ret']}", f"  {m['summary']}", f"  sig: {m['sig']}"]
        for p in m["params"]:
            en = f"  ({', '.join(p['enums'])})" if p["enums"] else ""
            lines.append(f"  - {p['type']} {p['name']}: {p['desc']}{en}")
        if m.get("ret_desc"):
            lines.append(f"  returns: {m['ret_desc']}")
        if m["remarks"]:
            lines.append("  remarks: " + m["remarks"][:1200].replace("\n", " | "))
        for t in m["tables"][:2]:
            lines.append("  table: " + " || ".join(" / ".join(row) for row in t[:12]))
        if m.get("avail"):
            lines.append(f"  since: {m['avail']}")
        if m.get("examples"):
            lines.append("  ejemplos oficiales (sw_api_example): " + "; ".join(m["examples"][:6]))
        return "\n".join(lines)


if __name__ == "__main__":
    import sys

    api = ApiIndex(__import__("solidworks_mcp.paths", fromlist=["x"]).INDEX_DB)
    if len(sys.argv) == 3:
        print(api.signature_help(sys.argv[1], sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1].endswith("_e"):
        e = api.enum(sys.argv[1]); print(e["summary"]); [print(f"  {x['n']} = {x['v']}  {x['d']}") for x in e["members"]]
    elif len(sys.argv) == 2:
        for r in api.search(sys.argv[1]): print(f"{r['iface']}::{r['name']}  {r['summary'][:90]}")
    else:
        print(__doc__)

#!/usr/bin/env python
"""
Construye sw_api_index.sqlite COMPLETO a partir de parsed.json (parse_raw.py).

Mismo esquema que el indice anterior (members, enums, enum_members,
iface_members, iface_remarks, fts) para que sw_api_lookup.py siga funcionando,
mas tablas nuevas:

    ifaces       ficha de cada interfaz: namespace, resumen, remarks,
                 accesores (como se obtiene), categoria funcional, ejemplos
    examples     codigo de los ejemplos oficiales (VBA; VB.NET si no hay VBA)
                 con las interfaces/miembros que los enlazan
    examples_fts busqueda libre sobre titulo, descripcion y codigo
    categories   categoria funcional -> interfaz

Si una interfaz del indice anterior (sw_api_index.json) no tiene miembros en
parsed.json, se conservan los antiguos: nunca se pierde cobertura.

    python build_index_full.py [salida.sqlite]
"""

import json
import os
import posixpath
import re
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
from solidworks_mcp.paths import DATA, INDEX_DB  # noqa: E402
HERE = os.path.join(DATA, "index_build")
PARSED = os.path.join(HERE, "parsed.json")
OLD_JSON = os.path.join(HERE, "sw_api_index.json")   # indice previo opcional
DEFAULT_OUT = INDEX_DB
BASE = "https://help.solidworks.com"


def parse_sig(sig):
    """'Feature FeatureExtrusion3( System.bool Sd, ... )' -> (ret, [(type,name)], es_propiedad).
    Igual que en build_index.py."""
    if not sig:
        return None, [], False
    s = sig.strip()
    mp = re.match(r"^(.+?)\s+(\w+)\s*\{\s*(get;)?\s*(set;)?\s*\}$", s)
    if mp:
        return mp.group(1).replace("System.", ""), [], True
    m = re.match(r"^(.+?)\s+(\w+)\s*\((.*)\)\s*$", s)
    if not m:
        return None, [], False
    ret = m.group(1).replace("System.", "").strip()
    args = m.group(3).strip()
    params = []
    if args:
        for a in re.split(r",(?![^\[]*\])", args):
            a = a.strip().replace("System.", "")
            mm = re.match(r"^((?:out |ref |params )?.+?)\s+(\w+)$", a)
            params.append((mm.group(1).strip(), mm.group(2)) if mm else (a, ""))
    return ret, params, False


def member_record(iface, name, r):
    ret, sp, is_prop = parse_sig(r.get("sig"))
    docp = {p["name"]: p for p in r.get("params", [])}
    params = []
    for typ, pn in sp:
        d = docp.get(pn, {})
        en = list(d.get("enums", [])) or sorted(set(re.findall(r"\bsw\w+_e\b", d.get("desc", ""))))
        params.append({"name": pn, "type": typ, "desc": d.get("desc", ""), "enums": en})
    for pn, d in docp.items():
        if pn not in {p["name"] for p in params}:
            params.append({"name": pn, "type": "", "desc": d.get("desc", ""), "enums": d.get("enums", [])})
    rem = r.get("remarks") or {}
    return {
        "name": name, "iface": iface, "kind": "property" if is_prop else "method",
        "summary": r.get("summary", ""), "sig": r.get("sig"), "ret": ret, "ret_desc": r.get("ret"),
        "params": params, "remarks": rem.get("text", "") if rem else "", "tables": rem.get("tables", []) if rem else [],
        "see": r.get("see", []), "examples": [e["t"] for e in r.get("ex", [])], "avail": r.get("avail"),
        "url": BASE + r["url"], "_ex": r.get("ex", []), "_urldir": posixpath.dirname(r["url"]),
    }


def enum_record(name, r):
    mems = []
    for mem in r.get("members", []):
        v, d = mem.get("v"), (mem.get("d") or "").strip()
        if v is None:
            mm = re.match(r"^(-?\d+)(?:\s+or\s+(0x[0-9A-Fa-f]+))?\s*[;=,]?\s*(.*)$", d) or \
                 re.match(r"^(0x[0-9A-Fa-f]+)\s*[;=,]?\s*(.*)$", d)
            if mm:
                g = mm.groups()
                v = int(g[0], 16) if g[0].lower().startswith("0x") else int(g[0])
                d = g[-1].strip()
        mems.append({"n": mem["n"], "v": v, "d": d})
    return {"name": name, "summary": r.get("summary", ""), "members": mems, "see": r.get("see", []),
            "remarks": r.get("remarks") or "", "tables": r.get("tables") or [], "ns": r.get("ns", "swconst")}


def main(out):
    parsed = json.load(open(PARSED, encoding="utf-8"))
    old = json.load(open(OLD_JSON, encoding="utf-8")) if os.path.exists(OLD_JSON) else {"interfaces": {}, "enums": {}}

    interfaces, info = {}, {}
    for iface, d in parsed["interfaces"].items():
        mems = {n: member_record(iface, n, r) for n, r in d["members"].items()}
        interfaces[iface] = {"members": mems, "summary_list": d["summary_list"], "remarks": d.get("remarks"), "ns": d.get("ns")}
        if d.get("info"):
            info[iface] = d["info"]
    conservadas = 0
    for iface, d in old.get("interfaces", {}).items():
        cur = interfaces.get(iface)
        if cur is None or not cur["members"]:
            interfaces[iface] = {"members": d["members"], "summary_list": d["summary_list"],
                                 "remarks": d.get("remarks"), "ns": "sldworks"}
            conservadas += 1
    enums = {n: enum_record(n, r) for n, r in parsed["enums"].items()}
    for n, e in old.get("enums", {}).items():
        enums.setdefault(n, dict(e, ns="swconst"))

    cat_of = {}
    for cat, ifs in parsed.get("categories", {}).items():
        for i in ifs:
            cat_of.setdefault(i, cat)

    # ---- ejemplos: href (absoluto) -> quien lo enlaza ----
    refs = defaultdict(set)
    for iface, d in interfaces.items():
        for n, m in d["members"].items():
            for e in m.get("_ex", []) or []:
                if e.get("h"):
                    refs[posixpath.normpath(posixpath.join(m["_urldir"], e["h"]))].add("%s::%s" % (iface, n))
    for iface, i in info.items():
        base = posixpath.dirname(i["url"])
        for e in i.get("ex", []):
            if e.get("h"):
                refs[posixpath.normpath(posixpath.join(base, e["h"]))].add(iface)

    if os.path.exists(out):
        os.remove(out)
    con = sqlite3.connect(out)
    con.executescript("""
    CREATE TABLE members(iface TEXT, name TEXT, kind TEXT, summary TEXT, sig TEXT, ret TEXT, ret_desc TEXT,
      params_json TEXT, remarks TEXT, tables_json TEXT, see_json TEXT, examples_json TEXT, avail TEXT, url TEXT,
      PRIMARY KEY(iface,name));
    CREATE TABLE enums(name TEXT PRIMARY KEY, summary TEXT, members_json TEXT, see_json TEXT, remarks TEXT, tables_json TEXT, ns TEXT);
    CREATE TABLE enum_members(enum_name TEXT, member TEXT, value INTEGER, desc TEXT);
    CREATE TABLE iface_members(iface TEXT, name TEXT, kind TEXT, summary TEXT);
    CREATE TABLE iface_remarks(iface TEXT PRIMARY KEY, remarks TEXT);
    CREATE TABLE ifaces(iface TEXT PRIMARY KEY, ns TEXT, summary TEXT, remarks TEXT, accessors TEXT,
      category TEXT, examples_json TEXT, url TEXT);
    CREATE TABLE examples(id INTEGER PRIMARY KEY, title TEXT, lang TEXT, desc TEXT, code TEXT, url TEXT, refs_json TEXT);
    CREATE TABLE categories(category TEXT, iface TEXT);
    CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE VIRTUAL TABLE fts USING fts5(iface, name, summary, params, remarks);
    CREATE VIRTUAL TABLE examples_fts USING fts5(title, desc, code);
    CREATE INDEX im_iface ON iface_members(iface);
    CREATE INDEX em_member ON enum_members(member);
    CREATE INDEX m_name ON members(name);
    """)
    n_m = 0
    for iface, d in interfaces.items():
        for nm, m in d["members"].items():
            con.execute("INSERT OR REPLACE INTO members VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                iface, nm, m["kind"], m["summary"], m["sig"], m["ret"], m["ret_desc"],
                json.dumps(m["params"], ensure_ascii=False), m["remarks"], json.dumps(m["tables"], ensure_ascii=False),
                json.dumps(m["see"]), json.dumps(m["examples"]), m["avail"], m["url"]))
            con.execute("INSERT INTO fts(iface,name,summary,params,remarks) VALUES(?,?,?,?,?)", (
                iface, nm, m["summary"], " ".join("%s %s" % (p["name"], p["desc"]) for p in m["params"]), m["remarks"]))
            n_m += 1
        sl = d["summary_list"]
        for kind, key in (("method", "methods"), ("property", "props"), ("event", "events")):
            for row in sl.get(key, []) or []:
                con.execute("INSERT INTO iface_members VALUES(?,?,?,?)", (iface, row[0], kind, row[1]))
        if d.get("remarks"):
            con.execute("INSERT OR REPLACE INTO iface_remarks VALUES(?,?)", (iface, d["remarks"]))
    for iface, i in info.items():
        con.execute("INSERT OR REPLACE INTO ifaces VALUES(?,?,?,?,?,?,?,?)", (
            iface, interfaces.get(iface, {}).get("ns"), i.get("summary", ""), i.get("remarks", ""),
            i.get("accessors", ""), cat_of.get(iface), json.dumps([e["t"] for e in i.get("ex", [])]), BASE + i["url"]))
        con.execute("INSERT INTO fts(iface,name,summary,params,remarks) VALUES(?,?,?,?,?)", (
            iface, "(interfaz)", i.get("summary", ""), i.get("accessors", ""), i.get("remarks", "")))
    for cat, ifs in parsed.get("categories", {}).items():
        for i in ifs:
            con.execute("INSERT INTO categories VALUES(?,?)", (cat, i))
    for nm, e in enums.items():
        con.execute("INSERT OR REPLACE INTO enums VALUES(?,?,?,?,?,?,?)", (
            nm, e["summary"], json.dumps(e["members"], ensure_ascii=False), json.dumps(e["see"]),
            e.get("remarks", ""), json.dumps(e.get("tables", []), ensure_ascii=False), e.get("ns")))
        for mem in e["members"]:
            con.execute("INSERT INTO enum_members VALUES(?,?,?,?)", (nm, mem["n"], mem["v"], mem["d"]))
    n_x = 0
    for url, x in parsed.get("examples", {}).items():
        key = posixpath.normpath(url)
        con.execute("INSERT INTO examples(title,lang,desc,code,url,refs_json) VALUES(?,?,?,?,?,?)", (
            x.get("title", ""), x.get("lang", ""), x.get("desc", ""), x.get("code", ""), BASE + url,
            json.dumps(sorted(refs.get(key, [])))))
        con.execute("INSERT INTO examples_fts(rowid,title,desc,code) VALUES(last_insert_rowid(),?,?,?)", (
            x.get("title", ""), x.get("desc", ""), x.get("code", "")))
        n_x += 1
    meta = {"source": "help.solidworks.com 2026 (SP04)", "interfaces": len(interfaces), "members": n_m,
            "enums": len(enums), "examples": n_x, "categories": len(parsed.get("categories", {})),
            "conservadas_del_indice_anterior": conservadas}
    for k, v in meta.items():
        con.execute("INSERT INTO meta VALUES(?,?)", (k, str(v)))
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(json.dumps(meta, ensure_ascii=False))
    print("sqlite MB:", round(os.path.getsize(out) / 1e6, 2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)

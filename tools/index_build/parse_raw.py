#!/usr/bin/env python
"""
Parsea lo que descargo crawl_full.py (raw/*.jsonl.gz) y lo deja en
parsed.json con la MISMA forma que usa build_index.py (interfaces con sus
miembros, enums) mas tres cosas nuevas: ficha de cada interfaz (namespace,
resumen, remarks, accesores, ejemplos), ejemplos de codigo y categorias
funcionales.

Es un port a Python (solo stdlib, html.parser) de extractor.js: mismas
reglas de extraccion, para que el indice nuevo y el antiguo casen.

    python parse_raw.py            -> parsed.json
"""

import gzip
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
from solidworks_mcp.paths import DATA  # noqa: E402
HERE = os.path.join(DATA, "index_build")
RAW = os.path.join(HERE, "raw")

# --------------------------------------------------------------------------
# DOM minimo sobre html.parser (sin bs4: el pipeline tiene que poder correr
# en la maquina con SOLIDWORKS, que solo tiene la stdlib)
# --------------------------------------------------------------------------

VOID = {"br", "img", "input", "hr", "meta", "link", "area", "base", "col", "embed",
        "param", "source", "track", "wbr"}
BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "div", "tr", "pre", "table", "dt", "dd", "ul", "ol"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs), [], parent

    def get(self, k, d=None):
        return self.attrs.get(k, d)

    def classes(self):
        return (self.attrs.get("class") or "").split()

    def iter(self):
        for ch in self.children:
            if isinstance(ch, Node):
                yield ch
                yield from ch.iter()

    def find_all(self, tag=None, cls=None, id=None):
        return [n for n in self.iter()
                if (tag is None or n.tag == tag)
                and (cls is None or cls in n.classes())
                and (id is None or n.get("id") == id)]

    def find(self, tag=None, cls=None, id=None):
        for n in self.iter():
            if ((tag is None or n.tag == tag) and (cls is None or cls in n.classes())
                    and (id is None or n.get("id") == id)):
                return n
        return None

    def text(self):
        out = []
        for ch in self.children:
            out.append(ch if isinstance(ch, str) else ch.text())
        return "".join(out)

    def text_lines(self, skip_tables=False):
        """Texto con saltos de linea en los elementos de bloque (como remarks() en JS)."""
        out = []

        def rec(n):
            for ch in n.children:
                if isinstance(ch, str):
                    out.append(ch)
                elif ch.tag == "br":
                    out.append("\n")
                elif skip_tables and ch.tag == "table":
                    out.append("[table]\n")
                else:
                    rec(ch)
                    if ch.tag in BLOCK:
                        out.append("\n")
        rec(self)
        s = "".join(out).replace(" ", " ")
        return "\n".join(l for l in (re.sub(r"\s+", " ", x).strip() for x in s.split("\n")) if l)

    def element_siblings(self):
        return [c for c in self.parent.children if isinstance(c, Node)] if self.parent else [self]

    def prev_element(self):
        sib = self.element_siblings()
        i = sib.index(self)
        return sib[i - 1] if i > 0 else None

    def next_element(self):
        sib = self.element_siblings()
        i = sib.index(self)
        return sib[i + 1] if i + 1 < len(sib) else None


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent          # cierra hasta la etiqueta que casa (tolerante)

    def handle_data(self, data):
        self.cur.children.append(data)


def dom(html):
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


# --------------------------------------------------------------------------
# extraccion (mismas reglas que extractor.js)
# --------------------------------------------------------------------------

def sections(root):
    out = {}
    for d in root.find_all("div", cls="section"):
        h = d.prev_element()
        while h is not None and not re.match(r"^h[1-4]$", h.tag):
            h = h.prev_element()
        name = clean(h.text()).lower() if h is not None else (d.get("id") or "?")
        out.setdefault(name, d)
    return out


def csharp_sig(root):
    cs = root.find(id="Syntax_CS")
    if cs is not None:
        pre = cs.find("pre")
        if pre is not None:
            return clean(pre.text())
    pres = root.find_all("pre")
    for p in pres:
        t = clean(p.text())
        if not re.search(r"ByVal|Dim |Function |Sub |Property ", t) and "^" not in t and not t.startswith("&"):
            return t
    return clean(pres[0].text()) if pres else None


def params(root):
    out = []
    dl = next((d for d in root.find_all("dl") if d.find("dt") is not None), None)
    if dl is None:
        return out
    for dt in dl.find_all("dt"):
        dd = dt.next_element()
        if dd is not None and dd.tag != "dd":
            dd = None
        enums = []
        if dd is not None:
            for a in dd.find_all("a"):
                t = clean(a.text())
                if re.match(r"^sw\w+_e$", t) and t not in enums:
                    enums.append(t)
        out.append({"name": clean(dt.text()), "desc": clean(dd.text()) if dd else "", "enums": enums})
    return out


def table_rows(tbl):
    return [[clean(c.text()) for c in tr.iter() if c.tag in ("th", "td")] for tr in tbl.find_all("tr")]


def remarks(secs):
    d = secs.get("remarks")
    if d is None:
        return None
    tables = [table_rows(t) for t in d.find_all("table")]
    return {"text": d.text_lines(skip_tables=True)[:8000], "tables": tables}


def see_also(secs):
    d = secs.get("see also")
    return [clean(a.text()) for a in d.find_all("a") if clean(a.text())] if d is not None else []


def examples(secs):
    d = secs.get("example") or secs.get("examples")
    if d is None:
        return []
    # "See the IThreadFeatureData examples." enlaza a la pagina de la interfaz, no a un
    # ejemplo: solo cuentan los enlaces cuyo destino es una pagina de ejemplo.
    return [{"t": clean(a.text()), "h": a.get("href")} for a in d.find_all("a")
            if clean(a.text()) and re.search(r"example", a.get("href") or "", re.I)]


def summary(root):
    body = root.find(id="mainbody") or root
    full = body.text_lines()
    i = full.find(".NET Syntax")
    head = full[:i] if i > 0 else full[:600]
    lines = [l.strip() for l in head.split("\n") if l.strip()]
    return " ".join(lines)


def _heading_value(root, nombre):
    for h in root.iter():
        if re.match(r"^h[1-4]$", h.tag) and clean(h.text()).lower() == nombre:
            partes, e = [], h.next_element()
            while e is not None and not re.match(r"^h[1-4]$", e.tag):
                partes.append(clean(e.text()))
                e = e.next_element()
            return clean(" ".join(partes)) or None
    return None


def parse_member(root):
    secs = sections(root)
    ret = clean(secs["return value"].text()) if "return value" in secs else None
    avail = clean(secs["availability"].text()) if "availability" in secs else None
    ret = ret or _heading_value(root, "return value")
    avail = avail or _heading_value(root, "availability")
    return {"k": "m", "summary": summary(root), "sig": csharp_sig(root), "params": params(root),
            "ret": ret, "remarks": remarks(secs), "see": see_also(secs), "ex": examples(secs), "avail": avail}


def parse_enum(root):
    secs = sections(root)
    tbl = next((t for t in root.find_all("table") if re.search(r"member", clean(t.text())[:40], re.I)), None)
    rows = table_rows(tbl)[1:] if tbl is not None else []
    members = []
    for r in rows:
        if not r:
            continue
        name, rest = r[0], " ".join(r[1:])
        m = re.match(r"^(-?\d+)\s*(?:=\s*(.*))?$", rest)
        members.append({"n": name, "v": int(m.group(1)), "d": m.group(2) or ""} if m else {"n": name, "v": None, "d": rest})
    rm = remarks(secs)
    return {"k": "e", "summary": summary(root), "members": members, "see": see_also(secs),
            "remarks": rm["text"] if rm else "", "tables": rm["tables"] if rm else []}


def parse_members_list(root):
    def grab(sid):
        sec = root.find(id=sid)
        out = []
        if sec is None:
            return out
        for tr in sec.find_all("tr"):
            n = tr.find("td", cls="MembersLinkCell")
            d = tr.find("td", cls="MembersDescriptionCell")
            if n is not None:
                a = n.find("a")
                out.append([clean(n.text()), clean(d.text()) if d else "", a.get("href") if a else None])
        return out
    secs = sections(root)
    rm = remarks(secs)
    return {"k": "l", "methods": grab("publicMethodsSection"), "props": grab("publicPropertiesSection"),
            "events": grab("publicEventsSection"), "remarks": (rm["text"][:3000] if rm and rm["text"] else None)}


def parse_iface(root):
    secs = sections(root)
    rm = remarks(secs)
    acc = secs.get("accessors")
    return {"k": "i", "summary": summary(root), "remarks": rm["text"] if rm else "",
            "tables": rm["tables"] if rm else [], "accessors": acc.text_lines() if acc is not None else "",
            "ex": examples(secs), "see": see_also(secs)}


def _code_text(n):
    out = []

    def rec(x):
        for ch in x.children:
            if isinstance(ch, str):
                out.append(ch)
            elif ch.tag == "br":
                out.append("\n")
            else:
                rec(ch)
    rec(n)
    s = "".join(out).replace("\u00a0", " ")
    # el HTML de la ayuda parte las lineas largas con saltos "de maquetacion"
    # seguidos de 'As Tipo': se reunen
    s = re.sub(r"[ \t]*\n[ \t]*(?=As\s)", " ", s)
    return s.strip("\n")


def parse_example(root, title):
    """El codigo de un ejemplo NO esta solo en <pre>: la cabecera va en un
    <pre> y el cuerpo, linea a linea, en <p class="APICODE">. Se recogen los
    dos, en orden de documento."""
    body = root.find(id="mainbody") or root
    trozos = [_code_text(n) for n in body.iter()
              if n.tag == "pre" or (n.tag == "p" and "APICODE" in n.classes())]
    code = "\n".join(t for t in trozos if t.strip())
    full = body.text_lines()
    desc = full.split("\n")[1:4] if full else []
    m = re.search(r"\((VBA|VB\.NET|C#|C\+\+)\)", title)
    return {"k": "x", "lang": m.group(1) if m else "", "desc": " ".join(desc)[:600], "code": code}


def parse_categories(root):
    """{categoria: [interfaces]} a partir de la pagina Functional Categories."""
    # Cada categoria es un <h4> ("Assembly Interfaces", pero tambien "Sketch" o
    # "Motion Studies" a secas) seguido de una lista de enlaces a interfaces.
    cats, actual = {}, None
    for n in root.iter():
        if re.match(r"^h[1-4]$", n.tag):
            t = clean(n.text())
            if t and len(t) < 60 and "top" not in t.lower():
                actual = t if t.endswith("Interfaces") else t + " Interfaces"
                cats.setdefault(actual, [])
        elif n.tag == "a" and n.get("href") and actual:
            t = clean(n.text())
            if re.match(r"^I[A-Z0-9]\w*$", t) and t not in cats[actual]:
                cats[actual].append(t)
    return {k: v for k, v in cats.items() if v}


# --------------------------------------------------------------------------

# La ayuda mezcla "SolidWorks.Interop" y "SOLIDWORKS.Interop" en las URLs: sin distinguir mayusculas.
RX_MEMBER = re.compile(r"\.Interop\.(\w+)~SolidWorks\.Interop\.\w+\.(\w+)~(\w+)\.html$", re.I)
RX_TYPE = re.compile(r"\.Interop\.(\w+)~SolidWorks\.Interop\.\w+\.(\w+?)(_members)?\.html$", re.I)


def read_stage(stage):
    p = os.path.join(RAW, stage + ".jsonl.gz")
    out = []
    if not os.path.exists(p):
        return out
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except (EOFError, OSError):
        pass
    return out


def main(out_path=None):
    out_path = out_path or os.path.join(HERE, "parsed.json")
    res = {"interfaces": {}, "enums": {}, "examples": {}, "categories": {}, "errors": []}

    def iface_entry(ns, name):
        return res["interfaces"].setdefault(name, {"ns": ns, "members": {}, "summary_list": {"methods": [], "props": []},
                                                   "remarks": None, "info": None})

    for st in ("types", "members"):
        filas = read_stage(st)
        ok_urls = {r["url"] for r in filas if not r.get("err")}
        vistos = set()
        for r in filas:
            if r.get("err"):
                if r["url"] not in ok_urls and r["url"] not in vistos:
                    res["errors"].append([r["url"], r["err"]])   # fallo que ningun reintento arreglo
                    vistos.add(r["url"])
                continue
            if r["url"] in vistos:
                continue
            vistos.add(r["url"])
            root = dom(r["html"])
            url = r["url"]
            if r["kind"] == "member":
                m = RX_MEMBER.search(url)
                if not m:
                    continue
                ns, iface, name = m.groups()
                rec = parse_member(root)
                rec["url"] = url
                iface_entry(ns, iface)["members"][name] = rec
            else:
                m = RX_TYPE.search(url)
                if not m:
                    continue
                ns, name, _ = m.groups()
                if r["kind"] == "enum":
                    e = parse_enum(root)
                    e["ns"] = ns
                    res["enums"][name] = e
                elif r["kind"] == "members_list":
                    l = parse_members_list(root)
                    ent = iface_entry(ns, name)
                    ent["summary_list"] = {"methods": [x[:2] for x in l["methods"]], "props": [x[:2] for x in l["props"]],
                                           "events": [x[:2] for x in l["events"]]}
                    if l["remarks"]:
                        ent["remarks"] = l["remarks"]
                elif r["kind"] == "iface":
                    i = parse_iface(root)
                    i["url"] = url
                    iface_entry(ns, name)["info"] = i
    ex_rows = read_stage("examples")
    ex_ok = {r["url"] for r in ex_rows if not r.get("err")}
    for r in ex_rows:
        if r.get("err"):
            if r["url"] not in ex_ok:
                res["errors"].append([r["url"], r["err"]])
            continue
        e = parse_example(dom(r["html"]), r.get("title", ""))
        e["title"] = r.get("title", "")
        res["examples"][r["url"]] = e
    for r in read_stage("extras"):
        if r.get("err") or r["kind"] != "categories":
            continue
        res["categories"] = parse_categories(dom(r["html"]))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False)
    print("interfaces %d (con miembros %d, miembros %d), enums %d, ejemplos %d, categorias %d, errores %d" % (
        len(res["interfaces"]), sum(1 for v in res["interfaces"].values() if v["members"]),
        sum(len(v["members"]) for v in res["interfaces"].values()), len(res["enums"]),
        len(res["examples"]), len(res["categories"]), len(res["errors"])))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

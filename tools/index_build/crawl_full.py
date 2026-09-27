#!/usr/bin/env python
"""
Crawler de la ayuda de la API de SOLIDWORKS 2026 (help.solidworks.com).

Descarga el fragmento helpText (va embebido en __NEXT_DATA__) de cada pagina
y lo guarda tal cual en raw/<etapa>.jsonl.gz, una linea JSON por pagina:
{"url", "kind", "title", "html"} o {"url", "kind", "err"}. El parseo va
aparte (parse_raw.py): asi se puede reparsear sin volver a descargar.

Reanudable: al arrancar lee todo lo que ya hay en raw/ y no repite URLs.

Se usa urllib y no requests: requests valida TLS con su propio paquete de
certificados (certifi) y aqui falla con CERTIFICATE_VERIFY_FAILED; urllib usa
el almacen de certificados de Windows y verifica bien.

Por que Python y no extractor.js en el navegador: el navegador obliga a sacar
los datos pasando por la herramienta (muy caro con ~15.000 paginas). Aqui se
escriben directamente a disco. Se pide gzip: cada pagina pesa ~1 MB en
claro (el arbol de navegacion viaja entero en cada una) y ~110 KB comprimida.

Etapas (en orden; cada una depende de la anterior):
    ns        paginas de namespace -> lista de interfaces y enums
    types     pagina de cada interfaz (+ su _members) y de cada enum nuevo
    members   pagina de cada miembro de las interfaces NO indexadas antes
    examples  ejemplos de codigo (VBA; VB.NET si no hay VBA) enlazados
    extras    Functional Categories y Release Notes

    python crawl_full.py ns types           solo esas etapas
    python crawl_full.py all                todas
    python crawl_full.py status             recuento de lo descargado
"""

import gzip
import json
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import gzip as _gz
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()
from solidworks_mcp.paths import DATA, INDEX_DB  # noqa: E402
RAW = os.path.join(DATA, "index_build", "raw")
BASE = "https://help.solidworks.com"
API = "/2026/english/api"

# namespace -> carpeta de la ayuda
NAMESPACES = {
    "sldworks": "sldworksapi", "swconst": "swconst", "swcommands": "swcommands",
    "swmotionstudy": "swmotionstudyapi", "swdimxpert": "swdimxpertapi",
    "swpublished": "swpublishedapi", "sw3dprinter": "sw3dprinterapi",
    "dsgnchk": "dsgnchkapi", "swhtmlcontrol": "swhtmlcontrolapi",
    "swscanto3d": "swscanto3dapi",
}

RX_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
RX_A = re.compile(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.S)
RX_TAG = re.compile(r"<[^>]+>")

WORKERS = 8
_HEADERS = {"User-Agent": "Mozilla/5.0 (sw_mcp index builder)", "Accept-Encoding": "gzip"}


def http_get(url, timeout=40):
    """(status, texto). Descomprime gzip a mano: urllib no lo hace solo."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = _gz.decompress(raw)
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(os.path.join(RAW, "_crawl.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fetch(path, kind):
    url = path if path.startswith("http") else BASE + path
    err = None
    for intento in range(4):
        try:
            status, texto = http_get(url)
            if status == 404:
                return {"url": path, "kind": kind, "err": "404"}
            if status >= 400:
                raise RuntimeError("HTTP %d" % status)
            m = RX_NEXT.search(texto)
            if not m:
                return {"url": path, "kind": kind, "err": "no-next-data"}
            h = json.loads(m.group(1))["props"]["pageProps"].get("helpContentData") or {}
            t = h.get("helpText")
            if not t:
                return {"url": path, "kind": kind, "err": "no-content:" + str(h.get("ErrorTitle"))}
            t = re.sub(r"<xml>.*?</xml>", "", t, flags=re.S)
            t = re.sub(r"<script.*?</script>", "", t, flags=re.S)
            return {"url": path, "kind": kind, "title": h.get("title", ""), "html": t}
        except Exception as exc:
            err = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(2.0 * (intento + 1))
    return {"url": path, "kind": kind, "err": err}


# --------------------------------------------------------------------------
# almacen: jsonl.gz por etapa, reanudable
# --------------------------------------------------------------------------

def read_stage(stage):
    """Lee raw/<stage>.jsonl.gz. Si el proceso anterior murio a medias, el gzip
    queda truncado (EOFError al final): se conserva lo legible y se REESCRIBE
    el fichero limpio, porque un miembro gzip anadido detras de uno truncado
    ya no se podria leer."""
    p = os.path.join(RAW, stage + ".jsonl.gz")
    out = []
    if not os.path.exists(p):
        return out
    truncado = False
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        truncado = True      # linea a medias
    except (EOFError, OSError):
        truncado = True
    if truncado:
        tmp = p + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            for r in out:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, p)
        log("  %s: fichero truncado por un corte previo, reescrito con %d lineas validas" % (stage, len(out)))
    return out


def run_stage(stage, items):
    """items: [(path, kind)]. Descarga lo que falte y lo anade a raw/stage."""
    hechos = {r["url"] for r in read_stage(stage) if not r.get("err")}
    pend = [(p, k) for p, k in dict.fromkeys(items) if p not in hechos]
    log("etapa %s: %d total, %d ya hechas, %d pendientes" % (stage, len(items), len(hechos), len(pend)))
    if not pend:
        return
    lock = threading.Lock()
    path = os.path.join(RAW, stage + ".jsonl.gz")
    n = [0, 0]
    t0 = time.time()
    with gzip.open(path, "at", encoding="utf-8") as fh:
        def trabajo(it):
            r = fetch(*it)
            with lock:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                n[0] += 1
                if r.get("err"):
                    n[1] += 1
                if n[0] % 200 == 0:
                    fh.flush()
                    v = n[0] / max(time.time() - t0, 1e-6)
                    log("  %s %d/%d (%d errores) %.1f pag/s, quedan ~%d min"
                        % (stage, n[0], len(pend), n[1], v, (len(pend) - n[0]) / max(v, 1e-6) / 60))
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(trabajo, pend))
    log("etapa %s terminada: %d descargadas, %d errores, %.0f s" % (stage, n[0], n[1], time.time() - t0))


# --------------------------------------------------------------------------
# extraccion ligera de enlaces (el parseo completo va en parse_raw.py)
# --------------------------------------------------------------------------

def links(html):
    return [(h, RX_TAG.sub("", t).strip()) for h, t in RX_A.findall(html)]


def ya_indexado():
    """Interfaces y enums que ya estan en sw_api_index.sqlite (no se repiten sus miembros)."""
    if not os.path.exists(INDEX_DB):
        return set(), set()
    con = sqlite3.connect(INDEX_DB)
    try:
        ifs = {r[0] for r in con.execute("SELECT DISTINCT iface FROM members")}
        ens = {r[0] for r in con.execute("SELECT name FROM enums")}
    finally:
        con.close()
    return ifs, ens


def tipos_de_namespaces():
    """[(ns, carpeta, nombre, href_abs, clase)] clase in {iface, enum, otro}"""
    out = []
    for r in read_stage("ns"):
        if r.get("err"):
            continue
        ns = r["url"].split("SolidWorks.Interop.")[1].split("~")[0]
        carpeta = NAMESPACES[ns]
        for h, t in links(r["html"]):
            if "~" not in h or not h.endswith(".html") or "_namespace" in h:
                continue
            nombre = h.rsplit(".", 2)[-2]
            if nombre != t:
                continue
            if nombre.endswith("_e"):
                clase = "enum"
            elif re.match(r"^I[A-Z0-9]", nombre):
                clase = "iface"
            else:
                clase = "otro"
            # normpath: algunos enlaces cruzan de namespace ("../sldworksapi/...") y el
            # servidor responde 403 a las rutas con "..": se resuelven antes de pedirlas.
            import posixpath
            out.append((ns, carpeta, nombre, posixpath.normpath("%s/%s/%s" % (API, carpeta, h)), clase))
    return list(dict.fromkeys(out))


def etapa_ns():
    items = [("%s/%s/SolidWorks.Interop.%s~SolidWorks.Interop.%s_namespace.html" % (API, c, ns, ns), "ns")
             for ns, c in NAMESPACES.items()]
    run_stage("ns", items)
    tipos = tipos_de_namespaces()
    from collections import Counter
    log("tipos:", dict(Counter((t[0], t[4]) for t in tipos)))


def etapa_types():
    ifs_hechas, enums_hechos = ya_indexado()
    items = []
    for ns, c, nombre, href, clase in tipos_de_namespaces():
        if clase == "iface":
            items.append((href, "iface"))
            items.append((href[:-5] + "_members.html", "members_list"))
        elif clase == "enum" and nombre not in enums_hechos:
            items.append((href, "enum"))
    run_stage("types", items)


def etapa_members(todas=False):
    """todas=False: solo interfaces que no estaban en el indice anterior.
    todas=True: tambien las ya indexadas (reparseo uniforme + enlaces a ejemplos)."""
    ifs_hechas, _ = ya_indexado()
    if todas:
        ifs_hechas = set()
    items = []
    for r in read_stage("types"):
        if r.get("err") or r["kind"] != "members_list":
            continue
        base = r["url"].rsplit("/", 1)[0]
        iface = r["url"].rsplit(".", 2)[-2].replace("_members", "")
        if iface in ifs_hechas:
            continue
        for h, t in links(r["html"]):
            if ("." + iface + "~") in h and h.endswith(".html"):
                items.append((base + "/" + h.split("/")[-1], "member"))
    run_stage("members", items)


def etapa_examples():
    cand = {}
    for st in ("types", "members"):
        for r in read_stage(st):
            if r.get("err"):
                continue
            base = r["url"].rsplit("/", 1)[0]
            for h, t in links(r["html"]):
                if "xample" not in h or not re.search(r"\.html?$", h):
                    continue
                full = h if h.startswith("/") else base + "/" + h
                cand[full] = t
    # agrupar por ejemplo: "Create a Thread Feature (VBA)" / "(VB.NET)" / "(C#)"
    grupos = {}
    for href, t in cand.items():
        m = re.match(r"^(.*?)\s*\((VBA|VB\.NET|C#|C\+\+)\)\s*$", t)
        clave, leng = (m.group(1), m.group(2)) if m else (t, "?")
        grupos.setdefault(clave, {})[leng] = href
    items = []
    for clave, por in grupos.items():
        href = por.get("VBA") or por.get("VB.NET") or por.get("?") or por.get("C#")
        if href:
            items.append((href, "example"))
    run_stage("examples", items)


def etapa_extras():
    items = [("%s/sldworksapi/FunctionalCategories-sldworksapi.html" % API, "categories"),
             ("%s/sldworksapi/ReleaseNotes-sldworksapi.html" % API, "release_notes")]
    run_stage("extras", items)


def status():
    for st in ("ns", "types", "members", "examples", "extras"):
        rs = read_stage(st)
        from collections import Counter
        log("%-9s %6d paginas, %4d errores, por tipo %s" % (
            st, len(rs), sum(1 for r in rs if r.get("err")), dict(Counter(r.get("kind") for r in rs))))


ETAPAS = {"ns": etapa_ns, "types": etapa_types, "members": etapa_members,
          "members_todos": lambda: etapa_members(todas=True),
          "examples": etapa_examples, "extras": etapa_extras}


def main(args):
    os.makedirs(RAW, exist_ok=True)
    if not args or args == ["status"]:
        status()
        return
    orden = ["ns", "types", "members_todos", "examples", "extras"] if args == ["all"] else args
    for e in orden:
        ETAPAS[e]()
    status()
    log("FIN")


if __name__ == "__main__":
    main(sys.argv[1:])

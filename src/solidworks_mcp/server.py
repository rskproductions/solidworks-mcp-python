#!/usr/bin/env python
"""
Servidor MCP propio para SOLIDWORKS. Transporte stdio, JSON-RPC 2.0 delimitado
por saltos de linea. Sin dependencias fuera de la stdlib + pywin32.

  python -m solidworks_mcp            arranca el servidor (lo hace Claude Desktop)
  python -m solidworks_mcp --probe    imprime tools/list y sale (diagnostico)

stdout esta reservado al protocolo: todo lo demas va a stderr y a server.log.
"""

import contextlib
import io
import json
import os
import re
import sys
import traceback
from datetime import datetime

from . import __version__, install_aliases
from .paths import LOG, OUT_DIR, TYPELIB_TXT
PROTOCOL_FALLBACK = "2025-06-18"
SERVER_INFO = {"name": "solidworks", "version": __version__}


def log(msg):
    line = "%s  %s\n" % (datetime.now().isoformat(timespec="seconds"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    sys.stderr.write(line)
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# definicion de herramientas
# ---------------------------------------------------------------------------

NUM = {"type": "number"}

PROFILE_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "description": (
        "Contornos del croquis, en mm. Cada entidad es uno de: "
        '{"type":"circle","x":0,"y":0,"d":10} | '
        '{"type":"rect","x0":0,"y0":0,"x1":10,"y1":5} | '
        '{"type":"polyline","points":[[0,0],[10,0],[10,5]],"close":true} | '
        '{"type":"hexagon","cx":0,"cy":0,"af":5,"rot":0}. '
        "Varias entidades en la misma lista = un solo croquis: un contorno exterior "
        "con otros dentro produce agujeros."
    ),
    "items": {"type": "object"},
}

TOOLS = [
    {
        "name": "sw_connect",
        "description": (
            "Adjunta a la instancia de SOLIDWORKS en marcha y devuelve version y "
            "documento activo. SOLIDWORKS debe estar ya abierto."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sw_doc_info",
        "description": "Titulo, ruta, tipo y numero de cuerpos del documento activo.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sw_new_part",
        "description": (
            "Crea una pieza nueva con la plantilla por defecto y la deja activa. "
            "Por defecto fija unidades MMGS (mm): la plantilla de fabrica viene en "
            "METROS y los DXF/DWG de chapa saldrian en metros."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"mm": {"type": "boolean", "default": True,
                                  "description": "false = dejar las unidades de la plantilla."}},
        },
    },
    {
        "name": "sw_open",
        "description": "Abre un documento existente (.SLDPRT/.SLDASM) y lo deja activo.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Ruta absoluta en Windows."}},
            "required": ["path"],
        },
    },
    {
        "name": "sw_extrude",
        "description": (
            "Croquis + extrusion en un solo paso sobre el documento activo. "
            "Todo en mm. El croquis va en el plano indicado y la extrusion crece en "
            "+Z desde z0 hasta z1 (z0 >= 0; si z1 < z0 se invierte). "
            "cut=true elimina material en vez de anadirlo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": PROFILE_SCHEMA,
                "z0": dict(NUM, description="Cota inicial en mm, >= 0."),
                "z1": dict(NUM, description="Cota final en mm."),
                "plane": {
                    "type": "integer",
                    "default": 0,
                    "description": "Indice del plano de referencia: 0 = primero del arbol (XY).",
                },
                "cut": {"type": "boolean", "default": False},
                "through": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Por todo: ignora z1 como profundidad y atraviesa todo el "
                        "material. z1 sigue marcando el sentido. Mas robusto que "
                        "la profundidad ciega para agujeros pasantes."
                    ),
                },
                "merge": {"type": "boolean", "default": True},
                "name": {"type": "string", "description": "Nombre para la operacion en el arbol."},
            },
            "required": ["profile", "z0", "z1"],
        },
    },
    {
        "name": "sw_list_bodies",
        "description": "Cuerpos solidos del documento activo con su caja envolvente en mm.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sw_bbox",
        "description": "Caja envolvente global en mm: min, max, tamano y centro.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sw_mass_properties",
        "description": (
            "Propiedades fisicas del documento activo: volumen (mm3), area (mm2), "
            "masa (g), densidad (kg/m3) y centro de gravedad (mm). Si se pasa "
            "material, lo asigna antes (nombre del .sldmat, p.ej. '6061-T6 (SS)')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "material": {"type": "string"},
                "database": {"type": "string", "default": "SOLIDWORKS Materials"},
            },
        },
    },
    {
        "name": "sw_check_machining",
        "description": (
            "Revision de fabricabilidad para fresado de 3 ejes desde +Z (perfil por "
            "defecto: Makera Carvera Air, 300x200x130 mm, pinzas 1/8\", 1/4\", 3/4/6 mm): "
            "cabe en el area, radio concavo minimo y fresas que caben, esbeltez "
            "L/D, agujeros justos para la fresa y caras que piden voltear la pieza. "
            "Con export_dir exporta ademas un STEP (mm) para Makera CAM. No genera "
            "trayectorias."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"export_dir": {"type": "string"}},
        },
    },
    {
        "name": "sw_interferences",
        "description": (
            "Interferencias del ENSAMBLAJE activo agrupadas por pareja de "
            "componentes (numero, volumen total y maximo en mm3). Puede tardar: "
            "~40 s con 22 componentes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"coincidence": {"type": "boolean", "default": False,
                                           "description": "Contar contactos coincidentes como interferencia."}},
        },
    },
    {
        "name": "sw_list_features",
        "description": "Arbol de operaciones del documento activo (nombre, tipo, suprimida).",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 200}},
        },
    },
    {
        "name": "sw_rebuild",
        "description": "Fuerza reconstruccion del documento activo.",
        "inputSchema": {
            "type": "object",
            "properties": {"full": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "sw_save_as",
        "description": "Guarda el documento activo en la ruta dada, opcionalmente tambien en STEP.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta absoluta con extension .SLDPRT/.SLDASM."},
                "step": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
    },
    {
        "name": "sw_export_step",
        "description": "Exporta el documento activo a STEP.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Ruta absoluta .STEP"}},
            "required": ["path"],
        },
    },
    {
        "name": "sw_execute_script",
        "description": (
            "Ejecuta Python arbitrario contra la sesion de SOLIDWORKS, igual que "
            "el 'script' del MCP de Fusion. En el scope del script: sw (la "
            "aplicacion), doc (ActiveDoc, puede ser None), c (el modulo solidworks_mcp.core, alias sw_core: "
            "extrude, bodies_info, overall_bbox, features_info, save_as...), "
            "fx (features: revolve, shell, hole_wizard, circular/linear_pattern, "
            "mirror_body, draft, rib, ref_plane_offset, loft, sweep, chapa, "
            "ecuaciones, configuraciones, mass_props), mz (mecanizado: dfm_3ejes, "
            "export_cam) y am (ensamblaje: add_mate, interferences). "
            "Escribe en la variable 'result' para devolver datos estructurados; "
            "lo impreso por print() se devuelve en 'stdout'. "
            "\n\n"
            "ANTES de escribir una llamada COM que no conozcas de memoria, "
            "consulta sw_api_doc(iface, member): devuelve el orden y tipo real "
            "de los parametros en esta instalacion mas su significado, el enum "
            "que los gobierna y las unidades (la API trabaja en METROS y "
            "radianes). Los metodos de features tienen 20+ parametros "
            "posicionales; adivinarlos falla en silencio. "
            "\n\n"
            "readOnly=true declara que el script no modifica el documento. Se "
            "comprueba en dos pasos: antes de ejecutar se bloquea si el texto "
            "contiene una llamada con pinta de mutar (Feature*, Insert*, Set*, "
            "Save*...) o una asignacion a propiedad; despues de ejecutar se "
            "compara el numero de cuerpos, de caras y el flag de modificado del "
            "documento, y si algo cambio pese a todo se devuelve en "
            "'readOnlyViolation' en vez de ocultarlo. "
            "ESTO NO ES UNA CAJA DE ARENA: es una red contra el despiste, no "
            "una barrera de seguridad; con COM cualquier script con acceso a "
            "doc puede llamar a lo que quiera. No hay limite de tiempo: un "
            "script que cuelga bloquea el servidor hasta que termine."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Codigo Python a ejecutar."},
                "readOnly": {
                    "type": "boolean",
                    "default": False,
                    "description": "Declara que el script no debe modificar el documento (ver arriba).",
                },
            },
            "required": ["script"],
        },
    },
    {
        "name": "sw_api_search",
        "description": (
            "Busca miembros de la API COM de SOLIDWORKS. Dos modos: "
            "'pattern' = regex sobre la typelib real de esta instalacion "
            "(api_index.txt, volcado por dump_api.py: nombre exacto, orden y tipo "
            "de cada parametro); 'text' = busqueda de texto libre sobre la ayuda "
            "oficial 2026 (sw_api_index.sqlite: resumen, parametros y remarks), "
            "para cuando no sabes como se llama lo que buscas ('bounding box', "
            "'save body as part', 'mass properties'). Con el nombre en la mano, "
            "sw_api_doc da la ficha completa."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex, sin distinguir mayusculas, sobre la typelib (nombre, parametros y tipos).",
                },
                "text": {
                    "type": "string",
                    "description": "Texto libre (FTS5) sobre la ayuda oficial. Alternativa a pattern.",
                },
                "interface": {
                    "type": "string",
                    "description": "Filtra por interfaz exacta, p.ej. IFeatureManager (sin distinguir mayusculas).",
                },
                "limit": {"type": "integer", "default": 40},
            },
        },
    },
    {
        "name": "sw_api_doc",
        "description": (
            "Ficha completa de un metodo o propiedad de la API: bloque de la "
            "typelib de ESTA instalacion (orden y tipo COM de cada parametro) + "
            "ayuda oficial 2026 SP04 (que significa cada parametro, enum que lo "
            "gobierna, unidades, retorno, remarks con las tablas de marcas de "
            "seleccion, desde que version existe). Consultar antes de escribir "
            "una llamada COM en sw_execute_script. Acepta 'FeatureManager' o "
            "'IFeatureManager'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "iface": {"type": "string", "description": "Interfaz, p.ej. IFeatureManager, IModelDoc2, ISketchManager."},
                "member": {"type": "string", "description": "Metodo o propiedad, p.ej. FeatureExtrusion3, SelectByID2."},
            },
            "required": ["iface", "member"],
        },
    },
    {
        "name": "sw_api_enum",
        "description": (
            "Miembros y valores de un enum swconst (swEndConditions_e, "
            "swSelectType_e, swDocumentTypes_e...). Acepta tambien el nombre de "
            "un miembro suelto (swEndCondBlind) y resuelve a que enum pertenece. "
            "En codigo usa el nombre via win32com.client.constants, no el "
            "numero. swSelectType_e trae la tabla enum -> string que espera "
            "SelectByID2 (swSelFACES -> \"FACE\")."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "sw_api_members",
        "description": (
            "Todos los miembros de una interfaz con su resumen de una linea: "
            "para descubrir que se puede hacer con IFeatureManager, IBody2, "
            "IFace2... Cubre todas las interfaces de los 10 namespaces de la "
            "ayuda 2026 (sldworks, swdimxpert, swmotionstudy, swpublished...). "
            "Incluye la ficha de la interfaz: resumen, como se obtiene "
            "(accessors), categoria funcional y ejemplos oficiales. Puede ser "
            "largo (IModelDoc2 tiene 762): filtra con kind si solo quieres "
            "propiedades."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "iface": {"type": "string"},
                "kind": {"type": "string", "enum": ["method", "property"]},
            },
            "required": ["iface"],
        },
    },
    {
        "name": "sw_api_example",
        "description": (
            "Ejemplos oficiales de codigo de la ayuda de la API 2026 (VBA; VB.NET "
            "si no hay VBA), indexados con las interfaces y miembros que los "
            "enlazan. Para descubrir el flujo completo de una operacion (que "
            "seleccionar, en que orden, que marcas) antes de escribirla en "
            "sw_execute_script. query = busqueda libre ('thread feature', "
            "'insert component transform'); iface/member = los que enlaza esa "
            "interfaz o miembro; id = el codigo completo de uno."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "iface": {"type": "string"},
                "member": {"type": "string"},
                "id": {"type": "integer"},
                "limit": {"type": "integer", "default": 8},
            },
        },
    },
    {
        "name": "sw_screenshot",
        "description": (
            "Captura la vista activa a PNG. Equivalente al queryType "
            "'screenshot' de Fusion: permite ver lo que se ha construido sin "
            "que el usuario este delante de la pantalla. Usa SaveBMP (unica "
            "exportacion de imagen confirmada en la typelib) y convierte a PNG "
            "con codigo propio, sin depender de Pillow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta .png de salida. Por defecto <datos>/out/screenshot.png (ver README: SW_MCP_DATA).",
                },
                "width": {"type": "integer", "default": 1600},
                "height": {"type": "integer", "default": 1200},
                "direction": {
                    "type": "string",
                    "default": "current",
                    "enum": ["current", "front", "back", "top", "bottom", "left",
                             "right", "isometric", "trimetric", "dimetric"],
                    "description": (
                        "Vista estandar antes de capturar. Los ViewId se "
                        "verificaron con un barrido empirico (diag_view.py) "
                        "comparando imagenes reales, no contra la typelib (esos "
                        "IDs viven en una biblioteca de constantes que dump_api.py "
                        "no llega a volcar). front e isometric se contrastaron "
                        "ademas contra una captura manual del usuario."
                    ),
                },
            },
        },
    },
    {
        "name": "sw_close",
        "description": "Cierra el documento activo. No guarda salvo que save=true.",
        "inputSchema": {
            "type": "object",
            "properties": {"save": {"type": "boolean", "default": False}},
        },
    },
]


# ---------------------------------------------------------------------------
# implementacion
# ---------------------------------------------------------------------------

def _core():
    from . import core
    return core


def t_sw_connect(a):
    c = _core()
    app = c.sw_app()
    doc = c.active_doc(required=False)
    return {
        "connected": True,
        "revision": app.RevisionNumber,
        "active_doc": (doc.GetTitle if doc is not None else None),
    }


def t_sw_doc_info(a):
    c = _core()
    doc = c.active_doc()
    tipo = c.doc_type(doc)
    out = {"title": doc.GetTitle, "path": doc.GetPathName, "type": tipo}
    if tipo == 1:
        out["bodies"] = len(doc.GetBodies2(0, True) or [])
    elif tipo == 2:
        out["components"] = len(doc.GetComponents(False) or [])   # todos los niveles
    return out


def t_sw_new_part(a):
    c = _core()
    app = c.sw_app()
    tpl = app.GetUserPreferenceStringValue(8)
    doc = app.NewDocument(tpl, 0, 0.0, 0.0)
    if doc is None:
        raise RuntimeError("NewDocument devolvio None. Plantilla usada: %r" % tpl)
    out = {"created": doc.GetTitle, "template": tpl}
    if a.get("mm", True):
        from . import features
        features.units_mm(app.ActiveDoc)
        out["unidades"] = "MMGS"
    return out


def t_sw_open(a):
    c = _core()
    app = c.sw_app()
    path = a["path"]
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    doc = c.open_doc(path)     # [in,out] como VARIANT BYREF: con ints sueltos daba "los tipos no coinciden"
    return {"opened": doc.GetTitle, "path": path}


def t_sw_extrude(a):
    c = _core()
    doc = c.active_doc()
    return c.extrude(
        doc,
        a["profile"],
        a["z0"],
        a["z1"],
        plane=int(a.get("plane", 0)),
        cut=bool(a.get("cut", False)),
        merge=bool(a.get("merge", True)),
        name=a.get("name"),
        through=bool(a.get("through", False)),
    )


def t_sw_list_bodies(a):
    c = _core()
    return {"bodies": c.bodies_info(c.active_doc())}


def t_sw_bbox(a):
    c = _core()
    bb = c.overall_bbox(c.active_doc())
    if bb is None:
        return {"bbox_mm": None, "note": "El documento no tiene cuerpos solidos."}
    return {"bbox_mm": bb}


def t_sw_list_features(a):
    c = _core()
    return {"features": c.features_info(c.active_doc(), int(a.get("limit", 200)))}


def t_sw_rebuild(a):
    c = _core()
    doc = c.active_doc()
    if a.get("full"):
        ok = c.soft(doc, "ForceRebuild3", False)
    else:
        ok = c.soft(doc, "EditRebuild3")
    return {"rebuilt": bool(ok)}


def t_sw_save_as(a):
    c = _core()
    return c.save_as(c.active_doc(), a["path"], bool(a.get("step", False)))


def t_sw_export_step(a):
    c = _core()
    doc = c.active_doc()
    path = os.path.abspath(a["path"])
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc.SaveAs3(path, 0, 0)
    return {"exported": path}


def t_sw_execute_script(a):
    c = _core()
    script = a["script"]
    read_only = bool(a.get("readOnly", False))

    if read_only:
        hits = c.scan_mutating(script)
        if hits:
            raise RuntimeError(
                "readOnly=true pero el script contiene algo con pinta de "
                "mutar el documento: %s. Si de verdad solo lee, es un falso "
                "positivo del filtro (revisa MUTATING_PREFIXES en core.py); "
                "si modifica el documento, quita readOnly."
                % ", ".join(hits)
            )

    app = c.sw_app()
    before = c.doc_state(c.active_doc(required=False))

    from . import asm as am, features as fx, mecanizado as mz
    ns = {"sw": app, "doc": c.active_doc(required=False), "c": c, "fx": fx, "mz": mz, "am": am}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(script, "<sw_execute_script>", "exec"), ns)
    except Exception as exc:
        raise RuntimeError(
            "%s: %s\n--- stdout hasta el fallo ---\n%s"
            % (type(exc).__name__, exc, buf.getvalue())
        )

    after = c.doc_state(c.active_doc(required=False))
    out = {"stdout": buf.getvalue()}
    if "result" in ns:
        out["result"] = ns["result"]
    if read_only:
        diff = c.state_diff(before, after)
        if diff:
            out["readOnlyViolation"] = diff
    return out


def _apidoc():
    from . import api_doc
    return api_doc


def t_sw_api_doc(a):
    return {"doc": _apidoc().doc(a["iface"], a["member"])}


def t_sw_api_enum(a):
    return _apidoc().enum(a["name"])


def t_sw_api_members(a):
    return _apidoc().members(a["iface"], a.get("kind"))


def t_sw_api_example(a):
    return _apidoc().example(query=a.get("query"), id=a.get("id"), iface=a.get("iface"),
                             member=a.get("member"), limit=int(a.get("limit", 8)))


def t_sw_api_search(a):
    if a.get("text") and not a.get("pattern"):
        hits = _apidoc().search_text(a["text"], int(a.get("limit", 40)))
        out = {"count": len(hits), "source": "ayuda 2026", "matches": hits}
        ej = _apidoc().search_examples(a["text"], 5)
        if ej:
            out["ejemplos"] = ej       # sw_api_example(id=...) da el codigo
        return out
    if not a.get("pattern"):
        raise ValueError("Indica 'pattern' (regex sobre la typelib) o 'text' (busqueda libre en la ayuda).")
    path = TYPELIB_TXT
    if not os.path.exists(path):
        raise FileNotFoundError(
            "No existe %s. Ejecuta antes, con SOLIDWORKS abierto: "
            "python tools/dump_api.py" % TYPELIB_TXT
        )
    try:
        rx = re.compile(a["pattern"], re.IGNORECASE)
    except re.error as exc:
        raise ValueError("pattern no es un regex valido: %s" % exc)
    iface = a.get("interface")
    limit = int(a.get("limit", 40))

    with open(path, "r", encoding="utf-8") as fh:
        blocks = fh.read().split("\n\n")

    hits = []
    truncated = False
    for b in blocks:
        b = b.strip("\n")
        if not b:
            continue
        if iface:
            head = b.splitlines()[0]
            if not head.split(".", 1)[0].strip().lower() == iface.lower():
                continue
        if rx.search(b):
            if len(hits) >= limit:
                truncated = True
                break
            hits.append(b)
    return {"count": len(hits), "truncated": truncated, "matches": hits}


def t_sw_screenshot(a):
    c = _core()
    doc = c.active_doc()
    path = a.get("path") or os.path.join(OUT_DIR, "screenshot.png")
    return c.screenshot(
        doc, path,
        width=int(a.get("width", 1600)),
        height=int(a.get("height", 1200)),
        direction=a.get("direction", "current"),
    )


def t_sw_close(a):
    c = _core()
    app = c.sw_app()
    doc = c.active_doc()
    title = doc.GetTitle
    if a.get("save"):
        path = doc.GetPathName
        if not path:
            raise RuntimeError(
                "El documento nunca se ha guardado: usa sw_save_as con una ruta."
            )
        doc.SaveAs3(path, 0, 0)
    app.CloseDoc(title)
    return {"closed": title}


def t_sw_mass_properties(a):
    c = _core()
    from . import features
    doc = c.active_doc()
    if a.get("material"):
        return features.set_material(doc, a["material"], a.get("database", "SOLIDWORKS Materials"))
    return features.mass_props(doc)


def t_sw_check_machining(a):
    c = _core()
    from . import mecanizado
    doc = c.active_doc()
    out = mecanizado.dfm_3ejes(doc)
    if a.get("export_dir"):
        out["export"] = mecanizado.export_cam(doc, a["export_dir"])
    return out


def t_sw_interferences(a):
    c = _core()
    from . import asm
    doc = c.active_doc()
    if c.doc_type(doc) != 2:
        raise ValueError("El documento activo no es un ensamblaje.")
    return asm.interferences(doc, coincidencia=bool(a.get("coincidence", False)))


HANDLERS = {t.__name__[2:]: t for t in [
    t_sw_connect, t_sw_doc_info, t_sw_new_part, t_sw_open, t_sw_extrude,
    t_sw_list_bodies, t_sw_bbox, t_sw_list_features, t_sw_rebuild,
    t_sw_save_as, t_sw_export_step, t_sw_close,
    t_sw_execute_script, t_sw_api_search, t_sw_api_doc, t_sw_api_enum,
    t_sw_api_members, t_sw_api_example, t_sw_screenshot,
    t_sw_mass_properties, t_sw_check_machining, t_sw_interferences,
]}


# ---------------------------------------------------------------------------
# protocolo
# ---------------------------------------------------------------------------

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(rid, payload):
    send({"jsonrpc": "2.0", "id": rid, "result": payload})


def error(rid, code, message):
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def call_tool(name, args):
    fn = HANDLERS.get(name)
    if fn is None:
        return {
            "content": [{"type": "text", "text": "Herramienta desconocida: %s" % name}],
            "isError": True,
        }
    try:
        out = fn(args or {})
        return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}]}
    except Exception as exc:
        tb = traceback.format_exc()
        log("ERROR en %s: %s" % (name, tb))
        return {
            "content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}],
            "isError": True,
        }


def handle(msg):
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        ver = params.get("protocolVersion") or PROTOCOL_FALLBACK
        result(rid, {
            "protocolVersion": ver,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    elif method in ("notifications/initialized", "notifications/cancelled"):
        pass
    elif method == "ping":
        result(rid, {})
    elif method == "tools/list":
        result(rid, {"tools": TOOLS})
    elif method == "tools/call":
        result(rid, call_tool(params.get("name"), params.get("arguments")))
    elif rid is not None:
        error(rid, -32601, "Metodo no soportado: %s" % method)


def main():
    if "--probe" in sys.argv:
        print(json.dumps({"tools": [t["name"] for t in TOOLS]}, indent=2))
        return

    for fallo in install_aliases():
        log("Aviso: alias no disponible: %s" % fallo)
    try:
        from . import core as sw_core
        sw_core.co_init()
    except Exception as exc:
        log("Aviso: no se pudo inicializar COM al arrancar (%s). "
            "El servidor sigue: se reintentara en la primera llamada." % exc)

    log("servidor arrancado, %d herramientas" % len(TOOLS))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            log("linea no-JSON ignorada: %.200s" % line)
            continue
        try:
            handle(msg)
        except Exception:
            log("fallo procesando mensaje: %s" % traceback.format_exc())
            if msg.get("id") is not None:
                error(msg["id"], -32603, "Error interno del servidor")
    log("stdin cerrado, saliendo")


if __name__ == "__main__":
    main()

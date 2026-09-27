"""
Donde viven los datos generados en local. Nada de esto se versiona: el indice
de la ayuda, el volcado de la typelib y las constantes se construyen en cada
maquina (tools/ y README).

Orden: variable de entorno SW_MCP_DATA > carpeta data/ del repositorio (si se
ejecuta desde un clon) > %LOCALAPPDATA%\\solidworks-mcp (instalado con pip).
"""

import os

_PKG = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_PKG))


def data_dir():
    d = os.environ.get("SW_MCP_DATA")
    if not d:
        if os.path.exists(os.path.join(_REPO, "pyproject.toml")):
            d = os.path.join(_REPO, "data")
        else:
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            d = os.path.join(base, "solidworks-mcp")
    os.makedirs(d, exist_ok=True)
    return d


DATA = data_dir()
INDEX_DB = os.path.join(DATA, "sw_api_index.sqlite")     # tools/index_build
TYPELIB_TXT = os.path.join(DATA, "api_index.txt")        # tools/dump_api.py
SWCONST_JSON = os.path.join(DATA, "swconst.json")        # python -m solidworks_mcp.const
SWCONST_CHOQUES = os.path.join(DATA, "swconst_colisiones.json")
OUT_DIR = os.path.join(DATA, "out")                       # capturas, pruebas
LOG = os.path.join(DATA, "server.log")

"""Servidor MCP para SOLIDWORKS (COM via pywin32)."""

__version__ = "0.1.0"

_ALIASES = {
    "sw_core": "core", "sw_asm": "asm", "sw_const": "const",
    "sw_api_doc": "api_doc", "sw_api_lookup": "api_lookup",
}


def install_aliases():
    """Permite `import sw_core as c` (nombres historicos) en los scripts de
    sw_execute_script y en scripts propios: apuntan a los modulos del paquete."""
    import importlib
    import sys
    fallos = []
    for viejo, nuevo in _ALIASES.items():
        if viejo in sys.modules:
            continue
        try:
            sys.modules[viejo] = importlib.import_module("." + nuevo, __name__)
        except ImportError as exc:        # p.ej. sin pywin32: el servidor sigue
            fallos.append("%s (%s)" % (viejo, exc))
    return fallos

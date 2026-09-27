#!/usr/bin/env python
"""
Diagnostico de ShowNamedView2: barre nombre x ViewId y captura una imagen por
combinacion, para ver empiricamente cual produce de verdad la vista isometrica.
No hay IDs verificados contra la typelib (swStandardViews_e vive en una
biblioteca de constantes que dump_api.py no ha volcado), asi que se prueba.

    py -3.14 diag_view.py

Reutiliza el documento activo (deja SOLIDWORKS abierto con la pieza de
selftest.py --build ya construida; si no hay documento activo, abre
out\\MCP_SELFTEST.SLDPRT). Escribe out\\view_<nombre>_<id>.png por cada
combinacion.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import solidworks_mcp  # noqa: E402
solidworks_mcp.install_aliases()

import sw_core as c

from solidworks_mcp.paths import OUT_DIR as OUT  # noqa: E402
PART = os.path.join(OUT, "MCP_SELFTEST.SLDPRT")

NAMES = ["*Isometric", "Isometric"]
IDS = list(range(-1, 11))


def main():
    c.co_init()
    app = c.sw_app()
    doc = c.active_doc(required=False)
    if doc is None:
        if not os.path.exists(PART):
            raise SystemExit(
                "No hay documento activo y no existe %s. "
                "Corre antes selftest.py --build." % PART
            )
        doc = app.OpenDoc6(PART, 1, 0, "", 0, 0)
        print("abierta", PART)
    else:
        print("usando documento activo:", doc.GetTitle)

    os.makedirs(OUT, exist_ok=True)
    written = []
    for name in NAMES:
        for vid in IDS:
            tag = "%s_%d" % (name.replace("*", "star"), vid)
            path = os.path.join(OUT, "view_%s.png" % tag)
            try:
                doc.ShowNamedView2(name, vid)
                doc.ViewZoomtofit2()
                doc.GraphicsRedraw2()
                bmp = path[:-4] + ".bmp"
                ok = doc.SaveBMP(bmp, 500, 375)
                if not ok:
                    print("%-24s SaveBMP devolvio False" % tag)
                    continue
                c.bmp_to_png(bmp, path)
                os.remove(bmp)
                written.append(path)
                print("%-24s ok -> %s" % (tag, os.path.basename(path)))
            except Exception as exc:
                print("%-24s FALLO: %s: %s" % (tag, type(exc).__name__, exc))

    print("\n%d capturas en %s" % (len(written), OUT))
    print("No se cierra ni se guarda el documento.")


if __name__ == "__main__":
    main()

# solidworks-mcp-python

An [MCP](https://modelcontextprotocol.io) server that lets Claude (or any MCP
client) drive **SOLIDWORKS** through its COM API, written in plain Python
(stdlib + `pywin32`). *[Leer en español](README.es.md).*

Its design follows the native Fusion 360 MCP: the main tool is
**`sw_execute_script`**, which runs arbitrary Python against the live COM
session. The typed tools are shortcuts on top. What makes that workable is the
second half of the server: **a local semantic index of the SOLIDWORKS API**
(signatures from your installation's type library + meaning, enums, units and
official examples from the 2026 API help), so the model looks a call up before
writing it instead of guessing 20 positional parameters.

> Status: personal project, used daily to model parts, build assemblies with
> mates and produce dimensioned drawings. Windows only (COM). Tested with
> SOLIDWORKS 2026 (3DEXPERIENCE R2026x, Spanish UI) and Python 3.14 only.
> Reports from other versions and languages are welcome (open an issue).

## Tools (22)

| Group | Tools |
|---|---|
| Session / document | `sw_connect` `sw_doc_info` `sw_new_part` `sw_open` `sw_close` `sw_rebuild` |
| Modelling shortcut | `sw_extrude` (sketch + boss/cut in one step, mm) |
| Inspection | `sw_list_bodies` `sw_bbox` `sw_list_features` `sw_screenshot` |
| Save / export | `sw_save_as` `sw_export_step` |
| Engineering / manufacturing | `sw_mass_properties` (optionally sets the material), `sw_check_machining` (3-axis DFM, default profile Makera Carvera Air, STEP for the CAM), `sw_interferences` (grouped by component pair) |
| Script | `sw_execute_script` — scope: `sw`, `doc`, `c` (core), `fx` (features), `mz` (machining), `am` (assembly); `readOnly` guard |
| **API knowledge** | `sw_api_doc` `sw_api_enum` `sw_api_members` `sw_api_search` `sw_api_example` |

## Install

Requirements: Windows, SOLIDWORKS running, Python ≥ 3.10.

```bat
git clone https://github.com/rskproductions/solidworks-mcp-python.git
cd solidworks-mcp-python
py -m pip install -e .
```

Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "solidworks": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["-m", "solidworks_mcp"]
    }
  }
}
```

Without `pip install`, point `PYTHONPATH` at the `src` folder instead:
`"env": {"PYTHONPATH": "C:\\path\\to\\solidworks-mcp-python\\src"}`.

Check it: `py -m solidworks_mcp --probe` lists the tools.

## Build the API index (once, locally)

The index is **not distributed**: the API help text and examples are
Dassault Systèmes' copyright. The scripts below build it on your machine from
your own installation and the public help site. Everything lands in `data/`
(or `%LOCALAPPDATA%\solidworks-mcp`, or `SW_MCP_DATA`).

```bat
:: 1. type library of YOUR installation -> data\api_index.txt (SOLIDWORKS open)
py tools\dump_api.py
:: 2. API help -> data\sw_api_index.sqlite (~15,000 pages, resumable)
py tools\index_build\crawl_full.py all
py tools\index_build\parse_raw.py
py tools\index_build\build_index_full.py
:: 3. swconst values (the help omits many) -> data\swconst.json
py -m solidworks_mcp.const --typelib
```

The server runs without the index; only the `sw_api_*` tools need it.

## Layout

```
src/solidworks_mcp/
  server.py      MCP protocol (stdio JSON-RPC), tool definitions and handlers
  core.py        COM helpers: connection, sketch, extrude, thread, bodies, bbox,
                 edges/faces by geometry, chamfer/fillet, screenshot, save
  features.py    revolve, shell, Hole Wizard, circular/linear pattern, mirror,
                 draft, rib, reference plane, loft, sweep, sheet metal + flat DXF,
                 material, mass properties, equations, configurations
  mecanizado.py  3-axis DFM check (Carvera Air profile) and STEP export for the CAM
  asm.py         assemblies: components by position, faces by geometry, AddMate5,
                 interference detection
  const.py       swconst values from the help index + type library
  api_doc.py     merges type library + help for the sw_api_* tools
  api_lookup.py  sqlite client (stdlib)
  paths.py       where local data lives
tools/           dump_api.py and index_build/ (crawler -> parser -> sqlite)
scripts/         selftest, probetas.py (23-step regression: every feature checked
                 by VOLUME against its theoretical value), benchmarks, diagnostics
docs/            notes on other SOLIDWORKS MCP servers and APIs
```

## Hard-won COM notes

Late-bound pywin32 against SOLIDWORKS has traps that neither the type library
nor the help mention. The short version (details in [README.es.md](README.es.md)):

- The COM API works in **metres and radians**.
- A zero-argument member may arrive already evaluated: use `c.soft(obj, "Name")`
  (`callable()` does not tell you — `CDispatch` is always callable).
- `[in,out]` ints → `c.byref_int()`; null interface → `c.null_dispatch()`.
- "Member not found" on methods that exist (`CreateTransform`, `FixComponent`)
  → `c.call_method(obj, "Name", *args)` forces `DISPATCH_METHOD`.
- Parameterised property puts (`ITableAnnotation.Text(r, c) = ...`)
  → `c.put_property(obj, "Text", r, c, value)`.
- Array properties (`IView.Position`) need `VARIANT(VT_ARRAY|VT_R8, [...])`;
  a tuple silently misplaces the view.
- Every COM round trip costs ~30 ms: performance is the number of calls.
  Scope face/edge searches to one feature (`comp.FeatureByName(...).GetFaces`).
- `win32com.client.constants` is empty without makepy: use `const("swFmFillet")`.
- The default part template is in **metres**: flat-pattern DXFs come out 1000x too
  small. `sw_new_part` now sets MMGS; `fx.units_mm(doc)` for existing parts.
- `ISurface.EvaluateAtPoint` returns the normal at [0:3], and
  `IFace2.FaceInSurfaceSense == True` means face and surface normals are *opposite*.
- Hole Wizard: `HoleWizard5` returned None in every combination tried;
  `CreateDefinition(swFmHoleWzd)` + `InitializeHole` + `SelectByRay` + `CreateFeature` works.
- Mirror features: select the features (mark 1) *before* the plane (mark 2). Ribs
  can't be mirrored as features: mirror the body (mark 256).
- A tool call has a 60 s budget in Claude Desktop: run long jobs as a
  subprocess that writes a log. Never two COM clients on the same session at once.

## Related projects

- [eyfel/mcp-server-solidworks](https://github.com/eyfel/mcp-server-solidworks) (SolidPilot)
- [jaylamping/solidworks-mcp](https://github.com/jaylamping/solidworks-mcp) (TypeScript + .NET, 133 tools)

What we learned from them is in `docs/`.

## License

MIT — see [LICENSE](LICENSE). SOLIDWORKS is a trademark of Dassault Systèmes;
this project is not affiliated with or endorsed by Dassault Systèmes.

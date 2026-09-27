# Índice semántico de la API de SOLIDWORKS 2026

Extraído de `help.solidworks.com` (API Help 2026 **SP04**) el 22-09-2026. Es la capa de conocimiento que tu `execute_python` necesita en runtime: firma, semántica de cada parámetro, enum que lo gobierna, unidades, remarks (incluidas las tablas de marcas de selección) y disponibilidad por versión.

## Contenido

Regenerado el 24-09-2026 con `crawl_full.py` (ayuda completa, no solo las interfaces nucleares):

| | antes (22-09) | ahora |
|---|---|---|
| Interfaces | 89 | **587** (10 namespaces: sldworks, swconst, swcommands, swdimxpert, swmotionstudy, swpublished, sw3dprinter, dsgnchk, swhtmlcontrol, swscanto3d) |
| Miembros documentados | 5.875 | **13.181** |
| Enums | 400 | **999** |
| Ejemplos de código | 0 (solo títulos) | **1.295** (VBA; VB.NET si no hay VBA), con las interfaces/miembros que los enlazan |
| Categorías funcionales | — | 13 (Assembly, Feature, Sketch, Drawing...) |
| Tamaño | 14,6 MB | 36,6 MB |

Tablas nuevas en el sqlite: `ifaces` (ficha: namespace, resumen, remarks, accesores, categoría, ejemplos), `examples` + `examples_fts`, `categories`, `meta`. El esquema antiguo se conserva tal cual (`sw_api_lookup.py` sigue funcionando).

Comparado con la API real de esta instalación (`api_index.txt`, typelib): los únicos miembros que la ayuda no documenta son unos 114 ocultos o de uso interno (`GetCookie`, `Dummy*`, `UnprojectModelPoint`...); están en `api_index.txt` y se encuentran con `sw_api_search(pattern=...)`.

## Ficheros

- `sw_api_index.sqlite` — el índice consultable. Tablas: `members`, `iface_members`, `iface_remarks`, `enums`, `enum_members`, y `fts` (FTS5 sobre nombre, resumen, parámetros y remarks).
- `sw_api_index.json` — lo mismo en JSON por si prefieres cargarlo en memoria.
- `sw_api_lookup.py` — cliente sin dependencias. `ApiIndex(db).lookup / enum / members / search / find_member / select_type_string / signature_help`.

## Uso como herramientas MCP (sugerido)

```python
api = ApiIndex("sw_api_index.sqlite")

@mcp.tool()
def sw_api(iface: str, member: str) -> str:
    """Firma y semántica de un método/propiedad de la API de SOLIDWORKS. Consultar SIEMPRE antes de escribir una llamada COM."""
    return api.signature_help(iface, member)

@mcp.tool()
def sw_enum(name: str) -> dict:
    """Valores de un enum swconst (p.ej. swEndConditions_e)."""
    return api.enum(name)

@mcp.tool()
def sw_members(iface: str) -> list:
    """Qué se puede hacer con una interfaz: todos sus miembros con resumen de una línea."""
    return api.members(iface)

@mcp.tool()
def sw_search(text: str) -> list:
    """Búsqueda de texto libre sobre toda la API ('bounding box', 'save body', 'STEP')."""
    return api.search(text)
```

Cuatro tools, ~600 tokens de esquema. Frente a las 132 tools / ~36.000 tokens de SolidworksMCP-python.

## Cosas a saber

- **Unidades: metros y radianes.** Prácticamente toda la API COM trabaja en metros. La descripción de cada parámetro lo dice cuando aplica; `signature_help` lo incluye.
- **Valores de enum `None`.** ~2.000 miembros (casi todos de `swUserPreference*_e`) no llevan valor numérico en la página de la ayuda — la propia página remite a "System Options and Document Properties". Usa siempre el nombre vía `win32com.client.constants.swXxx` (viene de la typelib) y no el número; el índice te da la semántica, la typelib el valor.
- **`swSelectType_e` → string de `SelectByID2`.** La tabla que traduce `swSelFACES` → `"FACE"` está en `enum("swSelectType_e")["tables"][0]` y en `select_type_string("swSelFACES")`.
- **Marcas de selección.** Los métodos que requieren preselección (extrusión, barrido, loft, mates…) llevan en `remarks`/`tables` la tabla "select … with mark …". Es lo que más se falla a ciegas.
- **Métodos con sufijo numérico** (`FeatureCut4`, `SaveAs3`, `OpenDoc6`): los remarks del nuevo dicen en qué se diferencia del obsoleto. `avail` te dice desde qué versión existe.
- **6 URLs fallaron** (typos en la propia ayuda: `swSelelectType_e`, `swLengthUnits_e`…). Irrelevantes.

## Regenerar

```
cd index_build
python crawl_full.py all          # ~15 min, ~1,5 GB descargados (gzip); reanudable
python parse_raw.py               # raw/*.jsonl.gz -> parsed.json (solo stdlib)
python build_index_full.py        # parsed.json -> ..\sw_api_index.sqlite
```

`crawl_full.py` descarga el fragmento `helpText` de `__NEXT_DATA__` de cada página (namespaces → interfaces y `_members` → miembros → ejemplos → categorías) y lo guarda en crudo: reparsear no obliga a descargar otra vez. Lecciones: `requests` falla con CERTIFICATE_VERIFY_FAILED (usa certifi), `urllib` usa el almacén de Windows y va bien; el servidor responde 403 a rutas con `..` (normalizar); la ayuda mezcla `SolidWorks.Interop` y `SOLIDWORKS.Interop` en las URLs. Para SW 2027 basta cambiar `/2026/` por `/2027/`. Alternativa sin red: los `.chm` de `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\`.

El índice anterior (89 interfaces) se generó con `extractor.js` en el navegador + `build_index.py`; `build_index_full.py` lo usa (`sw_api_index.json`) como respaldo si alguna interfaz se quedara sin miembros.

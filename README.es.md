# solidworks-mcp-python

Servidor MCP por stdio (JSON-RPC 2.0, una línea por mensaje) para controlar
SOLIDWORKS desde Claude, sin dependencias fuera de la stdlib + pywin32. Sigue el
patrón del MCP nativo de Fusion 360: **la vía principal es `sw_execute_script`**
(Python arbitrario contra la sesión COM, con `readOnly`), y las herramientas
tipadas son atajos encima. Un solo hilo → COM STA sin trucos.
*[Read in English](README.md).*

> Probado solo con SOLIDWORKS 2026 (3DEXPERIENCE R2026x, en español) y
> Python 3.14. Se agradecen informes con otras versiones o idiomas (issues).

## Instalación y arranque

```bat
git clone https://github.com/rskproductions/solidworks-mcp-python.git
cd solidworks-mcp-python
py -m pip install -e .
py -m solidworks_mcp --probe        :: lista las tools y sale
py scripts\selftest.py              :: solo lectura contra la sesión real
py scripts\selftest.py --build      :: construye una pieza de prueba en data\out
```

Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "solidworks": {
      "command": "C:\\ruta\\a\\python.exe",
      "args": ["-m", "solidworks_mcp"]
    }
  }
}
```

Sin `pip install`, añade `"env": {"PYTHONPATH": "C:\\ruta\\a\\solidworks-mcp-python\\src"}`.
SOLIDWORKS tiene que estar abierto. `stdout` es del protocolo; los logs van a
`stderr` y a `data/server.log`.

## Herramientas (22)

| Grupo | Tools |
|---|---|
| Sesión / documento | `sw_connect` `sw_doc_info` `sw_new_part` `sw_open` `sw_close` `sw_rebuild` |
| Modelado (atajos) | `sw_extrude` (croquis + extrusión/corte en un paso, mm) |
| Inspección | `sw_list_bodies` `sw_bbox` `sw_list_features` `sw_screenshot` |
| Guardar / exportar | `sw_save_as` `sw_export_step` |
| Ingeniería / fabricación | `sw_mass_properties` (y material), `sw_check_machining` (DFM de fresado 3 ejes, perfil Makera Carvera Air, STEP para el CAM), `sw_interferences` (agrupadas por pareja) |
| Script | `sw_execute_script` — scope: `sw`, `doc`, `c` (core), `fx` (features), `mz` (mecanizado), `am` (ensamblaje); `readOnly` con filtro previo + diff de estado posterior |
| **Conocimiento de la API** | `sw_api_doc` `sw_api_enum` `sw_api_members` `sw_api_search` `sw_api_example` |

Los scripts pueden seguir usando los nombres históricos (`import sw_core as c`,
`import sw_asm`): el servidor los registra como alias de los módulos del paquete.

## Conocimiento de la API: dos fuentes, una respuesta

| Fuente | Fichero (en `data/`) | Qué aporta |
|---|---|---|
| Typelib de **tu** instalación | `api_index.txt` (`tools/dump_api.py`) | Nombre exacto, orden y tipo COM de cada parámetro. |
| Ayuda oficial de la API 2026 | `sw_api_index.sqlite` (`tools/index_build/`) | Qué significa cada parámetro, enum que lo gobierna, unidades, remarks, ejemplos oficiales. |

`sw_api_doc(iface, member)` devuelve las dos juntas. `sw_api_search` busca por
regex en la typelib (`pattern`) o por texto libre en la ayuda (`text`).
`sw_api_enum` resuelve enums y miembros sueltos; `sw_api_members` lista una
interfaz entera; `sw_api_example` da el código de los ejemplos oficiales.

**El índice no se distribuye**: el texto y los ejemplos de la ayuda tienen
copyright de Dassault Systèmes. Se genera en local (una vez):

```bat
py tools\dump_api.py                          :: typelib -> data\api_index.txt (SOLIDWORKS abierto)
py tools\index_build\crawl_full.py all        :: ayuda -> data\index_build\raw (~15.000 páginas, reanudable)
py tools\index_build\parse_raw.py
py tools\index_build\build_index_full.py      :: -> data\sw_api_index.sqlite
py -m solidworks_mcp.const --typelib          :: constantes -> data\swconst.json
```

Cobertura con la ayuda 2026 SP04: 587 interfaces de 10 namespaces, 13.181
miembros, 999 enums, 1.295 ejemplos. Detalles en `tools/index_build/API_INDEX.md`.
Los datos van a `data/` en un clon, a `%LOCALAPPDATA%\solidworks-mcp` si está
instalado con pip, o a donde diga la variable `SW_MCP_DATA`.

## Estructura

```
src/solidworks_mcp/
  server.py      protocolo + definición de tools + handlers
  core.py        COM: conexión, croquis, extrusión, rosca, cuerpos, bbox, captura,
                 aristas/caras por geometría, chaflán/redondeo, guardar, readOnly
  features.py    revolución, vaciado, asistente de taladro, matrices, simetría,
                 ángulo de salida, nervio, plano, recubrimiento, barrido, chapa +
                 DXF, material, propiedades físicas, ecuaciones, configuraciones
  mecanizado.py  revisión DFM de fresado 3 ejes (Carvera Air) y STEP para el CAM
  asm.py         ensamblaje: componentes por posición, caras por geometría,
                 AddMate5, interferencias
  const.py       constantes swconst (índice + typelib; w.constants está vacío)
  api_doc.py     une typelib + ayuda para las tools sw_api_*
  api_lookup.py  cliente del sqlite (stdlib)
  paths.py       dónde viven los datos locales
tools/           dump_api.py e index_build/ (crawler -> parser -> sqlite)
scripts/         selftest, probetas.py (23 pasos, cada operación comprobada por
                 VOLUMEN contra su valor teórico), bench*, diag_*
docs/            notas de otros servidores MCP de SOLIDWORKS y de la documentación de 3DS
```

## Convenciones que cuestan caro si se olvidan

- **La API COM trabaja en metros y radianes.** Las tools tipadas hablan en mm (`core.mm`), los scripts no.
- **`win32com.client.constants` está vacío en esta instalación.** Solo se puebla cuando pywin32 genera el wrapper estático con `gencache.EnsureDispatch`, y eso falla aquí (lo explica el docstring de `tools/dump_api.py`). `w.constants.swFmFillet` lanza `AttributeError`, no devuelve el número. Usa valores numéricos con el nombre en un comentario, o `const.const("swFmFillet")` (`from solidworks_mcp.const import const`), que los lee del índice y de la biblioteca de tipos y los cachea en `data/swconst.json`.
- Los métodos de features (`FeatureExtrusion3`, `FeatureCut4`…) tienen 20+ parámetros posicionales: consultar `sw_api_doc` antes de escribirlos.
- Métodos con sufijo (`OpenDoc6`, `SaveAs3`): el remark del nuevo dice qué cambia frente al obsoleto.
- **Enlace tardío: un miembro de cero argumentos puede llegar como método o ya evaluado.** Ver abajo.
- **Parámetros COM especiales en enlace tardío** (comprobado): `[in,out]` enteros (`Errors`/`Warnings` de `OpenDoc6`, `ActivateDoc3`...) como `c.byref_int()`; puntero a interfaz nulo (`Callout` de `SelectByID2`) como `c.null_dispatch()`. Con `None` o un `int` suelto: "los tipos no coinciden".
- **Métodos que el enlace tardío no sabe invocar**: `IMathUtility.CreateTransform` o `IAssemblyDoc.FixComponent` dan "No se ha encontrado el miembro" aunque existan (pywin32 invoca como método|propiedad y SOLIDWORKS lo rechaza). `c.call_method(obj, "Nombre", *args)` fuerza `DISPATCH_METHOD`; `c.math_transform(rot9, tras_m)` construye la transformada de un componente.
- **Propiedades con parámetros** (`ITableAnnotation.Text(fila, col) = ...`): pywin32 tardío no sabe asignarlas (`AttributeError: SetText`). `c.put_property(tabla, "Text", fila, col, "valor")` invoca `DISPATCH_PROPERTYPUT`.
- **Arrays como argumento de propiedad** (`IView.Position`, `IView.ScaleRatio`): pasa `VARIANT(VT_ARRAY|VT_R8, [...])`. Con una tupla la vista acaba desplazada sin error.
- Para seleccionar una entidad de la que ya tienes el objeto, usa su propio `Select4`, no `SelectByID2`. La selección por coordenadas depende del estado de la vista y se rompe al girar el modelo; lo dicen los propios remarks de `SelectByID2`.

### Ensamblajes y planos (comprobado en un proyecto real, 24-09-2026)

- **Caras para relaciones**: las de `Component2.GetBodies2` dan su geometría en coordenadas de la PIEZA y se pueden seleccionar (`Select4`, marca 1) para `AddMate5`. No recorras todas las caras de una pieza compleja (930 caras × 30 ms): `comp.FeatureByName("Op3").GetFaces` da solo las de esa operación. `asm.py` lo encapsula y cachea.
- **Trabajos largos** (38 relaciones, ~106 s) no caben en el límite de 60 s de una llamada: lánzalos como subproceso que escribe un log y consulta el log. Nunca dos procesos COM contra la misma sesión a la vez.
- **Planos**: la plantilla `Dibujo.drwdot` viene en metros (fija MMGS con `SetUserPreferenceInteger`); la primera vista reajusta la escala de la hoja (fija `ISheet.SetScale` después); el croquis de una vista usa unidades de modelo con el origen en el centro de la caja del modelo; en una vista de sección `Position` no es el centro de su contorno, y las vistas alineadas se alinean por `Position` (proyecta sin alinear y centra por contorno). Para acotar, `SelectByID2("", "EDGE", x, y)` en coordenadas de hoja calculadas con `IView.ModelToViewTransform` (hoja = T + s·(p·R)) funciona; el valor de la cota confirma que se eligió la arista buena.

### Operaciones de pieza (probadas el 27-09-2026, `scripts/probetas.py`)

- **La plantilla de pieza de fábrica está en METROS**: un DXF de chapa sale 1000 veces
  pequeño. `sw_new_part` fija ya MMGS; en piezas existentes, `fx.units_mm(doc)`. Fijar
  además `swUnitsLinear` deja el sistema en "Personalizado".
- **Asistente de taladro**: `HoleWizard5` devolvió None en todas las combinaciones. Funciona
  `CreateDefinition(swFmHoleWzd)` + `InitializeHole` + `SelectByRay` sobre la cara (el
  punto del rayo es la posición) + `CreateFeature`.
- **Simetría**: operaciones (marca 1) ANTES que el plano (marca 2). Un nervio no se deja
  simetrizar como operación: simetría de cuerpo (marca 256).
- **Croquis en Planta**: x = X, y = −Z. `fx.sketch_point()` convierte con
  `ModelToSketchTransform`; `IMathUtility.CreatePoint` necesita `VARIANT(VT_ARRAY|VT_R8)`.
- **Ecuaciones**: la variable global `"Espesor"` se rechaza (−1); `"EspesorPlaca"` va bien.
- **Normales**: `ISurface.EvaluateAtPoint` da la normal en [0:3] y
  `IFace2.FaceInSurfaceSense = True` significa normales OPUESTAS.
- **Brida de arista de chapa**: pendiente (con solo `Edge`, `BendAngle`, `OffsetDistance`
  devuelve None; pide perfil con `InsertSketchForEdgeFlange`).
- **Interferencias**: una entrada por trocito (201 en un reductor de correas, todas
  dientes correa/polea de < 0,01 mm³): `am.interferences` las agrupa por pareja.

### Rendimiento: lo único que importa es el número de llamadas COM

Cada ida y vuelta a SOLIDWORKS cuesta **~30 ms**. Medido, constante: da igual
que sea la primera o la milésima, que la interfaz sea nueva o conocida. No hay
calentamiento ni caché que valga.

Eso convierte el recuento de llamadas en *el* factor de coste, y lo demás en
ruido. Medido sobre la placa del selftest (53 aristas, 43 rectas):

| | ms | |
|---|---|---|
| Recorrer el cuerpo, 6 llamadas por arista | 7.085 | punto de partida |
| `GetCurveParams2`: los dos extremos de una vez, 3 por arista | 2.509 | 2,8× |
| Acotar a la cara de interés primero (11 aristas, no 53) | ~600 | ~12× |
| Apagar la actualización gráfica y `UserControl` | 6.341 | 1,1× — descartado |
| Enlace anticipado (`makepy`) | — | la biblioteca no se registra |

Las tres cosas que funcionaron son la misma idea: **evitar la llamada**. Las dos
que no, intentaban abaratarla.

En la práctica:

- `c.edges_on_plane(doc, z)` en vez de `straight_edges(doc)` + filtro. Localiza
  la cara y lee solo sus aristas; cae al cuerpo entero si no la encuentra.
- `c.planar_faces(doc, z)` ordena sus filtros por coste: `GetBox` es una llamada
  y descarta casi todo; `GetSurface` + `IsPlane` solo se gastan en las
  candidatas.
- `c.edge_geom(e)` usa `GetCurveParams2`, que la ayuda marca como **obsoleto**
  frente a `GetCurveParams3`. El sustituto devuelve un objeto del que hay que
  leer `StartPoint` y `EndPoint` con dos llamadas más: sería más lento. Aquí el
  obsoleto gana, y por eso sigue ahí.

Y el corolario para los scripts sueltos: `python algo.py` paga intérprete,
`import win32com` y `Dispatch` cada vez. El mismo código por `sw_execute_script`
corre en un proceso ya vivo con COM conectado. Los `.py` de `scripts/` son
para depurar; la vía normal es la tool.

### Enlace tardío de pywin32: usa `c.soft()`

Con `win32com.client.Dispatch` (enlace tardío, sin `makepy`), un miembro COM de
cero argumentos llega unas veces como método y otras **ya evaluado**:

```python
b.GetBodyBox()        # funciona: llega como método
edge.GetCurve()       # com_error -2147352573 "No se ha encontrado el miembro"
                      # -> GetCurve ya devolvió la curva; el () intenta invocarla
```

Varía por miembro, no hay regla: `GetCurve`, `GetEdges`, `GetStartVertex`,
`GetPoint`, `IsLine`, `CreateSelectData` llegan evaluados; otros no.

**`callable()` no sirve para distinguirlos**, y este es el detalle que hace
perder la tarde: un objeto COM devuelto por pywin32 es un `CDispatch`, y un
`CDispatch` también es `callable`. La única comprobación fiable es intentar la
llamada y, si COM responde que no hay miembro y no había argumentos, quedarse
con el valor. Eso es `core.soft()`:

```python
soft(edge, "GetCurve")           # en vez de  edge.GetCurve()
soft(body, "GetEdges") or []     # en vez de  body.GetEdges()
```

Disponible en cualquier script como `c.soft`. Los miembros **con** argumentos
(`doc.GetBodies2(0, True)`, `edge.Select4(append, data)`) se llaman normal.

Ni la typelib ni la ayuda oficial mencionan esto: solo aparece chocando contra
ello.

`server.py` usa la misma función (`c.soft`); no hay copias locales.

## Licencia

MIT (ver `LICENSE`). SOLIDWORKS es marca de Dassault Systèmes; este proyecto no
está afiliado ni respaldado por Dassault Systèmes.

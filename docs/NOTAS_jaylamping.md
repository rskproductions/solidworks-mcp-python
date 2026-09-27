# Notas de jaylamping/solidworks-mcp para sw_mcp

Leído el 24-09-2026 (commit `a0f07cd`, 08-08-2026). **Licencia MIT**: se puede
reutilizar código citando la fuente. Arquitectura: servidor MCP en TypeScript +
worker .NET 8 (STA) que habla COM; 133 tools por niveles (core/extended/...).

## ¿Tiene API que nosotros no?

Su `generated/api-catalog.json` sale de reflejar la DLL de interop: 504
interfaces, 15.705 nombres (sin firma ni semántica). Comparado con nuestro
índice (ayuda 2026 completa: 587 interfaces, 13.181 miembros documentados),
normalizando `get_X`/`set_X` a `X`:

- En interfaces comunes solo 114 miembros de 32 interfaces no salen en la ayuda
  (no documentados: `ISldWorks.GetCookie`, `IModelView.UnprojectModelPoint`,
  `IComponent2.GetRemainingDOFs`, `IAssemblyDoc.ShowComponent`...), y 6
  interfaces ocultas (`IMember`, `IEnumCurves`, `IJournalManager`...).
- **Todos están ya en nuestro `api_index.txt`** (volcado de la typelib de esta
  instalación, 506 interfaces): se encuentran con `sw_api_search(pattern=...)`.
- Nosotros tenemos 88 interfaces que ellos no (add-ins de `swpublished`,
  `dsgnchk`, manejadores de PropertyManager...), y además firma, parámetros,
  remarks, enums y 1.295 ejemplos.

Conclusión: en conocimiento de la API no nos falta nada; lo que tienen de más
son **herramientas ya hechas**, sobre todo de ensamblaje.

## Herramientas suyas que nos faltan (por prioridad para ensamblajes)

1. Ensamblaje: `insert_component` (con transformada), `get/set/reset_component_transform`,
   `set_component_fixed`, `unfix_all_components`, `list_components`, `list_mates`,
   `mate_coincident/distance/parallel/perpendicular/tangent/width/limit_angle/coord_sys`,
   `get_assembly_degrees_of_freedom`, `list_interferences`, `list_bom`.
2. Identidad estable de entidades: `GetPersistReference3` /
   `GetObjectByPersistReference3` (sobrevive a reconstrucciones, al contrario
   que los índices de cara/arista).
3. Medir: `measure`, `measure_distance`, `get_mass_properties`, `component_mass_properties`.
4. Seguridad: checkpoints automáticos antes de mutar (`.checkpoints/` junto al
   documento) y `confirm_and_save` (reconstruir → salud de relaciones → guardar
   → reabrir → comprobar pose).
5. Importar STEP: `GetImportFileData(path)` + `LoadFile4(path, "r", data, errors)`;
   nunca `OpenDoc6` (da `swFileRequiresRepairError` con STEP válidos).
6. `pack_and_go`, `replace_component_path`, `make_component_independent`.

## Hechos de la API que documentan

- Relaciones: preferir la entidad en **contexto de ensamblaje**; la `Face2` del
  documento de pieza a menudo no se deja seleccionar para una relación.
- Relación de ángulo: entidades con mark 1 y eje de referencia con mark
  67108864; vía moderna `CreateMateData` + `CreateMate`.
- `SelectByID2` sobre un eje a veces selecciona también puntos de datum con la
  misma marca: filtrar por tipo de selección.
- Nunca dos llamadas COM a la vez contra la misma sesión: SOLIDWORKS se cae
  (`0x800706BE`). Nuestro servidor ya es de un solo hilo.
- La ayuda de la API también está en local: `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\*.chm`
  (sldworksapi.chm, sldworksapiprogguide.chm). Alternativa sin red al crawler.

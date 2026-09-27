# Notas de SolidPilot (eyfel/mcp-server-solidworks) para sw_mcp

Leído el 24-09-2026 (commit `fc3c743`, 10-09-2026). **Licencia AGPL-3.0**: de aquí
se toman ideas y hechos sobre la API COM (eso no se protege), **no código**. Todo
lo que se implemente en sw_mcp se escribe desde cero.

## Qué es

MCP para SOLIDWORKS 2026 con otra arquitectura que la nuestra: capa C# .NET 4.8
(única que toca COM, servidor REST en localhost:5000) + compilador Python
determinista + adaptador MCP. La idea central: el modelo no llama métodos de la
API, emite una **Feature Graph IR** (JSON neutro de CAD) y un compilador la baja
a llamadas. 47 tools + 13 recursos MCP.

Nuestra apuesta es la contraria (y la sostenemos): **`sw_execute_script` +
índice de la API**, como el MCP nativo de Fusion. Menos capas, cero
compilación, y el modelo ve la API real. Lo que sí merece la pena copiar son
ideas concretas, abajo.

## Hechos de la API COM que confirman (aplicables ya)

| Tema | Hecho | Estado en sw_mcp |
|---|---|---|
| `AddComponent5` | El documento del componente tiene que estar **cargado** antes: `OpenDoc6` silencioso, re-activar el ensamblaje con `ActivateDoc3`, insertar, cerrar el precargado y **volver a activar** el ensamblaje (`CloseDoc` deja el activo impredecible). | hecho (`asm.py`, ensamblajes desde STEP) |
| Transformada de componente | `Component2.Transform2.ArrayData` = 13 dobles: 3x3 rotación por filas, traslación (m), escala. Para fijarla: `MathUtility.CreateTransform` con 16 (los 13 + 3 ceros) y asignar `Transform2`. | pendiente |
| Fijo / flotante | Estado determinista: `Select4` del componente + `FixComponent`/`UnfixComponent` (SW fija a veces el primero por su cuenta). | pendiente |
| `AddMate5` | Las dos entidades con `Select4` y **mark=1**; devuelve `Mate2` sin nombre: el nombre se lee del último subfeature `Mate2` del `MateGroup`. Distancia en m, ángulo en rad. | pendiente |
| Índices de caras/aristas | SW **renumera** caras y aristas tras cada operación: un índice solo vale hasta la siguiente reconstrucción. | coincide con lo nuestro (seleccionar por handle, `Select4`) |
| Importar STEP de ensamblaje | `OpenDoc6` con un STEP y 3D Interconnect apagado devuelve None con `errors=2097152` (engañoso). `LoadFile4` (traductor clásico) importa un STEP de ensamblaje como **pieza multicuerpo**; solo 3D Interconnect (toggle 691, y 758 apagado) lo convierte en ensamblaje con componentes. | útil si importamos STEP |
| Parámetros `[in,out]` | En C# van por `ref`. En pywin32 tardío: `VARIANT(VT_I4\|VT_BYREF, 0)` (lo comprobamos nosotros con `OpenDoc6`). | aplicado |
| Unidades | Todo SI (m, rad) en la frontera COM; ellos pasan grados en la frontera de sus tools. | igual |
| Constantes | Enteros en línea con el nombre en comentario, nunca cargar el ensamblado swconst. | igual (`sw_const`) |

## Ideas de diseño que valen para sw_mcp

1. **Recursos MCP para lo estático.** La descripción de una tool se paga en cada
   turno; un recurso solo cuando se lee. Candidatos: convenciones de
   `README` (unidades, `soft()`, selección), tabla de marcas de selección.
2. **`compare_parts` con veredicto objetivo**: "verified" = topología exacta
   (caras/aristas/vértices) **y** |ΔV| ≤ 1 % **y** |ΔA| ≤ 1 %. Nosotros solo
   comparamos volumen: añadir área y recuento topológico endurece la
   verificación (una cara en el lado equivocado no cambia el volumen).
3. **`compare_assemblies`**: mismas piezas, transformadas a ≤ 1 µm y rotación
   ≤ 1e-6, número y tipo de relaciones, propiedades de masa.
4. **`get_selection`**: leer lo que el usuario tiene seleccionado en la GUI
   (`GetSelectedObjectCount2` / `GetSelectedObjectType3` /
   `GetSelectedObject6`). Muy útil para trabajar a medias con el usuario:
   "esta cara" en vez de coordenadas.
5. **`analyze_model` por modos** (geometry, mass_properties, bodies, features,
   edges, faces, sketch) con salida compacta: el modelo pide solo lo que
   necesita.
6. **Idempotencia / `state_version`**: cada operación con id; si se repite no se
   duplica. Útil sobre todo con los timeouts de 60 s del puente, que ya nos
   han dejado operaciones hechas a medias sin respuesta.
7. **`ensure_ready`**: arrancar SOLIDWORKS si está cerrado y adjuntarse (hoy,
   si SW se cae, sw_mcp se queda con un puntero COM muerto hasta reiniciar).

## Qué NO copiamos

- La IR + compilador: añade una capa entera que hay que mantener al ritmo de la
  API; nosotros ya tenemos el índice completo de la ayuda para escribir COM
  directo con seguridad.
- El servidor C#/.NET: nuestro proceso Python único (COM STA, sin REST) es más
  simple y ha demostrado bastar.

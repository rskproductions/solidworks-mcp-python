# Notas de 3DS Developer Assistance (R2026x FD03)

Revisado el 24-09-2026 (página abierta por el usuario con su sesión, sin credenciales
introducidas por Claude): `media.3ds.com/support/documentation/developer/cloud/R2026x-FD03`.

## Qué contiene

| Sección | Para qué es |
|---|---|
| Native Apps C++ (CAA) | Apps nativas de la plataforma 3DEXPERIENCE (base CATIA V6): reglas de C++, feature modeler, CGM... |
| Native Apps Automation | VBA/Python para las apps de 3DEXPERIENCE (Part Design, Drafting, Assembly Design de CATIA) |
| Web Services and Events | REST de la plataforma: 3DPassport, 3DSpace (Document, Drawing, Engineering, Lifecycle, Derived Outputs...), 3DDrive, 3DSwym, 3DCompass |
| Vertical Integration, Simulation Services, Data Science, Toolset | Integraciones de empresa |

## Conclusión para sw_mcp

- **No afecta a nuestra capa COM.** Nada de esto es la API de SOLIDWORKS de
  escritorio (`sldworks`); esa ya está entera en `sw_api_index.sqlite`. El
  "Automation" de aquí es el de CATIA/3DEXPERIENCE, con otro modelo de objetos.
  No se indexa.
- Único punto de contacto posible: los **servicios REST de 3DSpace/3DDrive**,
  si algún día los ficheros de trabajo viven en la plataforma (subir el PDF y
  el STEP como salidas derivadas, consultar el ciclo de vida o las revisiones).
  Requiere autenticación con 3DPassport (ticket de login + token CSRF) y que la
  licencia (SOLIDWORKS Maker) dé acceso a esos servicios. Hoy los ficheros de
  los proyectos actuales son locales, así que no se implementa.

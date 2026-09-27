# Notas de Makera (Carvera Air) para mecanizado.py

Leido el 27-09-2026: wiki.makera.com (~70 paginas; 5 de knowledge-sharing dan
404) y github.com/MakeraInc. Son resumenes propios; verificar cifras criticas
en la fuente. CarveraProfiles no tiene licencia: sus datos NO se copian aqui.

## Maquina (Air)
- Area 300 x 200 x 130 mm (makera.com y Makera_Air.fcm de CarveraProfiles).
  La wiki no da recorridos.
- Cambio de herramienta manual rapido (collar reutilizable, ~12 mm de cola
  para pinzar); la C1 tiene ATC de 6. Paginas del Air copian texto de la C1
  (T1-T6, sonda inalambrica): tomarlas con cuidado.
- Pinzas: 1/8" de serie; 4, 6 y 1/4" opcionales. Herramienta < 76 mm total,
  cabeza <= 1/4" (FAQ generica).
- Sonda con cable (Z, margen, autonivelado); TLO automatico al calibrar.
- Laser de diodo 5 W (C1: 2,5 W), foco ~5 mm bajo el modulo; grabar, no cortar.

## Sujecion y origen
- Anclas: 1 abajo-izquierda, 2 en el centro, con escuadra en L (2 pasadores
  de 4 mm + 3 M5). Origen de trabajo = offset X/Y desde el ancla.
- >= 10 mm entre la escuadra y el inicio de la trayectoria.
- Bridas superiores: piezas < 20 mm. Spoil board 1-2 mm para cortes pasantes.
- Cero maquina arriba a la derecha (coordenadas negativas). Z0 = cara
  superior del bruto (palpada). Soft limits desde firmware 0.9.5.

## G-code (firmware basado en Smoothieware)
- Solo G54. Sin ciclos fijos (G81-G83), sin G41/G42/G43 (TLO por M6/M491).
- M600 = pausa (no M0/M1); M30 no hace nada; M7/M9 aire (no M8).
- M6 Tn (T0 sonda, T-1 sin herramienta); M321/M322 modo laser; G32 autonivelado.
- El .nc se SUBE a la maquina (WiFi/USB) y se ejecuta desde su memoria.

## CAM
- Makera CAM: importa STEP (seleccion de CARAS: 3D Pocket solo con STEP),
  STL (malla), DXF/SVG, imagen, Gerber. Herramientas T1-T6 en la libreria.
  Solo previsualiza trayectorias, sin deteccion de colisiones.
- Posts oficiales en CarveraProfiles: SOLIDWORKS CAM (Carvera-3axis.ctl/.lng
  en ProgramData\SOLIDWORKS\SOLIDWORKS CAM 20XX\Posts\Mill), SolidCAM
  (gMilling_Carvera_3x.gpp/.vmid), Mastercam (MPFAN - C.pst), Fusion,
  VCarve, NX, GibbsCAM, Carveco, FreeCAD.
- CarveraProfiles trae 615 fichas de fresa (FreeCAD .fctb) con diametro, largo
  de filo y parametros por material.

## Parametros de referencia (fresa 1/8", 1 filo)
| Material | rpm | avance mm/min | penetracion | DOC mm |
|---|---|---|---|---|
| Aluminio | 12000 | 500 | 200 | 0,1-0,2 |
| Laton / cobre | 12000 | 300 | 100 | 0,05-0,1 |
| Madera dura / plastico | 10000 | 1000 | 300 | 0,5-1 |
| Madera blanda | 10000 | 1000 | 300 | 1-2 |
Roscas por fresado M1-M5 (broca previa M3 2,5 / M4 3,3 / M5 4,2).

## Lo que la wiki NO dice
Precision/tolerancias, longitud maxima de voladizo, limites del 4.o eje,
radios minimos o paredes finas: para eso, reglas generales de CNC.

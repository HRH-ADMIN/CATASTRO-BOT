# assets/

Activos visuales del proyecto.

## icon.ico (pendiente)

El shortcut de escritorio (`tools/install_desktop_shortcut.ps1`) busca
`assets/icon.ico`. Si no existe, usa un icono fallback de Windows
(globo terráqueo de SHELL32.dll).

**Para generar el icono real:**

1. Diseñar una imagen 256×256 con una "C" estilizada o una brújula de
   agrimensor (sugerido por el plan).
2. Exportar a `.ico` con resoluciones embebidas 16/32/48/256.
   Herramientas: GIMP, ImageMagick (`magick convert in.png -define icon:auto-resize=16,32,48,256 icon.ico`),
   o servicios online como icoconvert.com.
3. Guardar en `assets/icon.ico`.
4. Re-ejecutar `catastro-bot install-shortcut` para que tome el nuevo icono.

Una vez que `icon.ico` exista, este README puede quedar como histórico
o eliminarse.

Plan: PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-01 (paso 1).

# Manual de Pruebas Manuales — catastro-bot

Pruebas que NO se pueden automatizar (efecto colateral en el SO del operador,
hardware físico como Firma Digital, etc.). Cada tarea del PLAN_MEJORAS que
requiera test manual deja su procedimiento documentado acá.

---

## U-01 — Shortcut escritorio al dashboard

**Sprint:** 1
**Fecha de implementación:** 2026-05-22
**Branch:** `sprint-presprint/housekeeping`

### Procedimiento

1. Ejecutar:
   ```powershell
   .venv\Scripts\python.exe tools\catastro_bot.py install-shortcut
   ```
2. Verificar mensaje `[OK] Shortcut creado/actualizado` con ruta del .lnk.
3. Confirmar visualmente que existe `Catastro-Bot Dashboard.lnk` en el Escritorio.
4. Doble-click sobre el shortcut.
5. Esperado: el navegador por defecto abre `http://localhost:9224/`.
   - Si el dashboard está corriendo: muestra la tabla de expedientes.
   - Si NO está corriendo: el browser muestra "no se puede conectar". Eso es
     correcto — el shortcut no levanta el dashboard, solo lo abre. Para
     levantarlo: `catastro-bot dashboard-web` o `catastro-bot abrir` (ese sí
     lo arranca si no está).
6. Re-ejecutar el comando para verificar **idempotencia**: debe sobrescribir
   el .lnk sin error.

### Inspección del .lnk (opcional, para verificar atributos):

```powershell
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut("$env:USERPROFILE\Desktop\Catastro-Bot Dashboard.lnk")
"Target: $($lnk.TargetPath)"
"Args:   $($lnk.Arguments)"
"Icon:   $($lnk.IconLocation)"
```

**Valores esperados:**
- Target: `C:\Windows\System32\rundll32.exe`
- Args: `url.dll,FileProtocolHandler http://localhost:9224/`
- Icon: `C:\WINDOWS\System32\SHELL32.dll,14` (globo terráqueo, fallback) —
  o `C:\catastro-bot\assets\icon.ico,0` si se generó icono customizado.

### Criterios de aceptación

- [x] Doble click en el icono del escritorio abre el navegador por defecto en la URL del dashboard.
- [x] `catastro-bot install-shortcut` funciona idempotentemente.
- [ ] El icono es reconocible visualmente. **Pendiente:** generar `assets/icon.ico` (ver `assets/README.md`).

### Tests automatizados relacionados

`tests/test_catastro_bot_cli.py::TestAyuda::test_install_shortcut_registrado`
verifica que el subcomando está en el router y que el script PS no tiene
caracteres Unicode que rompan PowerShell 5.1.

---

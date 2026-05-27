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

## U-04 — Dashboard real-time (SSE)

**Sprint:** 1
**Fecha de implementación:** 2026-05-22
**Branch:** `sprint-1/u-04-sse`

### Procedimiento

1. **Arrancar el bot completo** (scheduler + dashboard Flask en el mismo proceso):
   ```powershell
   .venv\Scripts\python.exe -m src.main
   ```
   Logs deberían mostrar: `dashboard escuchando en http://127.0.0.1:9224`.

2. **Abrir el dashboard** en el browser: `http://localhost:9224/`
   - Verificar que el chip del header dice `live ✓` (verde pulsante).
   - Verificar en DevTools → Network que hay una conexión activa a
     `/api/events/stream` con `Content-Type: text/event-stream`.

3. **Verificar el flujo de eventos** desde una terminal separada,
   forzando una mutación:
   ```powershell
   .venv\Scripts\python.exe -c "
   from src.core.credential_manager import CredentialManager
   from src.core.database import Database
   from src.models.estado import Estado
   db = Database(credentials=CredentialManager())
   # Pickear un expediente activo
   exps = db.listar_expedientes(completados=False)
   exp = exps[0]
   # Forzar una actualización de metadata (no cambia estado_actual, no rompe el flujo real)
   db.actualizar_metadata(exp['id'], {'_u04_test_ping': '2026-05-22'}, actor='test-manual')
   print(f'OK — disparado evento para {exp[chr(34)+chr(34)]} ... esperá ver flash en dashboard')
   "
   ```

4. **Resultado esperado:**
   - El chip `live ✓` parpadea brevemente al recibir el evento.
   - La fila del expediente afectado **se ilumina (flash azul ~1.2s)**.
   - ~350ms después, la página hace reload automático.
   - Latencia total desde el comando hasta el flash: **< 1 segundo**
     (vs ~30s con el meta refresh viejo).

5. **Limpieza del test** (eliminar el campo `_u04_test_ping`):
   ```powershell
   .venv\Scripts\python.exe -c "
   import json, sqlite3
   conn = sqlite3.connect('data/catastro.db')
   conn.row_factory = sqlite3.Row
   for r in conn.execute('SELECT id, metadata_json FROM expedientes'):
       m = json.loads(r['metadata_json'] or '{}')
       if '_u04_test_ping' in m:
           m.pop('_u04_test_ping')
           conn.execute('UPDATE expedientes SET metadata_json = ? WHERE id = ?',
                        (json.dumps(m), r['id']))
   conn.commit()
   print('Limpieza OK')
   "
   ```

### Pruebas de robustez del SSE

#### Conexión perdida
1. Con el dashboard abierto y `live ✓`, matar el proceso `src.main`.
2. **Esperado:** el chip cambia a `reconectando…` (rojo).
3. Re-arrancar `src.main`.
4. **Esperado:** el chip vuelve a `live ✓` en 3-5 segundos
   (gracias al `retry: 3000` del server).

#### Server silencioso
1. Con el dashboard abierto, simular silencio del server (no es
   reproducible fácilmente porque el bus tiene heartbeat automático
   cada 25s — sería un bug si dispara este caso).
2. Si por algún motivo no llegan eventos en >60s: el chip pasa a
   `sin eventos recientes` (amarillo). En >120s: `sin actividad >2 min`.

#### JavaScript deshabilitado
1. En el browser, deshabilitar JS y recargar `http://localhost:9224/`.
2. **Esperado:** la página se recarga full-page cada 30s (fallback
   via `<noscript><meta http-equiv="refresh" content="30"></noscript>`).
3. Re-habilitar JS y recargar: vuelve al modo live.

### Inspección directa del stream (curl)

```powershell
curl -N http://localhost:9224/api/events/stream
```

Output esperado (formato SSE):
```
retry: 3000

data: {"type": "hello", "subscribers": 1}

data: {"type": "heartbeat", "ts": "2026-05-22T..."}

data: {"type": "expediente_updated", "id": "...", "estado_actual": "...", ...}
```

`Ctrl+C` para cerrar.

### Criterios de aceptación

- [x] Diagnóstico documenta inconsistencias detectadas y fixes aplicados
      (`docs/DIAGNOSTICO_SYNC_DATOS.md`).
- [x] Cambio de estado del expediente se refleja en dashboard **≤ 2s** sin recargar
      la página (verificado por inspección visual + test e2e
      `test_dashboard_sync.py`).
- [x] `fecha_actualizacion` coherente con la última mutación real
      (trigger SQL garantiza el invariante).
- [x] Tests pasando: 4 e2e + 10 event_bus + 2 web_app SSE + 14 view +
      8 trigger = **38 tests nuevos** de U-04.

### Tests automatizados relacionados

| Archivo | Tests | Cobre |
|---|---|---|
| `test_updated_at_trigger.py` | 8 | Trigger SQL `expedientes_touch_fecha_actualizacion` |
| `test_view_v_expedientes_dashboard.py` | 14 | Vista SQL con detección de divergencia |
| `test_event_bus.py` | 10 | Pub/sub in-memory + drop policy + singleton |
| `test_web_app.py` (sección SSE) | 2 | Endpoint `/api/events/stream` mimetype + headers |
| `test_dashboard_sync.py` | 4 | E2E: mutación → publish → subscriber recibe |

---

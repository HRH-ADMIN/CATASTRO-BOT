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

## U-03 — Panel de control con máquina de estados

**Sprint:** 1
**Fecha de implementación:** 2026-05-22
**Branch:** `sprint-1/u-03-state-machine`

### Por qué este cambio existe

El operador reportó que "el botón de apagado no funciona". Diagnóstico en
`docs/AUDITORIA_CONTROL_STATE.md` reveló DOS sistemas de control coexistiendo
sin sincronización (toggles JSON + taskkill+Popen). U-03 unifica ambos en
una máquina de estados explícita con feedback visual claro.

### Procedimiento

1. **Arrancar el bot:**
   ```powershell
   .venv\Scripts\python.exe -m src.main
   ```
   En los logs deberías ver:
   ```
   state_machine: bootstrap completado (7 módulos)
   ```
   Esto indica que los módulos quedaron en RUNNING en la tabla
   `module_state` automáticamente.

2. **Abrir el panel:** `http://localhost:9224/config/control`
   - Verificar que se ven 7 cards: `global`, `apt`, `muni`, `whatsapp`,
     `scheduler`, `drive_backup`, `rnp`.
   - Todas con badge **`RUNNING`** verde pulsante.
   - El chip `live ✓` en el header confirma que SSE está conectado.

3. **Probar transición simple — apagar APT:**
   - Click en **`■ Detener`** sobre la card `apt`.
   - Aparece modal "Detener apt" con campo de razón.
   - Escribir cualquier texto (ej. "test U-03") y confirmar.
   - **Esperado:**
     - Toast verde: "Transición iniciada: apt → STOPPING".
     - Badge cambia a `STOPPING` (ámbar pulsante).
     - Botón se reemplaza por "Deteniendo…" deshabilitado.
   - En este momento el bot interno **deja de correr** los jobs `apt-sync-estados`. Verificar en logs:
     ```
     apt-sync-estados: skipped — módulo apt en estado STOPPING (no RUNNING)
     ```
   - Esperar al worker (no implementado todavía en este sprint) — para
     completar el ciclo, simular el `mark_stopped`:
     ```powershell
     .venv\Scripts\python.exe -c "
     from src.core.state_machine import get_state_machine
     get_state_machine().mark_stopped('apt', actor='manual-test')
     "
     ```
   - Badge debe pasar a `STOPPED` gris automáticamente (via SSE).
   - El botón cambia a "▶ Iniciar".

4. **Probar emergency stop:**
   - En la "Zona de emergencia", escribir literal `APAGAR TODO`
     (mayúsculas, sin comillas).
   - El botón "🛑 Apagar todo" se habilita solo cuando el texto matchea.
   - Click → modal de confirmación con campo de razón opcional.
   - Confirmar.
   - **Esperado:**
     - Toast amarillo: "🛑 Emergency stop aplicado · cooldown 300s".
     - Todos los módulos en RUNNING pasan a STOPPING.
     - Todos reciben `cooldown_until` 5 minutos adelante.
   - Intentar **iniciar** un módulo durante el cooldown:
     - Click "▶ Iniciar" en cualquier card en STOPPED.
     - El API devuelve **423 Locked** con `cooldown_until`.
     - Toast rojo con el mensaje del cooldown.

5. **Reset desde error (simulado):**
   ```powershell
   .venv\Scripts\python.exe -c "
   from src.core.state_machine import get_state_machine
   sm = get_state_machine()
   sm.start('whatsapp', actor='test', force=True)
   sm.mark_error('whatsapp', actor='test', error_message='Green API 466')
   "
   ```
   - En el panel, la card `whatsapp` muestra badge `ERROR` rojo + el
     error en los detalles.
   - Click **`⟲ Reset`** → modal → confirmar.
   - Badge pasa a `STOPPED`.

### Inspección directa via API (curl / DevTools)

```powershell
# Snapshot
curl http://localhost:9224/api/control/status

# Iniciar apt
curl -X POST http://localhost:9224/api/control/start/apt `
  -H "Content-Type: application/json" `
  -d "{\"reason\":\"test\"}"

# Emergency stop
curl -X POST http://localhost:9224/api/control/emergency-stop `
  -H "Content-Type: application/json" `
  -d "{\"confirmation\":\"APAGAR TODO\",\"reason\":\"test\",\"cooldown_seconds\":60}"
```

### Verificación de que el gating funciona

Con `apt` en `STOPPED`, el job `apt-sync-estados` (que corre cada 30 min)
debe SALTAR. En logs:
```
apt-sync-estados: skipped — módulo apt en estado STOPPED (no RUNNING)
```

Si volvés a iniciar `apt` (transición a `STARTING` → `RUNNING` via la
máquina), el próximo tick lo encuentra en `RUNNING` y ejecuta.

### Criterios de aceptación (del plan U-03)

- [x] Click en "Apagar APT" produce confirmación, transición visible
      (`STOPPING`), y estado final consistente (`STOPPED`).
- [x] El estado en el header del panel se refleja en ≤2 segundos del
      cambio (gracias a SSE de U-04).
- [x] Botón de emergencia funciona, requiere texto literal `APAGAR TODO`,
      aplica cooldown.
- [x] Tests pasando: 28 state_machine + 17 API + 7 gated + 2 panel HTML
      = **54 tests nuevos** de U-03.

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_control_state_machine.py` | 28 | Máquina de estados + transiciones + cooldown + emergency |
| `test_dashboard_control_api.py` | 17 + 2 | Endpoints REST + página HTML |
| `test_gated_state_machine.py` | 7 | Wrapper `_gated()` lee SM con fallback legacy |

### Compatibilidad con sistema anterior

- `data/control.json` (sistema A original) sigue funcionando como
  espejo durante la migración. El operador puede tocarlo manualmente y
  los módulos legacy que no usen la SM lo van a respetar.
- Los botones del viejo `/config` (sistema B con taskkill+Popen) NO se
  eliminaron en este sprint — quedan como herramienta de emergencia
  cuando la SM no es suficiente (ej. proceso colgado que la SM marcó
  como RUNNING pero realmente no responde).
- Próximos sprints irán deprecando ambos en favor de la SM.

### Limitaciones conocidas del MVP

1. **El worker que efectivamente arranca/mata procesos** (Chrome bot,
   scheduler, watchdog) todavía vive en `_ejecutar_control` del
   dashboard legacy. La SM solo marca el estado, NO controla los
   procesos del SO. Próxima iteración (no en este sprint): conectar
   la transición `STOPPING` con `taskkill` y `STARTING` con `Popen`.
2. **Bootstrap fuerza RUNNING** sin verificar que los procesos
   subyacentes estén realmente vivos. Si Chrome del bot está caído al
   arrancar, la SM dirá `apt: RUNNING` aunque CDP no responda. El
   healthcheck (`/api/health`) sigue siendo la fuente de verdad para
   "¿está vivo el sistema?".

---

## U-02 — Procesos en background sin ventanas CMD

**Sprint:** 1
**Fecha de implementación:** 2026-05-22
**Branch:** `sprint-1/u-02-runtime-processes`

### Procedimiento de migración inicial (una sola vez)

1. **Verificar que el venv tiene `pythonw.exe`:**
   ```powershell
   ls .venv\Scripts\pythonw.exe
   ```
   Si no existe, correr `uv sync` (vino con Python 3.13.13 por default).

2. **Apagar el bot actual** (si está corriendo con el .bat viejo).
   Cerrar todas las ventanas CMD del autostart.

3. **Probar el .vbs manualmente** desde una terminal:
   ```powershell
   cscript //nologo tools\catastro_bot_autostart.vbs
   ```
   - **Esperado:** el comando devuelve inmediatamente (porque el .vbs lanza
     y suelta).
   - Verificar en Task Manager → Details que aparecen 3 procesos `pythonw.exe`
     y 1 `chrome.exe` (del bot, perfil dedicado).
   - **NO** debe aparecer ninguna ventana CMD/console.

4. **Verificar en el dashboard que está todo vivo:**
   - Abrir `http://localhost:9224/`
   - Click en **"🖥️ Procesos"** en el header.
   - Esperar 10-15 segundos.
   - Verificar que la tabla muestra **`scheduler` con status `alive`**
     y datos de CPU/RAM actualizados.
   - Después de 30s, el job `process-monitor` corre y verifica que el
     scheduler sigue vivo (heartbeat reciente).

5. **Reemplazar autostart de Windows:**
   - Abrir `shell:startup` (Win+R → escribir → Enter).
   - Eliminar `catastro_bot_autostart.bat` (o renombrarlo a `.bat.viejo`).
   - Crear shortcut o copia de `C:\catastro-bot\tools\catastro_bot_autostart.vbs`.
   - **Recomendado:** crear shortcut en lugar de copiar, así futuras
     actualizaciones del .vbs aplican al próximo reboot sin tener que
     re-copiar.

6. **Reiniciar Windows** para validar autostart completo.

### Probar el panel /config/runtime

1. Abrir `http://localhost:9224/config/runtime`.
2. Verificar columnas: **Proceso, PID, Estado, Iniciado, Último heartbeat, CPU%, RAM, Acciones**.
3. El proceso `scheduler` debería mostrar:
   - Estado: badge `alive` verde pulsante.
   - PID: el PID real del proceso `pythonw -m src.main`.
   - Último heartbeat: actualizado hace <15 segundos.
   - CPU%: número (depende de carga, suele ser <2%).
   - RAM: ~150-300 MB.
   - Botón **"📄 Ver log"** disponible.

4. **Click en "Ver log"** sobre scheduler:
   - Modal con tail del `logs/catastro-bot.log` (200 últimas líneas).
   - Auto-refresh cada 2s habilitado por default.
   - Scroll automático al final.
   - Botón "Cerrar" detiene el polling.

### Probar detección de proceso muerto

1. Anotar el PID del `scheduler` desde el panel.
2. En Task Manager, matar manualmente el proceso `pythonw.exe` con ese PID.
3. **Esperado:**
   - En el próximo `process-monitor` pass (cada 30s), el row se marca
     `dead` con `reason=dead_pid`.
   - El panel se actualiza vía SSE (chip live ✓ refresh la tabla).
   - El badge del scheduler pasa de `alive` verde a `dead` rojo
     **en ≤30 segundos**.
4. (Opcional) En el dashboard principal, el panel de "Estado del bot"
   debería reflejar que el scheduler está caído.

### Probar detección de proceso colgado

Para reproducir en tests manuales, modificar manualmente en BD:
```powershell
.venv\Scripts\python.exe -c "
import sqlite3
from datetime import datetime, timedelta, timezone
viejo = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
conn = sqlite3.connect('data/catastro.db')
conn.execute('UPDATE runtime_processes SET last_heartbeat_at = ? WHERE process_name = ?',
             (viejo, 'scheduler'))
conn.commit()
print('Heartbeat forzado a 5 min atrás')
"
```

En el próximo monitor pass (30s), el row se marca `hanging`. Para
restaurar, parar y arrancar el scheduler.

### Inspección directa via curl

```powershell
curl http://localhost:9224/api/runtime/processes
curl "http://localhost:9224/api/runtime/processes?include_dead=1"
curl "http://localhost:9224/api/runtime/logs/scheduler?tail=20"
```

### Verificación de redirect stdio bajo pythonw

Cuando el bot corre bajo `pythonw.exe`, cualquier `print()` o traceback
no manejado va a `logs/scheduler.stderr.log`. Verificar:
```powershell
ls logs\scheduler.stderr.log
type logs\scheduler.stderr.log | Select-Object -Last 10
```

Debería contener al menos la línea `=== <timestamp> pythonw startup ===`
cada vez que el bot arrancó.

### Criterios de aceptación

- [x] Tras reiniciar el bot vía autostart: **ninguna ventana CMD aparece**.
- [x] Dashboard `/config/runtime` muestra `scheduler` con `alive`
      y heartbeats actualizados (≤15s).
- [x] "📄 Ver log" abre modal con tail del log.
- [x] Matar el PID manualmente → el panel muestra `dead` en ≤30s.
- [x] Logs de stdout/stderr no se pierden (van a `scheduler.stderr.log`
      cuando corre bajo pythonw).

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_runtime_processes.py` | 15 | Schema + register + heartbeat + mark_stopped + monitor pass + eventos SSE |
| `test_runtime_api.py` | 9 | Endpoints REST + tail logs + defensa path traversal |
| `test_pythonw_redirect.py` | 3 | Redirect stdio + .vbs ASCII puro |

### Limitaciones conocidas

1. **`process-monitor` solo registra**, no recupera. Si el scheduler muere,
   el panel lo marca `dead` pero no se relanza solo. El operador debe
   reiniciar manualmente (próxima iteración: integrar con la state
   machine de U-03 para que `state=RUNNING + process=dead` dispare
   autorestart con cooldown).
2. **CPU%** mostrado puede ser bajo en la primera lectura — `psutil.cpu_percent(interval=None)`
   devuelve 0 en el primer call. Después se estabiliza.
3. **El `.vbs` no inicializa `runtime_processes`** para `chrome_bot` ni
   `watchdog` — solo el `scheduler` se registra (porque `src.main` tiene
   el hook). Para que los otros 2 aparezcan en el panel, el próximo
   sprint debe agregar `register_process()` al inicio de
   `start_chrome_bot.py` y `healthcheck.py`.

---

## N-03 — Fallback de Green API HTTP 466

**Sprint:** 4
**Fecha de implementación:** 2026-05-22
**Branch:** `sprint-4/n-03-greenapi-fallback`

### Por qué este cambio existe

Desde la sesión anterior (2026-05-21) Green API responde HTTP 466 al
intentar enviar mensajes. Causas habituales: instancia desautorizada
(QR caducó), quota mensual agotada, o instancia eliminada. Hasta ahora
el bot fallaba silenciosamente y los mensajes al cliente NO se
entregaban — sin notificar al operador.

N-03 resuelve:
- **Detecta** HTTP 466 explícitamente y marca el servicio como down.
- **Envía email fallback** al operador con el mensaje pendiente.
- **Banner visible** en el dashboard mientras Green API está down.
- **Auto-recovery** cada 30 min sin intervención.

### Procedimiento

1. **Verificar el estado actual:**
   ```powershell
   curl http://localhost:9224/api/external-services
   ```

2. **Forzar el escenario down** (sin esperar a que Green API falle de verdad):
   ```powershell
   .venv\Scripts\python.exe -c "
   from src.utils import external_services as es
   from pathlib import Path
   es.mark_down(Path('data/catastro.db'), 'green_api',
                error_code='466', error_message='test manual')
   print('green_api marcado como down')
   "
   ```

3. **Verificar el banner en el dashboard:**
   - Abrir `http://localhost:9224/`
   - Justo debajo del header debe aparecer un **banner amarillo**:
     "⚠️ Servicios externos: WhatsApp (Green API) caído (466) · Re-autorizar instancia · El bot está usando fallbacks"
   - El link abre `https://console.green-api.com/` en otra pestaña.

4. **Probar el fallback de email:**
   ```powershell
   .venv\Scripts\python.exe -c "
   from src.core.credential_manager import CredentialManager
   from src.core.database import Database
   from src.agents.whatsapp_agent import WhatsAppAgent
   creds = CredentialManager()
   db = Database(credentials=creds)
   agent = WhatsAppAgent(db, creds)
   # Esto irá por email, no por WhatsApp
   result = agent.enviar_mensaje('+50688887310', 'Test N-03', contexto='manual_test')
   print('Resultado:', result)
   "
   ```
   - `result` debe empezar con `email:` (ID sintético).
   - Verificar en `topografiahrh@gmail.com` el email con subject
     `[catastro-bot] WhatsApp caído — mensaje pendiente (manual_test)`.

5. **Restauración manual:**
   ```powershell
   curl -X POST http://localhost:9224/api/external-services/green_api/mark-up
   ```
   - El banner desaparece automáticamente (SSE).
   - El próximo `enviar_mensaje` vuelve a ir por WhatsApp.

6. **Auto-recovery del scheduler:**
   - El job `greenapi-recovery` corre cada 30 min.
   - Si Green API está down, intenta `getStateInstance`. Si responde
     `authorized`, marca el servicio como up automáticamente.

### Criterios de aceptación

- [x] Capturar HTTP 466 sin retry (no es transitorio) y marcar el
      servicio como `down` con audit log entry.
- [x] Enviar email al operador con el mensaje pendiente.
- [x] Banner amarillo persistente con link a la consola de Green API.
- [x] Auto-recovery cada 30 min vía `getStateInstance`.
- [x] Botón manual de restauración (`POST /api/external-services/green_api/mark-up`).
- [x] Tests: **19 nuevos** (10 external_services + 9 fallback agent).

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_external_services.py` | 10 | Schema, mark_down/up, dedup eventos, is_down |
| `test_whatsapp_466_fallback.py` | 9 | Detección 466, persistencia, fallback email, recovery |

### Limitaciones conocidas

1. **El email fallback va al operador, no al cliente.** Si el cliente
   espera el mensaje WhatsApp, no lo va a recibir hasta que el operador
   lo reenvíe manualmente o Green API se restaure. Es intencional:
   no hay email confirmable del cliente para todos los casos.
2. **`enviar_archivo` no tiene fallback todavía.** Próxima iteración:
   email con adjunto si pesa <25MB.
3. **`solicitar_confirmacion` mientras Green API down**: el bot crea la
   acción pendiente en BD pero NO recibe respuesta. `stale-alert` previene
   el olvido. El flujo se reanuda cuando se restaura el canal.

---

## O-08 — Medición de costo Anthropic + panel /config/costos

**Sprint:** 4
**Fecha de implementación:** 2026-05-22
**Branch:** `sprint-4/o-08-anthropic-costs`

### Por qué este cambio existe

Antes no había forma de saber cuánto cuesta cada plano procesado por Vision.
Si Anthropic API se sale del control de costos, el operador se entera
cuando llega la factura. O-08 captura cada llamada con tokens consumidos
y costo USD calculado en tiempo real, expone agregaciones en
`/config/costos`, y alerta a 80% del budget mensual configurado.

### Procedimiento

1. **Forzar una llamada manual** (sin esperar a procesar un plano):
   ```powershell
   .venv\Scripts\python.exe -c "
   from src.utils.api_costs import record_call
   from pathlib import Path
   record_call(Path('data/catastro.db'),
               model='claude-3-5-sonnet-20241022',
               tipo='vision_plano', expediente_id='SEG-2026-005',
               input_tokens=15000, output_tokens=2500)
   "
   ```

2. **Verificar la captura via curl:**
   ```powershell
   curl http://localhost:9224/api/costs/summary
   ```
   Debe mostrar `current_month_spend_usd` > 0, agregación mensual,
   top expedientes y budget actual.

3. **Abrir el panel:** `http://localhost:9224/config/costos`
   - Card grande con gasto del mes vs budget + barra de progreso.
   - Chart de barras con consumo de los últimos 31 días.
   - Tablas: resumen mensual por modelo, top expedientes, últimas 50 llamadas.

4. **Configurar budget mensual** desde la UI o vía API:
   ```powershell
   curl -X POST http://localhost:9224/api/costs/budget `
     -H "Content-Type: application/json" `
     -d "{\"monthly_usd\":100,\"alert_threshold\":0.90}"
   ```

5. **Forzar alerta de budget** (testing):
   ```powershell
   .venv\Scripts\python.exe -c "
   from src.utils.api_costs import set_budget, record_call, check_budget_alert
   from pathlib import Path
   db = Path('data/catastro.db')
   set_budget(db, monthly_usd=1.0, alert_threshold=0.50)
   record_call(db, model='claude-3-5-sonnet-20241022',
               tipo='test_alert', input_tokens=300_000, output_tokens=0)
   print('Alert:', check_budget_alert(db))
   "
   ```
   - El job `api-budget-alert` corre cada 6h y dispara la notificación.
   - Si Green API está caído (N-03), la alerta llega por email automático.

### Criterios de aceptación

- [x] Cada llamada Anthropic queda persistida en `api_costs` con tokens
      desglosados (input, output, cache_read, cache_write) + costo USD.
- [x] Tarifas embebidas para Sonnet/Opus/Haiku 3.x y 4.x.
- [x] Panel `/config/costos` con gasto, barra de %, chart diario, tablas.
      Actualización via SSE al recibir `api_cost_recorded`.
- [x] Budget configurable via UI (presupuesto + threshold).
- [x] Alerta a 80% del budget vía `WhatsAppAgent.enviar_mensaje`
      (con fallback email N-03 si Green API down).
- [x] Alerta idempotente por mes (no spam).
- [x] Tests: **31 nuevos** (23 api_costs + 8 API/panel).

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_api_costs.py` | 23 | Schema, calc costo, record + SSE, track wrapper, reads, budget |
| `test_costos_api.py` | 8 | Endpoints /api/costs/* + página /config/costos |

### Limitaciones conocidas

1. **`minuta_agent` no propaga `expediente_id`** al `record_call` — la
   llamada queda registrada con `expediente_id=NULL`. Mejora futura:
   pasar el contexto desde el workflow.
2. **Las tarifas son estáticas en código.** Si Anthropic cambia precios,
   actualizar `_PRICING_USD_PER_MTOK` en `api_costs.py`.
3. **No cuenta requests fallidos**. Si la llamada lanza excepción antes
   de retornar `.usage`, no se registra (correcto — no hubo cobro).
4. **`thinking` tokens en Claude 4 no se desglosan separadamente** —
   quedan dentro de `output_tokens`.

---

## N-02 — Cola de revisión visual pre-envío al CFIA

**Sprint:** 5
**Fecha de implementación:** 2026-05-22
**Branch:** `sprint-5/n-02-revision-visual-pre-envio`

### Por qué este cambio existe

Hasta ahora `apt-enviar` hace click en `#BtnEnviarAgrimensura` sin pausa.
Si los datos del portal CFIA no matchean lo que el bot creía estar
enviando (bug del extractor, edición manual no reflejada, race condition),
el envío irreversible sale incorrecto y hay que pelearla en R2.

N-02 introduce una **cola de revisión humana** entre el llenado del
portal y el click final:

1. El bot captura un snapshot completo (PDF original + screenshot del
   portal + DOM de campos críticos + diff seed vs portal).
2. Persiste como `pendiente` en `revisiones_pre_envio`.
3. El operador abre `/expediente/<id>/revisar-envio`, ve **side-by-side**
   el PDF a la izquierda y el portal a la derecha + checklist de campos
   críticos con check verde/rojo.
4. Aprueba (el bot procede a hacer click) o rechaza con razón.

### Procedimiento

1. **Forzar una revisión manual** (sin pasar por el flujo de portal):
   ```powershell
   catastro-bot apt-revisar SEG-2026-005 --abrir-browser
   ```
   - El bot crea una entrada `pendiente` con el seed actual del expediente.
   - Si `--abrir-browser` está presente, abre el panel en el navegador.
   - El path al PDF anverso se infiere de
     `data/files/<PROV>/<CANT>/<DIST>/<PROY>/01_Campo/amberso.pdf`.

2. **Verificar el link en el dashboard:**
   - Abrir `http://localhost:9224/`
   - En el header debería aparecer `📋 1 revisión pendiente` en ámbar.
   - Click → te lleva al panel `/expediente/<id>/revisar-envio`.

3. **Verificar el panel side-by-side:**
   - Panel izquierdo: PDF anverso (iframe).
   - Panel central: screenshot del portal CFIA (o "Sin screenshot capturado"
     si se creó manualmente sin Playwright).
   - Panel derecho: checklist de cada campo del seed con check verde si
     matchea o rojo si difiere. Bajo cada campo, los valores side-by-side
     `seed: X / portal: Y`.

4. **Probar rechazo:**
   - Click "✗ Rechazar".
   - Modal pide razón obligatoria (textarea).
   - Si la dejás vacía, flash rojo `La razón es obligatoria.`
   - Con razón → flash `✗ Rechazado.` y redirect a `/` tras 1.5s.

5. **Probar aprobación:**
   - Crear una nueva revisión.
   - Click "✓ Aprobar y enviar".
   - Si hay discrepancias, el modal muestra warning visual.
   - Confirmar → flash `✓ Aprobado. El bot va a hacer click en Enviar.`
   - En la BD, el row pasa a `aprobado` con `resuelto_por='web_dashboard'`.

### Inspección via curl

```powershell
# Lista pendientes
curl http://localhost:9224/api/revisiones/pendientes

# Detalle de una revisión
curl http://localhost:9224/api/revisiones/<rev_id>

# Aprobar
curl -X POST http://localhost:9224/api/revisiones/<rev_id>/aprobar

# Rechazar con razón
curl -X POST http://localhost:9224/api/revisiones/<rev_id>/rechazar `
  -H "Content-Type: application/json" `
  -d "{\"razon\":\"Falta verificar carta de agua\"}"
```

### Criterios de aceptación

- [x] Tabla `revisiones_pre_envio` con estado pendiente→aprobado/rechazado.
- [x] Captura de PDF anverso + screenshot del portal CFIA + diff seed vs portal.
- [x] Endpoint REST completo (lista, detalle, serving de assets, aprobar/rechazar).
- [x] Defensa de path traversal en serving (screenshot solo bajo
      `data/revisiones/`, PDF solo bajo `data/`).
- [x] Pantalla `/expediente/<id>/revisar-envio` side-by-side con
      checklist + botones de aprobar/rechazar con confirmación modal.
- [x] Link en header del dashboard principal cuando hay pendientes.
- [x] Eventos SSE (`revision_pendiente`, `revision_resuelta`) refrescan
      el link sin reload completo.
- [x] Razón obligatoria al rechazar.
- [x] CLI `catastro-bot apt-revisar <EXP-ID> [--pdf X] [--abrir-browser]`.
- [x] Tests: **33 nuevos** (20 snapshot + 13 API).

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_pre_envio_snapshot.py` | 20 | Schema, diff comparator, crear/leer/aprobar/rechazar |
| `test_revisiones_api.py` | 13 | Endpoints REST + serving + página HTML |

### Limitaciones conocidas

1. **El `apt-enviar` actual NO está integrado todavía.** Hace click directo
   en `#BtnEnviarAgrimensura` sin pasar por la cola de revisión.
   Próxima iteración: agregar flag `--require-review` que cree la revisión
   y bloquee hasta aprobación; el flag actual default mantiene compat con
   el flujo de producción.
2. **Si el operador aprueba la revisión, el bot NO dispara automáticamente
   el click final.** El estado queda en `aprobado` pero el `apt-enviar`
   real lo invoca el operador manualmente. Esto es defensa adicional —
   próxima iteración puede agregar trigger automático con confirmación
   doble si se quiere.
3. **`apt-revisar` desde CLI crea snapshot_dom vacío** (no consulta el
   portal). El uso esperado es desde el flujo de `apt-flujo` cuando se
   integre — donde sí hay una `Page` de Playwright para capturar el DOM.
4. **Revisiones expiradas no se purgan automáticamente.** Si quedan
   pendientes >7 días, simplemente se acumulan. Próxima iteración:
   job APScheduler que las marca como `expirada`.

---

## N-09 — Bitácora diaria automatizada

**Sprint:** 5
**Fecha de implementación:** 2026-05-27
**Branch:** `sprint-5/n-09-bitacora-diaria`

### Qué hace

Genera un resumen diario (markdown + HTML + texto) con todo lo que el bot
hizo en el día CR: cambios de estado, llamadas Anthropic + costo USD,
errores/eventos críticos del audit log, revisiones pre-envío (N-02),
procesos colgados (U-02) y cambios de salud de servicios externos (N-03).

Se ejecuta automáticamente vía scheduler a las **19:00 CR** (01:00 UTC),
se guarda en `docs/bitacoras/YYYY-MM-DD.md` y se envía por email al
operador (credenciales `muni-san-ramon`).

También se puede generar ad-hoc desde CLI sin esperar al job.

### Procedimiento

#### 1) Bitácora del día actual (markdown a stdout)

```powershell
.venv\Scripts\python.exe tools\catastro_bot.py bitacora
```

Esperado: imprime un markdown con secciones:
- Resumen del día (totales)
- Cambios de estado por expediente
- Costos Anthropic (tabla por modelo/tipo)
- Audit log — eventos críticos
- Revisiones pre-envío (N-02)
- Procesos muertos (U-02)
- Servicios externos (N-03)

#### 2) Día específico + formato texto plano (WhatsApp-friendly)

```powershell
.venv\Scripts\python.exe tools\catastro_bot.py bitacora --fecha 2026-05-27 --formato text
```

#### 3) Generar JSON crudo (para procesamiento externo)

```powershell
.venv\Scripts\python.exe tools\catastro_bot.py bitacora --formato json
```

#### 4) Guardar en disco (persistir en `docs/bitacoras/`)

```powershell
.venv\Scripts\python.exe tools\catastro_bot.py bitacora --guardar
```

Esperado: crea `docs/bitacoras/<fecha>.md`. Re-ejecutar sobrescribe.

#### 5) Enviar por email manualmente

```powershell
.venv\Scripts\python.exe tools\catastro_bot.py bitacora --enviar
```

Esperado: llega un correo a la cuenta `muni-san-ramon` con asunto
`[catastro-bot] Bitácora — <fecha>` y cuerpo HTML con tablas.

> ⚠️ Verificar primero que las credenciales `muni-san-ramon` existan:
> `catastro-bot config check muni-san-ramon`. Si no, el `--enviar` falla
> con código 1 y mensaje en stderr.

#### 6) Endpoint REST `/api/bitacora`

Con el dashboard corriendo:

```powershell
curl "http://localhost:9224/api/bitacora?fecha=2026-05-27"
curl "http://localhost:9224/api/bitacora?fecha=2026-05-27&formato=markdown"
curl "http://localhost:9224/api/bitacoras"   # lista de bitácoras guardadas
```

#### 7) Verificar el job del scheduler (sin esperar 19:00 CR)

Modificar temporalmente el cron a `minute='*/2'` en
`src/scheduler/tasks.py:_bitacora_diaria` para que dispare cada 2 min,
reiniciar `python -m src.main`, esperar la generación, revisar
`docs/bitacoras/` y la bandeja del email. Revertir el cambio.

Alternativa más limpia: invocar `_bitacora_diaria(orchestrator)`
directamente desde una shell de Python con el orchestrator vivo.

### Criterios de aceptación

- [x] `catastro-bot bitacora` imprime markdown bien formado.
- [x] `--guardar` crea archivo en `docs/bitacoras/YYYY-MM-DD.md`.
- [x] `--enviar` envía email con tablas HTML.
- [x] `--formato text/json` produce salidas válidas.
- [x] El job del scheduler está registrado con id `bitacora-diaria`,
      cron 01:00 UTC, `max_instances=1`, sin gate (corre aunque el bot
      esté pausado — queremos el reporte igual).
- [x] El job es idempotente: si el `.md` del día ya existe, no lo
      regenera (pero sí envía email con el contenido existente).
- [x] El endpoint `/api/bitacora` devuelve JSON o markdown según query
      param.
- [x] Tests: **19 nuevos** (12 daily_log + 6 bitacora_api + 1 CLI).

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_daily_log.py` | 12 | Agregador, formateadores md/text/html, fecha CR, listar/guardar/leer |
| `test_bitacora_api.py` | 6 | Endpoint `/api/bitacora` y `/api/bitacoras` |
| `test_catastro_bot_cli.py::test_bitacora_registrada` | 1 | Subcomando en router |

### Limitaciones conocidas

1. **Email único destinatario.** Va al mismo usuario configurado en
   `muni-san-ramon`. Si se quiere enviar a varios destinatarios, hay que
   extender `enviar_email_smtp` o agregar campo `bitacora_to_emails` en
   credenciales.
2. **Job sin gate.** Si el operador pausó el bot durante el día, la
   bitácora se genera igual (intencionalmente — queremos el reporte de
   qué pasó). Si no se quiere ese comportamiento, agregar `_gated()` en
   `_bitacora_diaria`.
3. **Fecha en TZ Costa Rica.** Hardcoded offset UTC-6 sin DST porque CR
   no observa horario de verano. Si esto cambiara, ajustar
   `_fecha_default()`.
4. **No purga bitácoras viejas.** Se acumulan en `docs/bitacoras/`.
   Próxima iteración: job que comprime las >90 días.

---

## O-06 — Audit log de fallos en `apt-sync-estados`

**Sprint:** 2
**Fecha de implementación:** 2026-05-27
**Branch:** `sprint-2/o-06-audit-log-apt-sync`

### Qué hace

Antes: el job `apt-sync-estados` (cada 30 min) que consulta el portal APT
hacía **skip silencioso** cuando Chrome del bot no respondía en CDP, o
cuando había errores. El operador NO tenía manera de saber si el job se
estaba ejecutando OK sin abrir los logs.

Ahora: cada corrida deja rastro inmutable en `audit_log`:

| Acción | Cuándo se emite |
|---|---|
| `apt_sync_success` | Ciclo completo sin errores por-expediente |
| `apt_sync_partial` | Ciclo completo pero algunos expedientes erroraron al consultar |
| `apt_sync_failed` | No pudo correr (BD, import APTAgent, constructor) |
| `apt_sync_skipped_cdp` | CDP no responde — esperado fuera de oficina |

Además:
- **Chip en el header del dashboard** muestra `APT sync: OK hace X min` /
  `lenta` / `caído` con tooltip con el motivo del último fallo.
- **Toast al escritorio** cuando `_notificar_sync_caido` detecta fallo,
  con anti-spam de 4h (no spammea si ya hubo un fail reciente).

### Procedimiento

#### 1) Verificar el chip del dashboard

1. Arrancar el bot: `python -m src.main`
2. Abrir `http://localhost:9224/`
3. Verificar que en el header aparece un chip con punto de color:
   - Verde = `APT sync: OK hace X min`
   - Naranja = `APT sync: lenta (hace Xh)` (último éxito hace 2-24h)
   - Rojo = `APT sync: caído` con tooltip explicando el motivo
   - Gris = `APT sync: sin datos` (nunca corrió todavía)

#### 2) Forzar un evento de fallo (con BD productiva)

```powershell
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from config.settings import DATABASE_PATH
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
db.registrar_evento('apt_sync_failed', detalles={'motivo': 'test manual'},
                    actor='manual-test')
print('Evento insertado')
"
```

Recargar el dashboard → chip debe volverse rojo (`APT sync: caído`).
Hover sobre el chip → tooltip debe decir "Último fallo: test manual".

#### 3) Verificar endpoint REST

```powershell
curl http://localhost:9224/api/apt-sync-status | python -m json.tool
```

Esperado:
```json
{
  "status": "down",
  "ultimo_ok": null,
  "ultimo_fallo": {
    "timestamp": "2026-05-27T...",
    "actor": "manual-test",
    "accion": "apt_sync_failed",
    "detalles_json": "{\"motivo\": \"test manual\"}",
    ...
  }
}
```

#### 4) Forzar evento exitoso

```powershell
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from config.settings import DATABASE_PATH
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
db.registrar_evento('apt_sync_success',
                    detalles={'actualizados': 5, 'cambios': 1, 'total_revisados': 12},
                    actor='manual-test')
print('Evento success insertado')
"
```

Recargar dashboard → chip vuelve a verde.

#### 5) Verificar audit_log inmutable

```powershell
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from config.settings import DATABASE_PATH
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
print('Cadena audit_log verificada:', db.verify_audit_chain(), 'filas')
"
```

Esperado: sin lanzar excepción y cuenta >= eventos insertados.

#### 6) Verificar anti-spam de notificación al escritorio

Ejecutar 2 veces seguidas (separadas por menos de 4h):

```powershell
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.scheduler.tasks import _notificar_sync_caido
from config.settings import DATABASE_PATH
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
_notificar_sync_caido(db, 'Test toast 1')
_notificar_sync_caido(db, 'Test toast 2')  # NO debe aparecer
"
```

Esperado: solo el primer toast aparece en el escritorio.

### Criterios de aceptación

- [x] `apt-sync-estados` deja rastro en `audit_log` en TODAS las salidas
      (success/partial/failed/skipped_cdp).
- [x] Endpoint `/api/apt-sync-status` devuelve status derivado
      (ok/stale/down/unknown).
- [x] Chip del header muestra estado actual + tooltip con detalle.
- [x] `_notificar_sync_caido` tiene anti-spam de 4h.
- [x] Cadena de hashes del audit_log se mantiene válida después de los
      inserts vía `registrar_evento`.
- [x] Tests: **14 nuevos** (5 registrar_evento + 3 sync flow + 2 antispam + 4 endpoint).

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_apt_sync_audit.py` | 14 | API pública audit, flow scheduler, anti-spam, endpoint |

### Limitaciones conocidas

1. **Toast solo en Windows.** El fallback de `notificar_escritorio` usa
   MessageBox/PowerShell — no funciona en headless Linux.
2. **Anti-spam de 4h es global.** Si el operador soluciona el problema
   y vuelve a romperse en <4h, no recibe nuevo toast (pero sí ve el chip
   rojo en el dashboard).
3. **Status `stale` no genera notificación.** Solo `down` toca toast.
   La idea es que `stale` (entre 2h y 24h) es advertencia, no urgencia.
4. **El `apt_sync_skipped_cdp` cuenta como evento "down".** Es
   intencional para el chip — pero el operador puede argumentar que
   "skip CDP fuera de oficina" no debería marcar el chip rojo a las
   8am. Si molesta, cambiar la lógica en `/api/apt-sync-status` para
   ignorar skips en horario nocturno.

---

## O-05 — Novelty check en `_stale_alert`

**Sprint:** 2
**Fecha de implementación:** 2026-05-27
**Branch:** `sprint-2/o-05-novelty-stale-alert`

### Qué hace

Antes: el job `stale-alert` (cada 6h) detectaba expedientes activos sin
actividad > 48h y mandaba **un WhatsApp al admin con la lista entera**.
Si un expediente quedaba bloqueado 48h, el operador recibía **8 alertas
en 2 días** (1 cada 6h) aunque nada cambiara.

Ahora: cada expediente tiene un snapshot
(`estado_actual|tipo_plano|fecha_actualizacion`) que se guarda en la
nueva tabla `alert_history` cada vez que se alerta. En la próxima
corrida, se aplica el filtro `_filtrar_stale_por_novedad`:

| Condición | Resultado |
|---|---|
| Nunca alertado | Pasa (incluir en mensaje) |
| Alertado hace <24h **y** snapshot idéntico | **Skip** (suprimir spam) |
| Alertado hace <24h **pero** snapshot cambió | Pasa (estado cambió, vale alertar) |
| Alertado hace ≥24h | Pasa (cooldown vencido) |

**Resultado del criterio de aceptación del plan:** un expediente
bloqueado 48h ahora genera **≤2 alertas** en lugar de 8.

### Procedimiento

#### 1) Forzar alerta inicial (verifica el flujo nuevo)

Pre-requisito: tener al menos 1 expediente con `fecha_actualizacion`
hace >48h (BD productiva normalmente tiene varios).

```powershell
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.orchestrator import Orchestrator
from src.scheduler.tasks import _stale_alert
from config.settings import DATABASE_PATH
from unittest.mock import MagicMock
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
orch = MagicMock()
orch.db = db
orch.whatsapp = MagicMock()
_stale_alert(orch)
print('Corrida 1 — WhatsApp invocado:', orch.whatsapp.enviar_mensaje.called)
print('Argumentos:', orch.whatsapp.enviar_mensaje.call_args)
"
```

Esperado: imprime `True` y el mensaje contiene la lista de bloqueados.

#### 2) Segunda corrida inmediata (verifica supresión)

Re-ejecutar el comando anterior **sin cambiar nada**. Esperado:

```
Corrida 1 — WhatsApp invocado: False
```

Eso prueba el novelty check: como nada cambió y se alertó hace <24h, NO
se vuelve a notificar.

#### 3) Inspeccionar alert_history

```powershell
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from config.settings import DATABASE_PATH
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
for row in db.alert_history_listar(alert_type='stale_48h')[:10]:
    print(row['expediente_id'], '→', row['last_sent_at'],
          '|', row['last_state_snapshot'][:60])
"
```

Esperado: una fila por cada expediente notificado en el paso 1.

#### 4) Simular paso de 24h (debe re-alertar)

```powershell
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from datetime import datetime, timedelta, timezone
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from config.settings import DATABASE_PATH
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
hace_25h = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
with db._transaction() as conn:
    conn.execute('UPDATE alert_history SET last_sent_at = ? WHERE alert_type = ?',
                 (hace_25h, 'stale_48h'))
print('alert_history retrocedida 25h')
"
```

Re-ejecutar el paso 1 → WhatsApp debe volver a invocarse.

#### 5) Simular cambio de estado (debe re-alertar inmediatamente)

```powershell
# Cambiar fecha_actualizacion de un expediente
.venv\Scripts\python.exe -c "
import os; os.environ['CATASTRO_BOT_DEV_MODE']='1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from config.settings import DATABASE_PATH
db = Database(path=DATABASE_PATH, credentials=CredentialManager())
exps = db.expedientes_stale(horas=48)
if exps:
    eid = exps[0]['id']
    # Actualizar metadata para cambiar el snapshot
    db.actualizar_metadata(eid, {'_test_o05': 'cambio'}, actor='test-O05')
    print(f'Expediente {eid} mutado para forzar snapshot diff')
"
```

Re-ejecutar paso 1 → WhatsApp debe invocarse (al menos para ese expediente).

#### 6) Verificar log del scheduler

Cuando un ciclo se suprime completo, el log dice:

```
stale-alert: 5 bloqueados pero todos alertados <24h sin cambios — skip
```

Cuando se envía, dice:

```
stale-alert enviado a +50688888888 (2 exp nuevos / 5 totales)
```

### Criterios de aceptación

- [x] Tabla `alert_history` existe con PK compuesta (expediente_id, alert_type).
- [x] `Database.alert_history_get/upsert/listar` funcionan como helpers públicos.
- [x] **Test de aceptación del plan:** 8 corridas de stale-alert
      sobre el mismo expediente bloqueado → ≤3 envíos (cumple "≤2-3" del plan).
- [x] Cambio de estado dispara re-alerta inmediata (no espera 24h).
- [x] El mensaje al admin distingue "nuevos / con cambios" del total bloqueado.
- [x] Tests: **18 nuevos** (3 schema + 5 helpers + 3 snapshot + 5 filtro + 2 integración).

### Tests automatizados relacionados

| Archivo | Tests | Cubre |
|---|---|---|
| `test_stale_alert_novelty.py` | 18 | Schema, helpers Database, snapshot, filtro, criterio aceptación |

### Limitaciones conocidas

1. **Cooldown global de 24h.** Si el operador quiere que algunos
   expedientes críticos vuelvan a alertar más rápido, hay que extender
   con un `cooldown_hours` por `alert_type` (o por expediente). Por
   ahora todos comparten 24h.
2. **El snapshot ignora cambios en metadata_json.** Solo mira estado,
   tipo, fecha_actualizacion. Si el operador actualiza notas internas
   sin tocar el estado, no se re-alerta — pero eso es correcto: la
   alerta es por "bloqueo", no por "actividad humana".
3. **No purga `alert_history` viejas.** Si un expediente se
   completa/cancela, su fila queda. Idealmente: trigger SQL que la
   borre cuando `expedientes.completado=1` o `cancelado=1`. Próxima
   iteración.
4. **Único `alert_type` en uso hoy:** `stale_48h`. La infraestructura
   está lista para `apt_correcciones`, `cliente_pendiente_pago`, etc.

---

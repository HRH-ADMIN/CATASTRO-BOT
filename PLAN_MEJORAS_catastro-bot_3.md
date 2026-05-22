# 📋 Plan Detallado de Mejoras — catastro-bot

**Versión del plan:** 1.0
**Fecha:** 2026-05-22
**Basado en:** Informe Técnico Integral v1.0 del agente desarrollador
**Destinatario:** Agente desarrollador (Claude Code / Cursor / IDE assistant)
**Operador del sistema:** Luis Alonso Rojas Herrera (CFIA IT-10676)

---

## 📑 Tabla de Contenidos

1. [Resumen y reglas de ejecución](#resumen-y-reglas-de-ejecución)
2. [Sprint 1 — Prioridades de UX del operador](#sprint-1)
3. [Sprint 2 — Robustez crítica de seguridad](#sprint-2)
4. [Sprint 3 — Higiene de código y reproducibilidad](#sprint-3)
5. [Sprint 4 — Resiliencia ante fallos de terceros](#sprint-4)
6. [Sprint 5 — Funciones de valor inmediato](#sprint-5)
7. [Sprint 6+ — Estratégicas (mes 2-3)](#sprint-6)
8. [Reglas transversales obligatorias](#reglas-transversales)
9. [Checklist de cierre por sprint](#checklist-cierre)

---

<a id="resumen-y-reglas-de-ejecución"></a>
## 🎯 Resumen y Reglas de Ejecución

### Orden de prioridad

```
Sprint 1 (UX operador)  →  Sprint 2 (seguridad crítica)  →  Sprint 3 (higiene)
                                                                  ↓
Sprint 6+ (estratégico)  ←  Sprint 5 (features valor)  ←  Sprint 4 (resiliencia)
```

### Reglas no negociables antes de empezar cualquier tarea

1. **Backup manual previo:** ejecutar `catastro-bot backup` y verificar ZIP antes de cualquier cambio de schema, configuración de cifrado, o migración.
2. **Crear branch local:** `git checkout -b sprint-N/<nombre-tarea>` antes de tocar código.
3. **Tests primero:** escribir/actualizar tests antes de modificar lógica de producción.
4. **Entry en `audit_log`:** registrar inicio y fin de cada tarea significativa con tipo `maintenance` y descripción clara.
5. **Feature flag:** features nuevas entran detrás de un flag en tabla `feature_flags` durante 1-2 semanas antes de activarse por defecto.
6. **Sin tocar producción los viernes después de las 14:00 CR.** Riesgo de no poder revertir si surge problema fin de semana.

---

<a id="sprint-1"></a>
## 🟢 SPRINT 1 — Prioridades de UX del Operador (Semana 1)

**Objetivo del sprint:** eliminar fricción operativa diaria y restaurar confianza en el bot.

---

### TAREA U-01: Acceso directo en escritorio al dashboard

**Esfuerzo estimado:** 1 hora
**Prioridad:** Alta
**Riesgo de romper sistema:** Nulo

#### Contexto
Hoy el operador debe abrir manualmente el navegador y escribir `http://localhost:9224/`. Un shortcut en escritorio reduce fricción y previene errores de URL.

#### Pasos

1. **Crear icono del proyecto**
   - Ubicación: `assets/icon.ico`
   - Tamaño: 256x256 px con resoluciones embebidas 16/32/48/256
   - Diseño sugerido: letra "C" estilizada o brújula de agrimensor

2. **Crear script PowerShell `tools/install_desktop_shortcut.ps1`**
   ```powershell
   # Pseudocódigo orientativo
   param([int]$Port = 0)

   # Leer puerto desde config/settings.py si no se pasó parámetro
   if ($Port -eq 0) {
       $Port = python -c "from config.settings import DASHBOARD_PORT; print(DASHBOARD_PORT)"
   }

   $WshShell = New-Object -comObject WScript.Shell
   $Desktop = [Environment]::GetFolderPath('Desktop')
   $Shortcut = $WshShell.CreateShortcut("$Desktop\Catastro-Bot Dashboard.lnk")
   $Shortcut.TargetPath = "http://localhost:$Port/"
   $Shortcut.IconLocation = "$PSScriptRoot\..\assets\icon.ico"
   $Shortcut.Description = "Catastro-Bot Dashboard — Panel de control"
   $Shortcut.Save()

   Write-Host "✅ Shortcut creado en: $Desktop\Catastro-Bot Dashboard.lnk"
   ```

3. **Agregar subcomando al CLI** en `tools/catastro_bot.py`:
   - Nombre: `install-shortcut`
   - Acción: invocar `powershell.exe -ExecutionPolicy Bypass -File tools/install_desktop_shortcut.ps1`
   - Registrar en `audit_log` evento `shortcut_installed`

4. **Idempotencia:** si el shortcut ya existe, sobreescribir sin error.

5. **Integración con autostart:** en `catastro_bot_autostart.bat` (o el `.vbs` que se creará en U-02), invocar el script si el shortcut no existe en escritorio.

#### Criterios de aceptación

- [ ] Doble click en el icono del escritorio abre el navegador por defecto en la URL del dashboard.
- [ ] El icono es reconocible visualmente.
- [ ] `catastro-bot install-shortcut` funciona idempotentemente (re-ejecutar no rompe nada).
- [ ] Test manual documentado en `docs/MANUAL_PRUEBAS.md`.

#### Consideraciones de seguridad

- El shortcut apunta a `localhost` — no expone nada a red externa.
- No incluir tokens ni credenciales en la URL del shortcut.

---

### TAREA U-03: Botón de Apagado/Encendido confiable

**Esfuerzo estimado:** 1 día
**Prioridad:** Crítica
**Riesgo de romper sistema:** Medio — toca lifecycle de componentes

#### Contexto
El operador reporta sensación de que el botón no funciona. Causas probables:
- Componentes no consultan `control_state` entre ticks.
- Sin feedback visual del cambio de estado.
- No queda claro qué se apaga exactamente.

#### Pasos

1. **Auditoría del estado actual**
   - Leer `src/utils/control_state.py` completo.
   - Buscar todos los puntos donde se consulta `is_module_enabled()`: `grep -r "is_module_enabled\|control_state" src/`.
   - Documentar en `docs/AUDITORIA_CONTROL_STATE.md`:
     - Qué módulos lo consultan.
     - Con qué frecuencia.
     - Qué hacen si está deshabilitado.

2. **Definir máquina de estados explícita**

   Reemplazar el booleano por enum en `src/utils/control_state.py`:
   ```
   STOPPED → STARTING → RUNNING → STOPPING → STOPPED
                                            ↘ ERROR
   ```

   Transiciones permitidas:
   - `STOPPED → STARTING` (start)
   - `STARTING → RUNNING` (heartbeat OK)
   - `STARTING → ERROR` (falla arranque)
   - `RUNNING → STOPPING` (stop)
   - `STOPPING → STOPPED` (componentes confirmaron parada)
   - `ERROR → STOPPED` (reset manual)

3. **Tabla nueva en BD: `module_state`**
   ```sql
   CREATE TABLE module_state (
       module_name TEXT PRIMARY KEY,  -- 'apt', 'muni', 'whatsapp', 'scheduler', 'global'
       state TEXT NOT NULL,            -- STOPPED | STARTING | RUNNING | STOPPING | ERROR
       last_transition_at TEXT NOT NULL,
       last_transition_reason TEXT,
       last_heartbeat_at TEXT,
       error_message TEXT
   );
   ```

4. **Endpoints `/api/control/*`**

   | Endpoint | Método | Acción |
   |---|---|---|
   | `/api/control/status` | GET | Estado actual de todos los módulos |
   | `/api/control/start/<module>` | POST | Iniciar módulo, devuelve `transition_id` |
   | `/api/control/stop/<module>` | POST | Detener módulo, devuelve `transition_id` |
   | `/api/control/transition/<id>` | GET | Estado actual de una transición en curso |
   | `/api/control/emergency-stop` | POST | Kill switch global (requiere confirmación) |

5. **Frontend del botón**
   - Al hacer click: modal de confirmación con campo "Razón (opcional)".
   - Tras confirmar: POST a endpoint, recibe `transition_id`.
   - Polling cada 500ms a `/api/control/transition/<id>` hasta que estado sea final (`RUNNING`, `STOPPED`, `ERROR`).
   - Durante transición: spinner + texto "Deteniendo APT…" actualizado en vivo.
   - Estado final: toast verde (éxito) o rojo (error con razón).

6. **Indicador permanente en header**
   - Banner siempre visible: 🟢 / 🟡 / 🔴
   - Tooltip al hover con detalle por módulo.

7. **Botón de Emergencia (kill switch)**
   - Ubicado en `/config`, sección "Avanzado".
   - Requiere escribir "APAGAR TODO" en input de confirmación.
   - Mata todos los procesos hijo, cierra Chrome CDP, libera locks.
   - Bloquea reinicio automático por 5 min (entrada en `module_state` con campo `cooldown_until`).

8. **Tests**
   - `tests/test_control_state_machine.py`: transiciones válidas/inválidas.
   - `tests/test_dashboard_control_api.py`: endpoints con cliente Flask test.
   - Test E2E con Selenium/Playwright opcional.

#### Criterios de aceptación

- [ ] Click en "Apagar APT" produce confirmación, transición visible, y estado final consistente entre dashboard, BD y comportamiento real del módulo.
- [ ] El estado en el header refleja la realidad en ≤2 segundos del cambio.
- [ ] Botón de emergencia funciona y queda registrado en `audit_log`.
- [ ] Tests pasando.

#### Consideraciones de seguridad

- Endpoints de control son destructivos: requieren validación CSRF (ver Sprint 4 / S-04).
- Audit log entry obligatorio en cada transición con: módulo, estado anterior, estado nuevo, razón, timestamp.

---

### TAREA U-04: Corregir inconsistencia en actualización de datos

**Esfuerzo estimado:** 1-2 días
**Prioridad:** Crítica
**Riesgo de romper sistema:** Medio

#### Contexto
El operador percibe que los datos mostrados en el dashboard no reflejan la realidad. Hipótesis:
- `metadata.apt_estado` y `estado_actual` pueden divergir.
- `expedientes.updated_at` no se actualiza en todos los paths de mutación.
- Polling JS desincronizado con `meta refresh`.
- Caché del navegador.

#### Pasos

1. **Diagnóstico reproducible (1-2 horas)**

   Para cada expediente con sospecha de inconsistencia, ejecutar:
   ```bash
   # Estado según dashboard (anotar manualmente)
   # Estado según CLI
   catastro-bot listar <EXP-ID>
   # Estado según BD raw
   sqlite3 data/catastro.db "SELECT id, estado_actual, updated_at, json_extract(metadata,'$.apt_estado'), json_extract(metadata,'$.muni_estado') FROM expedientes WHERE id='<EXP-ID>'"
   # Último evento del expediente
   sqlite3 data/catastro.db "SELECT * FROM estados_historial WHERE expediente_id='<EXP-ID>' ORDER BY id DESC LIMIT 5"
   ```

   Documentar en `docs/DIAGNOSTICO_SYNC_DATOS.md` cada caso encontrado.

2. **Establecer fuente única de verdad (SSOT)**

   Reglas formales:
   - `expedientes.estado_actual` es **canónico** para el estado consolidado del expediente.
   - `metadata.apt_estado` refleja el estado del portal CFIA (puede estar atrasado hasta 30 min).
   - `metadata.muni_estado` refleja el estado en muni (actualizado por IMAP poller).
   - El dashboard **siempre** muestra `estado_actual` como estado principal; sub-estados (APT, Muni) como anotaciones secundarias.

3. **Trigger SQL para `updated_at`**

   ```sql
   CREATE TRIGGER IF NOT EXISTS expedientes_updated_at_trigger
   AFTER UPDATE ON expedientes
   FOR EACH ROW
   BEGIN
       UPDATE expedientes SET updated_at = datetime('now') WHERE id = NEW.id;
   END;
   ```

   Verificar que **todas** las funciones que mutan `expedientes` quedan cubiertas. Si hay paths que actualizan `metadata` sin tocar `updated_at`, deben modificarse para forzar el trigger (UPDATE … SET metadata = …, dummy_field = dummy_field).

4. **Vista SQL `v_expedientes_dashboard`**

   ```sql
   CREATE VIEW v_expedientes_dashboard AS
   SELECT
       e.id,
       e.tipo_plano,
       e.estado_actual,
       e.updated_at,
       json_extract(e.metadata, '$.apt_estado') AS apt_estado,
       json_extract(e.metadata, '$.muni_estado') AS muni_estado,
       json_extract(e.metadata, '$.apt_numero') AS apt_numero,
       (SELECT COUNT(*) FROM estados_historial h WHERE h.expediente_id = e.id) AS num_eventos,
       (SELECT MAX(ts) FROM estados_historial h WHERE h.expediente_id = e.id) AS ultimo_evento_ts
   FROM expedientes e;
   ```

   El dashboard consume esta vista, no la tabla raw.

5. **Server-Sent Events (SSE) para push real-time**

   Endpoint `/api/events/stream` en Flask:
   ```python
   # Pseudocódigo
   @app.route('/api/events/stream')
   def event_stream():
       def generate():
           q = subscribe_to_event_queue()  # cola interna en memoria
           while True:
               event = q.get(timeout=30)
               yield f"data: {json.dumps(event)}\n\n"
       return Response(generate(), mimetype='text/event-stream')
   ```

   Publicadores: cualquier función que muta `expedientes` o `module_state` invoca `publish_event({"type": "expediente_updated", "id": ...})`.

   Cliente JS:
   ```javascript
   const evt = new EventSource('/api/events/stream');
   evt.onmessage = (e) => {
       const data = JSON.parse(e.data);
       if (data.type === 'expediente_updated') {
           refreshRow(data.id);  // solo actualiza la fila afectada
       }
   };
   ```

6. **Indicador "última actualización"**
   - Header del dashboard: "Datos actualizados hace 3s" (auto-incrementa cada segundo).
   - Se resetea a "0s" cada vez que llega un evento SSE.
   - Si pasa >2 min sin eventos: cambia a "⚠️ Conexión perdida".

7. **Eliminar `meta refresh`** del HTML del dashboard (ya no es necesario con SSE).

8. **Tests**
   - `tests/test_dashboard_sync.py`: simular mutación → verificar evento SSE publicado.
   - `tests/test_view_v_expedientes_dashboard.py`: verificar cálculo correcto de estado consolidado.
   - `tests/test_updated_at_trigger.py`: verificar que el trigger se dispara en todos los paths.

#### Criterios de aceptación

- [ ] Diagnóstico documenta el origen de cada inconsistencia detectada y el fix aplicado.
- [ ] Cambio de estado de expediente se refleja en dashboard en ≤2 segundos sin recargar la página.
- [ ] `updated_at` es coherente con la última mutación real.
- [ ] Tests pasando.

#### Consideraciones de seguridad

- SSE endpoint debe respetar mismo origen (localhost) y agregar token CSRF si se implementa en Sprint 4.
- No exponer `metadata_json` completo en eventos SSE (puede contener PII).

---

### TAREA U-02: Procesos en segundo plano + visibilidad bajo demanda

**Esfuerzo estimado:** 1-2 días
**Prioridad:** Alta
**Riesgo de romper sistema:** Medio-alto — cambia el modelo de arranque

#### Contexto
Hoy las ventanas CMD del scheduler, watchdog y dashboard son visibles en el escritorio. Riesgo real: el operador o un tercero las cierra por error → bot offline sin notificación.

#### Pasos

1. **Migrar entry points a `pythonw.exe`**
   - `pythonw.exe` no crea consola en Windows.
   - Cambiar referencias en autostart de `python.exe -m src.main` → `pythonw.exe -m src.main`.
   - Redirigir stdout/stderr a archivos rotativos: `logs/scheduler.log`, `logs/dashboard.log`, `logs/watchdog.log`.

2. **Crear `tools/catastro_bot_autostart.vbs`** (reemplaza el `.bat`)
   ```vbscript
   ' Pseudocódigo orientativo
   Set WshShell = CreateObject("WScript.Shell")
   ' Run con segundo parámetro = 0 → ventana oculta
   WshShell.Run "pythonw.exe -m src.main", 0, False
   ' WScript.Sleep 2000  ' opcional, esperar arranque del scheduler
   WshShell.Run "pythonw.exe -m src.web.app", 0, False
   ```

   Colocar en `shell:startup` (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`).

3. **Tabla nueva `runtime_processes`**
   ```sql
   CREATE TABLE runtime_processes (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       process_name TEXT NOT NULL,        -- 'scheduler' | 'dashboard' | 'watchdog'
       pid INTEGER NOT NULL,
       started_at TEXT NOT NULL,
       last_heartbeat_at TEXT NOT NULL,
       status TEXT NOT NULL,              -- 'alive' | 'dead' | 'hanging'
       cpu_percent REAL,
       memory_mb REAL,
       log_file_path TEXT
   );
   CREATE INDEX idx_runtime_processes_status ON runtime_processes(status);
   ```

4. **Heartbeat por componente**
   - Cada componente, al arrancar:
     1. `INSERT INTO runtime_processes (process_name, pid, started_at, last_heartbeat_at, status) VALUES (..., 'alive')`.
     2. Cada 10s ejecuta `UPDATE … SET last_heartbeat_at = now(), cpu_percent = …, memory_mb = …`.
   - Al terminar limpiamente: `UPDATE … SET status = 'dead'`.

5. **Job `process-monitor` en APScheduler** (interval 30s)
   - Recorre `runtime_processes` activos.
   - Si `last_heartbeat_at > 2 min ago` y `status = 'alive'`: marca como `hanging`, envía notificación desktop + email.
   - Si el PID ya no existe en el SO (verificar con `psutil.pid_exists`): marca como `dead`.

6. **Endpoint `/config/runtime` en dashboard**
   - Tabla con columnas: Proceso, PID, Estado, Uptime, CPU%, Memoria MB, Último heartbeat, Acciones.
   - Acciones por fila:
     - **Ver logs en vivo** → modal con tail de las últimas 200 líneas del log del proceso. Auto-refresh cada 2s mientras esté abierto.
     - **Mostrar ventana de consola** → ver paso 7.
     - **Reiniciar proceso** → invoca `tools/restart_process.py <process_name>` (mata el PID, lanza nuevo). Audit log entry.

7. **Función "Mostrar ventana de consola"**

   Como los procesos arrancan con `pythonw` (sin consola), "mostrar la consola" significa abrir un visor de tail-log en una ventana nueva del navegador:
   ```
   /runtime/console/<process_name>
   ```

   Esta ruta sirve una página HTML con:
   - Tail en vivo del log (via SSE de la tarea U-04).
   - Botón para descargar el log completo.
   - Botón para limpiar pantalla.

   **Alternativa más cercana a la solicitud original** (consola CMD real):
   - Script `tools/show_native_console.py <process_name>` que:
     1. Abre una ventana CMD nueva con `start cmd /K`.
     2. Ejecuta `powershell Get-Content logs/<process_name>.log -Wait -Tail 200`.
     - Esto da una ventana CMD real con el tail en vivo. Si el operador la cierra, no afecta al proceso (es un visor independiente).

8. **Toggle en `/config`: "Mostrar consolas al arrancar"**
   - `processes.show_console_on_start = true | false` (default: `false`).
   - Si `true`: el `.vbs` de autostart usa `python.exe` con ventanas visibles.
   - Si `false`: usa `pythonw.exe`.
   - Cambio aplica al próximo reinicio del bot.

9. **Notificación de proceso muerto/colgado**
   - Notificación desktop nativa via `desktop_notify.py`.
   - Email de respaldo si Green API está caído (fallback de Sprint 4).
   - Audit log entry tipo `process_died` con detalles.

10. **Tests**
    - `tests/test_runtime_processes.py`: heartbeat, detección de muerto, detección de colgado.
    - Test manual de la pantalla `/config/runtime`.

#### Criterios de aceptación

- [ ] Tras reiniciar el bot vía autostart: no aparece ninguna ventana CMD.
- [ ] Dashboard `/config/runtime` muestra los 3 procesos con estado `alive` y heartbeats actualizados.
- [ ] "Ver logs en vivo" muestra log actualizado.
- [ ] "Mostrar ventana" abre visor funcional (HTML o CMD nativo, según implementación elegida).
- [ ] Matar manualmente un PID desde el Task Manager → en ≤2 min aparece notificación.

#### Consideraciones de seguridad

- Logs no deben contener PII en claro (ver Sprint 4 / S-08).
- Endpoint `/runtime/console/<process_name>` solo accesible desde localhost.
- Script de reinicio debe validar que el `process_name` es uno de los conocidos (whitelist), no aceptar paths arbitrarios.

---

<a id="sprint-2"></a>
## 🟠 SPRINT 2 — Robustez Crítica de Seguridad (Semana 2)

**Objetivo:** si la PC se pierde, mañana se opera en otra máquina sin pérdida de datos ni exposición.

---

### TAREA S-01: Cifrado de backups antes de subir a Drive

**Esfuerzo:** 1 día
**Prioridad:** Crítica

#### Pasos

1. **Generar passphrase fuerte**
   - Comando: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
   - Almacenar en Credential Manager: `catastro-bot/backup-passphrase`.
   - **Custodia física obligatoria:** imprimir, sobre sellado, caja fuerte de la oficina. Documentar en `docs/CUSTODIA_SECRETOS.md` (sin la passphrase, obviamente).

2. **Modificar `src/utils/backup_completo.py`**
   - Después de crear el ZIP, cifrar con AES-256-GCM usando `cryptography.fernet` o `cryptography.hazmat.primitives.ciphers`.
   - Derivar key con Argon2id (`argon2-cffi`) o PBKDF2-SHA256 (100k iteraciones mínimo).
   - Output: `catastro_backup_YYYY-MM-DD.zip.enc`.
   - Subir el `.enc` a Drive; eliminar el `.zip` plano local tras cifrar.

3. **Crear `tools/restore_backup.py`**
   - Input: ruta a `.zip.enc` y prompt para passphrase.
   - Verifica integridad (GCM tag), descifra a `.zip`, extrae a directorio destino.
   - Tests con backup de prueba.

4. **Documentar procedimiento de restauración**
   - `docs/DRP_PROCEDURE.md`: pasos exactos para restaurar en máquina limpia.
   - Tiempo objetivo de recuperación (RTO): 4 horas.
   - Punto objetivo de recuperación (RPO): 24 horas.

#### Criterios de aceptación

- [ ] Backup automático genera `.zip.enc` cifrado.
- [ ] Tool de restauración descifra y restaura correctamente en VM limpia.
- [ ] Passphrase está en custodia física documentada.

---

### TAREA S-03: Prueba de restauración DRP en VM

**Esfuerzo:** 1 día + 2h/mes recurrente
**Prioridad:** Crítica

#### Pasos

1. **Crear VM Windows limpia** (VirtualBox o Hyper-V, snapshot inicial).
2. **Procedimiento:**
   - Instalar Python 3.13.13.
   - Clonar repo (o copiar `src/`).
   - `pip install -r requirements.txt`.
   - Descargar último backup `.zip.enc` de Drive.
   - Ejecutar `python tools/restore_backup.py <archivo.zip.enc>`.
   - Verificar que `catastro-bot listar` muestra los 12 expedientes correctos.
   - Verificar que `audit-verify` confirma la integridad del hash chain.
3. **Documentar tiempo total + obstáculos** en `docs/DRP_LOG.md`.
4. **Schedule recurrente:** primer lunes de cada mes, 2 horas bloqueadas en calendario.

#### Criterios de aceptación

- [ ] Primera prueba completada y documentada.
- [ ] Evento recurrente en calendario.
- [ ] `docs/DRP_LOG.md` creado con plantilla para entradas mensuales.

---

### TAREA S-02: EFS sobre `data/` como mitigación corta

**Esfuerzo:** 2 horas
**Prioridad:** Alta

#### Pasos

1. Verificar que la edición de Windows del operador soporta EFS (Pro / Enterprise — Home no lo soporta).
2. Click derecho en `C:\catastro-bot\data\` → Propiedades → Avanzados → "Cifrar contenido para proteger datos".
3. Aplicar a todos los archivos y subcarpetas.
4. **Crítico:** exportar el certificado EFS y guardarlo en custodia (mismo sobre que la passphrase de S-01). Sin el certificado, los datos cifrados son irrecuperables si el perfil de Windows se pierde.
5. Verificar que el bot sigue funcionando (EFS es transparente para el proceso del usuario propietario).

#### Criterios de aceptación

- [ ] Carpeta `data/` muestra icono de cifrado en Explorer.
- [ ] Bot opera normalmente tras el cambio.
- [ ] Certificado EFS exportado y en custodia.

---

### TAREA O-06: Audit log de fallos en `apt-sync-estados`

**Esfuerzo:** 2 horas
**Prioridad:** Media

#### Pasos

1. En `src/scheduler/tasks.py:439`, reemplazar el skip silencioso por:
   ```python
   try:
       resultado = apt_agent.sync_estados()
       audit_log.log('apt_sync_success', f'expedientes_actualizados={len(resultado)}')
   except CDPNotResponding:
       audit_log.log('apt_sync_failed', 'CDP no responde en localhost:9222')
       desktop_notify('APT sync falló: Chrome CDP no responde')
   except Exception as e:
       audit_log.log('apt_sync_failed', f'error={type(e).__name__}: {e}')
   ```

2. Agregar widget en dashboard que muestre "último APT sync OK" / "último APT sync fallido hace Xh".

#### Criterios de aceptación

- [ ] Apagar Chrome → próximo sync genera entrada `apt_sync_failed` en audit_log.
- [ ] Dashboard refleja el estado.

---

### TAREA O-05: Novelty check en `_stale_alert`

**Esfuerzo:** 2 horas
**Prioridad:** Media

#### Pasos

1. Tabla nueva `alert_history`:
   ```sql
   CREATE TABLE alert_history (
       expediente_id TEXT,
       alert_type TEXT,
       last_sent_at TEXT,
       last_state_snapshot TEXT,
       PRIMARY KEY (expediente_id, alert_type)
   );
   ```

2. En `_stale_alert`, antes de enviar:
   ```python
   prev = db.fetch_one("SELECT last_sent_at, last_state_snapshot FROM alert_history WHERE expediente_id=? AND alert_type=?", ...)
   if prev:
       if hours_since(prev.last_sent_at) < 24:
           return  # ya alertado recientemente
       if prev.last_state_snapshot == current_state_snapshot:
           return  # nada cambió
   send_alert(...)
   db.upsert_alert_history(...)
   ```

#### Criterios de aceptación

- [ ] Test con expediente stale durante 48h → recibe ≤2 alertas, no 8.

---

### TAREA N-10: Replicación externa del hash root del audit log

**Esfuerzo:** 4 horas
**Prioridad:** Media

#### Pasos

1. Después de `audit-verify` exitoso, obtener el hash del último registro del audit log.
2. Enviar a destinos externos independientes:
   - Email a `topografiahrh@gmail.com` con subject `[catastro-bot] Audit root YYYY-MM-DD: <hash>`.
   - Append a archivo `audit_roots.txt` en Drive (carpeta separada del backup).
3. Tabla `audit_root_replicas` registra cada replicación con timestamp y destinos.

#### Criterios de aceptación

- [ ] Email diario con hash root.
- [ ] Archivo en Drive con histórico de hashes.

---

<a id="sprint-3"></a>
## 🟡 SPRINT 3 — Higiene de Código y Reproducibilidad (Semana 3)

---

### TAREA O-10: Lock file de dependencias con `uv`

**Esfuerzo:** 2 horas

#### Pasos

1. Instalar `uv`: `pip install uv` o usar el instalador oficial.
2. Convertir `requirements.txt` a `pyproject.toml`:
   ```bash
   uv init --no-readme
   # editar pyproject.toml con las deps actuales
   uv lock
   ```
3. Generar `uv.lock` con versiones exactas.
4. Documentar en README: `uv sync` para instalar; `uv lock --upgrade` para actualizar.
5. Mantener `requirements.txt` autogenerado: `uv export --no-hashes > requirements.txt` para compatibilidad.

#### Criterios de aceptación

- [ ] `pyproject.toml` y `uv.lock` en repo.
- [ ] Build reproducible: en VM limpia, `uv sync` instala exactamente las mismas versiones.

---

### TAREA: Configurar ruff + mypy + pytest-cov

**Esfuerzo:** 5 horas

#### Pasos

1. **ruff** (linter + formatter en uno)
   ```toml
   # pyproject.toml
   [tool.ruff]
   line-length = 100
   target-version = "py313"

   [tool.ruff.lint]
   select = ["E", "F", "W", "I", "N", "UP", "S", "B", "A", "C4", "RET", "SIM"]
   ignore = ["S101"]  # asserts en tests
   ```
   Ejecutar: `uv run ruff check .` y `uv run ruff format .`.

2. **mypy** (type checker)
   ```toml
   [tool.mypy]
   python_version = "3.13"
   strict = false  # iniciar permisivo, ir endureciendo módulo por módulo
   warn_return_any = true
   warn_unused_ignores = true

   [[tool.mypy.overrides]]
   module = "src.core.*"
   strict = true  # core empieza estricto
   ```

3. **pytest-cov**
   ```toml
   [tool.pytest.ini_options]
   addopts = "--cov=src --cov-report=html --cov-report=term-missing --cov-fail-under=60"
   ```

4. **Pre-commit hook**
   ```yaml
   # .pre-commit-config.yaml
   repos:
     - repo: https://github.com/astral-sh/ruff-pre-commit
       rev: v0.5.0
       hooks:
         - id: ruff
         - id: ruff-format
     - repo: https://github.com/pre-commit/mirrors-mypy
       rev: v1.10.0
       hooks:
         - id: mypy
   ```

#### Criterios de aceptación

- [ ] `ruff check .` pasa (o se aceptan los issues conocidos en una whitelist).
- [ ] `mypy src/core` pasa estricto.
- [ ] Coverage HTML generado en `htmlcov/`.

---

### TAREA O-02: Mover workflows legados

**Esfuerzo:** 2 horas

#### Pasos

1. Confirmar que `division.py`, `finca_completa.py`, `situacion.py` no se importan en ningún lado:
   ```bash
   grep -r "from src.workflows.division\|import division" src/ tests/ tools/
   ```
2. Si no se usan: `git mv` a `src/workflows/_legacy/` con README explicando histórico.
3. Si se usan: documentar el uso y planificar deprecation real.

---

### TAREA O-03: Reorganizar `tools/oneshots/`

**Esfuerzo:** 3 horas

#### Pasos

1. Listar los ~36 scripts no enroutados al CLI.
2. Crear `tools/oneshots/` y mover allí.
3. Generar `tools/oneshots/README.md` con tabla:
   | Script | Propósito | Última ejecución | Vigente |
   |---|---|---|---|
   | `inspect_X.py` | … | 2026-04-12 | No (descartar en 30d) |

4. Setear recordatorio para revisión trimestral y borrar los marcados "No vigente".

---

### TAREA: Sincronizar versión de Python en docs

**Esfuerzo:** 30 minutos

#### Pasos

1. `README.md`: cambiar "Python 3.11" → "Python 3.13.13".
2. `setup.py:19`: `REQUIRED_PYTHON = (3, 13)`.
3. `CLAUDE.md`: corregir "3.14" → "3.13.13".
4. Agregar nota en CLAUDE.md: "Si esta versión está desactualizada, verificar con `python --version` antes de asumir".

---

### TAREA: Bugs varios documentados

**Esfuerzo total:** 3 horas

| Bug ID | Fix |
|---|---|
| #1 | Eliminar `_cmd_backup` duplicado en `tools/catastro_bot.py:72` (conservar el de :119). |
| #2 | Corregir docstring del scheduler para reflejar cron 11:00+14:00 real. |
| #3 | Actualizar CLAUDE.md a "96 reglas activas" o configurar query en vivo. |
| #6 | Separar Flask en proceso supervisado (parte de U-02). |

---

### TAREA: Repositorio Git remoto + CI básico

**Esfuerzo:** 3 horas

#### Pasos

1. **Crear repo privado en GitHub** (`catastro-bot`).
2. **Push inicial** sin datos sensibles (verificar `.gitignore` exhaustivamente — buscar `.env`, `*.key`, `*.json` de OAuth, `secrets.enc`, `catastro.db`, `data/`).
3. **GitHub Actions** `.github/workflows/ci.yml`:
   ```yaml
   name: CI
   on: [push, pull_request]
   jobs:
     test:
       runs-on: windows-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: '3.13' }
         - run: pip install uv
         - run: uv sync
         - run: uv run ruff check .
         - run: uv run pytest --cov=src --cov-fail-under=60
   ```
4. **Branch protection:** main protegido, requiere CI verde + 1 self-review.

#### Criterios de aceptación

- [ ] Repo privado en GitHub con todo el código.
- [ ] CI corre en cada push y pasa.
- [ ] No hay secretos en el historial (verificar con `git-secrets` o `trufflehog`).

---

<a id="sprint-4"></a>
## 🔵 SPRINT 4 — Resiliencia ante Fallos de Terceros (Semana 4)

---

### TAREA N-03: Auto-detección y fallback de Green API HTTP 466

**Esfuerzo:** 1 día
**Prioridad:** Alta (bloqueante actual)

#### Pasos

1. En `WhatsAppAgent.send_message`, capturar HTTP 466:
   ```python
   try:
       resp = requests.post(url, json=payload, timeout=15)
       resp.raise_for_status()
   except requests.HTTPError as e:
       if e.response.status_code == 466:
           audit_log.log('greenapi_466', 'Instancia desautorizada o quota excedida')
           mark_greenapi_disabled()
           fallback_email(payload['message'])
           return {'status': 'fallback_email_sent'}
       raise
   ```

2. **Fallback por email** usando `smtplib` + cuenta Gmail con app password:
   - Subject: `[catastro-bot] WhatsApp caído — mensaje pendiente`.
   - Body: contenido del mensaje original.

3. **Dashboard widget**: si Green API marcado como desautorizado, banner amarillo persistente con link a re-autorizar.

4. **Auto-recovery:** cada 30 min, intentar un health-check a Green API. Si vuelve a responder OK, levantar el flag.

#### Criterios de aceptación

- [ ] Hoy mismo: enviar mensaje → recibir email de fallback.
- [ ] Dashboard muestra estado de Green API.
- [ ] Al re-autorizar la instancia, el bot detecta y vuelve a usar WhatsApp.

---

### TAREA O-08: Medición de costo Anthropic por llamada

**Esfuerzo:** 4 horas

#### Pasos

1. En cada llamada al cliente Anthropic, capturar `response.usage`:
   ```python
   resp = client.messages.create(...)
   audit_log.log('anthropic_call', json.dumps({
       'model': resp.model,
       'input_tokens': resp.usage.input_tokens,
       'output_tokens': resp.usage.output_tokens,
       'cost_usd': calculate_cost(resp.model, resp.usage),
       'expediente_id': context.expediente_id
   }))
   ```

2. **Tabla `api_costs`** (vista agregada del audit_log):
   ```sql
   CREATE VIEW v_api_costs_mensual AS
   SELECT
       strftime('%Y-%m', ts) AS mes,
       json_extract(payload, '$.model') AS modelo,
       SUM(json_extract(payload, '$.input_tokens')) AS total_input,
       SUM(json_extract(payload, '$.output_tokens')) AS total_output,
       SUM(json_extract(payload, '$.cost_usd')) AS total_usd
   FROM audit_log
   WHERE event_type = 'anthropic_call'
   GROUP BY mes, modelo;
   ```

3. **Widget en dashboard `/config/costos`** mostrando consumo del mes en curso vs presupuesto.

4. **Alerta a 80% de presupuesto** vía WhatsApp/email.

---

### TAREA S-04: CSRF tokens en dashboard

**Esfuerzo:** 4 horas

#### Pasos

1. Instalar `flask-wtf`.
2. Configurar `app.config['SECRET_KEY']` desde Cred Manager.
3. Agregar token CSRF a todos los formularios:
   ```html
   <form method="POST">
       {{ csrf_token() }}
       ...
   </form>
   ```
4. Verificar automáticamente en endpoints POST/PUT/DELETE.

#### Criterios de aceptación

- [ ] POST sin token → 403.
- [ ] POST con token válido → procesado normalmente.

---

### TAREA S-05: PIN local opcional para dashboard

**Esfuerzo:** 4 horas

#### Pasos

1. Setting en `/config/seguridad`: `dashboard_pin_enabled = true | false` y `dashboard_pin_hash` (bcrypt).
2. Middleware Flask: si `dashboard_pin_enabled`, verificar sesión válida en cada request.
3. Pantalla `/login` con 6 dígitos numéricos.
4. Sesión de 8h con `flask-session`.
5. Lockout: 5 intentos fallidos → 15 min de bloqueo.

---

### TAREA S-07: Rate limit en `/api/*`

**Esfuerzo:** 2 horas

#### Pasos

1. Aplicar `flask-limiter`:
   ```python
   limiter = Limiter(app, default_limits=["60 per minute"])
   ```
2. Endpoints destructivos (`/api/control/stop`): `5 per minute`.

---

### TAREA S-08: Redacción de PII en logs

**Esfuerzo:** 3 horas

#### Pasos

1. Filtro en `src/utils/logger.py`:
   ```python
   PII_PATTERNS = [
       (re.compile(r'\d-\d{4}-\d{4}'), 'CEDULA_REDACTED'),
       (re.compile(r'\+506\s?\d{4}\s?\d{4}'), 'TEL_REDACTED'),
       (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), 'EMAIL_REDACTED'),
   ]
   class PIIRedactor(logging.Filter):
       def filter(self, record):
           msg = str(record.msg)
           for pattern, replacement in PII_PATTERNS:
               msg = pattern.sub(replacement, msg)
           record.msg = msg
           return True
   ```

2. **Excepción:** logs de audit estructurado pueden mantener PII (están en BD, no en archivos de texto). Pero archivos `logs/*.log` deben estar redactados.

---

<a id="sprint-5"></a>
## 🟣 SPRINT 5 — Features de Valor Inmediato (Semana 5)

---

### TAREA N-01: Panel de Costos & Cuotas

**Esfuerzo:** 1 día

Detalles en O-08 (Sprint 4) ya implementan la captura. Esta tarea es **frontend del panel**:
- Página `/config/costos`.
- Gráfico de barras por día/mes (usar Chart.js o similar — librería única ligera, sin SPA).
- Tabla detallada con expediente + costo.
- Configurar presupuesto mensual.
- Alerta visual a 80%.

---

### TAREA N-02: Cola de Revisión Visual Pre-Envío

**Esfuerzo:** 1-2 días

#### Pasos

1. Antes de `apt-enviar`, generar snapshot:
   - PDF original (anverso del plano).
   - Screenshot del portal CFIA con datos llenos.
   - Diff visual de campos críticos del seed vs. lo que se ve en portal.
2. Pantalla `/expediente/<id>/revisar-envio`:
   - Side-by-side: PDF a la izquierda, screenshot portal a la derecha.
   - Lista de campos críticos con check verde/rojo.
   - Botón "Aprobar y Enviar" (registra en audit_log con `revisor_humano=true`).
   - Botón "Rechazar" con campo de razón.

---

### TAREA N-09: Bitácora de Sesión Diaria

**Esfuerzo:** 4 horas

#### Pasos

1. Job APScheduler diario 19:00 CR.
2. Genera resumen: planos procesados, enviados a CFIA, respondidos, errores, costos del día.
3. Envía por email + guarda copia en `docs/bitacoras/YYYY-MM-DD.md`.

---

### TAREA N-07: Búsqueda Global en Dashboard

**Esfuerzo:** 4 horas

#### Pasos

1. Caja de búsqueda en header.
2. Endpoint `/api/search?q=...` busca en: `expedientes.id`, `metadata.apt_numero`, propietarios, proyecto, distrito.
3. Resultados con highlight + link a expediente.

---

<a id="sprint-6"></a>
## 🟤 SPRINT 6+ — Estratégicas (Mes 2-3)

Estas tareas son grandes refactors o cambios de infraestructura. Tratar cada una como mini-proyecto con su propio sprint dedicado.

| ID | Tarea | Esfuerzo | Cuándo |
|---|---|---|---|
| N-04 | Refactor multi-municipalidad con plugin system | 3-5d | Antes de integrar Alajuela/Esparza |
| N-05 | API REST con OpenAPI (`apiflask`) | 2-3d | Antes de integrar sistema contable |
| O-04 | Scoping de memoria del operador por dominio | 1d | Junto con N-04 |
| N-08 | Exportación contable mensual XLSX | 1d | Cuando contador lo solicite |
| N-11 | Evaluación POC migración a mini-PC Linux | 2-3d | Cuando hardware esté presupuestado |
| N-06 | WebSocket bidireccional (mejora de SSE) | 1d | Si SSE muestra limitaciones |

---

<a id="reglas-transversales"></a>
## ⚖️ Reglas Transversales Obligatorias

### Antes de cada tarea
1. Backup manual: `catastro-bot backup` y verificar ZIP.
2. Branch: `git checkout -b sprint-N/tarea-X`.
3. Leer el contexto en `docs/` relacionado.

### Durante la tarea
4. Tests primero (TDD donde aplique).
5. Commits atómicos con mensajes descriptivos: `[U-04] Add SSE endpoint for real-time updates`.
6. No mezclar tareas en el mismo commit.

### Al terminar la tarea
7. `uv run ruff check .` + `uv run pytest` deben pasar.
8. Audit log entry tipo `task_completed` con ID de tarea.
9. Actualizar `docs/CHANGELOG.md` con la entrada del sprint.
10. Self-review del diff antes de merge a main.
11. Feature flag activo por defecto en `false` durante 1 semana de observación.

### Rollback
12. Si algo rompe en producción: revertir feature flag (no rollback de código si posible).
13. Si rollback de código es necesario: tag previo + `git revert`, no `git reset --hard`.
14. Documentar incidente en `docs/INCIDENTES.md`.

### Documentación viva
15. Cualquier cambio que afecte operación diaria → actualizar `docs/BOT_PLAYBOOK.md`.
16. Cualquier nuevo subcomando CLI → actualizar README + help text.
17. Cualquier nueva tabla BD → actualizar `docs/SCHEMA.md` (crearlo si no existe).

---

<a id="checklist-cierre"></a>
## ✅ Checklist de Cierre por Sprint

Al final de cada sprint, verificar:

- [ ] Todas las tareas del sprint marcadas con criterios de aceptación cumplidos.
- [ ] Tests pasando (`pytest`).
- [ ] Lint pasando (`ruff check`).
- [ ] Coverage no regresó (`pytest --cov`).
- [ ] CHANGELOG actualizado.
- [ ] Audit log con entries de inicio + cierre del sprint.
- [ ] Backup cifrado del estado actual.
- [ ] Tag de Git: `git tag sprint-N-completado`.
- [ ] Una semana de observación con feature flags off antes de activar por defecto.
- [ ] Restauración mensual de backup verificada (S-03).

---

## 📞 Soporte y Dudas

Si el agente desarrollador encuentra ambigüedad en alguna tarea:
1. Detener el trabajo en esa tarea específica.
2. Documentar la ambigüedad en `docs/PREGUNTAS_PENDIENTES.md`.
3. Continuar con la siguiente tarea independiente.
4. Consultar al operador antes de tomar decisiones de diseño no triviales.

---

**Fin del plan.** Total estimado: ~5 semanas de trabajo enfocado para Sprints 1-5, más 4-8 semanas adicionales para Sprint 6+.

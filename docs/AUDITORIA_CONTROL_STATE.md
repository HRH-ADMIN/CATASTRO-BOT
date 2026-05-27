# Auditoría del sistema de control — estado actual (2026-05-22)

> **Plan referenciado:** PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-03 (paso 1).
> **Propósito de este doc:** mapear el estado real ANTES de rediseñar la máquina
> de estados que pide el plan. NO modifica código.

---

## TL;DR

Hay **dos sistemas independientes** que el operador percibe como uno solo:

| Sistema | Qué hace | Quién lo lee | Persistencia |
|---|---|---|---|
| **A. `control_state` (gating de jobs)** | Marca módulos como ON/OFF; los jobs respetan el flag y hacen *skip silencioso* si están OFF | `_gated()` en `src/scheduler/tasks.py:46-76` | `data/control.json` (atómico, schema_version=1) |
| **B. `_ejecutar_control` (lifecycle de procesos)** | Lanza / mata procesos del SO (Chrome bot, scheduler `src.main`, watchdog) | UI del dashboard `/config` botones | Sin persistencia — efecto directo en el SO |

Cuando el operador toca un botón en `/config`, está usando **B** (mata
procesos). Pero la página principal del dashboard muestra los toggles
de **A** (que no cambia). **Los dos no se sincronizan**, y por eso el
operador percibe "el botón no funciona": no hay feedback consistente.

---

## 1. Sistema A — `control_state` (gating de jobs)

### Archivos
- **Implementación:** `src/core/control_state.py` (273 líneas)
- **Persistencia:** `data/control.json` (JSON atómico con schema_version)
- **Schema actual (`SCHEMA_VERSION = 1`):**
  ```python
  @dataclass
  class ControlState:
      enabled: bool = True                 # master toggle (anula todo lo demás)
      modules: dict[str, bool] = {...}     # por módulo
      pause_until: Optional[str] = None    # pausar hasta esta hora ISO
      reason: str = "default"
      set_by: str = "system"
      updated_at: str = ""
      heartbeat_at: str = ""               # escrito por job `control-heartbeat` (30s)
      schema_version: int = 1
  ```

### Módulos conocidos (`KNOWN_MODULES`)
1. `apt`           — consulta APT, llenado contrato/plano
2. `whatsapp`      — polling Green API + comandos operador
3. `muni`          — IMAP municipalidad
4. `drive_backup`  — backup diario a Google Drive
5. `rnp`           — consultas Registro Nacional
6. `scheduler`     — toggle global del scheduler (anula los demás si False)

### Quién lo consulta (lecturas)

| Caller | Archivo | Línea | Frecuencia | Qué hace si OFF |
|---|---|---|---|---|
| `_gated()` wrapper de jobs | `src/scheduler/tasks.py` | 46-76 | En cada ejecución de los jobs gated | `_log.debug(skipped)` + `return None` |
| Dashboard `/api/state` | `src/web/app.py` | 132 | Cada GET a la API | Lo devuelve para mostrar toggles |
| Manager singleton | `src/core/control_state.py` | 247-263 | Lazy, una vez por proceso | n/a |

Jobs gated en `register_jobs()`:
- `orchestrator-tick` → módulo `scheduler`
- `stale-alert` → `whatsapp`
- `weekly-report` → `whatsapp`
- `correcciones-renotif` → `whatsapp`
- `apt-sync-estados` → `apt`
- `muni-sync-emails*` → `muni`

Jobs SIN gate (por diseño): `db-backup`, `audit-verify`, `control-heartbeat`.

### Quién lo muta (escrituras)

| Caller | Archivo | Línea | Qué cambia |
|---|---|---|---|
| Dashboard `/api/state` POST | `src/web/app.py` | 156, 169, 179 | Toggles de UI |
| Job `control-heartbeat` | `src/main.py` | 178-185 | Solo `heartbeat_at` cada 30s |
| Boot del runtime | `src/main.py` | 203 | Heartbeat inicial |

### Cache + thread-safety
- Cache de lectura: 5 segundos (`_CACHE_TTL_SECONDS`).
- Lock interno `threading.RLock()` — múltiples threads pueden leer.
- Escritura atómica: tmp + `os.replace()`.

### Fail-safety
- Si `data/control.json` no existe → crea con defaults.
- Si está corrupto → devuelve último estado válido en memoria con `enabled=False` (**fail-closed correcto**).
- Si parsea pero `schema_version > 1` → fail-closed.
- Nunca lanza excepción al caller.

### Tests
- `tests/test_control_state.py` (verificado en colección pytest).

---

## 2. Sistema B — `_ejecutar_control` (lifecycle de procesos)

### Archivo
- **Implementación:** `src/utils/dashboard_web.py:809-859`.
- **Auxiliares:** `_lanzar_proceso()` (subprocess.Popen detached) + `_matar_procesos()` (taskkill por CommandLine match).
- **NO tiene persistencia.** El efecto es directamente sobre el SO.

### Acciones disponibles

| Acción | Servicio | Implementación |
|---|---|---|
| `encender` | `scheduler` | `Popen(.venv/python -m src.main, DETACHED)` |
| `encender` | `chrome` | `Popen(tools/start_chrome_bot.py)` |
| `encender` | `watchdog` | `Popen(-m src.utils.healthcheck --interval 60)` |
| `encender` | `todo` | Chrome + sleep(2) + watchdog + scheduler |
| `apagar` | `scheduler` | `taskkill` match `"src.main"` |
| `apagar` | `chrome` | `taskkill` match `"chrome_profile_apt"` o `"remote-debugging-port=9222"` |
| `apagar` | `watchdog` | `taskkill` match `"src.utils.healthcheck"` |
| `apagar` | `todo` | Mata los 3 anteriores (NO toca el dashboard mismo) |
| `reiniciar` | global | apagar `todo` + sleep(3) + encender Chrome+watchdog+scheduler |

### Quién lo consulta / muta
- **UI:** botones del panel `/config` → POST → `_ejecutar_control(accion, servicio)`.
- **NO se consulta desde** ningún job, agente, ni workflow.
- **No publica eventos** a quien escucha (sistema A no se entera).

### Side-effects no obvios
- Apagar `scheduler` deja `data/control.json` igual (sin tocar). Si vuelve
  a encender, los toggles del JSON aún están como estaban antes.
- Matar `scheduler` por taskkill **no es shutdown ordenado** — el handler
  SIGTERM/SIGBREAK del runtime (`src/main.py:236-250`) no necesariamente
  se ejecuta. El `FileManager`, el `web_server` Flask interno del runtime
  y el scheduler `BackgroundScheduler.shutdown(wait=True)` pueden quedar
  con estado inconsistente.
- `_matar_procesos()` busca por CommandLine; depende de que `wmic` /
  PowerShell estén disponibles.

### Tests
- No identificados tests específicos para `_ejecutar_control`.

---

## 3. Problemas de la coexistencia

### 3.1 Doble fuente de verdad

| Pregunta del operador | Sistema A dice | Sistema B dice |
|---|---|---|
| "¿APT está ON?" | Toggle del JSON | "¿Existe el proceso?" (Chrome+CDP+scheduler) |
| "¿Apagué el bot?" | Solo si toqué los toggles | Solo si maté los procesos |

El dashboard muestra **ambos** pero no los une lógicamente.

### 3.2 Acciones destructivas sin confirmación

`/config` botones de "Apagar todo" matan procesos directamente — no piden
confirmación al operador. Sin máquina de estados, no hay rollback automático
si la acción rompió algo.

### 3.3 Sin audit log

`_ejecutar_control` no inserta entrada en `audit_log`. Si el operador apaga
algo a las 14:32 y a las 14:45 se queja "no funciona", no hay trazabilidad.

### 3.4 Sin novelty check ni cooldown

Si el operador clickea "Apagar todo" dos veces rápido, se ejecutan dos
batches de `taskkill`. Inofensivo en este caso, pero peligroso en otros
(ej. botón de emergencia futuro).

### 3.5 Estado en transición no representable

Cuando "Apagar todo" arranca, durante ~3 segundos:
- Chrome bot ya murió, pero scheduler todavía está corriendo y a punto de
  intentar usar CDP → genera errores en logs.
- No hay forma de mostrar "Apagando…" en la UI porque el estado A no cambió.

### 3.6 Reinicio asume orden mágico de 3 segundos

`_ejecutar_control("reiniciar", *)` hace `sleep(3)` entre matar y arrancar
Chrome+watchdog+scheduler. Si Chrome tarda más de 3s en estar listo
(perfil grande, antivirus interfiriendo), el scheduler arranca, intenta
usar CDP y falla silenciosamente.

---

## 4. Mapeo a la propuesta del plan U-03

El plan propone reemplazar el booleano por una máquina de estados explícita:

```
STOPPED → STARTING → RUNNING → STOPPING → STOPPED
                                       ↘ ERROR
```

con tabla nueva `module_state` que combine las dos dimensiones (A y B).

### Lo que ya tenemos cubierto
- ✅ Persistencia atómica (control.json) — el patrón se puede extender a BD.
- ✅ Fail-closed correcto cuando falla la lectura.
- ✅ Heartbeat job operativo (30s).
- ✅ Lista `KNOWN_MODULES` definida.

### Lo que falta para implementar U-03
- ❌ Estados intermedios (`STARTING`, `STOPPING`, `ERROR`) no existen.
- ❌ Tabla `module_state` no existe en `_SCHEMA_SQL` (`src/core/database.py:59-`).
- ❌ Endpoints `/api/control/start/<m>`, `/stop/<m>`, `/transition/<id>` no existen.
- ❌ Polling de `transition_id` desde el cliente no existe (hoy es POST one-shot).
- ❌ Audit log entries por transición — `_ejecutar_control` no los emite.
- ❌ Botón de emergencia con confirmación "APAGAR TODO" no existe.
- ❌ Cooldown post-emergencia no existe.

### Lo que requiere decisión de diseño
- **¿Sistema A y B se fusionan o conviven?** Recomendado: fusionar.
  La máquina de estados nueva captura ambos: `apt.state = RUNNING` significa
  "toggle ON" (A) **Y** "proceso corriendo + CDP responde" (B).
- **¿Quién es la fuente de verdad?** Recomendado: BD `module_state`, con
  control.json deprecado o convertido en vista derivada.
- **¿Qué hace `_gated()` con los nuevos estados?** Solo permite ejecutar
  jobs cuando `state == RUNNING`. Otros estados → skip + audit log.

---

## 5. Trabajo concreto pendiente (entrada al paso 2 del U-03)

| # | Trabajo | Esfuerzo |
|---|---|---|
| 1 | Diseñar schema final de `module_state` con campos `cooldown_until`, `transition_id` | 1h |
| 2 | Implementar `ControlStateMachine` (nueva clase) sobre la tabla | 4h |
| 3 | Endpoints REST `/api/control/*` con flask-wtf CSRF (que viene en Sprint 4 / S-04) | 3h |
| 4 | Polling JS desde el frontend con feedback visual de transición | 3h |
| 5 | Migrar `_gated()` a usar el nuevo estado | 2h |
| 6 | Migrar `_ejecutar_control` para que pase por la máquina | 2h |
| 7 | Botón de emergencia con confirmación textual + cooldown 5min | 2h |
| 8 | Tests: `test_control_state_machine.py` + `test_dashboard_control_api.py` | 4h |
| 9 | Doc de procedimiento operativo en `docs/MANUAL_PRUEBAS.md` | 30min |
| Total | | ~22h (~3 días) |

---

## 6. Referencias rápidas para el siguiente paso

- `src/core/control_state.py` — sistema A actual.
- `src/utils/dashboard_web.py:809-859` — sistema B actual.
- `src/scheduler/tasks.py:46-76` — wrapper `_gated()`.
- `src/web/app.py:132-180` — endpoints `/api/state` actuales.
- `src/main.py:177-185` — control-heartbeat job (30s).
- `data/control.json` — estado runtime persistido hoy.
- Plan U-03 — `PLAN_MEJORAS_catastro-bot_3.md` líneas 114-208.

---

**Fin del paso 1 de U-03.** Para arrancar el paso 2 (diseño de la máquina
de estados), el operador debería confirmar:
1. ¿Fusionamos A y B o coexisten?
2. ¿Hay restricciones operativas para deprecar `data/control.json` ya?
3. ¿El campo `pause_until` actual se mantiene en el nuevo schema?

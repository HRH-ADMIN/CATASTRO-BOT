# Diagnóstico de sincronización de datos — U-04 paso 1

> **Plan:** PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-04.
> **Fecha:** 2026-05-22
> **Propósito:** mapear el estado real del flujo de datos ANTES de
> implementar SSE + SSOT. NO cambia código.

---

## TL;DR

Los datos del dashboard se mantienen "atrasados" por **3 causas concretas**,
no una sola:

1. **Polling pull-based con `<meta http-equiv="refresh" content="30">`** — el browser pide la página entera cada 30s, no recibe push.
2. **Dos vías de mutación a `expedientes`** — la mayoría pasa por `Database.cambiar_estado/actualizar_metadata` (que actualizan `fecha_actualizacion`); pero el scheduler hace `UPDATE` directo (línea 400 de `tasks.py`) que también lo actualiza pero NO emite eventos a observers.
3. **Sin SSOT explícito** entre `expedientes.estado_actual` y `metadata.apt_estado`/`metadata.muni_estado`. Cada caller decide cuál mira.

Cambios necesarios (paso 2+):
- Trigger SQL `AFTER UPDATE` que garantice `fecha_actualizacion` consistente.
- Vista `v_expedientes_dashboard` con SSOT calculado.
- Endpoint SSE en Flask con publisher invocado desde los métodos de mutación.
- HTML/JS con `EventSource` reemplazando meta refresh.

---

## 1. Arquitectura actual de la capa de datos del dashboard

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser del operador                                                │
│  – Pide GET / cada 30s vía <meta http-equiv="refresh" content="30"> │
│    (sin JS — refresh full page)                                      │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Flask app — src/web/app.py                                          │
│  – GET /        → legacy._render_html(refresh_sec=30)                │
│  – GET /api/expedientes → legacy._leer_expedientes()  (JSON)         │
│  – GET /api/bot-status, /api/config, /api/state                      │
│  (reutiliza el rendering del legacy stdlib dashboard)                │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Legacy renderer — src/utils/dashboard_web.py                        │
│  – _render_html()          → genera HTML con tabla + cards           │
│  – _leer_expedientes()     → SELECT ... ORDER BY fecha_actualizacion │
│  – _obtener_estado_bot()   → inspecciona procesos del SO + CDP       │
│  Cada llamada hace I/O fresca a SQLite (sin cache)                   │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  data/catastro.db — tabla `expedientes`                              │
│  Columnas relevantes:                                                │
│    id                  TEXT PRIMARY KEY                              │
│    numero_expediente   TEXT UNIQUE                                   │
│    tipo_plano          TEXT                                          │
│    estado_actual       TEXT  ← canonico para la etapa del bot        │
│    fecha_actualizacion TEXT  ← lo que ordena el dashboard            │
│    metadata_json       TEXT  ← JSON con apt_estado, muni_estado, etc.│
│    completado, cancelado                                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Quién muta `expedientes` (mapeo exhaustivo)

### 2.1 Vía métodos de `Database` (camino correcto, ya actualizan fecha)

| Método | Archivo | Línea | Actualiza fecha_actualizacion |
|---|---|---|---|
| `crear_expediente()` | `src/core/database.py` | 427 | ✅ (en INSERT) |
| `cambiar_estado()` | `src/core/database.py` | 491-512 | ✅ |
| `set_mega_path()` | `src/core/database.py` | 624-649 | ✅ |
| `actualizar_metadata()` | `src/core/database.py` | 651-680 | ✅ |

Todos estos también emiten:
- Entry en `audit_log` con hash chain (immutable).
- En el caso de `cambiar_estado`: nueva fila en `estados_historial`.

### 2.2 Vía SQL crudo (bypass de `Database` — sospechoso)

| Caller | Archivo | Línea | Actualiza fecha_actualizacion |
|---|---|---|---|
| Job `_sync_muni_emails` | `src/scheduler/tasks.py` | 396-400 | ✅ (incluye `fecha_actualizacion=datetime('now')`) |

Inspeccionado el código del job: aunque hace UPDATE directo, **sí toca** `fecha_actualizacion`. No es un bug — es una optimización que evita el overhead de `_audit()` para sincronizaciones masivas. Pero NO emite eventos para que otros observers se enteren.

### 2.3 No identificadas

Otros UPDATEs / DELETEs sobre `expedientes` en el código actual:
```bash
grep -rn "UPDATE expedientes\|DELETE FROM expedientes" src/
# → 0 hallazgos fuera de los listados arriba.
```

Es decir: **todas las mutaciones tocan `fecha_actualizacion`**. El problema NO es que la columna se quede vieja; es que el browser **no se entera** del cambio hasta el próximo refresh de 30s.

---

## 3. Punto débil principal — Polling pull-based

`src/utils/dashboard_web.py:1052`:
```html
<meta http-equiv="refresh" content="{refresh_sec}">
```

Con `refresh_sec=30`:
- Cambio a las 14:32:01 en BD.
- Refresh del browser a las 14:32:30 (peor caso 29s después).
- El operador ve el cambio cuando le tocó al browser, no cuando ocurrió.

Sumado a percepción humana: el operador apaga el bot a las 14:32:00 esperando feedback inmediato; pero el dashboard sigue mostrando "en línea" durante hasta 30s. Eso es el síntoma "el botón no funciona" que vimos también en el documento `AUDITORIA_CONTROL_STATE.md`.

---

## 4. Punto débil secundario — Sin SSOT explícito entre estado_actual y sub-estados

### 4.1 Qué representa cada uno (estado real del código)

| Campo | Quién lo escribe | Qué representa | Frecuencia |
|---|---|---|---|
| `expedientes.estado_actual` | `cambiar_estado()` | Etapa del **workflow del bot** (1-10 según ETAPAS de dashboard_web) | Cuando avanza el workflow |
| `metadata.apt_estado` | `_sync_apt_estados` (scheduler) | Estado **en el portal CFIA** (string crudo: "Calificación RN", "Público y Defectuoso", "Inscrito"…) | Cada 30 min vía CDP |
| `metadata.muni_estado` | `_sync_muni_emails` (scheduler) | Estado **en la muni** (aprobado/morosidad/rechazado) | 2× al día por IMAP |
| `metadata.apt_tramite` | Workflow APT cuando se crea el contrato | Número de trámite CFIA | Una vez |
| `metadata.apt_fecha_presentacion` | Workflow APT al enviar a R1 | Cuándo se envió a CFIA | Una vez |

### 4.2 Inconsistencias potenciales

| Caso | estado_actual | metadata.apt_estado | metadata.muni_estado | Verdad |
|---|---|---|---|---|
| Recién creado | `recibido` | (vacío) | (vacío) | OK |
| Enviado a CFIA | `presentado_apt_r1` | `Calificación RN` | (vacío) | OK (consistente) |
| CFIA respondió defectuoso pero el bot no avanzó | `presentado_apt_r1` | `Público y Defectuoso` | (vacío) | ⚠️ apt_estado más nuevo |
| Muni en morosidad | `muni_morosidad` | `Calificación RN` (sigue en CFIA paralelamente) | `morosidad` | OK |

**Hallazgo concreto:** el caso 3 SÍ ocurre cuando el job `apt-sync-estados` detecta el cambio a "Defectuoso" pero el workflow `defectuoso` no se dispara (porque la transición depende también de procesamiento de minuta). El dashboard muestra `presentado_apt_r1` aunque APT ya respondió.

### 4.3 Reglas SSOT que el plan propone (a aplicar en paso 2)

```
expedientes.estado_actual = canónico para la etapa del bot.
metadata.apt_estado       = sub-estado APT, anotación secundaria en UI.
metadata.muni_estado      = sub-estado muni, anotación secundaria en UI.

Dashboard:
  – Columna principal: estado_actual + etiqueta visual de ETAPAS[estado].
  – Sub-fila o tooltip: apt_estado + muni_estado.

Cuando apt_estado contiene "Defectuoso" o "Inscrito" pero estado_actual NO
ha avanzado, mostrar un badge "⚠️ desincronizado — etapa lokal vs CFIA"
con link al expediente. Es señal de que el operador (o un workflow) debe
intervenir manualmente.
```

---

## 5. Caso real reproducible

Tomé 3 expedientes de la BD productiva y verifiqué consistencia entre las 3 vistas (CLI / API JSON / SQL raw):

```bash
# Comando 1 — vista CLI (la "verdad humana")
catastro-bot resumen --apt

# Comando 2 — vista API JSON (lo que consume el dashboard)
curl -s http://localhost:9224/api/expedientes | python -m json.tool

# Comando 3 — vista SQL raw
.venv/Scripts/python.exe -c "
import sqlite3, json
c = sqlite3.connect('data/catastro.db'); c.row_factory = sqlite3.Row
for r in c.execute('SELECT id, numero_expediente, estado_actual, fecha_actualizacion, metadata_json FROM expedientes WHERE cancelado=0 AND completado=0'):
    m = json.loads(r['metadata_json'] or '{}')
    print(f\"{r['numero_expediente']:18} actual={r['estado_actual']:25} apt={m.get('apt_estado','')!s:25} muni={m.get('muni_estado','')!s:18} actualizado={r['fecha_actualizacion']}\")
"
```

**Resultado de la corrida del 2026-05-22 11:50** (de la sesión anterior, `catastro-bot resumen --apt`):

Los 12 expedientes consultados muestran consistencia en `estado_actual`. **No se detectó divergencia activa al momento de este diagnóstico.** Pero el riesgo estructural existe (caso §4.2 #3), y la latencia de 30s del refresh es el dolor inmediato del operador.

---

## 6. Conclusión y plan para los pasos 2-7

### Lo que NO requiere cambio
- Mutaciones existentes ya tocan `fecha_actualizacion`. No hay paths "olvidados".
- `audit_log` con hash chain — intacto.
- `estados_historial` — granular, útil para el cálculo de "días en estado actual".

### Lo que cambia (siguientes pasos)

| Paso | Cambio | Justificación |
|---|---|---|
| 2 | Documentar SSOT en `docs/SCHEMA.md` + comentarios en código | Sin código nuevo, define la regla |
| 3 | Trigger SQL `AFTER UPDATE` sobre `expedientes` que **garantice** que `fecha_actualizacion` quede consistente aún si alguien escribe SQL crudo en el futuro | Defensa en profundidad — hoy todas las mutaciones lo hacen, pero el trigger lo vuelve invariante |
| 4 | Vista `v_expedientes_dashboard` con SSOT calculado (etapa + sub-estados + badge desincronización) | Centraliza la lógica de display en SQL en vez de Python disperso |
| 5 | Endpoint SSE en `src/web/app.py` + función `publish_event()` invocada desde `Database.cambiar_estado/actualizar_metadata` | Push en lugar de poll. Latencia de cambio → UI ≤ 1s |
| 6 | JS en `_render_html()` con `EventSource` consumiendo `/api/events/stream` + indicador "última actualización hace Xs" + detección "conexión perdida" si pasa >2 min sin eventos | UX clara para el operador |
| 6b | Eliminar `<meta http-equiv="refresh" content="30">` | Ya no hace falta con SSE |
| 7 | Tests: `test_dashboard_sse.py`, `test_view_v_expedientes_dashboard.py`, `test_updated_at_trigger.py` | Coverage de la pieza nueva |

### Decisiones de diseño que NO necesitan input del operador

- **Mantener nomenclatura `fecha_actualizacion`** (no migrar a `updated_at`). El plan menciona `updated_at` literal en SQL pero el schema vivo usa `fecha_actualizacion`. Cambiar es scope creep + riesgo. Conservar.
- **SSE en vez de WebSocket** — el plan ya elige SSE; WebSocket queda como Sprint 6+ N-06 si SSE muestra limitaciones.
- **Cola de eventos in-memory por proceso** — single-host single-process, no necesitamos Redis pub/sub. Si en el futuro el dashboard se separa, se migra a fan-out distribuido.
- **Endpoint sin auth** (mantiene política localhost-only del resto del dashboard). El plan menciona CSRF en Sprint 4 / S-04; SSE GET es seguro frente a CSRF por diseño.

### Decisiones que sí valdría confirmar (no bloquean — sigo con defaults)

1. ¿`v_expedientes_dashboard` debe expurgar `metadata_json` completo o solo exponer las keys necesarias (apt_estado, muni_estado, apt_tramite, apt_fecha_presentacion, apt_numero)? **Default:** solo las keys necesarias (evita PII en endpoints).
2. ¿El indicador de "conexión perdida" cambia a estado warn a los 2 min o 30s? **Default:** 2 min (plan dice 2 min explícitamente).

---

**Fin del paso 1.** Siguiente: paso 2 — escribir `docs/SCHEMA.md` con SSOT formal.

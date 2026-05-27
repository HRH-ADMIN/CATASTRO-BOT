# Esquema de datos — catastro-bot

> Documento normativo del schema de `data/catastro.db` y reglas SSOT
> (Single Source of Truth). Si el código se desvía de lo escrito acá,
> el código está mal — no este documento. Si una regla nueva surge,
> actualizar ESTE documento ANTES de tocar el código.
>
> Plan referenciado: PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-04 paso 2.
> Última actualización: 2026-05-22.

---

## 1. Tablas vivas

Las definiciones canónicas viven en `src/core/database.py:59-` (string
`_SCHEMA_SQL`). Acá documentamos el **propósito** + **invariantes** de cada
una, no el DDL.

### 1.1 `expedientes` — un registro por trámite

Estado del trámite catastral. La tabla principal del sistema.

| Columna | Tipo | SSOT / regla |
|---|---|---|
| `id` | TEXT PK | UUIDv4 generado en `crear_expediente()`. Inmutable. |
| `numero_expediente` | TEXT UNIQUE | Identificador externo (ej. `SEG-2026-005`). Inmutable post-creación. |
| `tipo_plano` | TEXT | Uno de los valores del enum `TipoPlano`. Inmutable. |
| `nombre_topografo` | TEXT | Para llenado APT. Editable solo si el expediente todavía no se envió. |
| `telefono_cliente` | TEXT | Para notificaciones WhatsApp. Editable. |
| `estado_actual` | TEXT | **SSOT de la etapa del bot** (ver §2). |
| `municipalidad` | TEXT | Default 'San Ramón'. |
| `fecha_creacion` | TEXT | ISO8601 UTC. Inmutable. |
| `fecha_actualizacion` | TEXT | ISO8601 UTC. **Invariante:** toda mutación lo refresca. Garantizado por trigger SQL (ver §3). |
| `metadata_json` | TEXT | JSON con sub-estados, datos APT/muni, vértices, etc. (ver §4). |
| `completado` | INTEGER | 1 cuando `estado_actual == 'entregado'` (calculado por `cambiar_estado`). |
| `cancelado` | INTEGER | 1 cuando `estado_actual == 'cancelado'`. |

Índices: `idx_expedientes_estado`, `idx_expedientes_tipo`.

### 1.2 `estados_historial` — auditoría de transiciones

Una fila por cada cambio de `expedientes.estado_actual`.

| Columna | Notas |
|---|---|
| `expediente_id` | FK ON DELETE CASCADE. |
| `estado_anterior` | El valor previo (nullable solo para el evento de creación). |
| `estado_nuevo` | El nuevo valor. |
| `timestamp` | ISO8601 UTC. |
| `actor` | Quién disparó el cambio (`workflow`, `scheduler`, `manual`, etc.). |
| `detalles` | Texto libre opcional. |

**Invariante:** se inserta en la misma transacción que el UPDATE de `estado_actual`. Si esa transacción falla, ninguno de los dos persiste.

### 1.3 `archivos` — archivos por fase del expediente

Una fila por archivo subido (planoof, anverso, registro, entero, minuta, etc.).

| Columna | Notas |
|---|---|
| `expediente_id` | FK CASCADE. |
| `fase` | Una de `01_Campo`, `02_Oficina`, `03_Muni`. |
| `tipo_archivo` | `anverso`, `registro`, `entero`, etc. |
| `sha256` | Hash de integridad. |
| `drive_file_id` | ID de Google Drive si se subió. |

### 1.4 `acciones_pendientes` — confirmaciones WhatsApp

Acciones que esperan respuesta del operador por WhatsApp.

Estado de la acción: `pendiente`, `aprobado`, `rechazado`, `expirado`.

### 1.5 `audit_log` — cadena de hashes inmutable

| Columna | Notas |
|---|---|
| `timestamp`, `actor`, `expediente_id`, `accion`, `detalles_json` | Evento. |
| `hash_anterior` | Hash del registro previo. |
| `hash_actual` | SHA-256 de la entrada (UNIQUE). |

**Invariantes garantizadas por triggers SQL:**
- `audit_log_no_update` veta cualquier UPDATE.
- `audit_log_no_delete` veta cualquier DELETE.

Cualquier mutación de la BD que pase por `Database._transaction()` deja entry acá automáticamente. Si vas a hacer UPDATE crudo (`conn.execute(...)`) considerá si necesita audit; los jobs del scheduler que sincronizan estados externos están permitidos a hacerlo (volumen alto, riesgo bajo).

### 1.6 `whatsapp_processed` — idempotencia

`id_message` de Green API ya procesados. Evita reprocesar si el bot crashea entre `procesar` y `deleteNotification`.

### 1.7 `usuarios` — RBAC

Roles: `admin`, `topografo`, `asistente`. Solo `admin` puede ejecutar comandos privilegiados por WhatsApp.

### 1.8 `apt_memoria_operador` — reglas operativas aprendidas

~96 reglas activas al 2026-05-22. Cada una tiene `patron` (slug), `descripcion`, `operador`, `tipo`, `activa`, `fecha_creacion`.

### 1.9 Tablas de lote — `lotes`, `lotes_items`

Procesamiento batch. Sin uso en producción al 2026-05-22.

### 1.10 `apt_reglas_historia`

Histórico de cambios en `apt_memoria_operador`. Sin uso activo.

---

## 2. SSOT del estado del expediente

### 2.1 Regla canónica

```
expedientes.estado_actual = la ETAPA DEL BOT (workflow interno).

metadata.apt_estado       = el estado en el PORTAL CFIA (cadena cruda
                            scrapeada: "Calificación RN", "Público y
                            Defectuoso", "Inscrito", etc.).

metadata.muni_estado      = el estado en la MUNI (aprobado / morosidad /
                            rechazado / pendiente — derivado del IMAP
                            poller).
```

### 2.2 Cómo se relacionan

`estado_actual` representa LO QUE EL BOT SABE Y HA DECIDIDO HACER. Los
sub-estados representan LO QUE LOS SISTEMAS EXTERNOS DICEN.

| Caso | estado_actual | apt_estado | muni_estado | Acción del bot |
|---|---|---|---|---|
| Recién creado | `recibido` | (vacío) | (vacío) | Esperar archivos |
| Subido a APT | `presentado_apt_r1` | `Calificación RN` | (vacío) | Esperar respuesta CFIA |
| CFIA respondió OK | `aprobado_r1` | `Público e Inscrito` | (vacío) | Avanzar a muni o cerrar |
| CFIA respondió defectuoso, bot no procesó | ⚠️ `presentado_apt_r1` | `Público y Defectuoso` | (vacío) | El bot DEBE detectar y avanzar a `defectuoso` |

El caso ⚠️ es lo que el dashboard tiene que **visualizar** como divergencia, no esconder.

### 2.3 Reglas para los consumidores

| Consumidor | Lee | Para qué |
|---|---|---|
| `_render_html()` (dashboard tabla principal) | `estado_actual` + ETAPAS[…] | Etapa visual + color + próximo paso |
| `_render_html()` (sub-fila/tooltip — pendiente impl.) | `apt_estado`, `muni_estado` | Anotación secundaria con badge de divergencia si aplica |
| Scheduler stale-alert | `estado_actual` | Detectar expedientes parados >48h |
| Scheduler apt-sync-estados | `metadata.apt_estado` previo | Para comparar y disparar workflows si cambió |
| WhatsApp resúmenes | `estado_actual` | Etapa del bot que el operador conoce |
| Reportes Drive / digest | `estado_actual` + `apt_estado` | Histórico operativo |

### 2.4 Quién muta cada campo

| Campo | Mutador único permitido |
|---|---|
| `estado_actual` | `Database.cambiar_estado()` (siempre). Excepción justificada: `_sync_muni_emails` (batch optimization) — pero acompañado de `fecha_actualizacion` en el mismo UPDATE. |
| `metadata.apt_estado` | `Database.actualizar_metadata()` desde `_sync_apt_estados` job. |
| `metadata.muni_estado` | `Database.actualizar_metadata()` desde `_sync_muni_emails` job. |
| Otras keys de `metadata_json` | `Database.actualizar_metadata()` o `set_mega_path()`. NUNCA escribir el JSON entero "a la antigua". |

### 2.5 Detección de divergencia (paso 4 implementará la vista)

Una fila se considera **divergente** si:
```
estado_actual ∈ {'presentado_apt_r1', 'enviado_cfia'}
   AND apt_estado contains "Defectuoso"  OR  apt_estado contains "Inscrito"
```
o también:
```
estado_actual ∈ {'enviado_muni', 'esperando_muni'}
   AND muni_estado IN ('aprobado', 'rechazado', 'morosidad')
```

La vista `v_expedientes_dashboard` calcula esta divergencia como columna `divergencia TEXT NULL` con valores:
- `NULL` → consistente
- `"apt:defectuoso"` → CFIA respondió defectuoso, hay que avanzar a `defectuoso`
- `"apt:inscrito"` → CFIA inscribió, hay que avanzar a `aprobado_r1` o `entregado`
- `"muni:aprobado"`, `"muni:rechazado"`, `"muni:morosidad"` → muni respondió

---

## 3. Invariante de `fecha_actualizacion`

**Regla:** toda mutación de la tabla `expedientes` (UPDATE de cualquier
columna, incluido `metadata_json`) debe dejar `fecha_actualizacion` apuntando
a "ahora" (ISO8601 UTC).

Estado actual (auditoría del 2026-05-22): **se cumple** en todas las rutas
identificadas:
- `Database.cambiar_estado()`, `actualizar_metadata()`, `set_mega_path()`,
  `crear_expediente()` — explícitamente setean el campo.
- `_sync_muni_emails` (scheduler) — hace UPDATE crudo pero incluye `fecha_actualizacion=datetime('now')`.

**Defensa en profundidad (paso 3 implementará):** trigger SQL
`AFTER UPDATE ON expedientes` que automáticamente setea
`fecha_actualizacion = datetime('now')` si la fila cambió pero el caller
olvidó tocar el campo. Esto convierte la regla de "convención del equipo"
a "invariante de la BD".

---

## 4. `metadata_json` — keys conocidas

Convención: usar `Database.actualizar_metadata({"key": value, ...})` que
hace merge (no reemplaza). Las keys no listadas acá igual se preservan,
pero documentar las nuevas en este archivo cuando se agreguen.

### Sub-estados externos
| Key | Tipo | Mutador |
|---|---|---|
| `apt_estado` | string | scheduler `apt-sync-estados` |
| `apt_estado_sync` | ISO8601 | scheduler `apt-sync-estados` |
| `apt_tramite` | string | workflow APT al crear contrato |
| `apt_numero` | string | workflow APT al crear plano |
| `apt_fecha_presentacion` | ISO8601 | workflow APT al `apt-enviar` |
| `muni_estado` | string | scheduler `muni-sync-emails` |
| `muni_aprobado`, `muni_morosidad`, `muni_rechazado` | bool | idem |
| `muni_fecha_aviso`, `muni_asunto_aviso`, `muni_remitente` | string | idem |
| `muni_monto_pendiente` | string (₡) | idem (cuando es morosidad) |

### Datos del trámite
| Key | Notas |
|---|---|
| `datos_apt` | dict completo del seed con `propietario`, `fincas`, `contrato`, `planos[]` |
| `nombre_proyecto` | nombre corto del proyecto (FELIPE_TIOS, VICTOR_2, etc.) |
| `provincia`, `canton`, `distrito` | para ubicación en FS |
| `vertices` | string con guiones (ej. `"1-2-3-4"`) |
| `tipo_acceso` | del visor SR |

### Notificaciones / scheduler
| Key | Notas |
|---|---|
| `correcciones_ultima_notif` | ISO8601 — última vez que `correcciones-renotif` envió WhatsApp |
| `correcciones_lista` | list[str] de correcciones del CFIA |
| `correcciones_tipo` | string libre |
| `stale_ultima_notif` | (futuro — Sprint 2 / O-05) — última vez que `stale-alert` notificó |

### Archivos en Drive / FS
| Key | Notas |
|---|---|
| `mega_path` | ruta del expediente en Drive |

---

## 5. Vista `v_expedientes_dashboard` (a crear en paso 4)

Definición de referencia (el DDL final vivirá en `database.py`):

```sql
CREATE VIEW IF NOT EXISTS v_expedientes_dashboard AS
SELECT
    e.id,
    e.numero_expediente,
    e.tipo_plano,
    e.estado_actual,
    e.fecha_creacion,
    e.fecha_actualizacion,
    e.completado,
    e.cancelado,
    json_extract(e.metadata_json, '$.apt_estado')           AS apt_estado,
    json_extract(e.metadata_json, '$.apt_estado_sync')      AS apt_estado_sync,
    json_extract(e.metadata_json, '$.apt_tramite')          AS apt_tramite,
    json_extract(e.metadata_json, '$.apt_numero')           AS apt_numero,
    json_extract(e.metadata_json, '$.apt_fecha_presentacion') AS apt_fecha_presentacion,
    json_extract(e.metadata_json, '$.muni_estado')          AS muni_estado,
    json_extract(e.metadata_json, '$.nombre_proyecto')      AS nombre_proyecto,
    json_extract(e.metadata_json, '$.provincia')            AS provincia,
    json_extract(e.metadata_json, '$.canton')               AS canton,
    json_extract(e.metadata_json, '$.distrito')             AS distrito,
    (SELECT COUNT(*) FROM estados_historial h
      WHERE h.expediente_id = e.id)                          AS n_transiciones,
    (SELECT MAX(timestamp) FROM estados_historial h
      WHERE h.expediente_id = e.id)                          AS ultimo_evento_ts,
    -- Detección de divergencia bot vs sistema externo
    CASE
        WHEN e.estado_actual IN ('presentado_apt_r1','enviado_cfia')
             AND json_extract(e.metadata_json, '$.apt_estado') LIKE '%Defectuoso%'
            THEN 'apt:defectuoso'
        WHEN e.estado_actual IN ('presentado_apt_r1','enviado_cfia')
             AND json_extract(e.metadata_json, '$.apt_estado') LIKE '%Inscrito%'
            THEN 'apt:inscrito'
        WHEN e.estado_actual IN ('enviado_muni','esperando_muni')
             AND json_extract(e.metadata_json, '$.muni_estado') IN
                 ('aprobado','rechazado','morosidad')
            THEN 'muni:' || json_extract(e.metadata_json, '$.muni_estado')
        ELSE NULL
    END AS divergencia
FROM expedientes e;
```

**No expone `metadata_json` completo** — solo las keys necesarias para el
dashboard. Otra capa (CLI, agentes) sigue accediendo a la tabla raw.

---

## 6. Eventos publicados al SSE (a implementar en paso 5)

Cada mutación que pasa por `Database` publica al stream `/api/events/stream`:

```json
{
  "type": "expediente_updated",
  "id": "<uuid>",
  "numero_expediente": "<exp>",
  "estado_actual": "<estado>",
  "ts": "<iso8601>"
}
```

```json
{
  "type": "control_state_changed",
  "module": "<module>",
  "enabled": true|false,
  "ts": "<iso8601>"
}
```

```json
{
  "type": "heartbeat",
  "ts": "<iso8601>"
}
```

El frontend usa `type` para decidir qué refresca. **No incluir
`metadata_json` completo** en el payload (PII + bandwidth).

---

## 7. Migrations y versionado del schema

Hoy no hay sistema formal de migrations. `_SCHEMA_SQL` usa `CREATE TABLE
IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` que es idempotente para
*agregar* tablas/índices, pero no para *modificar* columnas existentes.

Cuando llegue el primer cambio que requiera ALTER, agregaremos un sistema
formal (alembic o equivalente). Por ahora, los cambios del paso 3 (trigger)
y paso 4 (view) son agregar — no romper compatibilidad.

---

**Fin de SCHEMA.md.** Cualquier nueva columna, tabla, vista, trigger o key
de `metadata_json` debe documentarse acá antes de mergearse a main.

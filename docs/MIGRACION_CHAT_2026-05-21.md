# 📋 Migración de Chat — Contexto completo

**Fecha de migración:** 2026-05-21
**Operador:** Alonso Rojas (topógrafo IT-10676)
**Bot:** catastro-bot — sistema multi-agente Costa Rica (APT CFIA + Muni San Ramón)
**Repositorio:** `C:\catastro-bot\`

> 🤖 **A la nueva sesión de Claude que lee esto:**
> Esta conversación lleva ~2 semanas de trabajo iterativo con el operador.
> Lee este documento ENTERO antes de actuar. Las decisiones técnicas
> importantes están persistidas también en:
>   - `data/catastro.db` → tabla `apt_memoria_operador` (95+ reglas activas)
>   - `docs/BOT_PLAYBOOK.md` → playbook completo
>   - `CLAUDE.md` (raíz) → instrucciones de proyecto
>   - `docs/MUNI_SAN_RAMON_FORM_STRUCTURE.md` → análisis del Google Form muni
>
> No reinventes funcionalidad. Antes de codear algo nuevo, ejecutá:
> ```
> sqlite3 data/catastro.db "SELECT patron FROM apt_memoria_operador WHERE activa=1 ORDER BY id DESC LIMIT 30"
> ls tools/  # ver comandos existentes
> ```

---

## 🟢 Estado actual del bot (al cierre de esta sesión)

### Servicios corriendo
```
✅ Chrome del bot (CDP 9222) — perfil dedicado data/temp/chrome_profile_apt
✅ Watchdog Chrome (cada 60s)
✅ Scheduler src.main (9 jobs)
✅ Dashboard web http://localhost:9224 (con auto-refresh 30s y /config)
✅ Backup automático a Drive (DRIVE_BACKUP_ENABLED=1)
```

### Trámites APT activos (12 expedientes)

| Expediente | Trámite | Proyecto | Cliente | Estado | Notas |
|---|---|---|---|---|---|
| SEG-2026-005 | 1259213 | JAVSAL | JAVIER CASTRO JIMENEZ | `presentado_apt_r1` | Enviado 2026-05-15 |
| SEG-2026-006 | 1259216 | ROLESQ | AGROPECUARIA LAS ESTUFAS | `enviado_muni` | Re-enviado muni 2026-05-21 con archivo 2026-42856.pdf |
| SEG-2026-007 | 1259270 | JOSELITO | JOAQUIN VALVERDE ARAYA | `presentado_apt_r1` | **Rectificación** (no segregación). Enviado 2026-05-15 |
| RDF-2026-005 | 1258460 | VICTOR_2 | MARIA ALEJANDRA JIMENEZ | `presentado_apt_r1` | Caso VICTOR #2 |
| SEG-2026-002 | 1258460 (compartido) | VICTOR_2 | RODOLFO JIMENEZ | `presentado_apt_r1` | Hermano de RDF-005 |
| SEG-2026-003 | 1223951 | TILMAN | TILMAN | `muni_morosidad` | Esperando que cliente pague impuestos |
| SEG-2026-004 | 1223951 | TILMAN | TILMAN | `muni_morosidad` | Mismo contrato que SEG-003 |
| RDF-2026-001 | 1257831 | ROLANDO_GRANJA | GRANOS Y FORRAJES DEL PONIENTE | `presentado_apt_r1` | |
| RDF-2026-002 | 1257881 | OMAR_2026 | OMAR | `presentado_apt_r1` | |
| RDF-2026-003 | 1258052 | ROLANDO_Y_ROL | SOCIEDAD AGROPECUARIA | `presentado_apt_r1` | |
| RDF-2026-004 | 1258126 | FELIPE_TIOS | LUIS EMILIO PIÑEIRO | `presentado_apt_r1` | |
| SEG-2026-001 | TEST-DEV-001 | DANIEL_BUREAL | DANIEL_BUREAL | `carta_agua_requerida` | Test viejo, no real |

### Credenciales configuradas (Windows Credential Manager)

| Credencial | Estado |
|---|---|
| `anthropic-api` (Vision + minuta) | ✅ configurada |
| `muni-san-ramon` (Gmail IMAP) | ✅ `topografiahrh@gmail.com` + App Password |
| `green-api` (WhatsApp) | ✅ configurada, ⚠️ pero envíos fallan con HTTP 466 |
| `apt-cfia` | ❌ no configurada (no se usa programáticamente) |
| `email-bot` | ❌ no configurada |
| `google_oauth_credentials.json` | ✅ en `config/` + cargado en Cred Mgr |
| Drive OAuth refresh token | ✅ autorizado |

### .env (vars críticas)
```env
CATASTRO_BOT_DEV_MODE=1         # BD sin SQLCipher (necesario Windows)
CATASTRO_BOT_SIMULAR_APT=0      # ⚠️ CRÍTICO: NO simular workflows (era bug grande)
DRIVE_BACKUP_ENABLED=1          # backup diario a Drive activo
```

---

## 🎯 Trabajo completado en esta sesión

### Comandos CLI nuevos agregados a `catastro-bot`

| Comando | Qué hace |
|---|---|
| `catastro-bot resumen [--activos --apt --muni --hoy --exp X]` | Tabla en consola con todos los expedientes |
| `catastro-bot config list/set/check/rm` | Gestión de credenciales sin abrir Python |
| `catastro-bot apt-flujo <exp>` | apt-crear + apt-guardar + apt-plano todo de corrido |
| `catastro-bot apt-enviar <exp>` | Click `#BtnEnviarAgrimensura` (sin FD) |
| `catastro-bot apt-sync [exp]` | Consulta APT por estado real vía CDP |
| `catastro-bot apt-r2 <exp>` | Subir anverso corregido + visado muni (R2) |
| `catastro-bot drive autorizar/subir-backup/listar/activar/desactivar/estado` | Backup OAuth a Drive |
| `catastro-bot backup [--con-archivos --sin-codigo --listar --purgar N]` | Backup completo local (BD + código + datos) |
| `catastro-bot esquema` | Regenera PDF visual del flujo del bot |
| `catastro-bot abrir` | Abre el dashboard en el browser |
| `catastro-bot dashboard-web` | Arranca el dashboard manualmente |
| `catastro-bot muni notificar-cliente <exp>` | WhatsApp morosidad/aprobado/rechazado al cliente |

### Dashboard web (`http://localhost:9224`)

- **Página principal `/`** → tabla con 12 expedientes + columna Proyecto + etapas numeradas 1-10 + panel "Estado del bot" arriba
- **Página `/config`** → editor de credenciales (sin mostrar valores existentes) + **panel "🎛️ Control del bot"** con botones para apagar/encender/reiniciar servicios
- **API JSON:** `/api/expedientes`, `/api/bot-status`, `/api/config`
- Auto-refresh cada 30s, grid de puntos estilo n8n, colores por etapa

### Scheduler (9 jobs)
```
1. tick                       cada 60s    procesa workflows
2. apt-sync-estados           cada 30min  consulta APT vía CDP
3. muni-sync-emails-arranque  one-shot    al arrancar el bot
4. muni-sync-emails           cron 11:00 + 14:00 CR (L-V)
5. stale-alert                cada 6h     expedientes parados >48h
6. correcciones-renotif       cada 24h    recordatorios
7. db-backup                  diario 02:00 UTC  (+ sube a Drive si activo)
8. audit-verify               diario 03:00 UTC  cadena hash
9. weekly-report              lunes 07:00 CR
```

### Backup automático
- **Local:** `data/backups/completos/` — ZIP con BD + .env + config + reglas + **código fuente (src/ tools/ tests/ docs/)** + LEEME_RESTAURACION.txt
- **Drive:** `topografiahrh@gmail.com/Mi unidad/catastro-bot/backups/` — mismo ZIP subido vía OAuth
- **Retención local:** 30 días (auto-purga)
- **Backup pesado** (con PDFs de expedientes): lunes a la 2 AM
- **Comando manual:** `catastro-bot backup --con-archivos`

### Autostart Windows
- `tools/catastro_bot_autostart.bat` copiado a Startup folder del usuario
- Lanza: Chrome bot → Watchdog → Dashboard → Scheduler
- También abre `http://localhost:9224` en el navegador al iniciar

---

## 🐛 Bugs encontrados y arreglados (lista cronológica)

| # | Bug | Archivo | Solución |
|---|---|---|---|
| 1 | `catastro-bot health` faltaba ROOT en sys.path | `tools/catastro_bot.py` | sys.path.insert + os.chdir(ROOT) |
| 2 | Chrome bot caído al arrancar | sistema | watchdog_chrome_loop creado |
| 3 | `extract_registro` solo PNG/JPG, no PDF | `plano_vision_extractor.py` | Acepta application/pdf |
| 4 | Auto-detect registro PDF no detectaba | `extraer_datos_apt.py` | Aceptar .pdf |
| 5 | `_RE_NUMERO_ENTERO` requería num post "ENTERO" | `plano_pdf_extractor.py` | Aceptar headers de tabla |
| 6 | `seed_builder.py:328` hardcodeaba `titulares=[]` | `seed_builder.py` | Construir desde RegistroData (R5) |
| 7 | `_RE_AREA_REGISTRO` solo aceptaba miles | regex | Acepta sin separador |
| 8 | `mapear_tipo_uso` solo CAFE+SOLAR | `tipo_uso_mapper.py` | +7 categorías (AGRI, PASTO, FRUTAL, BOSQUE, CHARRAL, CAÑA, HABITACION) |
| 9 | Sin Anthropic key, bot abortaba | `extraer_datos_apt.py` | Fallback pypdf+regex completo |
| 10 | `_parsear_finca` no manejaba `2-X-000` | `seed_builder.py` | Regex con 3 segmentos |
| 11 | Vision devuelve `numero="2-X"` con prefijo | `seed_builder.py` | `_limpiar_numero_plano()` |
| 12 | Vision a veces deja `nombre=""` jurídicas | `extraer_datos_apt.py` | Cross-fallback Vision↔pypdf |
| 13 | **DEV_MODE simulaba workflows en producción** | `base_workflow.py` | **Separado en CATASTRO_BOT_SIMULAR_APT** |
| 14 | `start_chrome_bot` mataba TODOS los Chrome | `tools/start_chrome_bot.py` | Filtra por CommandLine (solo perfil bot) |
| 15 | Vision puede inventar protocolo en cajetín vector | regla | Verificar tomo/folio con operador |
| 16 | Honorarios calculados faltan ₡5000 fijos | regla | Sumar manual en seed |
| 17 | UTF-8 stdout en healthcheck (emoji crash) | `healthcheck.py` | io.TextIOWrapper con encoding |
| 18 | Polling muni cada 15min era exceso | `scheduler/tasks.py` | Cambiado a 11:00+14:00 CR L-V |
| 19 | Vision lee mal dígitos individuales (área, finca) | regla | SIEMPRE cross-check shapefile + registro |

---

## 📚 Reglas operativas aprendidas (95+ en BD)

Las críticas (NO REVERTIR):

```sql
-- En BD: SELECT patron FROM apt_memoria_operador WHERE activa=1
monto_pagado_entero_es_TASADO_no_DEBITADO
R1_corregida_fincas_unicas_no_planos
R5_titular_no_doble_para_segregacion
dev_mode_no_debe_simular_workflows_en_produccion
start_chrome_bot_NO_mata_chrome_del_usuario
fd_firma_digital_va_embebida_en_planof_pdf
carta_agua_3_tramos_muni_san_ramon            -- <1000 obligatoria / 1000-5000 opcional / >5000 nota
nota_muni_obligatoria_en_plano_sin_carta_agua -- siempre que no haya carta
honorarios_calculados_faltan_5000_colones_fijos
vision_inventa_protocolo_cajetin_vector
vision_se_equivoca_en_digitos_de_area_y_finca
protocolo_cajetin_plano_puede_tener_typo
muni_form_documentos_es_UN_SOLO_pdf_combinado
muni_form_tomo_asiento_son_del_cfia_no_del_topografo
muni_form_fecha_minuta_del_sello_cfia
muni_polling_3_veces_al_dia_no_cada_15min
area_mayor_que_madre_sugiere_rectificacion
extractor_pypdf_fallback_sin_vision
```

---

## 🔑 Datos clave del operador

| | |
|---|---|
| Nombre | Luis Alonso Rojas Herrera |
| Carné CFIA | IT-10676 |
| Tel WhatsApp | +50688887310 |
| Cuenta Gmail muni (IMAP) | `topografiahrh@gmail.com` |
| Cuenta personal Hotmail | `ingarojas@hotmail.com` |
| Cuenta Anthropic | `civilhrh@gmail.com` |
| Google Cloud project | `topografia-496423` (TOPOGRAFIA) |

---

## 📂 Estructura del proyecto

```
C:\catastro-bot\
├── .env                          # CATASTRO_BOT_DEV_MODE=1, SIMULAR_APT=0, DRIVE_BACKUP_ENABLED=1
├── CLAUDE.md                     # Instrucciones para Claude
├── requirements.txt
├── README.md
├── pytest.ini
├── config/
│   ├── munis.yaml                # Config muni San Ramón (entries del form)
│   ├── settings.py
│   └── google_oauth_credentials.json
├── data/
│   ├── catastro.db               # SQLite — 95+ reglas + 12 expedientes
│   ├── files/                    # PDFs de expedientes (estructura PROV/CANT/DIST/PROYECTO/)
│   ├── backups/completos/        # ZIPs locales (30 días retención)
│   └── temp/chrome_profile_apt/  # Perfil persistente Chrome bot
├── docs/
│   ├── BOT_PLAYBOOK.md           # Playbook maestro
│   ├── MUNI_SAN_RAMON_FORM_STRUCTURE.md
│   ├── MIGRACION_CHAT_2026-05-21.md  # ← ESTE ARCHIVO
│   └── PROTOCOLO_PRE_VUELO.md
├── src/
│   ├── main.py                   # Entry point scheduler (python -m src.main)
│   ├── agents/                   # 7 agentes: apt, muni, drive, file_manager, ...
│   ├── workflows/                # 5 tipos: segregacion, rectif, reun, info_pos, fincas_comp
│   ├── scheduler/tasks.py        # 9 jobs
│   ├── utils/                    # plano_pdf_extractor, registro_pdf_extractor,
│   │                             # dashboard_web, backup_completo, muni_imap_reader,
│   │                             # muni_uploader, muni_paquete, etc.
│   ├── core/
│   │   ├── database.py
│   │   └── credential_manager.py
│   └── ...
└── tools/
    ├── catastro_bot.py           # CLI principal con 24+ subcomandos
    ├── catastro_bot_autostart.bat
    ├── start_chrome_bot.py
    ├── apt_flujo.py
    ├── apt_enviar.py
    ├── apt_sync.py
    ├── drive.py
    ├── config.py
    ├── resumen.py
    ├── generar_esquema_pdf.py
    ├── muni.py
    ├── debug.py
    └── ...
```

---

## ⏳ Pendientes operativos (al cierre)

### Esperando respuesta externa
- ⏰ Muni San Ramón responda los 2 trámites TILMAN (cliente con morosidad)
- ⏰ Muni responda ROESQUINA recién enviado el 21/05
- ⏰ CFIA responda R1 de los 9 trámites en revisión (5-7 días esperados)

### Para arreglar cuando sea necesario
- 🐛 **Green API HTTP 466** — WhatsApp no envía. Posibles causas: quota mensual, número destino no whitelisted, instancia desautorizada. NO investigado todavía.
- 📦 **Verificar restauración del backup** — descargar último ZIP de Drive y probar restaurar en carpeta de prueba

### Funcionalidad pendiente / mejoras
- ⏸️ `apt-r2` con visado muni — pendiente cuando muni apruebe TILMAN/ROESQUINA
- ⏸️ Notificación WhatsApp a cliente (depende de Green API 466)
- ⏸️ Tests de restauración del backup en otra PC

---

## 🚀 Cómo retomar en una NUEVA sesión de Claude

### Paso 1 — Lectura obligatoria (en orden)
```bash
# 1. Lee este archivo entero (lo estás haciendo)
# 2. Lee el playbook
cat docs/BOT_PLAYBOOK.md
# 3. Lee instrucciones del proyecto
cat CLAUDE.md
# 4. Ve las reglas activas en BD
sqlite3 data/catastro.db "SELECT patron, descripcion FROM apt_memoria_operador WHERE activa=1 ORDER BY id"
```

### Paso 2 — Verifica que el bot esté vivo
```bash
.venv/Scripts/python.exe tools/catastro_bot.py debug chrome
.venv/Scripts/python.exe tools/catastro_bot.py health
curl http://localhost:9224/api/bot-status
```

### Paso 3 — Mira el resumen actual
```bash
.venv/Scripts/python.exe tools/catastro_bot.py resumen --apt
```

### Paso 4 — Preguntá al operador qué quiere hacer

**Probables tareas que va a pedir:**
- Procesar un plano nuevo (segregación, rectificación, etc.) → flujo es: `crear` → `extraer --save` → `apt-flujo` → revisión visual → `apt-enviar`
- Re-enviar un trámite a muni → preparar PDF combinado + abrir form pre-llenado
- Verificar respuestas muni → ya corre cada 11:00+14:00 CR. Manual: `catastro-bot muni revisar-respuestas`
- Consultar estado APT → manual: `catastro-bot apt-sync`

### Paso 5 — NO REINVENTAR
- Si el operador te dice "está mal X", ANTES de cambiar código, mira `apt_memoria_operador` en BD
- Si pensás que descubriste un bug, ANTES de fixearlo verificá los 1397+ tests existentes
- Si vas a tocar credenciales o el .env, **NUNCA** revertás `CATASTRO_BOT_SIMULAR_APT=0` ni desactives DRIVE_BACKUP_ENABLED sin avisar

---

## 🎨 Filosofía del proyecto (del operador)

> "El bot aprende cuando el operador lo corrige. Cada corrección debe quedar
> codificada como regla persistente — no en mi cabeza (que se va con el chat),
> sino en código + BD + docs.
>
> Cuando el operador me diga 'esto está mal', mi reacción debe ser:
> 1. PARAR de inventar
> 2. Buscar en código existente la regla relacionada
> 3. Si NO existe regla → documentar la nueva
> 4. Si SÍ existe → aplicar EXACTAMENTE lo codificado
>
> Reincidir es el peor pecado."

---

## 📞 Para validar que entendiste todo

Si sos Claude leyendo este documento en una nueva sesión, decile al operador algo como:

> "Leí el contexto. Veo que tenés 12 expedientes en BD (3 enviados al CFIA esta semana: JAVSAL, ROLESQ, JOSELITO; ROESQUINA recién enviado a muni el 21/05; TILMAN x2 en muni_morosidad). El bot corre como servicio con 9 jobs. Backup automático a Drive activo. Dashboard en localhost:9224. ¿En qué seguimos?"

Si el operador dice "perfecto, sigamos con X", procedés.
Si dice "espera, te falta detalle de Y", releé la sección correspondiente.

---

**Última acción de la sesión anterior:**
Se agregó el panel "🎛️ Control del bot" a la página `http://localhost:9224/config` con botones individuales (apagar/encender Chrome, Watchdog, Scheduler) + acciones masivas (Encender todo / Apagar todo / Reiniciar). El dashboard NO se apaga a sí mismo en "Apagar todo" para no perder acceso.

**Tarea que dejó pendiente el operador:**
Apagar el bot temporalmente vía el botón nuevo del panel /config (el operador quiere probarlo).

Fin de la migración.

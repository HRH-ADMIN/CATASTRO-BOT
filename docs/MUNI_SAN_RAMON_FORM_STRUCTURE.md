# Muni San Ramón — Formulario APT-PUBLICO (Google Forms)

**Análisis estructural del formulario para visado municipal.**
Fecha de captura del HTML: 2026-05-20.

Este documento es la **fuente de verdad** para la automatización del envío
muni. Si Google cambia algo del form, hay que actualizar acá.

---

## Identificadores generales

| Campo | Valor |
|---|---|
| **URL pública** | `https://docs.google.com/forms/d/e/1FAIpQLSejhSph15X_RPtT39SjiLUYnCqjg_FB1Gq67A8mjgA-1XVXOQ/viewform` |
| **Form ID** | `1FAIpQLSejhSph15X_RPtT39SjiLUYnCqjg_FB1Gq67A8mjgA-1XVXOQ` |
| **POST endpoint** | `https://docs.google.com/forms/u/0/d/e/{FORM_ID}/formResponse` |
| **Title** | `Formulario APT-PUBLICO` |
| **Owner** | Municipalidad de San Ramón |
| **Owner email** | `alonsorojas21@gmail.com` (form creator) |
| **Email muni catastral** | `mgamboa@sanramon.go.cr` |

## Características de seguridad y autenticación

| Característica | Valor |
|---|---|
| **Requiere login Google** | Sí — `data-is-response-view="true"` + email capturado del session |
| **Captura email automático** | Sí — `data-is-prepopulate-mode="false"` con campo `Correo` mostrado |
| **reCAPTCHA** | Invisible v3 |
| **reCAPTCHA sitekey** | `6LcJMyUUAAAAABOakew3hdiQ0dU8a21s-POW69KQ` |
| **reCAPTCHA callback** | `fpHtcb` |
| **CSRF token** | `name="token"` — valor dinámico por sesión |
| **fbzx (form key)** | dinámico por sesión |
| **submissionTimestamp** | `-1` (placeholder, lo setea el cliente JS al submit) |
| **target** | `_self` (no abre nueva ventana) |
| **method** | `POST` (multipart/form-data por los file uploads) |

---

## Hidden fields del form

```html
<input name="fvv" value="1">                              <!-- form version -->
<input name="partialResponse" value="[null,null,...]">    <!-- estado parcial -->
<input name="fuIds" value="702941744,125723641">          <!-- file uploads IDs -->
<input name="pageHistory" value="0">                      <!-- página actual -->
<input name="token" value="s1XGS54BAAA...">              <!-- CSRF -->
<input name="tag" value="AWjlRz+gQtTaKUdM...">           <!-- dinámico -->
<input name="fbzx" value="-4259059637606511254">         <!-- form session -->
<input name="submissionTimestamp" value="-1">             <!-- timestamp client-side -->
<input name="emailReceipt" value="true">                  <!-- copia por email -->
```

---

## Campos del formulario (15 inputs, orden visual)

### 1. Correo (auto-capturado)
- **Tipo:** email (read-only, sale del login Google)
- **Required:** sí
- **Entry ID:** N/A (no se envía via `entry.*`, se captura del session)
- **Nota:** `data-prefill="false"` — no se puede pre-llenar via URL

### 2. Tomo
| | |
|---|---|
| **Label** | `Tomo` |
| **Entry ID** | `entry.413935745` |
| **Tipo** | text |
| **Required** | sí (`*`) |
| **Pre-fillable via URL** | sí |

### 3. Asiento
| | |
|---|---|
| **Label** | `Asiento` |
| **Entry ID** | `entry.112406539` |
| **Tipo** | text |
| **Required** | sí |

### 4. Proyecto-APT
| | |
|---|---|
| **Label** | `Proyecto-APT` |
| **Help text** | `NÙMERO DE CONTRATO` |
| **Entry ID** | `entry.1184863793` |
| **Tipo** | text |
| **Required** | sí |

### 5. Fecha-Minuta
| | |
|---|---|
| **Label** | `Fecha-Minuta` |
| **Entry ID** | `entry.1421348955` |
| **Tipo** | date (input[type=date]) |
| **Required** | sí |
| **Max date** | `2076-01-01` |
| **Pre-fillable** | usa formato `entry.1421348955_year=YYYY&entry.1421348955_month=MM&entry.1421348955_day=DD` |

### 6. Área
| | |
|---|---|
| **Label** | `Área` |
| **Entry ID** | `entry.2015524080` |
| **Tipo** | text |
| **Required** | sí |
| **Formato esperado** | número con decimales (ej `7686.06`) |

### 7. Nº Finca
| | |
|---|---|
| **Label** | `Nº Finca` |
| **Help text** | `El formato debe de ser el siguiente: 2 222222-000 y en el caso de varias fincas: 2 333333-000/2 444444-001/etc.` |
| **Entry ID** | `entry.2090596021` |
| **Tipo** | text |
| **Required** | sí |
| **Formato** | `<provincia> <numero>-<derecho>` (separador `/` para múltiples) |

### 8. Distrito (LISTBOX / select)
| | |
|---|---|
| **Label** | `Distrito` |
| **Entry ID** | `entry.1965655160` |
| **Tipo** | listbox (`<select>`) |
| **Required** | sí |

**Opciones (14):**

| Código | Valor exacto del form |
|---|---|
| 01 | `01 San Ramón` |
| 02 | `02 Santiago` |
| 03 | `03 San Juan` |
| 04 | `04 Piedades Norte` |
| 05 | `05 Piedades Sur` |
| 06 | `06 San Rafael` |
| 07 | `07 San Isidro` |
| 08 | `08 Ángeles` |
| 09 | `09 Alfaro` |
| 10 | `10 Volio` |
| 11 | `11 Concepción` |
| 12 | `12 Zapotal` |
| 13 | `13 Peñas Blancas` |
| 14 | `14 San Lorenzo` |

### 9. Proceso trámite (LISTBOX)
| | |
|---|---|
| **Label** | `Proceso trámite` |
| **Entry ID** | `entry.335206885` |
| **Required** | sí |

**Opciones (5):**

1. `Segregación`
2. `Segregar y Reunir`
3. `Localizar Derecho`
4. `Información Posesoria`
5. `Validación de Vía Pública`

**Mapeo desde tipo_plano del bot:**

| tipo_plano (bot) | Valor para form |
|---|---|
| `segregacion` | `Segregación` |
| `reunion_de_fincas` | `Segregar y Reunir` |
| `rectificacion` | `Validación de Vía Pública` |
| `informacion_posesoria` | `Localizar Derecho` |
| `fincas_completas` | `Localizar Derecho` |

### 10. Tipo Acceso (LISTBOX)
| | |
|---|---|
| **Label** | `Tipo Acceso` |
| **Entry ID** | `entry.990422047` |
| **Required** | sí |

**Opciones (19) — ¡cuidado con dobles espacios y acentos!:**

```
Ruta Cantonal
Ruta Nacional  Nº 01        ← 2 espacios entre "Nacional" y "Nº"
Ruta Nacional  Nº 135       ← 2 espacios
Ruta Nacional Nº 156
Ruta Nacional Nº 169
Ruta Nacional Nº 702
Ruta Nacional  Nº 703       ← 2 espacios
Ruta Nacional Nº 704
Ruta Nacional Nº 705
Ruta Nacional Nº 713
Ruta Nacional N°725         ← N° con grado, NO Nº (ojo!)
Ruta Nacional Nº 742
Ruta Nacional Naranjo - Florencia
Servidumbre de Paso
Acceso excepcional uso residencial
Servidumbre Pecuaria
Servidumbre Forestal
Servidumbre Mixta
Servidumbre Agrícola
```

**⚠️ IMPORTANTE:** algunas opciones tienen **doble espacio** entre "Nacional" y "Nº",
y la opción 725 usa **N°** (con símbolo grado U+00B0) en vez de **Nº** (U+00BA).
Al pre-llenar via URL hay que pasar el valor EXACTO, sino el form rechaza.

### 11. Vértices
| | |
|---|---|
| **Label** | `Vértices` |
| **Help text** | `QUE ENFRENTAN LA VÌA DE ACCESO` |
| **Entry ID** | `entry.44837579` |
| **Tipo** | text |
| **Required** | sí |
| **Formato** | lista con guiones, ej `1-2-3-4` (NO cantidad) |

### 12. Profesional tramitante
| | |
|---|---|
| **Label** | `Profesional tramitante` |
| **Entry ID** | `entry.299608996` |
| **Tipo** | text |
| **Required** | sí |
| **Formato Muni SR** | `nombres primero` + lowercase (`luis alonso rojas herrera`) |

### 13. Carné
| | |
|---|---|
| **Label** | `Carné` |
| **Entry ID** | `entry.1470141826` |
| **Tipo** | text |
| **Required** | sí |
| **Formato Muni SR** | lowercase con guión: `it-10676` |

### 14. DOCUMENTOS (file upload — OBLIGATORIO)
| | |
|---|---|
| **Label** | `DOCUMENTOS` |
| **Entry ID** | `entry.702941744` |
| **fuId** | `702941744` (presente en hidden `fuIds`) |
| **Tipo** | file upload (Google Picker) |
| **Required** | sí |
| **Max size** | 100 MB (`104857600` bytes) |
| **Tipos permitidos** | `[5,6]` = **PDF y document** |
| **Cantidad** | 1 archivo |

**Contenido esperado del PDF combinado (instrucción del form):**
1. Archivo del plano rechazado por Catastro (con sello digital CFIA)
2. Archivo del plano nuevo (corregido)
3. "Minuta de Rechazo" del Catastro Nacional
4. Croquis del proceso a ejecutar
5. Nota de Disponibilidad de Agua (si aplica, según Circular DDU-02-2017)

**⚠️ TODOS los documentos en UN SOLO archivo PDF.**

### 15. Archivo Shape (file upload — OPCIONAL)
| | |
|---|---|
| **Label** | `Archivo Shape` |
| **Help text** | `Formato del archivo Shape debe enviarlo comprimido en zip.` |
| **Entry ID** | `entry.125723641` |
| **fuId** | `125723641` |
| **Tipo** | file upload (Google Picker) |
| **Required** | NO |
| **Max size** | 100 MB |
| **Tipos permitidos** | `[0]` = **archive (zip/rar/etc.)** |
| **Cantidad** | 1 archivo |

---

## URL pre-llenada construible

Todos los campos texto/listbox/date son pre-fillable via URL params. Los uploads NO.

```
https://docs.google.com/forms/d/e/{FORM_ID}/viewform?usp=pp_url
  &emailAddress={EMAIL}                            (auto-captura del login)
  &entry.413935745={TOMO}
  &entry.112406539={ASIENTO}
  &entry.1184863793={PROYECTO_APT}
  &entry.1421348955_year={YYYY}
  &entry.1421348955_month={MM}
  &entry.1421348955_day={DD}
  &entry.2015524080={AREA}
  &entry.2090596021={FINCA}                       (formato "2 629270-000/...")
  &entry.1965655160={DISTRITO}                    (valor exacto del listbox)
  &entry.335206885={PROCESO}                      (valor exacto)
  &entry.990422047={TIPO_ACCESO}                  (valor exacto, ¡ojo espacios!)
  &entry.44837579={VERTICES}                      (formato "1-2-3-4")
  &entry.299608996={PROFESIONAL}                  (lowercase)
  &entry.1470141826={CARNE}                       (lowercase, "it-10676")
```

⚠️ **URL-encode obligatorio** para tildes, ñ, espacios, etc.

---

## Restricciones operativas (aprendidas)

### Lo que SÍ se puede automatizar
- Pre-llenar todos los campos texto + listbox + date via URL
- Detectar respuestas via IMAP (`muni_imap_reader.py` ya lo hace)
- Verificar acuse de Google Forms en email

### Lo que NO se puede automatizar (intervención humana)
- **Login Google** — el form REQUIERE sesión Google activa (no anónima)
- **Upload de archivos** — Google Picker NO acepta `<input type=file>` programático en este form. El operador debe seleccionar manualmente desde Drive del bot.
- **Click final "Enviar"** — reCAPTCHA invisible bloquea clicks programáticos
   (verificado en sesión anterior con TILMAN). El operador debe hacer click humano.

### Flujo de trabajo automatizado parcial
1. Bot genera URL pre-llenada → operador la abre en Chrome del bot logueado
2. Bot sube archivos via Playwright + Google Picker (workaround `FileChooser`)
3. Operador da click manual en "Enviar"
4. Bot polea Gmail para confirmar acuse + detectar respuesta muni

---

## Estado del soporte en el bot (al 2026-05-20)

| Componente | Ubicación | Estado |
|---|---|---|
| URL pre-llenada | `src/agents/municipality_agent.py:construir_url_formulario()` | ✅ Implementado |
| Config form | `config/munis.yaml:san_ramon.google_form.entries` | ✅ Sincronizado |
| Mapeo distritos | `config/munis.yaml:san_ramon.distritos` | ✅ Los 14 |
| Mapeo procesos | `config/munis.yaml:san_ramon.procesos` | ✅ Los 5 |
| Tipos acceso | `config/munis.yaml:san_ramon.tipos_acceso` | ✅ Los 19 |
| Upload archivos | `src/utils/muni_uploader.py:subir_archivo_a_picker()` | ✅ via Playwright |
| Doble chequeo | `src/utils/muni_uploader.py:doble_chequeo_form_muni()` | ✅ |
| IMAP clasificación | `src/utils/muni_imap_reader.py` | ✅ APROBADO/MOROSIDAD/RECHAZADO |
| Polling automático | `src/scheduler/tasks.py:_sync_muni_emails` | ✅ cada 15min |
| Notificación cliente | `src/utils/muni_notificacion.py` | ✅ morosidad/aprobado/rechazado |

---

## Notas técnicas adicionales

- `data-shuffle-seed="-4259059637606511254"` — Google randomiza orden de opciones
  por seed. Para esta form NO se randomiza (los listbox tienen orden fijo).
- `data-first-entry="0"` `data-last-entry="14"` — confirma 15 campos en página única.
- `data-is-first-page="true"` — form de página única (no multi-step).
- `data-clean-viewform-url` apunta a `/viewform` sin parámetros — útil para reset.
- El form muestra mensaje "Se enviará una copia de tus respuestas por correo electrónico" → eso es la fuente del acuse de Google Forms que el bot detecta via IMAP.

---

## Para PASO 2 (cuando lo retomemos)

Probables temas a optimizar:
- Validar URL pre-llenada con valores reales (probar con un trámite test)
- Revisar el mapeo `tipo_plano → Proceso trámite` (las 5 opciones)
- Detectar si el form aceptó la URL (campos quedan pre-rellenos al cargar)
- Manejar timeout/expiración del CSRF token entre carga y submit
- Mejorar el "doble chequeo" antes del envío manual
- Capturar el N° de respuesta del acuse para asociar al expediente

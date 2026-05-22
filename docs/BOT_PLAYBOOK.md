# 📖 BOT PLAYBOOK — Lectura obligatoria al inicio de cada sesión

> **Este es el documento más importante del proyecto.**
>
> Cualquier sesión de Claude que abra este repo DEBE leer este archivo
> ANTES de tocar APT, modificar `apt_agent.py`, llenar formularios o
> construir seeds. Las 36 reglas activas (BD `apt_memoria_operador`) +
> los 60 tests + los módulos en `src/utils/` cubren todo lo que se
> aprendió hasta hoy. **No reinventes lo ya codificado.**
>
> Última actualización: 2026-05-12 (VICTOR #2 enviado al CFIA — APT 1258460)

---

## ⚡ ARRANQUE RÁPIDO (5 min)

Antes de tocar nada, ejecutar:

```bash
# 1. Ver reglas activas
sqlite3 data/catastro.db "SELECT patron FROM apt_memoria_operador WHERE activa=1 AND tipo='regla' ORDER BY id"

# 2. Leer documentos críticos
cat docs/BOT_PLAYBOOK.md docs/PROTOCOLO_PRE_VUELO.md docs/reglas_oficina_aprendidas.md

# 3. Verificar tests pasan
.venv/Scripts/python.exe -m pytest tests/test_apt_auditor.py tests/test_area_consolidator.py -q
```

---

## 🧭 ÍNDICE — DÓNDE ESTÁ CADA COSA

### Reglas operativas (R1-R18) → `docs/PROTOCOLO_PRE_VUELO.md`
| # | Tema | Función código |
|---|------|----------------|
| R1 | Áreas contrato multi-plano (dedup fincas) | `consolidar_areas_contrato()` |
| R2 | Honorarios contrato multi-plano | `aplicar_consolidacion_a_datos_apt()` |
| R3 | Conteo planos (max_planos, n_planos_catastrar) | idem |
| R4 | Área registro plano multi-finca | `consolidar_area_registro_plano()` |
| R5 | Multi-propietario por plano permitido | (operativa) |
| R6 | Crear plano N+1 en contrato | URL `?EsNuevoPlano=1` |
| R7 | bP2 FINCAS — GuardarFinca() por cada una | (operativa) |
| R8 | Enteros únicos por plano | (validación APT) |
| R9 | bP4 TITULARES — uno por propietario | `verificar_titulares_vs_fincas()` |
| R10 | Modificar enteros: DELETE+RE-ADD | (procedimiento) |
| R11 | Navegar planos con cargarPlano(contrato, id) | (procedimiento) |
| R12 | Tamaño físico desde PDF | (extracción) |
| R13 | Verificar archivos por plano_id en nombre | `verificar_archivos_subidos_por_plano()` |
| R14 | Revisar PDFs antes de llenar | `verificar_seed_vs_cajetin_pdf()` |
| R15 | Revisar después de llenar | `doble_chequeo_plano()` |
| R16 | bP4 ↔ propietarios de bP2 | `verificar_titulares_vs_fincas()` |
| R17 | DOBLE CHEQUEO obligatorio antes de enviar | `doble_chequeo_plano()` + `doble_chequeo_contrato_multi_plano()` |
| R18 | Cambio en bP1 dispara re-cálculo bC7 | (operativa) |

### Tablas de códigos APT → `docs/PROTOCOLO_PRE_VUELO.md`
- `#ddlTamanno` (10 códigos físicos del plano)
- `#ddlTipoUso` (47 códigos de naturaleza)
- `#ddlTipoIdentificacion` (12 tipos de cédula)
- `#ddlTitularidad` (7 tipos de titularidad)
- `#ddlTipoZona` (2=rural, 3=urbano)
- `#ddlTipoCoordenada` (3=CRTM05 siempre)

### Bugs identificados y fix → `docs/reglas_oficina_aprendidas.md`
- Bug-1: bot reporta `[OK]` falso (no verifica icono visual)
- Bug-2: `tamanno` vacío hace fail silencioso en bP1
- Bug-2b: panel del plano es `#P6`, no `#C6` (que es del contrato)
- Bug-2c: `txtMontoPagado` espera TASADO, no DEBITADO

---

## 🔑 LAS 10 REGLAS DE ORO (MEMORIZAR)

> Si solo tenés 5 minutos antes de tocar APT, leer estas 10:

### 1. NUNCA inventar — siempre consultar primero
```bash
grep -rn "<palabra_clave>" src/utils/seed_builder.py src/agents/apt_agent.py
sqlite3 data/catastro.db "SELECT * FROM apt_memoria_operador WHERE patron LIKE '%<palabra>%'"
```

### 2. `txtMontoPagado` = TASADO (sin descuento), NO debitado
```
Ejemplo entero VICTOR #2: tasado=17020 / debitado=16016.80 → APT espera 17020
```

### 3. `area_predio` del contrato = SUMA de FINCAS ÚNICAS (deduplicar)
```
NO sumar area_registro por plano si las fincas se comparten.
Caso real: 161099 en plano 1 Y plano 2 → cuenta UNA vez.
```

### 4. `tamanno` (bP1) es OBLIGATORIO — sin él, bP1 falla silencioso
```
Tabla: 22x32=1 / 32x44=2 (los únicos típicos en oficina)
Se infiere del PDF: dimensiones cm → código
```

### 5. bP4 TITULARES = propietarios ACTUALES de fincas de bP2 (1:1)
```
NO agregar receptores futuros ni propietarios de otros planos.
Plano de segregación → solo propietario actual de la finca madre.
```

### 6. Modificar entero = DELETE primero + RE-ADD limpio
```
EliminarEnteros(token, id) → confirmar swal → llenar form → GuardarEnteros()
NO se puede editar in-place (APT dice "numero usado en otro plano").
```

### 7. Cada plano del contrato tiene SU propio número de entero
```
Plano 1: entero del PDF de su carpeta REU/
Plano 2: entero del PDF de su carpeta SEG/
APT rechaza duplicados.
```

### 8. Después de llenar UN campo, DOBLE chequear TODO
```python
from src.utils.apt_auditor import doble_chequeo_plano
r = doble_chequeo_plano(snap_plano=..., seed_plano=..., plano_id_apt=...)
if not r["ok"]: # NO seguir
```

### 9. Archivos: verificar nombre del servidor contiene `_<plano_id>_<tipo>`
```
Plano 1 (id 1075911) archivos OK: ..._1075911_anverso.pdf, ..._1075911_entero.pdf, ...
Si en plano 1 aparece _1075920_ → ARCHIVOS MEZCLADOS → eliminar y re-subir.
```

### 10. NUNCA reincidir en error ya corregido por el operador
```
Si te corrige X y respondes "ya fixeado" pero vuelve a decir "sigue mal":
  PARAR → grep en código → encontrar regla codificada → aplicar exacto.
```

---

## 📋 CHECKLIST PRE-LLENADO (R14)

Antes de meter datos a APT, validar con `verificar_seed_vs_cajetin_pdf()`:

```python
from src.utils.apt_auditor import verificar_seed_vs_cajetin_pdf

# Leer cajetín del planof.pdf (con fitz o Vision)
cajetin = extraer_cajetin_planof(pdf_path)
seed = meta["datos_apt"]["plano"]

r = verificar_seed_vs_cajetin_pdf(seed_plano=seed, cajetin_extraido=cajetin)
if r["errores"]:
    raise ValueError(f"Seed no matchea PDF: {r['errores']}")
```

Verifica:
- ✅ descripcion seed == descripcion PDF
- ✅ area_real seed ≈ area_real PDF (tolerancia 0.1%)
- ✅ protocolo.tomo/folio matchea
- ✅ numero_entero matchea
- ⚠️ profesional_carne matchea (warning si no)

---

## 📋 CHECKLIST POST-LLENADO (R15 + R17)

Después de llenar bP1-bP7 de un plano:

```python
from src.utils.apt_auditor import (
    doble_chequeo_plano,
    doble_chequeo_contrato_multi_plano,
)

# 1. DOBLE CHEQUEO INDIVIDUAL por plano
for plano_id, num_exp in [(1075911, "RDF-2026-005"), (1075920, "SEG-2026-002")]:
    snap = capturar_snapshot_plano(page, plano_id)
    r = doble_chequeo_plano(
        snap_plano=snap,
        seed_plano=db.get_seed(num_exp),
        plano_id_apt=plano_id,
        propietarios_por_finca=db.get_propietarios(),
    )
    if not r["ok"]:
        alertar_y_no_enviar(r["errores"])

# 2. CROSS-CHECK CONTRATO vs PLANOS (R1+R3)
cross = doble_chequeo_contrato_multi_plano(
    planos_snap=todos_los_snapshots,
    contrato_bc7=leer_bc7(page),
)
if not cross["ok"]:
    alertar(cross["errores"])
    # Si cambió un bP1, recalcular bC7 + GUARDAR contrato
```

---

## 🚀 ENVIAR AL CFIA (paso final irreversible)

```python
# Solo después de que TODOS los doble chequeos pasen:
page.evaluate("() => document.querySelector('#BtnEnviarAgrimensura').click()")
# Modal: "¿Desea enviar el plano a revisión? ¡No podrá revertir esto!"
# Click "¡Si, enviarlo!"
# Espera 5-10s
# Modal éxito: "El contrato fue enviado con éxito"
#              "El plano X enviado al CFIA con éxito" (uno por plano)

# Persistir estado:
for num_exp in expedientes_del_contrato:
    db.cambiar_estado(exp_id, "presentado_apt_r1", actor=..., detalles=...)
```

---

## 🐛 ERRORES TÍPICOS Y SU CAUSA (referencia rápida)

| Síntoma                                              | Causa probable                              | Fix |
|------------------------------------------------------|---------------------------------------------|-----|
| bP1 rojo sin modal de error                          | `tamanno` vacío                             | Setear `#ddlTamanno` |
| "Tipo Uso debe indicarse"                            | naturaleza no mapeada                       | Usar tabla R12 |
| "Monto total CFIA debe ser múltiplo de 100"          | usaste pagado en lugar de TOTAL antes desc  | Total CFIA = 1600 (no 1504) |
| "Número de entero usado en otro plano"               | mismo N° en planos hermanos                 | Cada plano su N°; o DELETE+RE-ADD |
| Archivos no se suben (`#file` no encontrado)         | sin plano cargado en pantalla               | navegar con `cargarPlano()` primero |
| Bot dice `[OK]` pero APT muestra rojo                | Bug-1: no verifica visual                   | Usar `doble_chequeo_plano()` |
| bC7 inconsistente con bP1                            | R18: cambió bP1, no actualizaste bC7        | Recalcular y GUARDAR bC7 |
| Plano 2 hereda datos del plano 1                     | navegación sin click correcto               | usar `cargarPlano(contrato, plano_id)` |
| `area_predio` doble del esperado                     | R1 vieja: sumaste por plano                 | Usar `consolidar_areas_contrato()` (dedup) |

---

## 🧰 FUNCIONES REUTILIZABLES — APIs públicas

### `src/utils/area_consolidator.py`
```python
consolidar_areas_contrato(expedientes) → dict con area_real, area_predio, fincas_unicas
consolidar_area_registro_plano(fincas) → float
aplicar_consolidacion_a_datos_apt(datos_apt, totales) → recalcula bC7
encontrar_hermanos_de_contrato(db, numero_expediente) → list[exp]
```

### `src/utils/apt_auditor.py`
```python
parsear_nombre_servidor_apt(nombre) → {plano_id, tipo}
verificar_archivos_subidos_por_plano(archivos_actuales, plano_id_esperado) → resultado
verificar_titulares_vs_fincas(fincas_bp2, titulares_bp4, propietarios_por_finca) → resultado
verificar_seed_vs_cajetin_pdf(seed_plano, cajetin_extraido) → resultado
doble_chequeo_plano(...) → consolidado de todas las verificaciones
doble_chequeo_contrato_multi_plano(planos_snap, contrato_bc7) → R1+R3 cross-check
```

---

## 📚 CASO DE REFERENCIA — VICTOR #2

**Trámite APT 1258460 enviado al CFIA el 2026-05-12** — usar como caso canónico.

### Estructura
- 1 contrato APT con 2 planos hermanos
- Plano 1 RDF-2026-005 (reunión) — fincas 642038 + 161099, propietarios María + Rodolfo SA
- Plano 2 SEG-2026-002 (segregación) — finca 161099, propietario Rodolfo SA

### Valores correctos (de referencia)
```yaml
bC7:
  area_real:    4848.30   # 2751.30 + 2097.00
  area_predio: 10582.81   # 2500 + 8082.81 (fincas únicas, NO 18665.62)
  max_planos:        2
  honorarios:   569370    # 2 × ¢284,685 (mínimo legal rural × 2)

plano_1:
  descripcion:   "ALEPOA (1)"
  area_real:     2751.30
  area_registro: 10582.81  # suma fincas del plano
  tamanno:       1        # 22x32
  tipo_uso:      3        # SOLAR Y CASA
  tipo_zona:     2        # rural
  tipo_coord:    3        # CRTM05
  fincas:        [642038, 161099]
  titulares:     [María 109400768, Rodolfo SA 3101155297]
  entero:        661177998 / total_reg 15000 / total_cfia 1600 / pagado 17020 / cit 300

plano_2:
  descripcion:   "ALEPOA (2)"
  area_real:     2097.00
  area_registro: 8082.81
  tamanno:       1        # 22x32
  tipo_uso:      35       # AGRICULTURA
  tipo_zona:     2        # rural
  fincas:        [161099]
  titulares:     [Rodolfo SA 3101155297]   # SOLO uno
  entero:        661178420 / total_reg 15000 / total_cfia 1600 / pagado 17020 / cit 300
```

---

## ❌ ERRORES QUE COMETÍ HOY (y NO debo repetir)

> Pegar tu error aquí futuro Claude — para que la próxima sesión los lea.

1. **No consulté `seed_builder.py:263`** (que decía `monto_pagado = entero.monto_tasado`)
   → Reinventé mal, puse el monto debitado, el operador corrigió 3 veces.

2. **Sumé areas por plano sin deduplicar fincas**
   → `bC7.area_predio = 18665.62` en lugar de `10582.81` (161099 contada 2 veces).

3. **Asumí "elegí" el tamaño 22x32** sin consultar el PDF
   → El operador: "se saca del plano.pdf".

4. **Agregué a María como titular del plano 2 sin necesidad**
   → El operador: "solo 1 titular".

5. **No revisé archivos subidos post-upload**
   → Plano 1 tenía archivos del plano 2 mezclados.

6. **Corregí un campo y no revisé los relacionados**
   → Cambié `area_registro` plano 1 pero no actualicé `bC7.area_predio`.

7. **Reporté `[OK]` sin verificar visualmente**
   → Bug-1: bot marcaba completo lo que APT mostraba rojo.

8. **No leí las reglas en BD antes de empezar**
   → 28 reglas activas ya existían y las ignoré.

**Patrón común**: confiar en mi intuición > consultar lo que ya existe.

---

## 🎓 META-REGLAS (lo más importante)

### 1. El operador es la fuente de verdad
Si dice "esto está mal", la regla operativa es lo que él dice, no mi interpretación lingüística.

### 2. Cuando el operador corrige, releer código antes de re-implementar
```bash
grep -rn "<palabra_clave>" src/
sqlite3 data/catastro.db "SELECT * FROM apt_memoria_operador WHERE descripcion LIKE '%<palabra>%'"
```

### 3. Si una regla nueva sale, debe quedar en 3 lugares
- Código (`src/utils/`) con función reutilizable
- Tests (`tests/`) que reproducen el caso
- Docs (este playbook + `PROTOCOLO_PRE_VUELO.md` + `apt_memoria_operador`)

### 4. Sin doble chequeo, no se envía al CFIA
Aunque "se vea bien", ejecutar las funciones del módulo `apt_auditor.py` y validar `ok=True`.

### 5. NUNCA reincidir
Reincidir es señal de que NO leí lo que ya existía. Pausar, releer, después actuar.

---

## 📞 SI ALGO SALE MAL

1. **Capturar snapshot DOM** del plano problemático (`page.evaluate()` listando inputs)
2. **Comparar con caso de referencia** (VICTOR #2 valores arriba)
3. **Buscar regla relacionada en BD** `apt_memoria_operador`
4. **Si el error es nuevo (no en este playbook)**: documentarlo ANTES de avanzar:
   ```bash
   # Agregar regla nueva
   sqlite3 data/catastro.db "INSERT INTO apt_memoria_operador (tipo, patron, descripcion, operador, activa) VALUES ('regla', '<patron_corto>', '<descripcion_completa>', 'claude', 1)"
   ```
5. **Actualizar `PROTOCOLO_PRE_VUELO.md`** con el caso
6. **Agregar test en `tests/test_apt_auditor.py`** o donde corresponda

---

**Fin del playbook. Si llegaste hasta acá: estás listo para tocar APT con seguridad.**

---

## 🔄 FLUJO APT R2 — Segunda ronda con visado municipal

> Cuando el plano sale "Defectuoso" en R1 *o* viene de muni con visado emitido,
> hay que subir anverso corregido + visado a APT y volver a firmar con FD.

### Pre-requisitos en la carpeta del expediente

```
<carpeta_expediente>/
├── 01_Campo/
│   └── amberso.pdf       ← anverso CORREGIDO post-muni
├── 03_Muni/
│   └── visado.pdf        ← visado emitido por la muni (PDF firmado)
└── 02_Oficina/
```

Y en BD: `meta.apt_tramite` ya seteado (de R1).

### Comando

```bash
catastro-bot apt-r2 SEG-2026-003

# Overrides opcionales si los archivos están con otro nombre:
catastro-bot apt-r2 SEG-2026-003 --anverso ruta/anverso_v2.pdf --visado ruta/visado.pdf
```

El bot:
1. Localiza `amberso.pdf` (corregido) y `visado.pdf` automáticamente
2. Pide confirmación del operador
3. Sube ambos archivos al portal APT del trámite existente
4. NO firma con FD (token físico) — eso queda al operador

### Después de subir

```
ACCIÓN MANUAL DEL OPERADOR:
  1. Verificar archivos visualmente en el portal APT
  2. Firmar con Firma Digital (token físico)
  3. Confirmar en WhatsApp: 'FD R2 SI'
     (workflow avanza a INSCRIBIENDO)
```

### Watchdog Chrome (defensa en profundidad)

```bash
# Monitorea CDP 9222 y relanza Chrome si cae (proceso largo)
python -m src.utils.healthcheck --interval 60

# O healthcheck HTTP one-shot:
catastro-bot health
curl http://localhost:9223/health
```

---

## 🏛️ FLUJO MUNI SAN RAMÓN — Resumen completo

> Aprendido 2026-05-13 con TILMAN (SEG-2026-003 + SEG-2026-004 enviados al Google Form de Muni San Ramón).

### Setup único (UNA sola vez por máquina)

```bash
# 1. Lanzar Chrome del bot (perfil dedicado data/temp/chrome_profile_apt)
python tools/start_chrome_bot.py

# 2. En el Chrome del bot que se abre, ir a https://mail.google.com
#    y loguear con la cuenta del topógrafo (ej topografiahrh@gmail.com)
#    → sesión queda persistida para siempre
```

### Por cada plano que va a la muni

```bash
# Paso 1 — Armar paquete PDF combinado (minuta + imagenminuta + plano + carta_agua)
catastro-bot muni paquete SEG-2026-003
   # Genera 02_Oficina/<N° citas>.pdf (ej "2025 - 81701 - C.pdf")

# Paso 2 — Llenar form + subir archivos (todo menos click Enviar)
catastro-bot muni enviar SEG-2026-003
   # Hace automáticamente:
   #   - Verifica paquete PDF existe
   #   - Construye URL pre-llenada (apt_tomo, asiento, finca, vértices, etc.)
   #   - Abre form en Chrome del bot (asume sesión Gmail logueada)
   #   - Sube DOCUMENTOS (PDF) al campo correcto via picker
   #   - Sube Archivo Shape (Derrotero.zip) usando FileChooser (bypass accept)
   #   - Doble chequeo de todos los campos
   #   - Avisa al operador para click manual del Enviar

# Paso 3 — Operador hace click manual en "Enviar"
#   (Google bloquea click programático en acciones irreversibles —
#    correcto operativamente)
```

### Datos necesarios en metadata del expediente

```yaml
apt_tomo:                "2025"           # de la minuta CFIA
apt_asiento:             "81701"          # de la minuta CFIA
apt_tramite:             "1223951"        # del contrato APT
apt_fecha_minuta:        "2026-04-20"     # fecha presentación
area_m2:                 "6825.57"
vertices:                "1-2-3-4"        # LISTA con guiones, NO cantidad
distrito:                "05 Piedades Sur"
tipo_acceso:             "Ruta Cantonal"  # del visor de SR
carne_topografo:         "IT 10676"       # raw — bot lo convierte a "it-10676"
correo_topografo_muni:   "topografiahrh@gmail.com"
fincas_muni:             [...]            # opcional, sino se infiere de datos_apt
```

### Errores comunes del flujo muni y su fix

| Síntoma                                       | Causa                                          | Fix |
|-----------------------------------------------|------------------------------------------------|-----|
| Form pide login al abrir                      | Sesión Gmail no persistida en Chrome del bot   | Setup único arriba |
| Campos prefill no aparecen                    | Abrir link sin sesión → Google descarta entries | Loguear primero, abrir después |
| `set_input_files` falla con ZIP               | accept del input restringe a PDF/image          | Usar `expect_file_chooser()` |
| Click "Añadir archivo" no abre picker         | `evaluate-click` bloqueado                      | `page.mouse.click(coords)` |
| Múltiples pickers superpuestos confunden      | Pickers viejos no se cerraron                   | `cerrar_pickers_residuales()` |
| Click "Enviar" no envía                       | Google bloquea todos los clicks programáticos   | Operador hace click manual |
| Vértices `4` rechazado                        | Form espera LISTA, no cantidad                  | `1-2-3-4` con guiones |
| Finca `629270` no acepta                      | Form espera formato `prov num-derecho`          | `2 629270-000` |
| Vértices SEG2 distintos a SEG1                | Cada plano tiene SUS vértices a vía             | No copiar — verificar por plano |

### Notificar al cliente sobre la respuesta muni

```bash
# Morosidad — extrae monto del IMAP automáticamente (busca último email MOROSIDAD)
catastro-bot muni notificar-cliente SEG-2026-003 --tipo morosidad
   # → muestra mensaje + pide confirmación + envía por WhatsApp

# Morosidad con monto explícito (skip IMAP)
catastro-bot muni notificar-cliente SEG-2026-003 --tipo morosidad --monto 125000 --no-imap -y

# Aprobado (visado emitido)
catastro-bot muni notificar-cliente SEG-2026-003 --tipo aprobado -y

# Rechazado con motivo
catastro-bot muni notificar-cliente SEG-2026-003 --tipo rechazado --motivo "Acceso no coincide" -y

# Dry-run: solo imprime el mensaje, no envía
catastro-bot muni notificar-cliente SEG-2026-003 --dry-run
```

Compositores reutilizables: `src/utils/muni_notificacion.py` exporta
`componer_mensaje_morosidad`, `componer_mensaje_aprobado`,
`componer_mensaje_rechazado` (todos puros — fáciles de testear y reusar
desde scheduler/workflow).

### Funciones del bot reutilizables (`src/utils/muni_uploader.py`)

```python
from src.utils.muni_uploader import (
    subir_archivo_a_picker,       # PDF (es_zip=False) o ZIP (es_zip=True)
    doble_chequeo_form_muni,      # verifica campos pre-llenados
    cerrar_pickers_residuales,    # limpia estado entre uploads
    coords_boton_picker,          # x,y del botón Añadir archivo
    coords_examinar_picker,       # x,y reales del botón Examinar (multi-iframe)
)
```

Y la URL pre-llenada:
```python
from src.agents.municipality_agent import MunicipalityAgent
agent = MunicipalityAgent(db, creds)
url = agent.construir_url_formulario(expediente_id)  # formato correcto de todos los campos
```

---

**Estado final post-TILMAN (2026-05-13)**:
- 50+ reglas activas en `apt_memoria_operador`
- Módulos: `area_consolidator`, `apt_auditor`, `reglas_consulta`, `muni_paquete`, `muni_uploader`
- Subcomandos CLI: `crear`, `extraer`, `apt-crear/plano/guardar`, `lote`, `muni`, `reglas`
- 1 trámite APT enviado a CFIA (VICTOR #2 / 1258460) + 2 segregaciones a Muni San Ramón (TILMAN / 1223951)

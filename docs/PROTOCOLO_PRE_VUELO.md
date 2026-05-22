# 🛫 PROTOCOLO DE PRE-VUELO PARA LLENAR APT

> **Para futuras sesiones de Claude trabajando con este bot.**
>
> Aprendido el 2026-05-12 con VICTOR #2 después de cometer varios errores
> evitables. El operador (Luis Rojas Herrera) pidió expresamente:
>
> > "los datos de errores preguntame como ejercer para la proxima tengas
> > estos errores en tu memoria si no esto no sirve de nada"
>
> Este documento es el **antídoto** al olvido entre sesiones.

---

## ⚠️ REGLA DE ORO

**ANTES de inventar reglas o asumir defaults, hacer 3 consultas obligatorias:**

```bash
# 1. Reglas activas en BD
sqlite3 data/catastro.db "SELECT patron, descripcion FROM apt_memoria_operador WHERE activa=1"

# 2. Reglas en código (comments del agente)
grep -n "REGLA\|IMPORTANTE\|APT espera" src/agents/apt_agent.py src/utils/seed_builder.py

# 3. Este documento + reglas_oficina_aprendidas.md
cat docs/PROTOCOLO_PRE_VUELO.md docs/reglas_oficina_aprendidas.md
```

**Si una regla ya está codificada, USARLA. No reinventarla.**

---

## Checklist de pre-vuelo PARA CADA PLANO

Antes de tocar APT, recorrer con el operador:

### Datos básicos del CONTRATO (1 vez)
- [ ] ¿Cuántos planos van al contrato? (define max_planos)
- [ ] ¿Mismo propietario en todos o distintos? (multi-propietario permitido en bP1)
- [ ] ¿Hay exoneración de honorarios?

### Datos POR PLANO
- [ ] **Tamaño físico**: solo se usan 2 → `22x32 (código 1)` o `32x44 (código 2)`.
      Inferir de las dimensiones del PDF (ver tabla abajo).
- [ ] **Fincas que toca**: TODAS — para reunión van >=2; para segregación normalmente 1.
- [ ] **Naturaleza RNP**: define `tipo_uso` APT (ver tabla abajo).
- [ ] **Planos catastrados a modificar**: lista `A-NNNNNN-AAAA` (van a bP5).
- [ ] **Zona urbana/rural**: automático por área (umbral 2,000 m²); si urbana, pedir letra (A-E).

### Datos del ENTERO (uno por plano, ÚNICO por plano dentro del contrato)
- [ ] **N° entero**: 9 dígitos del comprobante BCR
- [ ] **Monto total Registro** (timbre 001): debe ser múltiplo de 100. **Antes** del descuento.
- [ ] **Monto total CFIA** (timbre 038): debe ser múltiplo de 100. **Antes** del descuento.
- [ ] **Monto total pagado**: **EL TASADO TOTAL** (suma sin descuento) → es el `Monto tasado` del PDF, NO el `Monto debitado`.
- [ ] **CIT-NTRIP** (timbre 055): generalmente 300
- [ ] **Fecha pago**: YYYY-MM-DD

---

## 🔑 Tablas críticas (referencia inmediata)

### Tamaño físico → código `#ddlTamanno`

| Código | Tamaño  | Cuándo |
|--------|---------|--------|
| `1`    | 22×32 CM vertical | Planos chicos (PDF ~24×34 cm) |
| `2`    | 32×44 CM vertical | Planos medianos (PDF ~34×46 cm) |
| `3`    | 44×64 CM vertical | (rara vez) |
| `4`    | 64×88 CM vertical | (raro) |
| `5`    | 88×128 CM vertical | (excepcional) |
| `11-15` | versiones apaisadas | (rara vez) |

**Regla operativa de oficina ROJAS HERRERA**: usar solo `1` o `2`.

### Naturaleza RNP → código `#ddlTipoUso`

| Naturaleza típica RNP                              | Código |
|----------------------------------------------------|--------|
| TERRENO DE SOLAR                                   | `3`    |
| SOLAR CON CONSTRUCCIÓN / CONSTRUIDO                | `2`    |
| TERRENO DE SOLAR CON UNA CASA EN EL CONSTRUIDA     | `31` (CONSTRUIDO Y SOLAR) |
| TERRENO DE AGRICULTURA / AGRÍCOLA                  | `35`   |
| TERRENO AGROPECUARIO                               | `101`  |
| BOSQUE                                             | `30`   |
| MONTAÑA                                            | `21`   |
| POTRERO                                            | `19`   |
| INDUSTRIAL                                         | `33`   |
| BALDÍO                                             | `37`   |

**Si no existe en la tabla**, inspeccionar `#ddlTipoUso` en Chrome con:
```javascript
Array.from(document.querySelector('#ddlTipoUso').options).map(o => `${o.value}=${o.innerText}`)
```

### Campos bP6 ENTEROS (panel `#P6`, NO `#C6`)

| Selector APT          | Qué espera                                       |
|-----------------------|--------------------------------------------------|
| `#txtNumEntero`       | 9 dígitos. **Único por plano dentro del contrato** |
| `#txtTotalRegistro`   | Monto TOTAL timbre 001 (antes descuento, múltiplo 100) |
| `#txtTotalCFIA`       | Monto TOTAL timbre 038 (antes descuento, múltiplo 100) |
| `#txtMontoPagado`     | **Monto TASADO total** (suma sin descuento)      |
| `#txtMontoCIT_NTRIP`  | Monto timbre 055 (típicamente 300)               |
| `#FechaPago`          | YYYY-MM-DD                                       |

⚠️ **MUY IMPORTANTE — `txtMontoPagado`**:
El nombre del campo es engañoso. APT **NO** quiere el "monto debitado/pagado realmente",
quiere el **MONTO TASADO TOTAL** (suma de todos los timbres SIN descuento).

Para entero 661177998 de VICTOR #2:
- Monto tasado total:    ₡17,020.00 ← este va en `txtMontoPagado`
- Monto debitado BCR:    ₡16,016.80 ← este NO va a APT
- Descuento aplicado:    ₡ 1,003.20

Esto ya está documentado en `src/utils/seed_builder.py:263`:
```python
"monto_pagado": entero.monto_tasado,  # APT espera total tasado
```
**Antes de tocar este campo, verificar siempre este comment.**

### Códigos APT comunes (`#ddlTipoZona`, `#ddlTipoCoordenada`, etc.)

| Campo                  | Valor      | Significado                       |
|------------------------|------------|-----------------------------------|
| `#ddlTipoZona`         | `2`        | RURAL (área >= 2000 m²)           |
| `#ddlTipoZona`         | `3`        | URBANO (área < 2000 m²)           |
| `#ddlTipoCoordenada`   | `3`        | CRTM05 (siempre — regla legal CR) |
| `#ddlTipoPlano`        | `27`       | Plano Simple Modificatorio        |
| `#ddlTipoUbicacion`    | `5-10`     | Solo URBANO: 5=A, 6=B, 7=C, 8=CH, 9=D, 10=E |

---

## 🎯 Reglas operativas de oficina (R1-R6) — siempre aplicar

### R1 (CORREGIDA 2026-05-12 v2): Áreas del CONTRATO multi-plano (bC7)

⚠️ **VERSIÓN VIEJA INCORRECTA** (suma `area_registro` por plano):
> Esto **doble-cuenta** fincas que aparecen en varios planos hermanos.
> Por eso fallé en VICTOR #2 dando `area_predio = 18,665.62` cuando lo
> correcto era `10,582.81`.

**VERSIÓN CORRECTA**:

- `area_real`   = **SUMA de `plano.area_real` de cada plano hermano**
  (cada plano catastra un polígono distinto — no hay solapamiento real)

- `area_predio` = **SUMA de `area_registro` de las FINCAS ÚNICAS del contrato**
  (deduplicar fincas que aparecen en varios planos)

**Caso real VICTOR #2**:
- Plano 1 (reunión) toca fincas 642038 (2,500) + 161099 (8,082.81)
- Plano 2 (segregación) toca finca 161099 (8,082.81 — la misma)
- Fincas únicas en el contrato: 642038 + 161099 = **2 fincas**
- `bC7.area_predio = 2,500 + 8,082.81 = 10,582.81 m²` ✓
  (NO 18,665.62 que es contar 161099 dos veces)

**Implementación**: `src/utils/area_consolidator.py::consolidar_areas_contrato()`
ahora dedupliica fincas por número y devuelve `fincas_unicas`.

### R2: Honorarios del CONTRATO multi-plano
- Pasar TODOS los planos a `HonorariosInput.planos`
- Total = SUMA de `precio_final` de cada plano (con su mínimo legal)

### R3: Conteo de planos del CONTRATO
- `max_planos` = N hermanos
- `n_planos_catastrar` = N hermanos
- Default `1` solo para uniplano

### R4: Área registro del PLANO multi-finca (bP1)
- Si el plano modifica varias fincas (reunión, segregación múltiple),
  `area_registro` = SUMA de `area_registro` de TODAS las fincas

### R5: Multi-propietario por plano permitido
- Cada bP1 lleva el propietario de la finca de ese plano
- bC1 lleva el "propietario principal del contrato" (decide topógrafo)

### R6: Crear plano N+1 en contrato existente
- Navegar a `/APT2/Contrato/Nuevo?NumContrato=<id>&EsNuevoPlano=1`
- Llenar bP1 → click `GuardarDatosGenerales(<contrato>, 0, 27)`
- **No basta** re-ejecutar `apt-plano`; hay que crear el plano nuevo primero

### R7: bP2 FINCAS para REUNIÓN
- Llenar `#ddlProvinciaFinca` (código provincia)
- Llenar `#txtNumFinca` (número de finca)
- Llenar `#txtDerecho` (típicamente `000`)
- `#ddlDuplicado` (típicamente `0`)
- Click GUARDAR con onclick `GuardarFinca()` (no "Agregar")
- Modal éxito: "Finca de plano guardada con éxito"
- Para reuniones, **repetir por cada finca** que el plano modifica
- Si plano toca 2 fincas, bP2 debe tener 2 entradas en la tabla

### R8: Enteros únicos por plano dentro del contrato
- APT rechaza si intentás guardar un entero ya usado en otro plano del mismo contrato
- **Cada plano lleva SU propio N° de entero** (el PDF que el topógrafo pagó)

### R9: bP4 TITULARES — uno por cada propietario distinto

> Aprendida 2026-05-12, VICTOR #2 plano 1 — el operador detectó que faltaba.

Cuando un plano modifica **MÚLTIPLES fincas con propietarios distintos**,
**bP4 TITULARES debe incluir UN titular por cada propietario distinto**.

**Ejemplo VICTOR #2 plano 1** (reúne 2 fincas):
- Finca 642038 → propietaria MARÍA ALEJANDRA JIMÉNEZ MONESTEL (física, 1-0940-0768)
- Finca 161099 → propietario RODOLFO JIMÉNEZ DE ALAJUELA S.A. (jurídica, 3-101-155297)
- ⇒ bP4 lleva 2 titulares (uno por finca, no se omite ninguno)

**Campos a llenar por titular**:
- `#ddlTipoIdentificacion`: `1`=FÍSICA, `2`=JURÍDICA, etc. (ver tabla abajo)
- `#txtIdentificacion`: cédula con guiones (`3-101-155297`)
- `#ddlTitularidad`: `5`=PROPIETARIO (más común)
- `#txtNombreTitular`: nombre completo. **Para jurídica, todo va aquí** (apellidos vacíos)
- `#txtApellido1Titular`, `#txtApellido2Titular`: solo para FÍSICA
- Click GUARDAR con onclick `GuardarTitular()`
- Modal éxito: "Titular guardado con éxito"

**Tip técnico**: si el panel `#P4` no está visible, forzarlo:
```javascript
document.querySelector('#P4').classList.add('show');
document.querySelector('#bP4').setAttribute('aria-expanded', 'true');
```

Y usar `el.value = X; dispatchEvent('input'/'change'/'blur')` vía JS si
`page.fill()`/`page.select_option()` tira timeout por "no visible".

---

## 🔢 Tablas de códigos críticos descubiertos hoy

### `#ddlTipoIdentificacion`

| value | Tipo                       |
|-------|----------------------------|
| `1`   | FISICA                     |
| `2`   | JURIDICA                   |
| `3`   | MENOR NACIONAL             |
| `4`   | CÉDULA DE RESIDENCIA       |
| `5`   | CARNÉ DE PENSIONADO        |
| `6`   | PASAPORTE                  |
| `7`   | CARNÉ DE REFUGIADO         |
| `8`   | CARNÉ DE SEGURO SOCIAL     |
| `9`   | LICENCIA DE CONDUCIR       |
| `11`  | DIMEX                      |
| `12`  | NITÉ                       |

### `#ddlTitularidad`

| value | Titularidad        |
|-------|--------------------|
| `1`   | CONCESIONARIO      |
| `2`   | ADMINISTRADOR      |
| `3`   | POSEEDOR           |
| `4`   | ARRENDATARIO       |
| `5`   | **PROPIETARIO** (más común) |
| `6`   | PERMISUARIO        |
| `7`   | PATRIMONIO         |

---

## 🚨 R10 — Modificar enteros: DELETE + RE-ADD (no editar in-place)

> Aprendida 2026-05-12 (3 veces) — el operador me corrigió 3 veces que
> el monto estaba mal. La causa fundamental: traté de EDITAR un entero
> existente, pero APT no acepta edits parciales.

**Síntoma**: cambias `#txtMontoPagado` y das GUARDAR → modal:
> "El número de entero está siendo utilizado para otro plano"

**Causa**: APT detecta que el N° de entero está "duplicado" — pero
realmente es el mismo entero del mismo plano. El error es que APT
ve dos copias: la guardada anteriormente y la nueva que intentás guardar.

**Solución correcta** (procedimiento exacto):

```python
# 1. Forzar bP6 expandido (puede estar collapse)
page.evaluate("() => document.querySelector('#P6').classList.add('show')")

# 2. Click EliminarEnteros (link con onclick específico)
#    Onclick: EliminarEnteros('<token>', <id>)
page.evaluate("""() => {
    const a = Array.from(document.querySelectorAll('a, button'))
        .find(l => (l.getAttribute('onclick') || '').startsWith('EliminarEnteros('));
    if (a) a.click();
}""")

# 3. APT muestra modal swal2 de confirmación tipo:
#    "¿Está seguro de eliminar?" → botón "Sí, eliminarlo!"
page.evaluate("""() => {
    const modal = document.querySelector('.swal2-popup');
    const btn = Array.from(modal.querySelectorAll('button'))
        .find(b => /sí|si|eliminar/i.test(b.innerText));
    if (btn) btn.click();
}""")

# 4. Esperar y aceptar modal de éxito
time.sleep(3); agent._aceptar_modal_swal(page)

# 5. Llenar form ahora vacío con TODOS los campos correctos
setVal('#txtNumEntero',      '661177998')   # mismo número OK ahora
setVal('#txtTotalRegistro',  '15000')        # múltiplo de 100
setVal('#txtTotalCFIA',      '1600')         # múltiplo de 100
setVal('#txtMontoPagado',    '17020')        # ⚠️ TASADO sin descuento
setVal('#txtMontoCIT_NTRIP', '300')
setVal('#FechaPago',         '2026-05-12')

# 6. Click GuardarEnteros() → modal "Entero guardado con éxito"
```

**Triple confirmación del operador (3 veces)**:

> "el monto total pagado del entero es el total sin descuento"
> "el monto de entero el total pagado sigue mal esta con descuento es el total sin descuento"

→ **`#txtMontoPagado` = monto TASADO = ₡17,020.00 (NO ₡16,016.80 que es el debitado)**

---

## 🚨 R11 — Cuándo estoy en qué plano (anti error humano del bot)

> Aprendida 2026-05-12 — confundí el plano actual y le puse N° de entero
> del plano 1 al plano 2 (rechazó). El bot no debe asumir, debe leer.

**Antes de tocar CUALQUIER campo**, verificar siempre:

```python
desc = page.evaluate("() => (document.querySelector('#txtDescripcion') || {}).value || '?'")
# Si esperabas plano 1 pero ves "ALEPOA (2)" → NO seguir
```

**Para navegar a un plano específico de un contrato**:

```python
# Click el link con onclick=cargarPlano(<contrato>, <plano_id>)
page.evaluate(f"""() => {{
    const links = document.querySelectorAll('a');
    for (const l of links) {{
        if ((l.getAttribute('onclick') || '').includes(
            'cargarPlano({contrato_id},{plano_id})'
        )) {{ l.click(); return; }}
    }}
}}""")
```

Los `plano_id` se descubren inspeccionando el listado del tab planos.
Para VICTOR #2 (contrato 1258460):
- Plano 1 (ALEPOA 1): plano_id = 1075911
- Plano 2 (ALEPOA 2): plano_id = 1075920

---

## 🚨 R12 — Tamaño físico se infiere del PDF (no hay que adivinar)

> Aprendida 2026-05-12 — yo "elegí" 22x32 sin consultar las dimensiones
> reales del PDF. El operador confirmó: "se saca del plano.pdf".

**Cómo extraer el tamaño físico del plano**:

```python
import fitz
doc = fitz.open(plano_pdf_path)
page = doc[0]
ancho_cm = page.rect.width  * 2.54 / 72
alto_cm  = page.rect.height * 2.54 / 72
doc.close()
# Mapear a código #ddlTamanno:
#   ~24×34 cm  → código 1  (22×32 CM)
#   ~34×46 cm  → código 2  (32×44 CM)
#   etc.
```

**Regla de oficina**: solo se usan **2 tamaños** en la práctica: `22x32` y `32x44`.
Si el PDF da otras dimensiones, **consultar al operador** antes de continuar.

---

## 🚨 R13 — Verificar archivos subidos POR PLANO (Bug-1 reaparece)

> Aprendida 2026-05-12 — operador detectó que plano 1 tenía archivos
> del plano 2 (mezclados).

APT renombra los archivos al subirlos con formato:
```
<token>_<plano_id>_<tipo>.<ext>
```

Ejemplo VICTOR #2:
- `639141960687378456_1075911_anverso.pdf` ← plano_id 1075911 (plano 1)
- `639141961167417132_1075920_anverso.pdf` ← plano_id 1075920 (plano 2)

**REGLA**: después de subir, verificar que en cada plano TODOS los
archivos tengan el `plano_id` correcto en el nombre del servidor. Si
un archivo del plano 1 (1075911) contiene `_1075920_` está mezclado.

**Fix si están mezclados**:
```
1. Eliminar TODOS los archivos del plano (link EliminarArchivo)
2. Borrar flags apt_progreso.archivos en BD
3. Re-subir los 3 archivos con los paths correctos de SU subcarpeta
```

**Función reutilizable**: `src/utils/apt_auditor.py::verificar_archivos_subidos_por_plano()`

```python
from src.utils.apt_auditor import verificar_archivos_subidos_por_plano

archivos_actuales = [...]  # leídos de APT
r = verificar_archivos_subidos_por_plano(
    archivos_actuales=archivos_actuales,
    plano_id_esperado=1075911,
)
if not r["ok"]:
    for e in r["errores"]: print(e)
    # NO seguir hasta resolver
```

---

## 🚨 R14 — SIEMPRE revisar los PDFs ANTES de llenar APT

> Aprendida 2026-05-12 — operador: "siempre revisa los pdf que no
> tengas datos erroneos".

**ANTES de meter datos a APT**:

```python
# 1. Leer planof.pdf con fitz/Vision y extraer cajetín
# 2. Cruzar con datos_apt del seed:
from src.utils.apt_auditor import verificar_seed_vs_cajetin_pdf

r = verificar_seed_vs_cajetin_pdf(
    seed_plano=meta["datos_apt"]["plano"],
    cajetin_extraido={
        "descripcion": "ALEPOA (1)",
        "area_real": "2751.30",
        "protocolo_tomo": "24162",
        ...
    },
)
if r["errores"]:
    # ABORTAR — el seed no matchea con el PDF real
    raise ValueError(...)
```

Verifica:
- `area_real` del seed = `area_real` del cajetín (con tolerancia 0.1%)
- `protocolo.tomo/folio` matchea
- `descripcion` matchea
- `numero_entero` matchea
- `profesional_carne` matchea (solo warning)

---

## 🚨 R15 — SIEMPRE revisar el plano después de llenarlo

> Aprendida 2026-05-12 — operador: "siempre realiza una revision de
> todos los datos de todos los planos para verificar que esten bien".

**Después de llenar bP1-bP7 de UN plano y ANTES de pasar al siguiente** o
de enviar al CFIA:

```python
# Snapshot completo del plano
snap = page.evaluate("""() => ({
    bp1: {...todos los campos...},
    fincas: [...],
    titulares: [...],
    enteros: [...],
    archivos: [...],
    iconos: {bP1: ..., ..., bP7: ...},
})""")

# Verificar cada cosa:
assert all(v == "OK" for v in snap["iconos"].values())
verificar_archivos_subidos_por_plano(archivos_actuales=snap["archivos"], ...)
verificar_titulares_vs_fincas(fincas_bp2=snap["fincas"], titulares_bp4=snap["titulares"], ...)
```

---

## 🚨 R16 (CORREGIDA v2) — bP4 TITULARES = propietarios ACTUALES de bP2 FINCAS

> Corregida 2026-05-12 v2 — el operador me corrigió DE NUEVO: plano 2
> tenía 2 titulares pero debe tener solo 1.

**REGLA CORRECTA**: bP4 TITULARES de un plano lleva **solo al/los
propietario(s) ACTUAL(es)** de las fincas que aparecen en bP2 DE ESE
PLANO. No agregar receptores futuros, no agregar propietarios de otros
planos hermanos.

**Caso VICTOR #2 (corregido)**:
- **Plano 1 (reunión)** — fincas 642038 (María) + 161099 (Rodolfo SA)
  → bP4: **María + Rodolfo SA** (2 propietarios distintos en bP2)
- **Plano 2 (segregación)** — finca 161099 (Rodolfo SA)
  → bP4: **SOLO Rodolfo SA** (1 propietario)

**Antes había errado**: agregué a María en plano 2 pensando que ella era
"receptora del lote segregado". **Error**: el receptor futuro NO va en
bP4 del plano de segregación; solo el dueño actual. María aparece en
bP4 del plano 1 porque la 642038 es de ella.

**Función**: `verificar_titulares_vs_fincas()` valida correspondencia 1:1
entre fincas de bP2 (con sus propietarios actuales) y titulares de bP4.

---

## 🚨 R17 — DOBLE CHEQUEO OBLIGATORIO antes de enviar al CFIA

> Aprendida 2026-05-12 — operador: "siempre hacer un doble checheo a
> toda la informacion para asegurarte que se lleno bien".

**ANTES de hacer click en ENVIAR AL CFIA**, ejecutar el doble chequeo
completo. Función codificada:

```python
from src.utils.apt_auditor import (
    doble_chequeo_plano, doble_chequeo_contrato_multi_plano,
)

# 1. Doble chequeo de CADA plano
for plano_id, descripcion, num_exp in lista_planos:
    snap = snapshot_plano(page)  # captura visual de APT
    r = doble_chequeo_plano(
        snap_plano=snap,
        seed_plano=meta["datos_apt"]["plano"],
        cajetin_extraido=leer_pdf_planof(),
        plano_id_apt=plano_id,
        propietarios_por_finca={"642038": "1-0940-0768", ...},
    )
    if not r["ok"]:
        # NO ENVIAR hasta resolver
        for e in r["errores"]: alertar(e)

# 2. CROSS-CHECK contrato (R1+R3): bC7 vs SUMA bP1
cross = doble_chequeo_contrato_multi_plano(
    planos_snap=todos_los_snapshots,
    contrato_bc7=leer_bc7(page),
)
if not cross["ok"]:
    # Indica desincronización entre planos individuales y consolidación
    for e in cross["errores"]: alertar(e)
```

**Caso real cazado por el doble chequeo (VICTOR #2)**: cambié
`area_registro` del plano 1 de 2500 → 10,582.81 (por R4 multi-finca)
pero **olvidé actualizar `bC7.area_predio` del contrato**. El
doble chequeo lo detectó:

```
bC7.area_predio=10,582.81 != SUMA bP1.area_registro=18,665.62
```

**Sin el doble chequeo, se hubiera enviado al CFIA con datos
inconsistentes.**

---

## 🚨 R18 — Cualquier cambio en bP1 dispara re-cálculo bC7

> Corolario de R17.

Si modificás `area_registro` o `area_real` de UN plano en bP1, el bC7
del contrato (que es la SUMA de todos los planos) **queda desactualizado**.

**Procedimiento obligatorio tras tocar bP1**:
1. Actualizar bP1 del plano ✓
2. **Inmediatamente** ir a bC7 del contrato
3. Recalcular `area_real` y `area_predio` como SUMA de todos los planos
4. Click GUARDAR bC7
5. Ejecutar `doble_chequeo_contrato_multi_plano()` para confirmar

---

## ⚠️ Lecciones meta — qué hacer cuando el operador te corrige

> Aprendido 2026-05-12 — el operador me corrigió 3 veces el mismo error
> antes de que yo finalmente lo arreglara. Eso no debe pasar de nuevo.

1. **NUNCA reincidir en el mismo error**.
   Si el operador te dice "esto está mal" y vos lo arreglás incompleto,
   y vuelve a decir "sigue mal" → **PARAR, releer el código del bot,
   buscar la regla codificada que ya existe, y aplicar EXACTAMENTE eso**.

2. **Antes de "inventar" una corrección, hacer `grep` por la palabra clave**.
   Ej: "monto" → `grep -rn "monto" src/utils/seed_builder.py` muestra
   el comment `"monto_pagado": entero.monto_tasado, # APT espera total tasado`.

3. **El operador es la verdad operativa**. Si dice "el monto sin descuento
   es 17020", la regla es 17020. No re-interpretes "monto pagado" según
   el lenguaje normal — usá el lenguaje del operador.

4. **Cuando descubrís un error grande, NO hagas un parche silencioso**.
   Documentá la regla en este archivo Y en BD. La próxima sesión de Claude
   no te tiene en cabeza.

---

## 🤖 Cómo "preguntar" al operador (R-checklist)

> Aprendido del operador 2026-05-12: "preguntame como ejercer para la
> proxima tengas estos errores en tu memoria"

Antes de armar `datos_apt` manualmente (sin Vision API), preguntá esto al
operador y guardá las respuestas en BD:

```
1. ¿Cuántos planos van al contrato? [si > 1: aplicar R1-R6]
2. Tamaño físico de cada plano? [22x32 / 32x44]
3. ¿El plano X reúne varias fincas? Si sí, lista (provincia, número, derecho)
4. Naturaleza RNP de cada finca? [SOLAR / AGRICULTURA / ...]
5. ¿Planos catastrados que modifica? [A-NNNNNN-AAAA list]
6. ¿Honorarios con exoneración?
7. Distancia oficina → sitio (km)? [si > 25: gastos transporte]
```

---

## ✅ Revisión FINAL antes de "ENVIAR A CFIA"

Para cada plano del contrato:

1. **Visual en pantalla**: todos los íconos bP1-bP7 en verde
2. **bP1 GENERALES**: descripción, área real, área registro, tipo zona, tipo uso, **tamaño físico**, coordenadas, vértices
3. **bP2 FINCAS**: lista completa (>=1 finca, todas con su número/derecho)
4. **bP3 SUSTRACCIONES**: solo si aplica (segregación con varios receptores)
5. **bP4 TITULARES**: co-propiedad si aplica
6. **bP5 PLANOS A MODIFICAR**: lista de planos catastrados anteriores
7. **bP6 ENTEROS**:
   - Número correcto (único en el contrato)
   - Total Registro = monto TOTAL timbre 001 (múltiplo 100)
   - Total CFIA = monto TOTAL timbre 038 (múltiplo 100)
   - **Monto Pagado = monto TASADO total (sin descuento)** ← ojo
   - CIT-NTRIP = monto timbre 055
   - Fecha pago
8. **bP7 ARCHIVOS**: 3 archivos por plano:
   - `planof.pdf` (anverso firmado digitalmente)
   - `entero.pdf`
   - `Derrotero.zip`
9. **Cross-check entre planos**:
   - bC7 area_real = SUMA de bP1.area_real de cada plano ✓
   - bC7 area_predio = SUMA de bP1.area_registro de cada plano ✓
   - bC7 max_planos = cantidad de planos ✓
   - bC7 honorarios = SUMA de honorarios por plano ✓

Solo después de los 9 puntos: click ENVIAR A CFIA (irreversible).

---

## 🐛 Errores comunes y su causa

| Síntoma APT                                      | Causa probable                              |
|--------------------------------------------------|---------------------------------------------|
| bP1 rojo después de GUARDAR sin modal de error   | `tamanno` vacío (silent fail)               |
| "Tipo Uso debe indicarse"                        | `tipo_uso` no mapeado para esa naturaleza   |
| "Monto total CFIA debe ser múltiplo de 100"      | Pasaste pagado en lugar de TOTAL antes desc |
| "Número de entero usado en otro plano"           | Mismo N° entero entre planos hermanos       |
| bP2 vacía después de "agregar"                   | `AgregarFinca` no se clickeó, faltan campos |
| Archivos no se suben (`#file` no encontrado)     | Estás en `Contrato/Nuevo` sin `EsNuevoPlano` o sin lápiz de plano clickeado |
| Bot dice `[OK]` pero APT está rojo               | Bug-1: bot no verifica visual; falta tamaño o tipo_uso |

---

## 📂 Archivos del proyecto relevantes

| Archivo                                | Qué contiene                                |
|----------------------------------------|---------------------------------------------|
| `src/utils/seed_builder.py`            | Construye `datos_apt` desde extracciones    |
| `src/utils/area_consolidator.py`       | R1, R2, R3, R4 codificadas                  |
| `src/utils/honorarios_calculator.py`   | Cálculo decreto 17481-MOPT                  |
| `src/utils/ubicacion_cr.py`            | Mapeos provincia/cantón/distrito → códigos  |
| `src/utils/plano_vision_extractor.py`  | Dataclasses CajetinData, RegistroData, EnteroData |
| `src/agents/apt_agent.py`              | Llenado bC1-bC8 + bP1-bP7 + selectores      |
| `tools/run_apt_crear_auto.py`          | Tool que llena el contrato                  |
| `tools/run_apt_plano_auto.py`          | Tool que llena el plano                     |
| `docs/reglas_oficina_aprendidas.md`    | Casos reales con detalle                    |
| **`docs/PROTOCOLO_PRE_VUELO.md`**      | **ESTE archivo**                            |
| BD: `apt_memoria_operador`             | 13+ reglas activas                          |

---

## 📌 Cosas que VICTOR #2 dejó como lección permanente

1. Si te pasan archivos con sufijos numerados (`planof 1.pdf`, `entero 2.pdf`),
   el detector ya tolera eso (`tools/extraer_datos_apt.py::_detectar_archivos`).
2. Los distritos de POAS están mapeados en `ubicacion_cr.py`.
3. PDFs del entero BCR contienen 5 valores que APT necesita (no solo el N°).
4. Plano de reunión + segregación = 1 contrato con 2 planos (no 2 contratos).
5. Cada plano del contrato puede tener propietario distinto en bP1.
6. APT rechaza enteros duplicados entre planos del mismo contrato.
7. El bot puede tener bug que reporta `[OK]` con falso positivo — siempre validar visualmente el ícono.

---

**Si encontrás un error nuevo no listado aquí: AGREGARLO antes de avanzar.
El bot no aprende solo — aprende cuando codificamos lo aprendido.**

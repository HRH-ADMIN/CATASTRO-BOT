# Reglas de oficina aprendidas — registro persistente

> **Importante**: este archivo sobrevive a las sesiones de chat. Cada vez que
> el bot (vía Claude) descubra una regla nueva trabajando con un trámite real,
> debe agregarse acá ADEMÁS de codificarla en `src/` y registrarla en
> `apt_memoria_operador`.
>
> El "autoaprendizaje" del bot vive en 3 lugares:
> 1. **Código en `src/`** — la lógica que aplica la regla
> 2. **Tablas BD** (`apt_memoria_operador`, `metadata.datos_apt`) — datos persistentes
> 3. **Este documento** — el "por qué" en lenguaje humano, para futuras sesiones de Claude

---

## R1 — Áreas del CONTRATO multi-plano

**Aprendida**: 2026-05-12, VICTOR #2 (RDF-2026-005 + SEG-2026-002)

Cuando un contrato APT tiene **MÚLTIPLES planos**:

- **bC7 → "Área real a catastrar"** = SUMA de `area_real` de TODOS los planos
- **bC7 → "Área aproximada del predio"** = SUMA de `area_registro` de TODOS los planos

**Codificada en**: `src/utils/area_consolidator.py::consolidar_areas_contrato()`
**Patrón en BD**: `apt_memoria_operador.patron='areas_contrato_multiples_planos'`

---

## R2 — Honorarios del CONTRATO multi-plano

**Aprendida**: 2026-05-12, VICTOR #2

Cuando un contrato APT tiene **MÚLTIPLES planos**:

- `HonorariosInput.planos` debe recibir TODOS los planos (no solo el principal)
- El total = SUMA de `precio_final` de cada plano (con su mínimo legal individual)
- Si N >= 10 planos, aplica descuento escalonado

**Error típico**: el bot calculaba con 1 solo plano → honorarios a la mitad.
**Caso real**: VICTOR #2 calculó ¢284,685 cuando lo correcto era ¢569,370.

**Codificada en**: `src/utils/area_consolidator.py::aplicar_consolidacion_a_datos_apt()`
**Patrón en BD**: `apt_memoria_operador.patron='honorarios_contrato_multiples_planos'`

---

## R3 — Conteo de planos del CONTRATO multi-plano

**Aprendida**: 2026-05-12, VICTOR #2

Cuando un contrato APT tiene **MÚLTIPLES planos**:

- **bC5 → "N° de planos a catastrar"** = N hermanos (no 1)
- **bC7 → "Máximo de planos del contrato"** = N hermanos (no 1)

El default `1` solo es válido para contratos uniplano.

**Codificada en**: `src/utils/area_consolidator.py::aplicar_consolidacion_a_datos_apt()`
**Patrón en BD**: `apt_memoria_operador.patron='n_planos_catastrar_contrato_multiples_planos'`

---

## R4 — Área registro del PLANO multi-finca

**Aprendida**: 2026-05-12, VICTOR #2 plano de reunión

Cuando un PLANO individual modifica **MÚLTIPLES fincas** (reunión, segregación
con varias fincas madre, etc.):

- **bP1 → "Área según registro"** del plano = SUMA de `area_registro` de
  TODAS las fincas que ese plano modifica.

**Diferente de R1**: R1 suma entre planos hermanos del contrato. R4 suma entre
fincas dentro de UN plano individual.

**Caso real**: plano de reunión RDF-2026-005 toca finca 642038 (2,500 m²) +
finca 161099 (8,082.81 m²) → `area_registro` bP1 = 10,582.81 m² (no 2,500).

**Codificada en**: `src/utils/area_consolidator.py::consolidar_area_registro_plano()`
**Patrón en BD**: `apt_memoria_operador.patron='area_registro_plano_multiples_fincas'`

---

## R5 — Contrato con MÚLTIPLES propietarios distintos (multi-plano)

**Aprendida**: 2026-05-12, VICTOR #2

Cuando un contrato APT tiene varios planos con **propietarios distintos**:
- Plano 1: MARÍA ALEJANDRA JIMÉNEZ MONESTEL (cédula 1-0940-0768, finca 642038)
- Plano 2: RODOLFO JIMÉNEZ DE ALAJUELA S.A. (3-101-155297, finca 161099)

APT permite que **cada PLANO tenga su propio propietario en bP1**, distinto
del propietario del CONTRATO en bC1. El topógrafo decide a quién va el contrato.

**Caso confirmado funcionando**: trámite APT 1258460, contrato con María como
propietaria principal, 2 planos con propietarios distintos en bP1.

## R6 — Crear Plano 2 en contrato existente

**Aprendida**: 2026-05-12, VICTOR #2

Para crear un SEGUNDO plano dentro de un contrato YA guardado:

1. Ir al tab PLANOS del contrato (`#plano-tab`)
2. Hay un link "Nuevo Plano" con href:
   `/APT2/Contrato/Nuevo?NumContrato=<id>&EsNuevoPlano=1`
3. Click ese link (o navegar directo a la URL)
4. Aparece una nueva pantalla bP1 en blanco
5. Llenar bP1 → click `GuardarDatosGenerales(<contrato_id>, 0, 27)` → se crea el plano
6. Una vez creado, aparecen bP2-bP7

**Importante**: NO basta con re-ejecutar `apt-plano`. Si el bot ya tiene un plano
"En Edición" abierto, intentará llenar ese (no crear uno nuevo). Hay que
navegar explícitamente al link "Nuevo Plano".

## Tabla COMPLETA `#ddlTipoUso` (descubierta 2026-05-12)

| value | Texto APT                            |
|-------|--------------------------------------|
| 1     | PARA CONSTRUIR                       |
| 2     | CONSTRUIDO                           |
| 3     | SOLAR                                |
| 4     | CONDOMINIO HABITACIONAL              |
| 5     | CONDOMINIO COMERCIAL                 |
| 6     | URBANIZACION HABITACIONAL            |
| 7     | URBANIZACION COMERCIAL               |
| 8     | CALLE PUBLICA                        |
| 9     | SERVIDUMBRE                          |
| 10    | CALLE COMUN ACCESO                   |
| 11    | TANQUE PARA USOS CONEXOS A Y A       |
| 12    | TANQUE DE AGUA                       |
| 13    | PLANTA DE TRATAMIENTO DE AGUAS       |
| 14    | PARQUE                               |
| 15    | ZONAS VERDES                         |
| 16    | AREA COMUNAL                         |
| 17    | AREA DE JUEGOS INFANTILES            |
| 18    | ACERAS                               |
| 19    | POTRERO                              |
| 20    | TACOTAL                              |
| 21    | MONTAÑA                              |
| 22    | YOLILLAL                             |
| 23    | CULTIVOS VARIOS                      |
| 24    | SALINAS                              |
| 25    | ACUACULTURA                          |
| 26    | BREÑON                               |
| 27    | FRUTALES                             |
| 28    | NICHOS O TUMBAS                      |
| 29    | PARQUEO / ESTACIONAMIENTO            |
| 30    | BOSQUE                               |
| 31    | CONSTRUIDO Y SOLAR                   |
| 32    | POTRERO-MONTAÑA                      |
| 33    | INDUSTRIAL                           |
| 34    | ALAMEDA                              |
| 35    | **AGRICULTURA**                      |
| 36    | REPASTOS                             |
| 37    | BALDÍO                               |
| 38    | MARINA                               |
| 39    | CEMENTERIO                           |
| 40    | PLAZA DE DEPORTES                    |
| 41    | BODEGA                               |
| 42    | OFICINA                              |
| 43    | CENIZARIO                            |
| 99    | NO INDICADO                          |
| 100   | CAMPO DE ATERRIZAJE                  |
| 101   | AGROPECUARIO                         |
| 102   | USO MIXTO                            |

**Mapeo recomendado naturaleza RNP → código APT** (extender `seed_builder.py`):
- "TERRENO DE SOLAR" / "SOLAR" → `3`
- "SOLAR CON CONSTRUCCION" / "CONSTRUIDO" → `2`
- "SOLAR Y CONSTRUIDO" / "CON UNA CASA EN EL CONSTRUIDA" → `31`
- "TERRENO DE AGRICULTURA" / "AGRICOLA" → `35`
- "TERRENO AGROPECUARIO" → `101`
- "BOSQUE" / "MONTAÑA" → `30` / `21`
- "POTRERO" → `19`
- "INDUSTRIAL" → `33`

## Hallazgos técnicos (bugs identificados, ⏳ pendiente arreglo)

### Bug-1: bot reporta "OK bPN" sin verificar visualmente

**Síntoma**: `run_apt_plano_auto.py` imprime `[OK] bP1 GENERALES` después de
llenar campos + click GUARDAR, sin verificar que el ícono de bP1 cambió a
verde en APT. Resultado: la BD local marca completado, APT sigue rojo, y
en el segundo intento hace `[SKIP] bP1 ya está completa` aunque APT está vacío.

**Repro VICTOR #2**: bot reportó bP1-bP6 OK pero APT mostraba `bP1: ¡Faltan datos por añadir!`
y el botón GUARDAR tenía `onclick='GuardarDatosGenerales(1258460,0,27)'` (plano_id=0 = no creado).

**Fix sugerido**:
```python
def _verificar_seccion_verde(self, page, seccion_id: str) -> bool:
    """True solo si la sección muestra fa-check-circle (verde)."""
    icono = page.evaluate(
        f"() => {{ const e = document.querySelector('#{seccion_id} i'); "
        f"return e ? e.className : ''; }}"
    )
    return "check-circle" in icono and "times-circle" not in icono
```

Llamar después de cada GUARDAR. Solo si devuelve `True`, hacer
`marcar_seccion_completa()`. Si `False`, lanzar `APTAnomalyError`.

### Bug-2: campo `tamanno` (tamaño físico plano) — RESUELTO 2026-05-12

**Causa raíz confirmada**: si `tamanno` queda vacío, **APT rechaza el
GUARDAR de bP1 silenciosamente** (sin modal de error). El plano NUNCA
se crea (`plano_id` queda en 0) y los siguientes bPN no aparecen.

Este fue el bloqueo principal en VICTOR #2 — al llenar bP1 sin `tamanno`,
APT rechazó y el bot no detectó que falló (Bug-1).

**Tabla COMPLETA de códigos `#ddlTamanno` (descubierta en APT 2026-05-12)**:

| value | Texto APT      | Orientación |
|-------|----------------|-------------|
| `1`   | 22 X 32 CM     | vertical    |
| `2`   | 32 X 44 CM     | vertical    |
| `3`   | 44 X 64 CM     | vertical    |
| `4`   | 64 X 88 CM     | vertical    |
| `5`   | 88 X 128 CM    | vertical    |
| `11`  | 32 X 22 CM     | apaisado    |
| `12`  | 44 X 32 CM     | apaisado    |
| `13`  | 64 X 44 CM     | apaisado    |
| `14`  | 88 X 64 CM     | apaisado    |
| `15`  | 128 X 88 CM    | apaisado    |

**Tamaños operativos típicos (regla de oficina ROJAS HERRERA)**:
- Planos catastrales pequeños (~1,500-3,500 m²): `1` (22×32 CM vertical)
- Más grandes: `2`, `3`, etc.

**Caso real**: VICTOR #2 — ambos planos (2,751 m² + 2,097 m²) → tamaño 22×32 CM = código `1`.

**Fix de código necesario**: en `seed_builder.py`, agregar default
basado en área (o exigir que el cajetín lo declare):
```python
def _tamanno_por_defecto(area_m2: float) -> str:
    if area_m2 < 5000:    return "1"   # 22 X 32 CM
    if area_m2 < 15000:   return "2"   # 32 X 44 CM
    if area_m2 < 50000:   return "3"   # 44 X 64 CM
    if area_m2 < 150000:  return "4"   # 64 X 88 CM
    return "5"  # 88 X 128 CM
```

### Bug-2b: panel del PLANO bP6 es `#P6` (no `#C6`)

**Aprendido 2026-05-12**: hay confusión entre los IDs de panel del CONTRATO
y del PLANO:

| Sección | Botón header | Panel container |
|---------|-------------|-----------------|
| Contrato bC1 | `#bC1` | `#C1` |
| Contrato bC6 | `#bC6` | `#C6` (CONTROVERSIAS) |
| Plano bP1 | `#bP1` | `#P1` |
| Plano bP6 | `#bP6` | `#P6` (ENTEROS) ← OJO |

El bot tenía un `#C6` hardcoded asumiendo que era el panel del plano. **Es del contrato.**

### Bug-2c: `txtTotalCFIA` y `txtTotalRegistro` esperan MONTO TOTAL (no pagado)

**Aprendido 2026-05-12**: en bP6 ENTEROS del plano, los 5 campos son:

| Campo APT | Qué espera | Tipo de monto |
|---|---|---|
| `#txtNumEntero` | Número de entero BCR | 9 dígitos |
| `#txtTotalRegistro` | Monto TOTAL timbre 001 (antes descuento) | **debe ser múltiplo de 100** |
| `#txtTotalCFIA` | Monto TOTAL timbre 038 (antes descuento) | **debe ser múltiplo de 100** |
| `#txtMontoPagado` | Monto DEBITADO (después descuento) | con decimales |
| `#txtMontoCIT_NTRIP` | Monto timbre 055 (es múltiplo de 100 siempre) | entero |
| `#FechaPago` | Fecha del pago BCR | YYYY-MM-DD |

**Caso real VICTOR #2 (entero 661177998)**:
- Timbre 001 (Registro): total ₡15,000.00 / pagado ₡14,100.00 → `txtTotalRegistro = 15000`
- Timbre 038 (CFIA): total ₡1,600.00 / pagado ₡1,504.00 → `txtTotalCFIA = 1600`
- Timbre 055 (CIT-NTRIP): total ₡300.00 / pagado ₡300.00 → `txtMontoCIT_NTRIP = 300`
- Monto debitado: ₡16,016.80 → `txtMontoPagado = 16016.80`

**Validación APT**: "Monto total CFIA debe ser múltiplo de 100" → si pasás 1504 (pagado), rechaza.

**Codificar**: en `seed_builder.py`, `EnteroData` debe extraer del PDF
los 5 valores específicos (no solo número y monto pagado). El cálculo por
timbre desde el regex existente:
```python
# En validar_pdf_entero o equivalente
totales = {}  # {"001": Decimal, "038": Decimal, "055": Decimal}
# Buscar líneas tipo: "038 TIMBRE R.R.P.CFIA  ₡ 1.600,00  ₡ 96,00  ₡ 1.504,00"
for linea in texto.split("\n"):
    m = re.match(r"(\d{3})\s+([A-Z][\w\.\s]+)\s+₡\s*([\d,.]+)\s+₡\s*([\d,.]+)\s+₡\s*([\d,.]+)", linea)
    if m: codigo, _desc, total, _desc_monto, pagado = m.groups(); ...
```

### Bug-3: `_set_input` SÍ dispara eventos, pero el modal de error es transitorio

**Hipótesis revisada**: el `_set_input` actual ya dispara
`input/change/keyup/blur` + jQuery triggers. **No es el problema** que
inicialmente pensé.

**Hipótesis real**: cuando bP1 tiene un campo inválido (probablemente
`tamanno` vacío — ver Bug-2), APT muestra un modal swal2 de error que
desaparece automáticamente en ~2s, y el bot lo confunde con un modal de
éxito.

**Fix sugerido**: en `_aceptar_modal_swal`, capturar el tipo del modal
(error/warning/success) y propagar el error como `APTAnomalyError`.

---

## Casos límite registrados

### VICTOR #2 — 2 planos / 1 contrato / 2 propietarios distintos

**Detalle**:
- Plano 1 (reunión): cliente = MARÍA ALEJANDRA JIMÉNEZ MONESTEL (cédula 1-0940-0768)
- Plano 2 (segregación): cliente jurídico = RODOLFO JIMÉNEZ DE ALAJUELA S.A. (3-101-155297)
- El contrato APT se llena UNO solo (decisión operativa del topógrafo)
- Quedó como propietaria en bC1 la persona del plano principal (María)

**Pregunta abierta**: ¿APT permite que un contrato tenga planos con propietarios
distintos? ¿O hay que pedir co-propiedad? **Validar con CFIA en la primera prueba.**

---

## Ubicaciones nuevas mapeadas

Cuando aparece un distrito/cantón que `src/utils/ubicacion_cr.py` no conoce,
**SIEMPRE** agregarlo ahí. Hoy se agregó:

```python
("2", "08"): {  # ALAJUELA / POAS
    "SAN PEDRO":       "01",
    "SAN JUAN":        "02",
    "SAN RAFAEL":      "03",
    "CARRILLOS":       "04",
    "SABANA REDONDA":  "05",
},
```

---

## ⚡ Preguntas que el bot DEBE hacer al operador ANTES de llenar un plano

> Aprendido 2026-05-12 del operador: "preguntame como ejercer para la
> proxima tengas estos errores en tu memoria si no esto no sirve de nada".
>
> Preguntar ANTES es mejor que fallar y aprender. Cuando armes un
> `datos_apt` para un caso nuevo, recorré esta checklist con el operador.

### Para el CONTRATO (bC1-bC8)

1. **¿Cuántos planos van a este contrato?** (1, 2, 3...)
   - Si > 1: aplicar reglas R1, R2, R3 (consolidación)
2. **Si son varios planos: ¿comparten propietario?**
   - Sí → bC1 lleva ese propietario
   - No → ¿quién va como propietario principal del contrato? (decisión operativa)
3. **¿Hay exoneración de honorarios?** (sí / no)
4. **¿Distancia desde oficina al sitio (km)?** — para gastos reembolsables si > 25 km

### Para cada PLANO (bP1-bP7) — PREGUNTAR POR PLANO

5. **¿Qué tamaño físico tiene el plano impreso?** (22×32, 32×44, 44×64, etc.)
   - Sin esto → APT rechaza bP1 silenciosamente (ver Bug-2 resuelto)
6. **¿Cuántas fincas modifica este plano?**
   - Si > 1: aplicar regla R4 (sumar areas_registro de todas las fincas)
7. **¿El plano modifica planos catastrados existentes? (¿cuáles?)**
   - Lista de `A-NNNNNN-AAAA` que van a bP5
8. **¿La parcela es urbana o rural?**
   - Auto-decidible por área (umbral 2,000 m²) pero confirmar
   - Si urbana → ¿zona A/B/C/CH/D/E? (define honorarios)
9. **¿Hay terreno quebrado (rural) o dificultad notoria (urbana)?**
   - Multiplica honorarios ×1.50
10. **¿El cajetín dice "DEL ESTADO" o similar?** (poco común, casi siempre no)

### Para CADA FINCA — PREGUNTAR POR FINCA

11. **¿Cuál es la cédula del propietario actual?**
12. **¿Hay anotaciones o gravámenes registrales?**
13. **¿La finca está en zona catastrada?** (lo dice el registro RNP)
14. **¿Naturaleza** según RNP? (TERRENO DE SOLAR / AGRICULTURA / etc.) — define tipo_uso APT
15. **¿Área según registro** en m² (exacto)?

### Para los ARCHIVOS (bP7)

16. **¿El plano firmado digitalmente está disponible (`planof.pdf`)?**
    - Sin firma digital → APT rechaza en validación
17. **¿El derrotero es CRTM05?** (regla legal CR — siempre 3)
18. **¿El derrotero tiene la misma área que el cajetín dentro de tolerancia 1%?**
    - Si no → ALERTAR al topógrafo antes de subir

### Si hay co-propiedad (varios titulares en una finca)

19. **Lista completa de titulares** con cédula, %, dirección, correo
    - Va a bP4 TITULARES

### Si hay segregación

20. **¿Cuál es el tamaño del lote a segregar?** (debe matchear el derrotero del plano de segregación)
21. **¿Quién recibirá el lote segregado?** (puede ser distinto del propietario actual)

---

## Instrucciones para futuras sesiones de Claude

Si al inicio de una sesión te encuentras trabajando con el bot:

1. **Leer este archivo completo** antes de tocar `apt_agent.py` o seeds.
2. **Consultar la tabla `apt_memoria_operador`**:
   ```bash
   sqlite3 data/catastro.db "SELECT patron, descripcion FROM apt_memoria_operador WHERE activa=1"
   ```
3. **Si descubres una regla nueva**, hacer 3 cosas:
   - Codificarla en `src/`
   - Insertar en `apt_memoria_operador`
   - **Agregarla a este archivo** con caso real + fecha
4. **No confiar en `[OK]` del bot** — verificar siempre con inspección DOM
   que el estado real en APT matchea.
5. **ANTES de llenar bP1 de un plano**, recorrer la checklist de
   "Preguntas que el bot DEBE hacer al operador" (sección ⚡ arriba).
   No asumir defaults sin preguntar — los defaults son la causa #1 de fallas.

# CLAUDE.md — Instrucciones para sesiones de Claude

> **Este archivo es lo PRIMERO que debe leer cualquier sesión nueva de Claude
> trabajando en este proyecto.** Tiene precedencia sobre cualquier suposición
> general que traigas de tu entrenamiento.

## 🛑 ANTES DE TOCAR APT, llenar formularios o construir seeds

**LECTURA OBLIGATORIA**, en este orden:

1. **`docs/BOT_PLAYBOOK.md`** — las 10 reglas de oro + checklist completa
2. **`docs/PROTOCOLO_PRE_VUELO.md`** — R1-R18 explicadas con casos reales
3. **`docs/reglas_oficina_aprendidas.md`** — bugs identificados + tablas APT

```bash
# Comando rápido para arrancar
cat docs/BOT_PLAYBOOK.md
sqlite3 data/catastro.db "SELECT patron FROM apt_memoria_operador WHERE activa=1 AND tipo='regla' ORDER BY id"
.venv/Scripts/python.exe -m pytest tests/test_apt_auditor.py tests/test_area_consolidator.py -q
```

## 🧰 Funciones reutilizables (NO reinventar)

```python
# Consolidación contrato multi-plano
from src.utils.area_consolidator import (
    consolidar_areas_contrato,           # R1: dedup fincas únicas
    consolidar_area_registro_plano,      # R4: suma por plano multi-finca
    aplicar_consolidacion_a_datos_apt,   # R2+R3: aplica al seed
    encontrar_hermanos_de_contrato,      # busca expedientes del contrato
)

# Auditoría obligatoria (R13-R17)
from src.utils.apt_auditor import (
    verificar_archivos_subidos_por_plano,   # R13: archivos correctos por plano_id
    verificar_seed_vs_cajetin_pdf,          # R14: cruzar antes
    verificar_titulares_vs_fincas,          # R16: bP4 ↔ bP2
    doble_chequeo_plano,                    # R15+R17: doble chequeo todo-en-uno
    doble_chequeo_contrato_multi_plano,     # cross-check bC7 vs bP1
)
```

## 🎯 Las 10 reglas de oro (resumen)

1. **NUNCA inventar — siempre consultar primero** (BD + código + docs)
2. **`txtMontoPagado` = TASADO** (sin descuento), no debitado
3. **`area_predio` = SUMA fincas ÚNICAS** del contrato (deduplicar)
4. **`tamanno` (bP1) es OBLIGATORIO** — sin él, falla silencioso
5. **bP4 TITULARES = propietarios actuales de bP2** (1:1, no agregar futuros)
6. **Modificar entero = DELETE + RE-ADD** (no editar in-place)
7. **Cada plano del contrato tiene su propio entero**
8. **Después de llenar, DOBLE chequear TODO** (`doble_chequeo_plano()`)
9. **Verificar archivos**: el nombre del servidor debe contener `_<plano_id>_<tipo>`
10. **NUNCA reincidir en error corregido por el operador** — pausar, releer, actuar

## ❌ Errores que evitar (cometidos en VICTOR #2 — no repetir)

| Error | Regla codificada |
|---|---|
| Asumir significado de "monto pagado" | `monto_pagado_entero_es_TASADO_no_DEBITADO` |
| Sumar areas por plano sin dedup | `R1_corregida_fincas_unicas_no_planos` |
| Agregar titular extra "por las dudas" | `R16_corregida_titular_no_doble_para_segregacion` |
| Confiar en `[OK]` del bot sin verificar | `verificar_visual_bp_guardado` |
| Corregir X sin revisar resto | `cuando_corrijas_revisa_todo_de_nuevo` |

## 📌 Caso de referencia canónico

**Trámite APT 1258460 — VICTOR #2** (enviado al CFIA el 2026-05-12).
Valores correctos documentados en `docs/BOT_PLAYBOOK.md` sección "Caso de referencia".

## 🔍 Si encontrás un error/regla nueva

Documentarlo en **3 lugares** antes de avanzar:

1. **Código**: función reutilizable en `src/utils/`
2. **Tests**: caso reproducible en `tests/test_apt_auditor.py` o equivalente
3. **Persistencia**:
   - `sqlite3 data/catastro.db "INSERT INTO apt_memoria_operador..."`
   - `docs/PROTOCOLO_PRE_VUELO.md` (regla)
   - `docs/BOT_PLAYBOOK.md` (resumen + ejemplo)

## 🚦 Estado actual del bot

> Los números cambian día a día. Cuando dudes, consultar directamente:
>
> ```bash
> # Cantidad de reglas activas (BD)
> .venv/Scripts/python.exe -c "import sqlite3; print(sqlite3.connect('data/catastro.db').execute('SELECT COUNT(*) FROM apt_memoria_operador WHERE activa=1').fetchone()[0])"
>
> # Cantidad de tests
> .venv/Scripts/python.exe -m pytest --collect-only -q | Select-Object -Last 1
>
> # Versión de Python en uso (NO confundir con README, que está desactualizado)
> .venv/Scripts/python.exe --version
>
> # Resumen de expedientes
> .venv/Scripts/python.exe tools/catastro_bot.py resumen --apt
> ```

**Snapshot 2026-05-22** (referencia, verificar con los comandos de arriba):
- Python 3.13.13 (Windows)
- ~96 reglas activas en `apt_memoria_operador`
- 1480 tests pytest colectados
- 12 expedientes activos en BD (3 enviados al CFIA esta semana)
- 9 jobs scheduler corriendo
- Backup automático local + Drive activo

## 🧠 Filosofía del proyecto

> El bot aprende cuando el operador lo corrige. Cada corrección debe quedar
> codificada como regla persistente — no en mi cabeza (que se va con el chat),
> sino en código + BD + docs.
>
> Cuando el operador me diga "esto está mal", mi reacción debe ser:
> 1. PARAR de inventar
> 2. Buscar en código existente la regla relacionada
> 3. Si NO existe regla → documentar la nueva
> 4. Si SÍ existe → aplicar EXACTAMENTE lo codificado
>
> Reincidir es el peor pecado.

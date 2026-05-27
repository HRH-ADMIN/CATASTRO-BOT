# tools/oneshots/ — Scripts históricos no enroutados al CLI

Esta carpeta contiene scripts ad-hoc creados durante el desarrollo y la
operación del bot que **no forman parte del CLI público** (`catastro-bot ...`).
Cada uno resolvió un caso puntual en su momento; quedan acá como referencia
histórica + debug.

**No se ejecutan automáticamente.** Tampoco se auditan por ruff/mypy
(excluidos en `pyproject.toml`).

Plan: PLAN_MEJORAS_catastro-bot_3.md Sprint 3 / O-03.
Movidos acá el 2026-05-22 (33 scripts).

---

## Inventario

| Script | Propósito | Categoría | Vigencia estimada |
|---|---|---|---|
| `agregar_titular_bp4.py` | Agrega titular faltante en bP4 de un plano APT | Corrección APT | One-shot, expediente específico |
| `aprender_contrato.py` | Explora la estructura del contrato APT (descubrimiento) | Exploración | Histórica |
| `datos_apt.py` | Dump de datos APT crudos | Debug | Histórica |
| `eliminar_titular_amalia.py` | Eliminó titular específica de un bP4 | Corrección APT | Resuelto, no re-aplicar |
| `enviar_cfia_rdf002.py` | Envío manual de RDF-2026-002 al CFIA | Operativo one-shot | Resuelto |
| `fix_titulares_omar.py` | Fix titulares plano OMAR | Corrección APT | Resuelto |
| `inspect_apt_form.py` | Snapshot del DOM del form contrato | Inspección | Histórica |
| `inspect_apt_full.py` | Snapshot completo APT (todos los bloques) | Inspección | Histórica |
| `inspect_apt_secciones.py` | Mapa de secciones del contrato | Inspección | Histórica |
| `inspect_apt_tab.py` | Inspección por tab del contrato | Inspección | Histórica |
| `inspect_bc3.py` | Inspección del bloque bC3 (citas) | Inspección | Histórica |
| `inspect_bp4_hidden.py` | bP4 elementos ocultos | Inspección | Histórica |
| `inspect_bp4_lista.py` | Lista de titulares bP4 | Inspección | Histórica |
| `inspect_bp4_titulares.py` | DOM de titulares bP4 | Inspección | Histórica |
| `inspect_fincas_bp2.py` | DOM de bP2 (fincas) | Inspección | Histórica |
| `inspect_modal.py` | Modales de confirmación APT | Inspección | Histórica |
| `inspect_plano_dropdowns.py` | Catálogo de #ddl* (tamanno/tipoUso/etc.) | Inspección | Útil — alimenta el código de mapeo |
| `inspect_protocolo.py` | Cajetín del protocolo | Inspección | Histórica |
| `monitor_bp4_manual.py` | Monitoreo en vivo de bP4 mientras se llena | Debug | Histórica |
| `muni_subir_archivos_roesquina.py` | Subida one-shot de archivos al form muni — caso ROESQUINA | Operativo one-shot | Resuelto |
| `muni_subir_roesquina.py` | Submit completo del form muni — caso ROESQUINA | Operativo one-shot | Resuelto |
| `probar_honorarios.py` | Pruebas del calculador de honorarios | Exploración | Reemplazado por `tests/test_honorarios_calculator.py` |
| `run_apt_crear_pasos.py` | Variante "paso a paso" de apt-crear (mostraba prompts) | Histórica | Reemplazado por `run_apt_crear_auto.py` |
| `run_apt_plano_pasos.py` | Variante "paso a paso" de apt-plano | Histórica | Reemplazado por `run_apt_plano_auto.py` |
| `seed_datos_apt_felipe_tios.py` | Seed hardcoded — FELIPE_TIOS | Seed expediente | One-shot |
| `seed_datos_apt_omar.py` | Seed hardcoded — OMAR | Seed expediente | One-shot |
| `seed_datos_apt_rdf.py` | Seed genérico para RDF (rectificaciones) | Seed expediente | Histórica |
| `seed_datos_apt_rovuelt.py` | Seed hardcoded — ROVUELT | Seed expediente | One-shot |
| `seed_datos_apt_seg.py` | Seed genérico para SEG (segregaciones) | Seed expediente | Histórica |
| `test_apt_crear.py` | Test manual antiguo (no de pytest) | Test ad-hoc | Reemplazado por `tests/test_apt_form_filling.py` |
| `test_apt_sesion.py` | Verifica que la sesión APT está viva | Debug | Útil para troubleshooting CDP |
| `test_override_nombre.py` | Override de nombre del topógrafo en seed | Debug | Histórica |
| `verificar_bp4.py` | Verifica que bP4 esté completo | Debug | Reemplazado por `apt_auditor.verificar_titulares_vs_fincas` |

---

## Política de retención

- Revisión **trimestral** (próxima: 2026-08-22).
- Si un script lleva 90+ días sin ejecutarse y no aporta como referencia → `git rm`.
- Si un script demuestra valor recurrente → migrar a `src/utils/` como función
  reutilizable + agregar test + enroutarlo al CLI.

## Lo que NO debería estar acá

- **Tests reales**: van a `tests/` y se corren con pytest.
- **Comandos operativos**: van enroutados a `tools/catastro_bot.py` con su handler.
- **Lógica reutilizable**: vive en `src/utils/`.

Si alguno de los archivos arriba cae en alguna de estas categorías, abrir
PR con la migración correspondiente y eliminar el original.

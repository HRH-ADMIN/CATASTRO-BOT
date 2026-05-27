# src/workflows/_legacy/

Workflows DEPRECATED — históricos, mantenidos como shims de re-export.

## Por qué están acá

Durante el desarrollo inicial del bot se nombraron workflows con los
nombres del trámite catastral en singular o con sufijo distinto:
- `division.py`
- `finca_completa.py`
- `situacion.py`

Más adelante se renombraron a los nombres oficiales del Reglamento 44647:
- `reunion_de_fincas.py` (la división se hace al revés — uniendo fincas)
- `fincas_completas.py` (plural — el trámite procesa varias a la vez)
- `informacion_posesoria.py` (nombre legal correcto del trámite)

Los 3 archivos viejos quedaron como **shims de un solo `import`** para no
romper si alguien todavía hacía `from src.workflows.division import ...`.

En `src/main.py:84-91` (función `build_workflows`) solo se registran los
**5 workflows activos**, no estos shims. Verificado el 2026-05-22:

```bash
grep -rn "from src.workflows.division\|workflows.situacion\|workflows.finca_completa" src/ tests/ tools/
# → 0 referencias (excepto este README y el plan de mejoras)
```

## Cuándo borrarlos definitivamente

Cuando se confirme que ninguna sesión externa (notebooks, scripts ad-hoc,
agentes auxiliares) los importa. Recomendado: 90 días sin tocar acá +
verificación adicional → `git rm` y borrar este `_legacy/`.

Plan referenciado: PLAN_MEJORAS_catastro-bot_3.md Sprint 3 / O-02.

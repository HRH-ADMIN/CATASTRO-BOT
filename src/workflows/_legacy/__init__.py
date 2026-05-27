"""Workflows DEPRECATED — solo re-exportan los workflows activos.

Plan: PLAN_MEJORAS_catastro-bot_3.md Sprint 3 / O-02.
Movidos acá el 2026-05-22 para limpiar el namespace de src.workflows
sin romper imports históricos.

Cada archivo es un shim que apunta al workflow vigente:
  division.py        → reunion_de_fincas.ReunionDeFincasWorkflow
  finca_completa.py  → fincas_completas.FincasCompletasWorkflow
  situacion.py       → informacion_posesoria.InformacionPosesoriaWorkflow

Si algún código externo todavía importa `from src.workflows.division import ...`,
fallará. La migración correcta es importar el workflow nuevo directamente.
"""

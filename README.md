# catastro-bot

Sistema multi-agente de automatización catastral para Costa Rica
(Ley 6545 — Ley de Catastro Nacional).

Automatiza la inscripción de planos ante el Catastro Nacional (APT) y la
Municipalidad de San Ramón, gestionando comunicación con clientes por
WhatsApp (Green API) con confirmación obligatoria antes de cada acción
irreversible.

## Tipos de plano soportados

- Segregación
- Finca completa
- División
- Rectificación de medida
- Situación

## Stack

- Python 3.11 (Windows)
- Playwright — automatización de APT y municipalidad
- SQLCipher — base de datos local cifrada AES-256
- Windows Credential Manager — almacenamiento de secretos
- Green API — WhatsApp
- Google Drive API — archivos por expediente
- Claude API — análisis de minutas (solo datos técnicos sanitizados)
- APScheduler — tareas periódicas

## Setup

```powershell
python setup.py
python -m src.core.credential_manager   # configurar credenciales
python -m src.main
```

## Garantías de seguridad

- Credenciales en Windows Credential Manager, nunca en archivos
- Base de datos AES-256 (SQLCipher 4)
- IA recibe solo datos técnicos sanitizados (sin nombres, cédulas ni fincas)
- Confirmación WhatsApp obligatoria antes de cada acción irreversible
- Audit log inmutable con cadena de hashes verificable

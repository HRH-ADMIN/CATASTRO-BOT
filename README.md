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

- Python 3.13.13 (Windows; 3.11+ debería funcionar pero el entorno productivo corre 3.13)
- Playwright — automatización de APT y municipalidad
- SQLCipher (planeado) — base de datos local cifrada AES-256
  - En Windows actualmente NO hay wheels de `sqlcipher3-binary`. Se opera con
    `CATASTRO_BOT_DEV_MODE=1` (SQLite plano). Para producción Linux se elimina ese flag.
- Windows Credential Manager / DPAPI machine-scope — almacenamiento de secretos
- Green API — WhatsApp
- Google Drive API (OAuth) — archivos por expediente + backup diario
- Claude API (Anthropic) — Vision para extracción de cajetín; fallback regex con pypdf
- APScheduler — 9 jobs periódicos
- Flask + Waitress — dashboard web local (localhost:9224)

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

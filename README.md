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

### Con `uv` (recomendado — builds reproducibles desde `uv.lock`)

```powershell
pip install uv                          # una vez
uv sync                                 # instala runtime
uv sync --extra dev                     # instala runtime + dev tools (ruff, mypy, pytest-cov)
uv run python -m src.core.credential_manager   # configurar credenciales
uv run python -m src.main
```

### Compat — `pip` clásico

```powershell
python setup.py                         # bootstrap inicial
pip install -r requirements.txt         # autogenerado desde uv.lock
python -m src.core.credential_manager
python -m src.main
```

### Comandos de desarrollo

```powershell
uv run ruff check .                     # lint (no bloquea aún — soft-fail en CI)
uv run ruff format .                    # formatter
uv run mypy src/core                    # type-check estricto en core
uv run pytest --cov=src                 # tests + cobertura
uv lock --upgrade                       # actualizar deps al último compatible
uv export --no-hashes -o requirements.txt   # regenerar requirements.txt
```

## Garantías de seguridad

- Credenciales en Windows Credential Manager, nunca en archivos
- Base de datos AES-256 (SQLCipher 4)
- IA recibe solo datos técnicos sanitizados (sin nombres, cédulas ni fincas)
- Confirmación WhatsApp obligatoria antes de cada acción irreversible
- Audit log inmutable con cadena de hashes verificable

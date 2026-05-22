"""Logger compartido. Los logs no deben contener datos sensibles.

Rotación automática: 10 MB por archivo, 5 archivos de historial.

Formatos soportados:
  - **texto** (default): `2026-05-11T10:00:00 [INFO] catastro.apt: msg`
  - **json**: `{"ts":"...","level":"INFO","logger":"...","msg":"...","extra":{}}`

Configuración:
  - env var `CATASTRO_LOG_FORMAT=json` → activa JSON en consola + archivo
  - env var `CATASTRO_LOG_FILE=catastro.jsonl` → cambia el nombre del archivo
  - env var `CATASTRO_LOG_LEVEL=DEBUG` → cambia el nivel del root

JSON facilita ingest a herramientas como `jq`, ELK, CloudWatch Logs Insights, etc.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone

from config.settings import LOGS_DIR

_LOG_FILE_DEFAULT = "catastro-bot.log"
_MAX_BYTES        = 10 * 1024 * 1024   # 10 MB
_BACKUP_COUNT     = 5
_FMT              = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT         = "%Y-%m-%dT%H:%M:%S"

# Logger raíz del proyecto — configurado una sola vez
_ROOT_LOGGER_NAME = "catastro"
_root_configured  = False


class JsonFormatter(logging.Formatter):
    """Formatter que emite cada log record como una línea JSON.

    Campos:
      ts       — ISO timestamp UTC con timezone
      level    — INFO / WARNING / ERROR / ...
      logger   — nombre del logger (ej. "catastro.apt_agent")
      msg      — mensaje ya formateado (con args resueltos)
      module   — nombre del módulo de Python
      func     — función que emitió
      line     — número de línea
      thread   — id del thread
      exc      — stack trace si hubo excepción (sólo en ERROR/CRITICAL)
      extra    — cualquier kwargs adicionales que el caller pasó via `extra={}`
    """

    _BUILTIN_ATTRS = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc) \
            .isoformat(timespec="milliseconds")
        body: dict = {
            "ts":     ts,
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
            "module": record.module,
            "func":   record.funcName,
            "line":   record.lineno,
            "thread": record.thread,
        }
        # Extras: cualquier atributo que NO sea built-in de LogRecord
        for k, v in record.__dict__.items():
            if k not in self._BUILTIN_ATTRS and not k.startswith("_"):
                try:
                    json.dumps(v)  # serializable?
                    body.setdefault("extra", {})[k] = v
                except Exception:
                    body.setdefault("extra", {})[k] = str(v)
        # Excepción si hubo
        if record.exc_info:
            body["exc"] = self.formatException(record.exc_info)
        return json.dumps(body, ensure_ascii=False, default=str)


def _configure_root() -> None:
    global _root_configured
    if _root_configured:
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    if root.handlers:
        _root_configured = True
        return

    # Nivel desde env
    level_name = os.environ.get("CATASTRO_LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    # Formato: json o text
    formato = os.environ.get("CATASTRO_LOG_FORMAT", "text").lower()
    if formato == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    # ── archivo rotante ──────────────────────────────────────────────
    log_filename = os.environ.get("CATASTRO_LOG_FILE", _LOG_FILE_DEFAULT)
    file_handler = logging.handlers.RotatingFileHandler(
        LOGS_DIR / log_filename,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # ── consola (stderr) ─────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger hijo del logger raíz 'catastro'.

    Todos los loggers del proyecto comparten los mismos handlers
    (archivo rotante + consola).  El nombre resultante es
    'catastro.<name>', lo que permite filtrar por subsistema.
    """
    _configure_root()
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def reset_for_tests() -> None:
    """Limpia config — sólo para tests que necesitan re-configurar formato."""
    global _root_configured
    root = logging.getLogger(_ROOT_LOGGER_NAME)
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _root_configured = False

"""Simulación local de 'APT SESION' — llama iniciar_sesion_manual directamente.

Útil para probar el flujo CDP sin depender de WhatsApp/Green API.
Usa fixtures de tests/ para no requerir credenciales reales.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# Forzar stdout/stderr a UTF-8 para que las notificaciones con emoji no fallen
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Asegurar que src/ está en el path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import FakeCredentialManager, TestDatabase
from src.agents.apt_agent import APTAgent


def main() -> int:
    db_path = Path(__file__).resolve().parents[1] / "data" / "temp" / "test_apt_sesion.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    creds = FakeCredentialManager()
    db = TestDatabase(path=db_path, credentials=creds)
    db.initialize_schema()

    agent = APTAgent(db, creds)

    print(f"[CDP endpoint] {agent._cdp_endpoint}")
    print(f"[CDP disponible] {agent._cdp_disponible()}")
    print()

    notifs: list[str] = []

    def notif(msg: str) -> None:
        print(">>> NOTIF:", msg)
        notifs.append(msg)

    print("[INICIANDO] iniciar_sesion_manual(timeout_min=5)...")
    print()
    try:
        agent.iniciar_sesion_manual(notificar_fn=notif, timeout_min=5)
        print("\n[ÉXITO] iniciar_sesion_manual completado sin error.")
    except Exception as exc:
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        return 1

    print(f"\n[RESUMEN] {len(notifs)} notificaciones enviadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

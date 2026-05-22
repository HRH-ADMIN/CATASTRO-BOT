"""Bot WhatsApp en vivo — catastro-bot.
Ejecutar:  .venv\\Scripts\\python.exe bot_live.py
Log en:    bot_live.log
"""
import contextlib
import logging
import sqlite3
import sys
import time
from pathlib import Path

import requests

# ── logging a archivo + consola ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot_live.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bot_live")

# ── imports del proyecto ──────────────────────────────────────────────────────
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.agents.drive_agent import DriveAgent
from src.agents.folder_manager import FolderManager
from src.agents.whatsapp_commands import WhatsAppCommandRouter


class ProdDB(Database):
    """Usa sqlite3 estandar (sin SQLCipher) — modo dev."""

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


def main():
    cm  = CredentialManager()
    ga  = cm.get_green_api()
    iid = ga["instance_id"]
    tok = ga["token"]

    # ── BD de produccion ──────────────────────────────────────────────────────
    db_path = Path("data/catastro.db")
    db_path.parent.mkdir(exist_ok=True)
    db = ProdDB(path=db_path, credentials=cm)
    db.initialize_schema()
    log.info("BD inicializada: %s", db_path.resolve())

    # Asegurar operador registrado
    try:
        db.crear_usuario(telefono="50663089219", nombre="Alonso",
                         rol="admin", actor="bot_live")
        log.info("Operador 50663089219 registrado")
    except Exception:
        log.info("Operador 50663089219 ya existe en BD")

    # Mostrar estado actual
    exps = db.listar_expedientes()
    log.info("Expedientes en BD: %d", len(exps))
    for e in exps:
        log.info("  [%s] %s | %s", e["numero_expediente"],
                 e["tipo_plano"], e["estado_actual"])

    # ── Agentes ───────────────────────────────────────────────────────────────
    fm    = FolderManager(db)
    drive = DriveAgent(db, cm, files_root=Path("data/files"))

    def _send(phone: str, msg: str) -> None:
        chat_id = phone.lstrip("+") + "@c.us"
        try:
            r = requests.post(
                f"https://api.green-api.com/waInstance{iid}/sendMessage/{tok}",
                json={"chatId": chat_id, "message": msg},
                timeout=15,
            )
            log.info("[->] %s | HTTP %s | %s",
                     phone, r.status_code, msg[:80].replace("\n", " "))
        except Exception as exc:
            log.error("Error enviando a %s: %s", phone, exc)

    router = WhatsAppCommandRouter(
        db=db, credentials=cm,
        drive_agent=drive,
        reply_fn=_send,
        folder_manager=fm,
    )

    # ── Loop principal ────────────────────────────────────────────────────────
    log.info("Bot iniciado. Escuchando en instancia %s...", iid)
    log.info("Numero del bot: +506 6073-5470")
    log.info("Operador autorizado: 50663089219")
    log.info("Envia AYUDA desde tu WhatsApp para probar")

    errores_consecutivos = 0

    while True:
        try:
            r = requests.get(
                f"https://api.green-api.com/waInstance{iid}"
                f"/receiveNotification/{tok}",
                timeout=20,
            )
            errores_consecutivos = 0
        except requests.exceptions.Timeout:
            # Normal en long-polling vacio
            continue
        except Exception as exc:
            errores_consecutivos += 1
            log.warning("Error polling (%d): %s", errores_consecutivos, exc)
            if errores_consecutivos >= 5:
                log.error("Demasiados errores consecutivos, esperando 30s")
                time.sleep(30)
                errores_consecutivos = 0
            continue

        data = r.json()
        if not data:
            continue

        body = data.get("body", {})
        tipo = body.get("typeWebhook", "")
        rid  = data.get("receiptId")

        log.debug("Evento recibido: %s", tipo)

        if tipo == "incomingMessageReceived":
            sender = (body.get("senderData", {})
                      .get("sender", "")
                      .replace("@c.us", ""))
            msg_data = body.get("messageData", {})
            texto = (msg_data.get("textMessageData", {})
                     .get("textMessage", ""))

            if texto:
                log.info("[<-] De %s: %s", sender, texto[:120])
                try:
                    procesado = router.handle(sender_phone=sender, text=texto)
                    if not procesado:
                        log.info("  -> Mensaje no reconocido como comando")
                except Exception as exc:
                    log.exception("Error procesando comando de %s: %s", sender, exc)
                    _send(sender, f"Error interno: {exc}")
            else:
                log.debug("Mensaje sin texto de %s (tipo: %s)",
                          sender, msg_data.get("typeMessage", "?"))

        # Eliminar de la cola siempre
        if rid:
            try:
                requests.delete(
                    f"https://api.green-api.com/waInstance{iid}"
                    f"/deleteNotification/{tok}/{rid}",
                    timeout=5,
                )
            except Exception:
                pass


if __name__ == "__main__":
    main()

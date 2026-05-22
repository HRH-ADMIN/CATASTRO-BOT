"""Email digest semanal — resumen del estado del bot enviado por correo.

Reutiliza:
  - `src.utils.metricas.resumen_completo` para los datos
  - Credenciales SMTP del bot (Gmail App Password, configurado en wizard)

Envía un correo plaintext + HTML con el snapshot del lunes 7am a los admins
registrados en la BD (rol=admin).

Útil cuando el operador no consulta WhatsApp/dashboard a diario pero sí
revisa correo.

USO:
    from src.utils.email_digest import enviar_digest_semanal
    res = enviar_digest_semanal(db, creds)
    # res = {"enviados": 2, "errores": []}

Wiring al scheduler:
    scheduler.add_job(
        lambda: enviar_digest_semanal(orchestrator.db, orchestrator.creds),
        trigger="cron", day_of_week="mon", hour=7,
    )
"""
from __future__ import annotations
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.email_digest")


def enviar_email_smtp(
    *,
    from_addr: str,
    password: str,
    to: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> bool:
    """Envía un email vía SMTP (Gmail / Outlook).

    Devuelve True si fue enviado. False con log si falló.
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(from_addr, password)
            smtp.sendmail(from_addr, [to], msg.as_string())
        log.info("digest enviado a %s", to)
        return True
    except Exception as exc:
        log.warning("error enviando digest a %s: %s", to, exc)
        return False


def _resumen_a_texto(resumen: dict) -> str:
    """Renderiza el resumen del dashboard como texto plano."""
    lineas = [
        f"📊 catastro-bot — Resumen semanal",
        f"{'=' * 50}",
        f"Fecha: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"",
        f"Total expedientes activos: {resumen.get('total_expedientes', 0)}",
        f"",
        "POR ESTADO WORKFLOW:",
    ]
    for k, v in sorted((resumen.get("por_estado") or {}).items(),
                       key=lambda x: -x[1]):
        lineas.append(f"  {k:30}  {v:>5}")

    lineas.extend(["", "POR TIPO DE PLANO:"])
    for k, v in sorted((resumen.get("por_tipo") or {}).items(),
                       key=lambda x: -x[1]):
        lineas.append(f"  {k:30}  {v:>5}")

    lineas.extend(["", "POR ESTADO APT:"])
    for k, v in sorted((resumen.get("por_estado_apt") or {}).items(),
                       key=lambda x: -x[1]):
        lineas.append(f"  {k:30}  {v:>5}")

    t = resumen.get("tiempo_promedio_creacion_a_envio_dias")
    if t is not None:
        lineas.extend(["", f"Tiempo promedio creación → envío CFIA: {t:.1f} días"])

    ratio = resumen.get("ratio_exoneracion_pct")
    if ratio is not None:
        lineas.append(f"Tasa de exoneración honorarios: {ratio:.1f}%")

    discrepancias = resumen.get("discrepancias_frecuentes") or []
    if discrepancias:
        lineas.extend(["", "TOP DISCREPANCIAS:"])
        for tipo, n in discrepancias[:5]:
            lineas.append(f"  {n:>3}× {tipo}")

    anomalias = resumen.get("anomalias_recientes") or []
    if anomalias:
        lineas.extend(["", "ÚLTIMAS ANOMALÍAS:"])
        for a in anomalias[:5]:
            ts = (a.get("ts") or "")[:19]
            ctx = a.get("contexto", "?")
            desc = (a.get("descripcion", "") or "")[:80]
            disp = a.get("display") or a.get("numero_expediente", "?")
            lineas.append(f"  [{ts}] {disp} | {ctx}")
            lineas.append(f"           → {desc}")

    alertas = resumen.get("alertas_proactivas") or []
    if alertas:
        lineas.extend(["", "🚨 ALERTAS PROACTIVAS:"])
        for a in alertas:
            lineas.append(f"  {a}")

    lineas.extend(["", "-" * 50, "Bot: catastro-bot", ""])
    return "\n".join(lineas)


def _resumen_a_html(resumen: dict) -> str:
    """Renderiza el resumen como HTML simple."""
    txt = _resumen_a_texto(resumen)
    # HTML mínimo — `<pre>` mantiene el formato del texto
    return (
        "<html><body>"
        '<pre style="font-family:monospace;font-size:13px;">' +
        txt.replace("<", "&lt;").replace(">", "&gt;") +
        "</pre></body></html>"
    )


def enviar_digest_semanal(db, creds) -> dict:
    """Genera el digest del dashboard y lo envía a los admins.

    Args:
        db: instancia de Database.
        creds: CredentialManager con `get_bot_email()` configurado.

    Returns dict con `enviados` (int), `errores` (list[str]).
    """
    from src.utils.metricas import resumen_completo
    res = {"enviados": 0, "errores": [], "destinatarios": []}

    # Renderizar contenido
    try:
        resumen = resumen_completo(db)
        body_text = _resumen_a_texto(resumen)
        body_html = _resumen_a_html(resumen)
        fecha_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        subject = f"[catastro-bot] Resumen semanal — {fecha_iso}"
    except Exception as exc:
        log.exception("error generando digest")
        res["errores"].append(f"render: {exc}")
        return res

    # Credenciales SMTP del bot
    try:
        from src.core.credential_manager import CredentialNotFoundError
        try:
            bot_email = creds.get_muni_san_ramon()  # (user, pass) tupla
            from_addr, password = bot_email
        except CredentialNotFoundError:
            res["errores"].append("credenciales SMTP no configuradas")
            return res
    except Exception as exc:
        res["errores"].append(f"creds: {exc}")
        return res

    # Resolver admins con email (asume `correo_apt` o `correo`)
    try:
        admins = db.listar_usuarios(rol="admin", activo=True)
    except Exception as exc:
        res["errores"].append(f"listar_admins: {exc}")
        return res

    for admin in admins:
        # Preferir correo_apt (CFIA), fallback a otros campos
        email = admin.get("correo_apt") or admin.get("email") or ""
        if not email or "@" not in email:
            continue
        ok = enviar_email_smtp(
            from_addr=from_addr,
            password=password,
            to=email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
        if ok:
            res["enviados"] += 1
            res["destinatarios"].append(email)
        else:
            res["errores"].append(f"envío a {email} falló")

    if res["enviados"] == 0 and not res["errores"]:
        res["errores"].append("ningún admin tiene correo configurado")

    return res


__all__ = [
    "enviar_email_smtp",
    "enviar_digest_semanal",
]

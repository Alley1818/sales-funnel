"""
KP (Коммерческое предложение) sending service.
Business logic extracted from the /api/agent/send-kp route handler.
"""
import logging
from db_conn import get_conn
from leads_db import update_lead_status

logger = logging.getLogger("kp_service")


def send_kp(lead_id: int, company_name: str = "", industry: str = "") -> dict:
    """
    Send КП (commercial proposal) to a lead via WhatsApp and/or Email.
    Checks Telegram approval gate before sending.

    Returns: {"results": {...}} on success, {"error": "..."} on failure.
    """
    conn = get_conn()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return {"error": "Lead not found"}

    lead = dict(lead)
    results = {}

    # Send WhatsApp КП
    if lead.get("whatsapp") or lead.get("mobile"):
        results["whatsapp"] = _send_whatsapp_kp(lead, lead_id, industry)

    # Send Email КП
    if lead.get("email"):
        results["email"] = _send_email_kp(lead, lead_id, industry)

    # Log to agent_sync
    from agent_sync import log_message, log_kp_sent
    log_message(
        lead_id, "whatsapp", "outbound",
        f"КП отправлено для {company_name}", results,
    )
    log_kp_sent(lead_id, "whatsapp", company_name)

    return {"results": results}


def _send_whatsapp_kp(lead: dict, lead_id: int, industry: str) -> str:
    """Send КП via WhatsApp. Returns status string."""
    from whatsapp_client import WhatsAppClient
    from telegram_notifier import notify_send

    # TODO: remove hardcoded phone after testing
    phone = "77026586714"  # lead.get("whatsapp") or lead.get("mobile")
    msg = f"""Здравствуйте!

Как и обещали — отправляем коммерческое предложение для {lead['company_name']}.

Мы специализируемся на AI-решениях для автоматизации бизнеса в сфере {industry}.

Если возникнут вопросы — ответьте на это сообщение!"""

    allowed = notify_send(
        "WhatsApp КП", phone,
        f"{lead['company_name']} / {industry}",
    )
    if not allowed:
        return "blocked (auto_send off, Telegram notified)"

    wa = WhatsAppClient()
    r = wa.send_text(phone, msg)
    if r.success:
        update_lead_status(get_conn(), lead_id, "sent_wa", "KP sent via WhatsApp by AI agent")
        return "sent"
    return f"failed: {r.error}"


def _send_email_kp(lead: dict, lead_id: int, industry: str) -> str:
    """Send КП via Email. Returns status string."""
    from email_sender import EmailSender, build_kp_html
    from telegram_notifier import notify_send

    to = lead["email"]
    allowed = notify_send(
        "Email КП", to,
        f"{lead['company_name']} / {industry}",
    )
    if not allowed:
        return "blocked (auto_send off, Telegram notified)"

    sender = EmailSender()
    html = build_kp_html(lead["company_name"], industry)
    r = sender.send(
        to_email=to,
        subject=f"Коммерческое предложение для {lead['company_name']}",
        body_html=html,
    )
    if r.success:
        update_lead_status(get_conn(), lead_id, "sent_email", "KP sent via email by AI agent")
        return "sent"
    return f"failed: {r.error}"

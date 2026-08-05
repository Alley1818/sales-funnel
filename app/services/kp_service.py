"""
KP (Коммерческое предложение) sending service.
Business logic extracted from the /api/agent/send-kp route handler.
"""
import logging
import json
from pathlib import Path
from db_conn import get_conn
from leads_db import update_lead_status

logger = logging.getLogger("kp_service")

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config_data" / "config.json"


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
        results["whatsapp"] = _send_whatsapp_kp(lead, lead_id, industry, company_name)

    # Send Email КП
    if lead.get("email"):
        results["email"] = _send_email_kp(lead, lead_id, industry, company_name)

    # Log to agent_sync
    from agent_sync import log_message, log_kp_sent
    log_message(
        lead_id, "whatsapp", "outbound",
        f"КП отправлено для {company_name}", results,
    )
    log_kp_sent(lead_id, "whatsapp", company_name)

    return {"results": results}


def _load_kp_config() -> dict:
    """Load KP-related settings from config.json."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg
    except Exception:
        return {}


def _send_whatsapp_kp(lead: dict, lead_id: int, industry: str, company_name: str = "") -> str:
    """Send КП via WhatsApp, attaching document if configured. Returns status string."""
    from whatsapp_client import WhatsAppClient
    from telegram_notifier import notify_send

    phone = lead.get("whatsapp") or lead.get("mobile") or ""
    cname = company_name or lead.get("company_name", "")
    msg = (
        f"Здравствуйте!\n\n"
        f"Отправляем коммерческое предложение для {cname}.\n\n"
        f"Мы специализируемся на AI-решениях для автоматизации бизнеса в сфере {industry}.\n\n"
        f"Если возникнут вопросы — ответьте на это сообщение!"
    )

    allowed = notify_send(
        "WhatsApp КП", phone,
        f"{cname} / {industry}",
    )
    if not allowed:
        return "blocked (auto_send off, Telegram notified)"

    wa = WhatsAppClient()

    # Check config for a КП document file
    cfg = _load_kp_config()
    kp_file_path = cfg.get("kp_file_path", "")
    r = None
    if kp_file_path and Path(kp_file_path).exists():
        doc_name = Path(kp_file_path).name
        r = wa.send_document(phone, kp_file_path, doc_name, caption=msg[:1024])

    # Fallback to text if no document or send failed
    if r is None or not r.success:
        r = wa.send_text(phone, msg)

    if r.success:
        update_lead_status(get_conn(), lead_id, "sent_wa", "KP sent via WhatsApp by AI agent")
        return "sent"
    return f"failed: {r.error}"


def _send_email_kp(lead: dict, lead_id: int, industry: str, company_name: str = "") -> str:
    """Send КП via Email with optional document attachment. Returns status string."""
    from email_sender import EmailSender, build_kp_html
    from telegram_notifier import notify_send

    to = lead["email"]
    cname = company_name or lead.get("company_name", "")
    allowed = notify_send(
        "Email КП", to,
        f"{cname} / {industry}",
    )
    if not allowed:
        return "blocked (auto_send off, Telegram notified)"

    sender = EmailSender()

    # Use custom template from config if available
    cfg = _load_kp_config()
    custom_template = cfg.get("kp_email_template", "")
    if custom_template:
        html = custom_template.replace("{company_name}", cname).replace("{industry}", industry)
    else:
        html = build_kp_html(cname, industry)

    # Collect attachments
    attachments: list[str] = []
    kp_file_path = cfg.get("kp_file_path", "")
    if kp_file_path and Path(kp_file_path).exists():
        attachments.append(kp_file_path)

    r = sender.send(
        to_email=to,
        subject=f"Коммерческое предложение для {cname}",
        body_html=html,
        attachments=attachments or None,
    )
    if r.success:
        update_lead_status(get_conn(), lead_id, "sent_email", "KP sent via email by AI agent")
        return "sent"
    return f"failed: {r.error}"

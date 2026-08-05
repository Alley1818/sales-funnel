"""
Agent Tools — Flask endpoints for Technomax AI agent 'Лидген'.
Called by the agent to send WhatsApp, email, create deals, schedule callbacks.
No CSRF, no browser auth (called server-to-server by Technomax).
"""
import json
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify

logger = logging.getLogger("agent_tools")

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}

agent_tools_bp = Blueprint("agent_tools", __name__)

MANAGER_PHONES = ["77761200700", "77773418838"]


def _notify_managers_wa(lead_id: int, event: str, contact_name: str = "", phone: str = "", notes: str = ""):
    """Send WhatsApp notification to managers about a lead event."""
    try:
        from whatsapp_client import WhatsAppClient
        msg = (
            f"🔔 *{event}*\n\n"
            f"Контакт: {contact_name or '—'}\n"
            f"Телефон: {phone or '—'}\n"
            f"Lead ID: {lead_id}"
        )
        if notes:
            msg += f"\nЗаметки: {notes}"
        client = WhatsAppClient()
        for mgr_phone in MANAGER_PHONES:
            result = client.send_text(mgr_phone, msg)
            if result.success:
                logger.info("Manager WA notification sent to %s for lead %d", mgr_phone, lead_id)
            else:
                logger.error("Manager WA notification failed to %s: %s", mgr_phone, result.error)
    except Exception as e:
        logger.error("WhatsApp manager notification error: %s", e)


@agent_tools_bp.route("/test-whatsapp", methods=["POST"])
def test_whatsapp():
    """Test endpoint: send WhatsApp message to any number with custom text.
    
    POST /api/agent/test-whatsapp
    {"phone": "77071234567", "message": "Привет, это тест"}
    """
    from whatsapp_client import WhatsAppClient

    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    message = data.get("message", "").strip()

    if not phone:
        return jsonify({"error": "phone required"}), 400
    if not message:
        return jsonify({"error": "message required"}), 400

    wa = WhatsAppClient()
    result = wa.send_text(phone, message)

    if result.success:
        return jsonify({"ok": True, "message_id": result.message_id, "phone": phone})
    else:
        return jsonify({"ok": False, "error": result.error, "phone": phone}), 502


@agent_tools_bp.route("/send-whatsapp", methods=["POST"])
def send_whatsapp():
    """Send WhatsApp message to a lead found by phone number."""
    from db_conn import get_conn
    from whatsapp_client import WhatsAppClient
    from agent_sync import log_event

    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify({"error": "phone required"}), 400
    if phone.startswith("{") and phone.endswith("}"):
        return jsonify({"error": f"phone is a template variable, not interpolated: {phone}"}), 400

    # Find lead by mobile or whatsapp or phone column
    conn = get_conn()
    lead = conn.execute(
        "SELECT * FROM leads WHERE mobile = ? OR whatsapp = ? OR phone = ?",
        (phone, phone, phone),
    ).fetchone()

    if not lead:
        # Auto-create lead if not found
        contact_name = data.get("contact_name", "").strip() or f"Клиент {phone[-4:]}"
        cur = conn.execute(
            "INSERT INTO leads (company_name, mobile, phone, status) VALUES (?, ?, ?, 'new')",
            (contact_name, phone, phone),
        )
        conn.commit()
        lead_id = cur.lastrowid
        lead = {"id": lead_id, "company_name": contact_name, "mobile": phone}
        log_event(lead_id, "auto_created", "Лид автоматически создан при отправке WhatsApp", channel="system")
    else:
        lead = dict(lead)
        lead_id = lead["id"]

    # Build the message — use template from config if available
    company = lead.get("company_name", "ваша компания")
    cfg = _load_config()
    template = cfg.get("whatsapp_template", "")
    if template:
        msg = template.replace("{company}", company).replace("{phone}", phone)
    else:
        msg = (
            f"Здравствуйте! Это Technomax.\n\n"
            f"Мы помогаем бизнесу автоматизировать процессы с помощью AI-решений. "
            f"Подготовили коммерческое предложение для {company}.\n\n"
            f"Если интересно — ответьте на это сообщение, и мы обсудим детали!"
        )

    # Try to send KP document if uploaded
    wa = WhatsAppClient()
    kp_sent = False
    try:
        from agent.memory import VectorMemory
        memory = VectorMemory()
        kp_docs = memory.get_knowledge_by_industry("general", doc_type="kp")
        if not kp_docs:
            kp_docs = memory.get_knowledge_by_industry(lead.get("industry", ""), doc_type="kp")
        if kp_docs:
            kp_file = kp_docs[0].get("metadata", {}).get("file_path")
            if kp_file and Path(kp_file).exists():
                doc_result = wa.send_document(phone, kp_file, Path(kp_file).name, caption=msg[:1024])
                if doc_result.success:
                    kp_sent = True
                    result = doc_result
    except Exception as e:
        logger.debug("KP doc send skipped: %s", e)

    if not kp_sent:
        result = wa.send_text(phone, msg)

    if not result.success:
        return jsonify({"error": f"WhatsApp send failed: {result.error}"}), 502

    # Log to timeline
    log_event(
        lead_id, "agent_whatsapp",
        f"Лидген отправил WhatsApp сообщение",
        channel="whatsapp",
        metadata={"phone": phone, "message_id": result.message_id},
    )

    return jsonify({"ok": True, "lead_id": lead_id, "message_sent": True})


@agent_tools_bp.route("/send-email", methods=["POST"])
def send_email():
    """Send KP email to a lead. Finds lead by email or phone."""
    from db_conn import get_conn
    from email_sender import EmailSender, build_kp_html
    from agent_sync import log_event

    data = request.get_json() or {}
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()

    if not email and not phone:
        return jsonify({"error": "email or phone required"}), 400

    conn = get_conn()
    lead = None
    if email:
        lead = conn.execute("SELECT * FROM leads WHERE email = ?", (email,)).fetchone()
    if not lead and phone:
        lead = conn.execute(
            "SELECT * FROM leads WHERE mobile = ? OR whatsapp = ? OR phone = ?",
            (phone, phone, phone),
        ).fetchone()

    company_name = lead["company_name"] if lead else "Клиент"
    industry = lead["industry"] if lead else ""
    to_email = email or (lead["email"] if lead else "")

    if not to_email:
        return jsonify({"error": "No email address available"}), 400

    sender = EmailSender()
    html = build_kp_html(company_name, industry)
    result = sender.send(
        to_email=to_email,
        subject=f"Коммерческое предложение для {company_name}",
        body_html=html,
    )

    if not result.success:
        return jsonify({"error": f"Email send failed: {result.error}"}), 502

    # Log to timeline if we have a lead
    if lead:
        log_event(
            lead["id"], "agent_email",
            f"Лидген отправил КП на {to_email}",
            channel="email",
            metadata={"to": to_email},
        )

    return jsonify({"ok": True})


@agent_tools_bp.route("/create-deal", methods=["POST"])
def create_deal():
    """Create or update a lead with status 'interested'."""
    from db_conn import get_conn
    from agent_sync import log_event, log_status_change, update_lead_context

    data = request.get_json() or {}
    contact_name = data.get("contact_name", "").strip()
    interest_level = data.get("interest_level", 7)
    notes = data.get("notes", "").strip()
    phone = data.get("phone", "").strip()

    if not phone:
        return jsonify({"error": "phone required"}), 400
    if phone.startswith("{") and phone.endswith("}"):
        return jsonify({"error": f"phone is a template variable, not interpolated: {phone}"}), 400

    conn = get_conn()

    # Find existing lead
    lead = conn.execute(
        "SELECT * FROM leads WHERE mobile = ? OR whatsapp = ? OR phone = ?",
        (phone, phone, phone),
    ).fetchone()

    if lead:
        lead = dict(lead)
        lead_id = lead["id"]
        old_status = lead.get("status", "new")
        # Update existing lead
        conn.execute(
            "UPDATE leads SET status = 'interested', notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (notes or lead.get("notes", ""), lead_id),
        )
        conn.commit()
        log_status_change(lead_id, old_status, "interested", notes)
    else:
        # Create new lead
        cur = conn.execute(
            "INSERT INTO leads (company_name, mobile, phone, status, notes) VALUES (?, ?, ?, 'interested', ?)",
            (contact_name or "Unknown", phone, phone, notes),
        )
        conn.commit()
        lead_id = cur.lastrowid
        log_status_change(lead_id, "new", "interested", "Created by Лидген agent")

    # Update context
    try:
        update_lead_context(
            lead_id,
            stage="interested",
            interest_level=int(interest_level) if interest_level else 7,
        )
    except Exception:
        pass  # lead_context table may not exist yet

    log_event(
        lead_id, "deal_created",
        f"Лидген создал сделку: {contact_name or 'Unknown'}",
        channel="system",
        metadata={"interest_level": interest_level, "notes": notes},
    )

    # Notify managers via WhatsApp
    if lead_id:
        _notify_managers_wa(lead_id, "Лидген зафиксировал интерес", contact_name, phone, notes)

    return jsonify({"ok": True, "lead_id": lead_id})


@agent_tools_bp.route("/schedule-callback", methods=["POST"])
def schedule_callback_route():
    """Schedule a callback for a lead."""
    from db_conn import get_conn
    from advanced_features import schedule_callback
    from agent_sync import log_event, update_lead_context

    data = request.get_json() or {}
    callback_datetime = data.get("callback_datetime", "").strip()
    contact_name = data.get("contact_name", "").strip()
    phone = data.get("phone", "").strip()

    if not phone:
        return jsonify({"error": "phone required"}), 400
    if phone.startswith("{") and phone.endswith("}"):
        return jsonify({"error": f"phone is a template variable, not interpolated: {phone}"}), 400
    if not callback_datetime:
        return jsonify({"error": "callback_datetime required"}), 400

    conn = get_conn()

    # Find or create lead
    lead = conn.execute(
        "SELECT * FROM leads WHERE mobile = ? OR whatsapp = ? OR phone = ?",
        (phone, phone, phone),
    ).fetchone()

    if lead:
        lead = dict(lead)
        lead_id = lead["id"]
        # Update status to callback
        conn.execute(
            "UPDATE leads SET status = 'callback', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (lead_id,),
        )
        conn.commit()
    else:
        # Create new lead
        cur = conn.execute(
            "INSERT INTO leads (company_name, mobile, phone, status) VALUES (?, ?, ?, 'callback')",
            (contact_name or "Unknown", phone, phone),
        )
        conn.commit()
        lead_id = cur.lastrowid

    # Schedule the callback
    try:
        schedule_callback(lead_id, callback_datetime, channel="voice",
                          notes=f"Запланировано Лидгеном для {contact_name}")
    except Exception as e:
        logger.warning("schedule_callback table may not exist: %s", e)

    # Update context
    try:
        update_lead_context(lead_id, stage="negotiating", next_action="schedule_callback")
    except Exception:
        pass

    log_event(
        lead_id, "callback_scheduled",
        f"Лидген запланировал звонок на {callback_datetime}",
        channel="system",
        metadata={"callback_datetime": callback_datetime, "contact_name": contact_name},
    )

    # Notify managers via WhatsApp
    if lead_id:
        _notify_managers_wa(lead_id, f"Запланирован звонок на {callback_datetime}", contact_name, phone)

    return jsonify({"ok": True, "lead_id": lead_id, "callback_datetime": callback_datetime})


# ======================================================================
# /call-complete — unified endpoint for Technomax after call ends
# ======================================================================

@agent_tools_bp.route("/call-complete", methods=["POST"])
def call_complete():
    """
    Unified endpoint: Technomax calls this after a voice call ends.
    One request: logs result, creates/updates lead, optionally sends WhatsApp.

    JSON body:
        phone          (required) — client phone number
        contact_name   — client name
        company_name   — company name
        industry       — industry
        result         — call result: interested|callback|refused|no_answer|wrong_number
        notes          — call transcript or notes
        send_kp        — bool, send commercial proposal via WhatsApp
        send_whatsapp  — bool, send follow-up WhatsApp message
        message        — custom WhatsApp message (overrides default)
    """
    from db_conn import get_conn
    from whatsapp_client import WhatsAppClient
    from agent_sync import log_event, log_message, log_status_change, update_lead_context

    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    if not phone:
        return jsonify({"error": "phone required"}), 400

    contact_name = data.get("contact_name", "").strip()
    company_name = data.get("company_name", "").strip() or "Unknown"
    industry = data.get("industry", "").strip()
    result = data.get("result", "unknown").strip()
    notes = data.get("notes", "").strip()
    send_kp = data.get("send_kp", False)
    send_wa = data.get("send_whatsapp", False) or send_kp
    custom_message = data.get("message", "").strip()

    # Normalize phone
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]

    # Find or create lead
    conn = get_conn()
    lead = conn.execute(
        "SELECT * FROM leads WHERE mobile = ? OR whatsapp = ? OR phone = ?",
        (digits, digits, digits),
    ).fetchone()

    if lead:
        lead = dict(lead)
        lead_id = lead["id"]
        old_status = lead.get("status", "new")
        # Update company/industry if provided and missing
        if company_name and company_name != "Unknown":
            conn.execute("UPDATE leads SET company_name = ? WHERE id = ? AND (company_name = '' OR company_name LIKE 'Клиент%')",
                         (company_name, lead_id))
        if industry:
            conn.execute("UPDATE leads SET industry = ? WHERE id = ? AND industry = ''",
                         (industry, lead_id))
        conn.commit()
    else:
        cur = conn.execute(
            "INSERT INTO leads (company_name, mobile, phone, industry, status) VALUES (?, ?, ?, ?, 'new')",
            (company_name if company_name != "Unknown" else f"Клиент {digits[-4:]}", digits, digits, industry),
        )
        conn.commit()
        lead_id = cur.lastrowid
        assert lead_id is not None
        old_status = "new"
        log_event(lead_id, "auto_created", "Лид создан по результату звонка Лидгена", channel="voice")

    # Update status based on call result
    status_map = {
        "interested": "interested",
        "callback": "callback",
        "refused": "refused",
        "no_answer": "no_answer",
        "wrong_number": "wrong_number",
    }
    new_status = status_map.get(result, "called")
    conn.execute(
        "UPDATE leads SET status = ?, call_result = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_status, result, lead_id),
    )
    conn.commit()

    # Log call result
    if notes:
        log_message(lead_id, "voice", "inbound", notes[:500], {"result": result})
    log_status_change(lead_id, old_status, new_status, f"[voice] {notes[:100]}")
    log_event(lead_id, "call_result", f"Звонок: {result}. {notes[:200]}", channel="voice",
              metadata={"result": result, "phone": digits})

    # Update context
    if result == "interested":
        update_lead_context(lead_id, stage="interested", interest_level=8)
    elif result == "refused":
        update_lead_context(lead_id, stage="lost", interest_level=0)
    elif result == "callback":
        update_lead_context(lead_id, stage="negotiating", interest_level=5)

    # Send WhatsApp if requested
    wa_sent = False
    wa_error = None
    if send_wa:
        if send_kp:
            # Send commercial proposal
            try:
                from app.services.kp_service import send_kp
                kp_result = send_kp(lead_id, company_name, industry)
                wa_sent = "error" not in kp_result
                if not wa_sent:
                    wa_error = kp_result.get("error", "kp_failed")
            except Exception as e:
                wa_error = str(e)
                logger.error("KP send failed: %s", e)
        else:
            # Send regular follow-up message
            wa = WhatsAppClient()
            if not custom_message:
                custom_message = (
                    f"Здравствуйте! Спасибо за разговор. "
                    f"Если возникнут вопросы — пишите сюда, мы на связи!"
                )
            wa_result = wa.send_text(digits, custom_message)
            wa_sent = wa_result.success
            if not wa_result.success:
                wa_error = wa_result.error

        if wa_sent:
            log_event(lead_id, "whatsapp_sent", "WhatsApp отправлен после звонка",
                      channel="whatsapp", metadata={"phone": digits})
        else:
            log_event(lead_id, "whatsapp_failed", f"WhatsApp не отправлен: {wa_error}",
                      channel="whatsapp", metadata={"error": wa_error})

    return jsonify({
        "ok": True,
        "lead_id": lead_id,
        "status": new_status,
        "whatsapp_sent": wa_sent,
        "whatsapp_error": wa_error,
    })


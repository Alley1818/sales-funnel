"""
Agent Tools — Flask endpoints for Technomax AI agent 'Лидген'.
Called by the agent to send WhatsApp, email, create deals, schedule callbacks.
No CSRF, no browser auth (called server-to-server by Technomax).
"""
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger("agent_tools")

agent_tools_bp = Blueprint("agent_tools", __name__)


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

    # Find lead by mobile or whatsapp or phone column
    conn = get_conn()
    lead = conn.execute(
        "SELECT * FROM leads WHERE mobile = ? OR whatsapp = ? OR phone = ?",
        (phone, phone, phone),
    ).fetchone()

    if not lead:
        return jsonify({"error": "Lead not found for this phone"}), 404

    lead = dict(lead)
    lead_id = lead["id"]

    # Build the message
    company = lead.get("company_name", "ваша компания")
    msg = (
        f"Здравствуйте! Меня зовут Лидген, я AI-ассистент компании Technomax.\n\n"
        f"Мы специализируемся на AI-решениях для автоматизации бизнеса. "
        f"Хотели бы обсудить, как можем помочь {company}.\n\n"
        f"Ответьте на это сообщение, если интересно!"
    )

    # Send via Evolution API
    wa = WhatsAppClient()
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

    return jsonify({"ok": True, "lead_id": lead_id})

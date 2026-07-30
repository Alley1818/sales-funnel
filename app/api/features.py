"""
Features blueprint: agents, templates, DNC, scoring, campaigns, A/B tests, WA inbox.
Wraps the existing funnel_features module.
"""
import json
import logging
from flask import Blueprint, request, jsonify
from middleware import require_auth

logger = logging.getLogger("features_api")
features_bp = Blueprint("features", __name__)


# ==================== AI AGENTS ====================

@features_bp.route("/api/agents", methods=["GET"])
@require_auth
def list_agents():
    from funnel_features import get_agents
    return jsonify({"agents": get_agents()})


@features_bp.route("/api/agents", methods=["POST"])
@require_auth
def create_agent():
    from funnel_features import create_agent
    data = request.get_json() or {}
    if not data.get("name") or not data.get("industry"):
        return jsonify({"error": "name and industry required"}), 400
    aid = create_agent(
        name=data["name"], industry=data["industry"],
        prompt=data.get("prompt", ""), welcome_phrase=data.get("welcome_phrase", ""),
        voice=data.get("voice", "ru-RU-SvetlanaNeural"),
        llm_model=data.get("llm_model", "xiaomi/mimo-v2.5-pro"),
        temperature=data.get("temperature", 0.3), max_tokens=data.get("max_tokens", 300),
    )
    return jsonify({"ok": True, "id": aid})


@features_bp.route("/api/agents/<int:agent_id>", methods=["GET"])
@require_auth
def get_agent(agent_id):
    from funnel_features import get_agents
    agents = get_agents()
    for a in agents:
        if a["id"] == agent_id:
            return jsonify(a)
    return jsonify({"error": "Not found"}), 404


@features_bp.route("/api/agents/<int:agent_id>", methods=["PUT"])
@require_auth
def update_agent(agent_id):
    from funnel_features import update_agent
    data = request.get_json() or {}
    ok = update_agent(agent_id, **data)
    return jsonify({"ok": ok})


@features_bp.route("/api/agents/<int:agent_id>", methods=["DELETE"])
@require_auth
def delete_agent(agent_id):
    from funnel_features import delete_agent
    delete_agent(agent_id)
    return jsonify({"ok": True})


# ==================== MESSAGE TEMPLATES ====================

@features_bp.route("/api/templates", methods=["GET"])
@require_auth
def list_templates():
    from funnel_features import get_templates
    industry = request.args.get("industry", "")
    channel = request.args.get("channel", "")
    return jsonify({"templates": get_templates(industry, channel)})


@features_bp.route("/api/templates", methods=["POST"])
@require_auth
def create_template():
    from funnel_features import create_template
    data = request.get_json() or {}
    if not data.get("name") or not data.get("body") or not data.get("channel"):
        return jsonify({"error": "name, body, channel required"}), 400
    tid = create_template(
        name=data["name"], industry=data.get("industry", ""),
        channel=data["channel"], body=data["body"],
        subject=data.get("subject", ""), is_default=data.get("is_default", 0),
        ab_variant=data.get("ab_variant", ""),
    )
    return jsonify({"ok": True, "id": tid})


@features_bp.route("/api/templates/<int:tid>", methods=["PUT"])
@require_auth
def update_template(tid):
    from funnel_features import update_template
    data = request.get_json() or {}
    ok = update_template(tid, **data)
    return jsonify({"ok": ok})


@features_bp.route("/api/templates/<int:tid>", methods=["DELETE"])
@require_auth
def delete_template(tid):
    from funnel_features import delete_template
    delete_template(tid)
    return jsonify({"ok": True})


# ==================== DO NOT CALL ====================

@features_bp.route("/api/dnc", methods=["GET"])
@require_auth
def list_dnc():
    from funnel_features import get_dnc_list
    return jsonify({"dnc": get_dnc_list()})


@features_bp.route("/api/dnc", methods=["POST"])
@require_auth
def add_dnc():
    from funnel_features import add_dnc
    data = request.get_json() or {}
    phone = data.get("phone", "")
    if not phone:
        return jsonify({"error": "phone required"}), 400
    ok = add_dnc(phone, data.get("reason", ""))
    return jsonify({"ok": ok})


@features_bp.route("/api/dnc/<phone>", methods=["DELETE"])
@require_auth
def remove_dnc(phone):
    from funnel_features import remove_dnc
    remove_dnc(phone)
    return jsonify({"ok": True})


# ==================== LEAD SCORING ====================

@features_bp.route("/api/scores", methods=["GET"])
@require_auth
def list_scores():
    from funnel_features import get_leads_by_score
    category = request.args.get("category", "")
    min_score = int(request.args.get("min_score", 0))
    return jsonify({"leads": get_leads_by_score(category, min_score)})


@features_bp.route("/api/scores/<int:lead_id>", methods=["GET"])
@require_auth
def get_score(lead_id):
    from funnel_features import get_lead_score
    score = get_lead_score(lead_id)
    return jsonify(score or {"score": 0, "category": "unscored"})


@features_bp.route("/api/scores/<int:lead_id>", methods=["POST"])
@require_auth
def set_score(lead_id):
    from funnel_features import score_lead
    data = request.get_json() or {}
    score_lead(lead_id, data.get("score", 0), data.get("category", "cold"), data.get("reasoning", ""))
    return jsonify({"ok": True})


# ==================== CAMPAIGNS ====================

@features_bp.route("/api/campaigns", methods=["GET"])
@require_auth
def list_campaigns():
    from funnel_features import get_campaigns
    return jsonify({"campaigns": get_campaigns()})


@features_bp.route("/api/campaigns", methods=["POST"])
@require_auth
def create_campaign():
    from funnel_features import create_campaign
    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"error": "name required"}), 400
    cid = create_campaign(
        name=data["name"], industry=data.get("industry", ""),
        channel=data.get("channel", "voice"),
        schedule_cron=data.get("schedule_cron", ""),
        schedule_time=data.get("schedule_time", ""),
        max_calls=data.get("max_calls", 50), cps=data.get("cps", 1),
        agent_id=data.get("agent_id"), template_id=data.get("template_id"),
    )
    return jsonify({"ok": True, "id": cid})


@features_bp.route("/api/campaigns/<int:cid>", methods=["DELETE"])
@require_auth
def delete_campaign(cid):
    from funnel_features import delete_campaign
    delete_campaign(cid)
    return jsonify({"ok": True})


@features_bp.route("/api/campaigns/<int:cid>/leads", methods=["POST"])
@require_auth
def add_campaign_leads(cid):
    from funnel_features import add_leads_to_campaign
    data = request.get_json() or {}
    lead_ids = data.get("lead_ids", [])
    added = add_leads_to_campaign(cid, lead_ids)
    return jsonify({"ok": True, "added": added})


@features_bp.route("/api/campaigns/<int:cid>/leads", methods=["GET"])
@require_auth
def get_campaign_leads(cid):
    from funnel_features import get_campaign_leads
    status = request.args.get("status", "")
    return jsonify({"leads": get_campaign_leads(cid, status)})


@features_bp.route("/api/campaigns/<int:cid>/start", methods=["POST"])
@require_auth
def start_campaign(cid):
    from funnel_features import update_campaign_status
    update_campaign_status(cid, "running")
    return jsonify({"ok": True})


@features_bp.route("/api/campaigns/<int:cid>/pause", methods=["POST"])
@require_auth
def pause_campaign(cid):
    from funnel_features import update_campaign_status
    update_campaign_status(cid, "paused")
    return jsonify({"ok": True})


# ==================== A/B TESTS ====================

@features_bp.route("/api/ab-tests", methods=["GET"])
@require_auth
def list_ab_tests():
    from funnel_features import get_ab_tests
    return jsonify({"tests": get_ab_tests()})


@features_bp.route("/api/ab-tests", methods=["POST"])
@require_auth
def create_ab_test():
    from funnel_features import create_ab_test
    data = request.get_json() or {}
    if not data.get("name") or not data.get("template_a_id") or not data.get("template_b_id"):
        return jsonify({"error": "name, template_a_id, template_b_id required"}), 400
    tid = create_ab_test(data["name"], data["template_a_id"], data["template_b_id"])
    return jsonify({"ok": True, "id": tid})


# ==================== CPS / RATE LIMITING ====================

@features_bp.route("/api/config/cps", methods=["GET"])
@require_auth
def get_cps():
    from funnel_features import CPS_LIMIT
    return jsonify({"cps": CPS_LIMIT})


@features_bp.route("/api/config/cps", methods=["POST"])
@require_auth
def set_cps():
    import funnel_features
    data = request.get_json() or {}
    funnel_features.CPS_LIMIT = data.get("cps", 1)
    return jsonify({"ok": True, "cps": funnel_features.CPS_LIMIT})


# ==================== WHATSAPP INBOX ====================

@features_bp.route("/api/wa/inbox", methods=["GET"])
@require_auth
def wa_inbox():
    from funnel_features import get_unreplied_wa
    return jsonify({"messages": get_unreplied_wa()})


@features_bp.route("/api/wa/webhook", methods=["POST"])
def wa_webhook():
    """Webhook for incoming WhatsApp messages (from Evolution API)."""
    import os
    webhook_secret = os.getenv("WA_WEBHOOK_SECRET", "")
    if webhook_secret:
        token = request.headers.get("X-Webhook-Secret") or request.args.get("token", "")
        if token != webhook_secret:
            return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    msg_data = data.get("data", {})
    phone = msg_data.get("key", {}).get("remoteJid", "").replace("@s.whatsapp.net", "")
    message = msg_data.get("message", {}).get("conversation", "")
    if not phone or not message:
        phone = data.get("from", "")
        message = data.get("body", data.get("text", ""))

    if phone and message:
        from funnel_features import log_incoming_wa
        mid = log_incoming_wa(phone, message)
        logger.info("Incoming WA from %s: %s (id=%s)", phone, message[:50], mid)

        # Process with AI agent (non-blocking)
        try:
            from wa_agent_service import process_incoming_message
            import threading
            threading.Thread(
                target=process_incoming_message,
                args=(phone, message),
                daemon=True,
            ).start()
        except Exception as e:
            logger.error("WA agent processing failed: %s", e)

    return jsonify({"ok": True})


# ==================== BITRIX24 ====================

@features_bp.route("/api/config/bitrix", methods=["GET"])
@require_auth
def get_bitrix():
    from pathlib import Path
    cfg = {}
    try:
        p = Path(__file__).parent.parent.parent / "config.json"
        if p.exists():
            full = json.loads(p.read_text())
            cfg = full.get("bitrix", {})
    except Exception:
        pass
    return jsonify({"portal": cfg.get("portal", ""), "configured": bool(cfg.get("portal"))})


@features_bp.route("/api/config/bitrix", methods=["POST"])
@require_auth
def set_bitrix():
    from pathlib import Path
    p = Path(__file__).parent.parent.parent / "config.json"
    full = {}
    if p.exists():
        full = json.loads(p.read_text())
    data = request.get_json() or {}
    full["bitrix"] = {
        "portal": data.get("portal", ""),
        "webhook_token": data.get("webhook_token", ""),
    }
    p.write_text(json.dumps(full, indent=2, ensure_ascii=False))
    return jsonify({"ok": True})


@features_bp.route("/api/bitrix/sync", methods=["POST"])
@require_auth
def bitrix_sync():
    """Sync leads to Bitrix24 as contacts/deals."""
    from pathlib import Path
    import httpx
    from db_conn import get_conn

    p = Path(__file__).parent.parent.parent / "config.json"
    if not p.exists():
        return jsonify({"error": "Bitrix not configured"}), 400
    full = json.loads(p.read_text())
    bitrix = full.get("bitrix", {})
    portal = bitrix.get("portal", "")
    token = bitrix.get("webhook_token", "")
    if not portal or not token:
        return jsonify({"error": "Bitrix not configured"}), 400

    base = f"https://{portal}/rest"
    synced = 0
    try:
        conn = get_conn()
        leads = conn.execute(
            "SELECT * FROM leads WHERE status IN ('interested', 'sent_wa', 'sent_email') LIMIT 50"
        ).fetchall()
        for lead in leads:
            lead = dict(lead)
            r = httpx.post(f"{base}/{token}/crm.contact.add.json", json={
                "fields": {
                    "NAME": lead["company_name"],
                    "PHONE": [{"VALUE": lead.get("mobile", ""), "VALUE_TYPE": "WORK"}],
                    "EMAIL": [{"VALUE": lead.get("email", ""), "VALUE_TYPE": "WORK"}],
                }
            }, timeout=10)
            if r.status_code == 200:
                synced += 1
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "synced": synced})

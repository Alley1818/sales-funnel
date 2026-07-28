"""
API endpoints for all sales funnel features.
Mounted on the main Flask app.
"""
import json
import logging
from flask import Flask, request, jsonify

logger = logging.getLogger("features_api")


def register_feature_routes(app: Flask):
    """Register all feature API routes on the Flask app."""

    # ==================== AI AGENTS ====================

    @app.route("/api/agents", methods=["GET"])
    def list_agents():
        from funnel_features import get_agents
        return jsonify({"agents": get_agents()})

    @app.route("/api/agents", methods=["POST"])
    def create_agent():
        from funnel_features import create_agent
        data = request.get_json() or {}
        if not data.get("name") or not data.get("industry"):
            return jsonify({"error": "name and industry required"}), 400
        aid = create_agent(
            name=data["name"],
            industry=data["industry"],
            prompt=data.get("prompt", ""),
            welcome_phrase=data.get("welcome_phrase", ""),
            voice=data.get("voice", "ru-RU-SvetlanaNeural"),
            llm_model=data.get("llm_model", "xiaomi/mimo-v2.5-pro"),
            temperature=data.get("temperature", 0.3),
            max_tokens=data.get("max_tokens", 300),
        )
        return jsonify({"ok": True, "id": aid})

    @app.route("/api/agents/<int:agent_id>", methods=["GET"])
    def get_agent(agent_id):
        from funnel_features import get_agents
        agents = get_agents()
        for a in agents:
            if a["id"] == agent_id:
                return jsonify(a)
        return jsonify({"error": "Not found"}), 404

    @app.route("/api/agents/<int:agent_id>", methods=["PUT"])
    def update_agent(agent_id):
        from funnel_features import update_agent
        data = request.get_json() or {}
        ok = update_agent(agent_id, **data)
        return jsonify({"ok": ok})

    @app.route("/api/agents/<int:agent_id>", methods=["DELETE"])
    def delete_agent(agent_id):
        from funnel_features import delete_agent
        delete_agent(agent_id)
        return jsonify({"ok": True})

    # ==================== MESSAGE TEMPLATES ====================

    @app.route("/api/templates", methods=["GET"])
    def list_templates():
        from funnel_features import get_templates
        industry = request.args.get("industry", "")
        channel = request.args.get("channel", "")
        return jsonify({"templates": get_templates(industry, channel)})

    @app.route("/api/templates", methods=["POST"])
    def create_template():
        from funnel_features import create_template
        data = request.get_json() or {}
        if not data.get("name") or not data.get("body") or not data.get("channel"):
            return jsonify({"error": "name, body, channel required"}), 400
        tid = create_template(
            name=data["name"],
            industry=data.get("industry", ""),
            channel=data["channel"],
            body=data["body"],
            subject=data.get("subject", ""),
            is_default=data.get("is_default", 0),
            ab_variant=data.get("ab_variant", ""),
        )
        return jsonify({"ok": True, "id": tid})

    @app.route("/api/templates/<int:tid>", methods=["PUT"])
    def update_template(tid):
        from funnel_features import update_template
        data = request.get_json() or {}
        ok = update_template(tid, **data)
        return jsonify({"ok": ok})

    @app.route("/api/templates/<int:tid>", methods=["DELETE"])
    def delete_template(tid):
        from funnel_features import delete_template
        delete_template(tid)
        return jsonify({"ok": True})

    # ==================== DO NOT CALL ====================

    @app.route("/api/dnc", methods=["GET"])
    def list_dnc():
        from funnel_features import get_dnc_list
        return jsonify({"dnc": get_dnc_list()})

    @app.route("/api/dnc", methods=["POST"])
    def add_dnc():
        from funnel_features import add_dnc
        data = request.get_json() or {}
        phone = data.get("phone", "")
        if not phone:
            return jsonify({"error": "phone required"}), 400
        ok = add_dnc(phone, data.get("reason", ""))
        return jsonify({"ok": ok})

    @app.route("/api/dnc/<phone>", methods=["DELETE"])
    def remove_dnc(phone):
        from funnel_features import remove_dnc
        remove_dnc(phone)
        return jsonify({"ok": True})

    # ==================== LEAD SCORING ====================

    @app.route("/api/scores", methods=["GET"])
    def list_scores():
        from funnel_features import get_leads_by_score
        category = request.args.get("category", "")
        min_score = int(request.args.get("min_score", 0))
        return jsonify({"leads": get_leads_by_score(category, min_score)})

    @app.route("/api/scores/<int:lead_id>", methods=["GET"])
    def get_score(lead_id):
        from funnel_features import get_lead_score
        score = get_lead_score(lead_id)
        return jsonify(score or {"score": 0, "category": "unscored"})

    @app.route("/api/scores/<int:lead_id>", methods=["POST"])
    def set_score(lead_id):
        from funnel_features import score_lead
        data = request.get_json() or {}
        score_lead(lead_id, data.get("score", 0), data.get("category", "cold"), data.get("reasoning", ""))
        return jsonify({"ok": True})

    # ==================== CAMPAIGNS ====================

    @app.route("/api/campaigns", methods=["GET"])
    def list_campaigns():
        from funnel_features import get_campaigns
        return jsonify({"campaigns": get_campaigns()})

    @app.route("/api/campaigns", methods=["POST"])
    def create_campaign():
        from funnel_features import create_campaign
        data = request.get_json() or {}
        if not data.get("name"):
            return jsonify({"error": "name required"}), 400
        cid = create_campaign(
            name=data["name"],
            industry=data.get("industry", ""),
            channel=data.get("channel", "voice"),
            schedule_cron=data.get("schedule_cron", ""),
            schedule_time=data.get("schedule_time", ""),
            max_calls=data.get("max_calls", 50),
            cps=data.get("cps", 1),
            agent_id=data.get("agent_id"),
            template_id=data.get("template_id"),
        )
        return jsonify({"ok": True, "id": cid})

    @app.route("/api/campaigns/<int:cid>", methods=["DELETE"])
    def delete_campaign(cid):
        from funnel_features import delete_campaign
        delete_campaign(cid)
        return jsonify({"ok": True})

    @app.route("/api/campaigns/<int:cid>/leads", methods=["POST"])
    def add_campaign_leads(cid):
        from funnel_features import add_leads_to_campaign
        data = request.get_json() or {}
        lead_ids = data.get("lead_ids", [])
        added = add_leads_to_campaign(cid, lead_ids)
        return jsonify({"ok": True, "added": added})

    @app.route("/api/campaigns/<int:cid>/leads", methods=["GET"])
    def get_campaign_leads(cid):
        from funnel_features import get_campaign_leads
        status = request.args.get("status", "")
        return jsonify({"leads": get_campaign_leads(cid, status)})

    @app.route("/api/campaigns/<int:cid>/start", methods=["POST"])
    def start_campaign(cid):
        from funnel_features import update_campaign_status
        update_campaign_status(cid, "running")
        return jsonify({"ok": True})

    @app.route("/api/campaigns/<int:cid>/pause", methods=["POST"])
    def pause_campaign(cid):
        from funnel_features import update_campaign_status
        update_campaign_status(cid, "paused")
        return jsonify({"ok": True})

    # ==================== A/B TESTS ====================

    @app.route("/api/ab-tests", methods=["GET"])
    def list_ab_tests():
        from funnel_features import get_ab_tests
        return jsonify({"tests": get_ab_tests()})

    @app.route("/api/ab-tests", methods=["POST"])
    def create_ab_test():
        from funnel_features import create_ab_test
        data = request.get_json() or {}
        if not data.get("name") or not data.get("template_a_id") or not data.get("template_b_id"):
            return jsonify({"error": "name, template_a_id, template_b_id required"}), 400
        tid = create_ab_test(data["name"], data["template_a_id"], data["template_b_id"])
        return jsonify({"ok": True, "id": tid})

    # ==================== CPS / RATE LIMITING ====================

    @app.route("/api/config/cps", methods=["GET"])
    def get_cps():
        from funnel_features import CPS_LIMIT
        return jsonify({"cps": CPS_LIMIT})

    @app.route("/api/config/cps", methods=["POST"])
    def set_cps():
        import funnel_features
        data = request.get_json() or {}
        funnel_features.CPS_LIMIT = data.get("cps", 1)
        return jsonify({"ok": True, "cps": funnel_features.CPS_LIMIT})

    # ==================== WHATSAPP INBOX ====================

    @app.route("/api/wa/inbox", methods=["GET"])
    def wa_inbox():
        from funnel_features import get_unreplied_wa
        return jsonify({"messages": get_unreplied_wa()})

    @app.route("/api/wa/webhook", methods=["POST"])
    def wa_webhook():
        """Webhook for incoming WhatsApp messages (from Evolution API)."""
        # Basic webhook signature verification
        import os
        webhook_secret = os.getenv("WA_WEBHOOK_SECRET", "")
        if webhook_secret:
            token = request.headers.get("X-Webhook-Secret") or request.args.get("token", "")
            if token != webhook_secret:
                return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json() or {}
        # Extract message from Evolution API webhook format
        msg_data = data.get("data", {})
        phone = msg_data.get("key", {}).get("remoteJid", "").replace("@s.whatsapp.net", "")
        message = msg_data.get("message", {}).get("conversation", "")
        if not phone or not message:
            # Try alternative format
            phone = data.get("from", "")
            message = data.get("body", data.get("text", ""))

        if phone and message:
            from funnel_features import log_incoming_wa
            mid = log_incoming_wa(phone, message)
            logger.info("Incoming WA from %s: %s (id=%s)", phone, message[:50], mid)

        return jsonify({"ok": True})

    # ==================== BITRIX24 ====================

    @app.route("/api/config/bitrix", methods=["GET"])
    def get_bitrix():
        cfg = {}
        try:
            from pathlib import Path
            p = Path(__file__).parent / "config.json"
            if p.exists():
                full = json.loads(p.read_text())
                cfg = full.get("bitrix", {})
        except Exception:
            pass
        return jsonify({
            "portal": cfg.get("portal", ""),
            "configured": bool(cfg.get("portal")),
        })

    @app.route("/api/config/bitrix", methods=["POST"])
    def set_bitrix():
        from pathlib import Path
        p = Path(__file__).parent / "config.json"
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

    @app.route("/api/bitrix/sync", methods=["POST"])
    def bitrix_sync():
        """Sync leads to Bitrix24 as contacts/deals."""
        from pathlib import Path
        p = Path(__file__).parent / "config.json"
        if not p.exists():
            return jsonify({"error": "Bitrix not configured"}), 400
        full = json.loads(p.read_text())
        bitrix = full.get("bitrix", {})
        portal = bitrix.get("portal", "")
        token = bitrix.get("webhook_token", "")
        if not portal or not token:
            return jsonify({"error": "Bitrix not configured"}), 400

        # Sync logic here
        import httpx
        base = f"https://{portal}/rest"
        synced = 0
        try:
            conn = __import__("leads_db", fromlist=["init_db"]).init_db()
            leads = conn.execute("SELECT * FROM leads WHERE status IN ('interested', 'sent_wa', 'sent_email') LIMIT 50").fetchall()
            for lead in leads:
                lead = dict(lead)
                # Create contact
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

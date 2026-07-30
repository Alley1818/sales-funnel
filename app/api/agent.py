"""
Agent callback endpoints: send-kp, log-call, start calls, call status.
"""
import threading
import logging
from flask import Blueprint, request, jsonify
from middleware import require_auth

logger = logging.getLogger("agent_api")
agent_bp = Blueprint("agent", __name__)


@agent_bp.route("/api/agent/send-kp", methods=["POST"])
@require_auth
def agent_send_kp():
    """Called by Technomax agent when client wants КП."""
    from app.services.kp_service import send_kp

    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    company_name = data.get("company_name", "")
    industry = data.get("industry", "")

    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    result = send_kp(lead_id, company_name, industry)
    if "error" in result:
        status = 404 if "not found" in result["error"].lower() else 400
        return jsonify(result), status
    return jsonify({"ok": True, "results": result["results"]})


@agent_bp.route("/api/agent/log-call", methods=["POST"])
@require_auth
def agent_log_call():
    """Called by Technomax agent to log conversation result."""
    from leads_db import update_lead_status
    from agent_sync import log_message, sync_after_whatsapp, update_lead_context, log_status_change
    from db_conn import get_conn

    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    channel = data.get("channel", "whatsapp")
    result = data.get("result", "unknown")
    notes = data.get("notes", "")

    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    conn = get_conn()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    old_status = lead["status"] if lead else "unknown"
    status_map = {"interested": "interested", "callback": "callback", "refused": "refused"}
    new_status = status_map.get(result, "called")
    update_lead_status(conn, lead_id, new_status, f"[{channel}] {notes}")

    log_message(lead_id, channel, "inbound", notes[:500], {"result": result})
    log_status_change(lead_id, old_status, new_status, f"[{channel}] {notes[:100]}")
    sync_after_whatsapp(lead_id, notes, is_inbound=True)

    if result == "interested":
        update_lead_context(lead_id, stage="interested", interest_level=8)
    elif result == "refused":
        update_lead_context(lead_id, stage="lost", interest_level=0)
    elif result == "callback":
        update_lead_context(lead_id, stage="negotiating", interest_level=5)

    return jsonify({"ok": True})


@agent_bp.route("/api/agent/push-leads", methods=["POST"])
@require_auth
def push_leads():
    """Push leads to a Technomax autocall task."""
    from app.services.lead_push_service import push_leads_to_task
    from db_conn import get_conn

    data = request.get_json() or {}
    lead_ids = data.get("lead_ids", [])
    task_name = data.get("task_name", "Funnel Push")
    agent_id = data.get("agent_id", "")
    bot_id = data.get("bot_id", "")
    cps = data.get("cps", 1)

    if not lead_ids:
        return jsonify({"error": "lead_ids required"}), 400

    conn = get_conn()
    placeholders = ",".join("?" * len(lead_ids))
    rows = conn.execute(
        f"SELECT * FROM leads WHERE id IN ({placeholders})", lead_ids
    ).fetchall()
    leads = [dict(r) for r in rows]

    if not leads:
        return jsonify({"error": "No leads found"}), 404

    result = push_leads_to_task(
        leads=leads, task_name=task_name,
        agent_id=agent_id, bot_id=bot_id, cps=cps,
    )

    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@agent_bp.route("/api/agent/create", methods=["POST"])
@require_auth
def create_technomax_agent():
    """Create AI agent on Technomax platform."""
    import httpx as _httpx
    from technomax_agent import BASE_URL, _get_credentials, _headers

    data = request.get_json() or {}
    funnel_url = data.get("funnel_url", "http://YOUR_VPS_IP:5050")

    try:
        with _httpx.Client(timeout=15) as _client:
            r = _client.post(
                f"{BASE_URL}/iam/api/v1/auth/login",
                json=_get_credentials(),
                headers={
                    "Origin": BASE_URL, "Referer": f"{BASE_URL}/app",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code != 200:
                return jsonify({"error": "Auth failed"}), 401
            token = r.json().get("token")

            agent_config = {
                "name": "Sales Funnel Agent",
                "config": {
                    "displayName": "Sales Funnel Agent",
                    "llm": {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro"},
                    "tts": {"provider": "edge-tts", "voice": "ru-RU-SvetlanaNeural"},
                },
            }
            r2 = _client.post(
                f"{BASE_URL}/agent/api/v1/agents",
                json=agent_config,
                headers=_headers(token),
            )
            if r2.status_code == 200:
                return jsonify({"ok": True, "agent": r2.json()})
            return jsonify({"error": "Failed to create agent"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@agent_bp.route("/api/calls/start", methods=["POST"])
@require_auth
def start_ai_calls():
    """Start AI calls via Pipecat agent (runs in background thread)."""
    from db_conn import get_conn
    from funnel_engine import FunnelEngine

    data = request.get_json() or {}
    industry = data.get("industry")
    limit = data.get("limit", 5)

    conn = get_conn()
    eng = FunnelEngine(conn)

    result_holder = {"calls": [], "done": False}

    def _run():
        try:
            result_holder["calls"] = eng.start_ai_calls(industry=industry, limit=limit)
        except Exception as e:
            result_holder["calls"] = [{"error": str(e)}]
        finally:
            result_holder["done"] = True

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({
        "status": "started",
        "message": f"Calling up to {limit} leads in background",
        "check_status_at": "/api/calls/status",
    })


@agent_bp.route("/api/calls/status")
@require_auth
def pipecat_status():
    """Check Pipecat agent status."""
    from pipecat_client import PipecatClient
    client = PipecatClient()
    return jsonify({
        "available": client.health(),
        "results": client.get_results() if client.health() else {},
    })

"""
Call Orchestrator API — endpoints for managing the auto-dialer.
"""
import logging
from flask import Blueprint, jsonify, request
from middleware import require_auth

logger = logging.getLogger("call_api")

call_bp = Blueprint("call", __name__)

# Singleton orchestrator instance (created lazily)
_orchestrator = None


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from call_orchestrator import CallOrchestrator
        _orchestrator = CallOrchestrator()
    return _orchestrator


@call_bp.route("/api/call/status")
@require_auth
def call_status():
    """Get orchestrator status: running, paused, queue stats, current lead."""
    orch = _get_orchestrator()
    return jsonify(orch.get_status())


@call_bp.route("/api/call/start", methods=["POST"])
@require_auth
def call_start():
    """Start the auto-dialer."""
    orch = _get_orchestrator()
    if orch.is_running:
        return jsonify({"error": "already_running"}), 409
    orch.start()
    return jsonify({"status": "started"})


@call_bp.route("/api/call/stop", methods=["POST"])
@require_auth
def call_stop():
    """Stop the auto-dialer."""
    orch = _get_orchestrator()
    orch.stop()
    return jsonify({"status": "stopped"})


@call_bp.route("/api/call/pause", methods=["POST"])
@require_auth
def call_pause():
    """Pause the auto-dialer (current call finishes)."""
    orch = _get_orchestrator()
    orch.pause()
    return jsonify({"status": "paused"})


@call_bp.route("/api/call/resume", methods=["POST"])
@require_auth
def call_resume():
    """Resume after pause."""
    orch = _get_orchestrator()
    orch.resume()
    return jsonify({"status": "resumed"})


@call_bp.route("/api/call/queue")
@require_auth
def call_queue_info():
    """Get queue stats and next leads."""
    from call_queue import get_queue_stats, get_next_batch
    stats = get_queue_stats()
    next_leads = get_next_batch(limit=20)
    return jsonify({"stats": stats, "next_leads": next_leads})


@call_bp.route("/api/call/history")
@require_auth
def call_history():
    """Get call history with pagination."""
    from db_conn import get_conn
    offset = int(request.args.get("offset", 0))
    limit = min(int(request.args.get("limit", 50)), 200)

    conn = get_conn()
    rows = conn.execute("""
        SELECT cl.*, l.company_name, l.mobile, l.industry
        FROM call_log cl
        JOIN leads l ON cl.lead_id = l.id
        ORDER BY cl.created_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    total = conn.execute("SELECT COUNT(*) as cnt FROM call_log").fetchone()["cnt"]

    return jsonify({
        "calls": [dict(r) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@call_bp.route("/api/call/schedule")
@require_auth
def call_schedule():
    """Get business hours info and retry stats."""
    from business_hours import get_schedule_info
    from retry_manager import get_retry_stats
    return jsonify({
        "schedule": get_schedule_info(),
        "retries": get_retry_stats(),
    })

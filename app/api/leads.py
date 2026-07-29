"""
Leads endpoints: list, detail, export, status update, next batch.
"""
from flask import Blueprint, request, jsonify
from middleware import require_auth
from db_conn import get_conn
from leads_db import update_lead_status

leads_bp = Blueprint("leads", __name__)


@require_auth
@leads_bp.route("/api/leads/list")
def leads_list():
    """Paginated, searchable, filterable leads list."""
    conn = get_conn()

    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    industry = request.args.get("industry", "").strip()
    score = request.args.get("score", "").strip()
    sort = request.args.get("sort", "id")
    direction = request.args.get("dir", "DESC").upper()
    offset = int(request.args.get("offset", 0))
    limit = min(int(request.args.get("limit", 50)), 200)

    sort_map = {
        "id": "l.id", "company_name": "l.company_name",
        "industry": "l.industry", "status": "l.status", "mobile": "l.mobile",
    }
    if sort not in sort_map:
        sort = "id"
    if direction not in ("ASC", "DESC"):
        direction = "DESC"

    where = ["1=1"]
    params = []

    if q:
        where.append(
            "(l.company_name LIKE ? OR l.mobile LIKE ? OR l.phone LIKE ? "
            "OR l.email LIKE ? OR l.whatsapp LIKE ?)"
        )
        params.extend([f"%{q}%"] * 5)
    if status:
        where.append("l.status = ?")
        params.append(status)
    if industry:
        where.append("l.industry = ?")
        params.append(industry)
    if score:
        where.append("s.category = ?")
        params.append(score)

    where_clause = " AND ".join(where)

    count_row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM leads l "
        f"LEFT JOIN lead_scores s ON l.id = s.lead_id "
        f"WHERE {where_clause}", params
    ).fetchone()
    total = count_row["cnt"] or 0

    rows = conn.execute(
        f"SELECT l.*, s.score, s.category as score_category, s.reasoning "
        f"FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id "
        f"WHERE {where_clause} ORDER BY {sort_map[sort]} {direction} "
        f"LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    leads = [dict(r) for r in rows]
    return jsonify({"leads": leads, "total": total, "offset": offset, "limit": limit})


@require_auth
@leads_bp.route("/api/leads/<int:lead_id>")
def lead_detail(lead_id):
    """Get full lead detail with conversation history, sentiment, score."""
    conn = get_conn()

    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return jsonify({"error": "Not found"}), 404

    lead_dict = dict(lead)

    score_row = conn.execute(
        "SELECT * FROM lead_scores WHERE lead_id = ?", (lead_id,)
    ).fetchone()
    score = dict(score_row) if score_row else None

    ctx_row = conn.execute(
        "SELECT * FROM lead_context WHERE lead_id = ?", (lead_id,)
    ).fetchone()
    context = dict(ctx_row) if ctx_row else None

    history_rows = conn.execute(
        "SELECT * FROM conversations WHERE lead_id = ? ORDER BY created_at DESC LIMIT 20",
        (lead_id,),
    ).fetchall()
    history = [dict(r) for r in reversed(history_rows)]

    sentiment_rows = conn.execute(
        "SELECT * FROM sentiment_log WHERE lead_id = ? ORDER BY analyzed_at DESC LIMIT 10",
        (lead_id,),
    ).fetchall()
    sentiment = [dict(r) for r in sentiment_rows]

    return jsonify({
        "lead": lead_dict, "score": score, "context": context,
        "history": history, "sentiment": sentiment,
    })


@require_auth
@leads_bp.route("/api/leads/export")
def leads_export():
    """Export leads as CSV-compatible JSON."""
    conn = get_conn()

    ids = request.args.get("ids", "").strip()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    industry = request.args.get("industry", "").strip()

    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        placeholders = ",".join("?" * len(id_list))
        rows = conn.execute(
            f"SELECT l.*, COALESCE(s.score, 0) as score "
            f"FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id "
            f"WHERE l.id IN ({placeholders})", id_list
        ).fetchall()
    else:
        where = ["1=1"]
        params = []
        if q:
            where.append("(l.company_name LIKE ? OR l.mobile LIKE ? OR l.email LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        if status:
            where.append("l.status = ?")
            params.append(status)
        if industry:
            where.append("l.industry = ?")
            params.append(industry)
        where_clause = " AND ".join(where)
        rows = conn.execute(
            f"SELECT l.*, COALESCE(s.score, 0) as score "
            f"FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id "
            f"WHERE {where_clause} LIMIT 5000", params
        ).fetchall()

    leads = [dict(r) for r in rows]
    return jsonify({"leads": leads})


@require_auth
@leads_bp.route("/api/leads/next")
def next_leads():
    """Get next batch of leads to call."""
    from funnel_engine import FunnelEngine
    conn = get_conn()
    eng = FunnelEngine(conn)
    industry = request.args.get("industry")
    limit = int(request.args.get("limit", 10))
    leads = eng.run_batch(industry=industry, limit=limit)
    return jsonify({"leads": leads, "count": len(leads)})


@require_auth
@leads_bp.route("/api/leads/<int:lead_id>/status", methods=["PUT"])
def update_status(lead_id):
    """Manually update lead status."""
    data = request.get_json()
    status = data.get("status")
    notes = data.get("notes", "")
    conn = get_conn()
    update_lead_status(conn, lead_id, status, notes)
    return jsonify({"ok": True})


@require_auth
@leads_bp.route("/api/leads/<int:lead_id>/timeline")
def lead_timeline(lead_id):
    """Get unified timeline for a lead — all events chronologically."""
    from agent_sync import get_lead_timeline
    limit = int(request.args.get("limit", 50))
    event_type = request.args.get("event_type", "").strip() or None
    events = get_lead_timeline(lead_id, limit=limit, event_type=event_type)
    return jsonify({"events": events, "count": len(events)})


@require_auth
@leads_bp.route("/api/industries")
def industries():
    """Get list of industries from the database."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT industry FROM leads WHERE industry != '' ORDER BY industry"
    ).fetchall()
    return jsonify({"industries": [r["industry"] for r in rows]})


@leads_bp.route("/api/leads/sync", methods=["POST"])
@require_auth
def leads_sync():
    """Sync leads from local/external source — upsert by mobile."""
    from app.services.lead_sync_service import sync_leads_from_local

    data = request.get_json()
    if not data or "leads" not in data:
        return jsonify({"error": "JSON body with 'leads' array required"}), 400

    leads = data["leads"]
    if not isinstance(leads, list):
        return jsonify({"error": "'leads' must be an array"}), 400

    result = sync_leads_from_local(leads)
    return jsonify(result)


@require_auth
@leads_bp.route("/api/call/result", methods=["POST"])
def call_result():
    """Receive call result from Technomax webhook."""
    from funnel_engine import FunnelEngine, CallResult
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON required"}), 400

    lead_id = data.get("lead_id")
    result = data.get("result")
    notes = data.get("notes", "")

    if not lead_id or not result:
        return jsonify({"error": "lead_id and result required"}), 400

    valid_results = [
        CallResult.INTERESTED, CallResult.CALLBACK, CallResult.REFUSED,
        CallResult.NO_ANSWER, CallResult.WRONG_NUMBER, CallResult.VOICEMAIL,
    ]
    if result not in valid_results:
        return jsonify({"error": f"Invalid result. Valid: {valid_results}"}), 400

    conn = get_conn()
    eng = FunnelEngine(conn)
    actions = eng.process_call_result(lead_id, result, notes)
    return jsonify({"actions": actions})

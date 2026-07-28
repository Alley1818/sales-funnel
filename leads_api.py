"""
Leads API endpoints for the new UI: list, search, export, detail.
"""
import json
import csv
import io
import logging
from flask import Flask, request, jsonify

logger = logging.getLogger("leads_api")


def register_leads_routes(app: Flask):

    @app.route("/api/leads/list")
    def leads_list():
        """Paginated, searchable, filterable leads list."""
        from middleware import get_pooled_conn
        conn = get_pooled_conn()

        q = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        industry = request.args.get("industry", "").strip()
        score = request.args.get("score", "").strip()
        sort = request.args.get("sort", "id")
        direction = request.args.get("dir", "DESC").upper()
        offset = int(request.args.get("offset", 0))
        limit = min(int(request.args.get("limit", 50)), 200)

        # Validate sort column
        allowed_sorts = {"id", "company_name", "industry", "status", "mobile"}
        if sort not in allowed_sorts:
            sort = "id"
        if direction not in ("ASC", "DESC"):
            direction = "DESC"

        where = ["1=1"]
        params = []

        if q:
            where.append("(l.company_name LIKE ? OR l.mobile LIKE ? OR l.phone LIKE ? OR l.email LIKE ? OR l.whatsapp LIKE ?)")
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

        # Count
        count_row = conn.execute(f"""
            SELECT COUNT(*) as cnt FROM leads l
            LEFT JOIN lead_scores s ON l.id = s.lead_id
            WHERE {where_clause}
        """, params).fetchone()
        total = count_row["cnt"] or 0

        # Fetch
        rows = conn.execute(f"""
            SELECT l.*, s.score, s.category as score_category, s.reasoning
            FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id
            WHERE {where_clause}
            ORDER BY {sort} {direction}
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        leads = [dict(r) for r in rows]
        return jsonify({"leads": leads, "total": total, "offset": offset, "limit": limit})

    @app.route("/api/leads/<int:lead_id>")
    def lead_detail(lead_id):
        """Get full lead detail with conversation history, sentiment, score."""
        from middleware import get_pooled_conn
        conn = get_pooled_conn()

        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return jsonify({"error": "Not found"}), 404

        lead_dict = dict(lead)

        # Score
        score_row = conn.execute("SELECT * FROM lead_scores WHERE lead_id = ?", (lead_id,)).fetchone()
        score = dict(score_row) if score_row else None

        # Context
        ctx_row = conn.execute("SELECT * FROM lead_context WHERE lead_id = ?", (lead_id,)).fetchone()
        context = dict(ctx_row) if ctx_row else None

        # Conversation history
        history_rows = conn.execute(
            "SELECT * FROM conversations WHERE lead_id = ? ORDER BY created_at DESC LIMIT 20",
            (lead_id,)
        ).fetchall()
        history = [dict(r) for r in reversed(history_rows)]

        # Sentiment
        sentiment_rows = conn.execute(
            "SELECT * FROM sentiment_log WHERE lead_id = ? ORDER BY analyzed_at DESC LIMIT 10",
            (lead_id,)
        ).fetchall()
        sentiment = [dict(r) for r in sentiment_rows]

        return jsonify({
            "lead": lead_dict,
            "score": score,
            "context": context,
            "history": history,
            "sentiment": sentiment,
        })

    @app.route("/api/leads/export")
    def leads_export():
        """Export leads as CSV-compatible JSON."""
        from middleware import get_pooled_conn
        conn = get_pooled_conn()

        ids = request.args.get("ids", "").strip()
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        industry = request.args.get("industry", "").strip()

        if ids:
            id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
            placeholders = ",".join("?" * len(id_list))
            rows = conn.execute(f"""
                SELECT l.*, COALESCE(s.score, 0) as score
                FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id
                WHERE l.id IN ({placeholders})
            """, id_list).fetchall()
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
            rows = conn.execute(f"""
                SELECT l.*, COALESCE(s.score, 0) as score
                FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id
                WHERE {where_clause} LIMIT 5000
            """, params).fetchall()

        leads = [dict(r) for r in rows]
        return jsonify({"leads": leads})

    @app.route("/api/industries")
    def industries():
        """Get list of industries from the database."""
        from middleware import get_pooled_conn
        conn = get_pooled_conn()
        rows = conn.execute("SELECT DISTINCT industry FROM leads WHERE industry != '' ORDER BY industry").fetchall()
        return jsonify({"industries": [r["industry"] for r in rows]})

"""
Core endpoints: index, health, stats, report.
"""
from flask import Blueprint, jsonify, render_template, current_app
from middleware import require_auth
from leads_db import init_db, get_stats, get_industry_stats

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
def index():
    return render_template("index.html")


@core_bp.route("/manifest.json")
def pwa_manifest():
    return current_app.send_static_file("manifest.json")


@core_bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@core_bp.route("/health/db")
def health_db():
    """Debug endpoint — check DB state without auth."""
    try:
        conn = init_db()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        lead_count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        return jsonify({"ok": True, "tables": tables, "leads": lead_count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@core_bp.route("/health/add-test-lead")
def add_test_lead():
    """One-shot debug: insert test lead."""
    try:
        conn = init_db()
        existing = conn.execute(
            "SELECT id FROM leads WHERE mobile = ?", ("77026586714",)
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "id": existing["id"], "msg": "already exists"})
        cur = conn.execute(
            "INSERT INTO leads (company_name, industry, mobile, whatsapp, email, status) VALUES (?,?,?,?,?,?)",
            ("Test Company", "IT", "77026586714", "77026586714", "alizeinolla@gmail.com", "new"),
        )
        conn.commit()
        return jsonify({"ok": True, "id": cur.lastrowid, "msg": "created"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@core_bp.route("/api/stats")
@require_auth
def stats():
    """Get funnel statistics."""
    from db_conn import get_conn
    conn = get_conn()
    return jsonify({
        "overall": get_stats(conn),
        "by_industry": get_industry_stats(conn),
    })


@core_bp.route("/api/report")
@require_auth
def report():
    """Get text funnel report."""
    from funnel_engine import FunnelEngine
    from db_conn import get_conn
    eng = FunnelEngine(get_conn())
    return jsonify({"report": eng.get_funnel_report()})

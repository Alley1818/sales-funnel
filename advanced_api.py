"""
API endpoints for advanced features: RAG, sentiment, scoring, callbacks, transfers, analytics, auth.
"""
import json
import logging
from flask import Flask, request, jsonify, session
from middleware import require_auth

logger = logging.getLogger("advanced_api")


def register_advanced_routes(app: Flask):
    """Register all advanced feature routes."""

    # ==================== RAG KNOWLEDGE BASE ====================

    @require_auth
    @app.route("/api/knowledge", methods=["GET"])
    def list_knowledge():
        from advanced_features import get_documents
        return jsonify({"documents": get_documents()})

    @require_auth
    @app.route("/api/knowledge", methods=["POST"])
    def add_knowledge():
        from advanced_features import add_document, log_action
        data = request.get_json() or {}
        if not data.get("title") or not data.get("content"):
            return jsonify({"error": "title and content required"}), 400
        doc_id = add_document(
            title=data["title"],
            content=data["content"],
            doc_type=data.get("doc_type", "text"),
            industry=data.get("industry", ""),
            file_path=data.get("file_path", ""),
        )
        log_action("knowledge_add", "knowledge", doc_id, f"Added: {data['title']}", request.remote_addr)
        return jsonify({"ok": True, "id": doc_id})

    @require_auth
    @app.route("/api/knowledge/<int:doc_id>", methods=["DELETE"])
    def del_knowledge(doc_id):
        from advanced_features import delete_document, log_action
        delete_document(doc_id)
        log_action("knowledge_delete", "knowledge", doc_id, "", request.remote_addr)
        return jsonify({"ok": True})

    @require_auth
    @app.route("/api/knowledge/search", methods=["POST"])
    def search_knowledge():
        from advanced_features import search_knowledge, get_rag_context
        data = request.get_json() or {}
        query = data.get("query", "")
        industry = data.get("industry", "")
        if not query:
            return jsonify({"error": "query required"}), 400
        chunks = search_knowledge(query, industry)
        context = get_rag_context(query, industry)
        return jsonify({"chunks": chunks, "context": context, "count": len(chunks)})

    # ==================== SENTIMENT ====================

    @require_auth
    @app.route("/api/sentiment/<int:lead_id>", methods=["GET"])
    def get_sentiment(lead_id):
        from advanced_features import get_sentiment_history
        return jsonify({"history": get_sentiment_history(lead_id)})

    @require_auth
    @app.route("/api/sentiment/analyze", methods=["POST"])
    def analyze():
        from advanced_features import log_sentiment
        data = request.get_json() or {}
        if not data.get("text"):
            return jsonify({"error": "text required"}), 400
        result = log_sentiment(
            lead_id=data.get("lead_id", 0),
            channel=data.get("channel", "unknown"),
            message=data["text"],
        )
        return jsonify(result)

    # ==================== AUTO SCORING ====================

    @require_auth
    @app.route("/api/scores/batch", methods=["POST"])
    def batch_score():
        from advanced_features import batch_score_leads
        data = request.get_json() or {}
        limit = data.get("limit", 100)
        scored = batch_score_leads(limit)
        return jsonify({"ok": True, "scored": scored})

    # ==================== CALLBACKS ====================

    @require_auth
    @app.route("/api/callbacks", methods=["GET"])
    def list_callbacks():
        from advanced_features import get_pending_callbacks
        return jsonify({"callbacks": get_pending_callbacks()})

    @require_auth
    @app.route("/api/callbacks", methods=["POST"])
    def add_callback():
        from advanced_features import schedule_callback, log_action
        data = request.get_json() or {}
        if not data.get("lead_id") or not data.get("scheduled_at"):
            return jsonify({"error": "lead_id and scheduled_at required"}), 400
        cid = schedule_callback(
            lead_id=data["lead_id"],
            scheduled_at=data["scheduled_at"],
            channel=data.get("channel", "voice"),
            notes=data.get("notes", ""),
        )
        log_action("callback_scheduled", "callback", cid, f"Lead {data['lead_id']}", request.remote_addr)
        return jsonify({"ok": True, "id": cid})

    @require_auth
    @app.route("/api/callbacks/<int:cid>/complete", methods=["POST"])
    def complete_cb(cid):
        from advanced_features import complete_callback
        data = request.get_json() or {}
        complete_callback(cid, data.get("status", "completed"))
        return jsonify({"ok": True})

    @require_auth
    @app.route("/api/callbacks/due", methods=["GET"])
    def due_callbacks():
        from advanced_features import get_due_callbacks
        return jsonify({"callbacks": get_due_callbacks()})

    # ==================== TRANSFERS ====================

    @require_auth
    @app.route("/api/transfers", methods=["GET"])
    def list_transfers():
        from advanced_features import get_pending_transfers
        return jsonify({"transfers": get_pending_transfers()})

    @require_auth
    @app.route("/api/transfers", methods=["POST"])
    def add_transfer():
        from advanced_features import request_transfer, log_action
        data = request.get_json() or {}
        if not data.get("lead_id"):
            return jsonify({"error": "lead_id required"}), 400
        tid = request_transfer(
            lead_id=data["lead_id"],
            reason=data.get("reason", ""),
            channel=data.get("channel", "voice"),
            manager_phone=data.get("manager_phone", ""),
            manager_chat_id=data.get("manager_chat_id", ""),
        )
        log_action("transfer_requested", "transfer", tid, f"Lead {data['lead_id']}", request.remote_addr)
        return jsonify({"ok": True, "id": tid})

    @require_auth
    @app.route("/api/transfers/<int:tid>/accept", methods=["POST"])
    def accept_transfer(tid):
        from advanced_features import complete_transfer
        complete_transfer(tid, "accepted")
        return jsonify({"ok": True})

    # ==================== ANALYTICS ====================

    @require_auth
    @app.route("/api/analytics/dashboard", methods=["GET"])
    def analytics_dashboard():
        from advanced_features import get_dashboard_data
        return jsonify(get_dashboard_data())

    @require_auth
    @app.route("/api/analytics/roi", methods=["GET"])
    def analytics_roi():
        from advanced_features import get_roi_data
        cid = int(request.args.get("campaign_id", 0))
        return jsonify(get_roi_data(cid))

    # ==================== AUTH ====================

    @app.route("/api/auth/login", methods=["POST"])
    def login():
        from middleware import verify_password, create_session, log_api_action
        data = request.get_json() or {}
        username = data.get("username", "")
        password = data.get("password", "")
        if verify_password(username, password):
            token = create_session(username)
            log_api_action("login", "auth", 0, f"User {username} logged in")
            return jsonify({"ok": True, "token": token})
        return jsonify({"error": "Invalid credentials"}), 401

    # ==================== ACTION LOG ====================

    @require_auth
    @app.route("/api/action-log", methods=["GET"])
    def action_log():
        from advanced_features import get_action_log
        limit = int(request.args.get("limit", 50))
        return jsonify({"log": get_action_log(limit)})

    # ==================== AUTO-SCORE ALL LEADS ====================

    @require_auth
    @app.route("/api/scores/auto-score-all", methods=["POST"])
    def auto_score_all():
        from advanced_features import batch_score_leads
        scored = batch_score_leads(2000)
        return jsonify({"ok": True, "scored": scored})

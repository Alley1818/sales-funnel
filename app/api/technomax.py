"""
Technomax platform endpoints: dashboard, tasks.
"""
from flask import Blueprint, jsonify
from middleware import require_auth

technomax_bp = Blueprint("technomax", __name__)


@technomax_bp.route("/api/technomax/dashboard")
@require_auth
def technomax_dashboard():
    """Get Technomax platform data: tasks, bots, agents, call stats."""
    import httpx as _httpx
    from technomax_client import technomax
    try:
        with _httpx.Client(timeout=15) as _client:
            technomax._ensure_token_sync(_client)
            data = technomax.get_dashboard_data_sync(_client)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@technomax_bp.route("/api/technomax/tasks")
@require_auth
def technomax_tasks():
    """List autocall tasks from Technomax."""
    import httpx as _httpx
    from technomax_client import technomax
    try:
        with _httpx.Client(timeout=15) as _client:
            technomax._ensure_token_sync(_client)
            data = technomax.get_autocall_tasks_sync(_client)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@technomax_bp.route("/api/technomax/tasks/<task_id>")
@require_auth
def technomax_task_detail(task_id):
    """Get autocall task detail from Technomax."""
    import httpx as _httpx
    from technomax_client import technomax
    try:
        with _httpx.Client(timeout=15) as _client:
            technomax._ensure_token_sync(_client)
            data = technomax.get_autocall_detail_sync(_client, task_id)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

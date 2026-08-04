"""
WhatsApp endpoints: send, status, QR, connect.
"""
from flask import Blueprint, request, jsonify
from middleware import require_auth

whatsapp_bp = Blueprint("whatsapp", __name__)


@whatsapp_bp.route("/api/whatsapp/send", methods=["POST"])
@require_auth
def send_whatsapp():
    """Send WhatsApp message manually."""
    from whatsapp_client import WhatsAppClient
    data = request.get_json()
    phone = data.get("phone")
    message = data.get("message")
    if not phone or not message:
        return jsonify({"error": "phone and message required"}), 400

    client = WhatsAppClient()
    result = client.send_text(phone, message)
    return jsonify({
        "success": result.success,
        "message_id": result.message_id,
        "error": result.error,
    })


@whatsapp_bp.route("/api/whatsapp/status")
@require_auth
def whatsapp_status():
    """Get WhatsApp connection state."""
    from whatsapp_client import WhatsAppClient
    client = WhatsAppClient()
    state = client.get_connection_state()
    return jsonify({"state": state})


@whatsapp_bp.route("/api/whatsapp/qr")
@require_auth
def whatsapp_qr():
    """Get WhatsApp QR code for connecting."""
    from whatsapp_client import WhatsAppClient
    client = WhatsAppClient()
    qr = client.get_qr_code()
    state = client.get_connection_state()
    return jsonify({"state": state, "qr": qr})


@whatsapp_bp.route("/api/whatsapp/connect", methods=["POST"])
@require_auth
def whatsapp_connect():
    """Create/initialize WhatsApp instance."""
    from whatsapp_client import WhatsAppClient
    client = WhatsAppClient()
    try:
        result = client.create_instance()
        return jsonify({"ok": True, "instance": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/api/whatsapp/qr-image")
@require_auth
def whatsapp_qr_image():
    """Get QR code as base64 image for embedding in UI."""
    from whatsapp_client import WhatsAppClient
    client = WhatsAppClient()
    state = client.get_connection_state()
    if state == "open":
        return jsonify({"state": "open", "qr": None})
    qr = client.get_qr_code()
    return jsonify({"state": state, "qr": qr})


@whatsapp_bp.route("/api/whatsapp/info")
@require_auth
def whatsapp_info():
    """Get WhatsApp instance info including connected number."""
    import os
    import requests as req

    evo_url = os.environ.get("EVO_API_URL", "http://evolution_api:8080")
    evo_key = os.environ.get("EVO_API_KEY", "")
    instance = os.environ.get("EVO_INSTANCE", "sales_funnel")

    if not evo_key:
        return jsonify({"error": "EVO_API_KEY not set"}), 500

    try:
        r = req.get(f"{evo_url}/instance/fetchInstances",
                     headers={"apikey": evo_key}, timeout=10)
        instances = r.json() if r.status_code == 200 else []
        for i in instances:
            name = i.get("instance", {}).get("instanceName") or i.get("instanceName")
            if name == instance:
                jid = i.get("ownerJid") or i.get("instance", {}).get("ownerJid") or ""
                phone = jid.replace("@s.whatsapp.net", "") if jid else ""
                return jsonify({
                    "connected": i.get("connectionStatus") == "open",
                    "phone": phone,
                    "profile_name": i.get("profileName"),
                    "instance": instance,
                    "version": "Evolution API v2.3.2",
                })
        return jsonify({"error": "Instance not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

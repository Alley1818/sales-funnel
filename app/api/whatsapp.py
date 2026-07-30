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

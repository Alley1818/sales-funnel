"""
Config endpoints: SMTP, notifications, CPS.
"""
import os
from flask import Blueprint, request, jsonify, current_app
from middleware import require_auth

config_bp = Blueprint("config", __name__)


# ---- SMTP Config ----

@require_auth
@config_bp.route("/api/config/smtp", methods=["GET"])
def get_smtp_config():
    """Get SMTP configuration (password masked)."""
    cfg = current_app.config["load_config"]()
    smtp = cfg.get("smtp", {})
    return jsonify({
        "host": smtp.get("host", "smtp.mail.ru"),
        "port": smtp.get("port", 465),
        "username": smtp.get("username", ""),
        "from_name": smtp.get("from_name", "Technomax"),
        "configured": bool(smtp.get("username") and smtp.get("password")),
    })


@require_auth
@config_bp.route("/api/config/smtp", methods=["POST"])
def set_smtp_config():
    """Save SMTP configuration."""
    data = request.get_json()
    cfg = current_app.config["load_config"]()
    cfg["smtp"] = {
        "host": data.get("host", "smtp.mail.ru"),
        "port": data.get("port", 465),
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "from_name": data.get("from_name", "Technomax"),
    }
    current_app.config["save_config"](cfg)

    os.environ["SMTP_HOST"] = cfg["smtp"]["host"]
    os.environ["SMTP_PORT"] = str(cfg["smtp"]["port"])
    os.environ["SMTP_USERNAME"] = cfg["smtp"]["username"]
    os.environ["SMTP_PASSWORD"] = cfg["smtp"]["password"]
    os.environ["SMTP_FROM_NAME"] = cfg["smtp"]["from_name"]

    return jsonify({"ok": True})


@require_auth
@config_bp.route("/api/config/smtp/test", methods=["POST"])
def test_smtp():
    """Send a test email."""
    data = request.get_json() or {}
    to_email = data.get("to", "")
    if not to_email:
        return jsonify({"error": "to email required"}), 400

    cfg = current_app.config["load_config"]()
    smtp_cfg = cfg.get("smtp", {})
    if not smtp_cfg.get("username") or not smtp_cfg.get("password"):
        return jsonify({"error": "SMTP not configured"}), 400

    os.environ["SMTP_HOST"] = smtp_cfg.get("host", "smtp.mail.ru")
    os.environ["SMTP_PORT"] = str(smtp_cfg.get("port", 465))
    os.environ["SMTP_USERNAME"] = smtp_cfg["username"]
    os.environ["SMTP_PASSWORD"] = smtp_cfg["password"]
    os.environ["SMTP_FROM_NAME"] = smtp_cfg.get("from_name", "Technomax")

    from email_sender import EmailSender
    sender = EmailSender()
    result = sender.send(
        to_email=to_email,
        subject="Test Email - Sales Funnel",
        body_html="<h2>Test</h2><p>Email configuration is working correctly.</p>",
    )
    return jsonify({"success": result.success, "error": result.error})


# ---- Notification Settings ----

@require_auth
@config_bp.route("/api/config/notifications", methods=["GET"])
def get_notifications():
    """Get notification config."""
    cfg = current_app.config["load_config"]()
    return jsonify({
        "auto_send": cfg.get("auto_send", False),
        "telegram_bot_token": bool(cfg.get("telegram_bot_token", "")),
        "telegram_chat_id": cfg.get("telegram_chat_id", ""),
        "require_approval": cfg.get("require_approval", True),
    })


@require_auth
@config_bp.route("/api/config/notifications", methods=["POST"])
def set_notifications():
    """Update notification config."""
    data = request.get_json() or {}
    cfg = current_app.config["load_config"]()

    if "auto_send" in data:
        cfg["auto_send"] = bool(data["auto_send"])
    if "telegram_bot_token" in data:
        cfg["telegram_bot_token"] = data["telegram_bot_token"]
    if "telegram_chat_id" in data:
        cfg["telegram_chat_id"] = data["telegram_chat_id"]
    if "require_approval" in data:
        cfg["require_approval"] = bool(data["require_approval"])

    current_app.config["save_config"](cfg)
    return jsonify({"ok": True, "auto_send": cfg.get("auto_send", False)})


@require_auth
@config_bp.route("/api/config/notifications/test", methods=["POST"])
def test_notification():
    """Send a test Telegram notification."""
    from telegram_notifier import _send_telegram_sync
    ok = _send_telegram_sync("<b>Test</b> — Sales Funnel Telegram notification works!")
    return jsonify({"success": ok})

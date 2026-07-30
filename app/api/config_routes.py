"""
Config endpoints: SMTP, notifications, CPS.
"""
import os
from flask import Blueprint, request, jsonify, current_app
from middleware import require_auth

config_bp = Blueprint("config", __name__)


# ---- SMTP Config ----

@config_bp.route("/api/config/smtp", methods=["GET"])
@require_auth
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


@config_bp.route("/api/config/smtp", methods=["POST"])
@require_auth
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


@config_bp.route("/api/config/smtp/test", methods=["POST"])
@require_auth
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

@config_bp.route("/api/config/notifications", methods=["GET"])
@require_auth
def get_notifications():
    """Get notification config."""
    cfg = current_app.config["load_config"]()
    return jsonify({
        "auto_send": cfg.get("auto_send", False),
        "telegram_bot_token": bool(cfg.get("telegram_bot_token", "")),
        "telegram_chat_id": cfg.get("telegram_chat_id", ""),
        "require_approval": cfg.get("require_approval", True),
    })


@config_bp.route("/api/config/notifications", methods=["POST"])
@require_auth
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


@config_bp.route("/api/config/notifications/test", methods=["POST"])
@require_auth
def test_notification():
    """Send a test Telegram notification."""
    from telegram_notifier import _send_telegram_sync
    ok = _send_telegram_sync("<b>Test</b> — Sales Funnel Telegram notification works!")
    return jsonify({"success": ok})


# ---- LLM Config ----

@config_bp.route("/api/config/llm", methods=["GET"])
@require_auth
def get_llm_config():
    """Get LLM configuration."""
    cfg = current_app.config["load_config"]()
    llm = cfg.get("llm", {})
    return jsonify({
        "provider": llm.get("provider", "ollama"),
        "model": llm.get("model", "qwen2.5:1.5b"),
        "base_url": llm.get("base_url", "http://ollama:11434"),
        "temperature": llm.get("temperature", 0.3),
        "max_tokens": llm.get("max_tokens", 500),
        "api_key_set": bool(llm.get("api_key")),
    })


@config_bp.route("/api/config/llm", methods=["POST"])
@require_auth
def set_llm_config():
    """Update LLM configuration."""
    data = request.get_json() or {}
    cfg = current_app.config["load_config"]()

    if "llm" not in cfg:
        cfg["llm"] = {}

    llm = cfg["llm"]
    if "provider" in data:
        llm["provider"] = data["provider"]
    if "model" in data:
        llm["model"] = data["model"]
    if "base_url" in data:
        llm["base_url"] = data["base_url"]
    if "temperature" in data:
        llm["temperature"] = float(data["temperature"])
    if "max_tokens" in data:
        llm["max_tokens"] = int(data["max_tokens"])
    if "api_key" in data:
        llm["api_key"] = data["api_key"]

    cfg["llm"] = llm
    current_app.config["save_config"](cfg)
    return jsonify({"ok": True})


@config_bp.route("/api/config/llm/test", methods=["POST"])
@require_auth
def test_llm_connection():
    """Test LLM connection by sending a simple prompt."""
    import requests as req

    cfg = current_app.config["load_config"]()
    llm = cfg.get("llm", {})
    provider = llm.get("provider", "ollama")
    model = llm.get("model", "qwen2.5:1.5b")
    base_url = llm.get("base_url", "http://ollama:11434")
    api_key = llm.get("api_key", "")
    temperature = llm.get("temperature", 0.3)
    max_tokens = llm.get("max_tokens", 500)

    test_prompt = "Say hello in one sentence."

    try:
        if provider == "ollama":
            url = f"{base_url.rstrip('/')}/api/chat"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": test_prompt}],
                "stream": False,
                "options": {"temperature": temperature, "num_predict": 50},
            }
            resp = req.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            reply = data.get("message", {}).get("content", "")
        elif provider in ("openrouter", "groq", "custom"):
            if provider == "groq":
                url = f"{base_url.rstrip('/')}/chat/completions"
                if not api_key:
                    return jsonify({"success": False, "error": "Groq API key is required"})
            elif provider == "openrouter":
                url = "https://openrouter.ai/api/v1/chat/completions"
                if not api_key:
                    return jsonify({"success": False, "error": "OpenRouter API key is required"})
            else:
                url = f"{base_url.rstrip('/')}/chat/completions"
                if not url.startswith("http"):
                    url = f"https://{url}"
                if not api_key:
                    return jsonify({"success": False, "error": "API key is required for this provider"})
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": test_prompt}],
                "temperature": temperature,
                "max_tokens": 50,
            }
            resp = req.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
        else:
            return jsonify({"success": False, "error": f"Unknown provider: {provider}"})

        return jsonify({
            "success": True,
            "reply": reply.strip()[:200],
            "provider": provider,
            "model": model,
        })
    except req.Timeout:
        return jsonify({"success": False, "error": "Timeout: model did not respond in 30s"})
    except req.ConnectionError:
        return jsonify({"success": False, "error": f"Connection failed: {base_url}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:300]})


# ---- Fallback LLM Config ----

@config_bp.route("/api/config/llm-fallback", methods=["GET"])
@require_auth
def get_llm_fallback():
    """Get fallback LLM config (masks api_key)."""
    cfg = current_app.config["load_config"]()
    fb = cfg.get("llm_fallback", {})
    key = fb.get("api_key", "")
    return jsonify({
        "provider": fb.get("provider", "ollama"),
        "model": fb.get("model", ""),
        "base_url": fb.get("base_url", ""),
        "api_key_set": bool(key),
        "api_key_preview": f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "",
        "temperature": fb.get("temperature", 0.3),
        "max_tokens": fb.get("max_tokens", 500),
    })


@config_bp.route("/api/config/llm-fallback", methods=["POST"])
@require_auth
def set_llm_fallback():
    """Update fallback LLM config."""
    data = request.get_json() or {}
    cfg = current_app.config["load_config"]()
    fb = cfg.get("llm_fallback", {})
    if "provider" in data:
        fb["provider"] = data["provider"]
    if "model" in data:
        fb["model"] = data["model"]
    if "base_url" in data:
        fb["base_url"] = data["base_url"]
    if "temperature" in data:
        fb["temperature"] = float(data["temperature"])
    if "max_tokens" in data:
        fb["max_tokens"] = int(data["max_tokens"])
    if "api_key" in data:
        fb["api_key"] = data["api_key"]
    cfg["llm_fallback"] = fb
    current_app.config["save_config"](cfg)
    return jsonify({"ok": True})


@config_bp.route("/api/config/llm-fallback/test", methods=["POST"])
@require_auth
def test_llm_fallback():
    """Test fallback LLM connection."""
    import requests as req
    cfg = current_app.config["load_config"]()
    fb = cfg.get("llm_fallback", {})
    provider = fb.get("provider", "ollama")
    model = fb.get("model", "")
    base_url = fb.get("base_url", "")
    api_key = fb.get("api_key", "")

    test_prompt = "Say hello in one sentence."
    try:
        if not api_key and not base_url:
            return jsonify({"success": False, "error": "Fallback not configured"})
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": test_prompt}], "max_tokens": 50}
        resp = req.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        reply = data["choices"][0]["message"]["content"]
        return jsonify({"success": True, "reply": reply.strip()[:200], "provider": provider, "model": model})
    except req.Timeout:
        return jsonify({"success": False, "error": "Timeout"})
    except req.ConnectionError:
        return jsonify({"success": False, "error": f"Connection failed: {base_url}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:300]})


# ---- WA Agent Prompt ----

@config_bp.route("/api/config/wa-prompt", methods=["GET"])
@require_auth
def get_wa_prompt():
    """Get WhatsApp agent prompt."""
    cfg = current_app.config["load_config"]()
    return jsonify({"prompt": cfg.get("wa_agent_prompt", "")})


@config_bp.route("/api/config/wa-prompt", methods=["POST"])
@require_auth
def set_wa_prompt():
    """Update WhatsApp agent prompt."""
    data = request.get_json() or {}
    cfg = current_app.config["load_config"]()
    cfg["wa_agent_prompt"] = data.get("prompt", "")
    current_app.config["save_config"](cfg)
    return jsonify({"ok": True})

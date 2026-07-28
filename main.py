"""
Sales Funnel — main entry point.
Provides a simple API server for Technomax webhook integration
and CLI for manual operations.
"""
import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, request, jsonify, render_template

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from leads_db import init_db, get_stats, get_industry_stats, update_lead_status, get_leads_by_status
from funnel_engine import FunnelEngine, FunnelConfig, CallResult
from whatsapp_client import WhatsAppClient, WhatsAppConfig
from email_sender import EmailSender, EmailConfig

LOG_FILE = Path(__file__).parent / "sales_funnel.log"

file_handler = RotatingFileHandler(
    str(LOG_FILE), maxBytes=10 * 1024 * 1024, backupCount=5  # 10 MB, 5 backups
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(), file_handler],
)
logger = logging.getLogger("sales_funnel")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())

# CSRF protection for state-changing requests
try:
    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect(app)
except ImportError:
    logger.warning("flask-wtf not installed — CSRF protection disabled")


@app.teardown_appcontext
def shutdown_session(exception=None):
    pass  # Connection pool handles cleanup

# ---- Config file path ----
CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> dict:
    """Load config from file."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(cfg: dict):
    """Save config to file."""
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


# ---- Global state ----
db_conn = None
engine = None


def get_engine() -> FunnelEngine:
    global engine, db_conn
    if engine is None:
        db_conn = init_db()
        engine = FunnelEngine(db_conn)
    return engine


# ---- Initialize extended tables and seed data ----
from db_extended import init_extended_tables
from funnel_features import seed_default_templates
init_extended_tables()
seed_default_templates()

# ---- Register feature API routes ----
from features_api import register_feature_routes
register_feature_routes(app)

# ---- Initialize advanced tables ----
from advanced_features import init_advanced_tables
init_advanced_tables()

# ---- Register advanced API routes ----
from advanced_api import register_advanced_routes
register_advanced_routes(app)

# ---- Register leads API routes ----
from leads_api import register_leads_routes
register_leads_routes(app)

# ---- Init auth + middleware ----
from middleware import init_auth, require_auth, rate_limit_middleware
init_auth()

@app.before_request
def before_request_hook():
    rate_limit_middleware()


# ========== API Endpoints ==========

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/manifest.json")
def pwa_manifest():
    return app.send_static_file("manifest.json")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/health/db")
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


@require_auth
@app.route("/api/stats")
def stats():
    """Get funnel statistics."""
    eng = get_engine()
    conn = eng.conn
    return jsonify({
        "overall": get_stats(conn),
        "by_industry": get_industry_stats(conn),
    })


@require_auth
@app.route("/api/leads/next")
def next_leads():
    """Get next batch of leads to call."""
    eng = get_engine()
    industry = request.args.get("industry")
    limit = int(request.args.get("limit", 10))
    leads = eng.run_batch(industry=industry, limit=limit)
    return jsonify({"leads": leads, "count": len(leads)})


@require_auth
@app.route("/api/call/result", methods=["POST"])
def call_result():
    """
    Receive call result from Technomax webhook.
    Expected JSON:
    {
        "lead_id": 123,
        "result": "interested|callback|refused|no_answer|wrong_number|voicemail",
        "notes": "optional notes from the call"
    }
    """
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

    eng = get_engine()
    actions = eng.process_call_result(lead_id, result, notes)
    return jsonify({"actions": actions})


@require_auth
@app.route("/api/leads/<int:lead_id>/status", methods=["PUT"])
def update_status(lead_id):
    """Manually update lead status."""
    data = request.get_json()
    status = data.get("status")
    notes = data.get("notes", "")
    eng = get_engine()
    update_lead_status(eng.conn, lead_id, status, notes)
    return jsonify({"ok": True})


@require_auth
@app.route("/api/whatsapp/send", methods=["POST"])
def send_whatsapp():
    """Send WhatsApp message manually."""
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


@require_auth
@app.route("/api/whatsapp/status")
def whatsapp_status():
    """Get WhatsApp connection state."""
    client = WhatsAppClient()
    state = client.get_connection_state()
    return jsonify({"state": state})


@require_auth
@app.route("/api/whatsapp/qr")
def whatsapp_qr():
    """Get WhatsApp QR code for connecting."""
    client = WhatsAppClient()
    qr = client.get_qr_code()
    state = client.get_connection_state()
    return jsonify({"state": state, "qr": qr})


@require_auth
@app.route("/api/whatsapp/connect", methods=["POST"])
def whatsapp_connect():
    """Create/initialize WhatsApp instance."""
    client = WhatsAppClient()
    try:
        result = client.create_instance()
        return jsonify({"ok": True, "instance": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@require_auth
@app.route("/api/whatsapp/qr-image")
def whatsapp_qr_image():
    """Get QR code as base64 image for embedding in UI."""
    client = WhatsAppClient()
    state = client.get_connection_state()
    if state == "open":
        return jsonify({"state": "open", "qr": None})
    qr = client.get_qr_code()
    return jsonify({"state": state, "qr": qr})


# ---- SMTP Config ----

@require_auth
@app.route("/api/config/smtp", methods=["GET"])
def get_smtp_config():
    """Get SMTP configuration (password masked)."""
    cfg = load_config()
    smtp = cfg.get("smtp", {})
    return jsonify({
        "host": smtp.get("host", "smtp.mail.ru"),
        "port": smtp.get("port", 465),
        "username": smtp.get("username", ""),
        "from_name": smtp.get("from_name", "Technomax"),
        "configured": bool(smtp.get("username") and smtp.get("password")),
    })


@require_auth
@app.route("/api/config/smtp", methods=["POST"])
def set_smtp_config():
    """Save SMTP configuration."""
    data = request.get_json()
    cfg = load_config()
    cfg["smtp"] = {
        "host": data.get("host", "smtp.mail.ru"),
        "port": data.get("port", 465),
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "from_name": data.get("from_name", "Technomax"),
    }
    save_config(cfg)

    # Set env vars for email_sender
    os.environ["SMTP_HOST"] = cfg["smtp"]["host"]
    os.environ["SMTP_PORT"] = str(cfg["smtp"]["port"])
    os.environ["SMTP_USERNAME"] = cfg["smtp"]["username"]
    os.environ["SMTP_PASSWORD"] = cfg["smtp"]["password"]
    os.environ["SMTP_FROM_NAME"] = cfg["smtp"]["from_name"]

    return jsonify({"ok": True})


@require_auth
@app.route("/api/config/smtp/test", methods=["POST"])
def test_smtp():
    """Send a test email."""
    data = request.get_json() or {}
    to_email = data.get("to", "")
    if not to_email:
        return jsonify({"error": "to email required"}), 400

    cfg = load_config()
    smtp_cfg = cfg.get("smtp", {})
    if not smtp_cfg.get("username") or not smtp_cfg.get("password"):
        return jsonify({"error": "SMTP not configured"}), 400

    os.environ["SMTP_HOST"] = smtp_cfg.get("host", "smtp.mail.ru")
    os.environ["SMTP_PORT"] = str(smtp_cfg.get("port", 465))
    os.environ["SMTP_USERNAME"] = smtp_cfg["username"]
    os.environ["SMTP_PASSWORD"] = smtp_cfg["password"]
    os.environ["SMTP_FROM_NAME"] = smtp_cfg.get("from_name", "Technomax")

    sender = EmailSender()
    result = sender.send(
        to_email=to_email,
        subject="Test Email - Sales Funnel",
        body_html="<h2>Test</h2><p>Email configuration is working correctly.</p>",
    )
    return jsonify({"success": result.success, "error": result.error})


@require_auth
@app.route("/api/report")
def report():
    """Get text funnel report."""
    eng = get_engine()
    return jsonify({"report": eng.get_funnel_report()})


# ---- Callbacks from Technomax AI Agent ----

@require_auth
@app.route("/api/agent/send-kp", methods=["POST"])
def agent_send_kp():
    """Called by Technomax agent when client wants КП."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    company_name = data.get("company_name", "")
    industry = data.get("industry", "")

    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    # Get lead info
    conn = init_db()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    lead = dict(lead)
    results = {}

    # Send WhatsApp КП
    if lead.get("whatsapp") or lead.get("mobile"):
        from whatsapp_client import WhatsAppClient
        from telegram_notifier import notify_send
        phone = lead.get("whatsapp") or lead.get("mobile")
        msg = f"""Здравствуйте!

Как и обещали — отправляем коммерческое предложение для {lead['company_name']}.

Мы специализируемся на AI-решениях для автоматизации бизнеса в сфере {lead.get('industry', '')}.

Если возникнут вопросы — ответьте на это сообщение!"""

        # Check with Telegram notifier before sending
        allowed = notify_send("WhatsApp КП", phone, f"{lead['company_name']} / {lead.get('industry','')}")
        if allowed:
            wa = WhatsAppClient()
            r = wa.send_text(phone, msg)
            results["whatsapp"] = "sent" if r.success else f"failed: {r.error}"
            if r.success:
                update_lead_status(conn, lead_id, "sent_wa", "KP sent via WhatsApp by AI agent")
        else:
            results["whatsapp"] = "blocked (auto_send off, Telegram notified)"

    # Send Email КП
    if lead.get("email"):
        from email_sender import EmailSender, build_kp_html
        from telegram_notifier import notify_send
        to = lead["email"]

        # Check with Telegram notifier before sending
        allowed = notify_send("Email КП", to, f"{lead['company_name']} / {lead.get('industry','')}")
        if allowed:
            sender = EmailSender()
            html = build_kp_html(lead["company_name"], lead.get("industry", ""))
            r = sender.send(
                to_email=to,
                subject=f"Коммерческое предложение для {lead['company_name']}",
                body_html=html,
            )
            results["email"] = "sent" if r.success else f"failed: {r.error}"
            if r.success:
                update_lead_status(conn, lead_id, "sent_email", "KP sent via email by AI agent")
        else:
            results["email"] = "blocked (auto_send off, Telegram notified)"

    # Log to agent_sync
    from agent_sync import log_message
    log_message(lead_id, "whatsapp", "outbound", f"КП отправлено для {company_name}", results)

    conn.close()
    return jsonify({"ok": True, "results": results})


@require_auth
@app.route("/api/agent/log-call", methods=["POST"])
def agent_log_call():
    """Called by Technomax agent to log conversation result."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    channel = data.get("channel", "whatsapp")
    result = data.get("result", "unknown")
    notes = data.get("notes", "")

    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    conn = init_db()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        return jsonify({"error": "Lead not found"}), 404

    # Update lead status
    status_map = {
        "interested": "interested",
        "callback": "callback",
        "refused": "refused",
    }
    new_status = status_map.get(result, "called")
    update_lead_status(conn, lead_id, new_status, f"[{channel}] {notes}")

    # Log to agent_sync
    from agent_sync import log_message, sync_after_whatsapp, update_lead_context
    log_message(lead_id, channel, "inbound", notes[:500], {"result": result})
    sync_after_whatsapp(lead_id, notes, is_inbound=True)

    # Update context
    if result == "interested":
        update_lead_context(lead_id, stage="interested", interest_level=8)
    elif result == "refused":
        update_lead_context(lead_id, stage="lost", interest_level=0)
    elif result == "callback":
        update_lead_context(lead_id, stage="negotiating", interest_level=5)

    conn.close()
    return jsonify({"ok": True})


@require_auth
@app.route("/api/agent/create", methods=["POST"])
def create_technomax_agent():
    """Create AI agent on Technomax platform."""
    import httpx as _httpx
    from technomax_agent import BASE_URL, _get_credentials, _headers

    data = request.get_json() or {}
    funnel_url = data.get("funnel_url", "http://YOUR_VPS_IP:5050")

    try:
        with _httpx.Client(timeout=15) as _client:
            # Authenticate
            r = _client.post(
                f"{BASE_URL}/iam/api/v1/auth/login",
                json=_get_credentials(),
                headers={"Origin": BASE_URL, "Referer": f"{BASE_URL}/app", "Content-Type": "application/json"},
            )
            if r.status_code != 200:
                return jsonify({"error": "Auth failed"}), 401
            token = r.json().get("token")

            # Create agent
            from technomax_agent import create_sales_agent
            # Use synchronous version via httpx.Client
            agent_config = {
                "name": "Sales Funnel Agent",
                "config": {
                    "displayName": "Sales Funnel Agent",
                    "llm": {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro"},
                    "tts": {"provider": "edge-tts", "voice": "ru-RU-SvetlanaNeural"},
                },
            }
            r2 = _client.post(
                f"{BASE_URL}/agent/api/v1/agents",
                json=agent_config,
                headers=_headers(token),
            )
            if r2.status_code == 200:
                return jsonify({"ok": True, "agent": r2.json()})
            return jsonify({"error": "Failed to create agent"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@require_auth
@app.route("/api/calls/start", methods=["POST"])
def start_ai_calls():
    """Start AI calls via Pipecat agent (runs in background thread)."""
    import threading
    data = request.get_json() or {}
    industry = data.get("industry")
    limit = data.get("limit", 5)

    eng = get_engine()

    # Run in background to avoid blocking the request thread
    # (start_ai_calls has time.sleep between calls)
    result_holder = {"calls": [], "done": False}

    def _run():
        try:
            result_holder["calls"] = eng.start_ai_calls(industry=industry, limit=limit)
        except Exception as e:
            result_holder["calls"] = [{"error": str(e)}]
        finally:
            result_holder["done"] = True

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": f"Calling up to {limit} leads in background",
                    "check_status_at": "/api/calls/status"})


@require_auth
@app.route("/api/calls/status")
def pipecat_status():
    """Check Pipecat agent status."""
    from pipecat_client import PipecatClient
    client = PipecatClient()
    return jsonify({
        "available": client.health(),
        "results": client.get_results() if client.health() else {},
    })


# ---- Notification Settings ----

@require_auth
@app.route("/api/config/notifications", methods=["GET"])
def get_notifications():
    """Get notification config."""
    cfg = load_config()
    return jsonify({
        "auto_send": cfg.get("auto_send", False),
        "telegram_bot_token": bool(cfg.get("telegram_bot_token", "")),
        "telegram_chat_id": cfg.get("telegram_chat_id", ""),
        "require_approval": cfg.get("require_approval", True),
    })


@require_auth
@app.route("/api/config/notifications", methods=["POST"])
def set_notifications():
    """Update notification config."""
    data = request.get_json() or {}
    cfg = load_config()

    if "auto_send" in data:
        cfg["auto_send"] = bool(data["auto_send"])
    if "telegram_bot_token" in data:
        cfg["telegram_bot_token"] = data["telegram_bot_token"]
    if "telegram_chat_id" in data:
        cfg["telegram_chat_id"] = data["telegram_chat_id"]
    if "require_approval" in data:
        cfg["require_approval"] = bool(data["require_approval"])

    save_config(cfg)
    return jsonify({"ok": True, "auto_send": cfg.get("auto_send", False)})


@require_auth
@app.route("/api/config/notifications/test", methods=["POST"])
def test_notification():
    """Send a test Telegram notification."""
    from telegram_notifier import _send_telegram_sync
    ok = _send_telegram_sync("<b>Test</b> — Sales Funnel Telegram notification works!")
    return jsonify({"success": ok})


@require_auth
@app.route("/api/technomax/dashboard")
def technomax_dashboard():
    """Get Technomax platform data: tasks, bots, agents, call stats."""
    import httpx as _httpx
    from technomax_client import technomax
    try:
        # Synchronous: authenticate then fetch dashboard
        with _httpx.Client(timeout=15) as _client:
            technomax._ensure_token_sync(_client)
            data = technomax.get_dashboard_data_sync(_client)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@require_auth
@app.route("/api/technomax/tasks")
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


@require_auth
@app.route("/api/technomax/tasks/<task_id>")
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


# ========== CLI Mode ==========

def cli():
    """Command-line interface."""
    if len(sys.argv) < 2:
        print("Sales Funnel CLI")
        print("Usage:")
        print("  python main.py serve              - Start API server")
        print("  python main.py import <file.xlsx> - Import leads from Excel")
        print("  python main.py report             - Show funnel report")
        print("  python main.py batch [industry]   - Show next leads to call")
        print("  python main.py wa-status          - Check WhatsApp connection")
        print("  python main.py wa-connect         - Initialize WhatsApp")
        print("  python main.py wa-qr              - Get WhatsApp QR code")
        return

    cmd = sys.argv[1]

    if cmd == "serve":
        port = int(os.environ.get("PORT", "5050"))
        logger.info("Starting Sales Funnel API on port %d", port)
        try:
            import gunicorn.app.base

            class StandaloneApplication(gunicorn.app.base.BaseApplication):
                def __init__(self, app, options=None):
                    self.options = options or {}
                    self.application = app
                    super().__init__()

                def load_config(self):
                    for key, value in self.options.items():
                        if key in self.cfg.settings and value is not None:
                            self.cfg.set(key.lower(), value)

                def load(self):
                    return self.application

            StandaloneApplication(app, {
                "bind": f"0.0.0.0:{port}",
                "workers": int(os.environ.get("GUNICORN_WORKERS", "2")),
                "timeout": 120,
            }).run()
        except ImportError:
            logger.warning("gunicorn not installed — falling back to Flask dev server")
            app.run(host="0.0.0.0", port=port, debug=False)

    elif cmd == "import":
        from leads_db import import_excel
        path = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DEFAULT_EXCEL_PATH", "")
        if not path:
            print("Error: No Excel file specified. Pass as argument or set DEFAULT_EXCEL_PATH env var.")
            return
        count = import_excel(path)
        print(f"Imported {count} leads")

    elif cmd == "report":
        eng = get_engine()
        print(eng.get_funnel_report())

    elif cmd == "batch":
        eng = get_engine()
        industry = sys.argv[2] if len(sys.argv) > 2 else None
        leads = eng.run_batch(industry=industry)
        print(f"Leads to call ({len(leads)}):")
        for l in leads:
            print(f"  [{l['id']}] {l['company_name']}")
            print(f"        {l['mobile']} | {l['email']} | {l['industry']}")

    elif cmd == "wa-status":
        client = WhatsAppClient()
        state = client.get_connection_state()
        print(f"WhatsApp state: {state}")

    elif cmd == "wa-connect":
        client = WhatsAppClient()
        result = client.create_instance()
        print(f"Instance created: {json.dumps(result, indent=2, ensure_ascii=False)}")

    elif cmd == "wa-qr":
        client = WhatsAppClient()
        qr = client.get_qr_code()
        state = client.get_connection_state()
        print(f"State: {state}")
        if qr:
            print(f"QR (base64): {qr[:100]}...")
        else:
            print("No QR code available. Try wa-connect first.")

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    cli()

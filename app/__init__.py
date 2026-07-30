"""
Sales Funnel — Flask application factory.
"""
import os
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask


LOG_FILE = Path(__file__).parent.parent / "sales_funnel.log"
CONFIG_FILE = Path(__file__).parent.parent / "config.json"


def load_config() -> dict:
    """Load config from file."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(cfg: dict):
    """Save config to file."""
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def create_app(config_override: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    # Logging
    file_handler = RotatingFileHandler(
        str(LOG_FILE), maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    ))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )

    # Flask app — templates live in project_root/templates
    project_root = Path(__file__).parent.parent
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get(
        "FLASK_SECRET_KEY", os.urandom(32).hex()
    )
    if config_override:
        app.config.update(config_override)

    # CSRF — disable for /api/ routes (JSON API uses Bearer token auth)
    try:
        from flask_wtf.csrf import CSRFProtect
        csrf = CSRFProtect(app)
        # Store csrf ref so blueprints can be exempted after registration
        app.extensions["csrf_protect"] = csrf
    except ImportError:
        logging.getLogger("app").warning(
            "flask-wtf not installed — CSRF protection disabled"
        )

    # Store config helpers on app for access in routes
    app.config["load_config"] = load_config
    app.config["save_config"] = save_config

    # Init DB tables (side effect on import — same as before)
    from db_extended import init_extended_tables
    from funnel_features import seed_default_templates
    from advanced_features import init_advanced_tables
    from agent_sync import init_sync_tables

    init_extended_tables()
    seed_default_templates()
    init_advanced_tables()

    # Auth + middleware
    from middleware import init_auth, rate_limit_middleware
    init_auth()

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        pass  # Connection pool handles cleanup

    @app.before_request
    def before_request_hook():
        rate_limit_middleware()

    # Register blueprints
    from app.api.core import core_bp
    from app.api.leads import leads_bp
    from app.api.whatsapp import whatsapp_bp
    from app.api.agent import agent_bp
    from app.api.config_routes import config_bp
    from app.api.technomax import technomax_bp
    from app.api.features import features_bp
    from app.api.advanced import advanced_bp
    from app.api.agent_tools import agent_tools_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(whatsapp_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(technomax_bp)
    app.register_blueprint(features_bp)
    app.register_blueprint(advanced_bp)
    app.register_blueprint(agent_tools_bp, url_prefix="/api/agent")

    # Exempt all API blueprints from CSRF (JSON API with Bearer auth)
    csrf = app.extensions.get("csrf_protect")
    if csrf:
        for bp in [core_bp, leads_bp, whatsapp_bp, agent_bp, agent_tools_bp,
                    config_bp, technomax_bp, features_bp, advanced_bp]:
            csrf.exempt(bp)

    # Auto-configure Evolution API webhook on startup
    _setup_evolution_webhook(app)

    return app


def _setup_evolution_webhook(app: Flask):
    """Configure Evolution API to send incoming WhatsApp messages to this app."""
    import threading

    def _configure():
        import time
        time.sleep(5)  # Wait for Evolution API to be ready
        try:
            import requests as req

            evo_url = os.environ.get("EVO_API_URL", "http://evolution_api:8080").rstrip("/")
            evo_key = os.environ.get("EVO_API_KEY", "")
            instance_name = os.environ.get("EVO_INSTANCE", "sales_funnel")
            # Webhook URL = this Flask app (from Evolution API's perspective = flask-app:5050)
            webhook_url = os.environ.get("WA_WEBHOOK_URL", "http://flask-app:5050/api/wa/webhook")
            webhook_secret = os.environ.get("WA_WEBHOOK_SECRET", "")

            if not evo_key:
                app.logger.warning("EVO_API_KEY not set — skipping webhook config")
                return

            headers = {"apikey": evo_key, "Content-Type": "application/json"}

            # Check if instance exists, create if not
            try:
                r = req.get(f"{evo_url}/instance/fetchInstances", headers=headers, timeout=10)
                instances = r.json() if r.status_code == 200 else []
                instance_exists = any(
                    (i.get("instance", {}).get("instanceName") or i.get("instanceName")) == instance_name
                    for i in (instances if isinstance(instances, list) else [])
                )
            except Exception:
                instance_exists = False

            if not instance_exists:
                app.logger.info("Creating Evolution API instance: %s", instance_name)
                req.post(f"{evo_url}/instance/create", headers=headers, json={
                    "instanceName": instance_name,
                    "integration": "WHATSAPP-BAILEYS",
                    "qrcode": True,
                }, timeout=15)

            # Configure webhook
            webhook_events = [
                "MESSAGES_UPSERT",
                "CONNECTION_UPDATE",
            ]
            payload = {
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "events": webhook_events,
                }
            }
            if webhook_secret:
                payload["webhook"]["headers"] = {"X-Webhook-Secret": webhook_secret}

            r = req.post(
                f"{evo_url}/webhook/set/{instance_name}",
                headers=headers, json=payload, timeout=10,
            )
            if r.status_code in (200, 201):
                app.logger.info("Evolution API webhook configured: %s", webhook_url)
            else:
                app.logger.warning("Webhook config failed (status %d): %s", r.status_code, r.text[:200])

        except Exception as e:
            app.logger.error("Evolution API webhook setup failed: %s", e)

    t = threading.Thread(target=_configure, daemon=True)
    t.start()

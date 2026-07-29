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

    return app

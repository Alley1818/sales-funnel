"""
Auth middleware, connection pool, input validation, error handling.
Central module that wraps all API routes.
"""
import os
import re
import secrets
import sqlite3
import logging
import functools
from datetime import datetime, timedelta
from pathlib import Path
from flask import request, jsonify, g

logger = logging.getLogger("middleware")

DB_PATH = Path(__file__).parent / "leads.db"

# ==================== CONNECTION POOL ====================

_pool_conn = None


def get_pooled_conn() -> sqlite3.Connection:
    """Single connection reused across requests."""
    global _pool_conn
    if _pool_conn is None:
        _pool_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _pool_conn.row_factory = sqlite3.Row
        _pool_conn.execute("PRAGMA foreign_keys=ON")
    return _pool_conn


def close_pool():
    global _pool_conn
    if _pool_conn:
        _pool_conn.close()
        _pool_conn = None


# ==================== AUTH ====================

# Password storage (hashed)
_AUTH_FILE = Path(__file__).parent / ".auth.json"


def _load_auth() -> dict:
    if _AUTH_FILE.exists():
        import json
        return json.loads(_AUTH_FILE.read_text())
    return {}


def _save_auth(data: dict):
    import json
    _AUTH_FILE.write_text(json.dumps(data, indent=2))


def init_auth():
    """Initialize auth with default admin if not exists."""
    auth = _load_auth()
    if "users" not in auth:
        import hashlib
        salt = secrets.token_hex(16)
        password = os.getenv("ADMIN_PASSWORD", "admin123")
        pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
        auth["users"] = {
            "admin": {
                "salt": salt,
                "hash": pw_hash,
                "role": "admin",
            }
        }
        _save_auth(auth)
        logger.info("Auth initialized with default admin")


def verify_password(username: str, password: str) -> bool:
    import hashlib
    auth = _load_auth()
    user = auth.get("users", {}).get(username)
    if not user:
        return False
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), user["salt"].encode(), 100000).hex()
    return secrets.compare_digest(pw_hash, user["hash"])


def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    conn = get_pooled_conn()
    expires = datetime.now() + timedelta(hours=24)
    conn.execute(
        "INSERT OR REPLACE INTO auth_sessions (token, user_name, expires_at) VALUES (?,?,?)",
        (token, username, expires.isoformat())
    )
    conn.commit()
    return token


def check_session(token: str) -> str | None:
    """Returns username if valid session, None otherwise."""
    if not token:
        return None
    conn = get_pooled_conn()
    row = conn.execute(
        "SELECT user_name FROM auth_sessions WHERE token = ? AND expires_at > datetime('now')",
        (token,)
    ).fetchone()
    return row["user_name"] if row else None


def require_auth(f):
    """Decorator: require valid auth token on all /api/ routes."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Allow login and health without auth
        if request.path in ("/api/auth/login", "/health"):
            return f(*args, **kwargs)

        # Check cookie or header
        token = request.cookies.get("sf_token") or request.headers.get("Authorization", "").replace("Bearer ", "")
        username = check_session(token)
        if not username:
            return jsonify({"error": "Unauthorized"}), 401
        g.username = username
        return f(*args, **kwargs)
    return decorated


# ==================== AGENT API KEY AUTH ====================

def require_agent_key(f):
    """Decorator: require X-Agent-API-Key header for Technomax agent callbacks."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        expected = os.getenv("AGENT_API_KEY", "")
        if not expected:
            logger.warning("AGENT_API_KEY not set — allowing agent request")
            return f(*args, **kwargs)
        provided = request.headers.get("X-Agent-API-Key", "")
        if not secrets.compare_digest(provided, expected):
            return jsonify({"error": "Invalid agent API key"}), 403
        return f(*args, **kwargs)
    return decorated


# ==================== INPUT VALIDATION ====================

VALID_STATUSES = {"new", "called", "interested", "callback", "sent_wa", "sent_email", "refused", "done", "lost"}
VALID_CHANNELS = {"voice", "whatsapp", "email", "sms"}
VALID_SENTIMENTS = {"positive", "neutral", "negative", "angry"}


def validate_status(status: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Valid: {VALID_STATUSES}")
    return status


def validate_channel(channel: str) -> str:
    if channel not in VALID_CHANNELS:
        raise ValueError(f"Invalid channel: {channel}. Valid: {VALID_CHANNELS}")
    return channel


def sanitize_string(value: str, max_len: int = 1000) -> str:
    """Strip and truncate string input."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def validate_phone(phone: str) -> str:
    """Validate and normalize phone number."""
    cleaned = re.sub(r"[^\d+]", "", phone)
    if len(cleaned) < 7 or len(cleaned) > 15:
        raise ValueError(f"Invalid phone: {phone}")
    return cleaned


def validate_email(email: str) -> str:
    """Basic email validation."""
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        raise ValueError(f"Invalid email: {email}")
    return email.lower()


# ==================== ACTION LOGGING ====================

def log_api_action(action: str, entity_type: str = "", entity_id: int = 0, details: str = ""):
    """Log an API action to the action_log table."""
    conn = get_pooled_conn()
    conn.execute(
        "INSERT INTO action_log (action, entity_type, entity_id, details, ip_address) VALUES (?,?,?,?,?)",
        (action, entity_type, entity_id, details[:1000], request.remote_addr or "")
    )
    conn.commit()


# ==================== RATE LIMITING ====================

RATE_LIMIT = 120  # per minute
_rate_store = {}  # In-memory rate limiting (no SQLite, avoids lock with gunicorn workers)

def check_rate(ip: str) -> bool:
    import time
    now = time.time()
    timestamps = _rate_store.get(ip, [])
    # Keep only last minute
    timestamps = [t for t in timestamps if now - t < 60]
    _rate_store[ip] = timestamps
    return len(timestamps) < RATE_LIMIT

def record_rate(ip: str, endpoint: str):
    import time
    now = time.time()
    if ip not in _rate_store:
        _rate_store[ip] = []
    _rate_store[ip].append(now)
    # Cleanup old entries periodically
    if len(_rate_store) > 1000:
        for k in list(_rate_store.keys()):
            _rate_store[k] = [t for t in _rate_store[k] if now - t < 60]
            if not _rate_store[k]:
                del _rate_store[k]


def rate_limit_middleware():
    """Before-request hook for rate limiting."""
    if request.path.startswith("/api/"):
        ip = request.remote_addr or "unknown"
        if not check_rate(ip):
            return jsonify({"error": "Rate limit exceeded"}), 429
        record_rate(ip, request.path)


# ==================== ERROR HANDLING ====================

def safe_send(send_fn, *args, **kwargs):
    """Wrap send functions with try/except."""
    try:
        return send_fn(*args, **kwargs)
    except Exception as e:
        logger.error("Send failed: %s", e)
        return type("Result", (), {"success": False, "error": str(e)})()

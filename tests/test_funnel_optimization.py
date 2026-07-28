"""Tests for funnel engine optimization: DNC, CPS, scoring, callbacks."""
import sys
import os
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("ADMIN_PASSWORD", "test123")


def _make_conn(tmp_path):
    """Create a fresh SQLite DB with all required tables."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT DEFAULT '',
            mobile TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            whatsapp TEXT DEFAULT '',
            email TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            city TEXT DEFAULT '',
            website TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            rating INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE lead_scores (
            lead_id INTEGER PRIMARY KEY,
            score INTEGER DEFAULT 0,
            category TEXT DEFAULT 'cold',
            reasoning TEXT DEFAULT '',
            scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE do_not_call (
            phone TEXT PRIMARY KEY,
            reason TEXT DEFAULT '',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE rate_limit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT,
            phone TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            call_type TEXT,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE message_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            channel TEXT,
            message_type TEXT,
            content TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            channel TEXT NOT NULL,
            direction TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE lead_context (
            lead_id INTEGER PRIMARY KEY,
            stage TEXT DEFAULT 'new',
            interest_level INTEGER DEFAULT 0,
            objections TEXT DEFAULT '[]',
            needs TEXT DEFAULT '[]',
            next_action TEXT DEFAULT '',
            last_channel TEXT DEFAULT '',
            last_contact_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE callbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            scheduled_at TIMESTAMP NOT NULL,
            channel TEXT DEFAULT 'voice',
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE wa_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            message TEXT,
            replied INTEGER DEFAULT 0,
            reply_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    return conn


@pytest.fixture
def db_conn(tmp_path):
    conn = _make_conn(tmp_path)
    yield conn
    conn.close()


@pytest.fixture
def engine(db_conn):
    """Create FunnelEngine with mocked external services.
    Patch get_conn in all modules that use it internally.
    funnel_engine itself uses self.conn (injected), not get_conn.
    """
    patches = [
        patch("funnel_features.get_conn", return_value=db_conn),
        patch("advanced_features.get_conn", return_value=db_conn),
        patch("agent_sync.get_conn", return_value=db_conn),
    ]
    for p in patches:
        p.start()

    from funnel_engine import FunnelEngine, FunnelConfig
    config = FunnelConfig(
        send_whatsapp_after_call=True,
        send_email_after_call=True,
    )
    eng = FunnelEngine(db_conn, config=config)
    eng.whatsapp = MagicMock()
    eng.whatsapp.send_text.return_value = MagicMock(success=True, error=None)
    eng.email = MagicMock()
    eng.email.send.return_value = MagicMock(success=True, error=None)

    yield eng

    for p in patches:
        p.stop()


# ---- DNC Tests ----

def test_dnc_blocks_whatsapp(engine, db_conn):
    """DNC-listed phone should block all outbound sends."""
    db_conn.execute("INSERT INTO leads (company_name, whatsapp, status) VALUES ('TestCo', '77001112233', 'new')")
    db_conn.execute("INSERT INTO do_not_call (phone, reason) VALUES ('77001112233', 'requested removal')")
    db_conn.commit()

    result = engine.process_call_result(1, "interested", "wants info")
    assert result.get("blocked") == "dnc"
    assert "whatsapp" not in result
    assert "email" not in result


def test_dnc_blocks_run_batch(engine, db_conn):
    """DNC-listed leads should be filtered out of run_batch."""
    db_conn.execute("INSERT INTO leads (company_name, whatsapp, status) VALUES ('DNC Co', '77009998877', 'new')")
    db_conn.execute("INSERT INTO leads (company_name, whatsapp, status) VALUES ('Good Co', '77005554433', 'new')")
    db_conn.execute("INSERT INTO do_not_call (phone) VALUES ('77009998877')")
    db_conn.commit()

    leads = engine.run_batch(limit=10)
    assert len(leads) == 1
    assert leads[0]["company_name"] == "Good Co"


# ---- CPS Rate Limiting Tests ----

def test_cps_blocks_when_rate_limited(engine, db_conn):
    """When CPS limit is reached, sends should be rate_limited."""
    db_conn.execute("INSERT INTO leads (company_name, whatsapp, email, status) VALUES ('TestCo', '77001112233', 'test@test.com', 'new')")
    db_conn.commit()

    with patch("funnel_engine.can_send", return_value=False):
        result = engine.process_call_result(1, "interested", "wants info")
        assert result.get("whatsapp") == "rate_limited"
        assert result.get("email") == "rate_limited"


# ---- Lead Scoring Integration Tests ----

def test_auto_score_after_interested(engine, db_conn):
    """Lead should be auto-scored after an 'interested' call result."""
    db_conn.execute(
        "INSERT INTO leads (company_name, mobile, whatsapp, industry, city, status) "
        "VALUES ('HotCo', '77001112233', '77001112233', 'Ломбарды', 'Алматы', 'new')"
    )
    db_conn.commit()

    engine.process_call_result(1, "interested", "very interested")

    score_row = db_conn.execute("SELECT * FROM lead_scores WHERE lead_id = 1").fetchone()
    assert score_row is not None
    score = dict(score_row)
    # base score (phone+15, WA+10, industry+25, contacts+10, city+5 = 65) + interested bonus (+20) = 85
    assert score["score"] >= 70
    assert score["category"] == "hot"
    assert "заинтересован" in score["reasoning"]


def test_run_batch_ordered_by_score(engine, db_conn):
    """run_batch should return leads ordered by score, highest first."""
    db_conn.execute("INSERT INTO leads (company_name, status) VALUES ('ColdCo', 'new')")
    db_conn.execute("INSERT INTO leads (company_name, status) VALUES ('HotCo', 'new')")
    db_conn.commit()

    db_conn.execute("INSERT INTO lead_scores (lead_id, score, category) VALUES (1, 20, 'cold')")
    db_conn.execute("INSERT INTO lead_scores (lead_id, score, category) VALUES (2, 90, 'hot')")
    db_conn.commit()

    leads = engine.run_batch(limit=10)
    assert len(leads) == 2
    assert leads[0]["company_name"] == "HotCo"
    assert leads[1]["company_name"] == "ColdCo"


# ---- Callback Processing Tests ----

def test_process_callbacks(engine, db_conn):
    """Due callbacks should be processed and leads reset to 'new'."""
    db_conn.execute("INSERT INTO leads (company_name, status) VALUES ('CallbackCo', 'callback')")
    db_conn.execute(
        "INSERT INTO callbacks (lead_id, scheduled_at, status) VALUES (1, datetime('now', '-1 hour'), 'pending')"
    )
    db_conn.commit()

    results = engine.process_callbacks()

    assert len(results) == 1
    assert results[0]["company"] == "CallbackCo"

    # Lead should be reset to 'new'
    lead = db_conn.execute("SELECT status FROM leads WHERE id = 1").fetchone()
    assert lead["status"] == "new"

    # Callback should be marked completed
    cb = db_conn.execute("SELECT status FROM callbacks WHERE id = 1").fetchone()
    assert cb["status"] == "completed"


def test_process_callbacks_empty(engine, db_conn):
    """No due callbacks should return empty list."""
    results = engine.process_callbacks()
    assert results == []


# ---- Cross-Channel Sync Tests ----

def test_sync_after_call_logs_history(engine, db_conn):
    """After a call, conversation history should be logged."""
    db_conn.execute("INSERT INTO leads (company_name, status) VALUES ('SyncCo', 'new')")
    db_conn.commit()

    engine.process_call_result(1, "interested", "wants to know more")

    history = db_conn.execute("SELECT * FROM conversations WHERE lead_id = 1").fetchall()
    assert len(history) >= 1
    assert history[0]["channel"] == "voice"


# ---- Full Pipeline Test ----

def test_full_pipeline_interested_to_wa(engine, db_conn):
    """Full flow: new lead -> call -> interested -> WhatsApp KP sent -> scored."""
    db_conn.execute(
        "INSERT INTO leads (company_name, mobile, whatsapp, industry, city, status) "
        "VALUES ('FullCo', '77001112233', '77001112233', 'Страхование', 'Алматы', 'new')"
    )
    db_conn.commit()

    result = engine.process_call_result(1, "interested", "wants demo")

    assert result["status"] == "interested"
    assert result.get("whatsapp") == "sent"
    assert result.get("blocked") is None

    # Check lead scored
    score = db_conn.execute("SELECT * FROM lead_scores WHERE lead_id = 1").fetchone()
    assert score is not None
    assert score["category"] in ("hot", "warm")

    # Check conversation logged
    conv = db_conn.execute("SELECT * FROM conversations WHERE lead_id = 1").fetchall()
    assert len(conv) >= 1


def test_refused_lead_no_outbound(engine, db_conn):
    """Refused lead should not trigger any sends."""
    db_conn.execute("INSERT INTO leads (company_name, whatsapp, status) VALUES ('RefusedCo', '77001112233', 'new')")
    db_conn.commit()

    result = engine.process_call_result(1, "refused", "not interested")
    assert result["status"] == "refused"
    assert "whatsapp" not in result
    assert "email" not in result

"""Tests for follow-up sequences in funnel engine."""
import sys
import os
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("ADMIN_PASSWORD", "test123")


def _make_conn(tmp_path):
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
    patches = [
        patch("funnel_features.get_conn", return_value=db_conn),
        patch("advanced_features.get_conn", return_value=db_conn),
        patch("agent_sync.get_conn", return_value=db_conn),
    ]
    for p in patches:
        p.start()

    from funnel_engine import FunnelEngine, FunnelConfig
    config = FunnelConfig(send_whatsapp_after_call=True, send_email_after_call=True)
    eng = FunnelEngine(db_conn, config=config)
    eng.whatsapp = MagicMock()
    eng.whatsapp.send_text.return_value = MagicMock(success=True, error=None)
    eng.email = MagicMock()
    eng.email.send.return_value = MagicMock(success=True, error=None)

    yield eng
    for p in patches:
        p.stop()


def test_followup_scheduled_after_kp(engine, db_conn):
    """Follow-up should be auto-scheduled when KP is sent."""
    db_conn.execute(
        "INSERT INTO leads (company_name, whatsapp, status) VALUES ('TestCo', '77001112233', 'new')"
    )
    db_conn.commit()

    engine.process_call_result(1, "interested", "wants info")

    # Check follow-up was scheduled
    fu = db_conn.execute("SELECT * FROM followup_sequences WHERE lead_id = 1").fetchone()
    assert fu is not None
    assert fu["status"] == "pending"
    assert fu["attempt"] == 1
    assert fu["channel"] == "whatsapp"


def test_process_followup_sends_message(engine, db_conn):
    """Due follow-up should send a WhatsApp message."""
    db_conn.execute(
        "INSERT INTO leads (company_name, whatsapp, status) VALUES ('TestCo', '77001112233', 'new')"
    )
    # Schedule a follow-up that's already due
    engine._init_followup_table()
    db_conn.execute(
        "INSERT INTO followup_sequences (lead_id, channel, attempt, next_followup_at) "
        "VALUES (1, 'whatsapp', 1, datetime('now', '-1 hour'))"
    )
    db_conn.commit()

    results = engine.process_followups()

    assert len(results) == 1
    assert results[0]["action"] == "followup_sent"
    assert results[0]["attempt"] == 1
    engine.whatsapp.send_text.assert_called_once()


def test_followup_skips_progressed_lead(engine, db_conn):
    """Follow-up should skip if lead already progressed."""
    db_conn.execute(
        "INSERT INTO leads (company_name, whatsapp, status) VALUES ('TestCo', '77001112233', 'interested')"
    )
    engine._init_followup_table()
    db_conn.execute(
        "INSERT INTO followup_sequences (lead_id, channel, attempt, next_followup_at) "
        "VALUES (1, 'whatsapp', 1, datetime('now', '-1 hour'))"
    )
    db_conn.commit()

    results = engine.process_followups()

    assert len(results) == 1
    assert results[0]["action"] == "skipped"
    engine.whatsapp.send_text.assert_not_called()


def test_followup_respects_dnc(engine, db_conn):
    """Follow-up should not send to DNC-listed numbers."""
    db_conn.execute(
        "INSERT INTO leads (company_name, whatsapp, status) VALUES ('TestCo', '77001112233', 'new')"
    )
    db_conn.execute("INSERT INTO do_not_call (phone) VALUES ('77001112233')")
    engine._init_followup_table()
    db_conn.execute(
        "INSERT INTO followup_sequences (lead_id, channel, attempt, next_followup_at) "
        "VALUES (1, 'whatsapp', 1, datetime('now', '-1 hour'))"
    )
    db_conn.commit()

    results = engine.process_followups()

    assert len(results) == 1
    assert results[0]["action"] == "skipped"
    engine.whatsapp.send_text.assert_not_called()


def test_followup_max_attempts(engine, db_conn):
    """After 3 attempts, follow-up should stop."""
    db_conn.execute(
        "INSERT INTO leads (company_name, whatsapp, status) VALUES ('TestCo', '77001112233', 'new')"
    )
    engine._init_followup_table()
    # 3rd (final) attempt
    db_conn.execute(
        "INSERT INTO followup_sequences (lead_id, channel, attempt, max_attempts, next_followup_at) "
        "VALUES (1, 'whatsapp', 3, 3, datetime('now', '-1 hour'))"
    )
    db_conn.commit()

    results = engine.process_followups()

    assert len(results) >= 1
    # Should have max_followups_reached
    max_reached = [r for r in results if r["action"] == "max_followups_reached"]
    assert len(max_reached) == 1

    # No new followup should be scheduled
    pending = db_conn.execute(
        "SELECT COUNT(*) as cnt FROM followup_sequences WHERE lead_id = 1 AND status = 'pending'"
    ).fetchone()["cnt"]
    assert pending == 0


def test_followup_schedules_next_attempt(engine, db_conn):
    """After first follow-up, second should be scheduled 48h later."""
    db_conn.execute(
        "INSERT INTO leads (company_name, whatsapp, status) VALUES ('TestCo', '77001112233', 'new')"
    )
    engine._init_followup_table()
    db_conn.execute(
        "INSERT INTO followup_sequences (lead_id, channel, attempt, next_followup_at) "
        "VALUES (1, 'whatsapp', 1, datetime('now', '-1 hour'))"
    )
    db_conn.commit()

    engine.process_followups()

    # Should have 2 records: 1st (sent) + 2nd (pending, +48h)
    all_fu = db_conn.execute("SELECT * FROM followup_sequences WHERE lead_id = 1").fetchall()
    assert len(all_fu) == 2
    pending = [f for f in all_fu if f["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["attempt"] == 2


def test_no_followup_without_kp(engine, db_conn):
    """No follow-up should be scheduled if KP wasn't sent."""
    db_conn.execute(
        "INSERT INTO leads (company_name, status) VALUES ('TestCo', 'new')"
    )
    db_conn.commit()

    engine.process_call_result(1, "refused", "not interested")

    engine._init_followup_table()
    fu = db_conn.execute("SELECT * FROM followup_sequences WHERE lead_id = 1").fetchall()
    assert len(fu) == 0

"""Tests for conversation memory: entity extraction, summarization, memory snapshot."""
import sys
import os
import sqlite3
import json
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("ADMIN_PASSWORD", "test123")


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT DEFAULT '',
            mobile TEXT DEFAULT '',
            whatsapp TEXT DEFAULT '',
            email TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            status TEXT DEFAULT 'new'
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
    yield conn
    conn.close()


@pytest.fixture
def patched(db_conn):
    with patch("agent_sync.get_conn", return_value=db_conn):
        yield db_conn


# ---- Entity Extraction Tests ----

def test_extract_budget(patched, db_conn):
    """Should extract budget from conversation text."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.commit()

    from agent_sync import extract_entities
    extract_entities(1, "Наш бюджет 500000 тенге в месяц")

    ctx = db_conn.execute("SELECT needs FROM lead_context WHERE lead_id = 1").fetchone()
    needs = json.loads(ctx["needs"])
    assert any("budget" in n for n in needs)
    assert any("500000" in n for n in needs)


def test_extract_decision_maker(patched, db_conn):
    """Should extract decision maker name."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.commit()

    from agent_sync import extract_entities
    extract_entities(1, "Решает директор Иван")

    ctx = db_conn.execute("SELECT needs FROM lead_context WHERE lead_id = 1").fetchone()
    needs = json.loads(ctx["needs"])
    assert any("decision_maker" in n for n in needs)


def test_extract_timeline(patched, db_conn):
    """Should extract timeline information."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.commit()

    from agent_sync import extract_entities
    extract_entities(1, "Срок: до конца месяца")

    ctx = db_conn.execute("SELECT needs FROM lead_context WHERE lead_id = 1").fetchone()
    needs = json.loads(ctx["needs"])
    assert any("timeline" in n for n in needs)


def test_extract_no_match(patched, db_conn):
    """Should not add anything when no entities match."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.commit()

    from agent_sync import extract_entities
    extract_entities(1, "Здравствуйте, как дела?")

    ctx = db_conn.execute("SELECT needs FROM lead_context WHERE lead_id = 1").fetchone()
    needs = json.loads(ctx["needs"])
    assert len(needs) == 0


def test_extract_merges_with_existing(patched, db_conn):
    """Should merge new entities with existing ones."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute(
        "INSERT INTO lead_context (lead_id, needs) VALUES (1, ?)",
        (json.dumps(["budget: 100000 тенге"]),)
    )
    db_conn.commit()

    from agent_sync import extract_entities
    extract_entities(1, "Решает директор Айбек")

    ctx = db_conn.execute("SELECT needs FROM lead_context WHERE lead_id = 1").fetchone()
    needs = json.loads(ctx["needs"])
    assert len(needs) == 2
    assert any("budget" in n for n in needs)
    assert any("decision_maker" in n for n in needs)


# ---- Summarization Tests ----

def test_summarize_creates_summary_message(patched, db_conn):
    """Summarization should create a summary conversation entry."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    for i in range(10):
        direction = "inbound" if i % 2 == 0 else "outbound"
        db_conn.execute(
            "INSERT INTO conversations (lead_id, channel, direction, content) "
            "VALUES (1, 'voice', ?, ?)",
            (direction, f"Message {i} about business")
        )
    db_conn.commit()

    from agent_sync import summarize_conversation
    summarize_conversation(1)

    summary = db_conn.execute(
        "SELECT * FROM conversations WHERE lead_id = 1 AND channel = 'summary'"
    ).fetchone()
    assert summary is not None
    assert "Клиент" in summary["content"] or "Агент" in summary["content"]
    assert "10" in summary["content"]  # message count


def test_summarize_skips_few_messages(patched, db_conn):
    """Should not summarize if fewer than 5 messages."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    for i in range(3):
        db_conn.execute(
            "INSERT INTO conversations (lead_id, channel, direction, content) "
            "VALUES (1, 'voice', 'inbound', ?)",
            (f"Message {i}",)
        )
    db_conn.commit()

    from agent_sync import summarize_conversation
    summarize_conversation(1)

    summary = db_conn.execute(
        "SELECT * FROM conversations WHERE lead_id = 1 AND channel = 'summary'"
    ).fetchone()
    assert summary is None


# ---- Memory Snapshot Tests ----

def test_memory_snapshot_with_entities(patched, db_conn):
    """Memory snapshot should include extracted entities."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute(
        "INSERT INTO lead_context (lead_id, needs) VALUES (1, ?)",
        (json.dumps(["budget: 500000 тенге", "timeline: до конца месяца"]),)
    )
    db_conn.commit()

    from agent_sync import get_memory_snapshot
    snapshot = get_memory_snapshot(1)

    assert "500000" in snapshot
    assert "timeline" in snapshot
    assert "ИЗВЛЕЧЁННЫЕ ДАННЫЕ" in snapshot


def test_memory_snapshot_with_summary(patched, db_conn):
    """Memory snapshot should include conversation summary."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.execute(
        "INSERT INTO conversations (lead_id, channel, direction, content, metadata) "
        "VALUES (1, 'summary', 'system', 'Краткая история переговоров', ?)",
        (json.dumps({"type": "auto_summary"}),)
    )
    db_conn.commit()

    from agent_sync import get_memory_snapshot
    snapshot = get_memory_snapshot(1)

    assert "КРАТКАЯ ИСТОРИЯ" in snapshot
    assert "Краткая история переговоров" in snapshot


def test_memory_snapshot_with_recent_messages(patched, db_conn):
    """Memory snapshot should include last 5 messages."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    for i in range(8):
        db_conn.execute(
            "INSERT INTO conversations (lead_id, channel, direction, content, created_at) "
            "VALUES (1, 'voice', 'inbound', ?, datetime('now', ?))",
            (f"Message {i}", f"-{8 - i} minutes")
        )
    db_conn.commit()

    from agent_sync import get_memory_snapshot
    snapshot = get_memory_snapshot(1)

    assert "ПОСЛЕДНИЕ СООБЩЕНИЯ" in snapshot
    # Should have last 5 messages
    assert "Message 7" in snapshot
    assert "Message 6" in snapshot


def test_memory_snapshot_empty_lead(patched, db_conn):
    """Memory snapshot should work for a lead with no history."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.commit()

    from agent_sync import get_memory_snapshot
    snapshot = get_memory_snapshot(1)

    # Should return empty or minimal text without crashing
    assert isinstance(snapshot, str)

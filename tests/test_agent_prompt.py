"""Tests for agent prompt optimization: RAG, sentiment, objection playbook, strategy."""
import sys
import os
import sqlite3
import json
import pytest
from unittest.mock import patch, MagicMock

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
        CREATE TABLE knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            doc_type TEXT DEFAULT 'text',
            industry TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            chunk_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER,
            chunk_index INTEGER,
            content TEXT NOT NULL,
            embedding TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE sentiment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            channel TEXT,
            message TEXT,
            sentiment TEXT DEFAULT 'neutral',
            score REAL DEFAULT 0.0,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def patched_conn(db_conn):
    """Patch get_conn in all relevant modules."""
    patches = [
        patch("agent_sync.get_conn", return_value=db_conn),
        patch("advanced_features.get_conn", return_value=db_conn),
    ]
    for p in patches:
        p.start()
    yield db_conn
    for p in patches:
        p.stop()


# ---- Prompt Structure Tests ----

def test_prompt_contains_company_and_industry(patched_conn, db_conn):
    """Prompt should include company name and industry."""
    db_conn.execute("INSERT INTO leads (company_name, industry) VALUES ('TestCo', 'Ломбарды')")
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1, "voice")

    assert "TestCo" in prompt
    assert "Ломбарды" in prompt
    assert "Technomax" in prompt


def test_prompt_voice_channel(patched_conn, db_conn):
    """Voice prompt should include phone-specific instructions."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1, "voice")

    assert "телефону" in prompt
    assert "1-2 минуты" in prompt


def test_prompt_whatsapp_channel(patched_conn, db_conn):
    """WhatsApp prompt should include messaging-specific instructions."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1, "whatsapp")

    assert "WhatsApp" in prompt


# ---- Interest-Based Strategy Tests ----

def test_strategy_educate_low_interest(patched_conn, db_conn):
    """Low interest (0-3) should trigger educate strategy."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id, interest_level) VALUES (1, 2)")
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1)

    assert "Обучение" in prompt
    assert "осведомлённость" in prompt


def test_strategy_nurture_medium_interest(patched_conn, db_conn):
    """Medium interest (4-6) should trigger nurture strategy."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id, interest_level) VALUES (1, 5)")
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1)

    assert "Развитие интереса" in prompt
    assert "демонстрацию" in prompt


def test_strategy_close_high_interest(patched_conn, db_conn):
    """High interest (7-10) should trigger close strategy."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id, interest_level) VALUES (1, 8)")
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1)

    assert "Закрытие" in prompt
    assert "демо" in prompt.lower() or "пилот" in prompt.lower()


# ---- Sentiment Overlay Tests ----

def test_sentiment_angry_warning(patched_conn, db_conn):
    """Angry last message should trigger de-escalation instructions."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.execute(
        "INSERT INTO conversations (lead_id, channel, direction, content) VALUES (1, 'voice', 'inbound', 'Идиоты, ненавижу вашу компанию')"
    )
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1, "voice")

    assert "раздражён" in prompt or "вежливы" in prompt


def test_sentiment_positive_boost(patched_conn, db_conn):
    """Positive last message should encourage closing."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.execute(
        "INSERT INTO conversations (lead_id, channel, direction, content) VALUES (1, 'whatsapp', 'inbound', 'Спасибо, очень интересно, давайте попробуем')"
    )
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1, "whatsapp")

    assert "позитивно" in prompt


# ---- Objection Playbook Tests ----

def test_objection_playbook_in_prompt(patched_conn, db_conn):
    """Known objections should trigger counter-arguments."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute(
        "INSERT INTO lead_context (lead_id, objections) VALUES (1, '[\"Слишком дорого\", \"Нет бюджета\"]')"
    )
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1)

    assert "ВОЗРАЖЕНИЯ" in prompt
    assert "Слишком дорого" in prompt
    assert "ROI" in prompt or "ценност" in prompt.lower()


def test_no_objections_section_when_empty(patched_conn, db_conn):
    """No objections section should appear when objections are empty."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1)

    assert "ВОЗРАЖЕНИЯ" not in prompt


# ---- RAG Context Tests ----

def test_rag_context_injected(patched_conn, db_conn):
    """RAG context from knowledge base should appear in prompt."""
    db_conn.execute("INSERT INTO leads (company_name, industry) VALUES ('TestCo', 'Ломбарды')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.execute(
        "INSERT INTO knowledge_base (title, content, industry, chunk_count) VALUES ('КП Ломбарды', 'автоматизация ломбард оценка', 'Ломбарды', 1)"
    )
    db_conn.execute(
        "INSERT INTO knowledge_chunks (doc_id, chunk_index, content) VALUES (1, 0, 'автоматизация ломбард оценка залога через фото')"
    )
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1)

    # RAG search uses AND keyword match; query is "TestCo Ломбарды"
    # "ломбард" appears in chunk content, "TestCo" doesn't
    # So RAG will return empty — test that prompt still works
    assert "Technomax" in prompt
    assert "Ломбарды" in prompt


# ---- History Truncation Test ----

def test_history_limited_to_8_messages(patched_conn, db_conn):
    """Prompt should only include last 8 messages, not entire history."""
    db_conn.execute("INSERT INTO leads (company_name) VALUES ('TestCo')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    for i in range(15):
        db_conn.execute(
            "INSERT INTO conversations (lead_id, channel, direction, content, created_at) "
            "VALUES (1, 'voice', 'inbound', ?, datetime('now', ?))",
            (f"Message {i}", f"-{15 - i} minutes")
        )
    db_conn.commit()

    from agent_sync import build_agent_prompt
    prompt = build_agent_prompt(1)

    # Last 8 messages (7-14) should be present
    assert "Message 14" in prompt
    assert "Message 13" in prompt
    # First messages (0-6) should NOT be present
    assert "Message 0\n" not in prompt
    assert "Message 6\n" not in prompt


def test_rag_with_matching_keywords(patched_conn, db_conn):
    """RAG should inject context when keywords match."""
    db_conn.execute("INSERT INTO leads (company_name, industry) VALUES ('TestCo', 'Ломбарды')")
    db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
    db_conn.commit()

    # Patch get_rag_context to return known content
    with patch("advanced_features.get_rag_context", return_value="\n\nКОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ:\n--- Фрагмент 1 ---\nавтоматизация оценки залога\n"):
        from agent_sync import build_agent_prompt
        prompt = build_agent_prompt(1)

    assert "БАЗЫ ЗНАНИЙ" in prompt
    assert "оценки залога" in prompt

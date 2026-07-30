"""Tests for WhatsApp AI Agent Service."""
import sys
import os
import sqlite3
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "services"))
os.environ.setdefault("ADMIN_PASSWORD", "test123")


@pytest.fixture
def db_conn(tmp_path):
    """Create a fresh test database with all required tables."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT DEFAULT '',
            region TEXT DEFAULT '',
            city TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            mobile TEXT DEFAULT '',
            email TEXT DEFAULT '',
            whatsapp TEXT DEFAULT '',
            telegram TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            rating REAL DEFAULT 0,
            status TEXT DEFAULT 'new',
            call_result TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            channel TEXT NOT NULL,
            direction TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            event_type TEXT DEFAULT 'message',
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
        CREATE INDEX IF NOT EXISTS idx_conv_lead ON conversations(lead_id);
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _mute_telegram():
    """Prevent real Telegram notifications during tests."""
    with patch("telegram_notifier._send_telegram_sync", return_value=True):
        yield


@pytest.fixture
def patched_all(db_conn):
    """Patch get_conn across all modules used by wa_agent_service."""
    patches = [
        patch("db_conn.get_conn", return_value=db_conn),
        patch("agent_sync.get_conn", return_value=db_conn),
        patch("wa_agent_service.get_conn", return_value=db_conn),
    ]
    for p in patches:
        p.start()
    yield db_conn
    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# _find_lead_by_phone
# ---------------------------------------------------------------------------

class TestFindLeadByPhone:
    """Tests for finding leads by phone number."""

    def test_find_by_mobile(self, patched_all, db_conn):
        from wa_agent_service import _find_lead_by_phone
        db_conn.execute(
            "INSERT INTO leads (company_name, mobile) VALUES ('TestCo', '77071234567')"
        )
        db_conn.commit()
        lead = _find_lead_by_phone("77071234567")
        assert lead is not None
        assert lead["company_name"] == "TestCo"

    def test_find_by_whatsapp(self, patched_all, db_conn):
        from wa_agent_service import _find_lead_by_phone
        db_conn.execute(
            "INSERT INTO leads (company_name, whatsapp) VALUES ('TestCo', '77079998877')"
        )
        db_conn.commit()
        lead = _find_lead_by_phone("77079998877")
        assert lead is not None
        assert lead["company_name"] == "TestCo"

    def test_find_with_plus_prefix(self, patched_all, db_conn):
        from wa_agent_service import _find_lead_by_phone
        db_conn.execute(
            "INSERT INTO leads (company_name, mobile) VALUES ('TestCo', '77071234567')"
        )
        db_conn.commit()
        lead = _find_lead_by_phone("+77071234567")
        assert lead is not None

    def test_find_with_spaces(self, patched_all, db_conn):
        from wa_agent_service import _find_lead_by_phone
        db_conn.execute(
            "INSERT INTO leads (company_name, mobile) VALUES ('TestCo', '77071234567')"
        )
        db_conn.commit()
        lead = _find_lead_by_phone("+7 707 123 45 67")
        assert lead is not None

    def test_find_by_partial_suffix(self, patched_all, db_conn):
        from wa_agent_service import _find_lead_by_phone
        db_conn.execute(
            "INSERT INTO leads (company_name, mobile) VALUES ('TestCo', '77071234567')"
        )
        db_conn.commit()
        # Last 7 digits should match
        lead = _find_lead_by_phone("1234567")
        assert lead is not None

    def test_not_found(self, patched_all, db_conn):
        from wa_agent_service import _find_lead_by_phone
        lead = _find_lead_by_phone("9999999999")
        assert lead is None


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    """Tests for prompt construction."""

    def test_basic_prompt_structure(self):
        from wa_agent_service import build_prompt
        lead = {"company_name": "TestCo", "industry": "Ломбарды"}
        timeline = []
        context = {"stage": "new", "interest_level": 2, "needs": "[]", "objections": "[]"}

        prompt = build_prompt(lead, timeline, context)

        assert "<role>" in prompt
        assert "Technomax" in prompt
        assert "TestCo" in prompt
        assert "Ломбарды" in prompt
        assert "<context>" in prompt
        assert "<rules>" in prompt
        assert "<response_format>" in prompt

    def test_prompt_contains_interest_level(self):
        from wa_agent_service import build_prompt
        lead = {"company_name": "TestCo", "industry": ""}
        context = {"stage": "interested", "interest_level": 8, "needs": "[]", "objections": "[]"}
        prompt = build_prompt(lead, [], context)
        assert "8/10" in prompt

    def test_prompt_contains_timeline(self):
        from wa_agent_service import build_prompt
        lead = {"company_name": "TestCo", "industry": ""}
        timeline = [
            {"channel": "whatsapp", "direction": "inbound", "content": "Привет, интересует AI"},
            {"channel": "whatsapp", "direction": "outbound", "content": "Здравствуйте! Расскажу."},
        ]
        context = {"stage": "new", "interest_level": 0, "needs": "[]", "objections": "[]"}
        prompt = build_prompt(lead, timeline, context)
        assert "Привет, интересует AI" in prompt
        assert "Расскажу" in prompt

    def test_prompt_low_interest_strategy(self):
        from wa_agent_service import build_prompt
        lead = {"company_name": "TestCo", "industry": ""}
        context = {"stage": "new", "interest_level": 2, "needs": "[]", "objections": "[]"}
        prompt = build_prompt(lead, [], context)
        assert "Обучай" in prompt

    def test_prompt_high_interest_strategy(self):
        from wa_agent_service import build_prompt
        lead = {"company_name": "TestCo", "industry": ""}
        context = {"stage": "interested", "interest_level": 9, "needs": "[]", "objections": "[]"}
        prompt = build_prompt(lead, [], context)
        assert "Закрывай" in prompt

    def test_prompt_with_needs_and_objections(self):
        from wa_agent_service import build_prompt
        lead = {"company_name": "TestCo", "industry": ""}
        context = {
            "stage": "negotiating",
            "interest_level": 6,
            "needs": '["автоматизация", "AI оценка"]',
            "objections": '["дорого"]',
        }
        prompt = build_prompt(lead, [], context)
        assert "автоматизация" in prompt
        assert "дорого" in prompt

    def test_prompt_json_format_instructions(self):
        from wa_agent_service import build_prompt
        lead = {"company_name": "TestCo", "industry": ""}
        context = {"stage": "new", "interest_level": 0, "needs": "[]", "objections": "[]"}
        prompt = build_prompt(lead, [], context)
        assert "update_status" in prompt
        assert "schedule_callback" in prompt
        assert "escalate" in prompt


# ---------------------------------------------------------------------------
# call_llm
# ---------------------------------------------------------------------------

class TestCallLLM:
    """Tests for LLM API calls."""

    def test_no_api_key(self):
        from wa_agent_service import call_llm
        with patch("wa_agent_service._get_openrouter_key", return_value=""):
            result = call_llm("system", "user")
            assert result is None

    def test_successful_call(self):
        from wa_agent_service import call_llm
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"reply": "Привет!", "actions": []}'}}]
        }

        with patch.dict(os.environ, {"LLM_PROVIDER": "openrouter"}):
            with patch("wa_agent_service._get_openrouter_key", return_value="test-key"):
                with patch("wa_agent_service.requests.post", return_value=mock_resp) as mock_post:
                    result = call_llm("system prompt", "hello")
                    assert result == '{"reply": "Привет!", "actions": []}'
                    mock_post.assert_called_once()
                    call_args = mock_post.call_args
                    assert "openrouter.ai" in call_args[0][0]

    def test_api_error(self):
        import requests as req_lib
        from wa_agent_service import call_llm
        with patch.dict(os.environ, {"LLM_PROVIDER": "openrouter"}):
            with patch("wa_agent_service._get_openrouter_key", return_value="test-key"):
                with patch("wa_agent_service.requests.post", side_effect=req_lib.RequestException("timeout")):
                    result = call_llm("system", "user")
                    assert result is None

    def test_unexpected_response_format(self):
        from wa_agent_service import call_llm
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"unexpected": "format"}

        with patch.dict(os.environ, {"LLM_PROVIDER": "openrouter"}):
            with patch("wa_agent_service._get_openrouter_key", return_value="test-key"):
                with patch("wa_agent_service.requests.post", return_value=mock_resp):
                    result = call_llm("system", "user")
                    assert result is None


# ---------------------------------------------------------------------------
# parse_and_execute
# ---------------------------------------------------------------------------

class TestParseAndExecute:
    """Tests for parsing LLM responses and executing actions."""

    def test_parse_simple_reply(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_response = json.dumps({
            "reply": "Здравствуйте! Чем могу помочь?",
            "actions": [],
        })

        with patch("wa_agent_service._send_reply"):
            result = parse_and_execute(1, llm_response)

        assert result["reply"] == "Здравствуйте! Чем могу помочь?"
        assert result["actions_taken"] == []
        assert result["error"] is None

    def test_parse_with_update_status(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_response = json.dumps({
            "reply": "Интересно!",
            "actions": [{"type": "update_status", "status": "interested"}],
        })

        with patch("wa_agent_service._send_reply"):
            result = parse_and_execute(1, llm_response)

        assert "update_status:interested" in result["actions_taken"]

        # Verify status was updated
        lead = db_conn.execute("SELECT status FROM leads WHERE id = 1").fetchone()
        assert lead["status"] == "interested"

    def test_parse_with_schedule_callback(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_response = json.dumps({
            "reply": "Перезвоню через 3 дня",
            "actions": [{"type": "schedule_callback", "days": 3}],
        })

        with patch("wa_agent_service._send_reply"):
            result = parse_and_execute(1, llm_response)

        assert "schedule_callback:3d" in result["actions_taken"]

    def test_parse_with_escalate(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_response = json.dumps({
            "reply": "Сейчас передам менеджеру",
            "actions": [{"type": "escalate", "reason": "client wants demo"}],
        })

        with patch("wa_agent_service._send_reply"):
            with patch("telegram_notifier._send_telegram_sync", return_value=True):
                result = parse_and_execute(1, llm_response)

        assert any("escalate" in a for a in result["actions_taken"])

    def test_parse_markdown_wrapped_json(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_response = '```json\n{"reply": "Привет!", "actions": []}\n```'

        with patch("wa_agent_service._send_reply"):
            result = parse_and_execute(1, llm_response)

        assert result["reply"] == "Привет!"
        assert result["error"] is None

    def test_parse_invalid_json_returns_error(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.commit()

        result = parse_and_execute(1, "This is not JSON at all")
        assert result["error"] == "json_parse_failed"
        assert result["reply"] == "This is not JSON at all"

    def test_parse_json_with_trailing_text(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.commit()

        llm_response = '{"reply": "Привет!", "actions": []} Спасибо за вопрос!'

        with patch("wa_agent_service._send_reply"):
            result = parse_and_execute(1, llm_response)

        assert result["reply"] == "Привет!"

    def test_parse_multiple_actions(self, patched_all, db_conn):
        from wa_agent_service import parse_and_execute
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_response = json.dumps({
            "reply": "Ок",
            "actions": [
                {"type": "update_status", "status": "negotiating"},
                {"type": "schedule_callback", "days": 5},
                {"type": "escalate", "reason": "high value client"},
            ],
        })

        with patch("wa_agent_service._send_reply"):
            result = parse_and_execute(1, llm_response)

        assert len(result["actions_taken"]) == 3
        assert "update_status:negotiating" in result["actions_taken"]
        assert "schedule_callback:5d" in result["actions_taken"]


# ---------------------------------------------------------------------------
# process_incoming_message — integration
# ---------------------------------------------------------------------------

class TestProcessIncomingMessage:
    """Integration tests for the full flow."""

    def test_no_lead_found(self, patched_all):
        from wa_agent_service import process_incoming_message
        result = process_incoming_message("9999999999", "Привет")
        assert result["lead_id"] is None
        assert result["error"] == "lead_not_found"

    def test_full_flow(self, patched_all, db_conn):
        from wa_agent_service import process_incoming_message
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile, industry) "
            "VALUES (1, 'TestCo', '77071234567', 'Ломбарды')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_json = json.dumps({
            "reply": "Здравствуйте! Расскажу о наших решениях.",
            "actions": [{"type": "update_status", "status": "called"}],
        })

        with patch("wa_agent_service.call_llm", return_value=llm_json):
            with patch("wa_agent_service._send_reply"):
                result = process_incoming_message("77071234567", "Привет, что предлагаете?")

        assert result["lead_id"] == 1
        assert result["reply"] == "Здравствуйте! Расскажу о наших решениях."
        assert "update_status:called" in result["actions_taken"]
        assert result["error"] is None

    def test_llm_failure(self, patched_all, db_conn):
        from wa_agent_service import process_incoming_message
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        with patch("wa_agent_service.call_llm", return_value=None):
            result = process_incoming_message("77071234567", "Привет")

        assert result["lead_id"] == 1
        assert result["error"] == "llm_failed"

    def test_inbound_logged(self, patched_all, db_conn):
        from wa_agent_service import process_incoming_message
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.execute("INSERT INTO lead_context (lead_id) VALUES (1)")
        db_conn.commit()

        llm_json = json.dumps({"reply": "Ок", "actions": []})

        with patch("wa_agent_service.call_llm", return_value=llm_json):
            with patch("wa_agent_service._send_reply"):
                process_incoming_message("77071234567", "Тестовое сообщение")

        # Check that inbound message was logged
        convs = db_conn.execute(
            "SELECT * FROM conversations WHERE lead_id = 1 AND direction = 'inbound'"
        ).fetchall()
        assert len(convs) >= 1
        assert convs[-1]["content"] == "Тестовое сообщение"


# ---------------------------------------------------------------------------
# _parse_json_response helper
# ---------------------------------------------------------------------------

class TestParseJsonResponse:
    """Tests for JSON extraction from LLM output."""

    def test_clean_json(self):
        from wa_agent_service import _parse_json_response
        result = _parse_json_response('{"reply": "hi", "actions": []}')
        assert result["reply"] == "hi"

    def test_markdown_wrapped(self):
        from wa_agent_service import _parse_json_response
        result = _parse_json_response('```json\n{"reply": "hi", "actions": []}\n```')
        assert result["reply"] == "hi"

    def test_no_markdown_label(self):
        from wa_agent_service import _parse_json_response
        result = _parse_json_response('```\n{"reply": "hi", "actions": []}\n```')
        assert result["reply"] == "hi"

    def test_json_embedded_in_text(self):
        from wa_agent_service import _parse_json_response
        result = _parse_json_response(
            'Вот ответ: {"reply": "Привет", "actions": []} Надеюсь поможет!'
        )
        assert result["reply"] == "Привет"

    def test_invalid_json(self):
        from wa_agent_service import _parse_json_response
        result = _parse_json_response("This is not JSON")
        assert result is None

    def test_empty_string(self):
        from wa_agent_service import _parse_json_response
        result = _parse_json_response("")
        assert result is None

    def test_none(self):
        from wa_agent_service import _parse_json_response
        result = _parse_json_response(None)
        assert result is None


# ---------------------------------------------------------------------------
# send_reply
# ---------------------------------------------------------------------------

class TestSendReply:
    """Tests for WhatsApp reply sending."""

    def test_send_reply_success(self, patched_all, db_conn):
        from wa_agent_service import _send_reply
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile) VALUES (1, 'TestCo', '77071234567')"
        )
        db_conn.commit()

        mock_client = MagicMock()
        mock_client.send_text.return_value = MagicMock(success=True, message_id="msg123")

        with patch("wa_agent_service.WhatsAppClient", return_value=mock_client):
            _send_reply(1, "Test reply")

        mock_client.send_text.assert_called_once_with("77071234567", "Test reply")

    def test_send_reply_no_phone(self, patched_all, db_conn, caplog):
        from wa_agent_service import _send_reply
        db_conn.execute(
            "INSERT INTO leads (id, company_name, mobile, whatsapp, phone) "
            "VALUES (1, 'TestCo', '', '', '')"
        )
        db_conn.commit()

        _send_reply(1, "Test reply")
        # Should log error about no phone number
        assert any("No phone" in r.message for r in caplog.records)

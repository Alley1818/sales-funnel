"""Tests for agent_tools endpoints (Technomax Лидген integration)."""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def app():
    """Create test app with fresh DB."""
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_lead():
    """Insert a test lead into the DB."""
    from db_conn import get_conn
    conn = get_conn()
    conn.execute(
        "INSERT INTO leads (company_name, mobile, whatsapp, phone, email, industry, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("TestCo", "77001234567", "77001234567", "77001234567", "test@example.com", "IT", "new"),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM leads WHERE mobile = '77001234567'").fetchone()
    return dict(row)["id"]


# ─── send-whatsapp ───────────────────────────────────────────────

class TestSendWhatsApp:
    def test_missing_phone(self, client):
        r = client.post("/api/agent/send-whatsapp", json={})
        assert r.status_code == 400
        assert "phone" in r.json["error"]

    def test_lead_not_found(self, client):
        r = client.post("/api/agent/send-whatsapp", json={"phone": "79999999999"})
        assert r.status_code == 404
        assert "not found" in r.json["error"].lower()

    @patch("whatsapp_client.WhatsAppClient.send_text")
    def test_success(self, mock_send, client, seed_lead):
        mock_send.return_value = MagicMock(success=True, message_id="msg_123")

        r = client.post("/api/agent/send-whatsapp", json={"phone": "77001234567"})
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["lead_id"] == seed_lead
        assert r.json["message_sent"] is True
        mock_send.assert_called_once()

    @patch("whatsapp_client.WhatsAppClient.send_text")
    def test_wa_send_failure(self, mock_send, client, seed_lead):
        mock_send.return_value = MagicMock(success=False, error="timeout")

        r = client.post("/api/agent/send-whatsapp", json={"phone": "77001234567"})
        assert r.status_code == 502
        assert "failed" in r.json["error"].lower()


# ─── send-email ──────────────────────────────────────────────────

class TestSendEmail:
    def test_missing_both(self, client):
        r = client.post("/api/agent/send-email", json={})
        assert r.status_code == 400

    @patch("email_sender.EmailSender.send")
    def test_success_by_email(self, mock_send, client, seed_lead):
        mock_send.return_value = MagicMock(success=True)

        r = client.post("/api/agent/send-email", json={
            "email": "test@example.com",
            "phone": "77001234567",
        })
        assert r.status_code == 200
        assert r.json["ok"] is True
        mock_send.assert_called_once()

    @patch("email_sender.EmailSender.send")
    def test_success_by_phone_only(self, mock_send, client, seed_lead):
        mock_send.return_value = MagicMock(success=True)

        r = client.post("/api/agent/send-email", json={"phone": "77001234567"})
        assert r.status_code == 200
        assert r.json["ok"] is True

    @patch("email_sender.EmailSender.send")
    def test_send_failure(self, mock_send, client, seed_lead):
        mock_send.return_value = MagicMock(success=False, error="SMTP down")

        r = client.post("/api/agent/send-email", json={"email": "test@example.com"})
        assert r.status_code == 502


# ─── create-deal ─────────────────────────────────────────────────

class TestCreateDeal:
    def test_missing_phone(self, client):
        r = client.post("/api/agent/create-deal", json={"contact_name": "John"})
        assert r.status_code == 400

    def test_update_existing_lead(self, client, seed_lead):
        r = client.post("/api/agent/create-deal", json={
            "phone": "77001234567",
            "contact_name": "TestCo",
            "interest_level": 8,
            "notes": "Very interested",
        })
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["lead_id"] == seed_lead

        # Verify status changed
        from db_conn import get_conn
        lead = get_conn().execute("SELECT status FROM leads WHERE id = ?", (seed_lead,)).fetchone()
        assert lead["status"] == "interested"

    def test_create_new_lead(self, client):
        r = client.post("/api/agent/create-deal", json={
            "phone": "77009998877",
            "contact_name": "NewCo",
            "interest_level": 6,
            "notes": "Warm lead",
        })
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["lead_id"] > 0

        # Cleanup
        from db_conn import get_conn
        lid = r.json["lead_id"]
        get_conn().execute("DELETE FROM conversations WHERE lead_id = ?", (lid,))
        get_conn().execute("DELETE FROM lead_context WHERE lead_id = ?", (lid,))
        get_conn().execute("DELETE FROM callbacks WHERE lead_id = ?", (lid,))
        get_conn().execute("DELETE FROM leads WHERE id = ?", (lid,))
        get_conn().commit()


# ─── schedule-callback ───────────────────────────────────────────

class TestScheduleCallback:
    def test_missing_phone(self, client):
        r = client.post("/api/agent/schedule-callback", json={
            "callback_datetime": "2026-08-01 10:00",
        })
        assert r.status_code == 400

    def test_missing_datetime(self, client):
        r = client.post("/api/agent/schedule-callback", json={
            "phone": "77001234567",
        })
        assert r.status_code == 400

    def test_success_existing_lead(self, client, seed_lead):
        r = client.post("/api/agent/schedule-callback", json={
            "phone": "77001234567",
            "callback_datetime": "2026-08-01 10:00",
            "contact_name": "TestCo",
        })
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["lead_id"] == seed_lead

    def test_success_new_lead(self, client):
        r = client.post("/api/agent/schedule-callback", json={
            "phone": "77007776655",
            "callback_datetime": "2026-08-02 14:00",
            "contact_name": "NewClient",
        })
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["lead_id"] > 0

        # Cleanup
        from db_conn import get_conn
        lid = r.json["lead_id"]
        get_conn().execute("DELETE FROM conversations WHERE lead_id = ?", (lid,))
        get_conn().execute("DELETE FROM lead_context WHERE lead_id = ?", (lid,))
        get_conn().execute("DELETE FROM callbacks WHERE lead_id = ?", (lid,))
        get_conn().execute("DELETE FROM leads WHERE id = ?", (lid,))
        get_conn().commit()


# ─── blueprint registration ──────────────────────────────────────

class TestBlueprintRegistered:
    def test_endpoints_exist(self, app):
        """Verify all 4 endpoints are registered."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/api/agent/send-whatsapp" in rules
        assert "/api/agent/send-email" in rules
        assert "/api/agent/create-deal" in rules
        assert "/api/agent/schedule-callback" in rules

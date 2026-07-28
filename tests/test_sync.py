"""Tests for unified timeline, status sync, and lead push."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def _ensure_db():
    """Ensure DB is initialized and create test leads."""
    from db_conn import get_conn
    conn = get_conn()
    # Create test leads for FK references
    for i in range(8):
        lead_id = 999001 + i
        try:
            conn.execute(
                "INSERT OR IGNORE INTO leads (id, company_name, industry, mobile, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (lead_id, f"TestCo-{lead_id}", "IT", f"700{lead_id}", "new"),
            )
        except Exception:
            pass
    conn.commit()
    yield
    # Cleanup
    for i in range(8):
        lead_id = 999001 + i
        try:
            conn.execute("DELETE FROM conversations WHERE lead_id = ?", (lead_id,))
            conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        except Exception:
            pass
    conn.commit()


# ---- Timeline / Event Logging Tests ----

def test_log_event_basic():
    """log_event writes to conversations with event_type."""
    from agent_sync import log_event, get_lead_timeline
    log_event(999001, "status_change", "Статус: new → called", channel="system")
    timeline = get_lead_timeline(999001, limit=5)
    assert len(timeline) >= 1
    evt = timeline[-1]
    assert evt["event_type"] == "status_change"
    assert evt["direction"] == "event"
    assert "new → called" in evt["content"]


def test_log_status_change():
    """log_status_change creates a formatted event."""
    from agent_sync import log_status_change, get_lead_timeline
    log_status_change(999002, "new", "interested", "AI call result")
    timeline = get_lead_timeline(999002, event_type="status_change")
    assert len(timeline) >= 1
    evt = timeline[-1]
    assert "new → interested" in evt["content"]
    assert evt["metadata"]["old_status"] == "new"
    assert evt["metadata"]["new_status"] == "interested"


def test_log_kp_sent():
    """log_kp_sent creates a kp_sent event."""
    from agent_sync import log_kp_sent, get_lead_timeline
    log_kp_sent(999003, "whatsapp", "TestCorp")
    timeline = get_lead_timeline(999003, event_type="kp_sent")
    assert len(timeline) >= 1
    evt = timeline[-1]
    assert "whatsapp" in evt["content"].lower()


def test_log_followup_event():
    """log_followup_event creates a followup event."""
    from agent_sync import log_followup_event, get_lead_timeline
    log_followup_event(999004, attempt=2, channel="whatsapp")
    timeline = get_lead_timeline(999004, event_type="followup")
    assert len(timeline) >= 1
    assert timeline[-1]["metadata"]["attempt"] == 2


def test_log_score_change():
    """log_score_change creates a score_change event."""
    from agent_sync import log_score_change, get_lead_timeline
    log_score_change(999005, 30, 50, "interested callback")
    timeline = get_lead_timeline(999005, event_type="score_change")
    assert len(timeline) >= 1
    evt = timeline[-1]
    assert "30 → 50" in evt["content"]
    assert evt["metadata"]["old_score"] == 30


def test_timeline_ordering():
    """Timeline returns all events for a lead."""
    from agent_sync import log_event, get_lead_timeline
    log_event(999006, "message", "first event")
    log_event(999006, "message", "second event")
    log_event(999006, "status_change", "third event")
    timeline = get_lead_timeline(999006)
    assert len(timeline) >= 3
    contents = [e["content"] for e in timeline]
    assert "first event" in contents
    assert "second event" in contents
    assert "third event" in contents


def test_timeline_filter_by_event_type():
    """get_lead_timeline filters by event_type."""
    from agent_sync import log_event, get_lead_timeline
    log_event(999007, "message", "a message")
    log_event(999007, "status_change", "a status change")
    log_event(999007, "kp_sent", "a kp")

    only_status = get_lead_timeline(999007, event_type="status_change")
    for evt in only_status:
        assert evt["event_type"] == "status_change"


def test_timeline_empty_lead():
    """Timeline for non-existent lead returns empty list."""
    from agent_sync import get_lead_timeline
    timeline = get_lead_timeline(999999)
    assert timeline == []


def test_timeline_metadata_parsed():
    """Timeline events have parsed metadata dicts."""
    from agent_sync import log_event, get_lead_timeline
    log_event(999008, "test_event", "test", metadata={"key": "value"})
    timeline = get_lead_timeline(999008, event_type="test_event")
    assert len(timeline) >= 1
    assert isinstance(timeline[-1]["metadata"], dict)
    assert timeline[-1]["metadata"]["key"] == "value"


# ---- Lead Push CSV Tests ----

def test_generate_csv_basic():
    """generate_csv produces valid CSV with headers."""
    from app.services.lead_push_service import generate_csv
    leads = [
        {"company_name": "TestCo", "mobile": "+77001234567", "email": "t@t.com", "industry": "IT"},
    ]
    csv_str = generate_csv(leads)
    lines = csv_str.strip().split("\n")
    assert lines[0] == "name,phone,email,industry"
    assert "TestCo" in lines[1]
    assert "77001234567" in lines[1]


def test_generate_csv_strips_plus():
    """generate_csv strips + from phone numbers."""
    from app.services.lead_push_service import generate_csv
    leads = [{"company_name": "X", "mobile": "+7 700 123-4567", "email": "", "industry": ""}]
    csv_str = generate_csv(leads)
    assert "+7" not in csv_str
    assert "77001234567" in csv_str


def test_generate_csv_multiple_leads():
    """generate_csv handles multiple leads."""
    from app.services.lead_push_service import generate_csv
    leads = [
        {"company_name": "A", "mobile": "7001111111", "email": "", "industry": ""},
        {"company_name": "B", "mobile": "7002222222", "email": "", "industry": ""},
    ]
    csv_str = generate_csv(leads)
    lines = csv_str.strip().split("\n")
    assert len(lines) == 3  # header + 2 leads


def test_push_leads_empty_list():
    """push_leads_to_task with empty list returns error."""
    from app.services.lead_push_service import push_leads_to_task
    result = push_leads_to_task([], "test")
    assert "error" in result


def test_push_leads_no_agent():
    """push_leads_to_task without agent_id returns error."""
    from app.services.lead_push_service import push_leads_to_task
    result = push_leads_to_task(
        [{"mobile": "+77001234567"}], "test", agent_id="", bot_id=""
    )
    assert "error" in result

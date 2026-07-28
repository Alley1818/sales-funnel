"""Basic tests for Sales Funnel."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_middleware_imports():
    from middleware import (init_auth, verify_password, create_session, check_session,
        require_auth, get_pooled_conn, validate_status, sanitize_string,
        log_api_action, safe_send, rate_limit_middleware)
    assert all(callable(f) for f in [init_auth, verify_password, create_session, check_session,
        require_auth, get_pooled_conn, validate_status, sanitize_string,
        log_api_action, safe_send, rate_limit_middleware])


def test_features_imports():
    from funnel_features import (create_agent, get_agents, create_template, get_templates,
        add_dnc, is_dnc, score_lead, create_campaign, get_campaigns)
    assert all(callable(f) for f in [create_agent, get_agents, create_template, get_templates,
        add_dnc, is_dnc, score_lead, create_campaign, get_campaigns])


def test_advanced_imports():
    from advanced_features import (add_document, search_knowledge, analyze_sentiment,
        auto_score_lead, batch_score_leads, schedule_callback, get_dashboard_data)
    assert all(callable(f) for f in [add_document, search_knowledge, analyze_sentiment,
        auto_score_lead, batch_score_leads, schedule_callback, get_dashboard_data])


def test_app_factory_import():
    """Verify the app factory can be imported."""
    from app import create_app
    assert callable(create_app)


@pytest.mark.integration
def test_flask_health():
    from app import create_app
    app = create_app()
    with app.test_client() as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json["status"] == "ok"


@pytest.mark.integration
def test_flask_stats():
    from app import create_app
    app = create_app()
    with app.test_client() as client:
        r = client.get("/api/stats")
        assert r.status_code == 200
        assert "overall" in r.json


@pytest.mark.integration
def test_flask_agents():
    from app import create_app
    app = create_app()
    with app.test_client() as client:
        r = client.get("/api/agents")
        assert r.status_code == 200
        assert "agents" in r.json


@pytest.mark.integration
def test_flask_templates():
    from app import create_app
    app = create_app()
    with app.test_client() as client:
        r = client.get("/api/templates")
        assert r.status_code == 200
        assert "templates" in r.json


@pytest.mark.integration
def test_flask_analytics():
    from app import create_app
    app = create_app()
    with app.test_client() as client:
        r = client.get("/api/analytics/dashboard")
        assert r.status_code == 200
        assert "status_counts" in r.json


@pytest.mark.integration
def test_flask_leads_list():
    from app import create_app
    app = create_app()
    with app.test_client() as client:
        r = client.get("/api/leads/list?limit=5")
        assert r.status_code == 200
        assert "leads" in r.json
        assert "total" in r.json


def test_sentiment_positive():
    from advanced_features import analyze_sentiment
    r = analyze_sentiment("Спасибо, очень интересно, давайте попробуем")
    assert r["sentiment"] == "positive"


def test_sentiment_negative():
    from advanced_features import analyze_sentiment
    r = analyze_sentiment("Не интересно, не звоните больше")
    assert r["sentiment"] == "negative"


def test_sentiment_angry():
    from advanced_features import analyze_sentiment
    r = analyze_sentiment("Идиоты, отвратительно, ненавижу")
    assert r["sentiment"] == "angry"


def test_auto_score():
    from advanced_features import auto_score_lead
    lead = {"mobile": "77001234567", "email": "test@test.com", "whatsapp": "77001234567",
            "industry": "Ломбарды", "city": "Алматы"}
    r = auto_score_lead(lead)
    assert r["score"] >= 50
    assert r["category"] in ("hot", "warm")


def test_sanitize_string():
    from middleware import sanitize_string
    assert sanitize_string("  hello  ") == "hello"
    assert sanitize_string("a" * 2000, max_len=100) == "a" * 100


def test_validate_status():
    from middleware import validate_status
    assert validate_status("new") == "new"
    assert validate_status("interested") == "interested"
    with pytest.raises(ValueError):
        validate_status("invalid_status")

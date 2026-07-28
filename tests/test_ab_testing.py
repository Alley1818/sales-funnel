"""Tests for A/B testing: Thompson sampling, chi-squared significance, auto-winner."""
import sys
import os
import sqlite3
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
        CREATE TABLE ab_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            template_a_id INTEGER,
            template_b_id INTEGER,
            sent_a INTEGER DEFAULT 0,
            sent_b INTEGER DEFAULT 0,
            response_a INTEGER DEFAULT 0,
            response_b INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE message_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            channel TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            body TEXT DEFAULT '',
            is_default INTEGER DEFAULT 0,
            ab_variant TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def patched(db_conn):
    with patch("funnel_features.get_conn", return_value=db_conn):
        yield db_conn


# ---- Thompson Sampling Tests ----

def test_thompson_balanced(patched, db_conn):
    """With equal rates, Thompson sampling should roughly split 50/50."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 100, 100, 20, 20)"
    )
    db_conn.commit()

    from funnel_features import pick_ab_variant
    counts = {"A": 0, "B": 0}
    for _ in range(1000):
        v = pick_ab_variant(1)
        counts[v] += 1

    # Should be roughly 50/50 (within 20% tolerance)
    assert 300 < counts["A"] < 700
    assert 300 < counts["B"] < 700


def test_thompson_favors_winner(patched, db_conn):
    """With clear winner, Thompson sampling should heavily favor it."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 100, 100, 50, 10)"
    )
    db_conn.commit()

    from funnel_features import pick_ab_variant
    counts = {"A": 0, "B": 0}
    for _ in range(1000):
        v = pick_ab_variant(1)
        counts[v] += 1

    # A has 50% response vs B's 10% — A should win overwhelmingly
    assert counts["A"] > 800


# ---- Chi-Squared Significance Tests ----

def test_significance_clear_winner(patched, db_conn):
    """Large difference with enough samples should be significant."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 100, 100, 50, 10)"
    )
    db_conn.commit()

    from funnel_features import get_ab_significance
    result = get_ab_significance(1)

    assert result["significant"] is True
    assert result["confidence"] >= 0.95
    assert result["winner"] == "A"


def test_significance_no_difference(patched, db_conn):
    """Equal rates should not be significant."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 100, 100, 20, 20)"
    )
    db_conn.commit()

    from funnel_features import get_ab_significance
    result = get_ab_significance(1)

    assert result["significant"] is False
    assert result["winner"] is None


def test_significance_insufficient_sample(patched, db_conn):
    """Should not be significant with fewer than 10 sends per variant."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 5, 5, 4, 1)"
    )
    db_conn.commit()

    from funnel_features import get_ab_significance
    result = get_ab_significance(1)

    assert result["significant"] is False
    assert "min 10" in result["reason"]


def test_significance_b_wins(patched, db_conn):
    """B variant with higher rate should be detected."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 100, 100, 10, 50)"
    )
    db_conn.commit()

    from funnel_features import get_ab_significance
    result = get_ab_significance(1)

    assert result["significant"] is True
    assert result["winner"] == "B"


def test_significance_edge_case_close(patched, db_conn):
    """Close rates should not be significant with moderate sample."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 50, 50, 12, 10)"
    )
    db_conn.commit()

    from funnel_features import get_ab_significance
    result = get_ab_significance(1)

    assert result["significant"] is False


# ---- Auto-Winner Tests ----

def test_check_ab_winner_declares(patched, db_conn):
    """check_ab_winner should mark test as completed with winner."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 100, 100, 50, 10)"
    )
    db_conn.commit()

    from funnel_features import check_ab_winner
    result = check_ab_winner(1)

    assert result["action"] == "winner_A_declared"

    # Verify DB updated
    test = db_conn.execute("SELECT status FROM ab_tests WHERE id = 1").fetchone()
    assert test["status"] == "winner_A"


def test_check_ab_winner_continues(patched, db_conn):
    """check_ab_winner should continue testing when not significant."""
    db_conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id, sent_a, sent_b, response_a, response_b) "
        "VALUES ('Test', 1, 2, 50, 50, 12, 10)"
    )
    db_conn.commit()

    from funnel_features import check_ab_winner
    result = check_ab_winner(1)

    assert result["action"] == "continue_testing"


# ---- Norm CDF Helper ----

def test_norm_cdf():
    """Test the normal CDF approximation."""
    from funnel_features import _norm_cdf
    assert abs(_norm_cdf(0) - 0.5) < 0.001
    assert abs(_norm_cdf(1.96) - 0.975) < 0.01
    assert _norm_cdf(-3) < 0.01
    assert _norm_cdf(3) > 0.99

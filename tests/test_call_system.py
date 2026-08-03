"""Tests for call_queue, call_orchestrator, retry_manager, business_hours."""
import sys
import os
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ===== Imports =====

def test_call_queue_imports():
    from call_queue import (get_next_batch, get_next_lead, get_queue_stats,
                            mark_called, update_lead_after_call, is_in_dnc,
                            CALLABLE_STATUSES, DAILY_CALL_LIMIT)
    assert all(callable(f) for f in [get_next_batch, get_next_lead, get_queue_stats,
                                      mark_called, update_lead_after_call, is_in_dnc])


def test_call_orchestrator_imports():
    from call_orchestrator import CallOrchestrator
    assert CallOrchestrator is not None


def test_retry_manager_imports():
    from retry_manager import (schedule_retry, get_retry_count, get_no_answer_count,
                               get_due_callbacks, get_retry_stats)
    assert all(callable(f) for f in [schedule_retry, get_retry_count,
                                      get_no_answer_count, get_due_callbacks, get_retry_stats])


def test_business_hours_imports():
    from business_hours import (is_business_hours, minutes_until_open,
                                get_cooldown_sec, get_schedule_info)
    assert all(callable(f) for f in [is_business_hours, minutes_until_open,
                                      get_cooldown_sec, get_schedule_info])


# ===== BusinessHours logic =====

def test_schedule_info_keys():
    from business_hours import get_schedule_info
    info = get_schedule_info()
    assert info["work_start"] == "09:00"
    assert info["work_end"] == "18:00"
    assert info["daily_limit"] == 50
    assert "is_business_hours" in info
    assert "minutes_until_open" in info


def test_cooldown_range():
    from business_hours import get_cooldown_sec
    for _ in range(10):
        cd = get_cooldown_sec()
        assert 30 <= cd <= 60


def test_minutes_until_open_non_negative():
    from business_hours import minutes_until_open
    assert minutes_until_open() >= 0


# ===== CallQueue logic =====

def test_queue_stats_keys():
    from call_queue import get_queue_stats
    stats = get_queue_stats()
    assert "waiting" in stats
    assert "called_today" in stats
    assert "daily_limit" in stats
    assert "remaining" in stats
    assert stats["daily_limit"] == 50


def test_callable_statuses():
    from call_queue import CALLABLE_STATUSES
    assert "new" in CALLABLE_STATUSES
    assert "callback" in CALLABLE_STATUSES
    assert "no_answer" in CALLABLE_STATUSES


def test_next_batch_returns_list():
    from call_queue import get_next_batch
    batch = get_next_batch(limit=3)
    assert isinstance(batch, list)
    assert len(batch) <= 3


def test_is_in_dnc_empty():
    from call_queue import is_in_dnc
    assert is_in_dnc("") is False


# ===== CallOrchestrator logic =====

def test_orchestrator_init():
    from call_orchestrator import CallOrchestrator
    orch = CallOrchestrator(pipecat=MagicMock())
    assert orch.is_running is False
    assert orch.is_paused is False
    assert orch.current_lead is None


def test_orchestrator_status():
    from call_orchestrator import CallOrchestrator
    orch = CallOrchestrator(pipecat=MagicMock())
    status = orch.get_status()
    assert status["running"] is False
    assert status["paused"] is False
    assert "stats" in status
    assert "queue" in status


def test_orchestrator_start_stop():
    from call_orchestrator import CallOrchestrator
    orch = CallOrchestrator(pipecat=MagicMock())
    orch.start()
    assert orch._running is True
    orch.stop()
    assert orch._running is False


def test_orchestrator_pause_resume():
    from call_orchestrator import CallOrchestrator
    orch = CallOrchestrator(pipecat=MagicMock())
    orch.start()
    orch.pause()
    assert orch.is_paused is True
    orch.resume()
    assert orch.is_paused is False
    orch.stop()


# ===== RetryManager logic =====

def test_retry_count_non_negative():
    from retry_manager import get_retry_count
    assert get_retry_count(99999) == 0


def test_schedule_retry_interested_no_retry():
    from retry_manager import schedule_retry
    result = schedule_retry(1, "interested")
    assert result["action"] == "no_retry"


def test_schedule_retry_refused_no_retry():
    from retry_manager import schedule_retry
    result = schedule_retry(1, "refused")
    assert result["action"] == "no_retry"


def test_retry_stats_keys():
    from retry_manager import get_retry_stats
    stats = get_retry_stats()
    assert "pending" in stats
    assert "due_now" in stats

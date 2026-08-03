"""
RetryManager — повторные попытки обзвана с exponential backoff.
Логика: no_answer → retry через 2ч → 12ч → 24ч → WhatsApp fallback.
Использует существующую таблицу callbacks для планирования.
"""
import logging
from datetime import datetime, timedelta

from db_conn import get_conn
from advanced_features import schedule_callback, get_pending_callbacks, complete_callback

logger = logging.getLogger("retry_manager")

# Максимум попыток дозвона
MAX_VOICE_RETRIES = 3

# Интервалы между попытками (часы)
RETRY_INTERVALS_HOURS = [2, 12, 24]

# Статусы, при которых нужен retry
RETRYABLE_RESULTS = ("no_answer", "voicemail", "error")


def get_retry_count(lead_id: int) -> int:
    """Сколько раз уже звонили этому лиду (всего)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM call_log WHERE lead_id = ?",
        (lead_id,)
    ).fetchone()
    return row["cnt"] if row else 0


def get_no_answer_count(lead_id: int) -> int:
    """Сколько раз не ответили этому лиду."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM call_log WHERE lead_id = ? AND result IN ('no_answer', 'voicemail', 'error')",
        (lead_id,)
    ).fetchone()
    return row["cnt"] if row else 0


def schedule_retry(lead_id: int, call_result: str) -> dict:
    """
    Планирует повторный звонок или WhatsApp fallback.
    Возвращает: {"action": "retry"|"whatsapp"|"give_up", "scheduled_at": "..."}
    """
    if call_result not in RETRYABLE_RESULTS:
        return {"action": "no_retry", "reason": f"result={call_result}"}

    no_answer_count = get_no_answer_count(lead_id)

    # Ещё есть попытки звонка
    if no_answer_count < MAX_VOICE_RETRIES:
        delay_hours = RETRY_INTERVALS_HOURS[no_answer_count]
        scheduled_at = (datetime.now() + timedelta(hours=delay_hours)).strftime("%Y-%m-%d %H:%M")
        schedule_callback(lead_id, scheduled_at, channel="voice",
                          notes=f"Retry #{no_answer_count + 1} after {delay_hours}h")
        logger.info(f"Lead {lead_id}: retry #{no_answer_count + 1} scheduled at {scheduled_at}")
        return {"action": "retry", "attempt": no_answer_count + 1, "scheduled_at": scheduled_at}

    # Все попытки исчерпаны → WhatsApp
    scheduled_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    schedule_callback(lead_id, scheduled_at, channel="whatsapp",
                      notes=f"WhatsApp fallback after {MAX_VOICE_RETRIES} failed calls")
    logger.info(f"Lead {lead_id}: WhatsApp fallback scheduled at {scheduled_at}")
    return {"action": "whatsapp", "scheduled_at": scheduled_at}


def get_due_callbacks() -> list[dict]:
    """Возвращает колбэки, которые пора выполнять (scheduled_at <= сейчас)."""
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = conn.execute("""
        SELECT cb.*, l.company_name, l.mobile, l.phone, l.whatsapp, l.industry
        FROM callbacks cb
        JOIN leads l ON cb.lead_id = l.id
        WHERE cb.status = 'pending' AND cb.scheduled_at <= ?
        ORDER BY cb.scheduled_at
    """, (now,)).fetchall()
    return [dict(r) for r in rows]


def process_due_callbacks():
    """
    Обрабатывает_due_колбэки: возвращает их для CallOrchestrator.
    Не выполняет звонки сам — только отдаёт список лидов.
    """
    due = get_due_callbacks()
    if due:
        logger.info(f"RetryManager: {len(due)} callbacks due")
    return due


def mark_callback_done(callback_id: int, status: str = "completed"):
    """Отмечает колбак как выполненный."""
    complete_callback(callback_id, status)


def get_retry_stats() -> dict:
    """Статистика по retry."""
    conn = get_conn()
    pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM callbacks WHERE status = 'pending'"
    ).fetchone()["cnt"]

    due = conn.execute(
        "SELECT COUNT(*) as cnt FROM callbacks WHERE status = 'pending' AND scheduled_at <= datetime('now')"
    ).fetchone()["cnt"]

    completed_today = conn.execute(
        "SELECT COUNT(*) as cnt FROM callbacks WHERE status = 'completed' AND DATE(scheduled_at) = DATE('now')"
    ).fetchone()["cnt"]

    return {
        "pending": pending,
        "due_now": due,
        "completed_today": completed_today,
    }

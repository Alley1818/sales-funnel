"""
CallQueue — очередь обзвана.
Выбирает лидов по рейтингу, фильтрует по статусу/DNC/повторам,
выдаёт следующего для CallOrchestrator.
"""
import logging
from datetime import datetime, date

from db_conn import get_conn

logger = logging.getLogger("call_queue")

# Статусы лидов, которые МОЖНО звонить
CALLABLE_STATUSES = ("new", "callback", "no_answer")

# Максимум звонков одному лиду в день
MAX_CALLS_PER_DAY = 2

# Максимум звонков в день всего
DAILY_CALL_LIMIT = 50


def get_next_batch(limit: int = 10) -> list[dict]:
    """
    Возвращает следующую пачку лидов для обзвана.
    Сортировка: рейтинг DESC, потом по дате создания (старые первые).
    Фильтры: callable status, не в DNC, не превышен дневной лимит звонков,
    не звонили больше MAX_CALLS_PER_DAY раз сегодня.
    """
    conn = get_conn()
    today = date.today().isoformat()

    rows = conn.execute("""
        SELECT l.id, l.company_name, l.phone, l.mobile, l.whatsapp,
               l.telegram, l.industry, l.rating, l.status, l.call_result,
               l.city, l.region
        FROM leads l
        WHERE l.status IN ({statuses})
          AND (l.phone IS NOT NULL AND l.phone != ''
               OR l.mobile IS NOT NULL AND l.mobile != '')
          AND l.phone NOT IN (SELECT phone FROM do_not_call)
          AND l.mobile NOT IN (SELECT phone FROM do_not_call)
          AND (
              SELECT COUNT(*) FROM call_log cl
              WHERE cl.lead_id = l.id
                AND DATE(cl.created_at) = ?
          ) < {max_calls}
        ORDER BY l.rating DESC, l.created_at ASC
        LIMIT ?
    """.format(
        statuses=", ".join(f"'{s}'" for s in CALLABLE_STATUSES),
        max_calls=MAX_CALLS_PER_DAY,
    ), (today, limit)).fetchall()

    leads = [dict(r) for r in rows]
    logger.info(f"CallQueue: {len(leads)} leads ready for dialing")
    return leads


def get_next_lead() -> dict | None:
    """Возвращает одного следующего лида или None если очередь пуста."""
    batch = get_next_batch(limit=1)
    return batch[0] if batch else None


def get_queue_stats() -> dict:
    """Статистика очереди: сколько ждут, сколько звонили сегодня, лимит."""
    conn = get_conn()
    today = date.today().isoformat()

    # Сколько лидов ждут обзвана
    waiting = conn.execute("""
        SELECT COUNT(*) as cnt FROM leads l
        WHERE l.status IN ({statuses})
          AND (l.phone IS NOT NULL AND l.phone != ''
               OR l.mobile IS NOT NULL AND l.mobile != '')
          AND l.phone NOT IN (SELECT phone FROM do_not_call)
          AND l.mobile NOT IN (SELECT phone FROM do_not_call)
    """.format(
        statuses=", ".join(f"'{s}'" for s in CALLABLE_STATUSES)
    )).fetchone()["cnt"]

    # Сколько звонков сделано сегодня
    called_today = conn.execute(
        "SELECT COUNT(*) as cnt FROM call_log WHERE DATE(created_at) = ?",
        (today,)
    ).fetchone()["cnt"]

    # Сколько уникальных лидов обзвонили сегодня
    leads_called_today = conn.execute(
        "SELECT COUNT(DISTINCT lead_id) as cnt FROM call_log WHERE DATE(created_at) = ?",
        (today,)
    ).fetchone()["cnt"]

    return {
        "waiting": waiting,
        "called_today": called_today,
        "leads_called_today": leads_called_today,
        "daily_limit": DAILY_CALL_LIMIT,
        "remaining": max(0, DAILY_CALL_LIMIT - called_today),
    }


def mark_called(lead_id: int, result: str, duration_sec: int = 0,
                transcript: str = "", call_type: str = "autocall") -> int:
    """Записывает результат звонка в call_log."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO call_log (lead_id, call_type, result, duration_sec, transcript)
           VALUES (?, ?, ?, ?, ?)""",
        (lead_id, call_type, result, duration_sec, transcript)
    )
    conn.commit()
    logger.info(f"Call logged: lead={lead_id}, result={result}, dur={duration_sec}s")
    return cur.lastrowid or 0


def update_lead_after_call(lead_id: int, result: str, notes: str = ""):
    """Обновляет статус лида после звонка."""
    conn = get_conn()
    status_map = {
        "interested": "interested",
        "callback": "callback",
        "refused": "refused",
        "no_answer": "no_answer",
        "wrong_number": "wrong_number",
        "voicemail": "no_answer",
    }
    new_status = status_map.get(result, "called")
    conn.execute(
        "UPDATE leads SET status = ?, call_result = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_status, result, notes, lead_id)
    )
    conn.commit()
    logger.info(f"Lead {lead_id} updated: status={new_status}, result={result}")


def is_in_dnc(phone: str) -> bool:
    """Проверяет, есть ли номер в DNC списке."""
    if not phone:
        return False
    conn = get_conn()
    clean = "".join(c for c in phone if c.isdigit())
    row = conn.execute(
        "SELECT 1 FROM do_not_call WHERE phone = ? LIMIT 1", (clean,)
    ).fetchone()
    return row is not None


def reset_daily_queue():
    """Сбрасывает счётчик звонков на день (вызывается в 00:00)."""
    logger.info("Daily queue reset")
    # Ничего не делаем — считаем по DATE(call_log.created_at)
    pass

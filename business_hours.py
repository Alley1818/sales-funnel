"""
BusinessHours — расписание звонков.
Проверяет рабочее время, enforce паузы и лимиты.
"""
import logging
from datetime import datetime, time

logger = logging.getLogger("business_hours")

# Рабочие часы (Астана, UTC+5)
WORK_START = time(9, 0)   # 09:00
WORK_END = time(18, 0)    # 18:00

# Рабочие дни (0=Пн, 4=Пт)
WORKDAYS = (0, 1, 2, 3, 4)

# Пауза между звонками (секунды)
MIN_COOLDOWN_SEC = 30
MAX_COOLDOWN_SEC = 60

# Дневной лимит звонков
DAILY_LIMIT = 50


def is_business_hours() -> bool:
    """Сейчас рабочее время? (Пн-Пт, 09:00-18:00)"""
    now = datetime.now()
    if now.weekday() not in WORKDAYS:
        return False
    return WORK_START <= now.time() < WORK_END


def minutes_until_open() -> int:
    """Сколько минут до открытия (0 если уже рабочее время)."""
    if is_business_hours():
        return 0
    now = datetime.now()
    # Если после 18:00 — до завтра 9:00
    if now.time() >= WORK_END:
        tomorrow = now.replace(hour=9, minute=0, second=0, microsecond=0)
        tomorrow = tomorrow.replace(day=now.day + 1)
        return int((tomorrow - now).total_seconds() / 60)
    # Если до 9:00 — до сегодня 9:00
    today_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    return max(0, int((today_open - now).total_seconds() / 60))


def get_cooldown_sec() -> int:
    """Возвращает случайную паузу между звонками (30-60 сек)."""
    import random
    return random.randint(MIN_COOLDOWN_SEC, MAX_COOLDOWN_SEC)


def get_schedule_info() -> dict:
    """Информация о текущем расписании."""
    now = datetime.now()
    return {
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "is_business_hours": is_business_hours(),
        "work_start": WORK_START.strftime("%H:%M"),
        "work_end": WORK_END.strftime("%H:%M"),
        "workdays": "Пн-Пт",
        "daily_limit": DAILY_LIMIT,
        "cooldown_range": f"{MIN_COOLDOWN_SEC}-{MAX_COOLDOWN_SEC}s",
        "minutes_until_open": minutes_until_open(),
    }

"""
CallOrchestrator — оркестратор обзвана.
Берёт лидов из CallQueue, вызывает через Pipecat, обрабатывает результаты.
"""
import logging
import threading
import time
from datetime import datetime

from call_queue import get_next_lead, get_queue_stats, mark_called, update_lead_after_call
from pipecat_client import PipecatClient, CallResult

logger = logging.getLogger("call_orchestrator")

# Пауза между звонками (секунды)
CALL_COOLDOWN_SEC = 45

# Таймаут ожидания результата звонка (секунды)
CALL_TIMEOUT_SEC = 120

# Маппинг результатов Pipecat → наши статусы
RESULT_MAP = {
    "interested": "interested",
    "callback": "callback",
    "refused": "refused",
    "no_answer": "no_answer",
    "wrong_number": "wrong_number",
    "voicemail": "voicemail",
    "completed": "interested",
    "failed": "no_answer",
}


class CallOrchestrator:
    """Управляет процессом обзвана: очередь → звонок → результат."""

    def __init__(self, pipecat: PipecatClient | None = None):
        self.pipecat = pipecat or PipecatClient()
        self._running = False
        self._thread: threading.Thread | None = None
        self._paused = False
        self._current_lead: dict | None = None
        self._stats = {
            "calls_today": 0,
            "interested": 0,
            "callback": 0,
            "refused": 0,
            "no_answer": 0,
            "errors": 0,
            "started_at": None,
        }

    # ---- Control ----

    def start(self):
        """Запускает обзвон в отдельном потоке."""
        if self._running:
            logger.warning("Orchestrator already running")
            return
        self._running = True
        self._paused = False
        self._stats["started_at"] = datetime.now().isoformat()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("CallOrchestrator started")

    def stop(self):
        """Останавливает обзвон."""
        self._running = False
        self._paused = False
        logger.info("CallOrchestrator stopped")

    def pause(self):
        """Ставит обзвон на паузу (текущий звонок завершится)."""
        self._paused = True
        logger.info("CallOrchestrator paused")

    def resume(self):
        """Возобновляет обзвон после паузы."""
        self._paused = False
        logger.info("CallOrchestrator resumed")

    @property
    def is_running(self) -> bool:
        return self._running and not self._paused

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def current_lead(self) -> dict | None:
        return self._current_lead

    def get_status(self) -> dict:
        """Возвращает текущий статус оркестратора."""
        queue_stats = get_queue_stats()
        return {
            "running": self._running,
            "paused": self._paused,
            "current_lead": self._current_lead,
            "stats": self._stats,
            "queue": queue_stats,
        }

    # ---- Main Loop ----

    def _run_loop(self):
        """Основной цикл обзвана."""
        logger.info("Call loop started")
        while self._running:
            if self._paused:
                time.sleep(2)
                continue

            # Проверяем дневной лимит
            queue_stats = get_queue_stats()
            if queue_stats["remaining"] <= 0:
                logger.info("Daily call limit reached (%s). Stopping.", queue_stats["daily_limit"])
                self._running = False
                break

            # Берём следующего лида
            lead = get_next_lead()
            if not lead:
                logger.info("Queue empty. Stopping.")
                self._running = False
                break

            # Звоним
            self._current_lead = lead
            self._process_lead(lead)
            self._current_lead = None

            # Пауза между звонками
            if self._running and not self._paused:
                logger.info("Cooldown %ds before next call...", CALL_COOLDOWN_SEC)
                time.sleep(CALL_COOLDOWN_SEC)

        logger.info("Call loop finished. Stats: %s", self._stats)

    def _process_lead(self, lead: dict):
        """Обрабатывает одного лида: звонит → ждёт → записывает результат."""
        lead_id = lead["id"]
        phone = lead.get("mobile") or lead.get("phone") or lead.get("whatsapp")
        company = lead.get("company_name", "")
        industry = lead.get("industry", "")

        if not phone:
            logger.warning("Lead %s has no phone, skipping", lead_id)
            mark_called(lead_id, "no_phone", call_type="skip")
            return

        logger.info("Calling lead %s (%s) at %s", lead_id, company, phone)

        # 1. Звоним
        result = self.pipecat.create_call(
            phone=phone,
            company_name=company,
            industry=industry,
            lead_id=lead_id,
        )

        if result.status == "error" or not result.call_id:
            logger.error("Failed to call lead %s: %s", lead_id, result.error)
            mark_called(lead_id, "error", call_type="autocall")
            self._stats["errors"] += 1
            return

        # 2. Ждём результат
        call_data = self._wait_for_result(result.call_id)
        call_result = self._extract_result(call_data)
        duration = call_data.get("duration_sec", 0) if call_data else 0
        transcript = call_data.get("transcript", "") if call_data else ""

        # 3. Записываем
        mark_called(lead_id, call_result, duration_sec=duration,
                    transcript=transcript, call_type="autocall")
        update_lead_after_call(lead_id, call_result)

        self._stats["calls_today"] += 1
        if call_result in self._stats:
            self._stats[call_result] += 1

        logger.info("Lead %s result: %s (dur=%ds)", lead_id, call_result, duration)

    def _wait_for_result(self, call_id: str) -> dict | None:
        """Ждёт завершения звонка, polling каждые 5 сек."""
        deadline = time.time() + CALL_TIMEOUT_SEC
        while time.time() < deadline:
            data = self.pipecat.get_call(call_id)
            if not data or "error" in data:
                time.sleep(5)
                continue
            status = data.get("status", "")
            if status in ("completed", "failed", "no_answer", "timeout"):
                return data
            time.sleep(5)
        logger.warning("Call %s timed out after %ds", call_id, CALL_TIMEOUT_SEC)
        return None

    def _extract_result(self, call_data: dict | None) -> str:
        """Извлекает результат из данных звонка."""
        if not call_data:
            return "no_answer"
        raw = call_data.get("result") or call_data.get("status") or "unknown"
        return RESULT_MAP.get(raw, "no_answer")

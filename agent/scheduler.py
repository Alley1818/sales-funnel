"""
FollowUpScheduler — automated follow-up reminders.

Checks scheduled_actions table and sends reminders when due.
Runs as a background thread in Flask.
"""
import json
import os
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agent.memory import VectorMemory
from agent.context import ContextManager
from agent.tools import AgentTools

logger = logging.getLogger("agent.scheduler")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", str(Path(__file__).parent.parent / "config.json")))


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


class FollowUpScheduler:
    """Automated follow-up scheduler."""

    def __init__(self, memory: VectorMemory = None):
        self.memory = memory or VectorMemory()
        self.context = ContextManager(self.memory)
        self.tools = AgentTools(self.memory, self.context)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the scheduler in background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("FollowUp scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("FollowUp scheduler stopped")

    def _run_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                self._check_scheduled_actions()
                self._check_stale_leads()
            except Exception as e:
                logger.error("Scheduler error: %s", e)

            # Sleep for 30 minutes
            time.sleep(30 * 60)

    def _check_scheduled_actions(self):
        """Check and execute scheduled actions."""
        from db_conn import get_conn

        conn = get_conn()

        # Create table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                executed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        # Get pending actions that are due
        now = datetime.now().isoformat()
        rows = conn.execute("""
            SELECT * FROM scheduled_actions
            WHERE status = 'pending' AND scheduled_at <= ?
            ORDER BY scheduled_at
        """, (now,)).fetchall()

        for row in rows:
            action = dict(row)
            self._execute_action(action)

    def _execute_action(self, action: dict):
        """Execute a scheduled action."""
        from db_conn import get_conn

        lead_id = action["lead_id"]
        action_type = action["action_type"]

        logger.info("Executing scheduled action: %s for lead %d", action_type, lead_id)

        # Get lead data
        conn = get_conn()
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            self._mark_action_done(action["id"], "lead_not_found")
            return

        lead_data = dict(lead)

        # Check if lead is still active
        if lead_data.get("status") in ["closed", "lost"]:
            self._mark_action_done(action["id"], "lead_closed")
            return

        # Execute based on type
        if action_type == "followup":
            result = self._send_followup(lead_id, lead_data, action)
        elif action_type == "reminder":
            result = self._send_reminder(lead_id, lead_data, action)
        elif action_type == "escalation":
            result = self._escalate(lead_id, lead_data, action)
        else:
            result = {"success": False, "error": f"Unknown action type: {action_type}"}

        # Mark as done
        status = "done" if result.get("success") else "failed"
        self._mark_action_done(action["id"], status)

    def _send_followup(self, lead_id: int, lead_data: dict, action: dict) -> dict:
        """Send a follow-up message."""
        from whatsapp_client import WhatsAppClient

        phone = lead_data.get("mobile") or lead_data.get("whatsapp", "")
        if not phone:
            return {"success": False, "error": "No phone number"}

        # Get follow-up strategy
        strategy = self.context.get_followup_strategy(lead_id, lead_data)
        message = strategy.get("message", "")

        if not message:
            message = "Здравствуйте! Хотели бы продолжить общение?"

        # Send message
        wa = WhatsAppClient()
        result = wa.send_text(phone, message)

        if result.success:
            # Record in context
            self.context.record_message(lead_id, "assistant", message)
            self.context.update_context(lead_id, {
                "last_followup_sent": datetime.now().isoformat(),
                "followup_count": lead_data.get("followup_count", 0) + 1,
            })
            logger.info("Sent followup to lead %d", lead_id)
            return {"success": True}
        else:
            return {"success": False, "error": result.error}

    def _send_reminder(self, lead_id: int, lead_data: dict, action: dict) -> dict:
        """Send a reminder about KP/presentation."""
        from whatsapp_client import WhatsAppClient

        phone = lead_data.get("mobile") or lead_data.get("whatsapp", "")
        if not phone:
            return {"success": False, "error": "No phone number"}

        reason = action.get("reason", "")

        if "kp" in reason.lower():
            message = "Здравствуйте! Вы получили наше коммерческое предложение? Есть ли вопросы?"
        elif "presentation" in reason.lower():
            message = "Добрый день! Посмотрели нашу презентацию? Готовы обсудить?"
        else:
            message = "Здравствуйте! Хотели бы продолжить общение?"

        wa = WhatsAppClient()
        result = wa.send_text(phone, message)

        if result.success:
            self.context.record_message(lead_id, "assistant", message)
            logger.info("Sent reminder to lead %d", lead_id)
            return {"success": True}
        else:
            return {"success": False, "error": result.error}

    def _escalate(self, lead_id: int, lead_data: dict, action: dict) -> dict:
        """Escalate to manager."""
        reason = action.get("reason", "No response after follow-ups")
        return self.tools.escalate_manager(lead_id, reason)

    def _check_stale_leads(self):
        """Check for leads that need attention."""
        from db_conn import get_conn

        conn = get_conn()

        # Get active leads
        rows = conn.execute("""
            SELECT * FROM leads
            WHERE status IN ('interested', 'negotiating', 'called')
            AND updated_at < datetime('now', '-2 days')
        """).fetchall()

        for row in rows:
            lead_data = dict(row)
            lead_id = lead_data["id"]

            # Check if follow-up already scheduled
            existing = conn.execute("""
                SELECT * FROM scheduled_actions
                WHERE lead_id = ? AND status = 'pending'
            """, (lead_id,)).fetchone()

            if not existing:
                # Schedule follow-up
                self.tools.schedule_followup(lead_id, days=0, reason="stale_lead")
                logger.info("Scheduled stale lead followup: %d", lead_id)

    def _mark_action_done(self, action_id: int, status: str):
        """Mark an action as done."""
        from db_conn import get_conn

        conn = get_conn()
        conn.execute("""
            UPDATE scheduled_actions
            SET status = ?, executed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, action_id))
        conn.commit()

    def get_pending_actions(self) -> list:
        """Get all pending scheduled actions."""
        from db_conn import get_conn

        conn = get_conn()

        # Ensure table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                executed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        rows = conn.execute("""
            SELECT sa.*, l.company_name, l.mobile
            FROM scheduled_actions sa
            JOIN leads l ON sa.lead_id = l.id
            WHERE sa.status = 'pending'
            ORDER BY sa.scheduled_at
        """).fetchall()

        return [dict(r) for r in rows]

    def cancel_action(self, action_id: int) -> bool:
        """Cancel a pending action."""
        from db_conn import get_conn

        conn = get_conn()
        conn.execute("""
            UPDATE scheduled_actions
            SET status = 'cancelled'
            WHERE id = ? AND status = 'pending'
        """, (action_id,))
        conn.commit()
        return True

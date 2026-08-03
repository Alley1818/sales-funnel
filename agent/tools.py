"""
AgentTools — tools available to the AI agent.

Tools:
- send_kp: Send commercial proposal by industry
- send_presentation: Send presentation
- update_status: Update lead status
- schedule_followup: Schedule a follow-up reminder
- escalate_manager: Escalate to human manager
- get_lead_info: Get lead information
"""
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agent.memory import VectorMemory
from agent.context import ContextManager

logger = logging.getLogger("agent.tools")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", str(Path(__file__).parent.parent / "config.json")))


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


class AgentTools:
    """Tools available to the AI agent."""

    def __init__(self, memory: VectorMemory = None, context_mgr: ContextManager = None):
        self.memory = memory or VectorMemory()
        self.context = context_mgr or ContextManager(self.memory)

    def send_kp(self, lead_id: int, industry: str, phone: str) -> dict:
        """Send commercial proposal based on industry."""
        from whatsapp_client import WhatsAppClient
        from db_conn import get_conn

        # Find KP for this industry
        kp_docs = self.memory.get_knowledge_by_industry(industry, doc_type="kp")
        if not kp_docs:
            # Try generic KP
            kp_docs = self.memory.get_knowledge_by_industry("general", doc_type="kp")

        if not kp_docs:
            return {"success": False, "error": "No KP found for this industry"}

        kp = kp_docs[0]
        kp_content = kp["content"]
        kp_filename = kp["metadata"].get("filename", "КП.pdf")
        kp_file_path = kp["metadata"].get("file_path")

        # Send via WhatsApp
        wa = WhatsAppClient()

        if kp_file_path and Path(kp_file_path).exists():
            # Send as document
            result = wa.send_document(phone, kp_file_path, kp_filename, caption="Коммерческое предложение")
        else:
            # Send as text
            result = wa.send_text(phone, kp_content[:4000])

        if result.success:
            # Record in context
            self.context.update_context(lead_id, {
                "last_kp_sent": datetime.now().isoformat(),
                "kp_industry": industry,
            })
            self.context.record_message(lead_id, "assistant", f"Отправлено КП: {kp_filename}")

            # Schedule follow-up
            self._schedule_followup(lead_id, days=2, reason="kp_sent")

            logger.info("Sent KP to lead %d (%s)", lead_id, industry)
            return {"success": True, "kp_name": kp_filename}
        else:
            return {"success": False, "error": result.error}

    def send_presentation(self, lead_id: int, industry: str, phone: str) -> dict:
        """Send presentation based on industry."""
        from whatsapp_client import WhatsAppClient

        # Find presentation
        pres_docs = self.memory.get_knowledge_by_industry(industry, doc_type="presentation")
        if not pres_docs:
            pres_docs = self.memory.get_knowledge_by_industry("general", doc_type="presentation")

        if not pres_docs:
            return {"success": False, "error": "No presentation found for this industry"}

        pres = pres_docs[0]
        pres_content = pres["content"]
        pres_filename = pres["metadata"].get("filename", "Презентация.pdf")
        pres_file_path = pres["metadata"].get("file_path")

        wa = WhatsAppClient()

        if pres_file_path and Path(pres_file_path).exists():
            result = wa.send_document(phone, pres_file_path, pres_filename, caption="Презентация Technomax")
        else:
            result = wa.send_text(phone, pres_content[:4000])

        if result.success:
            self.context.update_context(lead_id, {
                "last_presentation_sent": datetime.now().isoformat(),
            })
            self.context.record_message(lead_id, "assistant", f"Отправлена презентация: {pres_filename}")
            logger.info("Sent presentation to lead %d", lead_id)
            return {"success": True, "presentation_name": pres_filename}
        else:
            return {"success": False, "error": result.error}

    def update_status(self, lead_id: int, new_status: str) -> dict:
        """Update lead status."""
        from db_conn import get_conn
        from agent_sync import log_status_change

        valid_statuses = ["new", "called", "interested", "negotiating", "closed", "lost"]
        if new_status not in valid_statuses:
            return {"success": False, "error": f"Invalid status. Valid: {valid_statuses}"}

        conn = get_conn()
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return {"success": False, "error": "Lead not found"}

        old_status = lead["status"]
        conn.execute(
            "UPDATE leads SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, lead_id),
        )
        conn.commit()

        # Log change
        log_status_change(lead_id, old_status, new_status, "Updated by AI agent")

        # Update context
        self.context.update_context(lead_id, {"stage": new_status})

        logger.info("Lead %d status: %s → %s", lead_id, old_status, new_status)
        return {"success": True, "old_status": old_status, "new_status": new_status}

    def schedule_followup(self, lead_id: int, days: int, reason: str = "") -> dict:
        """Schedule a follow-up reminder."""
        return self._schedule_followup(lead_id, days, reason)

    def escalate_manager(self, lead_id: int, reason: str) -> dict:
        """Escalate to human manager via Telegram."""
        from db_conn import get_conn
        from telegram_notifier import _send_telegram_sync

        conn = get_conn()
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        company = dict(lead).get("company_name", "Unknown") if lead else "Unknown"

        # Send Telegram notification
        message = (
            f"<b>🔔 Эскалация от AI агента</b>\n"
            f"<b>Компания:</b> {company}\n"
            f"<b>Lead ID:</b> {lead_id}\n"
            f"<b>Причина:</b> {reason}"
        )
        ok = _send_telegram_sync(message)

        # Update context
        self.context.update_context(lead_id, {
            "escalated": True,
            "escalation_reason": reason,
            "escalation_time": datetime.now().isoformat(),
        })

        logger.info("Escalated lead %d to manager: %s", lead_id, reason)
        return {"success": ok, "reason": reason}

    def get_lead_info(self, lead_id: int) -> dict:
        """Get comprehensive lead information."""
        from db_conn import get_conn

        conn = get_conn()
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return {"success": False, "error": "Lead not found"}

        lead_data = dict(lead)
        context = self.context.get_full_context(lead_id, lead_data)

        return {
            "success": True,
            "lead": lead_data,
            "context": context,
        }

    def _schedule_followup(self, lead_id: int, days: int, reason: str = "") -> dict:
        """Internal: schedule a follow-up in the database."""
        from db_conn import get_conn

        scheduled_at = datetime.now() + timedelta(days=days)

        conn = get_conn()
        conn.execute("""
            INSERT INTO scheduled_actions (lead_id, action_type, scheduled_at, reason, status)
            VALUES (?, 'followup', ?, ?, 'pending')
        """, (lead_id, scheduled_at.isoformat(), reason))
        conn.commit()

        logger.info("Scheduled followup for lead %d in %d days (%s)", lead_id, days, reason)
        return {"success": True, "scheduled_at": scheduled_at.isoformat()}

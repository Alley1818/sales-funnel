"""
ContextManager — manages lead context and conversation state.

Tracks:
- Lead profile (company, industry, contact)
- Conversation stage (new → called → interested → negotiating → closed)
- Recent messages
- Sent materials (KP, presentations)
- Scheduled actions (follow-ups, reminders)
"""
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agent.memory import VectorMemory

logger = logging.getLogger("agent.context")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", str(Path(__file__).parent.parent / "config.json")))


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


# Lead stages
STAGES = {
    "new": "Новый лид",
    "called": "Звонок совершен",
    "interested": "Заинтересован",
    "negotiating": "Переговоры",
    "closed": "Закрыт (успех)",
    "lost": "Закрыт (потерян)",
}

# Stage transitions
STAGE_ACTIONS = {
    "new": ["call", "send_intro"],
    "called": ["send_kp", "schedule_followup"],
    "interested": ["send_kp", "send_presentation", "schedule_meeting"],
    "negotiating": ["send_proposal", "escalate_manager", "schedule_followup"],
    "closed": [],
    "lost": [],
}


class ContextManager:
    """Manages lead context and conversation state."""

    def __init__(self, memory: VectorMemory = None):
        self.memory = memory or VectorMemory()

    def get_full_context(self, lead_id: int, lead_data: dict) -> dict:
        """Get full context for a lead including history and materials."""
        # Get vector context
        vec_ctx = self.memory.get_lead_context(lead_id)

        # Get recent messages
        recent = self.memory.get_recent_messages(lead_id, limit=20)

        # Build context
        context = {
            "lead_id": lead_id,
            "company": lead_data.get("company_name", ""),
            "industry": lead_data.get("industry", ""),
            "stage": lead_data.get("status", "new"),
            "phone": lead_data.get("mobile") or lead_data.get("whatsapp", ""),
            "email": lead_data.get("email", ""),
            "interest_level": self._calculate_interest(lead_data),
            "needs": self._extract_needs(vec_ctx),
            "objections": self._extract_objections(vec_ctx),
            "recent_messages": recent,
            "sent_materials": self._get_sent_materials(lead_id),
            "last_contact": self._get_last_contact(lead_data),
            "days_since_contact": self._days_since_contact(lead_data),
        }

        return context

    def update_context(self, lead_id: int, updates: dict):
        """Update lead context in vector DB."""
        # Get existing context
        existing = self.memory.get_lead_context(lead_id)
        if existing:
            # Merge with existing
            meta = existing.get("metadata", {})
            meta.update(updates)
        else:
            meta = updates

        # Save to vector DB
        self.memory.save_lead_context(lead_id, meta)
        logger.info("Updated context for lead %d: %s", lead_id, list(updates.keys()))

    def record_message(self, lead_id: int, role: str, content: str, metadata: dict = None):
        """Record a message in conversation history."""
        self.memory.save_message(lead_id, role, content, metadata)

    def get_conversation_summary(self, lead_id: int) -> str:
        """Get a summary of the conversation for LLM context."""
        messages = self.memory.get_recent_messages(lead_id, limit=10)
        if not messages:
            return "Нет истории разговора."

        lines = []
        for msg in messages:
            role = msg["metadata"].get("role", "unknown")
            content = msg["content"][:200]
            if role == "user":
                lines.append(f"Клиент: {content}")
            elif role == "assistant":
                lines.append(f"Агент: {content}")
        return "\n".join(lines)

    def should_send_followup(self, lead_id: int, lead_data: dict) -> bool:
        """Check if a follow-up should be sent."""
        stage = lead_data.get("status", "new")
        days = self._days_since_contact(lead_data)

        # Get follow-up rules from config
        config = _load_config()
        rules = config.get("followup_rules", {})

        if stage == "interested" and days >= rules.get("interested_days", 2):
            return True
        if stage == "negotiating" and days >= rules.get("negotiating_days", 3):
            return True
        if stage == "called" and days >= rules.get("called_days", 5):
            return True

        return False

    def get_followup_strategy(self, lead_id: int, lead_data: dict) -> dict:
        """Get the appropriate follow-up strategy."""
        stage = lead_data.get("status", "new")
        config = _load_config()
        rules = config.get("followup_rules", {})

        strategies = {
            "interested": {
                "action": "send_reminder",
                "message": rules.get("interested_message", "Здравствуйте! Хотели бы обсудить наше предложение?"),
                "escalate_after_days": rules.get("interested_escalate_days", 5),
            },
            "negotiating": {
                "action": "send_followup",
                "message": rules.get("negotiating_message", "Добрый день! Есть ли вопросы по нашему предложению?"),
                "escalate_after_days": rules.get("negotiating_escalate_days", 7),
            },
            "called": {
                "action": "send_reengagement",
                "message": rules.get("called_message", "Здравствуйте! Мы готовы обсудить как можем помочь вашему бизнесу."),
                "escalate_after_days": rules.get("called_escalate_days", 10),
            },
        }

        return strategies.get(stage, {"action": "none", "message": "", "escalate_after_days": 999})

    def _calculate_interest(self, lead_data: dict) -> int:
        """Calculate interest level (0-10) based on status and history."""
        stage = lead_data.get("status", "new")
        stage_scores = {
            "new": 1,
            "called": 3,
            "interested": 7,
            "negotiating": 8,
            "closed": 10,
            "lost": 0,
        }
        return stage_scores.get(stage, 1)

    def _extract_needs(self, vec_ctx: dict) -> list:
        """Extract needs from vector context."""
        if not vec_ctx:
            return []
        doc = vec_ctx.get("document", "")
        # Simple extraction - can be enhanced with LLM
        needs = []
        for line in doc.split("\n"):
            if "потребност" in line.lower() or "нужд" in line.lower():
                needs.append(line.strip())
        return needs

    def _extract_objections(self, vec_ctx: dict) -> list:
        """Extract objections from vector context."""
        if not vec_ctx:
            return []
        doc = vec_ctx.get("document", "")
        objections = []
        for line in doc.split("\n"):
            if "возражен" in line.lower() or "отказ" in line.lower():
                objections.append(line.strip())
        return objections

    def _get_sent_materials(self, lead_id: int) -> list:
        """Get list of materials sent to this lead."""
        # Query from knowledge base with lead_id filter
        # For now, return empty - will be enhanced
        return []

    def _get_last_contact(self, lead_data: dict) -> str:
        """Get last contact timestamp."""
        return lead_data.get("updated_at", "")

    def _days_since_contact(self, lead_data: dict) -> int:
        """Calculate days since last contact."""
        last_contact = lead_data.get("updated_at")
        if not last_contact:
            return 999

        try:
            if isinstance(last_contact, str):
                last_dt = datetime.fromisoformat(last_contact.replace("Z", "+00:00"))
            else:
                last_dt = last_contact
            return (datetime.now() - last_dt).days
        except Exception:
            return 999

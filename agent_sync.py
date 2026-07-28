"""
Unified AI Agent — syncs voice calls and WhatsApp conversations per lead.
Single source of truth: each lead has a conversation history across all channels.
"""
import sqlite3
import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("agent_sync")

DB_PATH = Path(__file__).parent / "leads.db"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_sync_tables():
    """Create conversation tracking tables."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            channel TEXT NOT NULL,  -- 'voice', 'whatsapp', 'email'
            direction TEXT NOT NULL,  -- 'inbound', 'outbound'
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS lead_context (
            lead_id INTEGER PRIMARY KEY REFERENCES leads(id),
            stage TEXT DEFAULT 'new',  -- new, called, interested, negotiating, closed, lost
            interest_level INTEGER DEFAULT 0,  -- 0-10
            objections TEXT DEFAULT '[]',
            needs TEXT DEFAULT '[]',
            next_action TEXT DEFAULT '',
            last_channel TEXT DEFAULT '',
            last_contact_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_conv_lead ON conversations(lead_id);
        CREATE INDEX IF NOT EXISTS idx_conv_channel ON conversations(channel);
    """)
    conn.commit()
    conn.close()


# ---- Conversation Logging ----

def log_message(lead_id: int, channel: str, direction: str, content: str, metadata: dict = None):
    """Log a message from any channel."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO conversations (lead_id, channel, direction, content, metadata) VALUES (?,?,?,?,?)",
        (lead_id, channel, direction, content, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    # Update lead_context
    conn.execute("""
        INSERT INTO lead_context (lead_id, last_channel, last_contact_at, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(lead_id) DO UPDATE SET
            last_channel = excluded.last_channel,
            last_contact_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
    """, (lead_id, channel))
    conn.commit()
    conn.close()


def get_conversation_history(lead_id: int, limit: int = 20) -> list[dict]:
    """Get recent conversation history for a lead across all channels."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE lead_id = ? ORDER BY created_at DESC LIMIT ?",
        (lead_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_lead_context(lead_id: int) -> dict:
    """Get lead's current context (stage, interests, objections)."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM lead_context WHERE lead_id = ?", (lead_id,)).fetchone()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()

    ctx = dict(row) if row else {}
    if lead:
        ctx["company_name"] = lead["company_name"]
        ctx["industry"] = lead.get("industry", "")
        ctx["phone"] = lead.get("mobile", "")
        ctx["email"] = lead.get("email", "")
        ctx["status"] = lead.get("status", "")
    return ctx


def update_lead_context(lead_id: int, **kwargs):
    """Update lead context fields (stage, interest_level, objections, needs, next_action)."""
    conn = get_conn()
    allowed = {"stage", "interest_level", "objections", "needs", "next_action"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return

    # Ensure row exists
    conn.execute(
        "INSERT OR IGNORE INTO lead_context (lead_id) VALUES (?)", (lead_id,)
    )

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [lead_id]
    conn.execute(
        f"UPDATE lead_context SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ?",
        values,
    )
    conn.commit()
    conn.close()


# ---- AI Agent Prompt Builder ----

def build_agent_prompt(lead_id: int, channel: str = "voice") -> str:
    """
    Build a context-aware prompt for the AI agent.
    Includes conversation history from ALL channels.
    """
    ctx = get_lead_context(lead_id)
    history = get_conversation_history(lead_id)

    company = ctx.get("company_name", "клиент")
    industry = ctx.get("industry", "")
    stage = ctx.get("stage", "new")
    interest = ctx.get("interest_level", 0)
    objections = ctx.get("objections", "[]")
    needs = ctx.get("needs", "[]")

    # Build history text
    history_text = ""
    if history:
        history_text = "\nИСТОРИЯ ОБЩЕНИЯ:\n"
        for msg in history:
            ch = msg["channel"]
            direction = "Клиент" if msg["direction"] == "inbound" else "Агент"
            history_text += f"[{ch}] {direction}: {msg['content'][:200]}\n"

    # Channel-specific instructions
    channel_instructions = ""
    if channel == "voice":
        channel_instructions = """
Вы звоните по телефону. Говорите кратко, ясно, по делу.
Не повторяйтесь. Завершите разговор за 1-2 минуты.
Если клиент заинтересован — скажите что отправите информацию в WhatsApp.
"""
    elif channel == "whatsapp":
        channel_instructions = """
Вы пишете в WhatsApp. Используйте короткие сообщения.
Можно использовать эмодзи умеренно. Отвечайте быстро.
Если клиент просит КП — отправьте. Если звонок нужен — запланируйте.
"""

    return f"""Вы — AI-ассистент компании Technomax.
Вы общаетесь с компанией "{company}" из отрасли "{industry}".

ТЕКУЩЕЕ СОСТОЯНИЕ:
- Стадия: {stage}
- Интерес клиента: {interest}/10
- Возражения: {objections}
- Потребности: {needs}
- Канал: {channel}

{channel_instructions}
{history_text}

ПРАВИЛА:
1. Учитывайте всю историю общения (звонки, WhatsApp, email)
2. Не задавайте вопросы, на которые уже есть ответы
3. Если клиент уже отказался — не давите
4. Если клиент заинтересован — предлагайте конкретные следующие шаги
5. Всегда записывайте результат разговора

Отвечайте кратко и по делу."""


# ---- Cross-Channel Sync ----

def sync_after_call(lead_id: int, result: str, transcript: str):
    """After a voice call, sync to WhatsApp if needed."""
    log_message(lead_id, "voice", "outbound", transcript[:500], {"result": result})

    if result == "interested":
        update_lead_context(lead_id, stage="interested", interest_level=7, next_action="send_whatsapp_kp")
    elif result == "callback":
        update_lead_context(lead_id, stage="negotiating", interest_level=4, next_action="schedule_callback")
    elif result == "refused":
        update_lead_context(lead_id, stage="lost", interest_level=0)
    elif result == "no_answer":
        update_lead_context(lead_id, next_action="whatsapp_followup")


def sync_after_whatsapp(lead_id: int, message: str, is_inbound: bool = False):
    """After a WhatsApp message, update context."""
    direction = "inbound" if is_inbound else "outbound"
    log_message(lead_id, "whatsapp", direction, message[:500])

    if is_inbound:
        # Analyze message for interest signals
        lower = message.lower()
        if any(w in lower for w in ["interested", "интересно", "давайте", "отправьте", "kp", "кп"]):
            update_lead_context(lead_id, stage="interested", interest_level=8)
        elif any(w in lower for w in ["отказ", "не надо", "не интересно", "stop"]):
            update_lead_context(lead_id, stage="lost", interest_level=0)


def get_next_action(lead_id: int) -> dict:
    """Determine what to do next for a lead."""
    ctx = get_lead_context(lead_id)
    stage = ctx.get("stage", "new")
    next_action = ctx.get("next_action", "")
    channel = ctx.get("last_channel", "")

    if stage == "new":
        return {"action": "call", "reason": "Новый лид, нужен первый звонок"}
    elif stage == "interested" and next_action == "send_whatsapp_kp":
        return {"action": "send_whatsapp", "reason": "Клиент заинтересован, отправить КП в WhatsApp"}
    elif stage == "interested" and channel == "voice":
        return {"action": "send_whatsapp", "reason": "После звонка отправить КП в WhatsApp"}
    elif stage == "negotiating" and next_action == "schedule_callback":
        return {"action": "call", "reason": "Запланирован повторный звонок"}
    elif stage == "lost":
        return {"action": "skip", "reason": "Клиент отказался"}
    else:
        return {"action": "wait", "reason": "Нет действий"}


# Initialize on import
init_sync_tables()

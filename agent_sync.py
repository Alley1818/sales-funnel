"""
Unified AI Agent — syncs voice calls and WhatsApp conversations per lead.
Single source of truth: each lead has a conversation history across all channels.
"""
import json
import logging
import time
from datetime import datetime

from db_conn import get_conn

logger = logging.getLogger("agent_sync")


def init_sync_tables():
    """Create conversation tracking tables."""
    conn = get_conn()
    # Migrate existing table first (add event_type if missing)
    try:
        conn.execute("SELECT event_type FROM conversations LIMIT 1")
    except Exception:
        # Either table doesn't exist or column is missing
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN event_type TEXT DEFAULT 'message'")
            logger.info("Added event_type column to conversations")
        except Exception:
            pass  # Table doesn't exist yet — CREATE TABLE below will handle it
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            channel TEXT NOT NULL,  -- 'voice', 'whatsapp', 'email', 'system'
            direction TEXT NOT NULL,  -- 'inbound', 'outbound', 'event'
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            event_type TEXT DEFAULT 'message',
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
        CREATE INDEX IF NOT EXISTS idx_conv_event ON conversations(event_type);
        CREATE INDEX IF NOT EXISTS idx_conv_created ON conversations(created_at);

        CREATE TABLE IF NOT EXISTS scheduled_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            executed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_scheduled_lead ON scheduled_actions(lead_id);
        CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_actions(status);
        CREATE INDEX IF NOT EXISTS idx_scheduled_at ON scheduled_actions(scheduled_at);
    """)
    conn.commit()


# ---- Conversation Logging ----

def log_message(lead_id: int, channel: str, direction: str, content: str,
                metadata: dict = None, event_type: str = "message"):
    """Log a message from any channel."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO conversations (lead_id, channel, direction, content, metadata, event_type) VALUES (?,?,?,?,?,?)",
        (lead_id, channel, direction, content,
         json.dumps(metadata or {}, ensure_ascii=False), event_type),
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


def get_conversation_history(lead_id: int, limit: int = 20) -> list[dict]:
    """Get recent conversation history for a lead across all channels."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE lead_id = ? ORDER BY created_at DESC LIMIT ?",
        (lead_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_lead_context(lead_id: int) -> dict:
    """Get lead's current context (stage, interests, objections)."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM lead_context WHERE lead_id = ?", (lead_id,)).fetchone()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

    ctx = dict(row) if row else {}
    if lead:
        lead = dict(lead)
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


# ---- AI Agent Prompt Builder ----

# Objection handling playbook
OBJECTION_PLAYBOOK = {
    "дорого": "Объясните ценность ROI. Сравните с текущими затратами на ручной труд. Предложите pilot-проект.",
    "не нужно": "Уточните текущие процессы. Покажите, как AI экономит время. Предложите бесплатную демонстрацию.",
    "нет времени": "Предложите короткий звонок 5 минут. Отправьте КП в WhatsApp для ознакомления в удобное время.",
    "нет бюджета": "Предложите гибкую тарификацию. Объясните окупаемость. Предложите начать с пилотного проекта.",
    "не работаем с AI": "Приведите примеры конкурентов из той же отрасли. Объясните простоту внедрения.",
    "уже есть решение": "Уточните, что устраивает, а что нет. Покажите дополнительные возможности Technomax.",
    "подумаю": "Предложите отправить подробное КП. Запланируйте повторный звонок через 3 дня.",
}

# Interest-based strategy
STRATEGY_EDUCATE = """СТРАТЕГИЯ: Обучение (низкий интерес)
- Расскажите о возможностях AI в отрасли клиента
- Приведите конкретные примеры и кейсы
- Не давите на продажу — формируйте осведомлённость
- Предложите отправить обзорную информацию"""

STRATEGY_NURTURE = """СТРАТЕГИЯ: Развитие интереса (средний интерес)
- Сфокусируйтесь на конкретных болевых точках клиента
- Покажите, как Technomax решает именно их проблемы
- Предложите бесплатную демонстрацию
- Отправьте КП с кейсами из отрасли"""

STRATEGY_CLOSE = """СТРАТЕГИЯ: Закрытие (высокий интерес)
- Предложите конкретные следующие шаги (демо, пилот, договор)
- Создайте срочность (ограниченные условия, слоты)
- Запланируйте встречу или звонок с менеджером
- Отправьте КП немедленно"""


def build_agent_prompt(lead_id: int, channel: str = "voice") -> str:
    """
    Build a context-aware prompt for the AI agent.
    Includes: conversation history, RAG context, sentiment, objection playbook, interest strategy.
    """
    ctx = get_lead_context(lead_id)
    history = get_conversation_history(lead_id)

    company = ctx.get("company_name", "клиент")
    industry = ctx.get("industry", "")
    stage = ctx.get("stage", "new")
    interest = ctx.get("interest_level", 0)
    objections = ctx.get("objections", "[]")
    needs = ctx.get("needs", "[]")

    # --- RAG context ---
    from advanced_features import get_rag_context
    rag_query = f"{company} {industry}"
    rag_context = get_rag_context(rag_query, industry)

    # --- Sentiment overlay ---
    sentiment_instruction = ""
    if history:
        from advanced_features import analyze_sentiment
        last_msgs = [m for m in history if m["direction"] == "inbound"]
        if last_msgs:
            last_sentiment = analyze_sentiment(last_msgs[-1]["content"])
            if last_sentiment["sentiment"] == "angry":
                sentiment_instruction = """
ВНИМАНИЕ: Клиент раздражён. Будьте максимально вежливы.
Извинитесь за беспокойство. Предложите отказаться от контакта если не интересно.
Не давите. Спокойный, уважительный тон."""
            elif last_sentiment["sentiment"] == "negative":
                sentiment_instruction = """
ВНИМАНИЕ: Клиент настроен скептически. Не давите.
Выслушайте возражения. Предложите альтернативы."""
            elif last_sentiment["sentiment"] == "positive":
                sentiment_instruction = """
Клиент настроен позитивно. Используйте момент для предложения конкретных шагов."""

    # --- Interest-based strategy ---
    if interest <= 3:
        strategy = STRATEGY_EDUCATE
    elif interest <= 6:
        strategy = STRATEGY_NURTURE
    else:
        strategy = STRATEGY_CLOSE

    # --- Build history text ---
    history_text = ""
    if history:
        history_text = "\nИСТОРИЯ ОБЩЕНИЯ:\n"
        for msg in history[-8:]:  # Last 8 messages only
            ch = msg["channel"]
            direction = "Клиент" if msg["direction"] == "inbound" else "Агент"
            history_text += f"[{ch}] {direction}: {msg['content'][:200]}\n"

    # --- Channel-specific instructions ---
    channel_instructions = ""
    if channel == "voice":
        channel_instructions = """
Вы звоните по телефону. Говорите кратко, ясно, по делу.
Не повторяйтесь. Завершите разговор за 1-2 минуты.
Если клиент заинтересован — скажите что отправите информацию в WhatsApp."""
    elif channel == "whatsapp":
        channel_instructions = """
Вы пишете в WhatsApp. Используйте короткие сообщения.
Отвечайте быстро. Если клиент просит КП — отправьте.
Если звонок нужен — запланируйте."""

    # --- Objection handling ---
    objection_text = ""
    if objections and objections != "[]":
        objection_text = "\nВОЗРАЖЕНИЯ КЛИЕНТА:\n"
        import json
        try:
            obj_list = json.loads(objections) if isinstance(objections, str) else objections
        except (json.JSONDecodeError, TypeError):
            obj_list = [str(objections)]
        for obj in obj_list:
            objection_text += f"- {obj}\n"
            for key, advice in OBJECTION_PLAYBOOK.items():
                if key in str(obj).lower():
                    objection_text += f"  Рекомендация: {advice}\n"

    return f"""Вы — AI-ассистент компании Technomax.
Вы общаетесь с компанией "{company}" из отрасли "{industry}".

ТЕКУЩЕЕ СОСТОЯНИЕ:
- Стадия: {stage}
- Интерес клиента: {interest}/10
- Потребности: {needs}
- Канал: {channel}

{strategy}

{channel_instructions}
{sentiment_instruction}
{objection_text}
{rag_context}
{history_text}

ПРАВИЛА:
1. Учитывайте всю историю общения (звонки, WhatsApp, email)
2. Не задавайте вопросы, на которые уже есть ответы
3. Если клиент уже отказался — не давите
4. Если клиент заинтересован — предлагайте конкретные следующие шаги
5. Всегда записывайте результат разговора
6. Используйте информацию из базы знаний для аргументов

Отвечайте кратко и по делу."""


# ---- Cross-Channel Sync ----

def sync_after_call(lead_id: int, result: str, transcript: str):
    """After a voice call, sync to WhatsApp if needed."""
    log_message(lead_id, "voice", "outbound", transcript[:500], {"result": result})

    # Extract entities from transcript
    extract_entities(lead_id, transcript)

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

    # Extract entities from inbound messages
    if is_inbound:
        extract_entities(lead_id, message)

    if is_inbound:
        # Analyze message for interest signals
        lower = message.lower()
        if any(w in lower for w in ["interested", "интересно", "давайте", "отправьте", "kp", "кп"]):
            update_lead_context(lead_id, stage="interested", interest_level=8)
        elif any(w in lower for w in ["отказ", "не надо", "не интересно", "stop"]):
            update_lead_context(lead_id, stage="lost", interest_level=0)

    # Auto-summarize if enough messages accumulated
    _maybe_summarize(lead_id)


# ---- Entity Extraction ----

ENTITY_PATTERNS = {
    "budget": [
        r"(?:бюджет|budget|стоимость|цена|price|тариф)\s*[:=]?\s*(\d[\d\s]*(?:тенге|KZT|₸|руб|USD|\$)?)",
        r"(\d[\d\s]*(?:тенге|KZT|₸|руб|USD|\$))\s*(?:в месяц|в год|мес|год)",
    ],
    "decision_maker": [
        r"(?:решает|директор|руководитель|CEO|CTO|owner|владелец|генеральный)\s*[:=]?\s*(\w+)",
        r"(?:имя|зовут|contact)\s*[:=]?\s*(\w+)",
    ],
    "timeline": [
        r"(?:срок|когда|дата|timeline|дедлайн|deadline)\s*[:=]?\s*(.+?)(?:\.|,|$)",
        r"(?:через|в течение|на следующей)\s+(.+?)(?:\.|,|$)",
    ],
    "pain_points": [
        r"(?:проблема|сложно|слышность|pain|issue)\s*[:=]?\s*(.+?)(?:\.|,|$)",
        r"(?:не хватает|нет|отсутствует)\s+(.+?)(?:\.|,|$)",
    ],
}


def extract_entities(lead_id: int, text: str):
    """Extract structured entities from conversation text."""
    import re
    text_lower = text.lower()

    updates = {}
    for entity, patterns in ENTITY_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                value = match.group(1).strip()
                if value and len(value) > 2:
                    updates[entity] = value
                    break

    if updates:
        # Merge with existing needs
        ctx = get_lead_context(lead_id)
        existing_needs = ctx.get("needs", "[]")
        import json
        try:
            needs_list = json.loads(existing_needs) if isinstance(existing_needs, str) else existing_needs
        except (json.JSONDecodeError, TypeError):
            needs_list = []

        for entity, value in updates.items():
            entry = f"{entity}: {value}"
            if entry not in needs_list:
                needs_list.append(entry)

        update_lead_context(lead_id, needs=json.dumps(needs_list, ensure_ascii=False))
        logger.info("Extracted entities for lead %d: %s", lead_id, updates)


# ---- Conversation Summarization ----

def _maybe_summarize(lead_id: int):
    """Summarize conversation if more than 10 unsent messages."""
    conn = get_conn()
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM conversations WHERE lead_id = ?",
        (lead_id,)
    ).fetchone()["cnt"]

    if count >= 10 and count % 10 == 0:
        summarize_conversation(lead_id)


def summarize_conversation(lead_id: int):
    """
    Generate a summary of conversation history.
    Uses keyword extraction (no LLM call needed).
    Stores summary as a special 'summary' message.
    """
    conn = get_conn()
    messages = conn.execute(
        "SELECT * FROM conversations WHERE lead_id = ? AND channel != 'summary' ORDER BY created_at",
        (lead_id,)
    ).fetchall()

    if len(messages) < 5:
        return

    # Simple extractive summary: pick key sentences
    inbound = [m["content"] for m in messages if m["direction"] == "inbound"]
    outbound = [m["content"] for m in messages if m["direction"] == "outbound"]

    summary_parts = []
    if inbound:
        summary_parts.append(f"Клиент: {inbound[-1][:100]}")
    if outbound:
        summary_parts.append(f"Агент: {outbound[-1][:100]}")

    channels = set(m["channel"] for m in messages)
    summary_parts.append(f"Каналы: {', '.join(channels)}")
    summary_parts.append(f"Всего сообщений: {len(messages)}")

    summary = " | ".join(summary_parts)

    # Store as summary message (replaces old messages in prompt)
    conn.execute(
        "INSERT INTO conversations (lead_id, channel, direction, content, metadata) VALUES (?, 'summary', 'system', ?, ?)",
        (lead_id, summary, json.dumps({"type": "auto_summary", "message_count": len(messages)}, ensure_ascii=False)),
    )
    conn.commit()
    logger.info("Auto-summarized %d messages for lead %d", len(messages), lead_id)


# ---- Memory Snapshot ----

def get_memory_snapshot(lead_id: int) -> str:
    """
    Get a compact memory snapshot for the agent:
    - Key entities (budget, decision maker, timeline, pain points)
    - Latest summary (if available)
    - Last 5 raw messages
    - Current context (stage, interest, next action)
    """
    ctx = get_lead_context(lead_id)
    conn = get_conn()

    # Key entities
    entities_text = ""
    needs = ctx.get("needs", "[]")
    import json
    try:
        needs_list = json.loads(needs) if isinstance(needs, str) else needs
    except (json.JSONDecodeError, TypeError):
        needs_list = []
    if needs_list:
        entities_text = "\nИЗВЛЕЧЁННЫЕ ДАННЫЕ:\n"
        for need in needs_list:
            entities_text += f"- {need}\n"

    # Latest summary
    summary_text = ""
    summary = conn.execute(
        "SELECT content FROM conversations WHERE lead_id = ? AND channel = 'summary' ORDER BY created_at DESC LIMIT 1",
        (lead_id,)
    ).fetchone()
    if summary:
        summary_text = f"\nКРАТКАЯ ИСТОРИЯ: {summary['content']}\n"

    # Last 5 raw messages
    recent = conn.execute(
        "SELECT * FROM conversations WHERE lead_id = ? AND channel != 'summary' ORDER BY created_at DESC LIMIT 5",
        (lead_id,)
    ).fetchall()
    recent_text = ""
    if recent:
        recent_text = "\nПОСЛЕДНИЕ СООБЩЕНИЯ:\n"
        for msg in reversed(recent):
            direction = "Клиент" if msg["direction"] == "inbound" else "Агент"
            recent_text += f"[{msg['channel']}] {direction}: {msg['content'][:150]}\n"

    return entities_text + summary_text + recent_text


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


# ---- Timeline / Event Logging ----

def log_event(lead_id: int, event_type: str, content: str,
              channel: str = "system", metadata: dict = None):
    """Log a system event to the unified timeline.

    event_type: status_change, kp_sent, followup, score_change, call_result, message
    """
    log_message(lead_id, channel, "event", content,
                metadata=metadata, event_type=event_type)


def log_status_change(lead_id: int, old_status: str, new_status: str, notes: str = ""):
    """Log a lead status change event."""
    content = f"Статус: {old_status} → {new_status}"
    if notes:
        content += f" ({notes})"
    log_event(lead_id, "status_change", content,
              metadata={"old_status": old_status, "new_status": new_status, "notes": notes})


def log_kp_sent(lead_id: int, channel: str, company_name: str = ""):
    """Log a КП (commercial proposal) send event."""
    log_event(lead_id, "kp_sent", f"КП отправлено через {channel}",
              channel=channel, metadata={"company": company_name})


def log_followup_event(lead_id: int, attempt: int, channel: str):
    """Log a follow-up attempt."""
    log_event(lead_id, "followup", f"Follow-up #{attempt} через {channel}",
              channel=channel, metadata={"attempt": attempt})


def log_score_change(lead_id: int, old_score: int, new_score: int, reason: str = ""):
    """Log a lead score change event."""
    content = f"Скор: {old_score} → {new_score}"
    if reason:
        content += f" ({reason})"
    log_event(lead_id, "score_change", content,
              metadata={"old_score": old_score, "new_score": new_score, "reason": reason})


def get_lead_timeline(lead_id: int, limit: int = 50, event_type: str = None) -> list[dict]:
    """Get unified timeline for a lead. Returns all events chronologically.

    Args:
        lead_id: Lead ID
        limit: Max events to return
        event_type: Filter by event type (None = all)
    """
    conn = get_conn()
    if event_type:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE lead_id = ? AND event_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (lead_id, event_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE lead_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (lead_id, limit),
        ).fetchall()
    events = []
    for r in reversed(rows):
        row = dict(r)
        # Parse metadata JSON
        try:
            row["metadata"] = json.loads(row.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            row["metadata"] = {}
        events.append(row)
    return events


# Initialize on import
init_sync_tables()

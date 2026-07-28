"""
Business logic for all sales funnel features.
Agents per industry, templates, DNC, scoring, campaigns, A/B tests, CPS.
"""
import sqlite3
import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("funnel_features")

DB_PATH = Path(__file__).parent / "leads.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ==================== AI AGENTS ====================

def create_agent(name: str, industry: str, prompt: str = "", **kwargs) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO ai_agents (name, industry, prompt, welcome_phrase, voice, llm_model, temperature, max_tokens)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, industry, prompt,
         kwargs.get("welcome_phrase", ""),
         kwargs.get("voice", "ru-RU-SvetlanaNeural"),
         kwargs.get("llm_model", "xiaomi/mimo-v2.5-pro"),
         kwargs.get("temperature", 0.3),
         kwargs.get("max_tokens", 300))
    )
    conn.commit()
    agent_id = cur.lastrowid
    conn.close()
    return agent_id


def get_agents() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM ai_agents ORDER BY industry").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent_by_industry(industry: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM ai_agents WHERE industry = ? AND enabled = 1", (industry,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_agent(agent_id: int, **kwargs) -> bool:
    conn = get_conn()
    allowed = {"name", "industry", "prompt", "welcome_phrase", "voice", "llm_model", "temperature", "max_tokens", "enabled", "technomax_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [agent_id]
    conn.execute(f"UPDATE ai_agents SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_agent(agent_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM ai_agents WHERE id = ?", (agent_id,))
    conn.commit()
    conn.close()
    return True


# ==================== MESSAGE TEMPLATES ====================

def create_template(name: str, industry: str, channel: str, body: str, **kwargs) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO message_templates (name, industry, channel, subject, body, is_default, ab_variant)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, industry, channel, kwargs.get("subject", ""), body,
         kwargs.get("is_default", 0), kwargs.get("ab_variant", ""))
    )
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def get_templates(industry: str = "", channel: str = "") -> list[dict]:
    conn = get_conn()
    q = "SELECT * FROM message_templates WHERE 1=1"
    params = []
    if industry:
        q += " AND (industry = ? OR industry = '')"
        params.append(industry)
    if channel:
        q += " AND channel = ?"
        params.append(channel)
    q += " ORDER BY is_default DESC, industry, name"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_template_for_lead(lead_id: int, channel: str) -> dict | None:
    """Get the best template for a lead based on industry and channel."""
    conn = get_conn()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        return None
    industry = lead.get("industry", "")
    # Try industry-specific first, then default
    row = conn.execute(
        """SELECT * FROM message_templates
           WHERE channel = ? AND (industry = ? OR industry = '') AND is_default = 1
           ORDER BY CASE WHEN industry = ? THEN 0 ELSE 1 END
           LIMIT 1""",
        (channel, industry, industry)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM message_templates WHERE channel = ? AND is_default = 1 LIMIT 1",
            (channel,)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_template(template_id: int, **kwargs) -> bool:
    conn = get_conn()
    allowed = {"name", "industry", "channel", "subject", "body", "is_default", "ab_variant"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [template_id]
    conn.execute(f"UPDATE message_templates SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_template(template_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM message_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return True


# ==================== DO NOT CALL ====================

def add_dnc(phone: str, reason: str = "") -> bool:
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO do_not_call (phone, reason) VALUES (?, ?)", (phone, reason))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def is_dnc(phone: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM do_not_call WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return row is not None


def get_dnc_list() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM do_not_call ORDER BY added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_dnc(phone: str) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM do_not_call WHERE phone = ?", (phone,))
    conn.commit()
    conn.close()
    return True


# ==================== LEAD SCORING ====================

def score_lead(lead_id: int, score: int, category: str, reasoning: str = ""):
    conn = get_conn()
    conn.execute("""
        INSERT INTO lead_scores (lead_id, score, category, reasoning, scored_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(lead_id) DO UPDATE SET
            score = excluded.score,
            category = excluded.category,
            reasoning = excluded.reasoning,
            scored_at = CURRENT_TIMESTAMP
    """, (lead_id, score, category, reasoning))
    conn.commit()
    conn.close()


def get_lead_score(lead_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM lead_scores WHERE lead_id = ?", (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_leads_by_score(category: str = "", min_score: int = 0) -> list[dict]:
    conn = get_conn()
    q = """SELECT l.*, s.score, s.category as score_category, s.reasoning
           FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id
           WHERE l.status = 'new'"""
    params = []
    if category:
        q += " AND s.category = ?"
        params.append(category)
    if min_score > 0:
        q += " AND COALESCE(s.score, 0) >= ?"
        params.append(min_score)
    q += " ORDER BY COALESCE(s.score, 0) DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ==================== CAMPAIGNS ====================

def create_campaign(name: str, **kwargs) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO campaigns (name, industry, channel, schedule_cron, schedule_time, max_calls, cps, agent_id, template_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, kwargs.get("industry", ""), kwargs.get("channel", "voice"),
         kwargs.get("schedule_cron", ""), kwargs.get("schedule_time", ""),
         kwargs.get("max_calls", 50), kwargs.get("cps", 1),
         kwargs.get("agent_id"), kwargs.get("template_id"))
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def get_campaigns() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM campaign_leads cl WHERE cl.campaign_id = c.id) as total_leads,
               (SELECT COUNT(*) FROM campaign_leads cl WHERE cl.campaign_id = c.id AND cl.status = 'completed') as completed_leads,
               (SELECT COUNT(*) FROM campaign_leads cl WHERE cl.campaign_id = c.id AND cl.status = 'pending') as pending_leads
        FROM campaigns c ORDER BY c.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_leads_to_campaign(campaign_id: int, lead_ids: list[int]) -> int:
    conn = get_conn()
    added = 0
    for lid in lead_ids:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO campaign_leads (campaign_id, lead_id) VALUES (?, ?)",
                (campaign_id, lid)
            )
            added += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return added


def get_campaign_leads(campaign_id: int, status: str = "") -> list[dict]:
    conn = get_conn()
    q = """SELECT cl.*, l.company_name, l.mobile, l.email, l.industry
           FROM campaign_leads cl JOIN leads l ON cl.lead_id = l.id
           WHERE cl.campaign_id = ?"""
    params = [campaign_id]
    if status:
        q += " AND cl.status = ?"
        params.append(status)
    q += " ORDER BY cl.id"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_campaign_lead_status(campaign_id: int, lead_id: int, status: str, result: str = ""):
    conn = get_conn()
    conn.execute(
        "UPDATE campaign_leads SET status = ?, result = ?, called_at = CURRENT_TIMESTAMP WHERE campaign_id = ? AND lead_id = ?",
        (status, result, campaign_id, lead_id)
    )
    conn.commit()
    conn.close()


def update_campaign_status(campaign_id: int, status: str):
    conn = get_conn()
    if status == "running":
        conn.execute("UPDATE campaigns SET status = ?, started_at = CURRENT_TIMESTAMP WHERE id = ?", (status, campaign_id))
    elif status in ("completed", "paused"):
        conn.execute("UPDATE campaigns SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?", (status, campaign_id))
    else:
        conn.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))
    conn.commit()
    conn.close()


def delete_campaign(campaign_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM campaign_leads WHERE campaign_id = ?", (campaign_id,))
    conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()


# ==================== A/B TESTING ====================

def create_ab_test(name: str, template_a_id: int, template_b_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id) VALUES (?, ?, ?)",
        (name, template_a_id, template_b_id)
    )
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def get_ab_tests() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT t.*,
               ta.name as template_a_name, ta.body as template_a_body,
               tb.name as template_b_name, tb.body as template_b_body
        FROM ab_tests t
        LEFT JOIN message_templates ta ON t.template_a_id = ta.id
        LEFT JOIN message_templates tb ON t.template_b_id = tb.id
        ORDER BY t.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def pick_ab_variant(test_id: int) -> str:
    """Pick A or B variant based on even distribution."""
    conn = get_conn()
    test = conn.execute("SELECT * FROM ab_tests WHERE id = ?", (test_id,)).fetchone()
    if not test:
        conn.close()
        return "A"
    total_a = test["sent_a"] or 0
    total_b = test["sent_b"] or 0
    conn.close()
    return "A" if total_a <= total_b else "B"


def record_ab_result(test_id: int, variant: str, responded: bool = False):
    conn = get_conn()
    if variant == "A":
        conn.execute("UPDATE ab_tests SET sent_a = sent_a + 1 WHERE id = ?", (test_id,))
        if responded:
            conn.execute("UPDATE ab_tests SET response_a = response_a + 1 WHERE id = ?", (test_id,))
    else:
        conn.execute("UPDATE ab_tests SET sent_b = sent_b + 1 WHERE id = ?", (test_id,))
        if responded:
            conn.execute("UPDATE ab_tests SET response_b = response_b + 1 WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


# ==================== CPS RATE LIMITING ====================

CPS_LIMIT = 1  # calls per second default

def can_send(channel: str, cps: int = CPS_LIMIT) -> bool:
    """Check if we can send based on CPS limit."""
    conn = get_conn()
    # Count sends in the last second
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM rate_limit_log WHERE channel = ? AND sent_at > datetime('now', '-1 second')",
        (channel,)
    ).fetchone()
    conn.close()
    return (row["cnt"] or 0) < cps


def record_send(channel: str, phone: str):
    """Record a send for rate limiting."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO rate_limit_log (channel, phone) VALUES (?, ?)",
        (channel, phone)
    )
    conn.commit()
    conn.close()
    # Cleanup old entries (keep last 5 minutes)
    conn = get_conn()
    conn.execute("DELETE FROM rate_limit_log WHERE sent_at < datetime('now', '-5 minutes')")
    conn.commit()
    conn.close()


# ==================== INCOMING WHATSAPP ====================

def log_incoming_wa(phone: str, message: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO wa_inbox (phone, message) VALUES (?, ?)",
        (phone, message)
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


def get_unreplied_wa() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM wa_inbox WHERE replied = 0 ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_wa_replied(msg_id: int, reply_text: str):
    conn = get_conn()
    conn.execute(
        "UPDATE wa_inbox SET replied = 1, reply_text = ? WHERE id = ?",
        (reply_text, msg_id)
    )
    conn.commit()
    conn.close()


# ==================== DEFAULT TEMPLATES ====================

DEFAULT_TEMPLATES = [
    {
        "name": "КП — Турагентства",
        "industry": "Турагентства",
        "channel": "whatsapp",
        "subject": "",
        "body": "Здравствуйте! 👋\n\nМы — Technomax, помогаем турагентствам автоматизировать бронирования и общение с клиентами с помощью AI.\n\n✅ AI-ассистент для WhatsApp 24/7\n✅ Автоматические напоминания о вылете\n✅ Обработка заявок без менеджеров\n\nХотите узнать подробнее? Ответьте «Да» и мы пришлём КП.",
        "is_default": 1,
    },
    {
        "name": "КП — Ломбарды",
        "industry": "Ломбарды",
        "channel": "whatsapp",
        "subject": "",
        "body": "Здравствуйте! 👋\n\nTechnomax помогает ломбардам автоматизировать оценку и общение с клиентами.\n\n✅ AI-бот для оценки залога через фото\n✅ Автоматические уведомления о сроках\n✅ Обработка обращений 24/7\n\nИнтерно? Ответьте «Да» для подробностей.",
        "is_default": 1,
    },
    {
        "name": "КП — Микрофинансирование",
        "industry": "Микрофинансирование",
        "channel": "whatsapp",
        "subject": "",
        "body": "Здравствуйте! 👋\n\nTechnomax помогает МФО автоматизировать обзвон и работу с должниками.\n\n✅ AI-агент для обзвона должников\n✅ Автоматические напоминания о платежах\n✅ Экономия до 70% на колл-центре\n\nХотите демо? Ответьте «Да».",
        "is_default": 1,
    },
    {
        "name": "КП — Страхование",
        "industry": "Страхование",
        "channel": "whatsapp",
        "subject": "",
        "body": "Здравствуйте! 👋\n\nTechnomax помогает страховым компаниям автоматизировать клиентское обслуживание.\n\n✅ AI-консультант по полисам 24/7\n✅ Автоматическое продление полисов\n✅ Обработка страховых случаев\n\nИнтересно? Напишите «Да».",
        "is_default": 1,
    },
    {
        "name": "КП — Email (универсальный)",
        "industry": "",
        "channel": "email",
        "subject": "Коммерческое предложение — AI автоматизация для {company_name}",
        "body": "<h2>Здравствуйте!</h2><p>Мы — Technomax. Помогаем компаниям автоматизировать обзвон клиентов, обработку заявок и коммуникации через мессенджеры с помощью AI.</p><h3>Что мы предлагаем:</h3><ul><li>AI-агенты для WhatsApp и Telegram</li><li>Голосовые боты для обзвона</li><li>CRM-интеграция</li><li>Аналитика и отчёты</li></ul><p>Будем рады обсудить детали.</p>",
        "is_default": 1,
    },
]


def seed_default_templates():
    """Insert default templates if none exist."""
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) as cnt FROM message_templates").fetchone()["cnt"]
    if count > 0:
        conn.close()
        return
    for t in DEFAULT_TEMPLATES:
        conn.execute(
            """INSERT INTO message_templates (name, industry, channel, subject, body, is_default)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (t["name"], t["industry"], t["channel"], t["subject"], t["body"], t["is_default"])
        )
    conn.commit()
    conn.close()
    logger.info("Seeded %d default templates", len(DEFAULT_TEMPLATES))

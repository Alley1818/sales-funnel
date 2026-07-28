"""
Business logic for all sales funnel features.
Agents per industry, templates, DNC, scoring, campaigns, A/B tests, CPS.
"""
import json
import logging
import time
from datetime import datetime

from db_conn import get_conn

logger = logging.getLogger("funnel_features")


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
    return agent_id


def get_agents() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM ai_agents ORDER BY industry").fetchall()
    return [dict(r) for r in rows]


def get_agent_by_industry(industry: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM ai_agents WHERE industry = ? AND enabled = 1", (industry,)).fetchone()
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
    return True


def delete_agent(agent_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM ai_agents WHERE id = ?", (agent_id,))
    conn.commit()
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
    return [dict(r) for r in rows]


def get_template_for_lead(lead_id: int, channel: str) -> dict | None:
    """Get the best template for a lead based on industry and channel."""
    conn = get_conn()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
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
    return True


def delete_template(template_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM message_templates WHERE id = ?", (template_id,))
    conn.commit()
    return True


# ==================== DO NOT CALL ====================

def add_dnc(phone: str, reason: str = "") -> bool:
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO do_not_call (phone, reason) VALUES (?, ?)", (phone, reason))
        conn.commit()
        return True
    except Exception:
        return False


def is_dnc(phone: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM do_not_call WHERE phone = ?", (phone,)).fetchone()
    return row is not None


def get_dnc_list() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM do_not_call ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


def remove_dnc(phone: str) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM do_not_call WHERE phone = ?", (phone,))
    conn.commit()
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


def get_lead_score(lead_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM lead_scores WHERE lead_id = ?", (lead_id,)).fetchone()
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
    return [dict(r) for r in rows]


def update_campaign_lead_status(campaign_id: int, lead_id: int, status: str, result: str = ""):
    conn = get_conn()
    conn.execute(
        "UPDATE campaign_leads SET status = ?, result = ?, called_at = CURRENT_TIMESTAMP WHERE campaign_id = ? AND lead_id = ?",
        (status, result, campaign_id, lead_id)
    )
    conn.commit()


def update_campaign_status(campaign_id: int, status: str):
    conn = get_conn()
    if status == "running":
        conn.execute("UPDATE campaigns SET status = ?, started_at = CURRENT_TIMESTAMP WHERE id = ?", (status, campaign_id))
    elif status in ("completed", "paused"):
        conn.execute("UPDATE campaigns SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?", (status, campaign_id))
    else:
        conn.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))
    conn.commit()


def delete_campaign(campaign_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM campaign_leads WHERE campaign_id = ?", (campaign_id,))
    conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    conn.commit()


# ==================== A/B TESTING ====================

def create_ab_test(name: str, template_a_id: int, template_b_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO ab_tests (name, template_a_id, template_b_id) VALUES (?, ?, ?)",
        (name, template_a_id, template_b_id)
    )
    conn.commit()
    tid = cur.lastrowid
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
    return [dict(r) for r in rows]


def pick_ab_variant(test_id: int) -> str:
    """Pick A or B variant using Thompson sampling (probabilistic, favors winner)."""
    import random
    conn = get_conn()
    test = conn.execute("SELECT * FROM ab_tests WHERE id = ?", (test_id,)).fetchone()
    if not test:
        return "A"

    sent_a = max(test["sent_a"] or 0, 1)
    sent_b = max(test["sent_b"] or 0, 1)
    resp_a = test["response_a"] or 0
    resp_b = test["response_b"] or 0

    # Thompson sampling: sample from Beta distribution
    # Beta(successes + 1, failures + 1)
    sample_a = random.betavariate(resp_a + 1, sent_a - resp_a + 1)
    sample_b = random.betavariate(resp_b + 1, sent_b - resp_b + 1)

    return "A" if sample_a >= sample_b else "B"


def get_ab_significance(test_id: int) -> dict:
    """
    Calculate statistical significance of A/B test using chi-squared test.
    Returns: {significant: bool, confidence: float, winner: str|None, p_value: float}
    """
    conn = get_conn()
    test = conn.execute("SELECT * FROM ab_tests WHERE id = ?", (test_id,)).fetchone()
    if not test:
        return {"significant": False, "confidence": 0, "winner": None, "p_value": 1.0}

    sent_a = test["sent_a"] or 0
    sent_b = test["sent_b"] or 0
    resp_a = test["response_a"] or 0
    resp_b = test["response_b"] or 0

    # Need minimum sample size
    if sent_a < 10 or sent_b < 10:
        return {"significant": False, "confidence": 0, "winner": None, "p_value": 1.0,
                "reason": f"Need min 10 sends per variant (A={sent_a}, B={sent_b})"}

    # Chi-squared test for independence
    # Contingency table: [[resp_a, sent_a-resp_a], [resp_b, sent_b-resp_b]]
    no_resp_a = sent_a - resp_a
    no_resp_b = sent_b - resp_b
    total = sent_a + sent_b
    total_resp = resp_a + resp_b
    total_no_resp = no_resp_a + no_resp_b

    if total_resp == 0 or total_no_resp == 0:
        return {"significant": False, "confidence": 0, "winner": None, "p_value": 1.0,
                "reason": "No responses yet"}

    # Expected values
    e_resp_a = sent_a * total_resp / total
    e_resp_b = sent_b * total_resp / total
    e_no_resp_a = sent_a * total_no_resp / total
    e_no_resp_b = sent_b * total_no_resp / total

    # Chi-squared statistic
    chi2 = 0
    for observed, expected in [(resp_a, e_resp_a), (no_resp_a, e_no_resp_a),
                                (resp_b, e_resp_b), (no_resp_b, e_no_resp_b)]:
        if expected > 0:
            chi2 += (observed - expected) ** 2 / expected

    # Approximate p-value (1 df chi-squared)
    # Using Wilson-Hilferty approximation
    import math
    if chi2 > 0:
        z = math.sqrt(chi2)
        # Standard normal CDF approximation
        p_value = 2 * (1 - _norm_cdf(abs(z)))
    else:
        p_value = 1.0

    confidence = 1 - p_value
    significant = confidence >= 0.95

    winner = None
    if significant:
        rate_a = resp_a / sent_a if sent_a > 0 else 0
        rate_b = resp_b / sent_b if sent_b > 0 else 0
        winner = "A" if rate_a > rate_b else "B"

    return {
        "significant": significant,
        "confidence": round(confidence, 4),
        "winner": winner,
        "p_value": round(p_value, 4),
        "rate_a": round(resp_a / sent_a, 4) if sent_a > 0 else 0,
        "rate_b": round(resp_b / sent_b, 4) if sent_b > 0 else 0,
        "sent_a": sent_a,
        "sent_b": sent_b,
        "resp_a": resp_a,
        "resp_b": resp_b,
    }


def _norm_cdf(x: float) -> float:
    """Approximate standard normal CDF using error function."""
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def check_ab_winner(test_id: int) -> dict:
    """
    Check if A/B test has a winner. If significant at 95%, auto-pause the loser.
    Returns significance info + action taken.
    """
    result = get_ab_significance(test_id)

    if result["significant"] and result["winner"]:
        conn = get_conn()
        # Mark test as completed with winner
        conn.execute(
            "UPDATE ab_tests SET status = ? WHERE id = ?",
            (f"winner_{result['winner']}", test_id),
        )
        conn.commit()
        result["action"] = f"winner_{result['winner']}_declared"
    else:
        result["action"] = "continue_testing"

    return result


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
    return (row["cnt"] or 0) < cps


def record_send(channel: str, phone: str):
    """Record a send for rate limiting."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO rate_limit_log (channel, phone) VALUES (?, ?)",
        (channel, phone)
    )
    conn.commit()
    # Cleanup old entries (keep last 5 minutes)
    conn = get_conn()
    conn.execute("DELETE FROM rate_limit_log WHERE sent_at < datetime('now', '-5 minutes')")
    conn.commit()


# ==================== INCOMING WHATSAPP ====================

def log_incoming_wa(phone: str, message: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO wa_inbox (phone, message) VALUES (?, ?)",
        (phone, message)
    )
    conn.commit()
    mid = cur.lastrowid
    return mid


def get_unreplied_wa() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM wa_inbox WHERE replied = 0 ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_wa_replied(msg_id: int, reply_text: str):
    conn = get_conn()
    conn.execute(
        "UPDATE wa_inbox SET replied = 1, reply_text = ? WHERE id = ?",
        (reply_text, msg_id)
    )
    conn.commit()


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
        return
    for t in DEFAULT_TEMPLATES:
        conn.execute(
            """INSERT INTO message_templates (name, industry, channel, subject, body, is_default)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (t["name"], t["industry"], t["channel"], t["subject"], t["body"], t["is_default"])
        )
    conn.commit()
    logger.info("Seeded %d default templates", len(DEFAULT_TEMPLATES))

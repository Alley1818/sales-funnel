"""
Advanced features: RAG, sentiment, scoring, callbacks, transfers, analytics, auth, logging.
"""
import json
import os
import logging
import hashlib
import time
from datetime import datetime, timedelta

from db_conn import get_conn

logger = logging.getLogger("advanced_features")


def init_advanced_tables():
    conn = get_conn()
    conn.executescript("""
        -- Knowledge base for RAG
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            doc_type TEXT DEFAULT 'text',  -- text, pdf, html, md
            industry TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            chunk_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Knowledge chunks for RAG search
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER REFERENCES knowledge_base(id) ON DELETE CASCADE,
            chunk_index INTEGER,
            content TEXT NOT NULL,
            embedding TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Sentiment analysis results
        CREATE TABLE IF NOT EXISTS sentiment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            channel TEXT,
            message TEXT,
            sentiment TEXT DEFAULT 'neutral',  -- positive, neutral, negative, angry
            score REAL DEFAULT 0.0,  -- -1.0 to 1.0
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Callbacks scheduled
        CREATE TABLE IF NOT EXISTS callbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            scheduled_at TIMESTAMP NOT NULL,
            channel TEXT DEFAULT 'voice',  -- voice, whatsapp
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',  -- pending, completed, cancelled, missed
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Manager transfers (live handoff)
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            reason TEXT DEFAULT '',
            channel TEXT DEFAULT 'voice',
            manager_phone TEXT DEFAULT '',
            manager_chat_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',  -- pending, accepted, completed
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Action log (audit trail)
        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            entity_type TEXT DEFAULT '',  -- lead, campaign, agent, template
            entity_id INTEGER,
            details TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Auth sessions
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token TEXT PRIMARY KEY,
            user_name TEXT DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        );

        -- API rate limit tracking
        CREATE TABLE IF NOT EXISTS api_rate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Campaign costs for ROI
        CREATE TABLE IF NOT EXISTS campaign_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER REFERENCES campaigns(id),
            cost_type TEXT DEFAULT 'call',  -- call, sms, email, platform
            amount REAL DEFAULT 0.0,
            currency TEXT DEFAULT 'KZT',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_kb_industry ON knowledge_base(industry);
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON knowledge_chunks(doc_id);
        CREATE INDEX IF NOT EXISTS idx_sentiment_lead ON sentiment_log(lead_id);
        CREATE INDEX IF NOT EXISTS idx_callbacks_status ON callbacks(status, scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);
        CREATE INDEX IF NOT EXISTS idx_action_log_entity ON action_log(entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_rate_ip ON api_rate_log(ip, called_at);
    """)
    conn.commit()


# ==================== RAG KNOWLEDGE BASE ====================

def add_document(title: str, content: str, doc_type: str = "text", industry: str = "", file_path: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO knowledge_base (title, content, doc_type, industry, file_path) VALUES (?,?,?,?,?)",
        (title, content, doc_type, industry, file_path)
    )
    doc_id = cur.lastrowid
    # Chunk the content (simple: split by paragraphs, ~500 chars each)
    chunks = _chunk_text(content, 500)
    for i, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO knowledge_chunks (doc_id, chunk_index, content) VALUES (?,?,?)",
            (doc_id, i, chunk)
        )
    conn.execute("UPDATE knowledge_base SET chunk_count = ? WHERE id = ?", (len(chunks), doc_id))
    conn.commit()
    return doc_id


def _chunk_text(text: str, max_len: int = 500) -> list[str]:
    """Split text into chunks by paragraphs, respecting max_len."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) > max_len and current:
            chunks.append(current.strip())
            current = p
        else:
            current += "\n\n" + p if current else p
    if current.strip():
        chunks.append(current.strip())
    if not chunks:
        chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    return chunks


def search_knowledge(query: str, industry: str = "", limit: int = 3) -> list[str]:
    """Simple keyword search through knowledge chunks. Returns matching chunk texts."""
    conn = get_conn()
    q = """SELECT kc.content FROM knowledge_chunks kc
           JOIN knowledge_base kb ON kc.doc_id = kb.id
           WHERE 1=1"""
    params = []
    if industry:
        q += " AND (kb.industry = ? OR kb.industry = '')"
        params.append(industry)
    # Simple keyword match
    keywords = query.lower().split()
    for kw in keywords[:5]:  # max 5 keywords
        q += " AND LOWER(kc.content) LIKE ?"
        params.append(f"%{kw}%")
    q += " ORDER BY kc.chunk_index LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    return [r["content"] for r in rows]


def get_documents() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM knowledge_base ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM knowledge_chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM knowledge_base WHERE id = ?", (doc_id,))
    conn.commit()


def get_rag_context(query: str, industry: str = "") -> str:
    """Get RAG context for AI agent prompt injection."""
    chunks = search_knowledge(query, industry)
    if not chunks:
        return ""
    context = "\n\nКОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ:\n"
    for i, chunk in enumerate(chunks):
        context += f"\n--- Фрагмент {i+1} ---\n{chunk}\n"
    return context


# ==================== SENTIMENT ANALYSIS ====================

POSITIVE_WORDS = {"спасибо", "отлично", "хорошо", "супер", "класс", "замечательно", "да", "конечно",
                  "interested", "давайте", "хочу", "нужно", "круто", "прекрасно", "согласен"}
NEGATIVE_WORDS = {"нет", "отказ", "не надо", "неинтересно", "не интересно", "stop", "занят", "плохо", "ужасно",
                  "не звоните", "в суд", "жалоба", "раздражает", "бесит", "хватит"}
ANGRY_WORDS = {"идиоты", "дураки", "маразм", "бред", "отвратительно", "ненавижу", "скам"}


NEGATION_WORDS = {"не", "нет", "ничего"}
NEGATIVE_BIGRAMS = {"не надо", "не интересно", "ничего не", "не звоните", "не хочу", "не нужно", "не работает"}


def analyze_sentiment(text: str) -> dict:
    """Keyword-based sentiment analysis with negation handling."""
    lower = text.lower()
    word_list = lower.split()
    words = set(word_list)

    # Detect negation: if a negation word precedes a positive word, treat it as negative.
    # Also check bigrams against NEGATIVE_BIGRAMS for multi-word negative phrases.
    negated_positive = 0
    for i, w in enumerate(word_list):
        if w in NEGATION_WORDS and i + 1 < len(word_list):
            next_w = word_list[i + 1]
            if next_w in POSITIVE_WORDS:
                negated_positive += 1
            # Multi-word negative bigrams
            bigram = f"{w} {next_w}"
            if bigram in NEGATIVE_BIGRAMS:
                negated_positive += 1

    pos = len(words & POSITIVE_WORDS) - negated_positive
    neg = len(words & NEGATIVE_WORDS) + negated_positive
    angry = len(words & ANGRY_WORDS)

    if angry > 0:
        return {"sentiment": "angry", "score": -1.0}
    elif neg > pos and neg > 0:
        return {"sentiment": "negative", "score": -0.5}
    elif pos > neg and pos > 0:
        return {"sentiment": "positive", "score": 0.5}
    else:
        return {"sentiment": "neutral", "score": 0.0}


def log_sentiment(lead_id: int, channel: str, message: str) -> dict:
    result = analyze_sentiment(message)
    conn = get_conn()
    conn.execute(
        "INSERT INTO sentiment_log (lead_id, channel, message, sentiment, score) VALUES (?,?,?,?,?)",
        (lead_id, channel, message[:500], result["sentiment"], result["score"])
    )
    conn.commit()
    return result


def get_sentiment_history(lead_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sentiment_log WHERE lead_id = ? ORDER BY analyzed_at DESC LIMIT 20",
        (lead_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ==================== AUTO SCORING ====================

def auto_score_lead(lead: dict) -> dict:
    """Score a lead based on available data from Excel."""
    score = 0
    reasons = []

    # Has phone
    if lead.get("mobile") or lead.get("phone"):
        score += 15
        reasons.append("есть телефон")

    # Has WhatsApp
    if lead.get("whatsapp"):
        score += 10
        reasons.append("есть WhatsApp")

    # Has email
    if lead.get("email"):
        score += 10
        reasons.append("есть email")

    # Has website
    if lead.get("website"):
        score += 5
        reasons.append("есть сайт")

    # Industry scoring
    hot_industries = {"Микрофинансирование", "Ломбарды", "Страхование"}
    warm_industries = {"Турагентства", "Фитнес-клубы", "Агентства недвижимости"}
    industry = lead.get("industry", "")
    if industry in hot_industries:
        score += 25
        reasons.append(f"горячая отрасль: {industry}")
    elif industry in warm_industries:
        score += 15
        reasons.append(f"тёплая отрасль: {industry}")
    else:
        score += 5

    # Has multiple contacts
    contact_fields = sum(1 for f in ["mobile", "phone", "email", "whatsapp", "telegram"] if lead.get(f))
    if contact_fields >= 3:
        score += 15
        reasons.append("много контактов")
    elif contact_fields >= 2:
        score += 10
        reasons.append("есть контакты")

    # Has city/region
    if lead.get("city"):
        score += 5
        reasons.append("есть город")

    # Cap at 100
    score = min(score, 100)

    # Category
    if score >= 70:
        category = "hot"
    elif score >= 40:
        category = "warm"
    else:
        category = "cold"

    reasoning = "; ".join(reasons)
    return {"score": score, "category": category, "reasoning": reasoning}


def batch_score_leads(limit: int = 100) -> int:
    """Score unscored leads."""
    conn = get_conn()
    leads = conn.execute("""
        SELECT l.* FROM leads l
        LEFT JOIN lead_scores s ON l.id = s.lead_id
        WHERE s.lead_id IS NULL AND l.status = 'new'
        LIMIT ?
    """, (limit,)).fetchall()

    scored = 0
    for lead in leads:
        lead_dict = dict(lead)
        result = auto_score_lead(lead_dict)
        from funnel_features import score_lead
        score_lead(lead_dict["id"], result["score"], result["category"], result["reasoning"])
        scored += 1
    return scored


# ==================== CALLBACKS ====================

def schedule_callback(lead_id: int, scheduled_at: str, channel: str = "voice", notes: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO callbacks (lead_id, scheduled_at, channel, notes) VALUES (?,?,?,?)",
        (lead_id, scheduled_at, channel, notes)
    )
    cid = cur.lastrowid
    conn.commit()
    return cid


def get_pending_callbacks() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT cb.*, l.company_name, l.mobile, l.email, l.industry
        FROM callbacks cb JOIN leads l ON cb.lead_id = l.id
        WHERE cb.status = 'pending'
        ORDER BY cb.scheduled_at
    """).fetchall()
    return [dict(r) for r in rows]


def complete_callback(callback_id: int, status: str = "completed"):
    conn = get_conn()
    conn.execute("UPDATE callbacks SET status = ? WHERE id = ?", (status, callback_id))
    conn.commit()


def get_due_callbacks() -> list[dict]:
    """Get callbacks that are due now."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT cb.*, l.company_name, l.mobile, l.email
        FROM callbacks cb JOIN leads l ON cb.lead_id = l.id
        WHERE cb.status = 'pending' AND cb.scheduled_at <= datetime('now')
        ORDER BY cb.scheduled_at
    """).fetchall()
    return [dict(r) for r in rows]


# ==================== MANAGER TRANSFER ====================

def request_transfer(lead_id: int, reason: str = "", channel: str = "voice", manager_phone: str = "", manager_chat_id: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO transfers (lead_id, reason, channel, manager_phone, manager_chat_id) VALUES (?,?,?,?,?)",
        (lead_id, reason, channel, manager_phone, manager_chat_id)
    )
    tid = cur.lastrowid
    conn.commit()
    return tid


def get_pending_transfers() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT t.*, l.company_name, l.mobile, l.industry
        FROM transfers t JOIN leads l ON t.lead_id = l.id
        WHERE t.status = 'pending' ORDER BY t.created_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


def complete_transfer(transfer_id: int, status: str = "accepted"):
    conn = get_conn()
    conn.execute("UPDATE transfers SET status = ? WHERE id = ?", (status, transfer_id))
    conn.commit()


# ==================== ACTION LOG ====================

def log_action(action: str, entity_type: str = "", entity_id: int = 0, details: str = "", ip: str = ""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO action_log (action, entity_type, entity_id, details, ip_address) VALUES (?,?,?,?,?)",
        (action, entity_type, entity_id, details[:1000], ip)
    )
    conn.commit()


def get_action_log(limit: int = 50) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM action_log ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ==================== AUTH ====================
# Duplicate auth removed — use middleware.verify_password + create_session instead.
# See advanced_api.py /api/auth/login which now routes through middleware.
# ==================== RATE LIMITING ====================

RATE_LIMIT_PER_MINUTE = 60


def check_rate_limit(ip: str, endpoint: str) -> bool:
    """Returns True if request is within rate limit."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM api_rate_log WHERE ip = ? AND called_at > datetime('now', '-1 minute')",
        (ip,)
    ).fetchone()
    return (row["cnt"] or 0) < RATE_LIMIT_PER_MINUTE


def record_api_call(ip: str, endpoint: str):
    conn = get_conn()
    conn.execute("INSERT INTO api_rate_log (ip, endpoint) VALUES (?,?)", (ip, endpoint))
    conn.commit()
    # Cleanup old entries
    conn = get_conn()
    conn.execute("DELETE FROM api_rate_log WHERE called_at < datetime('now', '-10 minutes')")
    conn.commit()


# ==================== ANALYTICS ====================

def get_dashboard_data() -> dict:
    """Get all analytics data for the dashboard."""
    conn = get_conn()

    # Leads by status
    status_counts = {}
    for row in conn.execute("SELECT status, COUNT(*) as cnt FROM leads GROUP BY status").fetchall():
        status_counts[row["status"]] = row["cnt"]

    # Leads by industry
    industry_data = []
    for row in conn.execute("""
        SELECT industry, COUNT(*) as total,
               SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) as new_cnt,
               SUM(CASE WHEN status='called' THEN 1 ELSE 0 END) as called_cnt,
               SUM(CASE WHEN status='interested' THEN 1 ELSE 0 END) as interested_cnt,
               SUM(CASE WHEN status='sent_wa' OR status='sent_email' THEN 1 ELSE 0 END) as sent_cnt,
               SUM(CASE WHEN status='refused' THEN 1 ELSE 0 END) as refused_cnt
        FROM leads GROUP BY industry ORDER BY total DESC
    """).fetchall():
        industry_data.append(dict(row))

    # Score distribution
    score_dist = {"hot": 0, "warm": 0, "cold": 0, "unscored": 0}
    for row in conn.execute("""
        SELECT COALESCE(s.category, 'unscored') as cat, COUNT(*) as cnt
        FROM leads l LEFT JOIN lead_scores s ON l.id = s.lead_id
        GROUP BY cat
    """).fetchall():
        score_dist[row["cat"]] = row["cnt"]

    # Sentiment summary
    sentiment_summary = {"positive": 0, "neutral": 0, "negative": 0, "angry": 0}
    for row in conn.execute("SELECT sentiment, COUNT(*) as cnt FROM sentiment_log GROUP BY sentiment").fetchall():
        sentiment_summary[row["sentiment"]] = row["cnt"]

    # Callbacks today
    callbacks_today = conn.execute("""
        SELECT COUNT(*) as cnt FROM callbacks
        WHERE status = 'pending' AND date(scheduled_at) = date('now')
    """).fetchone()["cnt"]

    # Transfers pending
    transfers_pending = conn.execute("SELECT COUNT(*) as cnt FROM transfers WHERE status = 'pending'").fetchone()["cnt"]

    # Calls by hour (from action_log)
    calls_by_hour = []
    for row in conn.execute("""
        SELECT strftime('%H', created_at) as hour, COUNT(*) as cnt
        FROM action_log WHERE action = 'call_started'
        GROUP BY hour ORDER BY hour
    """).fetchall():
        calls_by_hour.append({"hour": int(row["hour"]), "count": row["cnt"]})

    # Campaign performance
    campaign_perf = []
    for row in conn.execute("""
        SELECT c.name, c.status, c.channel,
               (SELECT COUNT(*) FROM campaign_leads cl WHERE cl.campaign_id = c.id) as total,
               (SELECT COUNT(*) FROM campaign_leads cl WHERE cl.campaign_id = c.id AND cl.status = 'completed') as completed,
               (SELECT COUNT(*) FROM campaign_leads cl WHERE cl.campaign_id = c.id AND cl.result LIKE '%interested%') as interested,
               (SELECT COALESCE(SUM(cc.amount), 0) FROM campaign_costs cc WHERE cc.campaign_id = c.id) as total_cost
        FROM campaigns c ORDER BY c.created_at DESC LIMIT 10
    """).fetchall():
        campaign_perf.append(dict(row))

    # Knowledge base stats
    kb_count = conn.execute("SELECT COUNT(*) as cnt FROM knowledge_base").fetchone()["cnt"]
    kb_chunks = conn.execute("SELECT COUNT(*) as cnt FROM knowledge_chunks").fetchone()["cnt"]


    return {
        "status_counts": status_counts,
        "industry_data": industry_data,
        "score_distribution": score_dist,
        "sentiment_summary": sentiment_summary,
        "callbacks_today": callbacks_today,
        "transfers_pending": transfers_pending,
        "calls_by_hour": calls_by_hour,
        "campaign_performance": campaign_perf,
        "knowledge_base": {"documents": kb_count, "chunks": kb_chunks},
    }


def get_roi_data(campaign_id: int = 0) -> dict:
    """Calculate ROI for campaigns."""
    conn = get_conn()
    if campaign_id:
        campaigns = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchall()
    else:
        campaigns = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()

    results = []
    for c in campaigns:
        c = dict(c)
        costs = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM campaign_costs WHERE campaign_id = ?",
            (c["id"],)
        ).fetchone()
        total_cost = costs["total"] or 0

        leads_data = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN result LIKE '%interested%' THEN 1 ELSE 0 END) as conversions
            FROM campaign_leads WHERE campaign_id = ?
        """, (c["id"],)).fetchone()

        cost_per_lead = total_cost / max(leads_data["total"], 1)
        cost_per_conversion = total_cost / max(leads_data["conversions"], 1) if leads_data["conversions"] else 0
        conversion_rate = (leads_data["conversions"] / max(leads_data["completed"], 1)) * 100 if leads_data["completed"] else 0

        results.append({
            "campaign_id": c["id"],
            "name": c["name"],
            "total_cost": total_cost,
            "leads_total": leads_data["total"],
            "leads_completed": leads_data["completed"],
            "conversions": leads_data["conversions"],
            "cost_per_lead": round(cost_per_lead, 2),
            "cost_per_conversion": round(cost_per_conversion, 2),
            "conversion_rate": round(conversion_rate, 1),
        })

    return {"campaigns": results}

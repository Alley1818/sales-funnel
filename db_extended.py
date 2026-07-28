"""
Extended database schema for all sales funnel features.
"""
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger("db_extended")

DB_PATH = Path(__file__).parent / "leads.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_extended_tables():
    """Create all extended tables."""
    conn = get_conn()
    conn.executescript("""
        -- AI Agents per industry
        CREATE TABLE IF NOT EXISTS ai_agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            industry TEXT NOT NULL UNIQUE,
            technomax_id TEXT,
            prompt TEXT DEFAULT '',
            welcome_phrase TEXT DEFAULT '',
            voice TEXT DEFAULT 'ru-RU-SvetlanaNeural',
            llm_model TEXT DEFAULT 'xiaomi/mimo-v2.5-pro',
            temperature REAL DEFAULT 0.3,
            max_tokens INTEGER DEFAULT 300,
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Message templates per industry
        CREATE TABLE IF NOT EXISTS message_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            industry TEXT DEFAULT '',
            channel TEXT NOT NULL,  -- 'whatsapp', 'email', 'sms'
            subject TEXT DEFAULT '',
            body TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            ab_variant TEXT DEFAULT '',  -- 'A', 'B', '' for no A/B test
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Do Not Call list
        CREATE TABLE IF NOT EXISTS do_not_call (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE,
            reason TEXT DEFAULT '',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Lead scores (AI-generated)
        CREATE TABLE IF NOT EXISTS lead_scores (
            lead_id INTEGER PRIMARY KEY REFERENCES leads(id),
            score INTEGER DEFAULT 0,  -- 0-100
            category TEXT DEFAULT 'cold',  -- hot, warm, cold
            reasoning TEXT DEFAULT '',
            scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Call recordings
        CREATE TABLE IF NOT EXISTS call_recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            call_id TEXT,
            file_path TEXT,
            duration_sec INTEGER DEFAULT 0,
            transcript TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Scheduled campaigns
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            industry TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',  -- draft, scheduled, running, paused, completed
            channel TEXT DEFAULT 'voice',  -- voice, whatsapp, email
            schedule_cron TEXT DEFAULT '',
            schedule_time TEXT DEFAULT '',
            max_calls INTEGER DEFAULT 50,
            cps INTEGER DEFAULT 1,
            agent_id INTEGER REFERENCES ai_agents(id),
            template_id INTEGER REFERENCES message_templates(id),
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Campaign leads (many-to-many)
        CREATE TABLE IF NOT EXISTS campaign_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER REFERENCES campaigns(id),
            lead_id INTEGER REFERENCES leads(id),
            status TEXT DEFAULT 'pending',  -- pending, called, completed, failed, skipped
            result TEXT DEFAULT '',
            called_at TIMESTAMP,
            UNIQUE(campaign_id, lead_id)
        );

        -- A/B test results
        CREATE TABLE IF NOT EXISTS ab_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            template_a_id INTEGER REFERENCES message_templates(id),
            template_b_id INTEGER REFERENCES message_templates(id),
            metric TEXT DEFAULT 'response_rate',  -- response_rate, conversion_rate
            status TEXT DEFAULT 'running',
            sent_a INTEGER DEFAULT 0,
            sent_b INTEGER DEFAULT 0,
            response_a INTEGER DEFAULT 0,
            response_b INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Incoming WhatsApp messages (for auto-reply agent)
        CREATE TABLE IF NOT EXISTS wa_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            message TEXT NOT NULL,
            direction TEXT DEFAULT 'inbound',
            replied INTEGER DEFAULT 0,
            reply_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- CPS rate limiter tracking
        CREATE TABLE IF NOT EXISTS rate_limit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            phone TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_templates_industry ON message_templates(industry);
        CREATE INDEX IF NOT EXISTS idx_templates_channel ON message_templates(channel);
        CREATE INDEX IF NOT EXISTS idx_dnc_phone ON do_not_call(phone);
        CREATE INDEX IF NOT EXISTS idx_scores_category ON lead_scores(category);
        CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
        CREATE INDEX IF NOT EXISTS idx_campaign_leads_campaign ON campaign_leads(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_wa_inbox_phone ON wa_inbox(phone);
        CREATE INDEX IF NOT EXISTS idx_rate_limit_channel ON rate_limit_log(channel, sent_at);
    """)
    conn.commit()
    conn.close()
    logger.info("Extended tables initialized")


if __name__ == "__main__":
    init_extended_tables()
    print("Extended tables created")

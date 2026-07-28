"""
Sales Funnel Engine — orchestrates the full pipeline:
  1. Load leads from DB
  2. Call via Technomax AI agent
  3. Based on result: send WhatsApp / email / schedule callback
"""
import sqlite3
import logging
import time
import json
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta

from leads_db import init_db, get_leads_by_status, update_lead_status, get_stats
from whatsapp_client import WhatsAppClient, WhatsAppConfig, WhatsAppResult
from email_sender import EmailSender, EmailConfig, EmailResult, build_kp_html
from pipecat_client import PipecatClient, PipecatConfig
from funnel_features import is_dnc, can_send, record_send, score_lead
from advanced_features import auto_score_lead, analyze_sentiment, log_sentiment
from agent_sync import log_message, sync_after_call, sync_after_whatsapp, get_lead_context

logger = logging.getLogger("funnel_engine")

# ---------- Templates ----------

WHATSAPP_KP_TEMPLATE = """
Здравствуйте! 👋

Мы только что говорили с вами по телефону.
Как и обещали — отправляем наше коммерческое предложение.

📋 *КП для {company_name}*
Отрасль: {industry}

Если возникнут вопросы — ответьте на это сообщение или позвоните нам.

С уважением, команда продаж.
""".strip()

WHATSAPP_FOLLOWUP_TEMPLATE = """
Здравствуйте! 👋

Мы недавно отправляли вам коммерческое предложение.
Хотели узнать — были ли у вас вопросы?

Мы готовы обсудить детали в удобное для вас время. 📞
""".strip()


# ---------- Call Result Types ----------

class CallResult:
    INTERESTED = "interested"      # wants to hear more → send КП
    CALLBACK = "callback"          # call back later → schedule
    REFUSED = "refused"            # not interested → mark, don't bother
    NO_ANSWER = "no_answer"        # didn't pick up → retry / WhatsApp
    WRONG_NUMBER = "wrong_number"  # wrong number → skip
    VOICEMAIL = "voicemail"        # left voicemail → WhatsApp follow-up


@dataclass
class FunnelConfig:
    """Funnel behavior configuration."""
    # Delays
    delay_between_calls_sec: int = 30
    whatsapp_delay_sec: int = 5
    email_delay_sec: int = 5

    # Retry settings
    max_no_answer_retries: int = 3
    retry_delay_min: int = 60  # minutes between retries

    # Channels
    send_whatsapp_after_call: bool = True
    send_email_after_call: bool = True
    whatsapp_only_if_no_email: bool = False  # send both if available

    # Content
    kp_attachment_url: str = ""  # URL to PDF КП


class FunnelEngine:
    """Main sales funnel orchestrator."""

    def __init__(
        self,
        db_conn: sqlite3.Connection,
        whatsapp: WhatsAppClient | None = None,
        email: EmailSender | None = None,
        pipecat: PipecatClient | None = None,
        config: FunnelConfig | None = None,
    ):
        self.conn = db_conn
        self.whatsapp = whatsapp or WhatsAppClient()
        self.email = email or EmailSender()
        self.pipecat = pipecat or PipecatClient()
        self.config = config or FunnelConfig()

    def process_call_result(
        self,
        lead_id: int,
        call_result: str,
        notes: str = "",
    ) -> dict:
        """
        Process a call result and trigger follow-up actions.
        Returns summary of actions taken.
        """
        lead = self.conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()

        if not lead:
            return {"error": f"Lead {lead_id} not found"}

        lead = dict(lead)
        actions = {"lead_id": lead_id, "company": lead["company_name"]}

        # Check DNC before any outbound
        phone = lead.get("whatsapp") or lead.get("mobile") or lead.get("phone", "")
        if phone and is_dnc(phone):
            actions["blocked"] = "dnc"
            logger.warning("Lead %d blocked by DNC: %s", lead_id, phone)
            return actions

        # Log call to cross-channel history
        sync_after_call(lead_id, call_result, notes)

        # Update status based on call result
        if call_result == CallResult.INTERESTED:
            update_lead_status(self.conn, lead_id, "interested", notes)
            actions["status"] = "interested"

            # Send WhatsApp KP (with CPS check)
            if self.config.send_whatsapp_after_call and lead.get("whatsapp"):
                if can_send("whatsapp"):
                    wa_result = self._send_whatsapp_kp(lead)
                    actions["whatsapp"] = "sent" if wa_result.success else f"failed: {wa_result.error}"
                    if wa_result.success:
                        update_lead_status(self.conn, lead_id, "sent_wa")
                        record_send("whatsapp", lead["whatsapp"])
                else:
                    actions["whatsapp"] = "rate_limited"

            # Send Email KP (with CPS check)
            if self.config.send_email_after_call and lead.get("email"):
                if can_send("email"):
                    em_result = self._send_email_kp(lead)
                    actions["email"] = "sent" if em_result.success else f"failed: {em_result.error}"
                    if em_result.success:
                        if actions.get("whatsapp") != "sent":
                            update_lead_status(self.conn, lead_id, "sent_email")
                        record_send("email", lead["email"])
                else:
                    actions["email"] = "rate_limited"

        elif call_result == CallResult.CALLBACK:
            update_lead_status(self.conn, lead_id, "callback", notes)
            actions["status"] = "callback"
            actions["action"] = "schedule_callback"

        elif call_result == CallResult.REFUSED:
            update_lead_status(self.conn, lead_id, "refused", notes)
            actions["status"] = "refused"
            actions["action"] = "skip"

        elif call_result == CallResult.NO_ANSWER:
            update_lead_status(self.conn, lead_id, "called", notes)
            actions["status"] = "called"
            actions["action"] = "retry_or_whatsapp"

            # If WhatsApp available, send as fallback (with CPS check)
            if lead.get("whatsapp") and can_send("whatsapp"):
                wa_result = self._send_whatsapp_kp(lead, is_followup=True)
                actions["whatsapp_fallback"] = "sent" if wa_result.success else "failed"
                if wa_result.success:
                    record_send("whatsapp", lead["whatsapp"])

        elif call_result == CallResult.WRONG_NUMBER:
            update_lead_status(self.conn, lead_id, "refused", "wrong number")
            actions["status"] = "wrong_number"

        elif call_result == CallResult.VOICEMAIL:
            update_lead_status(self.conn, lead_id, "called", "voicemail")
            actions["status"] = "voicemail"

        # Auto-score after interaction
        self._update_lead_score(lead_id, lead, call_result)

        # Schedule follow-up sequence if KP was sent
        if actions.get("whatsapp") == "sent" or actions.get("email") == "sent":
            self._schedule_followup(lead_id, actions)

        # Log
        self.conn.execute(
            "INSERT INTO call_log (lead_id, call_type, result) VALUES (?, ?, ?)",
            (lead_id, "ai_target", call_result),
        )
        self.conn.commit()

        return actions

    def _update_lead_score(self, lead_id: int, lead: dict, call_result: str):
        """Re-score lead based on interaction signals."""
        result = auto_score_lead(lead)

        # Engagement bonuses
        if call_result == CallResult.INTERESTED:
            result["score"] = min(result["score"] + 20, 100)
            result["reasoning"] += "; заинтересован после звонка"
        elif call_result == CallResult.CALLBACK:
            result["score"] = min(result["score"] + 10, 100)
            result["reasoning"] += "; готов к перезвону"

        # Recalculate category
        if result["score"] >= 70:
            result["category"] = "hot"
        elif result["score"] >= 40:
            result["category"] = "warm"
        else:
            result["category"] = "cold"

        score_lead(lead_id, result["score"], result["category"], result["reasoning"])

    # ---- Follow-up Sequences ----

    def _init_followup_table(self):
        """Create follow-up sequences table if not exists."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS followup_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER REFERENCES leads(id),
                channel TEXT NOT NULL,
                attempt INTEGER DEFAULT 1,
                max_attempts INTEGER DEFAULT 3,
                next_followup_at TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'pending',
                result TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_followup_status ON followup_sequences(status, next_followup_at);
        """)
        self.conn.commit()

    def _schedule_followup(self, lead_id: int, actions: dict):
        """Schedule a follow-up 24h after KP send."""
        self._init_followup_table()
        channel = "whatsapp" if actions.get("whatsapp") == "sent" else "email"
        # First follow-up in 24 hours
        self.conn.execute(
            """INSERT INTO followup_sequences (lead_id, channel, attempt, next_followup_at)
               VALUES (?, ?, 1, datetime('now', '+24 hours'))""",
            (lead_id, channel),
        )
        self.conn.commit()
        logger.info("Follow-up scheduled for lead %d (channel=%s, +24h)", lead_id, channel)

    def process_followups(self, limit: int = 10) -> list[dict]:
        """
        Process due follow-ups:
        - Check if lead responded since KP was sent
        - If no response: send follow-up message, schedule next attempt
        - Max 3 attempts before giving up
        """
        self._init_followup_table()

        due = self.conn.execute("""
            SELECT f.*, l.company_name, l.whatsapp, l.mobile, l.email, l.industry, l.status as lead_status
            FROM followup_sequences f
            JOIN leads l ON f.lead_id = l.id
            WHERE f.status = 'pending' AND f.next_followup_at <= datetime('now')
            ORDER BY f.next_followup_at
            LIMIT ?
        """, (limit,)).fetchall()

        results = []
        for row in due:
            fu = dict(row)
            lead_id = fu["lead_id"]

            # Check if lead already responded or progressed
            if fu["lead_status"] in ("interested", "sent_wa", "sent_email", "done"):
                self.conn.execute(
                    "UPDATE followup_sequences SET status = 'skipped', result = ? WHERE id = ?",
                    ("lead already progressed", fu["id"]),
                )
                results.append({"lead_id": lead_id, "action": "skipped", "reason": "already progressed"})
                continue

            # Send follow-up
            lead = {k: fu[k] for k in ("company_name", "whatsapp", "mobile", "email", "industry")}
            channel = fu["channel"]

            if channel == "whatsapp":
                phone = lead.get("whatsapp") or lead.get("mobile")
                if phone and not is_dnc(phone) and can_send("whatsapp"):
                    wa_result = self._send_followup_message(lead, fu["attempt"])
                    if wa_result.success:
                        record_send("whatsapp", phone)
                        self.conn.execute(
                            "UPDATE followup_sequences SET status = 'sent', result = 'sent' WHERE id = ?",
                            (fu["id"],),
                        )
                        results.append({"lead_id": lead_id, "action": "followup_sent", "channel": "whatsapp", "attempt": fu["attempt"]})
                    else:
                        results.append({"lead_id": lead_id, "action": "followup_failed", "error": wa_result.error})
                else:
                    results.append({"lead_id": lead_id, "action": "skipped", "reason": "dnc or rate_limited"})

            # Schedule next attempt if under max
            if fu["attempt"] < fu["max_attempts"]:
                delay_hours = 48 if fu["attempt"] == 1 else 72  # 48h, then 72h
                self.conn.execute(
                    """INSERT INTO followup_sequences (lead_id, channel, attempt, max_attempts, next_followup_at)
                       VALUES (?, ?, ?, ?, datetime('now', ?))""",
                    (lead_id, channel, fu["attempt"] + 1, fu["max_attempts"], f"+{delay_hours} hours"),
                )
            else:
                self.conn.execute(
                    "UPDATE followup_sequences SET status = 'max_reached' WHERE id = ?",
                    (fu["id"],),
                )
                results.append({"lead_id": lead_id, "action": "max_followups_reached"})

            self.conn.commit()

        return results

    def _send_followup_message(self, lead: dict, attempt: int) -> "WhatsAppResult":
        """Send a follow-up WhatsApp message."""
        phone = lead.get("whatsapp") or lead.get("mobile")
        if not phone:
            from whatsapp_client import WhatsAppResult
            return WhatsAppResult(success=False, error="No phone")

        templates = {
            1: f"""Здравствуйте!

Мы недавно отправляли коммерческое предложение для {lead['company_name']}.
Хотели узнать — были ли у вас вопросы?

Готовы обсудить детали в удобное для вас время.""",
            2: f"""Здравствуйте!

Напоминаем о нашем предложении для {lead['company_name']} в сфере {lead.get('industry', '')}.

Если сейчас не удобно — просто ответьте, и мы свяжемся позже.""",
            3: f"""Здравствуйте!

Это последнее напоминание о КП для {lead['company_name']}.

Если не интересно — просто ответьте «Стоп» и мы больше не побеспокоим.""",
        }

        message = templates.get(attempt, templates[1])
        result = self.whatsapp.send_text(phone, message)

        # Log
        self.conn.execute(
            "INSERT INTO message_log (lead_id, channel, message_type, content, status) VALUES (?, ?, ?, ?, ?)",
            (0, "whatsapp", f"followup_{attempt}", message[:200], "sent" if result.success else "failed"),
        )
        self.conn.commit()
        return result

    def run_batch(self, industry: str | None = None, limit: int = 50) -> list[dict]:
        """
        Run a batch of calls for new leads, ordered by score (hottest first).
        Skips DNC-listed leads.
        Returns list of leads to call.
        """
        query = """
            SELECT l.*, COALESCE(s.score, 0) as lead_score, COALESCE(s.category, 'cold') as score_category
            FROM leads l
            LEFT JOIN lead_scores s ON l.id = s.lead_id
            WHERE l.status = 'new'
        """
        params = []
        if industry:
            query += " AND l.industry = ?"
            params.append(industry)
        query += " ORDER BY COALESCE(s.score, 0) DESC, l.id LIMIT ?"
        params.append(limit)

        leads = self.conn.execute(query, params).fetchall()
        result = []
        for l in leads:
            lead_dict = dict(l)
            phone = lead_dict.get("whatsapp") or lead_dict.get("mobile") or lead_dict.get("phone", "")
            if phone and is_dnc(phone):
                logger.info("Skipping DNC lead: %s (%s)", lead_dict["company_name"], phone)
                continue
            result.append(lead_dict)
        return result

    def get_callbacks(self) -> list[dict]:
        """Get leads scheduled for callback."""
        return [
            dict(r) for r in self.conn.execute(
                "SELECT * FROM leads WHERE status = 'callback' ORDER BY updated_at"
            ).fetchall()
        ]

    def process_callbacks(self, limit: int = 10) -> list[dict]:
        """
        Auto-process due callbacks: find leads with scheduled callbacks that are past due,
        and return them for re-calling. Updates status to 'new' so run_batch picks them up.
        """
        from advanced_features import get_due_callbacks, complete_callback
        due = get_due_callbacks()
        results = []
        for cb in due[:limit]:
            lead_id = cb["lead_id"]
            # Reset lead to 'new' so it enters the next batch
            update_lead_status(self.conn, lead_id, "new", f"callback due: {cb['scheduled_at']}")
            complete_callback(cb["id"], "completed")
            results.append({
                "lead_id": lead_id,
                "company": cb.get("company_name", ""),
                "callback_id": cb["id"],
                "scheduled_at": cb["scheduled_at"],
            })
            logger.info("Callback processed: lead %d (%s)", lead_id, cb.get("company_name", ""))
        return results

    def start_ai_calls(self, industry: str | None = None, limit: int = 5) -> list[dict]:
        """
        Start AI calls via Pipecat agent for new leads.
        Returns list of call results.
        """
        if not self.pipecat.health():
            logger.warning("Pipecat agent not available")
            return [{"error": "Pipecat agent not running on port 8082"}]

        leads = self.run_batch(industry=industry, limit=limit)
        results = []

        for lead in leads:
            phone = lead.get("mobile") or lead.get("phone")
            if not phone:
                continue

            # Mark as called
            update_lead_status(self.conn, lead["id"], "called", "ai_call_started")

            # Trigger call
            call_result = self.pipecat.create_call(
                phone=phone,
                company_name=lead.get("company_name", ""),
                industry=lead.get("industry", ""),
                lead_id=lead["id"],
            )

            results.append({
                "lead_id": lead["id"],
                "company": lead["company_name"],
                "phone": phone,
                "call_id": call_result.call_id,
                "status": call_result.status,
            })

            # NOTE: In production, replace time.sleep() with a task queue
            # (e.g. Celery, RQ) to schedule calls asynchronously.
            time.sleep(self.config.delay_between_calls_sec)

        return results

    def get_funnel_report(self) -> str:
        """Generate a text funnel report."""
        stats = get_stats(self.conn)
        total = sum(stats.values())

        lines = [
            f"=== ВОРОНКА ПРОДАЖ ===",
            f"Всего лидов: {total}",
            "",
            f"  Новые:        {stats.get('new', 0)}",
            f"  Позвонили:    {stats.get('called', 0)}",
            f"  Интерес:      {stats.get('interested', 0)}",
            f"  Перезвонить:  {stats.get('callback', 0)}",
            f"  Отправлено WA:{stats.get('sent_wa', 0)}",
            f"  Отправлено EM:{stats.get('sent_email', 0)}",
            f"  Завершено:    {stats.get('done', 0)}",
            f"  Отказ:        {stats.get('refused', 0)}",
        ]

        if total > 0:
            called = stats.get('called', 0) + stats.get('interested', 0) + \
                     stats.get('callback', 0) + stats.get('sent_wa', 0) + \
                     stats.get('sent_email', 0) + stats.get('done', 0) + \
                     stats.get('refused', 0)
            contacted = stats.get('interested', 0) + stats.get('sent_wa', 0) + \
                        stats.get('sent_email', 0) + stats.get('done', 0)
            lines.extend([
                "",
                f"  Конверсия звонок→контакт: {called/total*100:.1f}%",
                f"  Конверсия контакт→КП:     {contacted/max(called,1)*100:.1f}%",
            ])

        return "\n".join(lines)

    # ---- Internal helpers ----

    def _send_whatsapp_kp(self, lead: dict, is_followup: bool = False) -> "WhatsAppResult":
        """Send WhatsApp КП to a lead."""
        phone = lead.get("whatsapp") or lead.get("mobile")
        if not phone:
            return WhatsAppResult(success=False, error="No phone number")

        template = WHATSAPP_FOLLOWUP_TEMPLATE if is_followup else WHATSAPP_KP_TEMPLATE
        message = template.format(
            company_name=lead["company_name"],
            industry=lead.get("industry", ""),
        )

        result = self.whatsapp.send_text(phone, message)

        # Log
        self.conn.execute(
            "INSERT INTO message_log (lead_id, channel, message_type, content, status) VALUES (?, ?, ?, ?, ?)",
            (lead["id"], "whatsapp", "kp" if not is_followup else "followup",
             message[:200], "sent" if result.success else "failed"),
        )
        self.conn.commit()
        # NOTE: In production, replace time.sleep() with a task queue delay
        # (e.g. Celery countdown) to avoid blocking the worker thread.
        time.sleep(self.config.whatsapp_delay_sec)
        return result

    def _send_email_kp(self, lead: dict) -> "EmailResult":
        """Send email КП to a lead."""
        to_email = lead.get("email")
        if not to_email:
            return EmailResult(success=False, error="No email")

        html = build_kp_html(
            company_name=lead["company_name"],
            industry=lead.get("industry", ""),
        )

        result = self.email.send(
            to_email=to_email,
            subject=f"Коммерческое предложение для {lead['company_name']}",
            body_html=html,
        )

        # Log
        self.conn.execute(
            "INSERT INTO message_log (lead_id, channel, message_type, content, status) VALUES (?, ?, ?, ?, ?)",
            (lead["id"], "email", "kp", f"КП для {lead['company_name']}",
             "sent" if result.success else "failed"),
        )
        self.conn.commit()
        # NOTE: In production, replace time.sleep() with a task queue delay
        # (e.g. Celery countdown) to avoid blocking the worker thread.
        time.sleep(self.config.email_delay_sec)
        return result


# ---------- CLI ----------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    conn = init_db()
    engine = FunnelEngine(conn)

    if len(sys.argv) > 1 and sys.argv[1] == "report":
        print(engine.get_funnel_report())
    elif len(sys.argv) > 1 and sys.argv[1] == "batch":
        industry = sys.argv[2] if len(sys.argv) > 2 else None
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        leads = engine.run_batch(industry=industry, limit=limit)
        print(f"Leads to call ({len(leads)}):")
        for l in leads:
            print(f"  [{l['id']}] {l['company_name']} - {l['mobile']} - {l['industry']}")
    else:
        print("Usage:")
        print("  python funnel_engine.py report         - Show funnel stats")
        print("  python funnel_engine.py batch [industry] [limit] - Get leads to call")

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

        # Update status based on call result
        if call_result == CallResult.INTERESTED:
            update_lead_status(self.conn, lead_id, "interested", notes)
            actions["status"] = "interested"

            # Send WhatsApp КП
            if self.config.send_whatsapp_after_call and lead.get("whatsapp"):
                wa_result = self._send_whatsapp_kp(lead)
                actions["whatsapp"] = "sent" if wa_result.success else f"failed: {wa_result.error}"
                if wa_result.success:
                    update_lead_status(self.conn, lead_id, "sent_wa")

            # Send Email КП
            if self.config.send_email_after_call and lead.get("email"):
                em_result = self._send_email_kp(lead)
                actions["email"] = "sent" if em_result.success else f"failed: {em_result.error}"
                if em_result.success:
                    # Update status only if WhatsApp wasn't sent
                    if actions.get("whatsapp") != "sent":
                        update_lead_status(self.conn, lead_id, "sent_email")

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

            # If WhatsApp available, send as fallback
            if lead.get("whatsapp"):
                wa_result = self._send_whatsapp_kp(lead, is_followup=True)
                actions["whatsapp_fallback"] = "sent" if wa_result.success else "failed"

        elif call_result == CallResult.WRONG_NUMBER:
            update_lead_status(self.conn, lead_id, "refused", "wrong number")
            actions["status"] = "wrong_number"

        elif call_result == CallResult.VOICEMAIL:
            update_lead_status(self.conn, lead_id, "called", "voicemail")
            actions["status"] = "voicemail"

        # Log
        self.conn.execute(
            "INSERT INTO call_log (lead_id, call_type, result) VALUES (?, ?, ?)",
            (lead_id, "ai_target", call_result),
        )
        self.conn.commit()

        return actions

    def run_batch(self, industry: str | None = None, limit: int = 50) -> list[dict]:
        """
        Run a batch of calls for new leads.
        Returns list of leads to call (for the Technomax agent).
        """
        query = "SELECT * FROM leads WHERE status = 'new'"
        params = []
        if industry:
            query += " AND industry = ?"
            params.append(industry)
        query += " ORDER BY rating DESC LIMIT ?"
        params.append(limit)

        leads = self.conn.execute(query, params).fetchall()
        return [dict(l) for l in leads]

    def get_callbacks(self) -> list[dict]:
        """Get leads scheduled for callback."""
        return [
            dict(r) for r in self.conn.execute(
                "SELECT * FROM leads WHERE status = 'callback' ORDER BY updated_at"
            ).fetchall()
        ]

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

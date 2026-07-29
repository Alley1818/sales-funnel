"""
Lead sync service: upsert leads from local/external sources into the leads table.
"""
import sqlite3
import logging
from db_conn import get_conn

logger = logging.getLogger("lead_sync")


def sync_leads_from_local(leads: list[dict]) -> dict:
    """
    Upsert a list of lead dicts into the leads table.

    Each lead dict should have: company_name, industry, mobile, whatsapp, email.
    Matching is by mobile number:
      - If a lead with the same mobile exists, update its fields.
      - If no match, insert a new row.

    Returns {"synced": <inserted>, "updated": <updated>}.
    """
    if not leads:
        return {"synced": 0, "updated": 0}

    conn = get_conn()
    synced = 0
    updated = 0

    for lead in leads:
        mobile = (lead.get("mobile") or "").strip()
        if not mobile:
            logger.warning("Skipping lead with empty mobile: %s", lead.get("company_name"))
            continue

        company_name = (lead.get("company_name") or "").strip()
        industry = (lead.get("industry") or "").strip()
        whatsapp = (lead.get("whatsapp") or "").strip()
        email = (lead.get("email") or "").strip()

        existing = conn.execute(
            "SELECT id FROM leads WHERE mobile = ?", (mobile,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE leads
                   SET company_name = ?, industry = ?, whatsapp = ?, email = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE mobile = ?""",
                (company_name, industry, whatsapp, email, mobile),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO leads (company_name, industry, mobile, whatsapp, email, status)
                   VALUES (?, ?, ?, ?, ?, 'new')""",
                (company_name, industry, mobile, whatsapp, email),
            )
            synced += 1

    conn.commit()
    logger.info("Lead sync complete: %d inserted, %d updated", synced, updated)
    return {"synced": synced, "updated": updated}

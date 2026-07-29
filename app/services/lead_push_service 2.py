"""
Lead push service — push leads from Funnel to Technomax autocall tasks.
Uses Technomax REST API: POST /cis/api/v1/telephony/autoCall/import
"""
import csv
import io
import os
import logging
import httpx

logger = logging.getLogger("lead_push")

BASE_URL = "https://login.technomax.com.kz"


def _auth() -> str | None:
    """Authenticate with Technomax and return JWT token."""
    email = os.environ.get("TECHNOMAX_EMAIL", "")
    password = os.environ.get("TECHNOMAX_PASSWORD", "")
    if not email or not password:
        logger.error("TECHNOMAX_EMAIL/TECHNOMAX_PASSWORD not set")
        return None

    try:
        r = httpx.post(
            f"{BASE_URL}/iam/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/app",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("token")
        logger.error("Technomax auth failed: %s %s", r.status_code, r.text[:200])
        return None
    except Exception as e:
        logger.error("Technomax auth error: %s", e)
        return None


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def generate_csv(leads: list[dict]) -> str:
    """Generate CSV for Technomax import.

    Expected columns: name, phone (minimum).
    Additional columns: email, industry, company_name.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "phone", "email", "industry"])

    for lead in leads:
        phone = lead.get("mobile") or lead.get("whatsapp") or lead.get("phone", "")
        # Strip leading + and spaces for Technomax
        phone = phone.replace("+", "").replace(" ", "").replace("-", "")
        writer.writerow([
            lead.get("company_name", ""),
            phone,
            lead.get("email", ""),
            lead.get("industry", ""),
        ])

    return output.getvalue()


def push_leads_to_task(
    leads: list[dict],
    task_name: str,
    agent_id: str = "",
    bot_id: str = "",
    cps: int = 1,
) -> dict:
    """Push leads to Technomax as a new autocall task.

    Args:
        leads: List of lead dicts with at least 'mobile' or 'phone'
        task_name: Name for the autocall task on Technomax
        agent_id: Technomax AI agent UUID (defaultExecData)
        bot_id: Technomax bot UUID (alternative to agent_id)
        cps: Calls per second

    Returns:
        {"ok": True, "task_id": "...", "candidates": N} on success
        {"error": "..."} on failure
    """
    if not leads:
        return {"error": "No leads to push"}

    token = _auth()
    if not token:
        return {"error": "Technomax auth failed"}

    # Generate CSV
    csv_data = generate_csv(leads)

    # Use agent_id or bot_id for defaultExecData
    exec_id = agent_id or bot_id
    if not exec_id:
        # Use default from config or known agent
        exec_id = os.environ.get("TECHNOMAX_DEFAULT_AGENT", "")
    if not exec_id:
        return {"error": "No agent_id or bot_id provided for autocall task"}

    # Determine exec type
    exec_type = "MANUAL"  # default
    if agent_id:
        exec_type = "AI_AGENT"
    elif bot_id:
        exec_type = "BOT"

    # Build form fields
    fields = {
        "name": task_name,
        "defaultExec": exec_type,
        "defaultExecData": exec_id,
        "secondExec": "end",
        "secondExecData": "",
        "cps": str(cps),
        "callStrategy": "STEP_2_STEP",
        "startType": "manual",
        "cidType": "default",
        "cidData": "",
    }

    try:
        r = httpx.post(
            f"{BASE_URL}/cis/api/v1/telephony/autoCall/import",
            data=fields,
            files={"file": ("leads.csv", csv_data, "text/csv")},
            headers=_headers(token),
            timeout=30,
        )

        if r.status_code in (200, 201):
            data = r.json()
            logger.info("Pushed %d leads to Technomax task '%s'", len(leads), task_name)
            return {
                "ok": True,
                "task_id": data.get("id", ""),
                "task_name": task_name,
                "candidates": len(leads),
                "response": data,
            }
        else:
            logger.error("Technomax import failed: %s %s", r.status_code, r.text[:300])
            return {"error": f"Import failed: {r.status_code}", "detail": r.text[:300]}

    except Exception as e:
        logger.error("Technomax import error: %s", e)
        return {"error": str(e)}


def get_providers() -> list:
    """Get available SIP providers from Technomax."""
    token = _auth()
    if not token:
        return []

    try:
        r = httpx.get(
            f"{BASE_URL}/cis/api/v1/telephony/providers",
            headers=_headers(token),
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("items", [])
        return []
    except Exception:
        return []

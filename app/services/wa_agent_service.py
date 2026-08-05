"""
WhatsApp AI Agent Service — auto-replies to incoming WhatsApp messages.

Flow:
1. Incoming message arrives → process_incoming_message(phone, message)
2. Find lead by phone
3. Try new agent module (AIBrain) with tool calling + vector memory
4. If agent module unavailable/fails → legacy fallback:
   a. Get timeline + context, build XML prompt, call LLM, parse JSON actions
   b. Execute actions (send_wa, update_status, schedule_callback, escalate)
5. Send WhatsApp reply via _send_reply
"""
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

import requests

from db_conn import get_conn
from whatsapp_client import WhatsAppClient

logger = logging.getLogger("wa_agent_service")

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _get_openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        cfg = _load_config()
        key = cfg.get("llm", {}).get("api_key", "") or cfg.get("openrouter_api_key", "")
    return key


def _get_llm_config() -> dict:
    """Get LLM configuration from config.json with env overrides."""
    cfg = _load_config()
    llm = cfg.get("llm", {})
    return {
        "provider": os.environ.get("LLM_PROVIDER", llm.get("provider", "ollama")),
        "model": os.environ.get("LLM_MODEL", llm.get("model", "qwen2.5:1.5b")),
        "api_key": llm.get("api_key", ""),
        "base_url": os.environ.get("OLLAMA_URL", llm.get("base_url", "http://ollama:11434")),
        "temperature": llm.get("temperature", 0.3),
        "max_tokens": llm.get("max_tokens", 500),
    }


def _get_fallback_llm_config() -> dict | None:
    """Get fallback LLM configuration from config.json. Returns None if not configured."""
    cfg = _load_config()
    fb = cfg.get("llm_fallback", {})
    if not fb.get("api_key") and not fb.get("base_url"):
        return None
    return {
        "provider": fb.get("provider", "ollama"),
        "model": fb.get("model", ""),
        "api_key": fb.get("api_key", ""),
        "base_url": fb.get("base_url", ""),
        "temperature": fb.get("temperature", 0.3),
        "max_tokens": fb.get("max_tokens", 500),
    }


def _get_wa_agent_prompt() -> str:
    """Get WhatsApp agent prompt from config.json."""
    cfg = _load_config()
    return cfg.get("wa_agent_prompt", "")


# ---------------------------------------------------------------------------
# 1. process_incoming_message — the main entry point
# ---------------------------------------------------------------------------

def process_incoming_message(phone: str, message: str) -> dict:
    """
    Process an incoming WhatsApp message.

    Uses the new agent module (AIBrain) when available for smarter tool-calling
    and context-aware responses. Falls back to legacy LLM pipeline if the agent
    module fails or is not installed.

    Steps:
      - Find lead by phone number
      - Try new agent module (AIBrain) → tool calling, vector memory, scheduling
      - On failure, fall back to legacy prompt → LLM → parse actions pipeline
      - Send WhatsApp reply via _send_reply

    Returns dict with keys: lead_id, reply (str), actions_taken (list), error (str|None)
    """
    lead = _find_lead_by_phone(phone)
    if not lead:
        logger.warning("No lead found for phone %s", phone)
        return {"lead_id": None, "reply": "", "actions_taken": [], "error": "lead_not_found"}

    lead_id = lead["id"]

    # --- Try new agent module (AIBrain) first ---
    try:
        from agent import process_incoming_message as agent_process
        logger.info("Using new agent module (AIBrain) for lead %d", lead_id)

        agent_result = agent_process(lead_id, lead, message)

        reply = agent_result.get("reply", "")
        actions = agent_result.get("actions", [])
        tool_results = agent_result.get("tool_results", [])

        # Send the conversational reply via WhatsApp
        if reply:
            _send_reply(lead_id, reply)

        # Also log to agent_sync for backward compatibility with dashboards/reports
        _log_wa_message(lead_id, "inbound", message)
        if reply:
            _log_wa_message(lead_id, "outbound", reply)

        # Map agent actions to legacy actions_taken format
        actions_taken = []
        for action in actions:
            action_type = action.get("type", "unknown")
            actions_taken.append(action_type)
        for tr in tool_results:
            if isinstance(tr, dict) and not tr.get("success", True):
                actions_taken.append(f"tool_error:{tr.get('error', 'unknown')}")

        return {
            "lead_id": lead_id,
            "reply": reply,
            "actions_taken": actions_taken,
            "error": None,
        }

    except ImportError:
        logger.info("Agent module not installed, using legacy pipeline for lead %d", lead_id)
    except Exception as e:
        logger.warning("Agent module failed for lead %d, falling back to legacy: %s", lead_id, e)

    # --- Legacy fallback ---
    return _process_legacy(lead_id, message, lead)


def _process_legacy(lead_id: int, message: str, lead: dict) -> dict:
    """
    Legacy message processing pipeline (original hardcoded logic).

    Steps:
      - Log inbound message to conversation history
      - Get conversation timeline and lead context
      - Build XML-structured prompt, call LLM, parse JSON response
      - Execute actions (send_wa, update_status, schedule_callback, escalate)

    Returns dict with keys: lead_id, reply, actions_taken, error.
    """
    # Log inbound message to conversation history
    _log_wa_message(lead_id, "inbound", message)

    # Get timeline and context
    timeline = _get_timeline(lead_id)
    context = _get_context(lead_id, lead)

    # Build prompt and call LLM
    system_prompt = build_prompt(lead, timeline, context)
    llm_response = call_llm(system_prompt, message)

    if not llm_response:
        return {"lead_id": lead_id, "reply": "", "actions_taken": [], "error": "llm_failed"}

    # Parse and execute actions
    result = parse_and_execute(lead_id, llm_response)

    return {
        "lead_id": lead_id,
        "reply": result.get("reply", ""),
        "actions_taken": result.get("actions_taken", []),
        "error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# 2. build_prompt — XML-structured prompt for the LLM
# ---------------------------------------------------------------------------

def build_prompt(lead: dict, timeline: list[dict], context: dict) -> str:
    """
    Construct an XML-structured system prompt with:
      - Role definition (from config.json wa_agent_prompt or default)
      - Lead context (company, industry, stage, interests)
      - Conversation history
      - Response rules and action format
    """
    company = lead.get("company_name", "клиент")
    industry = lead.get("industry", "")
    stage = context.get("stage", "new")
    interest = context.get("interest_level", 0)
    needs = context.get("needs", "[]")
    objections = context.get("objections", "[]")

    # Format timeline into history string
    history_lines = []
    for ev in timeline[-10:]:  # last 10 events
        direction = ev.get("direction", "")
        content = ev.get("content", "")
        channel = ev.get("channel", "system")
        if direction == "inbound":
            history_lines.append(f"  [{channel}] Клиент: {content[:200]}")
        elif direction == "outbound":
            history_lines.append(f"  [{channel}] Агент: {content[:200]}")
        elif direction == "event":
            history_lines.append(f"  [{channel}] Событие: {content[:200]}")
    history_text = "\n".join(history_lines) if history_lines else "  (нет истории)"

    # Parse needs/objections from JSON
    try:
        needs_list = json.loads(needs) if isinstance(needs, str) else needs
    except (json.JSONDecodeError, TypeError):
        needs_list = []
    try:
        obj_list = json.loads(objections) if isinstance(objections, str) else objections
    except (json.JSONDecodeError, TypeError):
        obj_list = []

    needs_text = ", ".join(needs_list) if needs_list else "не выявлены"
    obj_text = ", ".join(obj_list) if obj_list else "нет"

    # Interest-based strategy
    if interest <= 3:
        strategy = "Обучай клиента. Не дави на продажу. Расскажи о возможностях AI."
    elif interest <= 6:
        strategy = "Развивай интерес. Покажи конкретные решения. Предложи демо."
    else:
        strategy = "Закрывай на конкретные шаги. Предложи договор, пилот, встречу."

    # Try to use prompt from config.json
    config_prompt = _get_wa_agent_prompt()
    if config_prompt:
        # Inject context variables into the config prompt
        prompt = config_prompt.replace("{company}", company)
        prompt = prompt.replace("{industry}", industry)
        prompt = prompt.replace("{stage}", stage)
        prompt = prompt.replace("{interest}", str(interest))
        prompt = prompt.replace("{needs}", needs_text)
        prompt = prompt.replace("{objections}", obj_text)
        prompt = prompt.replace("{strategy}", strategy)
        prompt = prompt.replace("{history}", history_text)
        return prompt

    # Default prompt (fallback)
    return f"""<role>
Ты — AI-ассистент компании Technomax. Ты общешься с клиентом в WhatsApp.
Твоя задача — помочь клиенту, ответить на вопросы, и продвигать продажу решений Technomax.
Общайся кратко, дружелюбно, по делу. Пиши на русском языке.
</role>

<context>
<company>{company}</company>
<industry>{industry}</industry>
<stage>{stage}</stage>
<interest_level>{interest}/10</interest_level>
<needs>{needs_text}</needs>
<objections>{obj_text}</objections>
</context>

<strategy>
{strategy}
</strategy>

<history>
{history_text}
</history>

<rules>
1. Отвечай кратко (1-3 предложения для WhatsApp)
2. Не задавай вопросы, на которые уже есть ответы в истории
3. Если клиент отказывается — уважай это, не дави
4. Если клиент заинтересован — предлагай конкретные следующие шаги
5. Всегда отвечай в формате JSON с действиями
</rules>

<response_format>
Отвечай ТОЛЬКО валидным JSON (без markdown, без ```):
{{
  "reply": "текст сообщения клиенту",
  "actions": [
    {{"type": "update_status", "status": "новый статус"}},
    {{"type": "schedule_callback", "days": 3}},
    {{"type": "escalate", "reason": "причина эскалации"}}
  ]
}}

Допустимые типы действий:
- update_status: обновить статус лида (new, called, interested, negotiating, closed, lost)
- schedule_callback: запланировать повторный контакт через N дней
- escalate: передать на менеджера (когда клиент хочет поговорить с человеком)
- send_kp: отправить коммерческое предложение (когда клиент просит КП или проявляет интерес). Параметры: industry (отрасль, если известна)

Если действий не нужно — верни пустой массив actions: []
</response_format>"""


# ---------------------------------------------------------------------------
# 3. call_llm — LLM API call (OpenRouter or Ollama)
# ---------------------------------------------------------------------------

def call_llm(system_prompt: str, user_message: str) -> str | None:
    """
    Call LLM API with system prompt and user message.
    Supports Ollama, OpenRouter, Groq, and custom OpenAI-compatible APIs.
    Tries primary provider first, then fallback if configured.
    Returns raw LLM response text or None on failure.
    """
    # Try primary provider
    llm_cfg = _get_llm_config()
    result = _call_llm_provider(system_prompt, user_message, llm_cfg)
    if result is not None:
        return result

    # Try fallback provider
    fallback_cfg = _get_fallback_llm_config()
    if fallback_cfg:
        logger.warning("Primary LLM (%s) failed, trying fallback (%s)",
                       llm_cfg["provider"], fallback_cfg["provider"])
        result = _call_llm_provider(system_prompt, user_message, fallback_cfg)
        if result is not None:
            logger.info("Fallback LLM (%s) succeeded", fallback_cfg["provider"])
            return result
        logger.error("Both primary and fallback LLM providers failed")

    return None


def _call_llm_provider(system_prompt: str, user_message: str, llm_cfg: dict) -> str | None:
    """Route LLM call to the correct provider."""
    provider = llm_cfg["provider"].lower()

    if provider == "ollama":
        # Check if this is Ollama Cloud (has api_key) or local Ollama
        if llm_cfg.get("api_key"):
            return _call_openai_compatible(system_prompt, user_message, llm_cfg)
        return _call_ollama(system_prompt, user_message, llm_cfg)
    elif provider == "groq":
        return _call_groq(system_prompt, user_message, llm_cfg)
    else:
        # openrouter / custom — both use OpenAI-compatible API
        return _call_openai_compatible(system_prompt, user_message, llm_cfg)


def _call_ollama(system_prompt: str, user_message: str, llm_cfg: dict) -> str | None:
    """Call Ollama API."""
    base_url = llm_cfg["base_url"]
    model = llm_cfg["model"]
    
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {
            "temperature": llm_cfg["temperature"],
            "num_predict": llm_cfg["max_tokens"],
        },
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        logger.info("Ollama response received (%d chars)", len(content))
        return content
    except requests.RequestException as e:
        logger.error("Ollama API error: %s", e)
        return None
    except (KeyError, IndexError) as e:
        logger.error("Unexpected Ollama response format: %s", e)
        return None


def _call_groq(system_prompt: str, user_message: str, llm_cfg: dict) -> str | None:
    """Call Groq API (https://api.groq.com/openai/v1)."""
    api_key = llm_cfg.get("api_key", "")
    if not api_key:
        logger.error("No Groq API key configured")
        return None

    base_url = llm_cfg.get("base_url", "https://api.groq.com/openai/v1")
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": llm_cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": llm_cfg["temperature"],
        "max_tokens": llm_cfg["max_tokens"],
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info("Groq response received (%d chars)", len(content))
        return content
    except requests.RequestException as e:
        logger.error("Groq API error: %s", e)
        return None
    except (KeyError, IndexError) as e:
        logger.error("Unexpected Groq response format: %s", e)
        return None


def _call_openai_compatible(system_prompt: str, user_message: str, llm_cfg: dict) -> str | None:
    """Call any OpenAI-compatible API (OpenRouter, custom endpoint)."""
    api_key = llm_cfg.get("api_key") or _get_openrouter_key()
    if not api_key:
        logger.error("No API key configured for OpenAI-compatible provider")
        return None

    base_url = llm_cfg.get("base_url", "https://openrouter.ai/api/v1")
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": llm_cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": llm_cfg["temperature"],
        "max_tokens": llm_cfg["max_tokens"],
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info("LLM response received (%d chars)", len(content))
        return content
    except requests.RequestException as e:
        logger.error("LLM API error: %s", e)
        return None
    except (KeyError, IndexError) as e:
        logger.error("Unexpected LLM response format: %s", e)
        return None


# ---------------------------------------------------------------------------
# 4. parse_and_execute — parse JSON actions and execute them
# ---------------------------------------------------------------------------

def parse_and_execute(lead_id: int, llm_response: str) -> dict:
    """
    Parse JSON actions from LLM response and execute them.

    Supported actions:
      - send_wa: send a WhatsApp message (reply is auto-sent)
      - update_status: change lead status
      - schedule_callback: schedule follow-up
      - escalate: notify manager

    Returns dict with reply text, actions taken, and any error.
    """
    result = {"reply": "", "actions_taken": [], "error": None}

    # Parse JSON from LLM response (handle possible markdown wrapping)
    parsed = _parse_json_response(llm_response)
    if parsed is None:
        logger.warning("Failed to parse LLM response as JSON: %s", llm_response[:200])
        result["error"] = "json_parse_failed"
        # Try to extract plain text as fallback reply
        result["reply"] = _strip_markdown(llm_response)[:500]
        return result

    reply = parsed.get("reply", "")
    actions = parsed.get("actions", [])
    result["reply"] = reply

    # Always send the reply via WhatsApp
    if reply:
        _send_reply(lead_id, reply)

    # Execute additional actions
    for action in actions:
        action_type = action.get("type", "")
        try:
            if action_type == "send_wa":
                # Extra WhatsApp message beyond the reply
                msg = action.get("message", "")
                if msg and msg != reply:
                    _send_reply(lead_id, msg)
                result["actions_taken"].append("send_wa")

            elif action_type == "update_status":
                new_status = action.get("status", "")
                if new_status:
                    _update_lead_status(lead_id, new_status)
                    result["actions_taken"].append(f"update_status:{new_status}")

            elif action_type == "schedule_callback":
                days = action.get("days", 3)
                _schedule_callback(lead_id, days)
                result["actions_taken"].append(f"schedule_callback:{days}d")

            elif action_type == "escalate":
                reason = action.get("reason", "Client request")
                _escalate_to_manager(lead_id, reason)
                result["actions_taken"].append(f"escalate:{reason}")

            elif action_type == "send_kp":
                # Send КП via WhatsApp
                from app.services.kp_service import send_kp
                industry = action.get("industry", lead.get("industry", "general"))
                phone = action.get("phone") or lead.get("mobile") or lead.get("whatsapp", "")
                if phone:
                    kp_result = send_kp(lead_id, lead.get("company_name", ""), industry)
                    result["actions_taken"].append(f"send_kp:{industry}:{phone}")
                else:
                    result["actions_taken"].append("send_kp:no_phone")

            else:
                logger.warning("Unknown action type: %s", action_type)
                result["actions_taken"].append(f"unknown:{action_type}")

        except Exception as e:
            logger.error("Action %s failed: %s", action_type, e)
            result["actions_taken"].append(f"error:{action_type}:{e}")

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_lead_by_phone(phone: str) -> dict | None:
    """Find a lead by phone number (mobile or whatsapp field)."""
    conn = get_conn()
    # Normalize phone
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]

    # Try exact match on mobile, whatsapp, or phone
    row = conn.execute(
        "SELECT * FROM leads WHERE mobile = ? OR whatsapp = ? OR phone = ? LIMIT 1",
        (digits, digits, digits),
    ).fetchone()
    if row:
        return dict(row)

    # Try partial match (last 7 digits)
    if len(digits) >= 7:
        suffix = digits[-7:]
        row = conn.execute(
            "SELECT * FROM leads WHERE mobile LIKE ? OR whatsapp LIKE ? OR phone LIKE ? LIMIT 1",
            (f"%{suffix}", f"%{suffix}", f"%{suffix}"),
        ).fetchone()
        if row:
            return dict(row)

    return None


def _get_timeline(lead_id: int, limit: int = 20) -> list[dict]:
    """Get conversation timeline for a lead."""
    from agent_sync import get_lead_timeline
    return get_lead_timeline(lead_id, limit=limit)


def _get_context(lead_id: int, lead: dict) -> dict:
    """Get lead context (stage, interests, objections)."""
    from agent_sync import get_lead_context
    ctx = get_lead_context(lead_id)
    return ctx


def _log_wa_message(lead_id: int, direction: str, content: str):
    """Log a WhatsApp message to the conversation history."""
    from agent_sync import log_message
    log_message(lead_id, "whatsapp", direction, content[:500])


def _send_reply(lead_id: int, reply: str):
    """Send a WhatsApp reply to the lead and log it."""
    conn = get_conn()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        logger.error("Lead %d not found for sending reply", lead_id)
        return

    lead = dict(lead)
    phone = lead.get("mobile") or lead.get("whatsapp") or lead.get("phone", "")
    if not phone:
        logger.error("No phone number for lead %d", lead_id)
        return

    # Send via WhatsApp client
    try:
        client = WhatsAppClient()
        result = client.send_text(phone, reply)
        if result.success:
            logger.info("Reply sent to lead %d (%s)", lead_id, phone)
            _log_wa_message(lead_id, "outbound", reply)
        else:
            logger.error("Failed to send reply to lead %d: %s", lead_id, result.error)
    except Exception as e:
        logger.error("WhatsApp send error for lead %d: %s", lead_id, e)


def _update_lead_status(lead_id: int, status: str):
    """Update lead status in the database."""
    conn = get_conn()
    conn.execute(
        "UPDATE leads SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, lead_id),
    )
    conn.commit()

    from agent_sync import log_event
    log_event(lead_id, "status_change", f"Статус обновлён → {status}",
              metadata={"new_status": status, "source": "wa_agent"})
    logger.info("Lead %d status updated to %s", lead_id, status)


def _schedule_callback(lead_id: int, days: int):
    """Schedule a follow-up callback."""
    from agent_sync import log_event, update_lead_context
    update_lead_context(lead_id, next_action="schedule_callback")
    log_event(lead_id, "followup", f"Запланирован повторный контакт через {days} дней",
              metadata={"days": days, "source": "wa_agent"})
    logger.info("Lead %d: callback scheduled in %d days", lead_id, days)


def _escalate_to_manager(lead_id: int, reason: str):
    """Escalate lead to human manager via Telegram notification."""
    conn = get_conn()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    company = dict(lead).get("company_name", "Unknown") if lead else "Unknown"

    from agent_sync import log_event, update_lead_context
    update_lead_context(lead_id, next_action="escalate_to_manager")
    log_event(lead_id, "escalate", f"Эскалация менеджеру: {reason}",
              metadata={"reason": reason, "source": "wa_agent"})

    # Try Telegram notification
    try:
        from telegram_notifier import _send_telegram_sync
        msg = (
            f"<b>🔔 Эскалация из WhatsApp</b>\n"
            f"<b>Компания:</b> {company}\n"
            f"<b>Lead ID:</b> {lead_id}\n"
            f"<b>Причина:</b> {reason}"
        )
        _send_telegram_sync(msg)
    except Exception as e:
        logger.error("Telegram escalation notification failed: %s", e)

    logger.info("Lead %d escalated to manager: %s", lead_id, reason)


def _parse_json_response(text: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if not text:
        return None

    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _strip_markdown(text: str) -> str:
    """Strip markdown code blocks, returning plain text."""
    text = re.sub(r"```(?:json)?\s*\n?", "", text)
    text = re.sub(r"```", "", text)
    return text.strip()

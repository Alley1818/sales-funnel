"""
Technomax AI Agent integration — create and manage AI agents on the platform
for WhatsApp conversations synced with the sales funnel.
"""
import os
import json
import logging
import hashlib
import time
import httpx

logger = logging.getLogger("technomax_agent")

BASE_URL = "https://login.technomax.com.kz"
COMPANY_ID = 15913
CREDENTIALS = {
    "email": "technomaxbot@technomax.io",
    "password": "f23dd1585515039652326cd3a08f0036",  # MD5 hash
}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/app",
    }


async def authenticate() -> str | None:
    """Get JWT token from Technomax."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/iam/api/v1/auth/login",
            json=CREDENTIALS,
            headers={"Origin": BASE_URL, "Referer": f"{BASE_URL}/app", "Content-Type": "application/json"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("token")
        logger.error("Auth failed: %s %s", r.status_code, r.text[:200])
        return None


async def list_agents(token: str) -> list:
    """List all AI agents."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE_URL}/agent/api/v1/agents", headers=_headers(token), timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
        return []


async def create_sales_agent(token: str, funnel_api_url: str = "http://YOUR_VPS_IP:5050", agent_api_key: str = "") -> dict | None:
    """
    Create an AI agent on Technomax platform for WhatsApp sales conversations.
    The agent will:
    - Greet clients
    - Present company services
    - Handle objections
    - Send КП via API call to our funnel
    - Sync conversation results back to funnel
    """
    agent_config = {
        "name": "Sales Funnel Agent",
        "config": {
            "maxSessionTime": 600,
            "maxAgentMessageCount": 50,
            "outOfLimitMessage": "К сожалению, время сессии истекло. Наш менеджер свяжется с вами в ближайшее время.",
            "contextVariables": [
                {"name": "phone", "description": "Номер телефона клиента", "defaultValue": ""},
                {"name": "phone_number", "description": "Номер телефона клиента (алиас)", "defaultValue": ""},
                {"name": "company_name", "description": "Название компании клиента", "defaultValue": ""},
                {"name": "industry", "description": "Отрасль клиента", "defaultValue": ""},
                {"name": "lead_id", "description": "ID лида в базе", "defaultValue": ""},
            ],
            "llm": {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "language": "RU",
                "temperature": 0.3,
                "maxOutputTokens": 300,
                "timeout": 10,
                "enableTimeContext": True,
                "systemInstructions": _build_prompt(funnel_api_url),
                "tools": [
                    {
                        "type": "API_REQUEST",
                        "name": "send_kp",
                        "instructions": "Когда клиент проявляет интерес и просит отправить коммерческое предложение. Вызывайте этот инструмент чтобы отправить КП на email клиента.",
                        "params": {
                            "method": "POST",
                            "url": f"{funnel_api_url}/api/agent/send-kp",
                            "headers": {
                                "Content-Type": "application/json",
                                "X-Agent-API-Key": agent_api_key,
                            },
                            "body": {
                                "lead_id": "{lead_id}",
                                "company_name": "{company_name}",
                                "industry": "{industry}",
                                "phone": "{phone}",
                            },
                            "rawBody": False,
                            "timeout": 15,
                            "userMessage": "Отправляю коммерческое предложение...",
                        },
                    },
                    {
                        "type": "API_REQUEST",
                        "name": "log_result",
                        "instructions": "В конце каждого разговора — зафиксируйте результат. Вызывайте с параметром result: interested, callback, refused. Также передайте краткое описание сути разговора в notes.",
                        "params": {
                            "method": "POST",
                            "url": f"{funnel_api_url}/api/agent/log-call",
                            "headers": {
                                "Content-Type": "application/json",
                                "X-Agent-API-Key": agent_api_key,
                            },
                            "body": {
                                "lead_id": "{lead_id}",
                                "channel": "whatsapp",
                                "result": "interested",
                                "notes": "Краткое описание результата",
                            },
                            "rawBody": False,
                            "timeout": 10,
                            "userMessage": "Сохраняю результат...",
                        },
                    },
                    {
                        "type": "END_CONVERSATION",
                        "name": "END_CONVERSATION",
                        "instructions": "Завершите диалог когда: клиент отказался, клиент попросил не звонить, договорились о следующих шагах, или разговор длится более 10 сообщений.",
                        "params": {
                            "userMessage": "Спасибо за уделённое время! Если возникнут вопросы — напишите нам. Хорошего дня!",
                        },
                    },
                ],
            },
            "welcomePhrase": "Здравствуйте! Это Technomax. Мы помогаем бизнесу автоматизировать процессы с помощью AI. Чем могу помочь?",
            "chatStaticMessages": [
                {"text": "Здравствуйте! Меня зовут Асем, я AI-ассистент компании Technomax."},
                {"text": "Мы помогаем компаниям автоматизировать обзвон клиентов, обработку заявок и коммуникации через мессенджеры."},
                {"text": "Расскажите, пожалуйста, с какими задачами сталкивается ваш бизнес?"},
            ],
        },
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/agent/api/v1/agents",
            json=agent_config,
            headers=_headers(token),
            timeout=30,
        )
        if r.status_code in (200, 201):
            data = r.json()
            logger.info("Agent created: %s", data.get("id", data.get("uuid", "?")))
            return data
        else:
            logger.error("Create agent failed: %s %s", r.status_code, r.text[:300])
            return None


def _build_prompt(funnel_api_url: str) -> str:
    return """<role>
Вы — Асем, AI-ассистент компании Technomax. Вы общаетесь с потенциальными клиентами через WhatsApp.
Вы вежливы, профессиональны, говорите кратко и по делу.
</role>

<goal>
1. Представиться и кратко рассказать о Technomax
2. Узнать потребности клиента
3. Предложить подходящие решения
4. Если интересно — отправить КП (вызовите инструмент send_kp)
5. Зафиксировать результат разговора (вызовите инструмент log_result)
</goal>

<rules>
- Отвечайте кратко: 1-3 предложения на сообщение
- Не используйте Markdown-форматирование (WhatsApp не поддерживает)
- Не задавайте больше 1 вопроса за раз
- Если клиент говорит "не интересно" — вежливо завершите
- Если клиент просит перезвонить — зафиксируйте callback
- Если клиент спрашивает цену — скажите что КП будет содержать все детали
- Всегда вызывайте log_result в конце разговора
</rules>

<scenarios>
1. Клиент интересуется услугами → рассказать о возможностях → предложить КП
2. Клиент говорит что уже есть поставщик → спросить можно ли отправить КП для сравнения
3. Клиент говорит "не интересно" → поблагодарить, log_result(refused), завершить
4. Клиент просит перезвонить → log_result(callback), завершить
5. Клиент не отвечает 2+ минуты → отправить напоминание, затем завершить
</scenarios>"""


async def get_agent(token: str, agent_id: str) -> dict | None:
    """Get agent details."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE_URL}/agent/api/v1/agents/{agent_id}", headers=_headers(token), timeout=15)
        if r.status_code == 200:
            return r.json()
        return None


async def update_agent_prompt(token: str, agent_id: str, new_prompt: str) -> bool:
    """Update agent's system instructions."""
    agent = await get_agent(token, agent_id)
    if not agent:
        return False

    agent["config"]["llm"]["systemInstructions"] = new_prompt

    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{BASE_URL}/agent/api/v1/agents/{agent_id}",
            json=agent,
            headers=_headers(token),
            timeout=30,
        )
        return r.status_code == 200

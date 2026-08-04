"""
Pipecat Autocall Agent — integrated with Asterisk ARI.
AI voice agent for automated sales calls.
Port 8082.
"""
import asyncio
import json
import os
import logging
import httpx
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pipecat-agent")

# Configuration
CONFIG = {
    "asterisk": {
        "host": os.getenv("ASTERISK_HOST", "asterisk"),
        "port": int(os.getenv("ASTERISK_PORT", "8089")),
        "ari_user": os.getenv("ASTERISK_ARI_USER", "sales-funnel"),
        "ari_password": os.getenv("ASTERISK_ARI_PASSWORD", ""),
    },
    "sip": {
        "host": os.getenv("SIP_HOST", ""),
        "port": int(os.getenv("SIP_PORT", "5060")),
    },
    "stt": {
        "provider": os.getenv("STT_PROVIDER", "deepgram"),
        "api_key": os.getenv("DEEPGRAM_API_KEY", ""),
    },
    "tts": {
        "provider": os.getenv("TTS_PROVIDER", "elevenlabs"),
        "api_key": os.getenv("ELEVENLABS_API_KEY", ""),
        "voice_id": os.getenv("TTS_VOICE_ID", ""),
    },
    "llm": {
        "provider": os.getenv("LLM_PROVIDER", "openrouter"),
        "api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "model": os.getenv("LLM_MODEL", "anthropic/claude-3-5-sonnet"),
    },
    "agent": {
        "name": os.getenv("AGENT_NAME", "Асем"),
        "company": os.getenv("COMPANY_NAME", "Technomax"),
        "language": os.getenv("AGENT_LANGUAGE", "ru"),
    },
    "funnel": {
        "api_url": os.getenv("FUNNEL_API_URL", "http://host.docker.internal:5050"),
    },
}

ARI_BASE = f"http://{CONFIG['asterisk']['host']}:{CONFIG['asterisk']['port']}/ari"


# Data models
class Lead(BaseModel):
    phone: str
    name: Optional[str] = None
    company_name: Optional[str] = None
    industry: Optional[str] = None
    metadata: Optional[dict] = None


class CallRequest(BaseModel):
    lead: Lead
    prompt_override: Optional[str] = None


class CallStatus(BaseModel):
    call_id: str
    status: str
    lead: Lead
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    result: Optional[str] = None
    transcript: Optional[str] = None


# In-memory storage
calls_db: dict[str, dict] = {}

# Sales call prompt
SALES_PROMPT = """Вы - {agent_name} из компании {company_name}.
Вы звоните чтобы предложить сотрудничество.

ВАЖНЫЕ ПРАВИЛА:
1. Представьтесь в начале разговора
2. Кратко расскажите о компании и услугах
3. Узнайте потребности клиента
4. Предложите решение
5. Если интересно — скажите что отправите КП на WhatsApp/email
6. Если отказ — поблагодарите и завершите разговор

ОБРАБОТКА ВОЗРАЖЕНИЙ:
- "Не интересно" → Уточните почему, предложите альтернативу
- "Нет времени" → Спросите когда удобно перезвонить
- "Уже есть поставщик" → Сравните условия
- "Дорого" → Расскажите о гибких тарифах
- "Пришлите информацию" → Спросите WhatsApp или email

ИНФОРМАЦИЯ О КЛИЕНТЕ:
- Компания: {company_name}
- Отрасль: {industry}
"""


# ---- Asterisk ARI Client ----

class AsteriskARI:
    """Client for Asterisk REST Interface."""

    def __init__(self):
        self.base = ARI_BASE
        self.auth = None
        if CONFIG["asterisk"]["ari_user"]:
            self.auth = httpx.BasicAuth(
                CONFIG["asterisk"]["ari_user"],
                CONFIG["asterisk"]["ari_password"],
            )

    async def health(self) -> bool:
        """Check if Asterisk ARI is reachable."""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self.base}/asterisk/info", auth=self.auth, timeout=5)
                return r.status_code == 200
        except Exception:
            return False

    async def originate_call(self, phone: str, endpoint: str = "PJSIP") -> dict:
        """
        Originate an outbound call via ARI.
        Returns channel info on success.
        """
        # Format phone number for SIP
        if not phone.startswith("+"):
            phone = f"+{phone}"

        # Strip + for SIP trunk (Kazakhstan format: 7XXXXXXXXXX)
        sip_phone = phone.lstrip("+")

        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{self.base}/channels",
                    params={
                        "endpoint": f"{endpoint}/{sip_phone}@tele2-endpoint",
                        "extension": "200",
                        "context": "outbound",
                        "priority": "1",
                        "callerId": CONFIG["agent"]["company"],
                        "timeout": 60,
                    },
                    auth=self.auth,
                    timeout=10,
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    logger.info("Call originated: %s -> %s", data.get("id"), phone)
                    return {"ok": True, "channel_id": data.get("id"), "state": data.get("state")}
                else:
                    logger.error("ARI originate failed: %s %s", r.status_code, r.text)
                    return {"ok": False, "error": r.text}
        except Exception as e:
            logger.error("ARI originate error: %s", e)
            return {"ok": False, "error": str(e)}

    async def hangup(self, channel_id: str) -> bool:
        """Hang up a channel."""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.delete(
                    f"{self.base}/channels/{channel_id}",
                    auth=self.auth,
                    timeout=5,
                )
                return r.status_code == 200
        except Exception:
            return False

    async def get_channels(self) -> list:
        """List active channels."""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self.base}/channels", auth=self.auth, timeout=5)
                return r.json() if r.status_code == 200 else []
        except Exception:
            return []


ari = AsteriskARI()


# ---- STT / LLM / TTS Pipeline ----

class SimplePipeline:
    """
    STT (faster-whisper) -> LLM (OpenRouter) -> TTS (edge-tts) pipeline.
    All free, runs locally.
    """

    def __init__(self):
        self.llm_key = CONFIG["llm"]["api_key"]
        self.model = CONFIG["llm"]["model"]
        self.language = CONFIG["agent"]["language"]

    def transcribe(self, audio_bytes: bytes) -> str:
        """STT: audio bytes -> text."""
        try:
            from stt import transcribe_audio
            return transcribe_audio(audio_bytes, language=self.language)
        except Exception as e:
            logger.error("STT error: %s", e)
            return ""

    async def get_response(self, user_text: str, system_prompt: str, history: list[dict] = None) -> str:
        """LLM: user text -> response text."""
        if not self.llm_key:
            return "Извините, сервис временно недоступен."

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.llm_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": 200,
                        "temperature": 0.3,
                    },
                    timeout=15,
                )
                data = r.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("LLM error: %s", e)
            return "Извините, возникла техническая проблема."

    async def speak(self, text: str) -> bytes:
        """TTS: text -> MP3 audio bytes."""
        try:
            from tts import synthesize_text
            return await synthesize_text(text)
        except Exception as e:
            logger.error("TTS error: %s", e)
            return b""

    async def process_conversation_turn(self, audio_bytes: bytes, system_prompt: str, history: list[dict] = None) -> tuple[str, str, bytes]:
        """
        Full pipeline: audio -> text -> LLM response -> audio.
        Returns (user_text, response_text, response_audio).
        """
        # 1. STT
        user_text = self.transcribe(audio_bytes)
        if not user_text:
            return ("", "", b"")

        # 2. LLM
        response_text = await self.get_response(user_text, system_prompt, history)

        # 3. TTS
        response_audio = await self.speak(response_text)

        return (user_text, response_text, response_audio)


pipeline = SimplePipeline()


# ---- Call Processing ----

async def process_call(call_id: str, lead: Lead, prompt: str):
    """Process a single outbound call."""
    try:
        calls_db[call_id]["status"] = "calling"
        calls_db[call_id]["start_time"] = datetime.now()

        logger.info("Calling %s (%s)", lead.phone, lead.company_name or "unknown")

        # Check if SIP trunk is configured
        if not CONFIG["sip"]["host"]:
            # No SIP trunk — simulate call for testing
            logger.warning("No SIP trunk configured, simulating call")
            await asyncio.sleep(3)
            result = "interested"
            transcript = f"[Simulated] Звонок на {lead.phone}. Клиент заинтересован."
        else:
            # Real call via Asterisk ARI
            origination = await ari.originate_call(lead.phone)

            if not origination.get("ok"):
                calls_db[call_id]["status"] = "failed"
                calls_db[call_id]["result"] = f"origination_failed: {origination.get('error')}"
                calls_db[call_id]["end_time"] = datetime.now()
                logger.error("Call failed: %s", origination.get("error"))
                return

            channel_id = origination["channel_id"]

            # Wait for call to complete (max 3 minutes)
            # In production, this would be driven by WebSocket events
            max_wait = 180
            waited = 0
            while waited < max_wait:
                await asyncio.sleep(5)
                waited += 5
                channels = await ari.get_channels()
                active = [c for c in channels if c.get("id") == channel_id]
                if not active:
                    break
                state = active[0].get("state")
                if state in ("Down", "Destroyed"):
                    break

            # Determine result based on call duration and events
            # In production, parse LLM conversation for result classification
            result = "no_answer"
            transcript = f"Звонок на {lead.phone} завершён"

        # Update call record
        calls_db[call_id]["status"] = "completed"
        calls_db[call_id]["end_time"] = datetime.now()
        calls_db[call_id]["result"] = result
        calls_db[call_id]["transcript"] = transcript

        # Notify sales funnel API
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{CONFIG['funnel']['api_url']}/api/call/result",
                    json={
                        "lead_id": lead.metadata.get("lead_id") if lead.metadata else None,
                        "result": result,
                        "notes": transcript[:500],
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    logger.info("Funnel API notified for lead %s", lead.metadata.get("lead_id"))
                else:
                    logger.warning("Funnel API returned %s", resp.status_code)
        except Exception as e:
            logger.warning("Failed to notify funnel API: %s", e)

        logger.info("Call %s completed: %s", call_id, result)

    except Exception as e:
        calls_db[call_id]["status"] = "failed"
        calls_db[call_id]["end_time"] = datetime.now()
        calls_db[call_id]["result"] = f"error: {str(e)}"
        logger.error("Call %s failed: %s", call_id, e)


# ---- FastAPI App ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    asterisk_ok = await ari.health()
    logger.info("Starting Pipecat Autocall Agent on port 8082")
    logger.info("Agent: %s @ %s", CONFIG["agent"]["name"], CONFIG["agent"]["company"])
    logger.info("Asterisk ARI: %s", "connected" if asterisk_ok else "NOT CONNECTED")
    logger.info("SIP trunk: %s", CONFIG["sip"]["host"] or "NOT CONFIGURED (simulation mode)")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Pipecat Autocall Agent",
    description="AI voice agent for sales calls via Asterisk",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    asterisk_ok = await ari.health()
    return {
        "status": "ok",
        "agent": CONFIG["agent"]["name"],
        "asterisk": "connected" if asterisk_ok else "disconnected",
        "sip_trunk": CONFIG["sip"]["host"] or "not_configured",
    }


@app.post("/calls", response_model=CallStatus)
async def create_call(request: CallRequest, bg: BackgroundTasks):
    """Start a new outbound call."""
    call_id = f"call_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(calls_db)}"

    prompt = request.prompt_override or SALES_PROMPT.format(
        agent_name=CONFIG["agent"]["name"],
        company_name=CONFIG["agent"]["company"],
        company_name2=request.lead.company_name or "ваша компания",
        industry=request.lead.industry or "ваша отрасль",
    )

    call = {
        "call_id": call_id,
        "status": "queued",
        "lead": request.lead.model_dump(),
        "start_time": None,
        "end_time": None,
        "result": None,
        "transcript": None,
    }
    calls_db[call_id] = call

    bg.add_task(process_call, call_id, request.lead, prompt)
    return CallStatus(**call)


@app.get("/calls/{call_id}", response_model=CallStatus)
async def get_call(call_id: str):
    if call_id not in calls_db:
        raise HTTPException(404, "Call not found")
    return CallStatus(**calls_db[call_id])


@app.get("/calls")
async def list_calls(limit: int = 100):
    return [CallStatus(**c) for c in list(calls_db.values())[:limit]]


@app.post("/calls/batch")
async def start_batch(leads: list[Lead], bg: BackgroundTasks, max_calls: int = 10):
    """Start batch calls from lead list."""
    started = []
    for lead in leads[:max_calls]:
        call_id = f"call_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(calls_db)}"
        prompt = SALES_PROMPT.format(
            agent_name=CONFIG["agent"]["name"],
            company_name=CONFIG["agent"]["company"],
            company_name2=lead.company_name or "ваша компания",
            industry=lead.industry or "ваша отрасль",
        )
        calls_db[call_id] = {
            "call_id": call_id,
            "status": "queued",
            "lead": lead.model_dump(),
            "start_time": None,
            "end_time": None,
            "result": None,
            "transcript": None,
        }
        bg.add_task(process_call, call_id, lead, prompt)
        started.append(call_id)

    return {"started": len(started), "call_ids": started}


@app.get("/results")
async def get_results():
    return {
        "total": len(calls_db),
        "completed": sum(1 for c in calls_db.values() if c["status"] == "completed"),
        "failed": sum(1 for c in calls_db.values() if c["status"] == "failed"),
        "calling": sum(1 for c in calls_db.values() if c["status"] == "calling"),
        "queued": sum(1 for c in calls_db.values() if c["status"] == "queued"),
    }


@app.get("/config")
async def get_config():
    return {
        "agent": CONFIG["agent"],
        "stt": CONFIG["stt"]["provider"],
        "tts": CONFIG["tts"]["provider"],
        "llm": CONFIG["llm"]["model"],
        "asterisk": "connected" if await ari.health() else "disconnected",
        "sip_trunk": CONFIG["sip"]["host"] or "not_configured",
    }


# ---- WebSocket for Asterisk media ----

@app.websocket("/ws/media")
async def media_websocket(ws: WebSocket):
    """
    WebSocket endpoint for Asterisk audio streaming.
    Asterisk connects here when a call is answered.
    Audio flows: Asterisk → STT → LLM → TTS → Asterisk
    """
    await ws.accept()
    logger.info("Asterisk media WebSocket connected")

    transcript = []
    history = []  # LLM conversation history
    system_prompt = SALES_PROMPT.format(
        agent_name=CONFIG["agent"]["name"],
        company_name=CONFIG["agent"]["company"],
        company_name2="клиент",
        industry="",
    )

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "media":
                # Audio chunk from Asterisk — run full STT→LLM→TTS pipeline
                audio_b64 = msg.get("data", "")
                if audio_b64:
                    import base64
                    audio_bytes = base64.b64decode(audio_b64)
                    user_text, response_text, response_audio = await pipeline.process_conversation_turn(
                        audio_bytes, system_prompt, history
                    )
                    if user_text:
                        transcript.append({"role": "user", "text": user_text})
                        history.append({"role": "user", "content": user_text})
                    if response_text:
                        transcript.append({"role": "assistant", "text": response_text})
                        history.append({"role": "assistant", "content": response_text})
                    if response_audio:
                        import base64
                        await ws.send_text(json.dumps({
                            "type": "media",
                            "data": base64.b64encode(response_audio).decode(),
                        }))

            elif msg.get("type") == "start":
                # Call started — send greeting
                greeting = f"Здравствуйте! Это {CONFIG['agent']['name']} из компании {CONFIG['agent']['company']}. Могу я услышать руководителя?"
                tts_audio = await pipeline.speak(greeting)
                transcript.append({"role": "assistant", "text": greeting})
                history.append({"role": "assistant", "content": greeting})
                if tts_audio:
                    import base64
                    await ws.send_text(json.dumps({
                        "type": "media",
                        "data": base64.b64encode(tts_audio).decode(),
                    }))
                else:
                    await ws.send_text(json.dumps({"type": "media", "text": greeting}))

            elif msg.get("type") == "transcript":
                # STT result from Asterisk-side STT
                user_text = msg.get("text", "")
                transcript.append({"role": "user", "text": user_text})
                history.append({"role": "user", "content": user_text})

                # Get LLM response
                response = await pipeline.get_response(user_text, system_prompt, history)
                transcript.append({"role": "assistant", "text": response})
                history.append({"role": "assistant", "content": response})

                # TTS and send back
                tts_audio = await pipeline.speak(response)
                if tts_audio:
                    import base64
                    await ws.send_text(json.dumps({
                        "type": "media",
                        "data": base64.b64encode(tts_audio).decode(),
                    }))
                else:
                    await ws.send_text(json.dumps({"type": "media", "text": response}))

    except WebSocketDisconnect:
        logger.info("Asterisk media WebSocket disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8082, log_level="info")


# ---- Browser chat endpoint ----

class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = None

class ChatResponse(BaseModel):
    reply: str
    audio_b64: Optional[str] = None
    transcript: list

@app.post("/chat", response_model=ChatResponse)
async def browser_chat(req: ChatRequest):
    """Text chat with voice response — for browser testing."""
    import base64

    system_prompt = SALES_PROMPT.format(
        agent_name=CONFIG["agent"]["name"],
        company_name=CONFIG["agent"]["company"],
        company_name2="клиент",
        industry="",
    )

    history = req.history or []
    history.append({"role": "user", "content": req.message})

    # Get LLM response
    response_text = await pipeline.get_response(req.message, system_prompt, history)
    history.append({"role": "assistant", "content": response_text})

    # Generate TTS
    audio_b64 = None
    try:
        audio_bytes = await pipeline.speak(response_text)
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode()
    except Exception as e:
        logger.warning("TTS failed: %s", e)

    transcript = [{"role": m["role"], "text": m["content"]} for m in history]

    return ChatResponse(
        reply=response_text,
        audio_b64=audio_b64,
        transcript=transcript,
    )

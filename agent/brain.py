"""
AIBrain — LLM integration with tool calling.

Flow:
1. Receive message from client
2. Build context (lead profile, history, available tools)
3. Call LLM with context
4. Parse LLM response (text + tool calls)
5. Execute tools
6. Return response
"""
import json
import logging
import re
from typing import Optional
import requests

from agent.memory import VectorMemory
from agent.context import ContextManager
from agent.tools import AgentTools

logger = logging.getLogger("agent.brain")


class AIBrain:
    """AI Brain — LLM integration with tool calling."""

    def __init__(self, memory: VectorMemory = None):
        self.memory = memory or VectorMemory()
        self.context = ContextManager(self.memory)
        self.tools = AgentTools(self.memory, self.context)

    def process_message(self, lead_id: int, lead_data: dict, message: str) -> dict:
        """
        Process an incoming message and return a response.

        Returns:
            {
                "reply": "text to send to client",
                "actions": [{"type": "...", ...}],
                "tool_results": [...]
            }
        """
        # 1. Get full context
        ctx = self.context.get_full_context(lead_id, lead_data)

        # 2. Record incoming message
        self.context.record_message(lead_id, "user", message)

        # 3. Build prompt
        system_prompt = self._build_system_prompt(ctx)
        conversation = self.context.get_conversation_summary(lead_id)

        # 4. Call LLM
        llm_response = self._call_llm(system_prompt, conversation, message)

        if not llm_response:
            return {
                "reply": "Извините, произошла ошибка. Попробуйте позже.",
                "actions": [],
                "tool_results": [],
            }

        # 5. Parse response
        parsed = self._parse_response(llm_response)

        # 6. Execute tools
        tool_results = []
        for action in parsed.get("actions", []):
            result = self._execute_tool(lead_id, lead_data, action)
            tool_results.append(result)

        # 7. Record response
        reply = parsed.get("reply", "")
        if reply:
            self.context.record_message(lead_id, "assistant", reply)

        return {
            "reply": reply,
            "actions": parsed.get("actions", []),
            "tool_results": tool_results,
        }

    def _build_system_prompt(self, ctx: dict) -> str:
        """Build system prompt with context."""
        # Try to get prompt from config
        config = self._load_config()
        base_prompt = config.get("wa_agent_prompt", "")

        if not base_prompt:
            base_prompt = self._default_prompt()

        # Inject context variables (handle None values)
        prompt = base_prompt.replace("{company}", str(ctx.get("company") or "клиент"))
        prompt = prompt.replace("{industry}", str(ctx.get("industry") or ""))
        prompt = prompt.replace("{stage}", str(ctx.get("stage") or "new"))
        prompt = prompt.replace("{interest}", str(ctx.get("interest_level") or 0))
        needs = ctx.get("needs") or []
        prompt = prompt.replace("{needs}", ", ".join(needs) if isinstance(needs, list) else str(needs) or "не выявлены")
        objections = ctx.get("objections") or []
        prompt = prompt.replace("{objections}", ", ".join(objections) if isinstance(objections, list) else str(objections) or "нет")

        # Add available tools description
        tools_desc = self._describe_tools()
        prompt = prompt.replace("{tools}", tools_desc)

        return prompt

    def _default_prompt(self) -> str:
        return """<role>
Ты — AI-ассистент компании Technomax. Ты общешься с клиентом в WhatsApp.
Твоя задача — помочь клиенту, ответить на вопросы, и продвигать продажу решений Technomax.
Общайся кратко, дружелюбно, по делу. Пиши на русском языке.
</role>

<context>
Компания: {company}
Отрасль: {industry}
Стадия: {stage}
Интерес: {interest}/10
Потребности: {needs}
Возражения: {objections}
</context>

<tools>
Ты можешь выполнять следующие действия:
{tools}
</tools>

<rules>
1. Отвечай кратко (1-3 предложения для WhatsApp)
2. Если клиент заинтересован — предложи отправить КП или презентацию
3. Если клиент не отвечает долго — запланируй напоминание
4. Если клиент хочет поговорить с человеком — эскалируй менеджеру
5. Всегда отвечай в формате JSON
</rules>

<response_format>
Отвечай ТОЛЬКО валидным JSON:
{{
  "reply": "текст сообщения клиенту",
  "actions": [
    {{"type": "send_kp", "industry": "название отрасли"}},
    {{"type": "schedule_followup", "days": 2}},
    {{"type": "escalate_manager", "reason": "причина"}}
  ]
}}
Если действий не нужно — actions: []
</response_format>"""

    def _describe_tools(self) -> str:
        """Describe available tools for the LLM."""
        return """- send_kp: Отправить коммерческое предложение. Параметры: industry (отрасль)
- send_presentation: Отправить презентацию. Параметры: industry (отрасль)
- update_status: Обновить статус лида. Параметры: status (new/called/interested/negotiating/closed/lost)
- schedule_followup: Запланировать напоминание. Параметры: days (через сколько дней)
- escalate_manager: Передать менеджеру. Параметры: reason (причина)"""

    def _call_llm(self, system_prompt: str, conversation: str, user_message: str) -> Optional[str]:
        """Call LLM API."""
        config = self._load_config()
        llm = config.get("llm", {})

        provider = llm.get("provider", "ollama")
        if provider == "ollama":
            return self._call_ollama(llm, system_prompt, conversation, user_message)
        else:
            return self._call_openrouter(llm, system_prompt, conversation, user_message)

    def _call_ollama(self, llm_config: dict, system_prompt: str, conversation: str, user_message: str) -> Optional[str]:
        """Call Ollama API."""
        base_url = llm_config.get("base_url", "http://ollama:11434")
        model = llm_config.get("model", "qwen2.5:1.5b")

        # Build messages with conversation history
        messages = [{"role": "system", "content": system_prompt}]
        if conversation:
            messages.append({"role": "user", "content": f"История разговора:\n{conversation}"})
            messages.append({"role": "assistant", "content": "Понял, продолжаем разговор."})
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": llm_config.get("temperature", 0.3),
                "num_predict": llm_config.get("max_tokens", 500),
            },
        }

        try:
            resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
        except Exception as e:
            logger.error("Ollama error: %s", e)
            return None

    def _call_openrouter(self, llm_config: dict, system_prompt: str, conversation: str, user_message: str) -> Optional[str]:
        """Call OpenRouter API."""
        api_key = llm_config.get("api_key", "")
        if not api_key:
            logger.error("No OpenRouter API key")
            return None

        messages = [{"role": "system", "content": system_prompt}]
        if conversation:
            messages.append({"role": "user", "content": f"История разговора:\n{conversation}"})
            messages.append({"role": "assistant", "content": "Понял, продолжаем разговор."})
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": llm_config.get("model", "xiaomi/mimo-v2.5-pro"),
            "messages": messages,
            "temperature": llm_config.get("temperature", 0.3),
            "max_tokens": llm_config.get("max_tokens", 500),
        }

        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("OpenRouter error: %s", e)
            return None

    def _parse_response(self, llm_response: str) -> dict:
        """Parse LLM response (JSON with reply and actions)."""
        # Try direct JSON parse
        try:
            return json.loads(llm_response.strip())
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", llm_response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding JSON object
        match = re.search(r"\{.*\}", llm_response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # Fallback: treat as plain text reply
        return {"reply": llm_response[:500], "actions": []}

    def _execute_tool(self, lead_id: int, lead_data: dict, action: dict) -> dict:
        """Execute a tool action."""
        action_type = action.get("type", "")
        phone = lead_data.get("mobile") or lead_data.get("whatsapp", "")

        if action_type == "send_kp":
            industry = action.get("industry", lead_data.get("industry", "general"))
            return self.tools.send_kp(lead_id, industry, phone)

        elif action_type == "send_presentation":
            industry = action.get("industry", lead_data.get("industry", "general"))
            return self.tools.send_presentation(lead_id, industry, phone)

        elif action_type == "update_status":
            return self.tools.update_status(lead_id, action.get("status", "interested"))

        elif action_type == "schedule_followup":
            return self.tools.schedule_followup(lead_id, action.get("days", 2), "agent_scheduled")

        elif action_type == "escalate_manager":
            return self.tools.escalate_manager(lead_id, action.get("reason", "Client request"))

        else:
            logger.warning("Unknown tool: %s", action_type)
            return {"success": False, "error": f"Unknown tool: {action_type}"}

    @staticmethod
    def _load_config() -> dict:
        import os
        from pathlib import Path
        config_path = Path(os.environ.get("CONFIG_PATH", str(Path(__file__).parent.parent / "config.json")))
        if config_path.exists():
            return json.loads(config_path.read_text())
        return {}

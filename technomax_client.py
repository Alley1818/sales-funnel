"""
Technomax API client — fetch call data, statuses, bot info.
Uses the platform REST API at login.technomax.com.kz.
"""
import os
import json
import logging
import hashlib
import time
import httpx

logger = logging.getLogger("technomax")

BASE_URL = "https://login.technomax.com.kz"
COMPANY_ID = 15913


class TechnomaxClient:
    """Client for Technomax platform REST API."""

    def __init__(self):
        self.email = os.getenv("TECHNOMAX_EMAIL", "technomaxbot@technomax.io")
        self.password = os.getenv("TECHNOMAX_PASSWORD", "f23dd1585515039652326cd3a08f0036")
        self.token = None
        self.token_time = 0

    def _md5(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    async def _ensure_token(self):
        """Get or refresh JWT token."""
        if self.token and (time.time() - self.token_time) < 600:
            return

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{BASE_URL}/iam/api/v1/auth/login",
                json={"email": self.email, "password": self.password},
                headers={
                    "Origin": BASE_URL,
                    "Referer": f"{BASE_URL}/app",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("token")
                self.token_time = time.time()
                logger.info("Technomax auth OK")
            else:
                logger.error("Technomax auth failed: %s %s", r.status_code, r.text[:200])
                self.token = None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ---- Autocall Tasks ----

    async def get_autocall_tasks(self, page: int = 0, limit: int = 20) -> dict:
        """List autocall (voice bot) tasks."""
        await self._ensure_token()
        if not self.token:
            return {"error": "auth_failed"}

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{BASE_URL}/cis/api/v1/telephony/autoCall",
                params={"page": page, "limit": limit, "sort": "-updatedAt"},
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                self.token = None
                return await self.get_autocall_tasks(page, limit)
            else:
                return {"error": r.status_code, "body": r.text[:200]}

    async def get_autocall_detail(self, task_id: str) -> dict:
        """Get autocall task details with call stats."""
        await self._ensure_token()
        if not self.token:
            return {"error": "auth_failed"}

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{BASE_URL}/cis/api/v1/telephony/autoCall/{task_id}",
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                self.token = None
                return await self.get_autocall_detail(task_id)
            else:
                return {"error": r.status_code}

    # ---- Bots ----

    async def get_bots(self) -> list:
        """List bot scenarios."""
        await self._ensure_token()
        if not self.token:
            return []

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{BASE_URL}/bot/api/v1/bots",
                params={
                    "page": 0, "limit": 50,
                    "fields": "id,name,type,language,updatedAt",
                    "deleted": 0,
                    "companyId": COMPANY_ID,
                },
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code == 200:
                return r.json().get("items", [])
            return []

    # ---- AI Agents ----

    async def get_ai_agents(self) -> list:
        """List AI agents."""
        await self._ensure_token()
        if not self.token:
            return []

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{BASE_URL}/agent/api/v1/agents",
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
            return []

    # ---- Call Results / Statuses ----

    async def get_call_statuses(self) -> list:
        """Get available call result statuses."""
        await self._ensure_token()
        if not self.token:
            return []

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{BASE_URL}/bot/api/v1/statusResultBlock",
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
            return []

    # ---- Summary ----

    async def get_dashboard_data(self) -> dict:
        """Get all data needed for the dashboard."""
        tasks_result = await self.get_autocall_tasks()
        tasks = tasks_result.get("items", []) if isinstance(tasks_result, dict) else []

        bots = await self.get_bots()
        agents = await self.get_ai_agents()
        statuses = await self.get_call_statuses()

        # Aggregate task stats
        total_calls = 0
        total_candidates = 0
        for t in tasks:
            total_calls += t.get("callCount", 0) or 0
            total_candidates += t.get("candidateCount", 0) or 0

        return {
            "tasks": tasks[:10],
            "task_count": len(tasks),
            "bots": [{"id": b.get("id"), "name": b.get("name"), "type": b.get("type")} for b in bots[:10]],
            "bot_count": len(bots),
            "agents": [{"id": a.get("id"), "name": a.get("name")} for a in agents[:10]],
            "agent_count": len(agents),
            "statuses": statuses,
            "total_calls": total_calls,
            "total_candidates": total_candidates,
        }


    # ---- Synchronous variants (for Flask routes) ----

    def _ensure_token_sync(self, client: httpx.Client):
        """Synchronous token refresh."""
        if self.token and (time.time() - self.token_time) < 600:
            return
        r = client.post(
            f"{BASE_URL}/iam/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
            headers={"Origin": BASE_URL, "Referer": f"{BASE_URL}/app", "Content-Type": "application/json"},
            timeout=15,
        )
        if r.status_code == 200:
            self.token = r.json().get("token")
            self.token_time = time.time()
        else:
            self.token = None

    def get_autocall_tasks_sync(self, client: httpx.Client, page: int = 0, limit: int = 20) -> dict:
        if not self.token:
            return {"error": "auth_failed"}
        r = client.get(
            f"{BASE_URL}/cis/api/v1/telephony/autoCall",
            params={"page": page, "limit": limit, "sort": "-updatedAt"},
            headers=self._headers(),
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        return {"error": r.status_code, "body": r.text[:200]}

    def get_autocall_detail_sync(self, client: httpx.Client, task_id: str) -> dict:
        if not self.token:
            return {"error": "auth_failed"}
        r = client.get(
            f"{BASE_URL}/cis/api/v1/telephony/autoCall/{task_id}",
            headers=self._headers(),
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        return {"error": r.status_code}

    def get_dashboard_data_sync(self, client: httpx.Client) -> dict:
        tasks_result = self.get_autocall_tasks_sync(client)
        tasks = tasks_result.get("items", []) if isinstance(tasks_result, dict) else []
        total_calls = sum(t.get("callCount", 0) or 0 for t in tasks)
        total_candidates = sum(t.get("candidateCount", 0) or 0 for t in tasks)
        return {
            "tasks": tasks[:10],
            "task_count": len(tasks),
            "total_calls": total_calls,
            "total_candidates": total_candidates,
        }


# Singleton
technomax = TechnomaxClient()

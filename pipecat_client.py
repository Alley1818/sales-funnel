"""
Pipecat Agent client — trigger AI calls from the sales funnel.
"""
import os
import logging
import requests
from dataclasses import dataclass

logger = logging.getLogger("pipecat_client")


@dataclass
class PipecatConfig:
    base_url: str = "http://localhost:8082"

    @classmethod
    def from_env(cls) -> "PipecatConfig":
        return cls(
            base_url=os.environ.get("PIPECAT_URL", "http://localhost:8082").rstrip("/"),
        )


@dataclass
class CallResult:
    call_id: str
    status: str
    result: str | None = None
    error: str | None = None


class PipecatClient:
    """Client for triggering AI voice calls via Pipecat agent."""

    def __init__(self, config: PipecatConfig | None = None):
        self.config = config or PipecatConfig.from_env()

    def health(self) -> bool:
        """Check if Pipecat agent is running."""
        try:
            r = requests.get(f"{self.config.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def create_call(
        self,
        phone: str,
        company_name: str = "",
        industry: str = "",
        lead_id: int | None = None,
    ) -> CallResult:
        """Start a single AI call."""
        try:
            r = requests.post(
                f"{self.config.base_url}/calls",
                json={
                    "lead": {
                        "phone": phone,
                        "company_name": company_name,
                        "industry": industry,
                        "metadata": {"lead_id": lead_id} if lead_id else None,
                    },
                },
                timeout=10,
            )
            data = r.json()
            return CallResult(
                call_id=data.get("call_id", ""),
                status=data.get("status", "unknown"),
            )
        except Exception as e:
            logger.error("Failed to create call: %s", e)
            return CallResult(call_id="", status="error", error=str(e))

    def get_call(self, call_id: str) -> dict:
        """Get call status and result."""
        try:
            r = requests.get(f"{self.config.base_url}/calls/{call_id}", timeout=10)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def start_batch(self, leads: list[dict], max_calls: int = 10) -> dict:
        """Start batch calls from a list of leads."""
        try:
            r = requests.post(
                f"{self.config.base_url}/calls/batch",
                json=leads,
                params={"max_calls": max_calls},
                timeout=10,
            )
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def get_results(self) -> dict:
        """Get call results summary."""
        try:
            r = requests.get(f"{self.config.base_url}/results", timeout=10)
            return r.json()
        except Exception:
            return {}

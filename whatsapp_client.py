"""
Evolution API WhatsApp client for sales funnel.
Provides send_text, send_document, send_image via Evolution API v2.
"""
import os
import logging
import requests
from dataclasses import dataclass

logger = logging.getLogger("whatsapp_client")


@dataclass
class WhatsAppConfig:
    """Evolution API configuration."""
    base_url: str = "http://localhost:8080"
    api_key: str = ""
    instance_name: str = "sales_funnel"

    @classmethod
    def from_env(cls) -> "WhatsAppConfig":
        return cls(
            base_url=os.environ.get("EVO_API_URL", "http://localhost:8080").rstrip("/"),
            api_key=os.environ.get("EVO_API_KEY", "sales_funnel_evo_key_2026"),
            instance_name=os.environ.get("EVO_INSTANCE", "sales_funnel"),
        )


@dataclass
class WhatsAppResult:
    success: bool
    message_id: str | None = None
    error: str | None = None


class WhatsAppClient:
    """Client for Evolution API v2 WhatsApp messaging."""

    def __init__(self, config: WhatsAppConfig | None = None):
        self.config = config or WhatsAppConfig.from_env()
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.config.api_key,
            "Content-Type": "application/json",
        })

    @property
    def base(self) -> str:
        return f"{self.config.base_url}/instance"

    # ---- Instance management ----

    def create_instance(self) -> dict:
        """Create a new WhatsApp instance."""
        resp = self.session.post(f"{self.base}/create", json={
            "instanceName": self.config.instance_name,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
        })
        resp.raise_for_status()
        return resp.json()

    def get_qr_code(self) -> str | None:
        """Get QR code URL for connecting WhatsApp."""
        resp = self.session.get(
            f"{self.base}/connect/{self.config.instance_name}"
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("base64") or data.get("qrcode")
        return None

    def get_connection_state(self) -> str:
        """Check connection state: open, close, connecting."""
        resp = self.session.get(
            f"{self.base}/connectionState/{self.config.instance_name}"
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("instance", {}).get("state", "unknown")

    def fetch_instances(self) -> list:
        """List all instances."""
        resp = self.session.get(f"{self.base}/fetchInstances")
        resp.raise_for_status()
        return resp.json()

    # ---- Messaging ----

    def send_text(self, phone: str, message: str) -> WhatsAppResult:
        """
        Send a text message.
        phone: number in format 77071234567 (no +, no spaces)
        """
        phone = self._normalize_phone(phone)
        url = f"{self.config.base_url}/message/sendText/{self.config.instance_name}"
        try:
            resp = self.session.post(url, json={
                "number": phone,
                "text": message,
            })
            data = resp.json()
            if resp.status_code in (200, 201):
                msg_id = data.get("key", {}).get("id")
                logger.info("Text sent to %s, id=%s", phone, msg_id)
                return WhatsAppResult(success=True, message_id=msg_id)
            else:
                logger.error("Send text failed: %s", data)
                return WhatsAppResult(success=False, error=str(data))
        except Exception as e:
            logger.exception("Send text error")
            return WhatsAppResult(success=False, error=str(e))

    def send_document(
        self,
        phone: str,
        document_url: str,
        filename: str,
        caption: str = "",
    ) -> WhatsAppResult:
        """
        Send a document (PDF, etc).
        phone: number without +
        document_url: direct URL to the file
        """
        phone = self._normalize_phone(phone)
        url = f"{self.config.base_url}/message/sendMedia/{self.config.instance_name}"
        try:
            resp = self.session.post(url, json={
                "number": phone,
                "mediatype": "document",
                "media": document_url,
                "filename": filename,
                "caption": caption,
            })
            data = resp.json()
            if resp.status_code in (200, 201):
                msg_id = data.get("key", {}).get("id")
                logger.info("Document sent to %s, id=%s", phone, msg_id)
                return WhatsAppResult(success=True, message_id=msg_id)
            else:
                logger.error("Send document failed: %s", data)
                return WhatsAppResult(success=False, error=str(data))
        except Exception as e:
            logger.exception("Send document error")
            return WhatsAppResult(success=False, error=str(e))

    def send_image(
        self,
        phone: str,
        image_url: str,
        caption: str = "",
    ) -> WhatsAppResult:
        """Send an image with optional caption."""
        phone = self._normalize_phone(phone)
        url = f"{self.config.base_url}/message/sendMedia/{self.config.instance_name}"
        try:
            resp = self.session.post(url, json={
                "number": phone,
                "mediatype": "image",
                "media": image_url,
                "caption": caption,
            })
            data = resp.json()
            if resp.status_code in (200, 201):
                msg_id = data.get("key", {}).get("id")
                return WhatsAppResult(success=True, message_id=msg_id)
            else:
                return WhatsAppResult(success=False, error=str(data))
        except Exception as e:
            return WhatsAppResult(success=False, error=str(e))

    # ---- Helpers ----

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Normalize phone: remove +, spaces, dashes. Keep digits only."""
        digits = "".join(c for c in phone if c.isdigit())
        # Kazakhstan: 10-digit local → prepend 7
        if len(digits) == 10:
            digits = "7" + digits
        # 11-digit starting with 8 → replace with 7
        elif len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        return digits

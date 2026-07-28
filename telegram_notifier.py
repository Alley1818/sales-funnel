"""
Telegram notifier — sends notifications to Ali's Telegram when messages are sent.
Every WhatsApp/email send goes through here first.
"""
import os
import json
import logging
import httpx
from pathlib import Path

logger = logging.getLogger("telegram_notifier")

CONFIG_PATH = Path(__file__).parent / "config.json"


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


async def send_telegram(text: str) -> bool:
    """Send a notification message to Telegram."""
    cfg = _load_config()
    bot_token = cfg.get("telegram_bot_token", os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id = cfg.get("telegram_chat_id", os.getenv("TELEGRAM_CHAT_ID", ""))

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured: bot_token=%s, chat_id=%s", bool(bot_token), chat_id)
        return False

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if r.status_code == 200:
                logger.info("Telegram notification sent")
                return True
            else:
                logger.error("Telegram send failed: %s", r.text[:200])
                return False
    except Exception as e:
        logger.error("Telegram error: %s", e)
        return False


def _send_telegram_sync(text: str) -> bool:
    """Synchronous Telegram send using httpx.Client (no asyncio)."""
    cfg = _load_config()
    bot_token = cfg.get("telegram_bot_token", os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id = cfg.get("telegram_chat_id", os.getenv("TELEGRAM_CHAT_ID", ""))

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured: bot_token=%s, chat_id=%s", bool(bot_token), chat_id)
        return False

    try:
        with httpx.Client() as client:
            r = client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if r.status_code == 200:
                logger.info("Telegram notification sent")
                return True
            else:
                logger.error("Telegram send failed: %s", r.text[:200])
                return False
    except Exception as e:
        logger.error("Telegram error: %s", e)
        return False


def notify_send(action: str, recipient: str, details: str = ""):
    """
    Called BEFORE any outbound message is sent.
    Sends notification to Telegram and checks if auto_send is enabled.
    Returns True if the send should proceed, False to block.
    """
    cfg = _load_config()

    # Build notification message
    msg = f"<b>📨 Sales Funnel</b>\n"
    msg += f"<b>Действие:</b> {action}\n"
    msg += f"<b>Получатель:</b> {recipient}\n"
    if details:
        msg += f"<b>Детали:</b> {details}\n"

    # Check if auto_send is enabled
    if not cfg.get("auto_send", False):
        msg += "\n⚠️ <b>Auto-send ВЫКЛЕН.</b> Отправка заблокирована."
        _send_telegram_sync(msg)
        logger.info("BLOCKED: %s to %s (auto_send=false)", action, recipient)
        return False

    # Auto-send is ON — notify and proceed
    msg += "\n✅ Отправлено автоматически."
    _send_telegram_sync(msg)
    logger.info("SENT: %s to %s (notified via Telegram)", action, recipient)
    return True


def enable_auto_send():
    """Enable automatic sending."""
    cfg = _load_config()
    cfg["auto_send"] = True
    _save_config(cfg)
    logger.info("Auto-send ENABLED")


def disable_auto_send():
    """Disable automatic sending."""
    cfg = _load_config()
    cfg["auto_send"] = False
    _save_config(cfg)
    logger.info("Auto-send DISABLED")


def set_telegram_config(bot_token: str, chat_id: str):
    """Set Telegram bot token and chat ID."""
    cfg = _load_config()
    cfg["telegram_bot_token"] = bot_token
    cfg["telegram_chat_id"] = chat_id
    _save_config(cfg)
    logger.info("Telegram config updated")

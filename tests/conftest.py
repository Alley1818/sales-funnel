"""Test configuration."""
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Set required env vars for testing
os.environ.setdefault("ADMIN_PASSWORD", "test_password_123")

# Initialize DB for tests (create tables)
from leads_db import init_db
from db_extended import init_extended_tables
init_db()
init_extended_tables()

# Global: mute Telegram notifications in all tests
_mute = patch("telegram_notifier._send_telegram_sync", return_value=True)
_mute.start()

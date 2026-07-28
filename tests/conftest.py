"""Test configuration."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Set required env vars for testing
os.environ.setdefault("ADMIN_PASSWORD", "test_password_123")

# Initialize DB for tests (create tables)
from leads_db import init_db
from db_extended import init_extended_tables
init_db()
init_extended_tables()

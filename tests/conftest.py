"""Shared test fixtures — init DB once before all tests."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

@pytest.fixture(autouse=True, scope="session")
def _init_db():
    """Initialize all DB tables once before the test session."""
    from leads_db import init_db
    from db_extended import init_extended_tables
    from funnel_features import seed_default_templates
    init_db()
    init_extended_tables()
    seed_default_templates()

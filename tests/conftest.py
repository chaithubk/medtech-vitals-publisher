"""Shared pytest fixtures for the test suite."""

from pathlib import Path

import pytest

_VENDORED_SCHEMA = Path(__file__).parent.parent / "contracts" / "vitals" / "v2.0.json"


@pytest.fixture(autouse=True)
def use_vendored_schema(monkeypatch):
    """Point VITALS_SCHEMA_PATH at the vendored schema for all tests.

    This ensures VitalsSimulator construction (which calls
    initialize_runtime_schema at startup) succeeds in the test environment
    without needing a device rootfs at /usr/share/medtech/...
    """
    monkeypatch.setattr("src.config.VITALS_SCHEMA_PATH", str(_VENDORED_SCHEMA))

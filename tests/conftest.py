"""Shared pytest fixtures (contract from docs/ARCHITECTURE.md §3.1).

The autouse ``data_dir`` fixture points ``TIMESHEET_DATA_DIR`` at a temp dir and
clears environment seeds so every test starts from a clean, isolated data set.
"""

from __future__ import annotations

import importlib

import pytest

SERVICE_MODULES = (
    "services.settings_service",
    "services.state_service",
    "services.practices_service",
    "services.ai_service",
    "services.github_service",
    "services.graph_service",
    "services.outlook_service",
)

ENV_SEEDS = (
    "USER_NAME",
    "OPENAI_MODEL",
    "TIMESHEET_TIMEZONE",
    "HISTORY_KEEP_DAYS",
    "GRAPH_TENANT_ID",
    "APP_PORT",
    "AUTO_OPEN_BROWSER",
)


def _reset_service_caches() -> None:
    """Call ``reset_cache()`` on every service (spec §3.1: each service exposes it)."""
    for name in SERVICE_MODULES:
        importlib.import_module(name).reset_cache()


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMESHEET_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GRAPH_CLIENT_ID", raising=False)
    for name in ENV_SEEDS:
        monkeypatch.delenv(name, raising=False)
    # reset in-memory caches of services (each service exposes reset_cache() for tests)
    _reset_service_caches()
    yield tmp_path
    _reset_service_caches()


@pytest.fixture
def client():
    from app import create_app

    app = create_app(testing=True)
    return app.test_client()

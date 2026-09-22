"""Spec conformance sweep over every HTTP endpoint (docs/ARCHITECTURE.md section 7).

Runs against a fresh data dir with nothing configured and no network access:
every route must answer with the documented status code and response keys.
"""

from __future__ import annotations

import pytest

import config
from services import outlook_service

CONFIG_KEYS = {
    "app_name", "app_version", "user_name", "daily_hours", "timezone", "today",
    "openai_configured", "openai_model", "integrations",
}
CONFIG_GITHUB_KEYS = {"configured", "connected", "login", "repos_count"}
CONFIG_MICROSOFT_KEYS = {"configured", "connected", "account", "provider", "outlook_com_available"}
ENTRY_KEYS = {"id", "type", "text", "time", "duration_min", "source_id", "meta"}
ELABORATION_KEYS = {"result", "elaborated_at", "elaborated_edited_at"}
SETTINGS_KEYS = {
    "settings", "ai_defaults", "ai_effective", "placeholders",
    "openai_key_set", "openai_key_source", "timezones",
}
GITHUB_STATUS_KEYS = {"configured", "connected", "pending", "device", "error", "identity", "repos"}
MICROSOFT_STATUS_KEYS = {
    "configured", "connected", "pending", "device", "error", "account",
    "provider", "source", "outlook_com_available",
}
MSG_GITHUB_NOT_CONFIGURED = "Configura il Client ID GitHub nelle impostazioni"
MSG_MICROSOFT_NOT_CONFIGURED = "Configura il Client ID Microsoft nelle impostazioni"
SECRET = "sk-conformance-secret"


@pytest.fixture(autouse=True)
def _no_outlook(monkeypatch):
    """Keep the sweep deterministic on Windows hosts that have pywin32 installed."""
    monkeypatch.setattr(outlook_service, "is_available", lambda: False)


def _json(resp) -> dict:
    body = resp.get_json()
    assert isinstance(body, dict), f"{resp.request.path}: JSON object expected, got {body!r}"
    return body


def _assert_error(resp, status: int, **fields) -> dict:
    assert resp.status_code == status, f"{resp.request.path}: {resp.status_code} != {status}"
    body = _json(resp)
    assert body["success"] is False
    assert isinstance(body["error"], str) and body["error"]
    for key, value in fields.items():
        assert body[key] == value
    return body


# ---------------------------------------------------------------------------
# 7.1 UI & config
# ---------------------------------------------------------------------------

def test_ui_and_config(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.mimetype == "text/html"

    body = _json(client.get("/api/config"))
    assert set(body) == CONFIG_KEYS
    assert set(body["integrations"]["github"]) == CONFIG_GITHUB_KEYS
    assert set(body["integrations"]["microsoft"]) == CONFIG_MICROSOFT_KEYS
    assert body["today"] == config.today_iso()
    assert body["openai_configured"] is False
    assert body["integrations"]["github"]["connected"] is False
    assert body["integrations"]["microsoft"]["provider"] is None


# ---------------------------------------------------------------------------
# 7.2 Days & entries
# ---------------------------------------------------------------------------

def test_days_and_entries_lifecycle(client):
    days = _json(client.get("/api/days"))["days"]
    assert days[0]["date"] == config.today_iso()
    assert {"date", "entry_count", "elaborated"} <= set(days[0])
    assert _json(client.get("/api/entries")) == {"date": config.today_iso(), "entries": []}

    _assert_error(client.post("/api/entry", json={}), 400)
    created = _json(client.post("/api/entry", json={"text": "Analisi"}))
    assert created["success"] is True
    assert set(created["entry"]) == ENTRY_KEYS
    entry_id = created["entry"]["id"]

    updated = _json(client.put(f"/api/entry/{entry_id}", json={"text": "Analisi requisiti"}))
    assert updated["success"] is True
    assert updated["entry"]["text"] == "Analisi requisiti"
    _assert_error(client.put("/api/entry/missing", json={"text": "x"}), 404)

    _assert_error(client.delete("/api/entry/missing"), 404)
    assert _json(client.delete(f"/api/entry/{entry_id}")) == {"success": True}
    assert _json(client.get("/api/entries"))["entries"] == []


# ---------------------------------------------------------------------------
# 7.3 Elaboration
# ---------------------------------------------------------------------------

def test_elaboration_endpoints(client):
    _assert_error(client.post("/api/elaborate", json={}), 400)  # no entries
    client.post("/api/entry", json={"text": "Sviluppo"})
    _assert_error(client.post("/api/elaborate", json={}), 400)  # no OpenAI key

    empty = _json(client.get("/api/elaborate"))
    assert ELABORATION_KEYS <= set(empty)
    assert empty["result"] is None

    invalid = _assert_error(client.put("/api/elaborate", json={"result": {"timesheet": []}}), 400)
    assert isinstance(invalid["errors"], list) and invalid["errors"]

    result = {"timesheet": [{"pratica": "1", "ore": 2, "descrizione": "Test."}], "note": ""}
    saved = _json(client.put("/api/elaborate", json={"result": result}))
    assert saved["success"] is True
    assert {"result", "elaborated_edited_at"} <= set(saved)
    assert saved["result"]["totale_ore"] == 2.0

    stored = _json(client.get("/api/elaborate"))
    assert stored["result"] == saved["result"]
    assert stored["elaborated_edited_at"] == saved["elaborated_edited_at"]
    assert stored["elaborated_at"]


# ---------------------------------------------------------------------------
# 7.4 Practices
# ---------------------------------------------------------------------------

def test_practices_endpoints(client):
    assert isinstance(_json(client.get("/api/practices"))["practices"], list)
    _assert_error(client.post("/api/practices", json={}), 400)

    created = _json(client.post("/api/practices", json={"code": "Z1", "name": "Zeta", "description": "d"}))
    assert created["success"] is True
    assert any(p["code"] == "Z1" for p in created["practices"])

    updated = _json(client.put("/api/practices/Z1", json={"name": "Zeta 2"}))
    assert updated["success"] is True
    assert next(p for p in updated["practices"] if p["code"] == "Z1")["name"] == "Zeta 2"
    _assert_error(client.put("/api/practices/NOPE", json={"name": "x"}), 404)

    deleted = _json(client.delete("/api/practices/Z1"))
    assert deleted["success"] is True
    assert all(p["code"] != "Z1" for p in deleted["practices"])
    _assert_error(client.delete("/api/practices/Z1"), 404)


# ---------------------------------------------------------------------------
# 7.5 Settings
# ---------------------------------------------------------------------------

def test_settings_endpoints(client):
    body = _json(client.get("/api/settings"))
    assert SETTINGS_KEYS <= set(body)
    assert body["openai_key_set"] is False
    assert body["openai_key_source"] is None
    assert body["timezones"][0] == "Europe/Rome"

    invalid = client.put("/api/settings", json={"general": {"daily_hours": 0}})
    assert invalid.status_code == 400
    assert _json(invalid)["success"] is False
    assert _json(invalid)["errors"]

    ok = _json(client.put("/api/settings", json={"general": {"daily_hours": 7.5}, "openai_api_key": SECRET}))
    assert ok["success"] is True
    assert ok["settings"]["general"]["daily_hours"] == 7.5
    assert SECRET not in client.get("/api/settings").get_data(as_text=True)
    assert _json(client.get("/api/config"))["openai_configured"] is True
    assert _json(client.get("/api/settings"))["openai_key_source"] == "settings"

    reset = _json(client.post("/api/settings/ai/reset", json={}))
    assert {"success", "settings", "ai_effective"} <= set(reset)
    assert reset["ai_effective"] == body["ai_defaults"]

    preview = _json(client.get("/api/settings/ai/preview"))
    assert {"system_prompt", "user_prompt", "entry_count"} <= set(preview)
    assert preview["entry_count"] == 0


# ---------------------------------------------------------------------------
# 7.6 GitHub (not configured, not connected -> no network)
# ---------------------------------------------------------------------------

def test_github_endpoints_unconfigured(client):
    status = _json(client.get("/api/github/status"))
    assert set(status) == GITHUB_STATUS_KEYS
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["repos"] == []

    assert _assert_error(client.post("/api/github/connect"), 400)["error"] == MSG_GITHUB_NOT_CONFIGURED
    _assert_error(client.post("/api/github/token", json={}), 400)
    assert _json(client.post("/api/github/disconnect")) == {"success": True}
    _assert_error(client.get("/api/github/repos"), 401, auth_required=True)

    saved = _json(client.put("/api/github/repos", json={"repos": ["owner/name"]}))
    assert saved == {"success": True, "repos": ["owner/name"]}
    assert _json(client.get("/api/github/status"))["repos"] == ["owner/name"]
    assert _json(client.get("/api/config"))["integrations"]["github"]["repos_count"] == 1

    _assert_error(client.post("/api/github/import", json={}), 401, auth_required=True)


# ---------------------------------------------------------------------------
# 7.7 Microsoft (not configured -> no MSAL / Graph traffic)
# ---------------------------------------------------------------------------

def test_microsoft_endpoints_unconfigured(client):
    status = _json(client.get("/api/microsoft/status"))
    assert set(status) == MICROSOFT_STATUS_KEYS
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["provider"] is None
    assert status["source"] == "auto"
    assert status["outlook_com_available"] is False

    assert _assert_error(client.post("/api/microsoft/connect"), 400)["error"] == MSG_MICROSOFT_NOT_CONFIGURED
    assert _json(client.post("/api/microsoft/disconnect")) == {"success": True}
    _assert_error(client.post("/api/microsoft/import", json={}), 400)  # no provider available

    client.put("/api/settings", json={"microsoft": {"source": "graph"}})
    assert _json(client.get("/api/microsoft/status"))["provider"] == "graph"
    body = _assert_error(client.post("/api/microsoft/import", json={}), 400)  # graph forced, not configured
    assert body["error"] == MSG_MICROSOFT_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Cross-cutting rules (section 7 intro)
# ---------------------------------------------------------------------------

def test_unknown_api_paths_use_json_envelope(client):
    _assert_error(client.get("/api/does-not-exist"), 404)
    _assert_error(client.post("/api/days"), 405)


@pytest.mark.parametrize("path", ["/api/entries", "/api/elaborate", "/api/settings/ai/preview"])
def test_invalid_date_falls_back_to_today(client, path):
    assert _json(client.get(f"{path}?date=garbage"))["date"] == config.today_iso()
    assert _json(client.get(f"{path}?date=2026-01-05"))["date"] == "2026-01-05"

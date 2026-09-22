"""Tests for /api/settings* (spec §7.5)."""

import pytest

import config
from routes.settings import PROMPT_FIELDS
from services import ai_service, settings_service

SETTINGS_KEYS = {
    "settings", "ai_defaults", "ai_effective", "placeholders",
    "openai_key_set", "openai_key_source", "timezones",
}
SECRET = "sk-super-secret-key"


def _get(client):
    return client.get("/api/settings").get_json()


def _put(client, payload):
    return client.put("/api/settings", json=payload)


def _assert_validation_failure(resp):
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert isinstance(body["errors"], list) and body["errors"]


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

def test_get_settings_shape(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert SETTINGS_KEYS <= set(body)
    assert {"general", "ai", "github", "microsoft"} <= set(body["settings"])
    assert set(body["ai_defaults"]) == set(PROMPT_FIELDS)
    assert body["ai_effective"] == body["ai_defaults"]
    assert isinstance(body["placeholders"], list) and body["placeholders"]
    assert body["openai_key_set"] is False
    assert body["openai_key_source"] is None


def test_get_settings_timezones_start_with_rome(client):
    zones = _get(client)["timezones"]
    assert zones[0] == "Europe/Rome"
    assert len(zones) >= 20
    curated = list(settings_service.COMMON_TIMEZONES)
    assert zones[: len(curated)] == curated


def test_get_settings_includes_configured_timezone(client):
    assert _put(client, {"general": {"timezone": "Pacific/Auckland"}}).status_code == 200
    assert "Pacific/Auckland" in _get(client)["timezones"]


# ---------------------------------------------------------------------------
# PUT: general settings & validation
# ---------------------------------------------------------------------------

def test_put_general_settings(client):
    resp = _put(client, {"general": {"daily_hours": 7.5, "user_name": "Anna"}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["settings"]["general"]["daily_hours"] == 7.5
    assert body["settings"]["general"]["user_name"] == "Anna"
    stored = _get(client)["settings"]["general"]
    assert stored["daily_hours"] == 7.5
    assert stored["user_name"] == "Anna"
    assert client.get("/api/config").get_json()["user_name"] == "Anna"


def test_put_version_key_is_ignored(client):
    assert _put(client, {"version": 2, "general": {"user_name": "Bea"}}).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"general": {"daily_hours": 0}},
        {"general": {"daily_hours": 25}},
        {"general": {"daily_hours": "otto"}},
        {"general": {"timezone": "Mars/Olympus"}},
        {"general": {"history_keep_days": 0}},
        {"ai": {"temperature": 3}},
        {"github": {"repos": ["not a repo"]}},
        {"microsoft": {"source": "carrier_pigeon"}},
    ],
)
def test_put_invalid_values_return_errors_list(client, payload):
    _assert_validation_failure(_put(client, payload))
    assert _get(client)["settings"]["general"]["daily_hours"] == 8.0


def test_put_unknown_section_is_400(client):
    resp = _put(client, {"webhook": {"url": "x"}})
    _assert_validation_failure(resp)
    assert "webhook" in resp.get_json()["errors"][0]


def test_put_section_must_be_object(client):
    _assert_validation_failure(_put(client, {"general": "x"}))


def test_put_empty_body_is_400(client):
    _assert_validation_failure(_put(client, {}))


def test_put_malformed_json_is_400(client):
    resp = client.put("/api/settings", data="nope", content_type="application/json")
    _assert_validation_failure(resp)


# ---------------------------------------------------------------------------
# PUT: OpenAI key (write-only secret)
# ---------------------------------------------------------------------------

def test_put_openai_key_is_stored_as_secret_and_never_returned(client):
    resp = _put(client, {"openai_api_key": SECRET})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["openai_key_set"] is True
    assert body["openai_key_source"] == "settings"
    assert SECRET not in resp.get_data(as_text=True)

    get_resp = client.get("/api/settings")
    assert SECRET not in get_resp.get_data(as_text=True)
    assert get_resp.get_json()["openai_key_set"] is True
    assert get_resp.get_json()["openai_key_source"] == "settings"
    assert settings_service.get_secret("openai_api_key") == SECRET
    assert client.get("/api/config").get_json()["openai_configured"] is True


def test_put_openai_key_empty_string_clears_it(client):
    _put(client, {"openai_api_key": SECRET})
    assert _put(client, {"openai_api_key": ""}).status_code == 200
    body = _get(client)
    assert body["openai_key_set"] is False
    assert body["openai_key_source"] is None
    assert not settings_service.get_secret("openai_api_key")


def test_put_openai_key_null_clears_it(client):
    _put(client, {"openai_api_key": SECRET})
    assert _put(client, {"openai_api_key": None}).status_code == 200
    assert _get(client)["openai_key_set"] is False


def test_put_openai_key_must_be_string(client):
    _assert_validation_failure(_put(client, {"openai_api_key": 123}))


def test_openai_key_from_env_is_reported(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    body = _get(client)
    assert body["openai_key_set"] is True
    assert body["openai_key_source"] == "env"


def test_openai_key_from_settings_wins_over_env(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    _put(client, {"openai_api_key": SECRET})
    assert _get(client)["openai_key_source"] == "settings"
    assert settings_service.get_openai_api_key() == SECRET


def test_put_invalid_settings_does_not_store_key(client):
    _assert_validation_failure(_put(client, {"general": {"daily_hours": -1}, "openai_api_key": SECRET}))
    assert _get(client)["openai_key_set"] is False


# ---------------------------------------------------------------------------
# PUT: AI prompt fields
# ---------------------------------------------------------------------------

def test_put_prompt_field_overrides_default(client):
    resp = _put(client, {"ai": {"system_rules": "Regole personalizzate {{codes}}"}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["settings"]["ai"]["system_rules"] == "Regole personalizzate {{codes}}"
    assert body["ai_effective"]["system_rules"] == "Regole personalizzate {{codes}}"
    assert body["ai_effective"]["system_intro"] == ai_service.DEFAULT_PROMPTS["system_intro"]


@pytest.mark.parametrize("reset_value", ["", "   ", None])
def test_put_prompt_empty_or_null_resets_to_default(client, reset_value):
    _put(client, {"ai": {"system_rules": "Custom"}})
    resp = _put(client, {"ai": {"system_rules": reset_value}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["settings"]["ai"]["system_rules"] is None
    assert body["ai_effective"]["system_rules"] == ai_service.DEFAULT_PROMPTS["system_rules"]


def test_put_prompt_must_be_string(client):
    resp = _put(client, {"ai": {"system_rules": ["x"]}})
    _assert_validation_failure(resp)
    assert "system_rules" in resp.get_json()["errors"][0]


# ---------------------------------------------------------------------------
# POST /api/settings/ai/reset
# ---------------------------------------------------------------------------

def test_reset_all_prompts(client):
    _put(client, {"ai": {"system_intro": "Intro custom", "system_rules": "Regole custom"}})
    resp = client.post("/api/settings/ai/reset", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert all(body["settings"]["ai"][field] is None for field in PROMPT_FIELDS)
    assert body["ai_effective"] == ai_service.DEFAULT_PROMPTS


def test_reset_selected_prompt_only(client):
    _put(client, {"ai": {"system_intro": "Intro custom", "system_rules": "Regole custom"}})
    body = client.post("/api/settings/ai/reset", json={"fields": ["system_rules"]}).get_json()
    assert body["settings"]["ai"]["system_rules"] is None
    assert body["settings"]["ai"]["system_intro"] == "Intro custom"
    assert body["ai_effective"]["system_intro"] == "Intro custom"


def test_reset_rejects_unknown_field(client):
    resp = client.post("/api/settings/ai/reset", json={"fields": ["nope"]})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


# ---------------------------------------------------------------------------
# GET /api/settings/ai/preview
# ---------------------------------------------------------------------------

def test_preview_returns_prompts_for_day(client):
    client.post("/api/entry", json={"text": "Analisi requisiti cliente"})
    resp = client.get("/api/settings/ai/preview")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["entry_count"] == 1
    assert body["date"] == config.today_iso()
    assert body["system_prompt"]
    assert "Analisi requisiti cliente" in body["user_prompt"]
    assert "{{" not in body["user_prompt"]


def test_preview_uses_custom_prompt_and_requested_day(client):
    _put(client, {"ai": {"system_intro": "INTRO CUSTOM\n{{practices}}"}})
    client.post("/api/entry", json={"text": "Lavoro passato", "date": "2026-01-05"})
    body = client.get("/api/settings/ai/preview?date=2026-01-05").get_json()
    assert body["system_prompt"].startswith("INTRO CUSTOM")
    assert body["entry_count"] == 1
    assert "Lavoro passato" in body["user_prompt"]


def test_preview_on_empty_day(client):
    body = client.get("/api/settings/ai/preview").get_json()
    assert body["entry_count"] == 0
    assert body["user_prompt"]

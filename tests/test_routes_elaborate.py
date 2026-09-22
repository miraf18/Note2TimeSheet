"""Tests for /api/elaborate GET/POST/PUT (spec §7.3). The AI call is mocked."""

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

import config
from routes.elaborate import MSG_NO_ENTRIES, MSG_UNEXPECTED
from services import ai_service

OTHER_DAY = "2026-01-05"
AI_RESULT = {
    "timesheet": [{"pratica": "6450", "ore": 8.0, "descrizione": "Sviluppo modulo X."}],
    "totale_ore": 8.0,
    "note": "",
}


def _seed_entry(client, text="Sviluppo modulo X", date=None):
    payload = {"text": text}
    if date:
        payload["date"] = date
    return client.post("/api/entry", json=payload).get_json()["entry"]


def _http_request():
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status_error(error_cls, status):
    response = httpx.Response(status, request=_http_request())
    return error_cls("errore openai", response=response, body=None)


def _mock_ai(monkeypatch, outcome):
    """Replace ai_service.elaborate_timesheet: dict → returned, Exception → raised."""
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ai_service, "elaborate_timesheet", fake)
    return captured


# ---------------------------------------------------------------------------
# POST /api/elaborate
# ---------------------------------------------------------------------------

def test_post_without_entries_is_400(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    resp = client.post("/api/elaborate", json={})
    assert resp.status_code == 400
    assert resp.get_json() == {"success": False, "error": MSG_NO_ENTRIES}


def test_post_without_api_key_is_400(client, monkeypatch):
    _seed_entry(client)
    _mock_ai(monkeypatch, AI_RESULT)
    resp = client.post("/api/elaborate", json={})
    assert resp.status_code == 400
    assert "OpenAI" in resp.get_json()["error"]


def test_post_success_persists_result(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _seed_entry(client)
    captured = _mock_ai(monkeypatch, AI_RESULT)

    resp = client.post("/api/elaborate", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["result"] == AI_RESULT
    assert body["elaborated_at"]

    assert captured["api_key"] == "sk-test"
    assert captured["date"] == config.today_iso()
    assert len(captured["entries"]) == 1
    assert captured["daily_hours"] == 8.0
    assert captured["model"]
    assert isinstance(captured["practices"], list)
    assert isinstance(captured["ai_settings"], dict)

    stored = client.get("/api/elaborate").get_json()
    assert stored["result"] == AI_RESULT
    assert stored["elaborated_at"] == body["elaborated_at"]
    assert stored["elaborated_edited_at"] is None
    assert client.get("/api/days").get_json()["days"][0]["elaborated"] is True


def test_post_uses_requested_day(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _seed_entry(client, date=OTHER_DAY)
    captured = _mock_ai(monkeypatch, AI_RESULT)
    assert client.post("/api/elaborate", json={"date": OTHER_DAY}).status_code == 200
    assert captured["date"] == OTHER_DAY
    assert client.get(f"/api/elaborate?date={OTHER_DAY}").get_json()["result"] == AI_RESULT
    assert client.get("/api/elaborate").get_json()["result"] is None


def test_post_value_error_is_400(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _seed_entry(client)
    _mock_ai(monkeypatch, ValueError("Risposta AI non valida"))
    resp = client.post("/api/elaborate", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Risposta AI non valida"


@pytest.mark.parametrize(
    "error, expected_status",
    [
        (_status_error(AuthenticationError, 401), 401),
        (_status_error(RateLimitError, 429), 502),
        (APIConnectionError(request=_http_request()), 502),
        (_status_error(APIStatusError, 503), 502),
    ],
)
def test_post_maps_openai_errors(client, monkeypatch, error, expected_status):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _seed_entry(client)
    _mock_ai(monkeypatch, error)
    resp = client.post("/api/elaborate", json={})
    assert resp.status_code == expected_status
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]
    assert client.get("/api/elaborate").get_json()["result"] is None


def test_post_api_status_error_mentions_status_code(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _seed_entry(client)
    _mock_ai(monkeypatch, _status_error(APIStatusError, 503))
    assert "503" in client.post("/api/elaborate", json={}).get_json()["error"]


def test_post_unexpected_error_is_500(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _seed_entry(client)
    _mock_ai(monkeypatch, RuntimeError("boom"))
    resp = client.post("/api/elaborate", json={})
    assert resp.status_code == 500
    assert resp.get_json() == {"success": False, "error": MSG_UNEXPECTED}


# ---------------------------------------------------------------------------
# GET /api/elaborate
# ---------------------------------------------------------------------------

def test_get_elaboration_empty(client):
    body = client.get("/api/elaborate").get_json()
    assert body["result"] is None
    assert body["elaborated_at"] is None
    assert body["elaborated_edited_at"] is None


# ---------------------------------------------------------------------------
# PUT /api/elaborate
# ---------------------------------------------------------------------------

def _put(client, result, date=None):
    payload = {"result": result}
    if date:
        payload["date"] = date
    return client.put("/api/elaborate", json=payload)


def test_put_recomputes_total_and_normalises_rows(client):
    result = {
        "timesheet": [
            {"pratica": " 6450 ", "ore": 2.5, "descrizione": " Sviluppo. "},
            {"pratica": "6447", "ore": "1,25", "descrizione": "Supporto."},
        ],
        "totale_ore": 99,
        "note": " nota ",
    }
    resp = _put(client, result)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["elaborated_edited_at"]
    saved = body["result"]
    assert saved["totale_ore"] == 3.75
    assert saved["note"] == "nota"
    assert saved["timesheet"][0] == {"pratica": "6450", "ore": 2.5, "descrizione": "Sviluppo."}
    assert saved["timesheet"][1]["ore"] == 1.25

    stored = client.get("/api/elaborate").get_json()
    assert stored["result"] == saved
    assert stored["elaborated_edited_at"] == body["elaborated_edited_at"]


def test_put_rounds_total_to_two_decimals(client):
    result = {"timesheet": [{"pratica": "1", "ore": 0.1, "descrizione": "A."},
                            {"pratica": "2", "ore": 0.2, "descrizione": "B."}]}
    assert _put(client, result).get_json()["result"]["totale_ore"] == 0.3


def test_put_defaults_note_and_description(client):
    body = _put(client, {"timesheet": [{"pratica": "1", "ore": 1}]}).get_json()
    assert body["result"]["note"] == ""
    assert body["result"]["timesheet"][0]["descrizione"] == ""


@pytest.mark.parametrize(
    "result",
    [
        None,
        "not-an-object",
        {},
        {"timesheet": []},
        {"timesheet": "x"},
        {"timesheet": ["x"]},
        {"timesheet": [{"pratica": "1", "ore": 0, "descrizione": "A."}]},
        {"timesheet": [{"pratica": "1", "ore": -1, "descrizione": "A."}]},
        {"timesheet": [{"pratica": "1", "ore": "abc", "descrizione": "A."}]},
        {"timesheet": [{"pratica": "1", "ore": True, "descrizione": "A."}]},
        {"timesheet": [{"pratica": "1", "ore": 25, "descrizione": "A."}]},
        {"timesheet": [{"pratica": "", "ore": 1, "descrizione": "A."}]},
        {"timesheet": [{"ore": 1, "descrizione": "A."}]},
        {"timesheet": [{"pratica": "1", "ore": 1, "descrizione": 5}]},
        {"timesheet": [{"pratica": "1", "ore": 1, "descrizione": "A."}], "note": 5},
    ],
)
def test_put_validation_errors(client, result):
    resp = _put(client, result)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]
    assert isinstance(body["errors"], list) and body["errors"]
    assert client.get("/api/elaborate").get_json()["result"] is None


def test_put_reports_every_invalid_row(client):
    result = {"timesheet": [{"pratica": "", "ore": 1}, {"pratica": "1", "ore": 0}]}
    errors = _put(client, result).get_json()["errors"]
    assert len(errors) == 2
    assert errors[0].startswith("Voce 1")
    assert errors[1].startswith("Voce 2")


def test_put_targets_requested_day(client):
    result = {"timesheet": [{"pratica": "1", "ore": 1, "descrizione": "A."}]}
    assert _put(client, result, date=OTHER_DAY).status_code == 200
    assert client.get(f"/api/elaborate?date={OTHER_DAY}").get_json()["result"]["totale_ore"] == 1.0
    assert client.get("/api/elaborate").get_json()["result"] is None

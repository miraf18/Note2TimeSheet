"""Tests for /api/days, /api/entries and /api/entry* (spec §7.2)."""

import pytest

import config
from routes.entries import MAX_ENTRY_TEXT_LEN, MSG_EMPTY_TEXT, MSG_ENTRY_NOT_FOUND

OTHER_DAY = "2026-01-05"


def _add(client, text, date=None):
    payload = {"text": text}
    if date:
        payload["date"] = date
    return client.post("/api/entry", json=payload)


def test_days_always_include_today(client):
    days = client.get("/api/days").get_json()["days"]
    assert config.today_iso() in [day["date"] for day in days]
    assert {"date", "entry_count", "elaborated"} <= set(days[0])


def test_entries_empty_by_default(client):
    body = client.get("/api/entries").get_json()
    assert body == {"date": config.today_iso(), "entries": []}


def test_add_entry_strips_and_persists(client):
    resp = _add(client, "  Fix bug login  ")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    entry = body["entry"]
    assert entry["text"] == "Fix bug login"
    assert entry["type"] == "manual"
    assert entry["id"]
    entries = client.get("/api/entries").get_json()["entries"]
    assert [e["id"] for e in entries] == [entry["id"]]


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "   "}, {"text": None}])
def test_add_entry_rejects_empty_text(client, payload):
    resp = client.post("/api/entry", json=payload)
    assert resp.status_code == 400
    assert resp.get_json() == {"success": False, "error": MSG_EMPTY_TEXT}


def test_add_entry_rejects_non_string_text(client):
    resp = _add(client, 123)
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_add_entry_enforces_max_length(client):
    assert _add(client, "x" * MAX_ENTRY_TEXT_LEN).status_code == 200
    resp = _add(client, "x" * (MAX_ENTRY_TEXT_LEN + 1))
    assert resp.status_code == 400
    assert str(MAX_ENTRY_TEXT_LEN) in resp.get_json()["error"]


def test_add_entry_with_malformed_json_is_400(client):
    resp = client.post("/api/entry", data="not json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_add_entry_for_specific_day(client):
    _add(client, "Lavoro passato", date=OTHER_DAY)
    assert len(client.get(f"/api/entries?date={OTHER_DAY}").get_json()["entries"]) == 1
    assert client.get("/api/entries").get_json()["entries"] == []
    days = {d["date"]: d for d in client.get("/api/days").get_json()["days"]}
    assert days[OTHER_DAY]["entry_count"] == 1
    assert days[OTHER_DAY]["elaborated"] is False


def test_invalid_date_falls_back_to_today(client):
    body = client.get("/api/entries?date=not-a-date").get_json()
    assert body["date"] == config.today_iso()


def test_update_entry_text(client):
    entry = _add(client, "Vecchio").get_json()["entry"]
    resp = client.put(f"/api/entry/{entry['id']}", json={"text": "  Nuovo  "})
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["text"] == "Nuovo"
    assert client.get("/api/entries").get_json()["entries"][0]["text"] == "Nuovo"


def test_update_entry_not_found(client):
    resp = client.put("/api/entry/missing", json={"text": "x"})
    assert resp.status_code == 404
    assert resp.get_json() == {"success": False, "error": MSG_ENTRY_NOT_FOUND}


def test_update_entry_rejects_empty_text(client):
    entry = _add(client, "Testo").get_json()["entry"]
    resp = client.put(f"/api/entry/{entry['id']}", json={"text": ""})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == MSG_EMPTY_TEXT


def test_update_entry_uses_body_date(client):
    entry = _add(client, "Passato", date=OTHER_DAY).get_json()["entry"]
    assert client.put(f"/api/entry/{entry['id']}", json={"text": "x"}).status_code == 404
    resp = client.put(f"/api/entry/{entry['id']}", json={"text": "Modificato", "date": OTHER_DAY})
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["text"] == "Modificato"


def test_delete_entry(client):
    entry = _add(client, "Da cancellare").get_json()["entry"]
    resp = client.delete(f"/api/entry/{entry['id']}")
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True}
    assert client.get("/api/entries").get_json()["entries"] == []


def test_delete_entry_not_found(client):
    resp = client.delete("/api/entry/missing")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_delete_entry_uses_query_date(client):
    entry = _add(client, "Passato", date=OTHER_DAY).get_json()["entry"]
    assert client.delete(f"/api/entry/{entry['id']}").status_code == 404
    assert client.delete(f"/api/entry/{entry['id']}?date={OTHER_DAY}").status_code == 200
    assert client.get(f"/api/entries?date={OTHER_DAY}").get_json()["entries"] == []

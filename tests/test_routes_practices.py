"""Tests for /api/practices* (spec §7.4)."""

import pytest

from routes.practices import MSG_CODE_NAME_REQUIRED, MSG_NAME_EMPTY, MSG_NOTHING_TO_UPDATE

NEW = {"code": "TST1", "name": "Test pratica", "description": "Descrizione di prova"}


def _codes(practices):
    return [p["code"] for p in practices]


def _add(client, payload=NEW):
    return client.post("/api/practices", json=payload)


def test_list_practices(client):
    resp = client.get("/api/practices")
    assert resp.status_code == 200
    assert isinstance(resp.get_json()["practices"], list)


def test_add_practice(client):
    resp = _add(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    added = next(p for p in body["practices"] if p["code"] == "TST1")
    assert added == NEW
    assert "TST1" in _codes(client.get("/api/practices").get_json()["practices"])


def test_add_practice_strips_whitespace(client):
    body = _add(client, {"code": " TST1 ", "name": " Nome ", "description": " d "}).get_json()
    added = next(p for p in body["practices"] if p["code"] == "TST1")
    assert added["name"] == "Nome"
    assert added["description"] == "d"


@pytest.mark.parametrize("payload", [{}, {"name": "Solo nome"}, {"code": "SOLO"}, {"code": " ", "name": "x"}])
def test_add_practice_requires_code_and_name(client, payload):
    resp = _add(client, payload)
    assert resp.status_code == 400
    assert resp.get_json() == {"success": False, "error": MSG_CODE_NAME_REQUIRED}


def test_add_practice_duplicate_is_400(client):
    assert _add(client).status_code == 200
    resp = _add(client)
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_add_practice_rejects_non_string_fields(client):
    resp = _add(client, {"code": 1234, "name": "x"})
    assert resp.status_code == 400
    assert "Codice" in resp.get_json()["error"]


def test_add_practice_rejects_too_long_code(client):
    """Length limits are enforced by practices_service (ValueError → 400)."""
    resp = _add(client, {"code": "x" * 10_000, "name": "x"})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    assert "TST1" not in _codes(client.get("/api/practices").get_json()["practices"])


def test_update_practice_name_and_description(client):
    _add(client)
    resp = client.put("/api/practices/TST1", json={"name": "Rinominata", "description": "Nuova"})
    assert resp.status_code == 200
    updated = next(p for p in resp.get_json()["practices"] if p["code"] == "TST1")
    assert updated["name"] == "Rinominata"
    assert updated["description"] == "Nuova"


def test_update_practice_only_description_keeps_name(client):
    _add(client)
    body = client.put("/api/practices/TST1", json={"description": "Solo descrizione"}).get_json()
    updated = next(p for p in body["practices"] if p["code"] == "TST1")
    assert updated["name"] == NEW["name"]
    assert updated["description"] == "Solo descrizione"


def test_update_practice_not_found(client):
    resp = client.put("/api/practices/NOPE", json={"name": "x"})
    assert resp.status_code == 404
    assert "NOPE" in resp.get_json()["error"]


def test_update_practice_rejects_empty_name(client):
    _add(client)
    resp = client.put("/api/practices/TST1", json={"name": "  "})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == MSG_NAME_EMPTY


def test_update_practice_requires_a_change(client):
    _add(client)
    resp = client.put("/api/practices/TST1", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == MSG_NOTHING_TO_UPDATE


def test_delete_practice(client):
    _add(client)
    resp = client.delete("/api/practices/TST1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "TST1" not in _codes(body["practices"])
    assert "TST1" not in _codes(client.get("/api/practices").get_json()["practices"])


def test_delete_practice_not_found(client):
    resp = client.delete("/api/practices/NOPE")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False

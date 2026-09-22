"""Route tests for ``/api/microsoft/*`` (graph / outlook services are stubbed)."""
from __future__ import annotations

import pytest

import config
from routes import microsoft as microsoft_routes
from services import graph_service, outlook_service, settings_service, state_service

DATE = "2026-09-16"
ACCOUNT = {"username": "mario.rossi@contoso.com", "name": "Mario Rossi"}
DEVICE = {
    "user_code": "ABCD-1234",
    "verification_uri": "https://microsoft.com/devicelogin",
    "message": "Vai su https://microsoft.com/devicelogin e inserisci ABCD-1234",
    "expires_in": 900,
}
MEETINGS = [
    {
        "subject": "Stand-up", "duration": 30, "duration_label": "30", "is_allday": False,
        "meeting_id": "AAMk1", "start": "09:00", "end": "09:30",
    },
    {
        "subject": "Ferie", "duration": 480, "duration_label": "Tutto il giorno", "is_allday": True,
        "meeting_id": "AAMk2", "start": "00:00", "end": "00:00",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def stub_graph(monkeypatch, *, configured=True, connected=False, account=None,
               meetings=None, error=None, device=None, auth_error=None, msal_available=True):
    status = {
        "configured": configured, "connected": connected, "pending": False,
        "device": None, "error": auth_error, "account": account,
    }
    monkeypatch.setattr(graph_service, "MSAL_AVAILABLE", msal_available)
    monkeypatch.setattr(graph_service, "is_configured", lambda: configured)
    monkeypatch.setattr(graph_service, "is_connected", lambda: connected)
    monkeypatch.setattr(graph_service, "auth_status", lambda: dict(status))
    monkeypatch.setattr(graph_service, "start_device_login", lambda: device)
    monkeypatch.setattr(graph_service, "get_meetings", lambda date_iso: (meetings, error))


def stub_outlook(monkeypatch, *, available=False, meetings=None, error=None):
    monkeypatch.setattr(outlook_service, "is_available", lambda: available)
    monkeypatch.setattr(
        outlook_service, "get_outlook_meetings",
        lambda date_iso, account_email="": (meetings, error),
    )


def set_source(source):
    settings_service.update_settings({"microsoft": {"source": source}})


def post_import(client, **payload):
    return client.post("/api/microsoft/import", json=payload)


# ---------------------------------------------------------------------------
# Provider resolution (pure)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source, graph_connected, outlook_available, expected",
    [
        ("auto", True, True, "graph"),
        ("auto", True, False, "graph"),
        ("auto", False, True, "outlook_com"),
        ("auto", False, False, None),
        ("graph", False, True, "graph"),
        ("graph", True, True, "graph"),
        ("outlook_com", True, False, "outlook_com"),
        ("outlook_com", False, False, "outlook_com"),
    ],
)
def test_resolve_provider(source, graph_connected, outlook_available, expected):
    assert microsoft_routes.resolve_provider(source, graph_connected, outlook_available) == expected


def test_configured_source_defaults_to_auto():
    assert microsoft_routes.configured_source() == "auto"
    set_source("graph")
    assert microsoft_routes.configured_source() == "graph"


# ---------------------------------------------------------------------------
# GET /api/microsoft/status
# ---------------------------------------------------------------------------

def test_status_unconfigured(client, monkeypatch):
    stub_graph(monkeypatch, configured=False)
    stub_outlook(monkeypatch, available=False)
    response = client.get("/api/microsoft/status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["configured"] is False
    assert body["connected"] is False
    assert body["pending"] is False
    assert body["account"] is None
    assert body["provider"] is None
    assert body["source"] == "auto"
    assert body["outlook_com_available"] is False


def test_status_auto_prefers_graph_when_connected(client, monkeypatch):
    stub_graph(monkeypatch, connected=True, account=ACCOUNT)
    stub_outlook(monkeypatch, available=True)
    body = client.get("/api/microsoft/status").get_json()
    assert body["connected"] is True
    assert body["account"] == ACCOUNT
    assert body["provider"] == "graph"
    assert body["outlook_com_available"] is True


def test_status_auto_falls_back_to_outlook(client, monkeypatch):
    stub_graph(monkeypatch, connected=False)
    stub_outlook(monkeypatch, available=True)
    body = client.get("/api/microsoft/status").get_json()
    assert body["provider"] == "outlook_com"


def test_status_source_graph_forced(client, monkeypatch):
    set_source("graph")
    stub_graph(monkeypatch, connected=False)
    stub_outlook(monkeypatch, available=True)
    body = client.get("/api/microsoft/status").get_json()
    assert body["source"] == "graph"
    assert body["provider"] == "graph"


def test_status_source_outlook_forced(client, monkeypatch):
    set_source("outlook_com")
    stub_graph(monkeypatch, connected=True, account=ACCOUNT)
    stub_outlook(monkeypatch, available=False)
    body = client.get("/api/microsoft/status").get_json()
    assert body["source"] == "outlook_com"
    assert body["provider"] == "outlook_com"


# ---------------------------------------------------------------------------
# POST /api/microsoft/connect
# ---------------------------------------------------------------------------

def test_connect_requires_client_id(client, monkeypatch):
    stub_graph(monkeypatch, configured=False)
    response = client.post("/api/microsoft/connect")
    assert response.status_code == 400
    assert response.get_json() == {
        "success": False, "error": "Configura il Client ID Microsoft nelle impostazioni",
    }


def test_connect_returns_device_code(client, monkeypatch):
    stub_graph(monkeypatch, device=DEVICE)
    response = client.post("/api/microsoft/connect")
    assert response.status_code == 200
    assert response.get_json() == {"success": True, "device": DEVICE}


def test_connect_reports_msal_failure_as_502(client, monkeypatch):
    stub_graph(monkeypatch, device=None, auth_error="Impossibile avviare il login Microsoft: boom")
    response = client.post("/api/microsoft/connect")
    assert response.status_code == 502
    body = response.get_json()
    assert body["success"] is False
    assert body["error"] == "Impossibile avviare il login Microsoft: boom"


def test_connect_without_msal_library(client, monkeypatch):
    stub_graph(monkeypatch, msal_available=False)
    response = client.post("/api/microsoft/connect")
    assert response.status_code == 500
    assert "MSAL" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# POST /api/microsoft/disconnect
# ---------------------------------------------------------------------------

def test_disconnect_calls_service(client, monkeypatch):
    calls = []
    monkeypatch.setattr(graph_service, "disconnect", lambda: calls.append(True))
    response = client.post("/api/microsoft/disconnect")
    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    assert calls == [True]


def test_disconnect_oserror_returns_500(client, monkeypatch):
    def failing():
        raise OSError("locked")

    monkeypatch.setattr(graph_service, "disconnect", failing)
    response = client.post("/api/microsoft/disconnect")
    assert response.status_code == 500
    assert response.get_json()["success"] is False


# ---------------------------------------------------------------------------
# POST /api/microsoft/import
# ---------------------------------------------------------------------------

def test_import_without_provider_returns_400(client, monkeypatch):
    stub_graph(monkeypatch, configured=False)
    stub_outlook(monkeypatch, available=False)
    response = post_import(client, date=DATE)
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert "Nessuna integrazione calendario" in body["error"]


def test_import_graph_not_connected_returns_401(client, monkeypatch):
    set_source("graph")
    stub_graph(monkeypatch, connected=False, error=graph_service.AUTH_REQUIRED)
    stub_outlook(monkeypatch, available=True)
    response = post_import(client, date=DATE)
    assert response.status_code == 401
    body = response.get_json()
    assert body["success"] is False
    assert body["auth_required"] is True


def test_import_graph_forced_but_not_configured_returns_400(client, monkeypatch):
    set_source("graph")
    stub_graph(monkeypatch, configured=False)
    stub_outlook(monkeypatch, available=False)
    response = post_import(client, date=DATE)
    assert response.status_code == 400
    assert response.get_json()["error"] == "Configura il Client ID Microsoft nelle impostazioni"


def test_import_graph_creates_meeting_entries_and_dedupes(client, monkeypatch):
    stub_graph(monkeypatch, connected=True, account=ACCOUNT, meetings=MEETINGS)
    stub_outlook(monkeypatch, available=False)

    response = post_import(client, date=DATE)
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["provider"] == "graph"
    assert body["date"] == DATE
    assert body["new"] == 2
    assert body["skipped"] == 0
    assert len(body["entries"]) == 2

    first = body["entries"][0]
    assert first["type"] == "meeting"
    assert first["text"] == "Stand-up"
    assert first["duration_min"] == 30
    assert first["source_id"] == "meeting:AAMk1"
    assert first["meta"] == {"provider": "graph", "start": "09:00", "end": "09:30", "is_allday": False}
    second = body["entries"][1]
    assert second["source_id"] == "meeting:AAMk2"
    assert second["meta"]["is_allday"] is True
    assert second["duration_min"] == 480
    assert len(state_service.get_entries(date=DATE)) == 2

    again = post_import(client, date=DATE).get_json()
    assert again["new"] == 0
    assert again["skipped"] == 2
    assert len(again["entries"]) == 2
    assert len(state_service.get_entries(date=DATE)) == 2


def test_import_skips_duplicate_ids_within_same_batch(client, monkeypatch):
    stub_graph(monkeypatch, connected=True, meetings=[MEETINGS[0], MEETINGS[0]])
    stub_outlook(monkeypatch, available=False)
    body = post_import(client, date=DATE).get_json()
    assert body["new"] == 1
    assert body["skipped"] == 1


def test_import_graph_upstream_error_returns_502(client, monkeypatch):
    stub_graph(monkeypatch, connected=True, error="Errore Microsoft Graph (500).")
    stub_outlook(monkeypatch, available=False)
    response = post_import(client, date=DATE)
    assert response.status_code == 502
    assert response.get_json() == {"success": False, "error": "Errore Microsoft Graph (500)."}


def test_import_uses_outlook_provider(client, monkeypatch):
    set_source("outlook_com")
    stub_graph(monkeypatch, connected=True)
    monkeypatch.setattr(
        graph_service, "get_meetings",
        lambda date_iso: pytest.fail("graph must not be queried when source is outlook_com"),
    )
    seen_dates = []

    def fake_outlook(date_iso, account_email=""):
        seen_dates.append(date_iso)
        return [MEETINGS[0]], None

    monkeypatch.setattr(outlook_service, "is_available", lambda: True)
    monkeypatch.setattr(outlook_service, "get_outlook_meetings", fake_outlook)

    body = post_import(client, date=DATE).get_json()
    assert body["success"] is True
    assert body["provider"] == "outlook_com"
    assert body["new"] == 1
    assert body["entries"][0]["meta"]["provider"] == "outlook_com"
    assert seen_dates == [DATE]


def test_import_outlook_error_returns_502(client, monkeypatch):
    set_source("outlook_com")
    stub_graph(monkeypatch, connected=False)
    stub_outlook(monkeypatch, available=True, error="Errore Outlook: COM non disponibile")
    response = post_import(client, date=DATE)
    assert response.status_code == 502
    assert response.get_json()["error"] == "Errore Outlook: COM non disponibile"


def test_import_invalid_date_falls_back_to_today(client, monkeypatch):
    stub_graph(monkeypatch, connected=True, meetings=[])
    stub_outlook(monkeypatch, available=False)
    body = post_import(client, date="garbage").get_json()
    assert body["success"] is True
    assert body["date"] == config.today_iso()
    assert body["new"] == 0


def test_import_reads_date_from_query_string(client, monkeypatch):
    stub_graph(monkeypatch, connected=True, meetings=[MEETINGS[0]])
    stub_outlook(monkeypatch, available=False)
    response = client.post("/api/microsoft/import?date=2026-01-05")
    body = response.get_json()
    assert body["date"] == "2026-01-05"
    assert len(state_service.get_entries(date="2026-01-05")) == 1
    assert state_service.get_entries(date=DATE) == []


def test_import_outlook_passes_outlook_account_from_env(client, monkeypatch):
    set_source("outlook_com")
    stub_graph(monkeypatch, connected=False)
    monkeypatch.setenv("OUTLOOK_ACCOUNT", "  raffaele@example.com ")
    seen = []

    def fake_outlook(date_iso, account_email=""):
        seen.append(account_email)
        return [MEETINGS[0]], None

    monkeypatch.setattr(outlook_service, "is_available", lambda: True)
    monkeypatch.setattr(outlook_service, "get_outlook_meetings", fake_outlook)
    assert post_import(client, date=DATE).get_json()["success"] is True
    assert seen == ["raffaele@example.com"]

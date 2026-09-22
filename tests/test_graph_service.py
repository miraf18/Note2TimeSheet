"""Unit tests for ``services.graph_service`` (msal and requests are mocked)."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest
import requests

import config
from services import graph_service, settings_service

CLIENT_ID = "11111111-2222-3333-4444-555555555555"
DATE = "2026-09-16"
ACCOUNT = {
    "username": "mario.rossi@contoso.com",
    "home_account_id": "uid.tid",
    "environment": "login.microsoftonline.com",
}
TOKEN = {"access_token": "tok", "token_type": "Bearer", "expires_in": 3600}
FLOW = {
    "user_code": "ABCD-1234",
    "verification_uri": "https://microsoft.com/devicelogin",
    "message": "Vai su https://microsoft.com/devicelogin e inserisci ABCD-1234",
    "expires_in": 900,
    "device_code": "very-secret-device-code",
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_flow_state():
    graph_service.reset_cache()
    yield
    thread = graph_service._auth_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    graph_service.reset_cache()


def configure(client_id=CLIENT_ID, tenant_id="common", **general):
    patch = {"microsoft": {"client_id": client_id, "tenant_id": tenant_id}}
    if general:
        patch = {**patch, "general": general}
    settings_service.update_settings(patch)


@pytest.fixture
def fake_msal(monkeypatch):
    """Replace ``msal.PublicClientApplication`` with a controllable fake."""
    state = SimpleNamespace(
        accounts=[], silent_result=None, flow=None, device_result=None,
        device_event=None, init_error=None, instances=[],
    )

    class FakeApp:
        def __init__(self, client_id, authority=None, token_cache=None):
            self.client_id = client_id
            self.authority = authority
            self.token_cache = token_cache
            self.removed = []
            state.instances.append(self)

        def get_accounts(self, username=None):
            return list(state.accounts)

        def acquire_token_silent(self, scopes, account=None, **kwargs):
            if isinstance(state.silent_result, Exception):
                raise state.silent_result
            return state.silent_result

        def initiate_device_flow(self, scopes=None, **kwargs):
            if state.init_error is not None:
                raise state.init_error
            return dict(state.flow)

        def acquire_token_by_device_flow(self, flow, **kwargs):
            if state.device_event is not None:
                state.device_event.wait(timeout=5)
            if isinstance(state.device_result, Exception):
                raise state.device_result
            return state.device_result

        def remove_account(self, account):
            self.removed.append(account)
            state.accounts = [a for a in state.accounts if a is not account]

    monkeypatch.setattr(graph_service.msal, "PublicClientApplication", FakeApp)
    return state


def connect(fake_msal, **general):
    configure(**general)
    fake_msal.accounts = [ACCOUNT]
    fake_msal.silent_result = dict(TOKEN)


class FakeResponse:
    def __init__(self, status_code, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("bad json")
        return self._payload


def install_requests(monkeypatch, responses):
    """Stub ``requests.get`` to return ``responses`` in order; records calls."""
    calls = []
    queue = list(responses)

    def fake_get(url, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def event(event_id, subject, start_hhmm, end_hhmm, **extra):
    """Graph event payload; ``extra`` keys override the defaults (e.g. ``end``)."""
    base = {
        "id": event_id,
        "subject": subject,
        "start": {"dateTime": f"{DATE}T{start_hhmm}:00.0000000", "timeZone": "Europe/Rome"},
        "end": {"dateTime": f"{DATE}T{end_hhmm}:00.0000000", "timeZone": "Europe/Rome"},
        "isAllDay": False,
        "isCancelled": False,
        "showAs": "busy",
    }
    return {**base, **extra}


def allday_event(event_id, subject, show_as="oof"):
    return event(
        event_id, subject, "00:00", "00:00",
        end={"dateTime": "2026-09-17T00:00:00.0000000", "timeZone": "Europe/Rome"},
        isAllDay=True, showAs=show_as,
    )


def start_blocked_login(fake_msal, **flow_overrides):
    fake_msal.flow = {**FLOW, "expires_at": time.time() + 900, **flow_overrides}
    fake_msal.device_event = threading.Event()
    return graph_service.start_device_login()


def finish_login(fake_msal, result):
    fake_msal.device_result = result
    if fake_msal.device_event is not None:
        fake_msal.device_event.set()
    graph_service._auth_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_not_configured_by_default(fake_msal):
    assert graph_service.is_configured() is False
    status = graph_service.auth_status()
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["pending"] is False
    assert status["account"] is None
    assert graph_service.start_device_login() is None
    meetings, error = graph_service.get_meetings(DATE)
    assert meetings is None
    assert error != graph_service.AUTH_REQUIRED
    assert "Client ID" in error
    assert fake_msal.instances == []


def test_env_is_fallback_when_settings_empty(monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_ID", "env-client")
    monkeypatch.setenv("GRAPH_TENANT_ID", "env-tenant")
    assert graph_service.client_id() == "env-client"
    assert graph_service.tenant_id() == "env-tenant"
    assert graph_service.is_configured() is True


def test_settings_take_precedence_over_env(monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_ID", "env-client")
    monkeypatch.setenv("GRAPH_TENANT_ID", "env-tenant")
    configure(tenant_id="contoso.onmicrosoft.com")
    assert graph_service.client_id() == CLIENT_ID
    assert graph_service.tenant_id() == "contoso.onmicrosoft.com"


def test_tenant_defaults_to_common():
    assert graph_service.tenant_id() == "common"


def test_msal_app_uses_settings_client_and_tenant(fake_msal):
    configure(tenant_id="contoso.onmicrosoft.com")
    graph_service.is_connected()
    app = fake_msal.instances[-1]
    assert app.client_id == CLIENT_ID
    assert app.authority == "https://login.microsoftonline.com/contoso.onmicrosoft.com"


def test_daily_minutes_from_settings():
    assert graph_service.daily_minutes() == 480
    configure(daily_hours=7.5)
    assert graph_service.daily_minutes() == 450


# ---------------------------------------------------------------------------
# Connection state / account
# ---------------------------------------------------------------------------

def test_not_connected_without_cached_accounts(fake_msal):
    configure()
    assert graph_service.is_connected() is False
    assert graph_service.get_account() is None
    status = graph_service.auth_status()
    assert status == {
        "configured": True, "connected": False, "pending": False,
        "device": None, "error": None, "account": None,
    }


def test_connected_with_cached_token(fake_msal):
    connect(fake_msal)
    assert graph_service.is_connected() is True
    assert graph_service.get_account() == {
        "username": ACCOUNT["username"], "name": ACCOUNT["username"],
    }
    status = graph_service.auth_status()
    assert status["connected"] is True
    assert status["account"]["username"] == ACCOUNT["username"]


def test_account_name_from_id_token_claims(fake_msal):
    connect(fake_msal)
    fake_msal.silent_result = {**TOKEN, "id_token_claims": {"name": "Mario Rossi"}}
    assert graph_service.get_account() == {"username": ACCOUNT["username"], "name": "Mario Rossi"}


def test_account_name_from_cached_id_token():
    payload = base64.urlsafe_b64encode(json.dumps({"name": "Anna Bianchi"}).encode()).decode().rstrip("=")
    cache = SimpleNamespace(search=lambda *args, **kwargs: [{"secret": f"h.{payload}.sig"}])
    assert graph_service._describe_account(cache, ACCOUNT, None) == {
        "username": ACCOUNT["username"], "name": "Anna Bianchi",
    }


def test_account_reported_even_when_token_expired(fake_msal):
    connect(fake_msal)
    fake_msal.silent_result = None
    assert graph_service.is_connected() is False
    status = graph_service.auth_status()
    assert status["connected"] is False
    assert status["account"]["username"] == ACCOUNT["username"]


def test_silent_acquisition_errors_are_not_raised(fake_msal):
    connect(fake_msal)
    fake_msal.silent_result = RuntimeError("network down")
    assert graph_service.is_connected() is False


# ---------------------------------------------------------------------------
# Device code login
# ---------------------------------------------------------------------------

def test_start_device_login_returns_instructions_and_reuses_pending_flow(fake_msal):
    configure()
    first = start_blocked_login(fake_msal)
    assert first == {
        "user_code": "ABCD-1234",
        "verification_uri": "https://microsoft.com/devicelogin",
        "message": FLOW["message"],
        "expires_in": 900,
    }
    assert graph_service.start_device_login() == first
    assert len(fake_msal.instances) == 1  # pending flow reused, no new MSAL app
    status = graph_service.auth_status()
    assert status["pending"] is True
    assert status["device"]["user_code"] == "ABCD-1234"
    assert status["error"] is None

    finish_login(fake_msal, dict(TOKEN))
    status = graph_service.auth_status()
    assert status["pending"] is False
    assert status["error"] is None


def test_device_login_declined_records_italian_error(fake_msal):
    configure()
    start_blocked_login(fake_msal)
    finish_login(fake_msal, {"error": "authorization_declined", "error_description": "AADSTS65004"})
    status = graph_service.auth_status()
    assert status["pending"] is False
    assert status["error"] == "Login Microsoft rifiutato dall'utente."


def test_device_login_unknown_error_uses_first_description_line(fake_msal):
    configure()
    start_blocked_login(fake_msal)
    finish_login(fake_msal, {
        "error": "invalid_grant",
        "error_description": "AADSTS70008: token expired\r\nTrace ID: abc",
    })
    assert graph_service.auth_status()["error"] == (
        "Login Microsoft non completato: AADSTS70008: token expired"
    )


def test_device_login_exception_is_captured(fake_msal):
    configure()
    start_blocked_login(fake_msal)
    finish_login(fake_msal, RuntimeError("boom"))
    error = graph_service.auth_status()["error"]
    assert error.startswith("Errore durante il login Microsoft")
    assert "boom" in error


def test_start_device_login_returns_none_when_msal_raises(fake_msal):
    configure()
    fake_msal.init_error = RuntimeError("no network")
    assert graph_service.start_device_login() is None
    status = graph_service.auth_status()
    assert status["pending"] is False
    assert status["error"].startswith("Impossibile avviare il login Microsoft")
    assert "no network" in status["error"]


def test_start_device_login_returns_none_on_error_payload(fake_msal):
    configure()
    fake_msal.flow = {"error": "invalid_client", "error_description": "AADSTS7000215: bad client"}
    assert graph_service.start_device_login() is None
    assert "AADSTS7000215" in graph_service.auth_status()["error"]


def test_expired_pending_flow_is_replaced(fake_msal):
    configure()
    start_blocked_login(fake_msal, expires_at=time.time() - 1)
    assert graph_service.auth_status()["pending"] is False
    second = start_blocked_login(fake_msal, user_code="WXYZ-0000")
    assert second["user_code"] == "WXYZ-0000"
    assert graph_service.auth_status()["pending"] is True
    finish_login(fake_msal, dict(TOKEN))


def test_reset_cache_clears_pending_flow(fake_msal):
    configure()
    start_blocked_login(fake_msal)
    graph_service.reset_cache()
    assert graph_service.auth_status()["pending"] is False
    fake_msal.device_result = dict(TOKEN)
    fake_msal.device_event.set()


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------

def _write_cache_file():
    path = config.graph_cache_file()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{}")
    return path


def test_disconnect_removes_accounts_and_cache_file(fake_msal):
    connect(fake_msal)
    path = _write_cache_file()
    graph_service.disconnect()
    assert not os.path.exists(path)
    assert fake_msal.instances[-1].removed == [ACCOUNT]
    assert graph_service.is_connected() is False


def test_disconnect_without_configuration_still_deletes_cache_file(fake_msal):
    path = _write_cache_file()
    graph_service.disconnect()
    assert not os.path.exists(path)
    assert fake_msal.instances == []


def test_disconnect_clears_pending_flow(fake_msal):
    configure()
    start_blocked_login(fake_msal)
    graph_service.disconnect()
    assert graph_service.auth_status()["pending"] is False
    fake_msal.device_result = dict(TOKEN)
    fake_msal.device_event.set()


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------

def test_get_meetings_requires_login(fake_msal):
    configure()
    assert graph_service.get_meetings(DATE) == (None, graph_service.AUTH_REQUIRED)


def test_get_meetings_rejects_invalid_date(fake_msal):
    connect(fake_msal)
    assert graph_service.get_meetings("not-a-date") == (None, "Data non valida.")


def test_get_meetings_parses_events(fake_msal, monkeypatch):
    connect(fake_msal, daily_hours=7.5, timezone="Europe/Rome")
    events = [
        event("e1", "Stand-up", "09:00", "09:30"),
        event("e2", "Cancellata", "10:00", "11:00", isCancelled=True),
        event("e3", "Troppo breve", "11:00", "11:03"),
        allday_event("e4", "Ferie"),
        allday_event("e5", "Compleanno", show_as="free"),
        event("e1", "Stand-up (duplicato)", "09:00", "09:30"),
        event("e6", "  ", "14:00", "15:30"),
    ]
    calls = install_requests(monkeypatch, [FakeResponse(200, {"value": events})])

    meetings, error = graph_service.get_meetings(DATE)

    assert error is None
    assert [m["meeting_id"] for m in meetings] == ["e1", "e4", "e6"]
    assert meetings[0] == {
        "subject": "Stand-up", "duration": 30, "duration_label": "30",
        "is_allday": False, "meeting_id": "e1", "start": "09:00", "end": "09:30",
    }
    assert meetings[1]["duration"] == 450
    assert meetings[1]["duration_label"] == "Tutto il giorno"
    assert meetings[1]["is_allday"] is True
    assert meetings[1]["start"] == "00:00"
    assert meetings[2]["subject"] == "Senza oggetto"
    assert meetings[2]["duration"] == 90

    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Prefer"] == 'outlook.timezone="Europe/Rome"'
    assert calls[0]["timeout"] == 20
    url = calls[0]["url"]
    start_utc, end_utc = config.day_bounds_utc(DATE)
    assert url.startswith("https://graph.microsoft.com/v1.0/me/calendarView?")
    assert f"startDateTime={graph_service._utc_literal(start_utc)}" in url
    assert f"endDateTime={graph_service._utc_literal(end_utc)}" in url
    assert "$select=subject,start,end,isAllDay,isCancelled,showAs" in url
    assert "$orderby=start/dateTime" in url
    assert "$top=100" in url


def test_get_meetings_follows_next_link(fake_msal, monkeypatch):
    connect(fake_msal)
    next_link = "https://graph.microsoft.com/v1.0/me/calendarView?$skip=1"
    calls = install_requests(monkeypatch, [
        FakeResponse(200, {"value": [event("e1", "Uno", "09:00", "10:00")], "@odata.nextLink": next_link}),
        FakeResponse(200, {"value": [event("e2", "Due", "11:00", "12:00")]}),
    ])
    meetings, error = graph_service.get_meetings(DATE)
    assert error is None
    assert [m["subject"] for m in meetings] == ["Uno", "Due"]
    assert len(calls) == 2
    assert calls[1]["url"] == next_link


def test_get_meetings_401_means_auth_required(fake_msal, monkeypatch):
    connect(fake_msal)
    install_requests(monkeypatch, [FakeResponse(401, {"error": {"code": "InvalidAuthenticationToken"}})])
    assert graph_service.get_meetings(DATE) == (None, graph_service.AUTH_REQUIRED)


def test_get_meetings_other_status_gives_italian_error(fake_msal, monkeypatch):
    connect(fake_msal)
    install_requests(monkeypatch, [
        FakeResponse(403, {"error": {"code": "ErrorAccessDenied", "message": "Access is denied"}}),
    ])
    meetings, error = graph_service.get_meetings(DATE)
    assert meetings is None
    assert error == "Errore Microsoft Graph (403): Access is denied"


def test_get_meetings_network_error(fake_msal, monkeypatch):
    connect(fake_msal)
    install_requests(monkeypatch, [requests.ConnectionError("offline")])
    meetings, error = graph_service.get_meetings(DATE)
    assert meetings is None
    assert error.startswith("Impossibile contattare Microsoft Graph")


def test_get_meetings_invalid_json_body(fake_msal, monkeypatch):
    connect(fake_msal)
    install_requests(monkeypatch, [FakeResponse(200, bad_json=True)])
    assert graph_service.get_meetings(DATE) == (None, "Risposta di Microsoft Graph non valida.")


def test_parse_events_is_pure_and_tolerant():
    assert graph_service.parse_events(None, 480) == []
    assert graph_service.parse_events([None, "junk", {}], 480) == []
    source = [event("e1", "Uno", "09:00", "10:00")]
    meetings = graph_service.parse_events(source, 480)
    assert meetings[0]["duration"] == 60
    assert source == [event("e1", "Uno", "09:00", "10:00")]  # input untouched


def test_login_completing_after_disconnect_is_not_persisted(fake_msal, monkeypatch):
    """disconnect() while the device thread is blocked must not be undone by a late login."""
    configure()
    saved = []
    monkeypatch.setattr(graph_service, "_save_cache", lambda cache: saved.append(cache))
    start_blocked_login(fake_msal)
    graph_service.disconnect()
    saved.clear()
    finish_login(fake_msal, dict(TOKEN))
    assert saved == []  # the cancelled flow never touches the cache file
    status = graph_service.auth_status()
    assert status["pending"] is False and status["error"] is None
    assert not os.path.exists(config.graph_cache_file())


def test_device_login_exit_condition_stops_after_cancel(fake_msal):
    """The exit_condition handed to MSAL keeps polling while the flow is current, stops after disconnect."""
    configure()
    captured = {}
    fake_app_cls = graph_service.msal.PublicClientApplication
    original = fake_app_cls.acquire_token_by_device_flow

    def acquire(self, flow, **kwargs):
        captured["exit_condition"] = kwargs.get("exit_condition")
        return original(self, flow, **kwargs)

    fake_app_cls.acquire_token_by_device_flow = acquire
    start_blocked_login(fake_msal)  # the worker thread blocks inside acquire()
    for _ in range(200):
        if "exit_condition" in captured:
            break
        time.sleep(0.01)
    exit_condition = captured["exit_condition"]
    current = graph_service._pending_flow
    assert callable(exit_condition) and current is not None
    assert exit_condition(current) is False  # current and not expired: keep polling
    graph_service.disconnect()
    assert exit_condition(current) is True   # cancelled: stop polling
    finish_login(fake_msal, {"error": "authorization_pending"})
    assert graph_service.auth_status()["error"] is None  # a cancelled flow records no error

"""
Microsoft Graph calendar integration (MSAL device-code flow).

Works everywhere (Docker, Linux, native Windows) because it talks to the
Microsoft cloud via REST instead of the Outlook desktop COM bridge
(see ``outlook_service`` for that fallback).

Auth: MSAL *device code flow*. The user signs in once at microsoft.com with a
code shown by the app; the token is persisted in an MSAL serializable cache
(``config.graph_cache_file()``) and refreshed silently afterwards. Requires an
Azure AD *public client* app registration with the delegated
``Calendars.Read`` permission.

``client_id`` / ``tenant_id`` are read from settings (``microsoft.client_id``,
``microsoft.tenant_id``) on EVERY call; the ``GRAPH_CLIENT_ID`` /
``GRAPH_TENANT_ID`` environment variables are used only as fallback when the
settings values are empty.
"""
from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Optional

import requests

try:
    import msal

    MSAL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the environment
    msal = None
    MSAL_AVAILABLE = False

import config
from services import settings_service

log = logging.getLogger(__name__)

AUTH_REQUIRED = "AUTH_REQUIRED"
SCOPES = ["Calendars.Read"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORITY_BASE = "https://login.microsoftonline.com"
DEFAULT_TENANT = "common"
DEFAULT_DAILY_HOURS = 8.0
MIN_MEETING_MINUTES = 5
HTTP_TIMEOUT = 20
PAGE_SIZE = 100
MAX_PAGES = 20  # safety cap when following @odata.nextLink
CALENDAR_SELECT = "subject,start,end,isAllDay,isCancelled,showAs"
ALLDAY_LABEL = "Tutto il giorno"
NO_SUBJECT = "Senza oggetto"

# Pending device-code flow state (single-user local app).
_lock = threading.Lock()
_pending_flow: Optional[dict] = None
_auth_thread: Optional[threading.Thread] = None
_auth_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Settings helpers (read at call time, never cached)
# ---------------------------------------------------------------------------

def _setting_or_env(path: str, env_var: str, default: str = "") -> str:
    """Settings value if non-empty, else the env variable, else ``default``."""
    value = settings_service.get(path, "")
    text = str(value).strip() if value is not None else ""
    if text:
        return text
    return (os.getenv(env_var) or "").strip() or default


def client_id() -> str:
    return _setting_or_env("microsoft.client_id", "GRAPH_CLIENT_ID")


def tenant_id() -> str:
    return _setting_or_env("microsoft.tenant_id", "GRAPH_TENANT_ID", DEFAULT_TENANT)


def _authority() -> str:
    return f"{AUTHORITY_BASE}/{tenant_id()}"


def _timezone_name() -> str:
    value = settings_service.get("general.timezone", "")
    text = str(value).strip() if value else ""
    return text or config.DEFAULT_TIMEZONE


def daily_minutes() -> int:
    """Length of an all-day meeting: ``general.daily_hours * 60`` (default 480)."""
    raw = settings_service.get("general.daily_hours", DEFAULT_DAILY_HOURS)
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        hours = DEFAULT_DAILY_HOURS
    if hours <= 0:
        hours = DEFAULT_DAILY_HOURS
    return int(round(hours * 60))


def is_configured() -> bool:
    """True if Graph can be used at all (library present + client id set)."""
    return MSAL_AVAILABLE and bool(client_id())


# ---------------------------------------------------------------------------
# Token cache persistence
# ---------------------------------------------------------------------------

def _load_cache():
    cache = msal.SerializableTokenCache()
    path = config.graph_cache_file()
    if not os.path.exists(path):
        return cache
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cache.deserialize(handle.read())
    except (OSError, ValueError) as exc:
        log.warning("Graph token cache unreadable (%s): %s", path, exc)
    return cache


def _save_cache(cache) -> None:
    if not cache.has_state_changed:
        return
    path = config.graph_cache_file()
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(cache.serialize())
        os.replace(tmp_path, path)
        _restrict_permissions(path)
    except OSError as exc:
        log.error("Cannot persist Graph token cache (%s): %s", path, exc)


def _restrict_permissions(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:  # pragma: no cover - filesystem dependent
        log.debug("chmod 600 not applied to %s: %s", path, exc)


def _build_app(cache):
    return msal.PublicClientApplication(
        client_id(), authority=_authority(), token_cache=cache
    )


# ---------------------------------------------------------------------------
# Account / connection probe
# ---------------------------------------------------------------------------

def _acquire_silent(app, account) -> Optional[dict]:
    """Token dict from cache (refreshing if needed) or None; never raises."""
    try:
        return app.acquire_token_silent(SCOPES, account=account)
    except Exception as exc:  # MSAL surfaces network/JSON errors as exceptions
        log.warning("Silent Graph token acquisition failed: %s", exc)
        return None


def _jwt_payload(token: str) -> dict:
    """Decode the payload of a JWT without validating it (claims only)."""
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError) as exc:
        log.debug("Cannot decode ID token payload: %s", exc)
        return {}
    return claims if isinstance(claims, dict) else {}


def _id_token_claims(cache, account: dict) -> dict:
    """Claims of the cached ID token for ``account`` ({} when unavailable)."""
    try:
        query = {"home_account_id": account.get("home_account_id")}
        entries = list(
            cache.search(msal.TokenCache.CredentialType.ID_TOKEN, query=query)
        )
    except Exception as exc:  # cache API differences / corrupt entries
        log.debug("ID token lookup failed: %s", exc)
        return {}
    for entry in entries:
        claims = _jwt_payload(entry.get("secret") or "")
        if claims:
            return claims
    return {}


def _describe_account(cache, account: dict, token_result: Optional[dict]) -> dict:
    """Build ``{"username", "name"}`` from the MSAL account and ID token claims."""
    claims = _id_token_claims(cache, account) or (token_result or {}).get(
        "id_token_claims"
    ) or {}
    username = (
        account.get("username")
        or claims.get("preferred_username")
        or claims.get("email")
        or ""
    )
    return {"username": username, "name": claims.get("name") or username}


def _probe() -> dict:
    """Load the cache once; return ``{"token": dict|None, "account": dict|None}``."""
    if not is_configured():
        return {"token": None, "account": None}
    cache = _load_cache()
    app = _build_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        return {"token": None, "account": None}
    result = _acquire_silent(app, accounts[0])
    _save_cache(cache)
    token = result if result and "access_token" in result else None
    return {"token": token, "account": _describe_account(cache, accounts[0], result)}


def is_connected() -> bool:
    """True if a valid (cached or silently refreshable) token is available."""
    return _probe()["token"] is not None


def get_account() -> Optional[dict]:
    """``{"username", "name"}`` of the cached account, or None when none is cached."""
    return _probe()["account"]


# ---------------------------------------------------------------------------
# Device code login
# ---------------------------------------------------------------------------

def _flow_summary(flow: dict) -> dict:
    return {
        "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri"),
        "message": flow.get("message"),
        "expires_in": flow.get("expires_in"),
    }


def _flow_expired(flow: dict) -> bool:
    expires_at = flow.get("expires_at")
    return expires_at is not None and float(expires_at) <= time.time()


def _login_error_text(result: dict) -> str:
    """Italian, user-facing description of a failed device login."""
    code = str(result.get("error") or "")
    known = {
        "authorization_declined": "Login Microsoft rifiutato dall'utente.",
        "expired_token": "Codice di accesso scaduto: avvia di nuovo il login Microsoft.",
        "bad_verification_code": "Codice di verifica non valido.",
        "authorization_pending": "Login Microsoft non completato.",
    }
    if code in known:
        return known[code]
    description = str(result.get("error_description") or "").strip()
    detail = description.splitlines()[0] if description else (code or "risposta non valida")
    return f"Login Microsoft non completato: {detail}"


def _is_current_flow(flow: dict) -> bool:
    with _lock:
        return _pending_flow is flow


def _run_device_login(flow: dict, app, cache) -> None:
    """Background thread: block until the user completes login, then persist.

    Polling stops early (``exit_condition``) when the flow is no longer the
    current one (disconnect/reset); a token obtained for a cancelled flow is
    never written to the cache file, so "Disconnetti" cannot be undone by a
    late login.
    """
    global _pending_flow, _auth_error
    error = None
    try:
        result = app.acquire_token_by_device_flow(
            flow,
            exit_condition=lambda f: _flow_expired(f) or not _is_current_flow(flow),
        ) or {}
        if "access_token" not in result and _is_current_flow(flow):
            error = _login_error_text(result)
            log.warning("Microsoft device login failed: %s", error)
    except Exception as exc:
        log.exception("Microsoft device login raised an exception")
        error = f"Errore durante il login Microsoft: {exc}"
    finally:
        # Decide and persist under the lock so disconnect() (which also takes the
        # lock) can never interleave between the check and the cache write.
        with _lock:
            current = _pending_flow is flow
            if current:
                _save_cache(cache)
                _pending_flow = None
                _auth_error = error
        if not current:
            log.info("Microsoft device login finished after cancellation; result discarded")


def _initiate_flow(app) -> "tuple[Optional[dict], Optional[str]]":
    try:
        flow = app.initiate_device_flow(scopes=SCOPES)
    except Exception as exc:
        log.error("Cannot start Microsoft device flow: %s", exc)
        return None, f"Impossibile avviare il login Microsoft: {exc}"
    if not isinstance(flow, dict) or "user_code" not in flow:
        payload = flow if isinstance(flow, dict) else {}
        detail = payload.get("error_description") or payload.get("error") or "risposta non valida"
        log.error("Device flow response without user_code: %s", detail)
        return None, f"Impossibile avviare il login Microsoft: {detail}"
    return flow, None


def start_device_login() -> Optional[dict]:
    """Begin (or reuse) a device-code flow; returns user-facing instructions."""
    global _pending_flow, _auth_thread, _auth_error
    if not is_configured():
        return None
    with _lock:
        if _pending_flow is not None and not _flow_expired(_pending_flow):
            return _flow_summary(_pending_flow)
        cache = _load_cache()
        app = _build_app(cache)
        flow, error = _initiate_flow(app)
        if flow is None:
            _auth_error = error
            return None
        _pending_flow = flow
        _auth_error = None
        _auth_thread = threading.Thread(
            target=_run_device_login,
            args=(flow, app, cache),
            name="graph-device-login",
            daemon=True,
        )
        _auth_thread.start()
        return _flow_summary(flow)


def auth_status() -> dict:
    """Snapshot of the login state for polling from the UI."""
    with _lock:
        pending = _pending_flow is not None and not _flow_expired(_pending_flow)
        device = _flow_summary(_pending_flow) if pending else None
        error = _auth_error
    probe = _probe()
    return {
        "configured": is_configured(),
        "connected": probe["token"] is not None,
        "pending": pending,
        "device": device,
        "error": error,
        "account": probe["account"],
    }


def _remove_cached_accounts() -> None:
    cache = _load_cache()
    app = _build_app(cache)
    for account in app.get_accounts():
        try:
            app.remove_account(account)
        except Exception as exc:
            log.warning("Cannot remove MSAL account %s: %s", account.get("username"), exc)
    _save_cache(cache)


def disconnect() -> None:
    """Remove all accounts from the MSAL cache and delete the cache file.

    Raises ``OSError`` if the cache file exists but cannot be deleted.
    """
    global _pending_flow, _auth_error
    with _lock:
        # Cancel any running device flow first, then wipe the cache: a login that
        # completes afterwards finds the flow cancelled and discards its token.
        _pending_flow = None
        _auth_error = None
        if is_configured():
            _remove_cached_accounts()
        path = config.graph_cache_file()
        if os.path.exists(path):
            os.remove(path)
    log.info("Microsoft account disconnected")


def reset_cache() -> None:
    """Clear in-memory device-flow state (tests)."""
    global _pending_flow, _auth_thread, _auth_error
    with _lock:
        _pending_flow = None
        _auth_thread = None
        _auth_error = None


# ---------------------------------------------------------------------------
# Calendar fetch
# ---------------------------------------------------------------------------

def _utc_literal(value: str) -> str:
    """Make sure a naive UTC timestamp carries an explicit ``Z`` suffix."""
    text = str(value)
    if text.endswith("Z") or "+" in text[10:] or "-" in text[10:]:
        return text
    return f"{text}Z"


def _calendar_view_url(start_utc: str, end_utc: str) -> str:
    return (
        f"{GRAPH_BASE}/me/calendarView"
        f"?startDateTime={_utc_literal(start_utc)}&endDateTime={_utc_literal(end_utc)}"
        f"&$select={CALENDAR_SELECT}&$orderby=start/dateTime&$top={PAGE_SIZE}"
    )


def _graph_error(resp) -> str:
    try:
        message = ((resp.json() or {}).get("error") or {}).get("message") or ""
    except ValueError:
        message = ""
    suffix = f": {message}" if message else "."
    return f"Errore Microsoft Graph ({resp.status_code}){suffix}"


def _fetch_events(url: str, headers: dict) -> "tuple[Optional[list], Optional[str]]":
    """Follow ``@odata.nextLink`` pages; returns ``(events, error)``."""
    events: list = []
    next_url: Optional[str] = url
    for _ in range(MAX_PAGES):
        if not next_url:
            return events, None
        resp = requests.get(next_url, headers=headers, timeout=HTTP_TIMEOUT)
        if resp.status_code == 401:
            return None, AUTH_REQUIRED
        if resp.status_code != 200:
            error = _graph_error(resp)
            log.error("Graph calendarView failed: %s", error)
            return None, error
        payload = resp.json() or {}
        events = events + list(payload.get("value") or [])
        next_url = payload.get("@odata.nextLink")
    log.warning("Graph calendarView pagination stopped after %d pages", MAX_PAGES)
    return events, None


def _parse_time(node: Any) -> Optional[datetime.datetime]:
    """Parse a Graph ``{"dateTime","timeZone"}`` node into a naive datetime."""
    raw = node.get("dateTime") if isinstance(node, dict) else None
    if not raw:
        return None
    # Graph returns e.g. "2026-06-25T09:00:00.0000000"; trim fraction / Z.
    cleaned = str(raw).split(".")[0].rstrip("Z")
    try:
        return datetime.datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _hhmm(value: Optional[datetime.datetime]) -> Optional[str]:
    return value.strftime("%H:%M") if value else None


def _minutes_between(start: Optional[datetime.datetime], end: Optional[datetime.datetime]) -> int:
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def _event_to_meeting(event: Any, allday_minutes: int) -> Optional[dict]:
    """Convert one Graph event to the v2 meeting shape (None = skip)."""
    if not isinstance(event, dict) or event.get("isCancelled"):
        return None
    is_allday = bool(event.get("isAllDay"))
    if is_allday and str(event.get("showAs") or "").lower() == "free":
        return None  # all-day "free" items are reminders/birthdays, not meetings
    start_dt = _parse_time(event.get("start"))
    end_dt = _parse_time(event.get("end"))
    duration = allday_minutes if is_allday else _minutes_between(start_dt, end_dt)
    if not is_allday and duration < MIN_MEETING_MINUTES:
        return None
    subject = str(event.get("subject") or "").strip() or NO_SUBJECT
    return {
        "subject": subject,
        "duration": duration,
        "duration_label": ALLDAY_LABEL if is_allday else str(duration),
        "is_allday": is_allday,
        "meeting_id": str(event.get("id") or "") or subject,
        "start": _hhmm(start_dt),
        "end": _hhmm(end_dt),
    }


def parse_events(events: Optional[list], allday_minutes: int) -> list:
    """Pure: Graph events → meetings (deduped by id, cancelled/short skipped)."""
    meetings: list = []
    seen: set = set()
    for event in events or []:
        event_id = str(event.get("id") or "") if isinstance(event, dict) else ""
        if event_id and event_id in seen:
            continue
        seen = seen | {event_id} if event_id else seen
        meeting = _event_to_meeting(event, allday_minutes)
        if meeting is not None:
            meetings = meetings + [meeting]
    return meetings


def get_meetings(date_iso: str) -> "tuple[Optional[list], Optional[str]]":
    """
    Fetch the meetings of the local day ``date_iso`` from Microsoft Graph.

    Returns ``(meetings, None)`` or ``(None, error)``; ``error`` is
    ``AUTH_REQUIRED`` when the user must (re)complete the device login.
    """
    if not is_configured():
        return None, "Microsoft Graph non configurato: imposta il Client ID nelle impostazioni."
    try:
        datetime.date.fromisoformat(str(date_iso))
    except (TypeError, ValueError):
        return None, "Data non valida."
    token = _probe()["token"]
    if token is None:
        return None, AUTH_REQUIRED
    start_utc, end_utc = config.day_bounds_utc(date_iso)
    headers = {
        "Authorization": f"Bearer {token['access_token']}",
        "Prefer": f'outlook.timezone="{_timezone_name()}"',
    }
    try:
        events, error = _fetch_events(_calendar_view_url(start_utc, end_utc), headers)
    except requests.RequestException as exc:
        log.error("Graph request failed: %s", exc)
        return None, f"Impossibile contattare Microsoft Graph: {exc}"
    except ValueError as exc:
        log.error("Graph returned an invalid JSON body: %s", exc)
        return None, "Risposta di Microsoft Graph non valida."
    if error:
        return None, error
    return parse_events(events, daily_minutes()), None

"""
Graph Service — riunioni dal calendario via Microsoft Graph API.

A differenza dell'integrazione COM ([outlook_service]), questa funziona ovunque
(Docker, Linux, server) perche' parla con il cloud Microsoft via REST, senza
dipendere da Outlook desktop.

Auth: device code flow (MSAL). L'utente fa login una volta su microsoft.com con
un codice mostrato dall'app; il token viene salvato in una cache persistente e
rinnovato automaticamente. Richiede una registrazione "public client" su Azure AD
con permesso delegato `Calendars.Read`.

Config via env:
  GRAPH_CLIENT_ID  -> Application (client) ID dell'app Azure AD
  GRAPH_TENANT_ID  -> tenant ("common", "organizations", "consumers" o un GUID)
"""

import os
import datetime
import threading

import requests

try:
    import msal
    MSAL_AVAILABLE = True
except ImportError:
    MSAL_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.getenv("TIMESHEET_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
CACHE_FILE = os.path.join(DATA_DIR, ".graph_token_cache.bin")

CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "").strip()
TENANT_ID = (os.getenv("GRAPH_TENANT_ID", "common").strip() or "common")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["Calendars.Read"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Signal returned by get_meetings() when the user must complete device login.
AUTH_REQUIRED = "AUTH_REQUIRED"

# Pending device-code flow state (single-user local app).
_lock = threading.Lock()
_pending_flow = None     # dict returned by initiate_device_flow()
_auth_thread = None
_auth_error = None


def is_configured():
    """True if Graph can be used at all (library present + client id set)."""
    return MSAL_AVAILABLE and bool(CLIENT_ID)


# ---- Token cache ----

def _load_cache():
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache.deserialize(f.read())
        except OSError:
            pass
    return cache


def _save_cache(cache):
    if cache.has_state_changed:
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                f.write(cache.serialize())
        except OSError:
            pass


def _build_app(cache):
    return msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )


def _acquire_silent(app):
    """Return a token dict from cache (refreshing if needed), or None."""
    accounts = app.get_accounts()
    if not accounts:
        return None
    return app.acquire_token_silent(SCOPES, account=accounts[0])


def is_authenticated():
    """True if a valid (cached/refreshable) token is available."""
    if not is_configured():
        return False
    cache = _load_cache()
    app = _build_app(cache)
    result = _acquire_silent(app)
    _save_cache(cache)
    return bool(result and "access_token" in result)


# ---- Device code login ----

def _run_device_login(flow):
    """Background: block until the user completes login, then cache the token."""
    global _pending_flow, _auth_error
    cache = _load_cache()
    app = _build_app(cache)
    try:
        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            _auth_error = result.get("error_description") or result.get("error") \
                or "Login non completato."
    except Exception as e:
        _auth_error = f"Errore login Graph: {e}"
    finally:
        _save_cache(cache)
        with _lock:
            _pending_flow = None


def start_device_login():
    """Begin (or reuse) a device-code flow. Returns user-facing instructions."""
    global _pending_flow, _auth_thread, _auth_error
    if not is_configured():
        return None

    with _lock:
        if _pending_flow is not None:
            return {
                "user_code": _pending_flow.get("user_code"),
                "verification_uri": _pending_flow.get("verification_uri"),
                "message": _pending_flow.get("message"),
                "expires_in": _pending_flow.get("expires_in"),
            }

        cache = _load_cache()
        app = _build_app(cache)
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            return None
        _pending_flow = flow
        _auth_error = None
        _auth_thread = threading.Thread(
            target=_run_device_login, args=(flow,), daemon=True
        )
        _auth_thread.start()

    return {
        "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri"),
        "message": flow.get("message"),
        "expires_in": flow.get("expires_in"),
    }


def auth_status():
    """Snapshot of the login state for polling from the UI."""
    with _lock:
        pending = _pending_flow is not None
        err = _auth_error
    return {
        "configured": is_configured(),
        "authenticated": is_authenticated(),
        "pending": pending,
        "error": err,
    }


# ---- Calendar fetch ----

def _day_bounds_utc(target_date):
    """Return (start, end) ISO-8601 UTC strings covering the local day."""
    local_start = datetime.datetime.combine(
        target_date, datetime.time.min
    ).astimezone()
    local_end = datetime.datetime.combine(
        target_date, datetime.time.max
    ).astimezone()
    to_utc = lambda d: d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return to_utc(local_start), to_utc(local_end)


def _parse_graph_dt(node):
    """Parse a Graph dateTime node ({'dateTime','timeZone'}) to naive datetime."""
    if not node or not node.get("dateTime"):
        return None
    raw = node["dateTime"]
    # Graph returns e.g. "2026-06-25T09:00:00.0000000"; trim fractional seconds.
    raw = raw.split(".")[0]
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def get_meetings(account_email="", target_date=None):
    """
    Fetch meetings for `target_date` (default today) from Microsoft Graph.
    Returns (meetings | None, error | None). On missing login, error is
    AUTH_REQUIRED so the caller can trigger the device-code flow.
    """
    if not is_configured():
        return None, "Microsoft Graph non configurato (manca GRAPH_CLIENT_ID)."

    cache = _load_cache()
    app = _build_app(cache)
    result = _acquire_silent(app)
    _save_cache(cache)
    if not result or "access_token" not in result:
        return None, AUTH_REQUIRED

    target_date = target_date or datetime.date.today()
    start_utc, end_utc = _day_bounds_utc(target_date)

    headers = {
        "Authorization": f"Bearer {result['access_token']}",
        "Prefer": 'outlook.timezone="UTC"',
    }
    url = (
        f"{GRAPH_BASE}/me/calendarView"
        f"?startDateTime={start_utc}&endDateTime={end_utc}"
        f"&$select=subject,start,end,isAllDay,isCancelled"
        f"&$orderby=start/dateTime&$top=100"
    )

    meetings = []
    seen_ids = set()
    try:
        while url:
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code == 401:
                return None, AUTH_REQUIRED
            if resp.status_code != 200:
                return None, f"Errore Graph API ({resp.status_code})."
            payload = resp.json()
            for ev in payload.get("value", []):
                meeting_id = ev.get("id") or ""
                if meeting_id in seen_ids:
                    continue
                seen_ids.add(meeting_id)

                if ev.get("isCancelled"):
                    continue

                subject = (ev.get("subject") or "").strip() or "Senza oggetto"
                is_allday = bool(ev.get("isAllDay"))

                start_dt = _parse_graph_dt(ev.get("start"))
                end_dt = _parse_graph_dt(ev.get("end"))
                if is_allday:
                    duration = 480
                elif start_dt and end_dt:
                    duration = int((end_dt - start_dt).total_seconds() // 60)
                else:
                    duration = 0

                if not is_allday and duration < 5:
                    continue

                meetings.append({
                    "subject": subject,
                    "duration": duration,
                    "duration_label": "Tutto il giorno" if is_allday else str(duration),
                    "is_allday": is_allday,
                    "meeting_id": meeting_id or subject,
                })
            url = payload.get("@odata.nextLink")
    except requests.RequestException as e:
        return None, f"Impossibile contattare Microsoft Graph: {e}"

    return meetings, None

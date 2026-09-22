"""GitHub integration service (spec §5).

OAuth *device flow* (background polling thread) and Personal Access Token login,
repository listing (10-minute in-memory cache), daily activity (user events +
default-branch commits) mapped to commits / pull requests / issues, pure entry
drafts and the import of a day's activity into the daily history.

The token record is stored in ``secrets.json`` via ``settings_service`` under the
key ``github_token``. All HTTP goes through one ``requests.Session`` per call chain
(``_session``) and always via ``Session.request`` so tests can patch that method.
"""

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests

import config
from services import settings_service, state_service

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
OAUTH_SCOPES = "repo read:user"
AUTH_REQUIRED = "AUTH_REQUIRED"
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Note2TimeSheet/2.0",
}

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_VERIFICATION_URI = "https://github.com/login/device"
SECRET_KEY = "github_token"
HTTP_TIMEOUT = 20
EVENTS_PER_PAGE = 100
EVENTS_MAX_PAGES = 3
REPOS_PER_PAGE = 100
REPOS_MAX_PAGES = 5
REPOS_AFFILIATION = "owner,collaborator,organization_member"
REPO_FIELDS = ("full_name", "private", "pushed_at", "default_branch", "description")
USER_FIELDS = ("login", "name", "avatar_url", "html_url")
COMMIT_REPOS_CAP = 25
REPO_CACHE_TTL_SECONDS = 600
MAX_COMMIT_LINES = 30
MAX_MESSAGE_CHARS = 120
DEFAULT_POLL_INTERVAL = 5
DEFAULT_DEVICE_EXPIRES_IN = 900
SLOW_DOWN_EXTRA_SECONDS = 5
MAX_POLL_NETWORK_FAILURES = 3

# User-facing messages (Italian).
AUTH_MESSAGE = "Autenticazione GitHub richiesta: collega l'account nelle impostazioni."
TOKEN_INVALID = "Token non valido"
RATE_LIMIT_MESSAGE = "Limite API GitHub raggiunto, riprova più tardi"
NOT_CONFIGURED_MESSAGE = "Configura il Client ID GitHub nelle impostazioni"
DEVICE_ERRORS = {
    "expired_token": "Codice scaduto: avvia di nuovo la connessione a GitHub.",
    "access_denied": "Accesso negato: la richiesta è stata rifiutata su GitHub.",
    "incorrect_device_code": "Codice dispositivo non valido.",
    "incorrect_client_credentials": "Client ID GitHub non valido: verifica le impostazioni.",
    "unsupported_grant_type": "Tipo di autorizzazione non supportato da GitHub.",
    "device_flow_disabled": "Device Flow non abilitato nella OAuth App GitHub.",
}
PR_ACTION_LABELS = {"opened": "aperta", "reopened": "riaperta", "closed": "chiusa", "merged": "unita",
                    "reviewed": "revisionata", "commented": "commentata"}
ISSUE_ACTION_LABELS = {"opened": "aperta", "reopened": "riaperta", "closed": "chiusa", "commented": "commentata"}
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(Exception):
    """Upstream/API failure with an Italian, user-facing message."""


class GitHubAuthError(GitHubError):
    """The stored credentials are missing or rejected by GitHub."""


# --- In-memory state (single-user app). The dicts are replaced under _lock and never
# --- mutated, so unlocked reads of the module globals are safe.
_lock = threading.Lock()
_flow_state = {"pending": None, "error": None}
_repo_cache = {"login": None, "fetched_at": 0.0, "repos": None}


def reset_cache():
    """Forget the pending device flow and the repository cache (tests)."""
    _set_flow_state(pending=None, error=None)
    _set_repo_cache(None, None)


def _set_flow_state(**changes):
    global _flow_state
    with _lock:
        _flow_state = {**_flow_state, **changes}


def _set_repo_cache(login, repos):
    global _repo_cache
    with _lock:
        _repo_cache = {"login": login, "fetched_at": time.time(), "repos": repos}


def _cached_repos(login):
    cache = _repo_cache
    fresh = time.time() - cache["fetched_at"] < REPO_CACHE_TTL_SECONDS
    return cache["repos"] if cache["repos"] is not None and cache["login"] == login and fresh else None


# --- Configuration / credentials

def _client_id():
    value = settings_service.get("github.client_id", "") or os.getenv("GITHUB_CLIENT_ID", "")
    return str(value or "").strip()


def is_configured():
    """True when an OAuth App client id is available (settings or env)."""
    return bool(_client_id())


def _token_record():
    record = settings_service.get_secret(SECRET_KEY)
    return record if isinstance(record, dict) and record.get("access_token") else None


def is_connected():
    return _token_record() is not None


def _identity_from(record):
    return {key: value for key, value in record.items() if key != "access_token"}


def get_identity():
    """Stored token record without the access token, or None."""
    record = _token_record()
    return _identity_from(record) if record else None


def _store_token(record):
    settings_service.set_secret(SECRET_KEY, record)
    _set_repo_cache(None, None)
    logger.info("GitHub account connected (%s, %s)", record.get("login"), record.get("auth_method"))


def _clear_token(reason):
    settings_service.set_secret(SECRET_KEY, None)
    _set_repo_cache(None, None)
    logger.warning("GitHub token cleared: %s", reason)


def disconnect():
    """Forget the stored token, any pending device flow and cached repos."""
    global _flow_state
    with _lock:
        # Cancel the flow and clear the token atomically w.r.t. _store_token_if_current().
        _flow_state = {"pending": None, "error": None}
        settings_service.set_secret(SECRET_KEY, None)
    _set_repo_cache(None, None)
    logger.warning("GitHub token cleared: disconnect requested")


def _store_token_if_current(pending, record):
    """Persist ``record`` only if ``pending`` is still the active flow (atomic with disconnect())."""
    global _flow_state
    with _lock:
        current = _flow_state["pending"]
        if current is None or current.get("device_code") != pending.get("device_code"):
            return False
        settings_service.set_secret(SECRET_KEY, record)
        _flow_state = {"pending": None, "error": None}
    _set_repo_cache(None, None)
    logger.info("GitHub account connected (%s, %s)", record.get("login"), record.get("auth_method"))
    return True


def _build_token_record(token_data, user, auth_method):
    return {
        "access_token": token_data["access_token"],
        "token_type": token_data.get("token_type") or "bearer",
        "scope": token_data.get("scope") or "",
        **{field: user.get(field) or "" for field in USER_FIELDS},
        "auth_method": auth_method,
        "connected_at": config.now_local().isoformat(timespec="seconds"),
    }


# --- HTTP helpers

def _session(token=None):
    """New requests.Session with the GitHub API headers (and bearer token)."""
    session = requests.Session()
    session.headers.update(API_HEADERS)
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def _request(session, method, url, **kwargs):
    try:
        return session.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        logger.warning("GitHub request failed: %s %s (%s)", method, url, exc)
        raise GitHubError(f"Impossibile contattare GitHub: {exc}") from exc


def _json(resp):
    try:
        return resp.json()
    except ValueError as exc:
        raise GitHubError("Risposta GitHub non valida (JSON non leggibile).") from exc


def _error_detail(resp):
    try:
        message = _json(resp).get("message")
    except (GitHubError, AttributeError):
        return ""
    return f": {message}" if message else ""


def _raise_for_api_error(resp):
    """Map an API error response to GitHubAuthError / GitHubError."""
    status = resp.status_code
    if status < 400:
        return
    if status == 401:
        _clear_token("GitHub API answered 401 (token revoked or expired)")
        raise GitHubAuthError(AUTH_MESSAGE)
    if status in (403, 429) and str(resp.headers.get("X-RateLimit-Remaining")) == "0":
        raise GitHubError(RATE_LIMIT_MESSAGE)
    logger.warning("GitHub API error %s on %s", status, getattr(resp, "url", "?"))
    raise GitHubError(f"Errore API GitHub ({status}){_error_detail(resp)}")


def _next_link(resp):
    match = _NEXT_LINK_RE.search(resp.headers.get("Link") or "")
    return match.group(1) if match else None


def _fetch_user(session):
    """GET /user → (user dict, granted scopes). 401 → GitHubAuthError(TOKEN_INVALID)."""
    resp = _request(session, "GET", f"{GITHUB_API}/user")
    if resp.status_code == 401:
        raise GitHubAuthError(TOKEN_INVALID)
    _raise_for_api_error(resp)
    user = _json(resp)
    if not isinstance(user, dict) or not user.get("login"):
        raise GitHubError("Risposta GitHub non valida (profilo utente).")
    return user, resp.headers.get("X-OAuth-Scopes") or ""


# --- Device flow

def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_expired(pending):
    return time.time() >= pending.get("expires_at", 0)


def _public_device(pending):
    return {key: pending[key] for key in ("user_code", "verification_uri", "expires_in", "interval")}


def _request_device_code(client_id):
    resp = _request(_session(), "POST", DEVICE_CODE_URL, headers={"Accept": "application/json"},
                    data={"client_id": client_id, "scope": OAUTH_SCOPES})
    if resp.status_code != 200:
        raise GitHubError(f"GitHub ha rifiutato la richiesta del codice ({resp.status_code}){_error_detail(resp)}")
    data = _json(resp)
    if not isinstance(data, dict) or not data.get("device_code") or not data.get("user_code"):
        error = data.get("error") if isinstance(data, dict) else None
        raise GitHubError(DEVICE_ERRORS.get(error) or "Risposta GitHub non valida (codice dispositivo).")
    expires_in = _to_int(data.get("expires_in"), DEFAULT_DEVICE_EXPIRES_IN)
    return {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri") or DEFAULT_VERIFICATION_URI,
        "expires_in": expires_in,
        "interval": _to_int(data.get("interval"), DEFAULT_POLL_INTERVAL),
        "expires_at": time.time() + expires_in,
    }


def _start_thread(target, *args):
    thread = threading.Thread(target=target, args=args, daemon=True, name="github-device-flow")
    thread.start()
    return thread


def start_device_login():
    """Begin (or reuse) a device-code flow; returns the public device info.

    None when not configured or when GitHub is unreachable (``auth_status()["error"]`` is set).
    """
    client_id = _client_id()
    if not client_id:
        return None
    pending = _flow_state["pending"]
    if pending and not _is_expired(pending):
        return _public_device(pending)
    try:
        pending = _request_device_code(client_id)
    except GitHubError as exc:
        logger.warning("Device code request failed: %s", exc)
        _set_flow_state(pending=None, error=str(exc))
        return None
    _set_flow_state(pending=pending, error=None)
    _start_thread(_poll_device_token, client_id, pending)
    return _public_device(pending)


def _is_current_flow(pending):
    current = _flow_state["pending"]
    return current is not None and current.get("device_code") == pending.get("device_code")


def _finish_flow(pending, error):
    """Clear the pending flow (only if it is still this one) and set the error."""
    global _flow_state
    with _lock:
        current = _flow_state["pending"]
        if current is None or current.get("device_code") == pending.get("device_code"):
            _flow_state = {"pending": None, "error": error}


def _poll_once(session, client_id, device_code):
    """One token request → ("token", data) | ("pending", extra_seconds) | ("error", message)."""
    resp = _request(session, "POST", ACCESS_TOKEN_URL, headers={"Accept": "application/json"},
                    data={"client_id": client_id, "device_code": device_code, "grant_type": DEVICE_GRANT_TYPE})
    data = _json(resp)
    if not isinstance(data, dict):
        raise GitHubError("Risposta GitHub non valida (token).")
    if data.get("access_token"):
        return "token", data
    error = data.get("error") or ""
    if error in ("authorization_pending", "slow_down"):
        return "pending", SLOW_DOWN_EXTRA_SECONDS if error == "slow_down" else 0
    return "error", (DEVICE_ERRORS.get(error) or data.get("error_description")
                     or f"Errore GitHub durante il login ({error or resp.status_code}).")


def _complete_device_login(pending, token_data):
    if not _is_current_flow(pending):
        # disconnect()/reset_cache() ran while GitHub was issuing the token: drop it.
        logger.info("GitHub device login completed after cancellation; token discarded")
        return
    try:
        user, _scopes = _fetch_user(_session(token_data["access_token"]))
    except GitHubError as exc:
        _finish_flow(pending, f"Login riuscito ma profilo non leggibile: {exc}")
        return
    if not _store_token_if_current(pending, _build_token_record(token_data, user, "device")):
        logger.info("GitHub device login completed after cancellation; token discarded")


def _poll_device_token(client_id, pending):
    """Background worker: poll until token, terminal error, expiry or cancellation."""
    session = _session()
    interval, failures = pending["interval"], 0
    while _is_current_flow(pending):
        time.sleep(interval)
        if _is_expired(pending):
            _finish_flow(pending, DEVICE_ERRORS["expired_token"])
            return
        try:
            outcome, value = _poll_once(session, client_id, pending["device_code"])
        except GitHubError as exc:
            failures += 1
            if failures >= MAX_POLL_NETWORK_FAILURES:
                _finish_flow(pending, str(exc))
                return
            continue
        if outcome == "token":
            _complete_device_login(pending, value)
            return
        if outcome == "error":
            _finish_flow(pending, value)
            return
        interval += value


def auth_status():
    """Snapshot for UI polling: configured/connected/pending/device/error/identity."""
    state = _flow_state
    pending = state["pending"]
    active = pending is not None and not _is_expired(pending)
    return {
        "configured": is_configured(),
        "connected": is_connected(),
        "pending": active,
        "device": _public_device(pending) if active else None,
        "error": state["error"],
        "identity": get_identity(),
    }


# --- Personal Access Token

def connect_with_token(token):
    """Validate a PAT with GET /user and store it. Returns (identity, error)."""
    if not isinstance(token, str) or not token.strip():
        return None, "Token mancante."
    token = token.strip()
    try:
        user, scopes = _fetch_user(_session(token))
    except GitHubAuthError:
        return None, TOKEN_INVALID
    except GitHubError as exc:
        return None, str(exc)
    record = _build_token_record({"access_token": token, "token_type": "bearer", "scope": scopes}, user, "pat")
    _store_token(record)
    return _identity_from(record), None


# --- Repositories

def _fetch_repos(session):
    url = f"{GITHUB_API}/user/repos"
    params = {"per_page": REPOS_PER_PAGE, "affiliation": REPOS_AFFILIATION, "sort": "pushed"}
    repos = []
    for _ in range(REPOS_MAX_PAGES):
        resp = _request(session, "GET", url, params=params)
        _raise_for_api_error(resp)
        batch = _json(resp)
        if not isinstance(batch, list):
            raise GitHubError("Risposta GitHub non valida (repository).")
        repos = repos + [{**{key: r.get(key) for key in REPO_FIELDS}, "private": bool(r.get("private"))}
                         for r in batch if isinstance(r, dict) and r.get("full_name")]
        url, params = _next_link(resp), None
        if not url:
            break
    return repos


def list_repos(force=False):
    """Repositories of the connected user, cached 10 minutes → (repos, error)."""
    record = _token_record()
    if not record:
        return None, AUTH_REQUIRED
    cached = None if force else _cached_repos(record.get("login"))
    if cached is not None:
        return cached, None
    try:
        repos = _fetch_repos(_session(record["access_token"]))
    except GitHubAuthError:
        return None, AUTH_REQUIRED
    except GitHubError as exc:
        return None, str(exc)
    _set_repo_cache(record.get("login"), repos)
    return repos, None


# --- Activity: user events + default-branch commits

def _parse_utc(value):
    """ISO-8601 string (optionally 'Z'-suffixed) → aware UTC datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _utc_z(value):
    text = (value or "").strip()
    return text if text.endswith("Z") or "+" in text[10:] else f"{text}Z"


def _dedupe(items, key):
    """First occurrence wins; order preserved."""
    unique = {}
    for item in items:
        unique.setdefault(key(item), item)
    return list(unique.values())


def _sorted_by_date(items):
    return sorted(items, key=lambda item: item.get("date") or "")


def _event_repo(event):
    return ((event.get("repo") if isinstance(event, dict) else None) or {}).get("name") or ""


def _event_in_scope(event, start, end, monitored_lower):
    if not isinstance(event, dict):
        return False
    created = _parse_utc(event.get("created_at"))
    if created is None or not (start <= created < end):
        return False
    repo = _event_repo(event)
    return bool(repo) and (not monitored_lower or repo.lower() in monitored_lower)


def _fetch_events(session, login, start, end, monitored):
    """Pages 1..3 of the user's events, stopping early once older than the day."""
    monitored_lower = {r.lower() for r in monitored}
    collected = []
    for page in range(1, EVENTS_MAX_PAGES + 1):
        resp = _request(session, "GET", f"{GITHUB_API}/users/{login}/events",
                        params={"per_page": EVENTS_PER_PAGE, "page": page})
        _raise_for_api_error(resp)
        batch = _json(resp)
        if not isinstance(batch, list) or not batch:
            break
        collected = collected + batch
        last = _parse_utc(batch[-1].get("created_at")) if isinstance(batch[-1], dict) else None
        if len(batch) < EVENTS_PER_PAGE or (last is not None and last < start):
            break
    return [event for event in collected if _event_in_scope(event, start, end, monitored_lower)]


def _commits_from_push(event):
    """Commits of a PushEvent; non-distinct ones (already pushed earlier) are skipped."""
    repo = _event_repo(event)
    raw = (event.get("payload") or {}).get("commits") or []
    return [{
        "sha": c["sha"], "repo": repo, "message": (c.get("message") or "").strip(),
        "url": f"https://github.com/{repo}/commit/{c['sha']}", "date": event.get("created_at"),
    } for c in raw if isinstance(c, dict) and c.get("sha") and c.get("distinct") is not False]


def _pr_action(payload):
    action = payload.get("action")
    if action in ("opened", "reopened"):
        return action
    if action == "closed":
        return "merged" if (payload.get("pull_request") or {}).get("merged") else "closed"
    return None


def _item_from_event(event, subject, action):
    number = subject.get("number")
    number = (event.get("payload") or {}).get("number") if number is None else number
    if number is None:
        return []
    return [{"repo": _event_repo(event), "number": _to_int(number, 0), "title": (subject.get("title") or "").strip(),
             "action": action, "url": subject.get("html_url") or "", "date": event.get("created_at")}]


def _pull_requests_from_event(event):
    etype, payload = event.get("type"), event.get("payload") or {}
    issue = payload.get("issue") or {}
    if etype == "PullRequestEvent":
        action, subject = _pr_action(payload), payload.get("pull_request") or {}
    elif etype == "PullRequestReviewEvent":
        action, subject = "reviewed", payload.get("pull_request") or {}
    elif etype == "IssueCommentEvent" and issue.get("pull_request"):
        action, subject = "commented", issue
    else:
        return []
    return _item_from_event(event, subject, action) if action else []


def _issues_from_event(event):
    etype, payload = event.get("type"), event.get("payload") or {}
    issue = payload.get("issue") or {}
    if etype == "IssuesEvent" and payload.get("action") in ("opened", "reopened", "closed"):
        return _item_from_event(event, issue, payload["action"])
    if etype == "IssueCommentEvent" and payload.get("action", "created") == "created" and not issue.get("pull_request"):
        return _item_from_event(event, issue, "commented")
    return []


def _activity_from_events(events):
    def item_key(item):
        return item["repo"].lower(), item["number"], item["action"]

    return {
        "commits": _dedupe([c for e in events if e.get("type") == "PushEvent" for c in _commits_from_push(e)],
                           lambda c: c["sha"]),
        "pull_requests": _dedupe([i for e in events for i in _pull_requests_from_event(e)], item_key),
        "issues": _dedupe([i for e in events for i in _issues_from_event(e)], item_key),
    }


def _commit_from_api(repo, item):
    commit = item.get("commit") or {}
    return {
        "sha": item["sha"], "repo": repo, "message": (commit.get("message") or "").strip(),
        "url": item.get("html_url") or f"https://github.com/{repo}/commit/{item['sha']}",
        "date": (commit.get("author") or {}).get("date") or (commit.get("committer") or {}).get("date"),
    }


def _fetch_repo_commits(session, repo, login, start_iso, end_iso):
    """Default-branch commits authored by `login` on the day; [] on 404/409 (missing/empty repo)."""
    params = {"author": login, "since": _utc_z(start_iso), "until": _utc_z(end_iso), "per_page": 100}
    resp = _request(session, "GET", f"{GITHUB_API}/repos/{repo}/commits", params=params)
    if resp.status_code in (404, 409):
        logger.info("Skipping commits of %s (HTTP %s)", repo, resp.status_code)
        return []
    _raise_for_api_error(resp)
    data = _json(resp)
    return [_commit_from_api(repo, item) for item in (data if isinstance(data, list) else [])
            if isinstance(item, dict) and item.get("sha")]


def _fetch_activity_or_raise(date_iso, repos, login=None):
    record = _token_record()
    login = login or (record or {}).get("login")
    if not record or not login:
        raise GitHubAuthError(AUTH_MESSAGE)
    try:
        start_iso, end_iso = config.day_bounds_utc(state_service.resolve_date(date_iso))
    except ValueError as exc:
        raise GitHubError(f"Data non valida: {exc}") from exc
    start, end = _parse_utc(start_iso), _parse_utc(end_iso)
    if start is None or end is None:
        raise GitHubError("Intervallo del giorno non valido.")
    monitored = list(dict.fromkeys(r.strip() for r in (repos or []) if isinstance(r, str) and r.strip()))
    session = _session(record["access_token"])
    events = _fetch_events(session, login, start, end, monitored)
    activity = _activity_from_events(events)
    # Empty `repos` = every repository with activity that day.
    target_repos = monitored or list(dict.fromkeys(_event_repo(e) for e in events))
    fetched = [c for repo in target_repos[:COMMIT_REPOS_CAP]
               for c in _fetch_repo_commits(session, repo, login, start_iso, end_iso)]
    return {
        "commits": _sorted_by_date(_dedupe(activity["commits"] + fetched, lambda c: c["sha"])),
        "pull_requests": _sorted_by_date(activity["pull_requests"]),
        "issues": _sorted_by_date(activity["issues"]),
    }


def fetch_activity(date_iso, repos, login=None):
    """Day activity for the connected user → (activity, error); error may be AUTH_REQUIRED."""
    try:
        return _fetch_activity_or_raise(date_iso, repos, login), None
    except GitHubAuthError:
        return None, AUTH_REQUIRED
    except GitHubError as exc:
        return None, str(exc)


# --- Entry drafts (pure)

def commit_source_id(sha):
    return f"github:commit:{sha}"


def _first_line(message):
    lines = [line.strip() for line in (message or "").splitlines() if line.strip()]
    line = lines[0] if lines else "(nessun messaggio)"
    return line if len(line) <= MAX_MESSAGE_CHARS else line[:MAX_MESSAGE_CHARS - 1].rstrip() + "…"


def commits_text(repo, commits):
    """`owner/repo · N commit` followed by one bullet per commit (max 30, then `… e altri N`)."""
    lines = [f"• {_first_line(c.get('message'))}" for c in commits[:MAX_COMMIT_LINES]]
    extra = len(commits) - MAX_COMMIT_LINES
    tail = [f"• … e altri {extra}"] if extra > 0 else []
    return "\n".join([f"{repo} · {len(commits)} commit", *lines, *tail])


def _commit_draft(repo, commits):
    slim = [{"sha": c["sha"], "message": c.get("message") or "", "url": c.get("url") or ""} for c in commits]
    return {
        "type": "github", "text": commits_text(repo, slim), "duration_min": None,
        "source_ids": [commit_source_id(c["sha"]) for c in slim],
        "meta": {"kind": "commits", "repo": repo, "commits": slim, "url": f"https://github.com/{repo}"},
    }


def _commit_drafts(commits):
    repos = list(dict.fromkeys(c["repo"] for c in commits if c.get("repo") and c.get("sha")))
    return [_commit_draft(repo, [c for c in commits if c["repo"] == repo]) for repo in repos]


def _item_draft(item, kind, noun, labels, id_prefix):
    repo, number, action = item["repo"], item["number"], item["action"]
    title = item.get("title") or ""
    return {
        "type": "github", "duration_min": None,
        "text": f"{repo} · {noun} #{number} {labels.get(action, action)}" + (f": {title}" if title else ""),
        "source_ids": [f"github:{id_prefix}:{repo}#{number}:{action}"],
        "meta": {"kind": kind, "repo": repo, "number": number, "action": action,
                 "url": item.get("url") or "", "title": title},
    }


def build_entry_drafts(activity, github_settings):
    """Pure mapping activity → entry drafts honouring the include_* toggles."""
    activity, settings = activity or {}, github_settings or {}
    commits = _commit_drafts(activity.get("commits") or []) if settings.get("include_commits", True) else []
    prs = [_item_draft(pr, "pull_request", "Pull request", PR_ACTION_LABELS, "pr")
           for pr in activity.get("pull_requests") or []] if settings.get("include_pull_requests", True) else []
    issues = [_item_draft(issue, "issue", "Issue", ISSUE_ACTION_LABELS, "issue")
              for issue in activity.get("issues") or []] if settings.get("include_issues", False) else []
    return commits + prs + issues


# --- Import into the daily history

def _is_commits_entry(entry, repo):
    meta = entry.get("meta") or {}
    return entry.get("type") == "github" and meta.get("kind") == "commits" \
        and str(meta.get("repo") or "").lower() == repo.lower()


def _import_commits(draft, new_ids, date):
    """Create the per-repo commits entry or merge the new commits into the existing one."""
    meta, new_set = draft["meta"], set(new_ids)
    repo = meta["repo"]
    new_commits = [c for c in meta["commits"] if commit_source_id(c["sha"]) in new_set]
    existing = state_service.find_entry(lambda e: _is_commits_entry(e, repo), date)
    if existing is None:
        state_service.add_entry(commits_text(repo, new_commits), entry_type="github", duration_min=None,
                                source_id=None, meta={**meta, "commits": new_commits}, date=date)
        return
    old_meta = existing.get("meta") or {}
    merged = _dedupe(list(old_meta.get("commits") or []) + new_commits, lambda c: c["sha"])
    new_meta = {**old_meta, "kind": "commits", "repo": old_meta.get("repo") or repo, "commits": merged,
                "url": old_meta.get("url") or meta["url"]}
    state_service.update_entry(existing["id"], text=commits_text(new_meta["repo"], merged), meta=new_meta, date=date)


def _import_draft(draft, date):
    kind = draft["meta"]["kind"]
    new_ids = [sid for sid in draft["source_ids"] if not state_service.is_source_imported(sid, date)]
    if not new_ids:
        return {"kind": kind, "imported": 0}
    if kind == "commits":
        _import_commits(draft, new_ids, date)
    else:
        state_service.add_entry(draft["text"], entry_type="github", duration_min=None,
                                source_id=new_ids[0], meta=draft["meta"], date=date)
    state_service.mark_sources_imported(new_ids, date)
    return {"kind": kind, "imported": len(new_ids)}


def _import_report(results):
    imported = [r for r in results if r["imported"] > 0]
    return {"new": len(imported), "skipped": len(results) - len(imported), "summary": {
        "commits": sum(r["imported"] for r in imported if r["kind"] == "commits"),
        "pull_requests": sum(1 for r in imported if r["kind"] == "pull_request"),
        "issues": sum(1 for r in imported if r["kind"] == "issue"),
    }}


def import_day(date_iso):
    """Import the day's GitHub activity as entries; raises GitHubAuthError / GitHubError.

    ``new`` = drafts that created or extended an entry, ``skipped`` = already imported,
    ``summary`` = newly imported commits / pull requests / issues.
    """
    date = state_service.resolve_date(date_iso)
    record = _token_record()
    if not record:
        raise GitHubAuthError(AUTH_MESSAGE)
    github_settings = (settings_service.load_settings() or {}).get("github") or {}
    activity = _fetch_activity_or_raise(date, github_settings.get("repos") or [], record.get("login"))
    results = [_import_draft(draft, date) for draft in build_entry_drafts(activity, github_settings)]
    report = _import_report(results)
    logger.info("GitHub import %s: %s", date, report)
    return report

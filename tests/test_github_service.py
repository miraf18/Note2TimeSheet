"""Unit tests for services.github_service (HTTP, settings, state and config are faked)."""

import copy
import itertools
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from services import github_service as gh

DAY = "2026-09-16"
LOGIN = "miraf18"
REPO = "bureau/timesheet"
OTHER_REPO = "bureau/website"
USER = {"login": LOGIN, "name": "Raffaele", "avatar_url": "https://avatars.githubusercontent.com/u/1",
        "html_url": f"https://github.com/{LOGIN}"}
TOKEN_RECORD = {
    "access_token": "ghp_secret", "token_type": "bearer", "scope": "repo read:user", "login": LOGIN,
    "name": "Raffaele", "avatar_url": USER["avatar_url"], "html_url": USER["html_url"],
    "auth_method": "pat", "connected_at": "2026-09-16T08:00:00+02:00",
}
_ids = itertools.count(1)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, url=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.url = url
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeHTTP:
    """Dispatches Session.request calls on (method, url suffix); records every call."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, method, url_suffix, response):
        self.routes.append((method, url_suffix, response))
        return self

    def __call__(self, session, method, url, **kwargs):
        call = {"method": method, "url": url, "session_headers": dict(session.headers), **kwargs}
        self.calls.append(call)
        for route_method, suffix, response in self.routes:
            if route_method == method and (url == suffix or url.endswith(suffix)):
                return response(call) if callable(response) else response
        raise AssertionError(f"Unexpected request {method} {url}")

    def count(self, url_part):
        return sum(1 for c in self.calls if url_part in c["url"])


class FakeSettings:
    def __init__(self):
        self.settings = {"github": {"client_id": "", "repos": [], "include_commits": True,
                                    "include_pull_requests": True, "include_issues": False}}
        self.secrets = {}

    def load_settings(self):
        return copy.deepcopy(self.settings)

    def get(self, path, default=None):
        node = self.settings
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def get_secret(self, key, default=None):
        return self.secrets.get(key, default)

    def set_secret(self, key, value):
        self.secrets = {**self.secrets, key: value}

    def update_settings(self, patch):
        merged = copy.deepcopy(self.settings)
        for section, values in patch.items():
            merged[section] = {**merged.get(section, {}), **values}
        self.settings = merged
        return copy.deepcopy(merged)


class FakeState:
    def __init__(self):
        self.days = {}

    def _day(self, date):
        return self.days.setdefault(date or DAY, {"entries": [], "imported": []})

    def resolve_date(self, date):
        try:
            return datetime.strptime(date, "%Y-%m-%d").date().isoformat()
        except (TypeError, ValueError):
            return DAY

    def get_entries(self, date=None):
        return list(self._day(date)["entries"])

    def add_entry(self, text, entry_type="manual", duration_min=None, source_id=None, meta=None, date=None):
        entry = {"id": f"e{next(_ids)}", "type": entry_type, "text": text, "time": "09:00",
                 "duration_min": duration_min, "source_id": source_id, "meta": meta or {}}
        day = self._day(date)
        day["entries"] = day["entries"] + [entry]
        return entry

    def update_entry(self, entry_id, text=None, meta=None, duration_min=None, date=None):
        day = self._day(date)
        updated = None
        entries = []
        for entry in day["entries"]:
            if entry["id"] == entry_id:
                updated = {**entry, **({"text": text} if text is not None else {}),
                           **({"meta": meta} if meta is not None else {})}
                entries.append(updated)
            else:
                entries.append(entry)
        day["entries"] = entries
        return updated

    def find_entry(self, predicate, date=None):
        return next((e for e in self._day(date)["entries"] if predicate(e)), None)

    def is_source_imported(self, source_id, date=None):
        return source_id in self._day(date)["imported"]

    def mark_sources_imported(self, source_ids, date=None):
        day = self._day(date)
        day["imported"] = list(dict.fromkeys(day["imported"] + list(source_ids)))


def _fake_day_bounds(date_iso):
    start = datetime.fromisoformat(date_iso)
    end = start + timedelta(days=1)
    return start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    gh.reset_cache()
    settings, state = FakeSettings(), FakeState()
    fake_config = SimpleNamespace(day_bounds_utc=_fake_day_bounds,
                                  now_local=lambda: datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(gh, "settings_service", settings)
    monkeypatch.setattr(gh, "state_service", state)
    monkeypatch.setattr(gh, "config", fake_config)
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    yield SimpleNamespace(settings=settings, state=state)
    gh.reset_cache()


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, **kw: fake(self, method, url, **kw))
    return fake


@pytest.fixture
def connected(env):
    env.settings.set_secret("github_token", dict(TOKEN_RECORD))
    return env


@pytest.fixture
def sync_thread(monkeypatch):
    """Run the device-flow worker synchronously and record sleeps."""
    sleeps = []
    monkeypatch.setattr(gh, "_start_thread", lambda target, *args: target(*args))
    monkeypatch.setattr(gh.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _event(etype, repo, created_at, payload):
    return {"id": str(next(_ids)), "type": etype, "actor": {"login": LOGIN}, "public": False,
            "repo": {"id": 42, "name": repo}, "payload": payload, "created_at": created_at}


def push_event(repo, created_at, commits):
    payload_commits = [{
        "sha": sha, "message": message, "distinct": distinct,
        "author": {"email": "r@example.com", "name": "Raffaele"},
        "url": f"https://api.github.com/repos/{repo}/commits/{sha}",
    } for sha, message, distinct in commits]
    payload = {"repository_id": 42, "push_id": next(_ids), "ref": "refs/heads/feature",
               "head": commits[-1][0] if commits else "", "before": "0" * 40, "commits": payload_commits}
    return _event("PushEvent", repo, created_at, payload)


def pr_event(repo, created_at, action, number, title, merged=False):
    pull = {"number": number, "title": title, "merged": merged,
            "html_url": f"https://github.com/{repo}/pull/{number}"}
    return _event("PullRequestEvent", repo, created_at, {"action": action, "number": number, "pull_request": pull})


def review_event(repo, created_at, number, title):
    pull = {"number": number, "title": title, "html_url": f"https://github.com/{repo}/pull/{number}"}
    return _event("PullRequestReviewEvent", repo, created_at,
                  {"action": "created", "review": {"state": "approved"}, "pull_request": pull})


def issue_event(repo, created_at, action, number, title):
    issue = {"number": number, "title": title, "html_url": f"https://github.com/{repo}/issues/{number}"}
    return _event("IssuesEvent", repo, created_at, {"action": action, "issue": issue})


def comment_event(repo, created_at, number, title, on_pr=False):
    issue = {"number": number, "title": title, "html_url": f"https://github.com/{repo}/issues/{number}"}
    if on_pr:
        issue = {**issue, "pull_request": {"url": f"https://api.github.com/repos/{repo}/pulls/{number}"}}
    return _event("IssueCommentEvent", repo, created_at, {"action": "created", "issue": issue, "comment": {}})


def api_commit(repo, sha, message, date, author_login=LOGIN, email="r@example.com", parents=1):
    return {"sha": sha, "html_url": f"https://github.com/{repo}/commit/{sha}",
            "author": {"login": author_login} if author_login else None,
            "parents": [{"sha": f"{n:040x}"} for n in range(parents)],
            "commit": {"message": message, "author": {"date": date, "name": "Raffaele", "email": email},
                       "committer": {"date": date}}}


def api_branch(name, head):
    return {"name": name, "commit": {"sha": head}}


def day_events():
    return [
        pr_event(REPO, f"{DAY}T16:30:00Z", "closed", 12, "Add GitHub import", merged=True),
        review_event(REPO, f"{DAY}T15:00:00Z", 13, "Fix rounding"),
        comment_event(REPO, f"{DAY}T14:30:00Z", 13, "Fix rounding", on_pr=True),
        issue_event(REPO, f"{DAY}T12:00:00Z", "opened", 40, "Crash on empty day"),
        comment_event(REPO, f"{DAY}T11:00:00Z", 41, "Old issue"),
        _event("WatchEvent", REPO, f"{DAY}T10:30:00Z", {"action": "started"}),
        push_event(REPO, f"{DAY}T10:00:00Z", [("a" * 40, "feat: events\n\nbody", True),
                                              ("b" * 40, "fix: typo", True),
                                              ("c" * 40, "old commit re-pushed", False)]),
        push_event(OTHER_REPO, f"{DAY}T09:00:00Z", [("d" * 40, "docs: readme", True)]),
        push_event(REPO, "2026-09-15T23:00:00Z", [("e" * 40, "yesterday", True)]),
    ]


def wire_activity(http, events=None, repo_commits=None):
    http.add("GET", f"/users/{LOGIN}/events", FakeResponse(200, events if events is not None else day_events()))
    http.add("GET", f"/repos/{REPO}/branches", FakeResponse(200, [
        api_branch("main", "1" * 40), api_branch("feature", "2" * 40)]))
    http.add("GET", f"/repos/{REPO}/commits", FakeResponse(200, repo_commits if repo_commits is not None else [
        api_commit(REPO, "a" * 40, "feat: events", f"{DAY}T10:00:00Z"),
        api_commit(REPO, "f" * 40, "chore: bump version", f"{DAY}T08:00:00Z"),
    ]))
    http.add("GET", f"/repos/{OTHER_REPO}/branches", FakeResponse(200, [api_branch("main", "3" * 40)]))
    http.add("GET", f"/repos/{OTHER_REPO}/commits", FakeResponse(200, [
        api_commit(OTHER_REPO, "d" * 40, "docs: readme", f"{DAY}T09:00:00Z")]))
    return http


# ---------------------------------------------------------------------------
# Configuration, identity, disconnect
# ---------------------------------------------------------------------------

def test_is_configured_from_settings_or_env(env, monkeypatch):
    assert gh.is_configured() is False
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.env")
    assert gh.is_configured() is True
    monkeypatch.delenv("GITHUB_CLIENT_ID")
    env.settings.settings["github"]["client_id"] = "Iv1.settings"
    assert gh.is_configured() is True


def test_identity_hides_access_token(connected):
    identity = gh.get_identity()
    assert identity["login"] == LOGIN and identity["auth_method"] == "pat"
    assert "access_token" not in identity
    assert gh.is_connected() is True


def test_identity_none_when_not_connected(env):
    assert gh.get_identity() is None
    assert gh.is_connected() is False
    assert gh.auth_status() == {"configured": False, "connected": False, "pending": False,
                                "device": None, "error": None, "identity": None}


def test_disconnect_clears_secret_pending_and_cache(connected):
    gh._set_repo_cache(LOGIN, [{"full_name": REPO}])
    gh._set_flow_state(pending={"device_code": "x", "expires_at": 9e12}, error="old")
    gh.disconnect()
    assert connected.settings.get_secret("github_token") is None
    assert gh.is_connected() is False
    assert gh._cached_repos(LOGIN) is None
    assert gh.auth_status()["pending"] is False and gh.auth_status()["error"] is None


# ---------------------------------------------------------------------------
# Personal access token
# ---------------------------------------------------------------------------

def test_connect_with_token_success(env, http):
    http.add("GET", "/user", FakeResponse(200, USER, headers={"X-OAuth-Scopes": "repo, read:user"}))
    identity, error = gh.connect_with_token("  ghp_newtoken ")
    assert error is None
    assert identity["login"] == LOGIN and identity["auth_method"] == "pat"
    assert identity["scope"] == "repo, read:user" and "access_token" not in identity
    stored = env.settings.get_secret("github_token")
    assert stored["access_token"] == "ghp_newtoken" and stored["connected_at"].startswith("2026-09-16")
    call = http.calls[0]
    assert call["session_headers"]["Authorization"] == "Bearer ghp_newtoken"
    assert call["session_headers"]["Accept"] == gh.API_HEADERS["Accept"]
    assert call["timeout"] == 20


def test_connect_with_token_invalid(env, http):
    http.add("GET", "/user", FakeResponse(401, {"message": "Bad credentials"}))
    identity, error = gh.connect_with_token("ghp_bad")
    assert identity is None and error == "Token non valido"
    assert env.settings.get_secret("github_token") is None


def test_connect_with_token_blank_or_network_error(env, http):
    assert gh.connect_with_token("   ") == (None, "Token mancante.")
    assert gh.connect_with_token(None) == (None, "Token mancante.")

    def boom(call):
        raise requests.ConnectionError("dns failure")
    http.add("GET", "/user", boom)
    identity, error = gh.connect_with_token("ghp_x")
    assert identity is None and error.startswith("Impossibile contattare GitHub")


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------

DEVICE_PAYLOAD = {"device_code": "dev-code-123", "user_code": "ABCD-1234",
                  "verification_uri": "https://github.com/login/device", "expires_in": 899, "interval": 5}


def _configure(env):
    env.settings.settings["github"]["client_id"] = "Iv1.client"


def test_start_device_login_not_configured(env, http):
    assert gh.start_device_login() is None
    assert http.calls == []


def test_device_flow_completes_after_pending_and_slow_down(env, http, sync_thread):
    _configure(env)
    polls = iter([{"error": "authorization_pending"}, {"error": "slow_down", "interval": 10},
                  {"access_token": "gho_token", "token_type": "bearer", "scope": "repo,read:user"}])
    http.add("POST", "/login/device/code", FakeResponse(200, DEVICE_PAYLOAD))
    http.add("POST", "/login/oauth/access_token", lambda call: FakeResponse(200, next(polls)))
    http.add("GET", "/user", FakeResponse(200, USER))

    device = gh.start_device_login()

    assert device == {"user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device",
                      "expires_in": 899, "interval": 5}
    assert sync_thread == [5, 5, 10]
    code_call = http.calls[0]
    assert code_call["data"] == {"client_id": "Iv1.client", "scope": gh.OAUTH_SCOPES}
    assert code_call["headers"]["Accept"] == "application/json"
    poll_call = http.calls[1]
    assert poll_call["data"] == {"client_id": "Iv1.client", "device_code": "dev-code-123",
                                 "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}
    assert http.calls[-1]["session_headers"]["Authorization"] == "Bearer gho_token"
    stored = env.settings.get_secret("github_token")
    assert stored["access_token"] == "gho_token" and stored["auth_method"] == "device"
    assert stored["login"] == LOGIN and stored["scope"] == "repo,read:user"
    status = gh.auth_status()
    assert status["connected"] is True and status["pending"] is False and status["error"] is None
    assert status["identity"]["login"] == LOGIN


@pytest.mark.parametrize("error_code, expected", [
    ("access_denied", gh.DEVICE_ERRORS["access_denied"]),
    ("incorrect_client_credentials", gh.DEVICE_ERRORS["incorrect_client_credentials"]),
    ("device_flow_disabled", gh.DEVICE_ERRORS["device_flow_disabled"]),
    ("weird_error", "Errore GitHub durante il login (weird_error)."),
])
def test_device_flow_terminal_errors(env, http, sync_thread, error_code, expected):
    _configure(env)
    http.add("POST", "/login/device/code", FakeResponse(200, DEVICE_PAYLOAD))
    http.add("POST", "/login/oauth/access_token", FakeResponse(200, {"error": error_code}))
    assert gh.start_device_login()["user_code"] == "ABCD-1234"
    status = gh.auth_status()
    assert status["pending"] is False and status["connected"] is False
    assert status["error"] == expected
    assert env.settings.get_secret("github_token") is None


def test_device_flow_expiry(env, http, sync_thread):
    _configure(env)
    http.add("POST", "/login/device/code", FakeResponse(200, {**DEVICE_PAYLOAD, "expires_in": 0}))
    gh.start_device_login()
    assert http.count("access_token") == 0
    assert gh.auth_status()["error"] == gh.DEVICE_ERRORS["expired_token"]
    assert gh.auth_status()["pending"] is False


def test_device_flow_survives_transient_network_errors(env, http, sync_thread):
    _configure(env)
    attempts = iter([requests.ConnectionError("blip"), requests.Timeout("slow"), None])
    token = {"access_token": "gho_token", "token_type": "bearer", "scope": ""}

    def poll(call):
        failure = next(attempts)
        if failure:
            raise failure
        return FakeResponse(200, token)
    http.add("POST", "/login/device/code", FakeResponse(200, DEVICE_PAYLOAD))
    http.add("POST", "/login/oauth/access_token", poll)
    http.add("GET", "/user", FakeResponse(200, USER))
    gh.start_device_login()
    assert gh.is_connected() is True and http.count("access_token") == 3


def test_start_device_login_reuses_pending_flow(env, http, monkeypatch):
    _configure(env)
    monkeypatch.setattr(gh, "_start_thread", lambda target, *args: None)
    http.add("POST", "/login/device/code", FakeResponse(200, DEVICE_PAYLOAD))
    first = gh.start_device_login()
    second = gh.start_device_login()
    assert first == second and http.count("device/code") == 1
    status = gh.auth_status()
    assert status["pending"] is True and status["device"]["user_code"] == "ABCD-1234"


def test_start_device_login_github_unreachable(env, http, monkeypatch):
    _configure(env)
    monkeypatch.setattr(gh, "_start_thread", lambda target, *args: None)
    http.add("POST", "/login/device/code", FakeResponse(404, {"message": "Not Found"}))
    assert gh.start_device_login() is None
    status = gh.auth_status()
    assert status["pending"] is False and "404" in status["error"]


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

def _repo(full_name, private=False):
    return {"full_name": full_name, "private": private, "pushed_at": f"{DAY}T10:00:00Z",
            "default_branch": "main", "description": "desc", "extra": "ignored"}


def test_list_repos_not_connected(env, http):
    assert gh.list_repos() == (None, gh.AUTH_REQUIRED)
    assert http.calls == []


def test_list_repos_paginates_and_caches(connected, http):
    next_url = "https://api.github.com/user/repos?page=2&per_page=100"
    http.add("GET", "/user/repos", FakeResponse(200, [_repo(REPO, True), _repo(OTHER_REPO)],
                                                headers={"Link": f'<{next_url}>; rel="next", <x>; rel="last"'}))
    http.add("GET", next_url, FakeResponse(200, [_repo("bureau/tools")]))

    repos, error = gh.list_repos()

    assert error is None
    assert [r["full_name"] for r in repos] == [REPO, OTHER_REPO, "bureau/tools"]
    assert repos[0] == {"full_name": REPO, "private": True, "pushed_at": f"{DAY}T10:00:00Z",
                        "default_branch": "main", "description": "desc"}
    assert http.calls[0]["params"] == {"per_page": 100, "affiliation": "owner,collaborator,organization_member",
                                       "sort": "pushed"}
    assert http.calls[1]["params"] is None and http.calls[1]["url"] == next_url
    assert http.count("user/repos") == 2

    cached, _ = gh.list_repos()
    assert cached == repos and http.count("user/repos") == 2
    forced, _ = gh.list_repos(force=True)
    assert forced == repos and http.count("user/repos") == 4


def test_list_repos_401_clears_token(connected, http):
    http.add("GET", "/user/repos", FakeResponse(401, {"message": "Bad credentials"}))
    assert gh.list_repos() == (None, gh.AUTH_REQUIRED)
    assert connected.settings.get_secret("github_token") is None


def test_list_repos_upstream_error(connected, http):
    http.add("GET", "/user/repos", FakeResponse(500, {"message": "boom"}))
    repos, error = gh.list_repos()
    assert repos is None and error == "Errore API GitHub (500): boom"


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

def test_fetch_activity_maps_events_and_scans_every_branch(connected, http):
    wire_activity(http)
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert error is None

    commits = activity["commits"]
    # Both branches return the same two commits: deduped by sha, sorted by date.
    assert [c["sha"][0] for c in commits] == ["f", "a"]
    assert commits[1] == {"sha": "a" * 40, "repo": REPO, "message": "feat: events",
                          "url": f"https://github.com/{REPO}/commit/{'a' * 40}", "date": f"{DAY}T10:00:00Z"}
    assert commits[0]["message"] == "chore: bump version" and commits[0]["date"] == f"{DAY}T08:00:00Z"

    prs = activity["pull_requests"]
    assert [(p["number"], p["action"]) for p in prs] == [(13, "commented"), (13, "reviewed"), (12, "merged")]
    assert prs[2]["title"] == "Add GitHub import" and prs[2]["url"] == f"https://github.com/{REPO}/pull/12"

    issues = activity["issues"]
    assert [(i["number"], i["action"]) for i in issues] == [(41, "commented"), (40, "opened")]

    events_call = next(c for c in http.calls if "/events" in c["url"])
    assert events_call["params"] == {"per_page": 100, "page": 1}
    branch_params = {c["params"]["sha"]: c["params"] for c in http.calls if c["url"].endswith("/commits")}
    assert set(branch_params) == {"main", "feature"}  # one commits request per branch, no author filter
    assert branch_params["feature"] == {"sha": "feature", "since": f"{DAY}T00:00:00Z",
                                        "until": "2026-09-17T00:00:00Z", "per_page": 100}
    assert http.count(f"/repos/{REPO}/branches") == 1
    assert http.count(f"/repos/{OTHER_REPO}/commits") == 0  # not monitored → not scanned


def test_fetch_activity_empty_repos_means_all_repos(connected, http):
    wire_activity(http)
    activity, error = gh.fetch_activity(DAY, [])
    assert error is None
    assert {c["repo"] for c in activity["commits"]} == {REPO, OTHER_REPO}
    assert http.count(f"/repos/{REPO}/branches") == 1 and http.count(f"/repos/{REPO}/commits") == 2
    assert http.count(f"/repos/{OTHER_REPO}/branches") == 1 and http.count(f"/repos/{OTHER_REPO}/commits") == 1


def test_fetch_activity_pr_closed_without_merge(connected, http):
    events = [pr_event(REPO, f"{DAY}T10:00:00Z", "closed", 7, "Abandoned"),
              pr_event(REPO, f"{DAY}T10:05:00Z", "synchronize", 7, "Abandoned")]
    wire_activity(http, events=events, repo_commits=[])
    activity, _ = gh.fetch_activity(DAY, [REPO])
    assert [(p["number"], p["action"]) for p in activity["pull_requests"]] == [(7, "closed")]


def _full_page(created_at):
    return [push_event(REPO, created_at, [(f"{i:040x}", f"commit {i}", True)]) for i in range(100)]


def test_fetch_activity_stops_paging_when_older_than_day(connected, http):
    pages = {1: _full_page(f"{DAY}T12:00:00Z"), 2: _full_page("2026-09-10T12:00:00Z"), 3: []}
    http.add("GET", "/events", lambda call: FakeResponse(200, pages[call["params"]["page"]]))
    http.add("GET", "/branches", FakeResponse(200, []))
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert error is None and activity["commits"] == []  # PushEvents never contribute commits
    assert http.count("/events") == 2


def test_fetch_activity_reads_at_most_three_pages(connected, http):
    http.add("GET", "/events", lambda call: FakeResponse(200, _full_page(f"{DAY}T12:00:00Z")))
    http.add("GET", "/branches", FakeResponse(200, []))
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert error is None and http.count("/events") == 3


def test_fetch_activity_401_clears_token(connected, http):
    http.add("GET", "/events", FakeResponse(401, {"message": "Bad credentials"}))
    assert gh.fetch_activity(DAY, [REPO]) == (None, gh.AUTH_REQUIRED)
    assert connected.settings.get_secret("github_token") is None


def test_fetch_activity_rate_limited(connected, http):
    http.add("GET", "/events", FakeResponse(403, {"message": "API rate limit exceeded"},
                                            headers={"X-RateLimit-Remaining": "0"}))
    assert gh.fetch_activity(DAY, [REPO]) == (None, "Limite API GitHub raggiunto, riprova più tardi")


def test_fetch_activity_forbidden_without_rate_limit_is_generic(connected, http):
    http.add("GET", "/events", FakeResponse(403, {"message": "Resource protected by SAML"},
                                            headers={"X-RateLimit-Remaining": "55"}))
    _, error = gh.fetch_activity(DAY, [REPO])
    assert error == "Errore API GitHub (403): Resource protected by SAML"


def test_fetch_activity_skips_missing_or_empty_repos(connected, http):
    http.add("GET", "/events", FakeResponse(200, []))
    http.add("GET", "/repos/bureau/gone/branches", FakeResponse(404, {"message": "Not Found"}))
    http.add("GET", "/repos/bureau/empty/branches", FakeResponse(409, {"message": "Git Repository is empty."}))
    activity, error = gh.fetch_activity(DAY, ["bureau/gone", "bureau/empty"])
    assert error is None
    assert activity == {"commits": [], "pull_requests": [], "issues": []}


def test_fetch_activity_not_connected(env, http):
    assert gh.fetch_activity(DAY, [REPO]) == (None, gh.AUTH_REQUIRED)
    assert http.calls == []


def test_fetch_activity_network_error(connected, http):
    def boom(call):
        raise requests.Timeout("timeout")
    http.add("GET", "/events", boom)
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert activity is None and error.startswith("Impossibile contattare GitHub")


# ---------------------------------------------------------------------------
# Entry drafts (pure)
# ---------------------------------------------------------------------------

def _commit(repo, sha, message):
    return {"sha": sha, "repo": repo, "message": message, "url": f"https://github.com/{repo}/commit/{sha}",
            "date": f"{DAY}T10:00:00Z"}


def test_build_entry_drafts_groups_commits_per_repo():
    activity = {"commits": [_commit(REPO, "a" * 40, "feat: first\n\nlong body"),
                            _commit(OTHER_REPO, "b" * 40, "docs"),
                            _commit(REPO, "c" * 40, "fix: second")],
                "pull_requests": [], "issues": []}
    drafts = gh.build_entry_drafts(activity, {})
    assert [d["meta"]["repo"] for d in drafts] == [REPO, OTHER_REPO]
    first = drafts[0]
    assert first["type"] == "github" and first["duration_min"] is None
    assert first["text"] == f"{REPO} · 2 commit\n• feat: first\n• fix: second"
    assert first["source_ids"] == [f"github:commit:{'a' * 40}", f"github:commit:{'c' * 40}"]
    assert first["meta"] == {"kind": "commits", "repo": REPO, "url": f"https://github.com/{REPO}",
                             "commits": [{"sha": "a" * 40, "message": "feat: first\n\nlong body",
                                          "url": f"https://github.com/{REPO}/commit/{'a' * 40}"},
                                         {"sha": "c" * 40, "message": "fix: second",
                                          "url": f"https://github.com/{REPO}/commit/{'c' * 40}"}]}


def test_build_entry_drafts_truncates_long_lists_and_messages():
    commits = [_commit(REPO, f"{i:040x}", "x" * 200 if i == 0 else f"commit {i}") for i in range(35)]
    draft = gh.build_entry_drafts({"commits": commits}, {"include_commits": True})[0]
    lines = draft["text"].split("\n")
    assert lines[0] == f"{REPO} · 35 commit"
    assert len(lines) == 32 and lines[-1] == "• … e altri 5"
    assert len(lines[1]) == 2 + 120 and lines[1].endswith("…")


@pytest.mark.parametrize("action, label", [
    ("opened", "aperta"), ("reopened", "riaperta"), ("closed", "chiusa"),
    ("merged", "unita"), ("reviewed", "revisionata"), ("commented", "commentata"),
])
def test_build_entry_drafts_pull_request_labels(action, label):
    pr = {"repo": REPO, "number": 12, "title": "Add import", "action": action,
          "url": f"https://github.com/{REPO}/pull/12", "date": f"{DAY}T10:00:00Z"}
    draft = gh.build_entry_drafts({"pull_requests": [pr]}, {})[0]
    assert draft["text"] == f"{REPO} · Pull request #12 {label}: Add import"
    assert draft["source_ids"] == [f"github:pr:{REPO}#12:{action}"]
    assert draft["meta"]["kind"] == "pull_request" and draft["meta"]["action"] == action
    assert draft["meta"]["number"] == 12 and draft["meta"]["url"].endswith("/pull/12")


def test_build_entry_drafts_issue_labels_and_toggles():
    issue = {"repo": REPO, "number": 40, "title": "Crash", "action": "closed", "url": "u", "date": ""}
    activity = {"commits": [_commit(REPO, "a" * 40, "m")], "pull_requests": [], "issues": [issue]}
    assert [d["meta"]["kind"] for d in gh.build_entry_drafts(activity, {})] == ["commits"]
    only_issues = gh.build_entry_drafts(activity, {"include_commits": False, "include_issues": True})
    assert [d["meta"]["kind"] for d in only_issues] == ["issue"]
    assert only_issues[0]["text"] == f"{REPO} · Issue #40 chiusa: Crash"
    assert only_issues[0]["source_ids"] == [f"github:issue:{REPO}#40:closed"]
    assert gh.build_entry_drafts({}, None) == []


def test_build_entry_drafts_does_not_mutate_input():
    activity = {"commits": [_commit(REPO, "a" * 40, "m")], "pull_requests": [], "issues": []}
    snapshot = copy.deepcopy(activity)
    gh.build_entry_drafts(activity, {"include_commits": True})
    assert activity == snapshot


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def test_import_day_creates_entries_and_marks_sources(connected, http):
    connected.settings.settings["github"]["repos"] = [REPO]
    wire_activity(http)
    report = gh.import_day(DAY)

    assert report == {"new": 4, "skipped": 0, "summary": {"commits": 2, "pull_requests": 3, "issues": 0}}
    entries = connected.state.get_entries(DAY)
    assert [e["type"] for e in entries] == ["github"] * 4
    commits_entry = entries[0]
    assert commits_entry["meta"]["kind"] == "commits" and commits_entry["meta"]["repo"] == REPO
    assert [c["sha"][0] for c in commits_entry["meta"]["commits"]] == ["f", "a"]
    assert commits_entry["text"].startswith(f"{REPO} · 2 commit\n• chore: bump version\n• feat: events")
    assert commits_entry["source_id"] is None
    pr_entry = entries[-1]
    assert pr_entry["text"] == f"{REPO} · Pull request #12 unita: Add GitHub import"
    assert pr_entry["source_id"] == f"github:pr:{REPO}#12:merged"
    imported = connected.state.days[DAY]["imported"]
    assert f"github:commit:{'a' * 40}" in imported and f"github:pr:{REPO}#13:reviewed" in imported
    assert f"github:commit:{'b' * 40}" not in imported  # PushEvent commits are never imported


def test_import_day_merges_new_commits_into_existing_entry_and_dedupes(connected, http):
    connected.settings.settings["github"]["repos"] = [REPO]
    existing_commit = {"sha": "a" * 40, "message": "feat: events", "url": "u"}
    connected.state.add_entry(gh.commits_text(REPO, [existing_commit]), entry_type="github",
                              meta={"kind": "commits", "repo": REPO, "commits": [existing_commit],
                                    "url": f"https://github.com/{REPO}"}, date=DAY)
    connected.state.mark_sources_imported([f"github:commit:{'a' * 40}", f"github:pr:{REPO}#12:merged"], DAY)
    wire_activity(http)

    report = gh.import_day(DAY)

    assert report["new"] == 3 and report["skipped"] == 1
    assert report["summary"] == {"commits": 1, "pull_requests": 2, "issues": 0}
    entries = connected.state.get_entries(DAY)
    commits_entries = [e for e in entries if e["meta"].get("kind") == "commits"]
    assert len(commits_entries) == 1
    merged = commits_entries[0]
    assert [c["sha"][0] for c in merged["meta"]["commits"]] == ["a", "f"]
    assert merged["text"] == f"{REPO} · 2 commit\n• feat: events\n• chore: bump version"
    assert sum(1 for e in entries if e["source_id"] == f"github:pr:{REPO}#12:merged") == 0

    again = gh.import_day(DAY)
    assert again == {"new": 0, "skipped": 4, "summary": {"commits": 0, "pull_requests": 0, "issues": 0}}
    assert len(connected.state.get_entries(DAY)) == len(entries)


def test_import_day_recreates_entry_only_with_new_commits_after_deletion(connected, http):
    connected.settings.settings["github"]["repos"] = [REPO]
    connected.state.mark_sources_imported([f"github:commit:{'a' * 40}"], DAY)  # entry deleted by the user
    wire_activity(http)
    gh.import_day(DAY)
    commits_entry = next(e for e in connected.state.get_entries(DAY) if e["meta"].get("kind") == "commits")
    assert [c["sha"][0] for c in commits_entry["meta"]["commits"]] == ["f"]
    assert commits_entry["text"].startswith(f"{REPO} · 1 commit")


def test_import_day_respects_include_flags(connected, http):
    connected.settings.settings["github"].update({"repos": [REPO], "include_commits": False,
                                                  "include_pull_requests": False, "include_issues": True})
    wire_activity(http)
    report = gh.import_day(DAY)
    assert report["summary"] == {"commits": 0, "pull_requests": 0, "issues": 2}
    texts = [e["text"] for e in connected.state.get_entries(DAY)]
    assert texts == [f"{REPO} · Issue #41 commentata: Old issue", f"{REPO} · Issue #40 aperta: Crash on empty day"]


def test_import_day_not_connected_raises_auth_error(env, http):
    with pytest.raises(gh.GitHubAuthError):
        gh.import_day(DAY)
    assert http.calls == []


def test_import_day_token_revoked_raises_auth_error_and_clears_token(connected, http):
    http.add("GET", "/events", FakeResponse(401, {"message": "Bad credentials"}))
    with pytest.raises(gh.GitHubAuthError):
        gh.import_day(DAY)
    assert connected.settings.get_secret("github_token") is None


def test_import_day_rate_limit_raises_github_error(connected, http):
    http.add("GET", "/events", FakeResponse(403, {"message": "rate"}, headers={"X-RateLimit-Remaining": "0"}))
    with pytest.raises(gh.GitHubError, match="Limite API GitHub raggiunto"):
        gh.import_day(DAY)
    assert connected.state.get_entries(DAY) == []


def test_import_day_invalid_date_falls_back_to_today(connected, http):
    wire_activity(http, events=[], repo_commits=[])
    connected.settings.settings["github"]["repos"] = [REPO]
    assert gh.import_day("not-a-date") == {"new": 0, "skipped": 0,
                                           "summary": {"commits": 0, "pull_requests": 0, "issues": 0}}
    assert http.calls[0]["params"]["page"] == 1


def test_token_arriving_after_disconnect_is_discarded(env, http, sync_thread):
    """A device flow completing after disconnect() must not silently reconnect."""
    _configure(env)
    http.add("POST", "/login/device/code", FakeResponse(200, DEVICE_PAYLOAD))
    http.add("GET", "/user", FakeResponse(200, USER))

    def token_after_disconnect(call):
        gh.disconnect()  # user clicks "Disconnetti" while GitHub is issuing the token
        return FakeResponse(200, {"access_token": "gho_late", "token_type": "bearer", "scope": "repo"})

    http.add("POST", "/login/oauth/access_token", token_after_disconnect)
    gh.start_device_login()
    assert env.settings.get_secret("github_token") is None
    assert gh.is_connected() is False
    assert http.count("/user") == 0
    assert gh.auth_status()["pending"] is False


# ---------------------------------------------------------------------------
# Branch scanning
# ---------------------------------------------------------------------------

def test_branch_scan_finds_commits_only_on_feature_branches(connected, http):
    """Commits pushed to a branch not yet merged into main are imported."""
    http.add("GET", "/events", FakeResponse(200, []))
    http.add("GET", f"/repos/{REPO}/branches", FakeResponse(200, [
        api_branch("main", "1" * 40), api_branch("backoffice", "2" * 40)]))
    by_branch = {
        "main": [],
        "backoffice": [api_commit(REPO, "a" * 40, "feat: antiriciclaggio", f"{DAY}T08:35:00Z"),
                       api_commit(REPO, "b" * 40, "fix: conflitti", f"{DAY}T07:57:00Z")],
    }
    http.add("GET", f"/repos/{REPO}/commits", lambda call: FakeResponse(200, by_branch[call["params"]["sha"]]))
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert error is None
    assert [c["message"] for c in activity["commits"]] == ["fix: conflitti", "feat: antiriciclaggio"]


def test_branch_scan_keeps_only_own_non_merge_commits(connected, http):
    connected.settings.settings["github"]["author_emails"] = ["Raffaele.Mirabelli@bureauplattner.com"]
    http.add("GET", "/events", FakeResponse(200, []))
    http.add("GET", f"/repos/{REPO}/branches", FakeResponse(200, [api_branch("main", "1" * 40)]))
    http.add("GET", f"/repos/{REPO}/commits", FakeResponse(200, [
        api_commit(REPO, "a" * 40, "mine (linked login)", f"{DAY}T10:00:00Z"),
        api_commit(REPO, "b" * 40, "Merge branch 'main'", f"{DAY}T09:30:00Z", parents=2),
        api_commit(REPO, "c" * 40, "colleague", f"{DAY}T09:00:00Z", author_login="GianM86", email="g@x.it"),
        api_commit(REPO, "d" * 40, "mine (unlinked, corporate e-mail)", f"{DAY}T08:00:00Z",
                   author_login=None, email="raffaele.mirabelli@bureauplattner.com"),
        api_commit(REPO, "e" * 40, "unlinked, unknown e-mail", f"{DAY}T07:00:00Z",
                   author_login=None, email="someone@else.it"),
    ]))
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert error is None
    assert [c["sha"][0] for c in activity["commits"]] == ["d", "a"]


def test_branch_scan_dedupes_branches_with_same_head(connected, http):
    http.add("GET", "/events", FakeResponse(200, []))
    http.add("GET", f"/repos/{REPO}/branches", FakeResponse(200, [
        api_branch("main", "1" * 40), api_branch("release", "1" * 40), api_branch("dev", "2" * 40)]))
    http.add("GET", f"/repos/{REPO}/commits", FakeResponse(200, []))
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert error is None and activity["commits"] == []
    assert sorted(c["params"]["sha"] for c in http.calls if c["url"].endswith("/commits")) == ["dev", "main"]


def test_branch_scan_caps_the_number_of_branches(connected, http):
    http.add("GET", "/events", FakeResponse(200, []))
    http.add("GET", f"/repos/{REPO}/branches", FakeResponse(200, [
        api_branch(f"b{i}", f"{i:040x}") for i in range(gh.MAX_BRANCHES_PER_REPO + 15)]))
    http.add("GET", f"/repos/{REPO}/commits", FakeResponse(200, []))
    activity, error = gh.fetch_activity(DAY, [REPO])
    assert error is None
    assert http.count(f"/repos/{REPO}/commits") == gh.MAX_BRANCHES_PER_REPO

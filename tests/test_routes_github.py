"""Route tests for /api/github/* (real settings/state services on a tmp DATA_DIR; HTTP faked).

Uses the ``client`` fixture from tests/conftest.py.
"""

import json

import pytest
import requests

from services import github_service as gh
from services import settings_service

DAY = "2026-09-16"
LOGIN = "miraf18"
REPO = "bureau/timesheet"
USER = {"login": LOGIN, "name": "Raffaele", "avatar_url": "https://avatars.githubusercontent.com/u/1",
        "html_url": f"https://github.com/{LOGIN}"}
TOKEN_RECORD = {
    "access_token": "ghp_secret", "token_type": "bearer", "scope": "repo", "login": LOGIN, "name": "Raffaele",
    "avatar_url": USER["avatar_url"], "html_url": USER["html_url"], "auth_method": "pat",
    "connected_at": "2026-09-16T08:00:00+02:00",
}
DEVICE_PAYLOAD = {"device_code": "dev-code", "user_code": "WXYZ-9876",
                  "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5}


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.url = ""
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeHTTP:
    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, method, url_suffix, response):
        self.routes.append((method, url_suffix, response))
        return self

    def __call__(self, session, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for route_method, suffix, response in self.routes:
            if route_method == method and url.endswith(suffix):
                return response
        raise AssertionError(f"Unexpected request {method} {url}")

    def count(self, url_part):
        return sum(1 for c in self.calls if url_part in c["url"])


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, **kw: fake(self, method, url, **kw))
    monkeypatch.setattr(gh, "_start_thread", lambda target, *args: None)
    gh.reset_cache()
    yield fake
    gh.reset_cache()


@pytest.fixture
def connected(http):
    settings_service.set_secret("github_token", dict(TOKEN_RECORD))
    return http


def _push_event(repo, created_at, sha, message):
    return {"id": "1", "type": "PushEvent", "actor": {"login": LOGIN}, "repo": {"id": 1, "name": repo},
            "created_at": created_at, "public": False,
            "payload": {"ref": "refs/heads/main", "commits": [
                {"sha": sha, "message": message, "distinct": True,
                 "url": f"https://api.github.com/repos/{repo}/commits/{sha}"}]}}


def _pr_event(repo, created_at, number, title):
    return {"id": "2", "type": "PullRequestEvent", "actor": {"login": LOGIN}, "repo": {"id": 1, "name": repo},
            "created_at": created_at, "public": False,
            "payload": {"action": "opened", "number": number,
                        "pull_request": {"number": number, "title": title, "merged": False,
                                         "html_url": f"https://github.com/{repo}/pull/{number}"}}}


def _wire_day(http):
    events = [_pr_event(REPO, f"{DAY}T10:30:00Z", 12, "Add GitHub import"),
              _push_event(REPO, f"{DAY}T10:00:00Z", "a" * 40, "feat: events mapping")]
    http.add("GET", f"/users/{LOGIN}/events", FakeResponse(200, events))
    http.add("GET", f"/repos/{REPO}/branches", FakeResponse(200, [{"name": "main", "commit": {"sha": "1" * 40}}]))
    http.add("GET", f"/repos/{REPO}/commits", FakeResponse(200, [
        {"sha": "b" * 40, "html_url": f"https://github.com/{REPO}/commit/{'b' * 40}", "author": {"login": LOGIN},
         "parents": [{"sha": "0" * 40}],
         "commit": {"message": "chore: bump", "author": {"date": f"{DAY}T09:00:00Z"}}}]))


# ---------------------------------------------------------------------------
# status / connect / token / disconnect
# ---------------------------------------------------------------------------

def test_status_default(client, http):
    resp = client.get("/api/github/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["configured"] is False and body["connected"] is False and body["pending"] is False
    assert body["identity"] is None and body["repos"] == []


def test_status_never_exposes_access_token(client, connected):
    body = client.get("/api/github/status").get_json()
    assert body["connected"] is True and body["identity"]["login"] == LOGIN
    assert "access_token" not in json.dumps(body)


def test_connect_requires_client_id(client, http):
    resp = client.post("/api/github/connect")
    assert resp.status_code == 400
    assert resp.get_json() == {"success": False, "error": "Configura il Client ID GitHub nelle impostazioni"}
    assert http.calls == []


def test_connect_returns_device_code(client, http):
    settings_service.update_settings({"github": {"client_id": "Iv1.client"}})
    http.add("POST", "/login/device/code", FakeResponse(200, DEVICE_PAYLOAD))
    resp = client.post("/api/github/connect")
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "device": {
        "user_code": "WXYZ-9876", "verification_uri": "https://github.com/login/device",
        "expires_in": 900, "interval": 5}}
    status = client.get("/api/github/status").get_json()
    assert status["configured"] is True and status["pending"] is True
    assert status["device"]["user_code"] == "WXYZ-9876"


def test_connect_github_unreachable(client, http):
    settings_service.update_settings({"github": {"client_id": "Iv1.client"}})
    http.add("POST", "/login/device/code", FakeResponse(502, None))
    resp = client.post("/api/github/connect")
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["success"] is False and "502" in body["error"]


def test_token_missing(client, http):
    assert client.post("/api/github/token", json={}).status_code == 400
    assert client.post("/api/github/token", json={"token": "   "}).status_code == 400
    assert client.post("/api/github/token", data="not json", content_type="text/plain").status_code == 400
    assert http.calls == []


def test_token_invalid(client, http):
    http.add("GET", "/user", FakeResponse(401, {"message": "Bad credentials"}))
    resp = client.post("/api/github/token", json={"token": "ghp_bad"})
    assert resp.status_code == 401
    assert resp.get_json() == {"success": False, "error": "Token non valido"}
    assert settings_service.get_secret("github_token") is None


def test_token_success_then_disconnect(client, http):
    http.add("GET", "/user", FakeResponse(200, USER, headers={"X-OAuth-Scopes": "repo"}))
    resp = client.post("/api/github/token", json={"token": "ghp_good"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True and body["identity"]["login"] == LOGIN
    assert body["identity"]["auth_method"] == "pat" and "access_token" not in body["identity"]
    assert settings_service.get_secret("github_token")["access_token"] == "ghp_good"
    assert client.get("/api/github/status").get_json()["connected"] is True

    resp = client.post("/api/github/disconnect")
    assert resp.status_code == 200 and resp.get_json() == {"success": True}
    assert settings_service.get_secret("github_token") is None
    assert client.get("/api/github/status").get_json()["connected"] is False


# ---------------------------------------------------------------------------
# repos
# ---------------------------------------------------------------------------

def test_repos_requires_auth(client, http):
    resp = client.get("/api/github/repos")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["success"] is False and body["auth_required"] is True and body["error"]


def test_repos_lists_and_marks_selected(client, connected):
    settings_service.update_settings({"github": {"repos": [REPO]}})
    connected.add("GET", "/user/repos", FakeResponse(200, [
        {"full_name": REPO, "private": True, "pushed_at": f"{DAY}T10:00:00Z", "default_branch": "main",
         "description": "Timesheet", "size": 12},
        {"full_name": "bureau/website", "private": False, "pushed_at": None, "default_branch": "master",
         "description": None}]))
    resp = client.get("/api/github/repos")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["selected"] == [REPO]
    assert body["repos"] == [
        {"full_name": REPO, "private": True, "pushed_at": f"{DAY}T10:00:00Z", "default_branch": "main",
         "description": "Timesheet"},
        {"full_name": "bureau/website", "private": False, "pushed_at": None, "default_branch": "master",
         "description": None}]
    client.get("/api/github/repos")
    assert connected.count("/user/repos") == 1  # cached
    client.get("/api/github/repos?refresh=1")
    assert connected.count("/user/repos") == 2


def test_repos_upstream_error(client, connected):
    connected.add("GET", "/user/repos", FakeResponse(500, {"message": "boom"}))
    resp = client.get("/api/github/repos")
    assert resp.status_code == 502 and resp.get_json()["success"] is False


@pytest.mark.parametrize("body", [
    {}, {"repos": "bureau/timesheet"}, {"repos": ["bad name"]}, {"repos": ["noslash"]},
    {"repos": [42]}, {"repos": ["a/b/c"]},
])
def test_put_repos_validation(client, http, body):
    resp = client.put("/api/github/repos", json=body)
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    assert settings_service.get("github.repos") == []


def test_put_repos_saves_unique_names(client, http):
    resp = client.put("/api/github/repos", json={"repos": [f" {REPO} ", "bureau/web.site", REPO]})
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "repos": [REPO, "bureau/web.site"]}
    assert settings_service.get("github.repos") == [REPO, "bureau/web.site"]
    assert client.get("/api/github/status").get_json()["repos"] == [REPO, "bureau/web.site"]


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def test_import_requires_auth(client, http):
    resp = client.post("/api/github/import", json={"date": DAY})
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["success"] is False and body["auth_required"] is True
    assert http.calls == []


def test_import_creates_entries_and_returns_day(client, connected):
    settings_service.update_settings({"github": {"repos": [REPO]}})
    _wire_day(connected)

    resp = client.post("/api/github/import", json={"date": DAY})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True and body["new"] == 2 and body["skipped"] == 0
    assert body["summary"] == {"commits": 1, "pull_requests": 1, "issues": 0}
    kinds = sorted(e["meta"]["kind"] for e in body["entries"])
    assert kinds == ["commits", "pull_request"]
    commits_entry = next(e for e in body["entries"] if e["meta"]["kind"] == "commits")
    assert commits_entry["type"] == "github" and commits_entry["meta"]["repo"] == REPO
    assert commits_entry["text"] == f"{REPO} · 1 commit\n• chore: bump"
    pr_entry = next(e for e in body["entries"] if e["meta"]["kind"] == "pull_request")
    assert pr_entry["text"] == f"{REPO} · Pull request #12 aperta: Add GitHub import"
    assert pr_entry["source_id"] == f"github:pr:{REPO}#12:opened"

    again = client.post("/api/github/import", json={"date": DAY}).get_json()
    assert again["new"] == 0 and again["skipped"] == 2 and len(again["entries"]) == 2
    listed = client.get(f"/api/entries?date={DAY}").get_json()
    assert len(listed["entries"]) == 2


def test_import_rate_limit_is_502(client, connected):
    connected.add("GET", "/events", FakeResponse(403, {"message": "rate limit"},
                                                 headers={"X-RateLimit-Remaining": "0"}))
    resp = client.post("/api/github/import", json={"date": DAY})
    assert resp.status_code == 502
    assert resp.get_json() == {"success": False, "error": "Limite API GitHub raggiunto, riprova più tardi"}


def test_import_revoked_token_is_401_and_clears_secret(client, connected):
    connected.add("GET", "/events", FakeResponse(401, {"message": "Bad credentials"}))
    resp = client.post("/api/github/import", json={"date": DAY})
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["success"] is False and body["auth_required"] is True
    assert settings_service.get_secret("github_token") is None

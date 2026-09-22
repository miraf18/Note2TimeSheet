"""Tests for the application factory, error envelope and /api/config."""

from types import SimpleNamespace

import app as app_module
import config
import routes.elaborate as elaborate_routes
from app import NO_CACHE_HEADERS, create_app

CONFIG_KEYS = {
    "app_name", "app_version", "user_name", "daily_hours", "timezone", "today",
    "openai_configured", "openai_model", "integrations",
}
GITHUB_KEYS = {"configured", "connected", "login", "repos_count"}
MICROSOFT_KEYS = {"configured", "connected", "account", "provider", "outlook_com_available"}


def test_create_app_sets_testing_flag_and_folders():
    app = create_app(testing=True)
    assert app.testing is True
    assert app.static_folder.replace("\\", "/").endswith("/static")
    assert app.template_folder == "templates"


def test_index_is_html_with_no_cache(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert resp.headers["Cache-Control"] == NO_CACHE_HEADERS["Cache-Control"]


def test_api_404_is_json_envelope(client):
    resp = client.get("/api/x")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]


def test_api_405_is_json_envelope(client):
    resp = client.post("/api/days")
    assert resp.status_code == 405
    assert resp.get_json()["success"] is False


def test_non_api_404_keeps_default_html(client):
    resp = client.get("/no-such-page")
    assert resp.status_code == 404
    assert resp.get_json(silent=True) is None


def test_api_responses_have_no_cache_headers(client):
    resp = client.get("/api/days")
    for header, value in NO_CACHE_HEADERS.items():
        assert resp.headers[header] == value


def test_api_config_shape(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.get_json()
    assert CONFIG_KEYS <= set(body)
    assert body["app_name"] == config.APP_NAME
    assert body["app_version"] == config.APP_VERSION
    assert body["today"] == config.today_iso()
    assert body["openai_configured"] is False
    assert set(body["integrations"]["github"]) == GITHUB_KEYS
    assert set(body["integrations"]["microsoft"]) == MICROSOFT_KEYS


def test_api_config_openai_configured_from_env(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert client.get("/api/config").get_json()["openai_configured"] is True


def test_api_config_reports_connected_integrations(client, monkeypatch):
    monkeypatch.setattr(elaborate_routes, "github_service", SimpleNamespace(
        is_configured=lambda: True,
        is_connected=lambda: True,
        get_identity=lambda: {"login": "octocat", "name": "Octo"},
    ))
    monkeypatch.setattr(elaborate_routes, "graph_service", SimpleNamespace(
        is_configured=lambda: True,
        is_connected=lambda: True,
        get_account=lambda: {"username": "user@example.com", "name": "User"},
    ))
    monkeypatch.setattr(elaborate_routes, "outlook_service", SimpleNamespace(is_available=lambda: False))

    body = client.get("/api/config").get_json()
    github = body["integrations"]["github"]
    microsoft = body["integrations"]["microsoft"]
    assert github == {"configured": True, "connected": True, "login": "octocat", "repos_count": 0}
    assert microsoft["connected"] is True
    assert microsoft["account"]["username"] == "user@example.com"
    assert microsoft["provider"] == "graph"
    assert microsoft["outlook_com_available"] is False


def test_api_config_survives_failing_integration(client, monkeypatch):
    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(elaborate_routes, "graph_service", SimpleNamespace(
        is_configured=boom, is_connected=boom, get_account=boom,
    ))
    monkeypatch.setattr(elaborate_routes, "github_service", SimpleNamespace(
        is_configured=boom, is_connected=boom, get_identity=boom,
    ))
    resp = client.get("/api/config")
    assert resp.status_code == 200
    integrations = resp.get_json()["integrations"]
    assert integrations["microsoft"]["connected"] is False
    assert integrations["microsoft"]["account"] is None
    assert integrations["github"] == {"configured": False, "connected": False, "login": None, "repos_count": 0}


def test_api_config_provider_uses_microsoft_route_resolution(client, monkeypatch):
    """/api/config and /api/microsoft/status must agree on the provider (single implementation)."""
    monkeypatch.setattr(elaborate_routes.graph_service, "is_connected", lambda: False)
    monkeypatch.setattr(elaborate_routes.outlook_service, "is_available", lambda: True)
    config_provider = client.get("/api/config").get_json()["integrations"]["microsoft"]["provider"]
    status_provider = client.get("/api/microsoft/status").get_json()["provider"]
    assert config_provider == status_provider == "outlook_com"


# ---------------------------------------------------------------------------
# Command-line entry point helpers
# ---------------------------------------------------------------------------

def test_integration_state_labels():
    def boom():
        raise RuntimeError("no network")

    assert app_module._integration_state(lambda: True) == "connesso"
    assert app_module._integration_state(lambda: False) == "non connesso"
    assert app_module._integration_state(boom) == "non disponibile"


def test_banner_reports_real_integration_state(monkeypatch):
    monkeypatch.setattr(app_module.github_service, "is_connected", lambda: True)
    monkeypatch.setattr(app_module.graph_service, "is_connected", lambda: False)
    lines = "\n".join(app_module._banner_lines(5600))
    assert "GitHub:     connesso" in lines
    assert "Microsoft:  non connesso" in lines


def test_banner_lines_mention_key_facts(monkeypatch, data_dir):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    lines = "\n".join(app_module._banner_lines(5600))
    assert f"{config.APP_NAME} v{config.APP_VERSION}" in lines
    assert "http://localhost:5600" in lines
    assert str(data_dir) in lines
    assert "OpenAI:     configurato" in lines
    assert "GitHub:" in lines and "Microsoft:" in lines


def test_open_browser_never_raises(monkeypatch):
    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", lambda url: opened.append(url))
    app_module._open_browser(5600)
    assert opened == ["http://localhost:5600"]

    def boom(url):
        raise OSError("no browser")

    monkeypatch.setattr(app_module.webbrowser, "open", boom)
    app_module._open_browser(5600)  # logged, not raised


def test_main_prints_banner_and_runs_server(monkeypatch, capsys):
    monkeypatch.setenv("APP_PORT", "5777")
    monkeypatch.setenv("AUTO_OPEN_BROWSER", "true")
    monkeypatch.delenv("APP_HOST", raising=False)
    monkeypatch.setattr(app_module, "load_dotenv", lambda path: False)  # never read the real .env
    run_calls, timers = [], []
    monkeypatch.setattr(app_module.Flask, "run", lambda self, **kwargs: run_calls.append(kwargs))

    class FakeTimer:
        def __init__(self, delay, func, args=()):
            timers.append((delay, func, args))

        def start(self):
            return None

    monkeypatch.setattr(app_module.threading, "Timer", FakeTimer)

    app_module.main()

    assert run_calls == [{"host": "127.0.0.1", "port": 5777, "debug": False}]
    assert timers == [(app_module.BROWSER_OPEN_DELAY_S, app_module._open_browser, (5777,))]
    assert "http://localhost:5777" in capsys.readouterr().out


def test_main_respects_auto_open_browser_false(monkeypatch):
    monkeypatch.setenv("AUTO_OPEN_BROWSER", "false")
    monkeypatch.setattr(app_module, "load_dotenv", lambda path: False)
    monkeypatch.setattr(app_module.Flask, "run", lambda self, **kwargs: None)
    timers = []
    monkeypatch.setattr(app_module.threading, "Timer", lambda *a, **k: timers.append(a))
    app_module.main()
    assert timers == []


def test_main_binds_to_app_host_env(monkeypatch):
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.setenv("AUTO_OPEN_BROWSER", "false")
    monkeypatch.setattr(app_module, "load_dotenv", lambda path: False)
    run_calls = []
    monkeypatch.setattr(app_module.Flask, "run", lambda self, **kwargs: run_calls.append(kwargs))
    app_module.main()
    assert run_calls[0]["host"] == "0.0.0.0"

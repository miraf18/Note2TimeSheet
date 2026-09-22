"""GitHub endpoints (spec §7.6): ``/api/github/*``.

Error mapping: ``GitHubAuthError`` → 401 ``{"success": false, "auth_required": true}``,
``GitHubError`` → 502, validation → 400, anything unexpected → 500 (logged).
"""

from __future__ import annotations

import functools
import re

from flask import Blueprint, current_app, jsonify, request

from routes._common import json_error, parse_json_body, req_date
from services import github_service, settings_service, state_service
from services.github_service import AUTH_REQUIRED, GitHubAuthError, GitHubError

github_bp = Blueprint("github", __name__, url_prefix="/api/github")

REPO_NAME_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
MAX_SELECTED_REPOS = 200
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

MSG_NOT_CONFIGURED = github_service.NOT_CONFIGURED_MESSAGE
MSG_UNREACHABLE = "GitHub non raggiungibile, riprova più tardi"
MSG_TOKEN_MISSING = "Token mancante"
MSG_REPOS_NOT_LIST = "Formato non valido: 'repos' deve essere una lista di stringhe"
MSG_REPOS_TOO_MANY = f"Troppi repository selezionati (massimo {MAX_SELECTED_REPOS})"
MSG_REPO_INVALID = "Nome repository non valido (atteso: owner/nome)"
MSG_UNEXPECTED = "Errore interno durante l'operazione GitHub"


def _guarded(view):
    """Translate service exceptions into the JSON error envelope."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except GitHubAuthError as exc:
            return json_error(str(exc) or github_service.AUTH_MESSAGE, 401, auth_required=True)
        except GitHubError as exc:
            current_app.logger.warning("GitHub upstream error on %s: %s", request.path, exc)
            return json_error(str(exc), 502)
        except Exception:  # noqa: BLE001 - last-resort handler, logged with traceback
            current_app.logger.exception("Unexpected error in GitHub route %s", request.path)
            return json_error(MSG_UNEXPECTED, 500)

    return wrapper


def _selected_repos() -> list[str]:
    repos = settings_service.get("github.repos", [])
    return list(repos) if isinstance(repos, list) else []


def _validate_repos(value) -> tuple[list[str] | None, str | None]:
    """Return ``(clean_list, error)``: unique ``owner/name`` strings or an Italian message."""
    if not isinstance(value, list):
        return None, MSG_REPOS_NOT_LIST
    if len(value) > MAX_SELECTED_REPOS:
        return None, MSG_REPOS_TOO_MANY
    cleaned = []
    for item in value:
        name = item.strip() if isinstance(item, str) else ""
        if not REPO_NAME_RE.match(name):
            return None, MSG_REPO_INVALID
        cleaned.append(name)
    return list(dict.fromkeys(cleaned)), None


@github_bp.get("/status")
@_guarded
def status():
    """Auth snapshot plus the monitored repositories."""
    return jsonify({**github_service.auth_status(), "repos": _selected_repos()})


@github_bp.post("/connect")
@_guarded
def connect():
    """Start the OAuth device flow and return the code to show to the user."""
    if not github_service.is_configured():
        return json_error(MSG_NOT_CONFIGURED, 400)
    device = github_service.start_device_login()
    if device is None:
        error = github_service.auth_status().get("error") or MSG_UNREACHABLE
        return json_error(error, 502)
    return jsonify({"success": True, "device": device})


@github_bp.post("/token")
@_guarded
def connect_token():
    """Connect with a Personal Access Token: body ``{"token"}``."""
    token = parse_json_body().get("token")
    if not isinstance(token, str) or not token.strip():
        return json_error(MSG_TOKEN_MISSING, 400)
    identity, error = github_service.connect_with_token(token)
    if error == github_service.TOKEN_INVALID:
        return json_error(error, 401)
    if error:
        return json_error(error, 502)
    return jsonify({"success": True, "identity": identity})


@github_bp.post("/disconnect")
@_guarded
def disconnect():
    github_service.disconnect()
    return jsonify({"success": True})


@github_bp.get("/repos")
@_guarded
def list_repos():
    """Repositories of the connected account (``?refresh=1`` bypasses the cache)."""
    refresh = (request.args.get("refresh") or "").strip().lower() in TRUE_VALUES
    repos, error = github_service.list_repos(force=refresh)
    if error == AUTH_REQUIRED:
        return json_error(github_service.AUTH_MESSAGE, 401, auth_required=True)
    if error:
        return json_error(error, 502)
    return jsonify({"repos": repos, "selected": _selected_repos()})


@github_bp.put("/repos")
@_guarded
def save_repos():
    """Persist the monitored repositories: body ``{"repos": ["owner/name", ...]}``."""
    repos, error = _validate_repos(parse_json_body().get("repos"))
    if error:
        return json_error(error, 400)
    try:
        settings = settings_service.update_settings({"github": {"repos": repos}})
    except ValueError as exc:
        return json_error(str(exc), 400)
    saved = (settings.get("github") or {}).get("repos", repos) if isinstance(settings, dict) else repos
    return jsonify({"success": True, "repos": saved})


@github_bp.post("/import")
@_guarded
def import_day():
    """Import the day's commits / pull requests / issues as entries."""
    date = state_service.resolve_date(req_date())
    report = github_service.import_day(date)
    return jsonify({"success": True, **report, "entries": state_service.get_entries(date=date)})

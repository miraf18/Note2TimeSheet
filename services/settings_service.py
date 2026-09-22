"""Settings service: non-secret ``settings.json`` and secret ``secrets.json``.

``DEFAULTS`` is built lazily from environment seeds (see ``build_defaults``);
``settings_service.DEFAULTS`` always reflects the current environment.
Nothing is cached in memory: every call reads the files through ``config``.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from typing import Any

import config

logger = logging.getLogger(__name__)

SETTINGS_VERSION = 2
SECTIONS = ("general", "ai", "github", "microsoft")
PROMPT_FIELDS = ("system_intro", "system_rules", "system_output", "user_template")
MICROSOFT_SOURCES = ("auto", "graph", "outlook_com")
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

MAX_USER_NAME_LEN = 100
MAX_MODEL_LEN = 100
MAX_CLIENT_ID_LEN = 200
MAX_PROMPT_LEN = 20000
MIN_HISTORY_DAYS, MAX_HISTORY_DAYS = 1, 365
MAX_DAILY_HOURS = 24.0
MIN_TEMPERATURE, MAX_TEMPERATURE = 0.0, 2.0

OPENAI_KEY_PATCH_FIELD = "openai_api_key"
SECRET_DEFAULTS: dict[str, Any] = {"openai_api_key": "", "github_token": None}

COMMON_TIMEZONES = (
    "Europe/Rome", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Madrid",
    "Europe/Zurich", "Europe/Vienna", "Europe/Amsterdam", "Europe/Brussels", "Europe/Lisbon",
    "Europe/Dublin", "Europe/Prague", "Europe/Warsaw", "Europe/Stockholm", "Europe/Athens",
    "Europe/Istanbul", "Europe/Moscow", "UTC", "America/New_York", "America/Chicago",
    "America/Denver", "America/Los_Angeles", "America/Toronto", "America/Sao_Paulo",
    "America/Mexico_City", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Hong_Kong",
    "Asia/Shanghai", "Asia/Tokyo", "Asia/Seoul", "Australia/Sydney", "Pacific/Auckland",
    "Africa/Cairo", "Africa/Johannesburg",
)


class SettingsValidationError(ValueError):
    """Raised by ``update_settings`` when the merged settings are invalid."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def build_defaults() -> dict:
    """Default settings, seeded from environment variables (fresh dict each call)."""
    return {
        "version": SETTINGS_VERSION,
        "general": {
            "user_name": config.env_str("USER_NAME", config.DEFAULT_USER_NAME),
            "daily_hours": config.DEFAULT_DAILY_HOURS,
            "timezone": config.env_str("TIMESHEET_TIMEZONE", config.DEFAULT_TIMEZONE),
            "history_keep_days": config.env_int("HISTORY_KEEP_DAYS", config.DEFAULT_HISTORY_KEEP_DAYS),
            "openai_model": config.env_str("OPENAI_MODEL", config.DEFAULT_MODEL),
        },
        "ai": {
            "system_intro": None,
            "system_rules": None,
            "system_output": None,
            "user_template": None,
            "temperature": 0.2,
        },
        "github": {
            "client_id": config.env_str("GITHUB_CLIENT_ID", ""),
            "repos": [],
            "include_commits": True,
            "include_pull_requests": True,
            "include_issues": False,
        },
        "microsoft": {
            "client_id": config.env_str("GRAPH_CLIENT_ID", ""),
            "tenant_id": config.env_str("GRAPH_TENANT_ID", config.DEFAULT_TENANT_ID),
            "source": "auto",
        },
    }


def __getattr__(name: str) -> Any:
    """Module-level lazy attribute: ``DEFAULTS`` is rebuilt from env on each access."""
    if name == "DEFAULTS":
        return build_defaults()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def reset_cache() -> None:
    """No in-memory cache is kept; present for the shared test fixture contract."""
    return None


# ---------------------------------------------------------------------------
# Merge helpers (pure)
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _merge_into_defaults(defaults: dict, raw: dict) -> dict:
    """Merge a settings file over the defaults, keeping only known sections."""
    merged = dict(defaults)
    for key, value in raw.items():
        if key == "version":
            continue
        if key not in defaults:
            logger.debug("Ignoring unknown key %r in settings.json", key)
            continue
        if isinstance(defaults[key], dict) and not isinstance(value, dict):
            logger.warning("Section %r in settings.json is not an object; using defaults", key)
            continue
        merged[key] = _deep_merge(defaults[key], value) if isinstance(value, dict) else value
    return merged


def _set_path(settings: dict, path: str, value: Any) -> dict:
    """Return a copy of ``settings`` with the dotted ``path`` set to ``value``."""
    head, _, rest = path.partition(".")
    if not rest:
        return {**settings, head: value}
    child = settings.get(head)
    child = child if isinstance(child, dict) else {}
    return {**settings, head: _set_path(child, rest, value)}


def _get_path(settings: dict, path: str, default: Any = None) -> Any:
    node: Any = settings
    for part in str(path).split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    """Settings deep-merged with defaults. Never raises: corrupt values fall back to defaults."""
    defaults = build_defaults()
    raw = config.read_json(config.settings_file(), None)
    if raw is None:
        return defaults
    if not isinstance(raw, dict):
        logger.warning("settings.json has unexpected content (%s); using defaults", type(raw).__name__)
        return defaults
    merged = _merge_into_defaults(defaults, raw)
    return _reset_invalid_fields(merged, defaults)


def _reset_invalid_fields(settings: dict, defaults: dict) -> dict:
    """Replace invalid leaves with their defaults, logging a warning for each."""
    repaired = settings
    for path, message in _issues(settings):
        logger.warning("Invalid setting %s (%s); using default", path, message)
        repaired = _set_path(repaired, path, _get_path(defaults, path))
    return repaired


def save_settings(settings: dict) -> dict:
    """Atomically write ``settings`` (with ``version`` forced) and return the saved dict."""
    if not isinstance(settings, dict):
        raise ValueError("Le impostazioni devono essere un oggetto JSON.")
    to_save = {"version": SETTINGS_VERSION, **{k: v for k, v in settings.items() if k != "version"}}
    config.atomic_write_json(config.settings_file(), to_save)
    return to_save


def get(path: str, default: Any = None) -> Any:
    """Dotted lookup, e.g. ``get("general.daily_hours")``."""
    return _get_path(load_settings(), path, default)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def update_settings(patch: dict) -> dict:
    """Deep-merge ``patch`` into the current settings, validate, save and return the result.

    Unknown top-level keys are ignored (logged). ``openai_api_key`` in the patch is
    stored in secrets (empty string / None clears it). Raises ``SettingsValidationError``.
    """
    if not isinstance(patch, dict):
        raise SettingsValidationError(["Formato impostazioni non valido."])
    key_errors = _openai_key_issues(patch)
    cleaned = _clean_patch(patch)
    merged = _normalise(_deep_merge(load_settings(), cleaned))
    errors = [*key_errors, *validate(merged)]
    if errors:
        raise SettingsValidationError(errors)
    saved = save_settings(merged)
    if OPENAI_KEY_PATCH_FIELD in patch:
        _apply_openai_key(patch[OPENAI_KEY_PATCH_FIELD])
    return saved


def _clean_patch(patch: dict) -> dict:
    cleaned = {}
    for key, value in patch.items():
        if key == OPENAI_KEY_PATCH_FIELD or key == "version":
            continue
        if key not in SECTIONS:
            logger.info("Ignoring unknown settings key %r", key)
            continue
        if not isinstance(value, dict):
            logger.info("Ignoring settings section %r: not an object", key)
            continue
        cleaned[key] = value
    return cleaned


def _openai_key_issues(patch: dict) -> list[str]:
    if OPENAI_KEY_PATCH_FIELD not in patch:
        return []
    value = patch[OPENAI_KEY_PATCH_FIELD]
    if value is None or isinstance(value, str):
        return []
    return ["La chiave OpenAI deve essere una stringa."]


def _apply_openai_key(value: Any) -> None:
    set_secret("openai_api_key", (value or "").strip() if isinstance(value, str) else "")


# ---------------------------------------------------------------------------
# Normalisation (pure)
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return value
    return value


def _to_int(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return value
    return value


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _prompt_or_none(value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def _clean_repos(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    stripped = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return list(dict.fromkeys(stripped))


def _normalise(settings: dict) -> dict:
    """Coerce types and trim strings so that validation sees canonical values."""
    general = dict(settings.get("general") or {})
    general = {
        **general,
        "user_name": _strip(general.get("user_name")),
        "daily_hours": _to_float(general.get("daily_hours")),
        "timezone": _strip(general.get("timezone")),
        "history_keep_days": _to_int(general.get("history_keep_days")),
        "openai_model": _strip(general.get("openai_model")),
    }
    ai = dict(settings.get("ai") or {})
    ai = {**ai, **{field: _prompt_or_none(ai.get(field)) for field in PROMPT_FIELDS},
          "temperature": _to_float(ai.get("temperature"))}
    github = dict(settings.get("github") or {})
    github = {**github, "client_id": _strip(github.get("client_id")), "repos": _clean_repos(github.get("repos"))}
    microsoft = dict(settings.get("microsoft") or {})
    source = microsoft.get("source")
    microsoft = {
        **microsoft,
        "client_id": _strip(microsoft.get("client_id")),
        "tenant_id": _strip(microsoft.get("tenant_id")),
        "source": source.strip().lower() if isinstance(source, str) else source,
    }
    return {**settings, "general": general, "ai": ai, "github": github, "microsoft": microsoft}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(settings: dict) -> list[str]:
    """Return a list of Italian error messages (empty = valid)."""
    return [message for _, message in _issues(settings)]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _issues(settings: dict) -> list[tuple[str, str]]:
    if not isinstance(settings, dict):
        return [("", "Le impostazioni devono essere un oggetto JSON.")]
    issues: list[tuple[str, str]] = []
    checks = (
        ("general", _general_issues), ("ai", _ai_issues),
        ("github", _github_issues), ("microsoft", _microsoft_issues),
    )
    for section, check in checks:
        value = settings.get(section)
        if not isinstance(value, dict):
            issues.append((section, f"Sezione '{section}' non valida."))
            continue
        issues.extend((f"{section}.{field}", message) for field, message in check(value))
    return issues


def _general_issues(general: dict) -> list[tuple[str, str]]:
    issues = []
    name = general.get("user_name")
    if not isinstance(name, str) or len(name) > MAX_USER_NAME_LEN:
        issues.append(("user_name", f"Il nome utente deve essere una stringa di massimo {MAX_USER_NAME_LEN} caratteri."))
    hours = general.get("daily_hours")
    if not _is_number(hours) or not 0 < hours <= MAX_DAILY_HOURS:
        issues.append(("daily_hours", "Le ore giornaliere devono essere un numero maggiore di 0 e al massimo 24."))
    if not config.is_valid_timezone(general.get("timezone")):
        issues.append(("timezone", "Fuso orario non valido (usa una chiave IANA, es. Europe/Rome)."))
    days = general.get("history_keep_days")
    if not isinstance(days, int) or isinstance(days, bool) or not MIN_HISTORY_DAYS <= days <= MAX_HISTORY_DAYS:
        issues.append(("history_keep_days", "I giorni di storico devono essere un intero tra 1 e 365."))
    model = general.get("openai_model")
    if not isinstance(model, str) or not model.strip() or len(model) > MAX_MODEL_LEN:
        issues.append(("openai_model", "Il modello OpenAI deve essere una stringa non vuota."))
    return issues


def _ai_issues(ai: dict) -> list[tuple[str, str]]:
    issues = []
    for field in PROMPT_FIELDS:
        value = ai.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > MAX_PROMPT_LEN):
            issues.append((field, f"Il campo prompt '{field}' deve essere testo di massimo {MAX_PROMPT_LEN} caratteri."))
    temperature = ai.get("temperature")
    if not _is_number(temperature) or not MIN_TEMPERATURE <= temperature <= MAX_TEMPERATURE:
        issues.append(("temperature", "La temperatura deve essere un numero tra 0 e 2."))
    return issues


def _client_id_issue(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > MAX_CLIENT_ID_LEN:
        return f"Il Client ID deve essere una stringa di massimo {MAX_CLIENT_ID_LEN} caratteri."
    return None


def _github_issues(github: dict) -> list[tuple[str, str]]:
    issues = []
    client_issue = _client_id_issue(github.get("client_id"))
    if client_issue:
        issues.append(("client_id", client_issue))
    repos = github.get("repos")
    if not isinstance(repos, list) or not all(isinstance(r, str) and REPO_RE.match(r) for r in repos):
        issues.append(("repos", "I repository devono essere nel formato owner/nome."))
    for flag in ("include_commits", "include_pull_requests", "include_issues"):
        if not isinstance(github.get(flag), bool):
            issues.append((flag, f"L'opzione '{flag}' deve essere vero o falso."))
    return issues


def _microsoft_issues(microsoft: dict) -> list[tuple[str, str]]:
    issues = []
    client_issue = _client_id_issue(microsoft.get("client_id"))
    if client_issue:
        issues.append(("client_id", client_issue))
    tenant = microsoft.get("tenant_id")
    if not isinstance(tenant, str) or not tenant.strip() or len(tenant) > MAX_CLIENT_ID_LEN:
        issues.append(("tenant_id", "Il Tenant ID deve essere una stringa non vuota."))
    if microsoft.get("source") not in MICROSOFT_SOURCES:
        issues.append(("source", "La sorgente riunioni deve essere: auto, graph oppure outlook_com."))
    return issues


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def load_secrets() -> dict:
    """``{"openai_api_key": "", "github_token": None}`` merged with the secrets file."""
    raw = config.read_json(config.secrets_file(), None)
    if raw is None:
        return dict(SECRET_DEFAULTS)
    if not isinstance(raw, dict):
        logger.warning("secrets.json has unexpected content (%s); using defaults", type(raw).__name__)
        return dict(SECRET_DEFAULTS)
    return {**SECRET_DEFAULTS, **raw}


def save_secrets(secrets: dict) -> dict:
    """Atomically write the secrets file and restrict its permissions (best effort)."""
    if not isinstance(secrets, dict):
        raise ValueError("I segreti devono essere un oggetto JSON.")
    to_save = {**SECRET_DEFAULTS, **secrets}
    path = config.secrets_file()
    config.atomic_write_json(path, to_save)
    _restrict_permissions(path)
    return to_save


def _restrict_permissions(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.debug("Cannot chmod %s: %s", path, exc)


def set_secret(key: str, value: Any) -> None:
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Nome del segreto non valido.")
    save_secrets({**load_secrets(), key: value})


def get_secret(key: str, default: Any = None) -> Any:
    return load_secrets().get(key, default)


def _settings_openai_key() -> str:
    value = get_secret("openai_api_key")
    return value.strip() if isinstance(value, str) else ""


def get_openai_api_key() -> str:
    """OpenAI key from secrets if set, otherwise env ``OPENAI_API_KEY`` (may be empty)."""
    return _settings_openai_key() or config.env_str("OPENAI_API_KEY", "")


def openai_key_source() -> str | None:
    """``"settings"`` | ``"env"`` | ``None`` depending on where the key comes from."""
    if _settings_openai_key():
        return "settings"
    if config.env_str("OPENAI_API_KEY", ""):
        return "env"
    return None

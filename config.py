"""Application configuration: paths, environment defaults, constants, time helpers.

Paths are exposed as *functions* (not import-time constants) so that tests can
redirect ``TIMESHEET_DATA_DIR`` with ``monkeypatch.setenv`` without reloading
modules. Services must call these functions at use time and never cache the
result at module level.

This module must NOT import services (avoid import cycles).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import tempfile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Note2TimeSheet"
APP_VERSION = "2.0.0"
DEFAULT_PORT = 5600
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TIMEZONE = "Europe/Rome"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_DAILY_HOURS = 8.0
DEFAULT_HISTORY_KEEP_DAYS = 30
DEFAULT_USER_NAME = "User"
DEFAULT_TENANT_ID = "common"

ENV_FILE = os.path.join(BASE_DIR, ".env")
DEFAULT_DATA_DIR_NAME = "data"
SETTINGS_FILE_NAME = "settings.json"
SECRETS_FILE_NAME = "secrets.json"
PRACTICES_FILE_NAME = "practices.json"
HISTORY_DIR_NAME = ".timesheet_history"
GRAPH_CACHE_FILE_NAME = ".graph_token_cache.bin"
LEGACY_STATE_FILE_NAME = ".timesheet_state.json"

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_BOUND_FORMAT = "%Y-%m-%dT%H:%M:%S"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f"})


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def env_str(name: str, default: str = "") -> str:
    """Return the stripped value of an environment variable, or ``default``."""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def env_flag(name: str, default: bool) -> bool:
    """Parse a boolean environment variable (1/true/yes/on, 0/false/no/off)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    logger.warning("Invalid boolean for %s=%r, using default %s", name, raw, default)
    return default


def env_int(name: str, default: int) -> int:
    """Parse an integer environment variable, falling back to ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("Invalid integer for %s=%r, using default %s", name, raw, default)
        return default


def app_port() -> int:
    return env_int("APP_PORT", DEFAULT_PORT)


def app_host() -> str:
    """Bind address: loopback by default (local tool), ``0.0.0.0`` inside Docker."""
    return env_str("APP_HOST", DEFAULT_HOST)


def auto_open_browser() -> bool:
    return env_flag("AUTO_OPEN_BROWSER", True)


# ---------------------------------------------------------------------------
# Paths (evaluated at call time)
# ---------------------------------------------------------------------------

def data_dir() -> str:
    """Data directory (env ``TIMESHEET_DATA_DIR`` or ``BASE_DIR/data``), created if missing."""
    path = os.path.abspath(env_str("TIMESHEET_DATA_DIR", os.path.join(BASE_DIR, DEFAULT_DATA_DIR_NAME)))
    os.makedirs(path, exist_ok=True)
    return path


def settings_file() -> str:
    return os.path.join(data_dir(), SETTINGS_FILE_NAME)


def secrets_file() -> str:
    return os.path.join(data_dir(), SECRETS_FILE_NAME)


def practices_file() -> str:
    return os.path.join(data_dir(), PRACTICES_FILE_NAME)


def default_practices_file() -> str:
    return os.path.join(BASE_DIR, PRACTICES_FILE_NAME)


def history_dir() -> str:
    path = os.path.join(data_dir(), HISTORY_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def graph_cache_file() -> str:
    return os.path.join(data_dir(), GRAPH_CACHE_FILE_NAME)


def legacy_state_file() -> str:
    return os.path.join(data_dir(), LEGACY_STATE_FILE_NAME)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _settings_timezone_name() -> str | None:
    """Read ``general.timezone`` straight from settings.json (no settings_service import)."""
    settings = read_json(settings_file(), None)
    if not isinstance(settings, dict):
        return None
    general = settings.get("general")
    if not isinstance(general, dict):
        return None
    name = general.get("timezone")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _try_zoneinfo(name: str | None) -> ZoneInfo | None:
    if not name or not isinstance(name, str):
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def is_valid_timezone(name: Any) -> bool:
    """True when ``name`` is a usable IANA timezone key."""
    return _try_zoneinfo(name) is not None


def local_tz(tz_name: str | None = None) -> dt.tzinfo:
    """Resolve the local timezone.

    Order: explicit ``tz_name`` → ``general.timezone`` in settings.json →
    env ``TIMESHEET_TIMEZONE`` → ``DEFAULT_TIMEZONE`` → UTC.
    """
    candidates = (
        tz_name,
        _settings_timezone_name(),
        env_str("TIMESHEET_TIMEZONE", ""),
        DEFAULT_TIMEZONE,
    )
    for candidate in candidates:
        if not candidate:
            continue
        zone = _try_zoneinfo(candidate)
        if zone is not None:
            return zone
        logger.warning("Unknown timezone %r, trying next fallback", candidate)
    return dt.timezone.utc


def now_local() -> dt.datetime:
    """Aware ``datetime`` in the local timezone."""
    return dt.datetime.now(tz=local_tz())


def today_iso() -> str:
    """Today's date (YYYY-MM-DD) in the local timezone."""
    return now_local().date().isoformat()


def is_valid_date_iso(value: Any) -> bool:
    """True for strings shaped ``YYYY-MM-DD`` that are real calendar dates."""
    if not isinstance(value, str) or not ISO_DATE_RE.match(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def day_bounds_utc(date_iso: str) -> tuple[str, str]:
    """UTC bounds (``YYYY-MM-DDTHH:MM:SS``, no ``Z``) covering the local day ``date_iso``."""
    if not is_valid_date_iso(date_iso):
        raise ValueError(f"Data non valida: {date_iso!r}")
    zone = local_tz()
    day = dt.date.fromisoformat(date_iso)
    start_local = dt.datetime.combine(day, dt.time.min, tzinfo=zone)
    end_local = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=zone)
    return (
        start_local.astimezone(dt.timezone.utc).strftime(_UTC_BOUND_FORMAT),
        end_local.astimezone(dt.timezone.utc).strftime(_UTC_BOUND_FORMAT),
    )


# ---------------------------------------------------------------------------
# JSON file helpers
# ---------------------------------------------------------------------------

def atomic_write_json(path: str, data: Any) -> None:
    """Write ``data`` as JSON to a temp file in the same directory, then ``os.replace``."""
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def read_json(path: str, default: Any) -> Any:
    """Read a JSON file; return ``default`` when missing or corrupt (corrupt → warning)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Corrupt JSON in %s (%s); using default", path, exc)
        return default
    except OSError as exc:
        logger.warning("Cannot read %s (%s); using default", path, exc)
        return default

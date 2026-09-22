"""Practices service: CRUD over ``practices.json`` (``[{"code","name","description"}]``).

On first use the bundled ``BASE_DIR/practices.json`` is copied into the data
directory. All functions return new lists; nothing is mutated in place.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

import config

logger = logging.getLogger(__name__)

MAX_CODE_LEN = 32
MAX_NAME_LEN = 100
MAX_DESCRIPTION_LEN = 2000


class PracticeNotFoundError(KeyError):
    """Raised when a practice code does not exist (``str()`` gives the Italian message)."""

    def __init__(self, code: str):
        self.code = code
        self.message = f"Pratica {code} non trovata"
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


def reset_cache() -> None:
    """No in-memory cache is kept; present for the shared test fixture contract."""
    return None


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------

def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _validate_code(code: Any) -> str:
    clean = _clean_str(code)
    if not clean:
        raise ValueError("Il codice pratica è obbligatorio.")
    if len(clean) > MAX_CODE_LEN:
        raise ValueError(f"Il codice pratica non può superare {MAX_CODE_LEN} caratteri.")
    if any(ch in clean for ch in "\r\n\t"):
        raise ValueError("Il codice pratica non può contenere interruzioni di riga o tabulazioni.")
    return clean


def _validate_name(name: Any) -> str:
    clean = _clean_str(name)
    if not clean:
        raise ValueError("Il nome della pratica è obbligatorio.")
    if len(clean) > MAX_NAME_LEN:
        raise ValueError(f"Il nome della pratica non può superare {MAX_NAME_LEN} caratteri.")
    return clean


def _validate_description(description: Any) -> str:
    if description is not None and not isinstance(description, str):
        raise ValueError("La descrizione della pratica deve essere testo.")
    clean = _clean_str(description)
    if len(clean) > MAX_DESCRIPTION_LEN:
        raise ValueError(f"La descrizione della pratica non può superare {MAX_DESCRIPTION_LEN} caratteri.")
    return clean


def _validated_practice(code: Any, name: Any, description: Any) -> dict:
    return {
        "code": _validate_code(code),
        "name": _validate_name(name),
        "description": _validate_description(description),
    }


def _sanitise(raw: Any) -> list[dict]:
    """Drop malformed / duplicate items coming from disk, logging each one."""
    if not isinstance(raw, list):
        logger.warning("practices.json does not contain a list; ignoring its content")
        return []
    practices: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            logger.warning("Skipping practice #%d: not an object", index)
            continue
        code, name = _clean_str(item.get("code")), _clean_str(item.get("name"))
        if not code or not name:
            logger.warning("Skipping practice #%d: missing code or name", index)
            continue
        if code in seen:
            logger.warning("Skipping practice #%d: duplicate code %s", index, code)
            continue
        seen.add(code)
        practices.append({"code": code, "name": name, "description": _clean_str(item.get("description"))})
    return practices


def find_practice(code: str, practices: list[dict] | None = None) -> dict | None:
    """Return the practice with ``code`` (from ``practices`` or from disk) or ``None``."""
    clean = _clean_str(code)
    for practice in practices if practices is not None else load_practices():
        if practice.get("code") == clean:
            return dict(practice)
    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _ensure_file() -> None:
    """Copy the bundled defaults into the data dir when no practices file exists yet."""
    path = config.practices_file()
    if os.path.exists(path):
        return
    default_path = config.default_practices_file()
    if not os.path.exists(default_path):
        logger.warning("Default practices file %s not found; starting with an empty list", default_path)
        return
    try:
        shutil.copyfile(default_path, path)
        logger.info("Copied default practices to %s", path)
    except OSError as exc:
        logger.warning("Cannot copy default practices to %s: %s", path, exc)


def load_practices() -> list[dict]:
    _ensure_file()
    return _sanitise(config.read_json(config.practices_file(), []))


def save_practices(practices: list[dict]) -> None:
    if not isinstance(practices, list):
        raise ValueError("L'elenco pratiche deve essere una lista.")
    config.atomic_write_json(config.practices_file(), [dict(p) for p in practices])


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_practice(code: str, name: str, description: str = "") -> list[dict]:
    """Append a practice; raises ``ValueError`` on invalid data or duplicate code."""
    practice = _validated_practice(code, name, description)
    practices = load_practices()
    if any(p["code"] == practice["code"] for p in practices):
        raise ValueError(f"Pratica {practice['code']} già esistente")
    updated = [*practices, practice]
    save_practices(updated)
    return updated


def update_practice(code: str, name: str | None = None, description: str | None = None) -> list[dict]:
    """Change name and/or description of an existing practice; raises ``PracticeNotFoundError``."""
    clean_code = _clean_str(code)
    practices = load_practices()
    if not any(p["code"] == clean_code for p in practices):
        raise PracticeNotFoundError(clean_code)
    changes: dict[str, str] = {}
    if name is not None:
        changes["name"] = _validate_name(name)
    if description is not None:
        changes["description"] = _validate_description(description)
    updated = [{**p, **changes} if p["code"] == clean_code else p for p in practices]
    save_practices(updated)
    return updated


def delete_practice(code: str) -> list[dict]:
    """Remove a practice; raises ``PracticeNotFoundError`` when the code is unknown."""
    clean_code = _clean_str(code)
    practices = load_practices()
    updated = [p for p in practices if p["code"] != clean_code]
    if len(updated) == len(practices):
        raise PracticeNotFoundError(clean_code)
    save_practices(updated)
    return updated

"""State service: per-day history files with entries, imported source ids and elaboration.

File per day ``HISTORY_DIR/<YYYY-MM-DD>.json``::

    {
      "date": "2026-09-16",
      "entries": [{"id","type","text","time","duration_min","source_id","meta"}],
      "imported_source_ids": [],
      "elaborated": null, "elaborated_at": null, "elaborated_edited_at": null
    }

Legacy formats are normalised on load: v1 ``activities`` lists, ``type: "outlook"``
→ ``"meeting"``, ``meeting_id`` → ``source_id`` (``meeting:`` prefix),
``imported_meeting_ids`` → ``imported_source_ids``, and the old single-day
``.timesheet_state.json`` file is migrated into the history directory.
All functions accept ``date=None`` (→ today); invalid dates fall back to today.
"""

from __future__ import annotations

import copy
import datetime as dt
import logging
import os
import uuid
from typing import Any, Callable, Iterable

import config
from services import settings_service

logger = logging.getLogger(__name__)

ENTRY_TYPES = ("manual", "meeting", "github")
LEGACY_TYPE_MAP = {"outlook": "meeting"}
MEETING_PREFIX = "meeting:"
DEFAULT_ENTRY_TIME = "00:00"
TIMESTAMP_SPEC = "seconds"


def reset_cache() -> None:
    """No in-memory cache is kept; present for the shared test fixture contract."""
    return None


# ---------------------------------------------------------------------------
# Dates & paths
# ---------------------------------------------------------------------------

def resolve_date(date: Any) -> str:
    """Return a valid ``YYYY-MM-DD`` string, falling back to today."""
    if isinstance(date, dt.datetime):
        date = date.date()
    if isinstance(date, dt.date):
        return date.isoformat()
    if isinstance(date, str) and config.is_valid_date_iso(date.strip()):
        return date.strip()
    return config.today_iso()


def _state_file(date: str) -> str:
    return os.path.join(config.history_dir(), f"{date}.json")


def _now_iso() -> str:
    return config.now_local().isoformat(timespec=TIMESTAMP_SPEC)


# ---------------------------------------------------------------------------
# Normalisation (pure)
# ---------------------------------------------------------------------------

def _fresh_state(date: str) -> dict:
    return {
        "date": date,
        "entries": [],
        "imported_source_ids": [],
        "elaborated": None,
        "elaborated_at": None,
        "elaborated_edited_at": None,
    }


def _meeting_source_id(meeting_id: Any) -> str | None:
    if not isinstance(meeting_id, str) or not meeting_id.strip():
        return None
    clean = meeting_id.strip()
    return clean if clean.startswith(MEETING_PREFIX) else f"{MEETING_PREFIX}{clean}"


def _normalize_type(entry_type: Any) -> str:
    if isinstance(entry_type, str):
        mapped = LEGACY_TYPE_MAP.get(entry_type.strip().lower(), entry_type.strip().lower())
        if mapped in ENTRY_TYPES:
            return mapped
    raise ValueError(f"Tipo attività non valido: {entry_type!r}")


def _clean_duration(duration_min: Any) -> int | None:
    if duration_min is None:
        return None
    if isinstance(duration_min, bool):
        raise ValueError("La durata deve essere un numero intero di minuti.")
    try:
        value = int(duration_min)
    except (TypeError, ValueError) as exc:
        raise ValueError("La durata deve essere un numero intero di minuti.") from exc
    if value < 0:
        raise ValueError("La durata non può essere negativa.")
    return value


def _clean_text(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Il testo dell'attività non può essere vuoto.")
    return text.strip()


def _normalize_entry(raw: dict) -> dict:
    """Bring a stored entry (possibly legacy) to the v2 shape."""
    raw_type = raw.get("type", "manual")
    try:
        entry_type = _normalize_type(raw_type)
    except ValueError:
        logger.warning("Unknown entry type %r, treating as manual", raw_type)
        entry_type = "manual"
    source_id = raw.get("source_id") or _meeting_source_id(raw.get("meeting_id"))
    meta = raw.get("meta")
    duration = raw.get("duration_min")
    return {
        "id": str(raw.get("id") or uuid.uuid4()),
        "type": entry_type,
        "text": str(raw.get("text") or ""),
        "time": str(raw.get("time") or DEFAULT_ENTRY_TIME),
        "duration_min": int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
        "source_id": source_id if isinstance(source_id, str) and source_id else None,
        "meta": copy.deepcopy(meta) if isinstance(meta, dict) else {},
    }


def _normalize_source_ids(data: dict) -> list[str]:
    current = data.get("imported_source_ids")
    legacy = data.get("imported_meeting_ids")
    ids = [s for s in (current if isinstance(current, list) else []) if isinstance(s, str) and s]
    if isinstance(legacy, list):
        ids = [*ids, *(sid for sid in map(_meeting_source_id, legacy) if sid)]
    return list(dict.fromkeys(ids))


def _migrate_v1_activities(old: dict) -> list[dict]:
    """Convert the very first ``activities`` format into entries."""
    entries: list[dict] = []
    for activity in old.get("activities") or []:
        if not isinstance(activity, dict):
            continue
        time = activity.get("time") or DEFAULT_ENTRY_TIME
        if activity.get("type") == "manual":
            entries.append({"type": "manual", "text": activity.get("text", ""), "time": time})
        elif activity.get("type") == "outlook":
            for meeting in activity.get("meetings") or []:
                if isinstance(meeting, dict):
                    entries.append({"type": "meeting", "text": meeting.get("subject") or "Riunione",
                                    "time": time, "duration_min": meeting.get("duration")})
    return entries


def _normalize(data: dict, date: str) -> dict:
    """Return a complete v2 state dict for ``date`` built from ``data`` (never mutates it)."""
    if "activities" in data and "entries" not in data:
        raw_entries: list = _migrate_v1_activities(data)
    else:
        raw_entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    elaborated = data.get("elaborated")
    return {
        **_fresh_state(date),
        "entries": [_normalize_entry(e) for e in raw_entries if isinstance(e, dict)],
        "imported_source_ids": _normalize_source_ids(data),
        "elaborated": copy.deepcopy(elaborated) if isinstance(elaborated, dict) else None,
        "elaborated_at": data.get("elaborated_at") or None,
        "elaborated_edited_at": data.get("elaborated_edited_at") or None,
    }


# ---------------------------------------------------------------------------
# Legacy single-file migration
# ---------------------------------------------------------------------------

def _migrate_legacy_file() -> None:
    """Move the old single-day ``.timesheet_state.json`` into the history dir (once)."""
    path = config.legacy_state_file()
    if not os.path.exists(path):
        return
    data = config.read_json(path, None)
    if not isinstance(data, dict):
        _rename_corrupt_legacy(path)
        return
    date = resolve_date(data.get("date"))
    target = _state_file(date)
    if os.path.exists(target):
        logger.info("Legacy state for %s skipped: day file already exists", date)
    else:
        config.atomic_write_json(target, _normalize(data, date))
        logger.info("Migrated legacy state file into %s", target)
    try:
        os.remove(path)
    except OSError as exc:
        logger.warning("Cannot remove legacy state file %s: %s", path, exc)


def _rename_corrupt_legacy(path: str) -> None:
    backup = f"{path}.corrupt"
    try:
        os.replace(path, backup)
        logger.warning("Legacy state file unreadable; moved to %s", backup)
    except OSError as exc:
        logger.warning("Legacy state file unreadable and cannot be moved: %s", exc)


# ---------------------------------------------------------------------------
# Load / save / prune
# ---------------------------------------------------------------------------

def load_state(date: Any = None) -> dict:
    """State for ``date`` (default today); a fresh state when the file is missing/corrupt."""
    _migrate_legacy_file()
    resolved = resolve_date(date)
    data = config.read_json(_state_file(resolved), None)
    if not isinstance(data, dict):
        if data is not None:
            logger.warning("Day file for %s has unexpected content; starting fresh", resolved)
        return _fresh_state(resolved)
    return _normalize(data, resolved)


def save_state(state: dict) -> None:
    """Atomically persist ``state`` under its day and prune old history."""
    if not isinstance(state, dict):
        raise ValueError("Lo stato deve essere un oggetto JSON.")
    date = resolve_date(state.get("date"))
    config.atomic_write_json(_state_file(date), _normalize(state, date))
    _prune_history()


def _keep_days() -> int:
    raw = settings_service.get("general.history_keep_days", config.DEFAULT_HISTORY_KEEP_DAYS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        logger.warning("Invalid history_keep_days %r, using %d", raw, config.DEFAULT_HISTORY_KEEP_DAYS)
        return config.DEFAULT_HISTORY_KEEP_DAYS


def _stored_dates() -> list[str]:
    """Dates that have a day file, newest first."""
    try:
        names = os.listdir(config.history_dir())
    except OSError as exc:
        logger.warning("Cannot list history dir: %s", exc)
        return []
    dates = (name[:-5] for name in names if name.endswith(".json"))
    return sorted((d for d in dates if config.is_valid_date_iso(d)), reverse=True)


def _prune_history() -> None:
    keep = _keep_days()
    for stale in _stored_dates()[keep:]:
        try:
            os.remove(_state_file(stale))
            logger.info("Pruned history file for %s", stale)
        except OSError as exc:
            logger.warning("Cannot prune history file for %s: %s", stale, exc)


def _summary(date: str) -> dict:
    state = load_state(date)
    return {"date": date, "entry_count": len(state["entries"]), "elaborated": bool(state["elaborated"])}


def list_days() -> list[dict]:
    """Stored days (newest first) with ``{"date","entry_count","elaborated"}``; today always present."""
    _migrate_legacy_file()
    today = config.today_iso()
    summaries = {date: _summary(date) for date in _stored_dates()}
    if today not in summaries:
        summaries[today] = {"date": today, "entry_count": 0, "elaborated": False}
    return sorted(summaries.values(), key=lambda d: d["date"], reverse=True)


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def get_entries(date: Any = None) -> list[dict]:
    return load_state(date)["entries"]


def add_entry(text: str, entry_type: str = "manual", duration_min: int | None = None,
              source_id: str | None = None, meta: dict | None = None, date: Any = None) -> dict:
    """Append a new entry to ``date`` and return it. Raises ``ValueError`` on invalid input."""
    if source_id is not None and not isinstance(source_id, str):
        raise ValueError("L'identificativo sorgente deve essere una stringa.")
    if meta is not None and not isinstance(meta, dict):
        raise ValueError("I metadati devono essere un oggetto.")
    entry = {
        "id": str(uuid.uuid4()),
        "type": _normalize_type(entry_type),
        "text": _clean_text(text),
        "time": config.now_local().strftime("%H:%M"),
        "duration_min": _clean_duration(duration_min),
        "source_id": source_id or None,
        "meta": copy.deepcopy(meta) if meta else {},
    }
    state = load_state(date)
    save_state({**state, "entries": [*state["entries"], entry]})
    return entry


def update_entry(entry_id: str, text: str | None = None, meta: dict | None = None,
                 duration_min: int | None = None, date: Any = None) -> dict | None:
    """Change only the provided fields of an entry; ``None`` when the id is unknown."""
    state = load_state(date)
    existing = next((e for e in state["entries"] if e["id"] == entry_id), None)
    if existing is None:
        return None
    changes: dict[str, Any] = {}
    if text is not None:
        changes["text"] = _clean_text(text)
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError("I metadati devono essere un oggetto.")
        changes["meta"] = copy.deepcopy(meta)
    if duration_min is not None:
        changes["duration_min"] = _clean_duration(duration_min)
    updated = {**existing, **changes}
    entries = [updated if e["id"] == entry_id else e for e in state["entries"]]
    save_state({**state, "entries": entries})
    return updated


def remove_entry(entry_id: str, date: Any = None) -> bool:
    state = load_state(date)
    entries = [e for e in state["entries"] if e["id"] != entry_id]
    if len(entries) == len(state["entries"]):
        return False
    save_state({**state, "entries": entries})
    return True


def find_entry(predicate: Callable[[dict], bool], date: Any = None) -> dict | None:
    return next((e for e in get_entries(date) if predicate(e)), None)


# ---------------------------------------------------------------------------
# Imported source ids
# ---------------------------------------------------------------------------

def is_source_imported(source_id: str, date: Any = None) -> bool:
    return source_id in load_state(date)["imported_source_ids"]


def mark_sources_imported(source_ids: Iterable[str], date: Any = None) -> None:
    """Record ``source_ids`` (any iterable; a bare string counts as one id) without duplicates."""
    candidates = [source_ids] if isinstance(source_ids, str) else list(source_ids or [])
    clean = [s for s in candidates if isinstance(s, str) and s.strip()]
    state = load_state(date)
    existing = state["imported_source_ids"]
    new_ids = [s for s in dict.fromkeys(clean) if s not in existing]
    if not new_ids:
        return
    save_state({**state, "imported_source_ids": [*existing, *new_ids]})


# ---------------------------------------------------------------------------
# Elaboration
# ---------------------------------------------------------------------------

def _with_total(result: dict) -> dict:
    """Copy of ``result`` with ``totale_ore`` recomputed when the timesheet is numeric."""
    timesheet = result.get("timesheet")
    if not isinstance(timesheet, list):
        return copy.deepcopy(result)
    try:
        total = round(sum(float(item["ore"]) for item in timesheet), 2)
    except (KeyError, TypeError, ValueError):
        return copy.deepcopy(result)
    return {**copy.deepcopy(result), "totale_ore": total}


def _elaboration_view(state: dict) -> dict:
    return {
        "result": state["elaborated"],
        "elaborated_at": state["elaborated_at"],
        "elaborated_edited_at": state["elaborated_edited_at"],
    }


def set_elaboration(result: dict, date: Any = None) -> None:
    """Store a fresh AI elaboration (resets the manual-edit timestamp)."""
    if not isinstance(result, dict):
        raise ValueError("Il risultato dell'elaborazione deve essere un oggetto.")
    state = load_state(date)
    save_state({**state, "elaborated": copy.deepcopy(result),
                "elaborated_at": _now_iso(), "elaborated_edited_at": None})


def update_elaboration(result: dict, date: Any = None) -> dict:
    """Store a manually edited elaboration; sets ``elaborated_edited_at`` and returns the view."""
    if not isinstance(result, dict):
        raise ValueError("Il risultato dell'elaborazione deve essere un oggetto.")
    state = load_state(date)
    now = _now_iso()
    new_state = {**state, "elaborated": _with_total(result),
                 "elaborated_at": state["elaborated_at"] or now, "elaborated_edited_at": now}
    save_state(new_state)
    return _elaboration_view(new_state)


def get_elaboration(date: Any = None) -> dict:
    """``{"result","elaborated_at","elaborated_edited_at"}`` for ``date``."""
    return _elaboration_view(load_state(date))

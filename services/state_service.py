"""
State Service — gestione persistenza con storico giornaliero su file JSON.

Ogni giorno ha il proprio file dentro `.timesheet_history/<YYYY-MM-DD>.json`:
{
  "date": "YYYY-MM-DD",
  "entries": [
    {
      "id": "<uuid>",
      "type": "manual|outlook",
      "text": "...",
      "time": "HH:MM",
      "duration_min": null,
      "meeting_id": null
    }
  ],
  "imported_meeting_ids": [],
  "elaborated": null,
  "elaborated_at": null
}

Lo storico conserva al massimo HISTORY_KEEP_DAYS giorni con dati: i file
piu' vecchi vengono eliminati automaticamente. Tutte le funzioni accettano un
parametro `date` opzionale (default: oggi) per leggere/scrivere un giorno
specifico.
"""

import json
import datetime
import uuid
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.getenv("TIMESHEET_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

HISTORY_DIR = os.path.join(DATA_DIR, ".timesheet_history")
os.makedirs(HISTORY_DIR, exist_ok=True)

# File legacy a giorno singolo (versioni precedenti dell'app).
LEGACY_STATE_FILE = os.path.join(DATA_DIR, ".timesheet_state.json")

HISTORY_KEEP_DAYS = int(os.getenv("HISTORY_KEEP_DAYS", "30"))

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _today():
    return datetime.date.today().isoformat()


def _valid_date(date):
    """Return a validated ISO date string, or None if invalid."""
    if not date or not _DATE_RE.match(date):
        return None
    try:
        datetime.date.fromisoformat(date)
        return date
    except ValueError:
        return None


def _resolve_date(date):
    """Normalize the requested date, falling back to today when invalid/None."""
    return _valid_date(date) or _today()


# Public alias for callers that need to know which day will be used.
def resolve_date(date):
    return _resolve_date(date)


def _state_file(date):
    return os.path.join(HISTORY_DIR, f"{date}.json")


def _fresh_state(date=None):
    return {
        "date": date or _today(),
        "entries": [],
        "imported_meeting_ids": [],
        "elaborated": None,
        "elaborated_at": None,
    }


def _migrate_old_state(old):
    """Convert v1 state format (activities) to v2 (entries)."""
    new = _fresh_state(old.get("date", _today()))
    for act in old.get("activities", []):
        if act.get("type") == "manual":
            new["entries"].append({
                "id": str(uuid.uuid4()),
                "type": "manual",
                "text": act.get("text", ""),
                "time": act.get("time", "00:00"),
                "duration_min": None,
                "meeting_id": None,
            })
        elif act.get("type") == "outlook":
            for m in act.get("meetings", []):
                new["entries"].append({
                    "id": str(uuid.uuid4()),
                    "type": "outlook",
                    "text": m.get("subject", "Riunione"),
                    "time": act.get("time", "00:00"),
                    "duration_min": m.get("duration"),
                    "meeting_id": None,
                })
    return new


def _normalize(data, date):
    """Ensure a loaded state dict has all required keys and the right date."""
    if "activities" in data and "entries" not in data:
        data = _migrate_old_state(data)
    data.setdefault("entries", [])
    data.setdefault("imported_meeting_ids", [])
    data.setdefault("elaborated", None)
    data.setdefault("elaborated_at", None)
    data["date"] = date
    return data


def _migrate_legacy_file():
    """Move the old single-day state file into the history dir, once."""
    if not os.path.exists(LEGACY_STATE_FILE):
        return
    try:
        with open(LEGACY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        os.remove(LEGACY_STATE_FILE)
        return
    date = _valid_date(data.get("date")) or _today()
    target = _state_file(date)
    if not os.path.exists(target):
        save_state(_normalize(data, date))
    try:
        os.remove(LEGACY_STATE_FILE)
    except OSError:
        pass


def load_state(date=None):
    """Load the state for `date` (default today). Returns fresh state if absent."""
    date = _resolve_date(date)
    path = _state_file(date)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize(data, date)
    except (FileNotFoundError, json.JSONDecodeError):
        return _fresh_state(date)


def save_state(state):
    """Persist state to its day's file and prune old history."""
    date = _resolve_date(state.get("date"))
    state["date"] = date
    with open(_state_file(date), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    _prune_history()


def _prune_history():
    """Keep only the newest HISTORY_KEEP_DAYS day-files."""
    try:
        files = [
            f for f in os.listdir(HISTORY_DIR)
            if f.endswith(".json") and _valid_date(f[:-5])
        ]
    except FileNotFoundError:
        return
    if len(files) <= HISTORY_KEEP_DAYS:
        return
    # Sort by date descending; delete everything past the keep window.
    files.sort(reverse=True)
    for stale in files[HISTORY_KEEP_DAYS:]:
        try:
            os.remove(os.path.join(HISTORY_DIR, stale))
        except OSError:
            pass


# ---- History listing ----

def list_days():
    """Return stored days (newest first) with summary metadata.

    Always includes today, even if it has no file yet. Each item:
    {"date", "entry_count", "elaborated"}.
    """
    days = {}
    try:
        names = os.listdir(HISTORY_DIR)
    except FileNotFoundError:
        names = []

    for name in names:
        if not name.endswith(".json"):
            continue
        date = _valid_date(name[:-5])
        if not date:
            continue
        state = load_state(date)
        days[date] = {
            "date": date,
            "entry_count": len(state.get("entries", [])),
            "elaborated": bool(state.get("elaborated")),
        }

    today = _today()
    if today not in days:
        days[today] = {"date": today, "entry_count": 0, "elaborated": False}

    return sorted(days.values(), key=lambda d: d["date"], reverse=True)


# ---- Entry CRUD ----

def add_entry(text, entry_type="manual", duration_min=None, meeting_id=None, date=None):
    """Add a new entry to `date` (default today) and return it."""
    state = load_state(date)
    entry = {
        "id": str(uuid.uuid4()),
        "type": entry_type,
        "text": text.strip(),
        "time": datetime.datetime.now().strftime("%H:%M"),
        "duration_min": duration_min,
        "meeting_id": meeting_id,
    }
    state["entries"].append(entry)
    save_state(state)
    return entry


def remove_entry(entry_id, date=None):
    """Remove entry by id from `date`. Returns True if found and removed."""
    state = load_state(date)
    before = len(state["entries"])
    state["entries"] = [e for e in state["entries"] if e["id"] != entry_id]
    if len(state["entries"]) < before:
        save_state(state)
        return True
    return False


def update_entry(entry_id, new_text, date=None):
    """Update entry text in `date`. Returns updated entry or None."""
    state = load_state(date)
    for entry in state["entries"]:
        if entry["id"] == entry_id:
            entry["text"] = new_text.strip()
            save_state(state)
            return entry
    return None


def get_entries(date=None):
    """Return entries for `date` (default today)."""
    return load_state(date)["entries"]


# ---- Outlook dedup ----

def is_meeting_imported(meeting_id, date=None):
    """Check if meeting_id was already imported on `date`."""
    state = load_state(date)
    return meeting_id in state["imported_meeting_ids"]


def mark_meeting_imported(meeting_id, date=None):
    """Record a meeting_id as imported on `date`."""
    state = load_state(date)
    if meeting_id not in state["imported_meeting_ids"]:
        state["imported_meeting_ids"].append(meeting_id)
        save_state(state)


# ---- Elaboration ----

def set_elaboration(result, date=None):
    """Store the AI elaboration result for `date`."""
    state = load_state(date)
    state["elaborated"] = result
    state["elaborated_at"] = datetime.datetime.now().isoformat()
    save_state(state)


def get_elaboration(date=None):
    """Return the last elaboration result for `date` (or None)."""
    state = load_state(date)
    return {
        "result": state.get("elaborated"),
        "elaborated_at": state.get("elaborated_at"),
    }


# Run legacy migration once at import time.
_migrate_legacy_file()

"""
State Service — gestione persistenza giornaliera su file JSON.
Schema:
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
"""

import json
import datetime
import uuid
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.getenv("TIMESHEET_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, ".timesheet_state.json")


def _today():
    return datetime.date.today().isoformat()


def _fresh_state():
    return {
        "date": _today(),
        "entries": [],
        "imported_meeting_ids": [],
        "elaborated": None,
        "elaborated_at": None,
    }


def _migrate_old_state(old):
    """Convert v1 state format (activities) to v2 (entries)."""
    new = _fresh_state()
    new["date"] = old.get("date", _today())
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


def load_state():
    """Load today's state from disk. Resets if date changed. Migrates old format."""
    today = _today()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != today:
            return _fresh_state()
        # Detect old format
        if "activities" in data and "entries" not in data:
            return _migrate_old_state(data)
        # Ensure all keys exist
        data.setdefault("entries", [])
        data.setdefault("imported_meeting_ids", [])
        data.setdefault("elaborated", None)
        data.setdefault("elaborated_at", None)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return _fresh_state()


def save_state(state):
    """Persist state to disk."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---- Entry CRUD ----

def add_entry(text, entry_type="manual", duration_min=None, meeting_id=None):
    """Add a new entry and return it."""
    state = load_state()
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


def remove_entry(entry_id):
    """Remove entry by id. Returns True if found and removed."""
    state = load_state()
    before = len(state["entries"])
    state["entries"] = [e for e in state["entries"] if e["id"] != entry_id]
    if len(state["entries"]) < before:
        save_state(state)
        return True
    return False


def update_entry(entry_id, new_text):
    """Update entry text. Returns updated entry or None."""
    state = load_state()
    for entry in state["entries"]:
        if entry["id"] == entry_id:
            entry["text"] = new_text.strip()
            save_state(state)
            return entry
    return None


def get_entries():
    """Return today's entries."""
    return load_state()["entries"]


# ---- Outlook dedup ----

def is_meeting_imported(meeting_id):
    """Check if meeting_id was already imported today."""
    state = load_state()
    return meeting_id in state["imported_meeting_ids"]


def mark_meeting_imported(meeting_id):
    """Record a meeting_id as imported."""
    state = load_state()
    if meeting_id not in state["imported_meeting_ids"]:
        state["imported_meeting_ids"].append(meeting_id)
        save_state(state)


# ---- Elaboration ----

def set_elaboration(result):
    """Store the AI elaboration result."""
    state = load_state()
    state["elaborated"] = result
    state["elaborated_at"] = datetime.datetime.now().isoformat()
    save_state(state)


def get_elaboration():
    """Return the last elaboration result (or None)."""
    state = load_state()
    return {
        "result": state.get("elaborated"),
        "elaborated_at": state.get("elaborated_at"),
    }

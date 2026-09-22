"""Days & entries endpoints (spec §7.2)."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from routes._common import json_error, parse_json_body, req_date
from services import state_service

logger = logging.getLogger(__name__)

entries_bp = Blueprint("entries", __name__, url_prefix="/api")

MAX_ENTRY_TEXT_LEN = 4000

MSG_EMPTY_TEXT = "Testo vuoto"
MSG_INVALID_TEXT = "Testo non valido"
MSG_TEXT_TOO_LONG = f"Testo troppo lungo (massimo {MAX_ENTRY_TEXT_LEN} caratteri)"
MSG_ENTRY_NOT_FOUND = "Attività non trovata"


def _validate_text(value) -> tuple[str, str | None]:
    """Return ``(clean_text, error_message)``; error is None when valid."""
    if value is None:
        return "", MSG_EMPTY_TEXT
    if not isinstance(value, str):
        return "", MSG_INVALID_TEXT
    text = value.strip()
    if not text:
        return "", MSG_EMPTY_TEXT
    if len(text) > MAX_ENTRY_TEXT_LEN:
        return "", MSG_TEXT_TOO_LONG
    return text, None


@entries_bp.get("/days")
def list_days():
    """Return the stored days (newest first, today always present)."""
    return jsonify({"days": state_service.list_days()})


@entries_bp.get("/entries")
def list_entries():
    """Return the entries of the requested day (``?date=``, default today)."""
    date = state_service.resolve_date(req_date())
    return jsonify({"date": date, "entries": state_service.get_entries(date=date)})


@entries_bp.post("/entry")
def add_entry():
    """Create a manual entry: body ``{"text", "date"}``."""
    body = parse_json_body()
    text, error = _validate_text(body.get("text"))
    if error:
        return json_error(error, 400)
    date = state_service.resolve_date(req_date())
    entry = state_service.add_entry(text, entry_type="manual", date=date)
    return jsonify({"success": True, "entry": entry})


@entries_bp.put("/entry/<entry_id>")
def update_entry(entry_id: str):
    """Replace the text of an entry: body ``{"text", "date"}``."""
    body = parse_json_body()
    text, error = _validate_text(body.get("text"))
    if error:
        return json_error(error, 400)
    date = state_service.resolve_date(req_date())
    updated = state_service.update_entry(entry_id, text=text, date=date)
    if updated is None:
        return json_error(MSG_ENTRY_NOT_FOUND, 404)
    return jsonify({"success": True, "entry": updated})


@entries_bp.delete("/entry/<entry_id>")
def delete_entry(entry_id: str):
    """Delete an entry of the requested day (``?date=``)."""
    date = state_service.resolve_date(req_date())
    if not state_service.remove_entry(entry_id, date=date):
        return json_error(MSG_ENTRY_NOT_FOUND, 404)
    return jsonify({"success": True})

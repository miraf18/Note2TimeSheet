"""Shared helpers for the route blueprints (request parsing, error envelope)."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request


def parse_json_body() -> dict[str, Any]:
    """Return the JSON body of the current request as a dict.

    Missing, malformed or non-object bodies yield an empty dict so callers can
    validate individual fields without special-casing the transport layer.
    """
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def req_date() -> str | None:
    """Return the requested day from ``?date=`` or the JSON body ``date``.

    The raw string is returned (or ``None`` when absent / not a string):
    ``state_service.resolve_date`` validates it and falls back to today.
    """
    query_date = (request.args.get("date") or "").strip()
    if query_date:
        return query_date
    body_date = parse_json_body().get("date")
    if isinstance(body_date, str) and body_date.strip():
        return body_date.strip()
    return None


def json_error(message: str, status: int = 400, **extra: Any):
    """Build the standard error envelope ``{"success": false, "error": ...}``."""
    payload = {"success": False, "error": message, **extra}
    return jsonify(payload), status

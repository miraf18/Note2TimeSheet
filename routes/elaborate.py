"""Elaboration endpoints (``/api/elaborate`` GET/POST/PUT) and ``/api/config``.

Spec §7.1 and §7.3.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

from flask import Blueprint, jsonify
from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

import config
from routes._common import json_error, parse_json_body, req_date
from routes.microsoft import resolve_provider
from services import (
    ai_service,
    github_service,
    graph_service,
    outlook_service,
    practices_service,
    settings_service,
    state_service,
)

logger = logging.getLogger(__name__)

elaborate_bp = Blueprint("elaborate", __name__, url_prefix="/api")

MAX_HOURS_PER_ITEM = 24.0
MAX_PRATICA_LEN = 50
MAX_DESCRIZIONE_LEN = 2000
MAX_NOTE_LEN = 4000

MSG_NO_ENTRIES = "Nessuna attività da elaborare"
MSG_NO_API_KEY = "Chiave OpenAI non configurata. Inseriscila nelle impostazioni o nel file .env."
MSG_AUTH_ERROR = "Chiave OpenAI non valida o non più attiva. Verificala nelle impostazioni."
MSG_RATE_LIMIT = "Limite di utilizzo OpenAI raggiunto. Riprova tra qualche istante."
MSG_CONNECTION = "Impossibile contattare OpenAI. Controlla connessione, proxy o firewall."
MSG_API_STATUS = "OpenAI ha restituito un errore API ({status})."
MSG_UNEXPECTED = "Errore inatteso durante l'elaborazione AI."
MSG_RESULT_NOT_OBJECT = "Il campo 'result' deve essere un oggetto"
MSG_TIMESHEET_EMPTY = "Il timesheet deve contenere almeno una voce"
MSG_NOTE_INVALID = "Il campo 'note' deve essere una stringa"
MSG_NOTE_TOO_LONG = f"Il campo 'note' supera i {MAX_NOTE_LEN} caratteri"


# ---------------------------------------------------------------------------
# /api/config
# ---------------------------------------------------------------------------

def _safe_call(probe: Callable[[], Any], default: Any) -> Any:
    """Call ``probe()`` returning ``default`` on failure: status must never break /api/config."""
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 - logged, never propagated
        logger.warning("Stato integrazione non disponibile (%s): %s", getattr(probe, "__qualname__", probe), exc)
        return default


def _github_status(github_settings: dict) -> dict:
    identity = _safe_call(github_service.get_identity, None)
    login = identity.get("login") if isinstance(identity, dict) else None
    return {
        "configured": bool(_safe_call(github_service.is_configured, False)),
        "connected": bool(_safe_call(github_service.is_connected, False)),
        "login": login,
        "repos_count": len(github_settings.get("repos") or []),
    }


def _microsoft_status(microsoft_settings: dict) -> dict:
    connected = bool(_safe_call(graph_service.is_connected, False))
    outlook_available = bool(_safe_call(outlook_service.is_available, False))
    source = microsoft_settings.get("source") or "auto"
    return {
        "configured": bool(_safe_call(graph_service.is_configured, False)),
        "connected": connected,
        "account": _safe_call(graph_service.get_account, None) if connected else None,
        "provider": resolve_provider(source, connected, outlook_available),
        "outlook_com_available": outlook_available,
    }


@elaborate_bp.get("/config")
def get_config():
    settings = settings_service.load_settings()
    general = settings.get("general") or {}
    return jsonify({
        "app_name": config.APP_NAME,
        "app_version": config.APP_VERSION,
        "user_name": general.get("user_name", ""),
        "daily_hours": general.get("daily_hours", 8.0),
        "timezone": general.get("timezone", config.DEFAULT_TIMEZONE),
        "today": config.today_iso(),
        "openai_configured": bool(settings_service.get_openai_api_key()),
        "openai_model": general.get("openai_model", config.DEFAULT_MODEL),
        "integrations": {
            "github": _github_status(settings.get("github") or {}),
            "microsoft": _microsoft_status(settings.get("microsoft") or {}),
        },
    })


# ---------------------------------------------------------------------------
# POST /api/elaborate
# ---------------------------------------------------------------------------

def _map_openai_error(exc: Exception) -> tuple[str, int] | None:
    """Translate an OpenAI SDK exception into ``(italian_message, http_status)``."""
    # Order matters: AuthenticationError and RateLimitError subclass APIStatusError.
    mapping = (
        (AuthenticationError, MSG_AUTH_ERROR, 401),
        (RateLimitError, MSG_RATE_LIMIT, 502),
        (APIConnectionError, MSG_CONNECTION, 502),
        (APIStatusError, None, 502),
    )
    for error_cls, message, status in mapping:
        if isinstance(exc, error_cls):
            if message is None:
                message = MSG_API_STATUS.format(status=getattr(exc, "status_code", "sconosciuto"))
            return message, status
    return None


def _run_ai(entries: list, date: str, api_key: str) -> dict:
    settings = settings_service.load_settings()
    general = settings.get("general") or {}
    return ai_service.elaborate_timesheet(
        entries=entries,
        practices=practices_service.load_practices(),
        user_name=general.get("user_name", ""),
        date=date,
        daily_hours=float(general.get("daily_hours", 8.0)),
        ai_settings=settings.get("ai") or {},
        model=general.get("openai_model") or config.DEFAULT_MODEL,
        api_key=api_key,
    )


def _ai_error_response(exc: Exception, date: str):
    if isinstance(exc, ValueError):
        logger.warning("Elaborazione AI del %s rifiutata: %s", date, exc)
        return json_error(str(exc), 400)
    mapped = _map_openai_error(exc)
    if mapped is None:
        logger.exception("Errore inatteso durante l'elaborazione AI del %s", date)
        return json_error(MSG_UNEXPECTED, 500)
    message, status = mapped
    logger.error("Errore OpenAI (HTTP %s) durante l'elaborazione del %s: %s", status, date, exc)
    return json_error(message, status)


@elaborate_bp.post("/elaborate")
def run_elaboration():
    """Send the day's entries to the AI and persist the resulting timesheet."""
    date = state_service.resolve_date(req_date())
    entries = state_service.get_entries(date=date)
    if not entries:
        return json_error(MSG_NO_ENTRIES, 400)
    api_key = settings_service.get_openai_api_key()
    if not api_key:
        return json_error(MSG_NO_API_KEY, 400)
    try:
        result = _run_ai(entries, date, api_key)
    except Exception as exc:  # noqa: BLE001 - every error is mapped to an HTTP status
        return _ai_error_response(exc, date)
    state_service.set_elaboration(result, date=date)
    elaboration = state_service.get_elaboration(date=date)
    return jsonify({
        "success": True,
        "result": result,
        "elaborated_at": elaboration.get("elaborated_at"),
        "date": date,
    })


# ---------------------------------------------------------------------------
# GET /api/elaborate
# ---------------------------------------------------------------------------

@elaborate_bp.get("/elaborate")
def get_elaboration():
    date = state_service.resolve_date(req_date())
    elaboration = state_service.get_elaboration(date=date)
    return jsonify({
        "result": elaboration.get("result"),
        "elaborated_at": elaboration.get("elaborated_at"),
        "elaborated_edited_at": elaboration.get("elaborated_edited_at"),
        "date": date,
    })


# ---------------------------------------------------------------------------
# PUT /api/elaborate (manual edits)
# ---------------------------------------------------------------------------

def _parse_hours(value) -> float | None:
    """Accept int/float or numeric strings ("2.5", "2,5"); None when invalid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number <= 0 or number > MAX_HOURS_PER_ITEM:
        return None
    return round(number, 2)


def _clean_item(raw, position: int) -> tuple[dict | None, list[str]]:
    """Validate one timesheet row; returns ``(clean_item, errors)``."""
    prefix = f"Voce {position}"
    if not isinstance(raw, dict):
        return None, [f"{prefix}: formato non valido"]
    errors: list[str] = []
    pratica = raw.get("pratica")
    if not isinstance(pratica, str) or not pratica.strip():
        errors.append(f"{prefix}: codice pratica mancante")
    elif len(pratica.strip()) > MAX_PRATICA_LEN:
        errors.append(f"{prefix}: codice pratica troppo lungo")
    hours = _parse_hours(raw.get("ore"))
    if hours is None:
        errors.append(f"{prefix}: le ore devono essere un numero maggiore di 0")
    descrizione = raw.get("descrizione") or ""
    if not isinstance(descrizione, str):
        errors.append(f"{prefix}: descrizione non valida")
    elif len(descrizione) > MAX_DESCRIZIONE_LEN:
        errors.append(f"{prefix}: descrizione troppo lunga (massimo {MAX_DESCRIZIONE_LEN} caratteri)")
    if errors:
        return None, errors
    return {"pratica": pratica.strip(), "ore": hours, "descrizione": descrizione.strip()}, []


def _validate_result(payload) -> tuple[dict | None, list[str]]:
    """Validate a manually edited result and recompute ``totale_ore``."""
    if not isinstance(payload, dict):
        return None, [MSG_RESULT_NOT_OBJECT]
    timesheet = payload.get("timesheet")
    if not isinstance(timesheet, list) or not timesheet:
        return None, [MSG_TIMESHEET_EMPTY]
    note = payload.get("note") or ""
    if not isinstance(note, str):
        return None, [MSG_NOTE_INVALID]
    if len(note) > MAX_NOTE_LEN:
        return None, [MSG_NOTE_TOO_LONG]
    items: list[dict] = []
    errors: list[str] = []
    for position, raw in enumerate(timesheet, start=1):
        item, item_errors = _clean_item(raw, position)
        errors = errors + item_errors
        if item is not None:
            items.append(item)
    if errors:
        return None, errors
    total = round(sum(item["ore"] for item in items), 2)
    return {"timesheet": items, "totale_ore": total, "note": note.strip()}, []


@elaborate_bp.put("/elaborate")
def save_elaboration():
    """Persist a manually edited timesheet: body ``{"date", "result": {...}}``."""
    body = parse_json_body()
    result, errors = _validate_result(body.get("result"))
    if errors or result is None:
        return json_error("; ".join(errors), 400, errors=errors)
    date = state_service.resolve_date(req_date())
    state_service.update_elaboration(result, date=date)
    elaboration = state_service.get_elaboration(date=date)
    return jsonify({
        "success": True,
        "result": result,
        "elaborated_edited_at": elaboration.get("elaborated_edited_at"),
        "date": date,
    })

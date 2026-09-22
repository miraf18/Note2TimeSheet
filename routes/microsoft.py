"""Microsoft calendar integration endpoints (``/api/microsoft/*``, spec §7.7).

Provider resolution (spec §6)::

    source = settings.microsoft.source            # "auto" | "graph" | "outlook_com"
    graph      if source == "graph"      or (auto and Graph connected)
    outlook_com if source == "outlook_com" or (auto and Outlook COM available)
    None       otherwise
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Blueprint, jsonify

import config
from routes._common import json_error, req_date
from services import graph_service, outlook_service, settings_service, state_service

log = logging.getLogger(__name__)

microsoft_bp = Blueprint("microsoft", __name__, url_prefix="/api/microsoft")

SOURCES = ("auto", "graph", "outlook_com")
DEFAULT_SOURCE = "auto"
PROVIDER_GRAPH = "graph"
PROVIDER_OUTLOOK = "outlook_com"

MSG_NOT_CONFIGURED = "Configura il Client ID Microsoft nelle impostazioni"
MSG_MSAL_MISSING = "Libreria MSAL non installata sul server: esegui 'pip install msal'."
MSG_LOGIN_FAILED = "Impossibile avviare il login Microsoft."
MSG_AUTH_REQUIRED = "Login Microsoft richiesto: connetti l'account dalle impostazioni."
MSG_NO_PROVIDER = (
    "Nessuna integrazione calendario disponibile: connetti un account Microsoft "
    "oppure abilita Outlook desktop nelle impostazioni."
)
MSG_DISCONNECT_FAILED = "Impossibile rimuovere la cache del token Microsoft."


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def configured_source() -> str:
    """``settings.microsoft.source`` normalised to one of ``SOURCES``."""
    value = settings_service.get("microsoft.source", DEFAULT_SOURCE)
    return value if value in SOURCES else DEFAULT_SOURCE


def resolve_provider(source: str, graph_connected: bool, outlook_available: bool) -> Optional[str]:
    """Pure implementation of the spec §6 provider resolution."""
    if source == PROVIDER_GRAPH or (source == DEFAULT_SOURCE and graph_connected):
        return PROVIDER_GRAPH
    if source == PROVIDER_OUTLOOK or (source == DEFAULT_SOURCE and outlook_available):
        return PROVIDER_OUTLOOK
    return None


def current_provider() -> Optional[str]:
    """Provider an import would use right now (also handy for ``/api/config``)."""
    return resolve_provider(
        configured_source(), graph_service.is_connected(), outlook_service.is_available()
    )


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _fetch_meetings(provider: str, date_iso: str):
    if provider == PROVIDER_GRAPH:
        return graph_service.get_meetings(date_iso)
    # OUTLOOK_ACCOUNT (.env) selects the Outlook desktop store; empty = default profile.
    return outlook_service.get_outlook_meetings(date_iso, account_email=config.env_str("OUTLOOK_ACCOUNT"))


def _meeting_meta(meeting: dict, provider: str) -> dict:
    return {
        "provider": provider,
        "start": meeting.get("start"),
        "end": meeting.get("end"),
        "is_allday": bool(meeting.get("is_allday")),
    }


def import_meetings(meetings: list, provider: str, date_iso: str) -> dict:
    """Create ``meeting`` entries for meetings not yet imported on ``date_iso``."""
    new_ids: list = []
    skipped = 0
    for meeting in meetings:
        source_id = f"meeting:{meeting['meeting_id']}"
        if source_id in new_ids or state_service.is_source_imported(source_id, date=date_iso):
            skipped += 1
            continue
        state_service.add_entry(
            text=meeting["subject"],
            entry_type="meeting",
            duration_min=meeting.get("duration"),
            source_id=source_id,
            meta=_meeting_meta(meeting, provider),
            date=date_iso,
        )
        state_service.mark_sources_imported([source_id], date=date_iso)
        new_ids = new_ids + [source_id]
    return {
        "new": len(new_ids),
        "skipped": skipped,
        "entries": state_service.get_entries(date=date_iso),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@microsoft_bp.get("/status")
def status():
    auth = graph_service.auth_status()
    source = configured_source()
    outlook_available = outlook_service.is_available()
    provider = resolve_provider(source, bool(auth.get("connected")), outlook_available)
    return jsonify({
        **auth,
        "provider": provider,
        "source": source,
        "outlook_com_available": outlook_available,
    })


@microsoft_bp.post("/connect")
def connect():
    if not graph_service.MSAL_AVAILABLE:
        log.error("MSAL library not importable: Microsoft login unavailable")
        return json_error(MSG_MSAL_MISSING, 500)
    if not graph_service.is_configured():
        return json_error(MSG_NOT_CONFIGURED, 400)
    device = graph_service.start_device_login()
    if not device:
        message = graph_service.auth_status().get("error") or MSG_LOGIN_FAILED
        log.error("Microsoft device flow could not start: %s", message)
        return json_error(message, 502)
    return jsonify({"success": True, "device": device})


@microsoft_bp.post("/disconnect")
def disconnect():
    try:
        graph_service.disconnect()
    except OSError as exc:
        log.error("Microsoft disconnect failed: %s", exc)
        return json_error(MSG_DISCONNECT_FAILED, 500)
    return jsonify({"success": True})


@microsoft_bp.post("/import")
def import_day():
    date_iso = state_service.resolve_date(req_date())
    source = configured_source()
    provider = resolve_provider(
        source, graph_service.is_connected(), outlook_service.is_available()
    )
    if provider is None:
        return json_error(MSG_NO_PROVIDER, 400)
    if provider == PROVIDER_GRAPH and not graph_service.is_configured():
        return json_error(MSG_NOT_CONFIGURED, 400)
    meetings, error = _fetch_meetings(provider, date_iso)
    if error == graph_service.AUTH_REQUIRED:
        return json_error(MSG_AUTH_REQUIRED, 401, auth_required=True)
    if error:
        log.error("Meeting import failed (%s, %s): %s", provider, date_iso, error)
        return json_error(error, 502)
    result = import_meetings(meetings or [], provider, date_iso)
    return jsonify({"success": True, **result, "provider": provider, "date": date_iso})

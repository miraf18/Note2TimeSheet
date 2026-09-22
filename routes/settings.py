"""Settings endpoints (spec §7.5): read/patch settings, reset prompts, preview."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from routes._common import json_error, parse_json_body, req_date
from services import ai_service, practices_service, settings_service, state_service

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__, url_prefix="/api")

PROMPT_FIELDS = ("system_intro", "system_rules", "system_output", "user_template")
SETTINGS_SECTIONS = ("general", "ai", "github", "microsoft")
IGNORED_KEYS = ("version",)
OPENAI_KEY_FIELD = settings_service.OPENAI_KEY_PATCH_FIELD
MAX_OPENAI_KEY_LEN = 500

MSG_EMPTY_BODY = "Nessuna impostazione da aggiornare"
MSG_KEY_NOT_STRING = "La chiave OpenAI deve essere una stringa"
MSG_KEY_TOO_LONG = f"La chiave OpenAI supera i {MAX_OPENAI_KEY_LEN} caratteri"
MSG_INVALID_PROMPT_FIELDS = "Campi prompt non validi"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timezones(current: str | None) -> list[str]:
    """Curated zones (Europe/Rome first) plus the configured one, so the UI select can show it."""
    zones = list(settings_service.COMMON_TIMEZONES)
    return zones if not current or current in zones else [*zones, current]


def _settings_payload(settings: dict) -> dict:
    key_source = settings_service.openai_key_source()
    general = settings.get("general") or {}
    return {
        "settings": settings,
        "ai_defaults": dict(ai_service.DEFAULT_PROMPTS),
        "ai_effective": ai_service.effective_prompts(settings.get("ai") or {}),
        "placeholders": list(ai_service.PLACEHOLDERS),
        "openai_key_set": key_source is not None,
        "openai_key_source": key_source,
        "timezones": _timezones(general.get("timezone")),
    }


def _normalize_ai_patch(ai_patch: dict) -> tuple[dict, list[str]]:
    """Prompt fields: empty string / null → None (= built-in default)."""
    normalized: dict = {}
    errors: list[str] = []
    for key, value in ai_patch.items():
        if key not in PROMPT_FIELDS:
            normalized[key] = value  # e.g. temperature: validated by settings_service
        elif value is None or (isinstance(value, str) and not value.strip()):
            normalized[key] = None
        elif isinstance(value, str):
            normalized[key] = value
        else:
            errors.append(f"Il campo ai.{key} deve essere una stringa")
    return normalized, errors


def _extract_patch(body: dict) -> tuple[dict, list[str]]:
    """Keep only known sections (each must be an object); collect errors."""
    patch: dict = {}
    errors: list[str] = []
    for key, value in body.items():
        if key in IGNORED_KEYS or key == OPENAI_KEY_FIELD:
            continue
        if key not in SETTINGS_SECTIONS:
            errors.append(f"Sezione sconosciuta: {key}")
        elif not isinstance(value, dict):
            errors.append(f"La sezione '{key}' deve essere un oggetto")
        elif key == "ai":
            normalized, ai_errors = _normalize_ai_patch(value)
            patch[key] = normalized
            errors = errors + ai_errors
        else:
            patch[key] = value
    return patch, errors


def _extract_openai_key(body: dict) -> tuple[str | None, list[str]]:
    """Return ``(key, errors)``: None = not provided, "" = clear the stored key."""
    if OPENAI_KEY_FIELD not in body:
        return None, []
    value = body[OPENAI_KEY_FIELD]
    if value is None:
        return "", []
    if not isinstance(value, str):
        return None, [MSG_KEY_NOT_STRING]
    key = value.strip()
    if len(key) > MAX_OPENAI_KEY_LEN:
        return None, [MSG_KEY_TOO_LONG]
    return key, []


def _validation_failure(errors: list[str]):
    return jsonify({"success": False, "error": errors[0], "errors": errors}), 400


def _errors_of(exc: ValueError) -> list[str]:
    """``SettingsValidationError`` carries an ``errors`` list; plain ValueError does not."""
    errors = getattr(exc, "errors", None)
    return [str(e) for e in errors] if isinstance(errors, list) and errors else [str(exc)]


def _prompt_context() -> tuple[dict, str, list]:
    """Shared inputs for prompt building: (settings, date, entries)."""
    date = state_service.resolve_date(req_date())
    entries = state_service.get_entries(date=date)
    return settings_service.load_settings(), date, entries


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@settings_bp.get("/settings")
def get_settings():
    return jsonify(_settings_payload(settings_service.load_settings()))


@settings_bp.put("/settings")
def update_settings():
    """Apply a partial patch; ``openai_api_key`` is stored separately as a secret."""
    body = parse_json_body()
    if not body:
        return _validation_failure([MSG_EMPTY_BODY])
    patch, errors = _extract_patch(body)
    api_key, key_errors = _extract_openai_key(body)
    if errors or key_errors:
        return _validation_failure(errors + key_errors)
    if api_key is not None:
        patch = {**patch, OPENAI_KEY_FIELD: api_key}
    try:
        # update_settings deep-merges, normalises, validates and saves; the OpenAI key
        # is written to secrets only when the merged settings are valid. Invalid values
        # raise SettingsValidationError (a ValueError carrying an ``errors`` list).
        settings = settings_service.update_settings(patch)
    except ValueError as exc:
        logger.warning("Aggiornamento impostazioni rifiutato: %s", exc)
        return _validation_failure(_errors_of(exc))
    if api_key is not None:
        logger.info("Chiave OpenAI %s dalle impostazioni", "aggiornata" if api_key else "rimossa")
    return jsonify({"success": True, **_settings_payload(settings)})


@settings_bp.post("/settings/ai/reset")
def reset_ai_prompts():
    """Reset the given prompt fields (or all of them) to the built-in defaults."""
    fields = parse_json_body().get("fields") or list(PROMPT_FIELDS)
    if not isinstance(fields, list) or any(field not in PROMPT_FIELDS for field in fields):
        return json_error(MSG_INVALID_PROMPT_FIELDS, 400)
    settings = settings_service.update_settings({"ai": {field: None for field in fields}})
    return jsonify({
        "success": True,
        "settings": settings,
        "ai_effective": ai_service.effective_prompts(settings.get("ai") or {}),
    })


@settings_bp.get("/settings/ai/preview")
def preview_prompts():
    """Return the exact prompts that would be sent to the AI for the given day."""
    settings, date, entries = _prompt_context()
    general = settings.get("general") or {}
    system_prompt, user_prompt = ai_service.build_prompts(
        entries=entries,
        practices=practices_service.load_practices(),
        user_name=general.get("user_name", ""),
        date=date,
        daily_hours=float(general.get("daily_hours", 8.0)),
        ai_settings=settings.get("ai") or {},
    )
    return jsonify({
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "entry_count": len(entries),
        "date": date,
    })

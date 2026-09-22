"""Practices CRUD endpoints (spec §7.4) backed by ``practices_service``.

The route validates shape (types, required fields); domain rules (length limits,
duplicates, unknown codes) live in the service and surface as ValueError → 400,
KeyError → 404.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from routes._common import json_error, parse_json_body
from services import practices_service

logger = logging.getLogger(__name__)

practices_bp = Blueprint("practices", __name__, url_prefix="/api")

FIELD_LABELS = {"code": "Codice", "name": "Nome", "description": "Descrizione"}

MSG_CODE_NAME_REQUIRED = "Codice e nome sono obbligatori"
MSG_NAME_EMPTY = "Il nome non può essere vuoto"
MSG_NOTHING_TO_UPDATE = "Nessuna modifica fornita"


def _not_found(code: str):
    return json_error(f"Pratica {code} non trovata", 404)


def _parse_fields(body: dict) -> tuple[dict, str | None]:
    """Return ``({field: stripped_str_or_None}, error)``; absent fields stay None."""
    fields: dict = {}
    for key, label in FIELD_LABELS.items():
        value = body.get(key)
        if value is not None and not isinstance(value, str):
            return {}, f"{label}: valore non valido"
        fields[key] = value.strip() if isinstance(value, str) else None
    return fields, None


@practices_bp.get("/practices")
def list_practices():
    return jsonify({"practices": practices_service.load_practices()})


@practices_bp.post("/practices")
def add_practice():
    """Create a practice: body ``{"code", "name", "description"}``."""
    fields, error = _parse_fields(parse_json_body())
    if error:
        return json_error(error, 400)
    if not fields.get("code") or not fields.get("name"):
        return json_error(MSG_CODE_NAME_REQUIRED, 400)
    try:
        practices = practices_service.add_practice(
            fields["code"], fields["name"], fields.get("description") or ""
        )
    except ValueError as exc:
        logger.info("Creazione pratica rifiutata: %s", exc)
        return json_error(str(exc), 400)
    return jsonify({"success": True, "practices": practices})


@practices_bp.put("/practices/<code>")
def update_practice(code: str):
    """Update name and/or description of an existing practice."""
    fields, error = _parse_fields(parse_json_body())
    if error:
        return json_error(error, 400)
    name, description = fields.get("name"), fields.get("description")
    if name is None and description is None:
        return json_error(MSG_NOTHING_TO_UPDATE, 400)
    if name is not None and not name:
        return json_error(MSG_NAME_EMPTY, 400)
    try:
        practices = practices_service.update_practice(code, name=name, description=description)
    except KeyError:
        return _not_found(code)
    except ValueError as exc:
        logger.info("Aggiornamento pratica %s rifiutato: %s", code, exc)
        return json_error(str(exc), 400)
    return jsonify({"success": True, "practices": practices})


@practices_bp.delete("/practices/<code>")
def delete_practice(code: str):
    try:
        practices = practices_service.delete_practice(code)
    except KeyError:
        return _not_found(code)
    return jsonify({"success": True, "practices": practices})

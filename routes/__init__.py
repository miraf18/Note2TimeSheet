"""Blueprint registration for the Note2TimeSheet Flask app (HTTP API, spec §7)."""

from __future__ import annotations

from flask import Blueprint, Flask

from routes.elaborate import elaborate_bp
from routes.entries import entries_bp
from routes.github import github_bp
from routes.microsoft import microsoft_bp
from routes.practices import practices_bp
from routes.settings import settings_bp
from routes.ui import ui_bp

BLUEPRINTS: tuple[Blueprint, ...] = (
    ui_bp,
    entries_bp,
    elaborate_bp,
    practices_bp,
    settings_bp,
    github_bp,
    microsoft_bp,
)


def register_blueprints(app: Flask) -> None:
    """Attach every blueprint of the application to ``app``."""
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

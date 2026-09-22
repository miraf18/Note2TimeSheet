"""UI blueprint: serves the single-page application."""

from flask import Blueprint, render_template

ui_bp = Blueprint("ui", __name__)


@ui_bp.get("/")
def index():
    """Render the single HTML page; all behaviour lives in static/js."""
    return render_template("index.html")

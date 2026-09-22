"""Note2TimeSheet — Flask application factory and command-line entry point."""

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Callable

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

import config
from services import github_service, graph_service, settings_service

logger = logging.getLogger(__name__)

API_PREFIX = "/api/"
BROWSER_OPEN_DELAY_S = 1.5
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

API_ERROR_MESSAGES = {
    400: "Richiesta non valida",
    404: "Risorsa non trovata",
    405: "Metodo non consentito",
    500: "Errore interno del server",
}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_app(testing: bool = False) -> Flask:
    """Build the Flask app. ``testing=True`` skips ``.env`` loading so fixtures
    fully control the environment."""
    if not testing:
        load_dotenv(config.ENV_FILE)
        _configure_logging()

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["TESTING"] = testing
    app.json.sort_keys = False
    app.json.ensure_ascii = False

    from routes import register_blueprints

    register_blueprints(app)
    _register_no_cache(app)
    _register_error_handlers(app)
    return app


def _configure_logging() -> None:
    """Basic console logging (no-op when the root logger is already set up)."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def _register_no_cache(app: Flask) -> None:
    @app.after_request
    def add_no_cache_headers(response):
        if request.path.startswith(API_PREFIX) or response.mimetype == "text/html":
            for header, value in NO_CACHE_HEADERS.items():
                response.headers[header] = value
        return response


def _register_error_handlers(app: Flask) -> None:
    for status in API_ERROR_MESSAGES:
        app.register_error_handler(status, _handle_http_error)


def _handle_http_error(error: HTTPException):
    """JSON envelope for API paths; default HTML error page elsewhere."""
    if not request.path.startswith(API_PREFIX):
        return error
    status = getattr(error, "code", None) or 500
    if status >= 500:
        logger.error("Errore %s su %s %s: %s", status, request.method, request.path, error)
    message = API_ERROR_MESSAGES.get(status, "Errore")
    return jsonify({"success": False, "error": message}), status


# ---------------------------------------------------------------------------
# Command-line entry point
# ---------------------------------------------------------------------------

def _integration_state(probe: Callable[[], bool]) -> str:
    """Italian label describing whether an integration is connected (never raises)."""
    try:
        connected = probe()
    except Exception as exc:  # noqa: BLE001 - the banner must never abort startup
        logger.warning("Stato di %s non disponibile: %s", getattr(probe, "__module__", probe), exc)
        return "non disponibile"
    return "connesso" if connected else "non connesso"


def _banner_lines(port: int) -> list[str]:
    openai_state = (
        "configurato"
        if settings_service.get_openai_api_key()
        else "NON configurato (impostalo dalle impostazioni o nel file .env)"
    )
    return [
        "",
        f"  {config.APP_NAME} v{config.APP_VERSION}",
        f"  URL:        http://localhost:{port}",
        f"  Utente:     {settings_service.get('general.user_name', '') or '-'}",
        f"  Dati:       {config.data_dir()}",
        f"  OpenAI:     {openai_state}",
        f"  GitHub:     {_integration_state(github_service.is_connected)}",
        f"  Microsoft:  {_integration_state(graph_service.is_connected)}",
        "",
    ]


def _open_browser(port: int) -> None:
    url = f"http://localhost:{port}"
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - purely a convenience feature
        logger.warning("Impossibile aprire il browser su %s: %s", url, exc)


def main() -> None:
    app = create_app()
    port = config.app_port()
    print("\n".join(_banner_lines(port)))
    if config.auto_open_browser():
        threading.Timer(BROWSER_OPEN_DELAY_S, _open_browser, args=(port,)).start()
    app.run(host=config.app_host(), port=port, debug=False)


if __name__ == "__main__":
    main()

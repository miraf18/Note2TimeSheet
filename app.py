"""
TimeSheet App v2 — Backend Flask
AI-powered local timesheet processing with OpenAI GPT-4.1-mini.
"""

import os
import json
import datetime
import shutil
import webbrowser
import threading

from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

APP_PORT = int(os.getenv("APP_PORT", "5599"))
USER_NAME = os.getenv("USER_NAME", "User")
AUTO_OPEN_BROWSER = os.getenv("AUTO_OPEN_BROWSER", "true").strip().lower() == "true"
DATA_DIR = os.getenv("TIMESHEET_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

PRACTICES_FILE = os.path.join(DATA_DIR, "practices.json")
DEFAULT_PRACTICES_FILE = os.path.join(BASE_DIR, "practices.json")
if not os.path.exists(PRACTICES_FILE) and os.path.exists(DEFAULT_PRACTICES_FILE):
    shutil.copyfile(DEFAULT_PRACTICES_FILE, PRACTICES_FILE)

from services import state_service, webhook_service, outlook_service, ai_service

# ---- Practices helpers ----

def load_practices():
    try:
        with open(PRACTICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_practices(practices):
    with open(PRACTICES_FILE, "w", encoding="utf-8") as f:
        json.dump(practices, f, ensure_ascii=False, indent=2)


# ---- Flask App ----

app = Flask(__name__, template_folder="templates")


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ---- UI ----

@app.route("/")
def index():
    return render_template("index.html")


# ---- Config ----

@app.route("/api/config")
def api_config():
    return jsonify({
        "user_name": USER_NAME,
        "outlook_available": outlook_service.is_available(),
        "webhook_enabled": webhook_service.is_webhook_enabled(),
    })


# ---- Entries ----

@app.route("/api/entries")
def api_entries():
    return jsonify({"entries": state_service.get_entries()})


@app.route("/api/entry", methods=["POST"])
def api_entry_add():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "Testo vuoto"}), 400
    entry = state_service.add_entry(text, entry_type="manual")
    return jsonify({"success": True, "entry": entry})


@app.route("/api/entry/<entry_id>", methods=["DELETE"])
def api_entry_delete(entry_id):
    removed = state_service.remove_entry(entry_id)
    if not removed:
        return jsonify({"success": False, "error": "Entry non trovata"}), 404
    return jsonify({"success": True})


@app.route("/api/entry/<entry_id>", methods=["PUT"])
def api_entry_update(entry_id):
    data = request.get_json(silent=True) or {}
    new_text = (data.get("text") or "").strip()
    if not new_text:
        return jsonify({"success": False, "error": "Testo vuoto"}), 400
    updated = state_service.update_entry(entry_id, new_text)
    if not updated:
        return jsonify({"success": False, "error": "Entry non trovata"}), 404
    return jsonify({"success": True, "entry": updated})


# ---- Outlook ----

@app.route("/api/outlook", methods=["POST"])
def api_outlook():
    if not outlook_service.is_available():
        return jsonify({"success": False, "error": "Outlook non disponibile"}), 400

    meetings, error = outlook_service.get_outlook_meetings()
    if error:
        return jsonify({"success": False, "error": error}), 400

    new_count = 0
    skipped_count = 0

    for m in (meetings or []):
        mid = m["meeting_id"]
        if state_service.is_meeting_imported(mid):
            skipped_count += 1
            continue
        state_service.add_entry(
            text=m["subject"],
            entry_type="outlook",
            duration_min=m["duration"],
            meeting_id=mid,
        )
        state_service.mark_meeting_imported(mid)
        new_count += 1

    return jsonify({
        "success": True,
        "new": new_count,
        "skipped": skipped_count,
        "entries": state_service.get_entries(),
    })


# ---- Elaborate ----

@app.route("/api/elaborate", methods=["POST"])
def api_elaborate():
    entries = state_service.get_entries()
    if not entries:
        return jsonify({"success": False, "error": "Nessuna attività da elaborare"}), 400

    practices = load_practices()
    today = datetime.date.today().isoformat()

    try:
        result = ai_service.elaborate_timesheet(
            entries=entries,
            user_name=USER_NAME,
            date=today,
            practices=practices,
        )
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        try:
            from openai import AuthenticationError, APIConnectionError, APIStatusError
        except Exception:
            AuthenticationError = APIConnectionError = APIStatusError = tuple()

        if AuthenticationError and isinstance(e, AuthenticationError):
            return jsonify({
                "success": False,
                "error": "OpenAI API key non valida o non piu' attiva. Verifica OPENAI_API_KEY nel file .env.",
            }), 401

        if APIConnectionError and isinstance(e, APIConnectionError):
            return jsonify({
                "success": False,
                "error": "Impossibile contattare OpenAI. Controlla connessione, proxy o firewall.",
            }), 502

        if APIStatusError and isinstance(e, APIStatusError):
            return jsonify({
                "success": False,
                "error": f"OpenAI ha restituito un errore API ({getattr(e, 'status_code', 'sconosciuto')}).",
            }), 502

        app.logger.exception("Errore inatteso durante l'elaborazione AI")
        return jsonify({"success": False, "error": f"Errore AI: {e}"}), 500

    state_service.set_elaboration(result)

    # Optionally send to webhook
    if webhook_service.is_webhook_enabled():
        payload = {
            "date": today,
            "user": USER_NAME,
            "type": "elaborated_timesheet",
            "result": result,
        }
        webhook_service.send_to_webhook(payload)

    return jsonify({"success": True, "result": result})


@app.route("/api/elaborate", methods=["GET"])
def api_elaborate_get():
    return jsonify(state_service.get_elaboration())


# ---- Practices CRUD ----

@app.route("/api/practices", methods=["GET"])
def api_practices_get():
    return jsonify({"practices": load_practices()})


@app.route("/api/practices", methods=["POST"])
def api_practices_add():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()

    if not code or not name:
        return jsonify({"success": False, "error": "Codice e nome sono obbligatori"}), 400

    practices = load_practices()
    if any(p["code"] == code for p in practices):
        return jsonify({"success": False, "error": f"Pratica {code} già esistente"}), 400

    practices.append({"code": code, "name": name, "description": description})
    save_practices(practices)
    return jsonify({"success": True, "practices": practices})


@app.route("/api/practices/<code>", methods=["PUT"])
def api_practices_update(code):
    data = request.get_json(silent=True) or {}
    practices = load_practices()
    for p in practices:
        if p["code"] == code:
            p["name"] = (data.get("name") or p["name"]).strip()
            p["description"] = (data.get("description") or p.get("description", "")).strip()
            save_practices(practices)
            return jsonify({"success": True, "practices": practices})
    return jsonify({"success": False, "error": f"Pratica {code} non trovata"}), 404


@app.route("/api/practices/<code>", methods=["DELETE"])
def api_practices_delete(code):
    practices = load_practices()
    new_list = [p for p in practices if p["code"] != code]
    if len(new_list) == len(practices):
        return jsonify({"success": False, "error": f"Pratica {code} non trovata"}), 404
    save_practices(new_list)
    return jsonify({"success": True, "practices": new_list})


# ---- Webhook callbacks (only active when ENABLE_WEBHOOK=true) ----

_callback_messages = []


@app.route("/api/callback", methods=["POST"])
def api_callback():
    if not webhook_service.is_webhook_enabled():
        return jsonify({"ok": False, "error": "Webhook disabilitato"}), 403
    data = request.get_json(silent=True) or {}
    now = datetime.datetime.now()
    msg = {"timestamp": now.isoformat(), "time": now.strftime("%H:%M"), "data": data, "read": False}
    _callback_messages.append(msg)
    print(f"[CALLBACK] Ricevuto da webhook: {json.dumps(data, ensure_ascii=False)[:200]}")
    return jsonify({"ok": True}), 200


@app.route("/api/callbacks", methods=["GET"])
def api_callbacks():
    if not webhook_service.is_webhook_enabled():
        return jsonify({"messages": []})
    unread = [m for m in _callback_messages if not m["read"]]
    for m in unread:
        m["read"] = True
    return jsonify({"messages": unread})


# ---- Main ----

def _open_browser():
    webbrowser.open(f"http://localhost:{APP_PORT}")


if __name__ == "__main__":
    print(f"\n  TimeSheet App v2")
    print(f"  http://localhost:{APP_PORT}")
    print(f"  Utente: {USER_NAME}")
    print(f"  Outlook: {'disponibile' if outlook_service.is_available() else 'non disponibile'}")
    print(f"  Webhook: {'abilitato' if webhook_service.is_webhook_enabled() else 'disabilitato'}")
    print(f"  OpenAI: {'configurato' if os.getenv('OPENAI_API_KEY') else 'NON configurato (imposta OPENAI_API_KEY nel .env)'}")
    print()
    if AUTO_OPEN_BROWSER:
        threading.Timer(1.5, _open_browser).start()
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)

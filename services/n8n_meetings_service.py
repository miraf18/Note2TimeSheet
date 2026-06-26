"""
n8n Meetings Service — riunioni dal calendario tramite un flusso n8n.

A differenza di COM ([outlook_service]) e Graph ([graph_service]), qui non
gestiamo noi l'autenticazione Microsoft: l'app chiama un webhook n8n che ha gia'
le credenziali Microsoft configurate, legge il calendario e risponde con le
riunioni. Funziona ovunque (Docker/server) e non richiede setup Azure lato app.

Config via env:
  N8N_MEETINGS_URL    -> URL del webhook n8n (Respond to Webhook con le riunioni)
  N8N_MEETINGS_TOKEN  -> (opzionale) token inviato come header X-Timesheet-Token

Il flusso n8n da importare e' in `n8n/calendar-to-timesheet.json`.
"""

import os
import datetime

import requests

GRAPH_DT_FMT = "%Y-%m-%dT%H:%M:%S"


def _url():
    return os.getenv("N8N_MEETINGS_URL", "").strip()


def is_configured():
    url = _url()
    return bool(url) and "your-n8n" not in url and "esempio" not in url


def _day_bounds_utc(target_date):
    """(start, end) ISO-8601 UTC strings covering the local day of target_date."""
    local_start = datetime.datetime.combine(target_date, datetime.time.min).astimezone()
    local_end = datetime.datetime.combine(target_date, datetime.time.max).astimezone()
    to_utc = lambda d: d.astimezone(datetime.timezone.utc).strftime(GRAPH_DT_FMT)
    return to_utc(local_start), to_utc(local_end)


def _parse_dt(value):
    """Parse an ISO-ish datetime string to a naive datetime, or None."""
    if not value:
        return None
    raw = str(value).replace("Z", "").split(".")[0].split("+")[0].strip()
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def _normalize(item):
    """Map one raw meeting (app shape OR raw Graph event) to the app shape."""
    # Already in app shape?
    if "subject" in item and ("duration" in item or "is_allday" in item):
        subject = (item.get("subject") or "").strip() or "Senza oggetto"
        is_allday = bool(item.get("is_allday"))
        duration = int(item.get("duration") or (480 if is_allday else 0))
        return {
            "subject": subject,
            "duration": duration,
            "duration_label": item.get("duration_label")
            or ("Tutto il giorno" if is_allday else str(duration)),
            "is_allday": is_allday,
            "meeting_id": str(item.get("meeting_id") or item.get("id") or subject),
        }

    # Raw Microsoft Graph event shape.
    subject = (item.get("subject") or "").strip() or "Senza oggetto"
    is_allday = bool(item.get("isAllDay"))
    start = item.get("start") or {}
    end = item.get("end") or {}
    start_dt = _parse_dt(start.get("dateTime") if isinstance(start, dict) else start)
    end_dt = _parse_dt(end.get("dateTime") if isinstance(end, dict) else end)
    if is_allday:
        duration = 480
    elif start_dt and end_dt:
        duration = int((end_dt - start_dt).total_seconds() // 60)
    else:
        duration = 0
    return {
        "subject": subject,
        "duration": duration,
        "duration_label": "Tutto il giorno" if is_allday else str(duration),
        "is_allday": is_allday,
        "meeting_id": str(item.get("id") or subject),
    }


def _extract_list(payload):
    """Pull the meetings array out of various plausible n8n response shapes."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("meetings", "value", "data", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def get_meetings(account_email="", target_date=None):
    """
    Ask the n8n flow for the meetings of `target_date` (default today).
    Returns (meetings | None, error | None).
    """
    if not is_configured():
        return None, "Flusso n8n non configurato (manca N8N_MEETINGS_URL)."

    if not target_date:
        target_date = datetime.date.today()
    elif isinstance(target_date, str):
        target_date = datetime.date.fromisoformat(target_date)
    start_utc, end_utc = _day_bounds_utc(target_date)

    headers = {"Content-Type": "application/json"}
    token = os.getenv("N8N_MEETINGS_TOKEN", "").strip()
    if token:
        headers["X-Timesheet-Token"] = token

    body = {
        "date": target_date.isoformat(),
        "start": start_utc,
        "end": end_utc,
        "user": os.getenv("USER_NAME", ""),
    }

    try:
        resp = requests.post(_url(), json=body, headers=headers, timeout=30)
    except requests.exceptions.ConnectionError:
        return None, "Impossibile connettersi al flusso n8n."
    except requests.exceptions.Timeout:
        return None, "Timeout: il flusso n8n non risponde."
    except requests.RequestException as e:
        return None, f"Errore chiamando n8n: {e}"

    if resp.status_code == 401 or resp.status_code == 403:
        return None, "n8n ha rifiutato la richiesta (token non valido?)."
    if resp.status_code >= 400:
        return None, f"n8n ha risposto {resp.status_code}."

    try:
        payload = resp.json()
    except ValueError:
        return None, "Risposta n8n non in formato JSON."

    meetings = []
    seen = set()
    for raw in _extract_list(payload):
        if not isinstance(raw, dict):
            continue
        if raw.get("isCancelled") or raw.get("is_cancelled"):
            continue
        m = _normalize(raw)
        if not m["is_allday"] and m["duration"] < 5:
            continue
        if m["meeting_id"] in seen:
            continue
        seen.add(m["meeting_id"])
        meetings.append(m)

    return meetings, None

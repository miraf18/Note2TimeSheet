"""
Outlook Service — estrazione riunioni dal calendario Outlook tramite COM (Windows).
Nessun limite giornaliero: la deduplicazione avviene per meeting_id in state_service.
"""

import datetime
import hashlib

OUTLOOK_AVAILABLE = False
try:
    import win32com.client
    OUTLOOK_AVAILABLE = True
except ImportError:
    pass

OL_FOLDER_CALENDAR = 9


def is_available():
    return OUTLOOK_AVAILABLE


def _to_python_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.datetime(
            value.year, value.month, value.day,
            value.hour, value.minute, value.second,
        )
    except Exception:
        return None


def _get_meeting_id(appt, subject, start_dt):
    """Extract a stable unique ID for an appointment. Fallback chain."""
    try:
        gid = getattr(appt, "GlobalAppointmentID", None)
        if gid:
            return str(gid)
    except Exception:
        pass
    try:
        eid = getattr(appt, "EntryID", None)
        if eid:
            return str(eid)
    except Exception:
        pass
    # Hash fallback
    raw = f"{subject}|{start_dt.isoformat() if start_dt else ''}"
    return hashlib.md5(raw.encode()).hexdigest()


def get_outlook_meetings(account_email=""):
    """
    Extract today's meetings from Outlook.
    Returns (meetings: list | None, error: str | None).
    Each meeting dict: {subject, duration, duration_label, is_allday, meeting_id}
    """
    if not OUTLOOK_AVAILABLE:
        return None, "Outlook non disponibile (pywin32 non installato)"

    import os
    account_email = account_email or os.getenv("OUTLOOK_ACCOUNT", "")

    try:
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        today = datetime.date.today()
        start_dt = datetime.datetime.combine(today, datetime.time.min)
        end_dt = datetime.datetime.combine(today, datetime.time.max)

        # Find target store
        target_store = None
        if account_email:
            for store in outlook.Stores:
                try:
                    name = store.DisplayName or ""
                except Exception:
                    name = ""
                if account_email.lower() in name.lower():
                    target_store = store
                    break

        if not target_store:
            try:
                target_store = outlook.DefaultStore
            except Exception:
                return None, "Nessun account Outlook trovato"

        try:
            calendar = target_store.GetDefaultFolder(OL_FOLDER_CALENDAR)
        except Exception:
            return None, "Calendario non accessibile"

        items = calendar.Items
        items.Sort("[Start]")
        items.IncludeRecurrences = True

        start_str = start_dt.strftime("%m/%d/%Y %H:%M")
        end_str = end_dt.strftime("%m/%d/%Y %H:%M")
        start_str2 = start_dt.strftime("%d/%m/%Y %H:%M")
        end_str2 = end_dt.strftime("%d/%m/%Y %H:%M")

        filters = [
            f"[Start] >= '{start_str}' AND [Start] <= '{end_str}'",
            f"[Start] <= '{end_str}' AND [End] >= '{start_str}'",
            f"[Start] >= '{start_str2}' AND [Start] <= '{end_str2}'",
            f"[Start] <= '{end_str2}' AND [End] >= '{start_str2}'",
        ]

        meetings = []
        seen_ids = set()

        for filter_str in filters:
            try:
                restricted = items.Restrict(filter_str)
            except Exception:
                continue
            for appt in restricted:
                try:
                    subject = (getattr(appt, "Subject", "") or "").strip() or "Senza oggetto"

                    appt_start = _to_python_datetime(getattr(appt, "Start", None))
                    meeting_id = _get_meeting_id(appt, subject, appt_start)

                    if meeting_id in seen_ids:
                        continue
                    seen_ids.add(meeting_id)

                    if "annullat" in subject.lower() or "canceled" in subject.lower():
                        continue

                    duration = int(getattr(appt, "Duration", 0) or 0)
                    is_allday = bool(getattr(appt, "AllDayEvent", False))

                    if not is_allday and duration < 5:
                        continue

                    meetings.append({
                        "subject": subject,
                        "duration": duration if not is_allday else 480,
                        "duration_label": "Tutto il giorno" if is_allday else str(duration),
                        "is_allday": is_allday,
                        "meeting_id": meeting_id,
                    })
                except Exception:
                    continue

        return meetings, None

    except Exception as e:
        return None, f"Errore Outlook: {e}"

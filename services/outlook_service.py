"""
Outlook desktop (COM) fallback - Windows only.

Reads the meetings of a given local day straight from the running Outlook
client through ``win32com``. Times are the machine's local time (Outlook COM
does not expose time zones), which is the expected setup for a native Windows
install where the app and Outlook run on the same PC.

Deduplication by ``meeting_id`` is handled by ``state_service``; the id comes
from ``GlobalAppointmentID`` / ``EntryID`` with an md5 hash fallback (kept
identical to v1 so previously imported meetings are still recognised).
"""
from __future__ import annotations

import datetime
import hashlib
import logging
from typing import Optional

from services.graph_service import daily_minutes

try:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    OUTLOOK_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the platform
    pythoncom = None
    win32com = None
    OUTLOOK_AVAILABLE = False

log = logging.getLogger(__name__)

OL_FOLDER_CALENDAR = 9
MIN_MEETING_MINUTES = 5
ALLDAY_LABEL = "Tutto il giorno"
NO_SUBJECT = "Senza oggetto"
CANCELLED_MARKERS = ("annullat", "canceled", "cancelled")
# Outlook's Restrict() parses dates with the machine locale: try both orders.
DATE_FORMATS = ("%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M")
FILTER_TEMPLATES = (
    "[Start] >= '{start}' AND [Start] <= '{end}'",
    "[Start] <= '{end}' AND [End] >= '{start}'",
)


def is_available() -> bool:
    """True only when ``win32com`` could be imported (native Windows + pywin32)."""
    return OUTLOOK_AVAILABLE


def reset_cache() -> None:
    """No in-memory cache is kept; present for the shared test fixture contract."""
    return None


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _to_python_datetime(value) -> Optional[datetime.datetime]:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.datetime(
            value.year, value.month, value.day, value.hour, value.minute, value.second
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _com_attr(appt, name: str, default=None):
    """``getattr`` that tolerates COM attribute errors."""
    try:
        return getattr(appt, name, default)
    except Exception as exc:  # COM can raise arbitrary pywintypes errors
        log.debug("Outlook attribute %s unreadable: %s", name, exc)
        return default


def _meeting_id(appt, subject: str, start_dt: Optional[datetime.datetime]) -> str:
    """Stable unique id: GlobalAppointmentID → EntryID → md5(subject|start)."""
    for attr in ("GlobalAppointmentID", "EntryID"):
        value = _com_attr(appt, attr)
        if value:
            return str(value)
    raw = f"{subject}|{start_dt.isoformat() if start_dt else ''}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()  # nosec - not security related


def _day_bounds(date_iso: str) -> "tuple[datetime.datetime, datetime.datetime]":
    day = datetime.date.fromisoformat(str(date_iso))
    return (
        datetime.datetime.combine(day, datetime.time.min),
        datetime.datetime.combine(day, datetime.time.max),
    )


def _day_filters(day_start: datetime.datetime, day_end: datetime.datetime) -> list:
    return [
        template.format(start=day_start.strftime(fmt), end=day_end.strftime(fmt))
        for fmt in DATE_FORMATS
        for template in FILTER_TEMPLATES
    ]


def _overlaps_day(start, end, day_start, day_end) -> bool:
    """True if [start, end] intersects the requested day (end exclusive)."""
    if start is None:
        return False
    if start > day_end:
        return False
    if start >= day_start:
        return True
    return end is not None and end > day_start


def _appointment_to_meeting(appt, day_start, day_end, allday_minutes: int) -> Optional[dict]:
    """Convert one COM appointment to the v2 meeting shape (None = skip)."""
    subject = str(_com_attr(appt, "Subject", "") or "").strip() or NO_SUBJECT
    start_dt = _to_python_datetime(_com_attr(appt, "Start"))
    duration = int(_com_attr(appt, "Duration", 0) or 0)
    end_dt = _to_python_datetime(_com_attr(appt, "End"))
    if end_dt is None and start_dt is not None:
        end_dt = start_dt + datetime.timedelta(minutes=duration)
    if not _overlaps_day(start_dt, end_dt, day_start, day_end):
        return None
    lowered = subject.lower()
    if any(marker in lowered for marker in CANCELLED_MARKERS):
        return None
    is_allday = bool(_com_attr(appt, "AllDayEvent", False))
    if not is_allday and duration < MIN_MEETING_MINUTES:
        return None
    return {
        "subject": subject,
        "duration": allday_minutes if is_allday else duration,
        "duration_label": ALLDAY_LABEL if is_allday else str(duration),
        "is_allday": is_allday,
        "meeting_id": _meeting_id(appt, subject, start_dt),
        "start": start_dt.strftime("%H:%M") if start_dt else None,
        "end": end_dt.strftime("%H:%M") if end_dt else None,
    }


def _safe_convert(appt, day_start, day_end, allday_minutes: int) -> Optional[dict]:
    try:
        return _appointment_to_meeting(appt, day_start, day_end, allday_minutes)
    except Exception as exc:
        log.warning("Skipping unreadable Outlook appointment: %s", exc)
        return None


def _collect_meetings(items, filters, day_start, day_end, allday_minutes: int) -> list:
    meetings: list = []
    seen: set = set()
    for filter_str in filters:
        try:
            restricted = items.Restrict(filter_str)
        except Exception as exc:
            log.debug("Outlook rejected filter %r: %s", filter_str, exc)
            continue
        for appt in restricted:
            meeting = _safe_convert(appt, day_start, day_end, allday_minutes)
            if meeting is None or meeting["meeting_id"] in seen:
                continue
            seen = seen | {meeting["meeting_id"]}
            meetings = meetings + [meeting]
    return meetings


# ---------------------------------------------------------------------------
# Outlook session helpers
# ---------------------------------------------------------------------------

def _find_store(namespace, account_email: str):
    if account_email:
        needle = account_email.lower()
        for store in namespace.Stores:
            name = str(_com_attr(store, "DisplayName", "") or "")
            if needle in name.lower():
                return store
    try:
        return namespace.DefaultStore
    except Exception as exc:
        log.error("Outlook default store not available: %s", exc)
        return None


def _open_calendar(namespace, account_email: str):
    """Return ``(calendar_folder, None)`` or ``(None, error)``."""
    store = _find_store(namespace, account_email)
    if store is None:
        return None, "Nessun account Outlook trovato."
    try:
        return store.GetDefaultFolder(OL_FOLDER_CALENDAR), None
    except Exception as exc:
        log.error("Outlook calendar folder not accessible: %s", exc)
        return None, "Calendario Outlook non accessibile."


def get_outlook_meetings(date_iso: str, account_email: str = "") -> "tuple[Optional[list], Optional[str]]":
    """
    Meetings of the local day ``date_iso`` from the Outlook desktop calendar.

    Returns ``(meetings, None)`` or ``(None, error)``. Each meeting:
    ``{subject, duration, duration_label, is_allday, meeting_id, start, end}``.
    """
    if not OUTLOOK_AVAILABLE:
        return None, "Outlook desktop non disponibile (pywin32 non installato)."
    try:
        day_start, day_end = _day_bounds(date_iso)
    except (TypeError, ValueError):
        return None, "Data non valida."
    # Flask serves each request on its own thread: COM must be initialised
    # on the calling thread or Dispatch fails with "CoInitialize has not been called".
    initialised = _co_initialize()
    try:
        namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        calendar, error = _open_calendar(namespace, account_email)
        if error:
            return None, error
        items = calendar.Items
        items.Sort("[Start]")
        items.IncludeRecurrences = True
        meetings = _collect_meetings(
            items, _day_filters(day_start, day_end), day_start, day_end, daily_minutes()
        )
        return sorted(meetings, key=lambda m: m.get("start") or ""), None
    except Exception as exc:
        log.error("Outlook COM error: %s", exc)
        return None, f"Errore Outlook: {exc}"
    finally:
        if initialised:
            _co_uninitialize()


def _co_initialize() -> bool:
    """Initialise COM for the current thread; True when we must uninitialise later."""
    if pythoncom is None:
        return False
    try:
        pythoncom.CoInitialize()
        return True
    except Exception as exc:  # already initialised with another model, etc.
        log.debug("CoInitialize skipped: %s", exc)
        return False


def _co_uninitialize() -> None:
    try:
        pythoncom.CoUninitialize()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("CoUninitialize failed: %s", exc)

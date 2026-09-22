"""Unit tests for ``services.outlook_service`` (Outlook COM objects are faked; no Outlook needed)."""

from __future__ import annotations

import datetime
import hashlib
from types import SimpleNamespace

import pytest

from services import outlook_service, settings_service

DATE = "2026-09-16"
DAY_START = datetime.datetime(2026, 9, 16, 0, 0)
DAY_END = datetime.datetime(2026, 9, 16, 23, 59, 59, 999999)
ALLDAY_MINUTES = 480
UNAVAILABLE_MESSAGE = "Outlook desktop non disponibile (pywin32 non installato)."


def dt(hour: int, minute: int = 0, day: int = 16) -> datetime.datetime:
    return datetime.datetime(2026, 9, day, hour, minute)


# ---------------------------------------------------------------------------
# Fakes (duck-typed Outlook COM objects)
# ---------------------------------------------------------------------------

class FakeAppointment:
    def __init__(self, subject="Riunione team", start=None, end=None, duration=30,
                 all_day=False, global_id="GID-1", entry_id="EID-1"):
        self.Subject = subject
        self.Start = dt(9) if start is None else start
        self.End = end
        self.Duration = duration
        self.AllDayEvent = all_day
        self.GlobalAppointmentID = global_id
        self.EntryID = entry_id


class BrokenStartAppointment(FakeAppointment):
    """``Start`` raises, like a COM property can."""

    @property
    def Start(self):  # noqa: N802 - COM naming
        raise RuntimeError("COM failure")

    @Start.setter
    def Start(self, value):  # noqa: N802
        return None


class FakeItems:
    def __init__(self, appointments, reject_filters=()):
        self._appointments = list(appointments)
        self._reject = tuple(reject_filters)
        self.sorted_by = None
        self.IncludeRecurrences = False
        self.filters = []

    def Sort(self, key):  # noqa: N802
        self.sorted_by = key

    def Restrict(self, filter_str):  # noqa: N802
        self.filters.append(filter_str)
        if any(marker in filter_str for marker in self._reject):
            raise RuntimeError("Condition is not valid")
        return list(self._appointments)


class FakeStore:
    def __init__(self, display_name, items=None, calendar_error=None):
        self.DisplayName = display_name
        self._items = items
        self._calendar_error = calendar_error

    def GetDefaultFolder(self, folder_type):  # noqa: N802
        assert folder_type == outlook_service.OL_FOLDER_CALENDAR
        if self._calendar_error:
            raise RuntimeError(self._calendar_error)
        return SimpleNamespace(Items=self._items)


class FakeNamespace:
    def __init__(self, stores, default_store=None):
        self.Stores = list(stores)
        self._default = default_store

    @property
    def DefaultStore(self):  # noqa: N802
        if self._default is None:
            raise RuntimeError("no default store")
        return self._default


def install_outlook(monkeypatch, namespace=None, dispatch_error=None):
    """Make ``outlook_service`` believe pywin32 is present and hand it our fake Outlook."""

    def dispatch(prog_id):
        assert prog_id == "Outlook.Application"
        if dispatch_error is not None:
            raise dispatch_error
        return SimpleNamespace(GetNamespace=lambda kind: namespace)

    monkeypatch.setattr(outlook_service, "OUTLOOK_AVAILABLE", True)
    monkeypatch.setattr(outlook_service, "win32com", SimpleNamespace(client=SimpleNamespace(Dispatch=dispatch)))


def convert(**kwargs):
    return outlook_service._appointment_to_meeting(FakeAppointment(**kwargs), DAY_START, DAY_END, ALLDAY_MINUTES)


# ---------------------------------------------------------------------------
# Availability & helpers
# ---------------------------------------------------------------------------

def test_is_available_follows_import_flag(monkeypatch):
    monkeypatch.setattr(outlook_service, "OUTLOOK_AVAILABLE", True)
    assert outlook_service.is_available() is True
    monkeypatch.setattr(outlook_service, "OUTLOOK_AVAILABLE", False)
    assert outlook_service.is_available() is False


def test_reset_cache_is_noop():
    assert outlook_service.reset_cache() is None


def test_to_python_datetime_variants():
    aware = datetime.datetime(2026, 9, 16, 9, 0, tzinfo=datetime.timezone.utc)
    assert outlook_service._to_python_datetime(aware) == dt(9)
    com_like = SimpleNamespace(year=2026, month=9, day=16, hour=11, minute=5, second=7)
    assert outlook_service._to_python_datetime(com_like) == datetime.datetime(2026, 9, 16, 11, 5, 7)
    assert outlook_service._to_python_datetime(None) is None
    assert outlook_service._to_python_datetime("2026-09-16") is None


def test_com_attr_tolerates_raising_properties():
    appt = BrokenStartAppointment()
    assert outlook_service._com_attr(appt, "Start", "fallback") == "fallback"
    assert outlook_service._com_attr(appt, "Subject") == "Riunione team"
    assert outlook_service._com_attr(appt, "Missing", 42) == 42


def test_meeting_id_prefers_global_then_entry_then_hash():
    assert outlook_service._meeting_id(FakeAppointment(global_id="G", entry_id="E"), "s", dt(9)) == "G"
    assert outlook_service._meeting_id(FakeAppointment(global_id="", entry_id="E"), "s", dt(9)) == "E"
    expected = hashlib.md5(f"Demo|{dt(9).isoformat()}".encode("utf-8")).hexdigest()
    assert outlook_service._meeting_id(FakeAppointment(global_id="", entry_id=None), "Demo", dt(9)) == expected
    no_start = outlook_service._meeting_id(FakeAppointment(global_id="", entry_id=""), "Demo", None)
    assert no_start == hashlib.md5(b"Demo|").hexdigest()


def test_day_bounds_and_filters():
    start, end = outlook_service._day_bounds(DATE)
    assert start == DAY_START
    assert end == DAY_END
    filters = outlook_service._day_filters(start, end)
    assert len(filters) == 4
    assert "[Start] >= '09/16/2026 00:00' AND [Start] <= '09/16/2026 23:59'" in filters
    assert "[Start] <= '16/09/2026 23:59' AND [End] >= '16/09/2026 00:00'" in filters
    with pytest.raises(ValueError):
        outlook_service._day_bounds("not-a-date")


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (None, None, False),
        (dt(9, day=17), dt(10, day=17), False),
        (dt(9), dt(10), True),
        (dt(23, day=15), dt(1), True),
        (dt(23, day=15), dt(0), False),
        (dt(23, day=15), None, False),
    ],
)
def test_overlaps_day(start, end, expected):
    assert outlook_service._overlaps_day(start, end, DAY_START, DAY_END) is expected


# ---------------------------------------------------------------------------
# Appointment -> meeting conversion
# ---------------------------------------------------------------------------

def test_regular_meeting_shape():
    assert convert(subject=" Stand-up ", start=dt(9), end=dt(9, 30), duration=30) == {
        "subject": "Stand-up", "duration": 30, "duration_label": "30", "is_allday": False,
        "meeting_id": "GID-1", "start": "09:00", "end": "09:30",
    }


def test_allday_meeting_uses_daily_minutes():
    meeting = convert(subject="Ferie", start=DAY_START, end=dt(0, day=17), duration=1440, all_day=True)
    assert meeting["is_allday"] is True
    assert meeting["duration"] == ALLDAY_MINUTES
    assert meeting["duration_label"] == "Tutto il giorno"
    assert meeting["start"] == "00:00"


@pytest.mark.parametrize("subject", ["Annullato: Demo", "Canceled: sync", "CANCELLED - review"])
def test_cancelled_markers_are_skipped(subject):
    assert convert(subject=subject) is None


def test_short_meeting_is_skipped():
    assert convert(start=dt(9), end=dt(9, 3), duration=3) is None


def test_missing_end_is_computed_from_duration():
    meeting = convert(start=dt(10), end=None, duration=45)
    assert meeting["end"] == "10:45"


def test_outside_day_is_skipped():
    assert convert(start=dt(9, day=17), end=dt(10, day=17)) is None


def test_overnight_meeting_from_previous_day_is_kept():
    meeting = convert(start=dt(23, day=15), end=dt(1), duration=120)
    assert meeting is not None
    assert meeting["start"] == "23:00"
    assert meeting["end"] == "01:00"


def test_empty_subject_gets_placeholder_and_com_like_dates_are_read():
    com_start = SimpleNamespace(year=2026, month=9, day=16, hour=11, minute=0, second=0)
    com_end = SimpleNamespace(year=2026, month=9, day=16, hour=11, minute=30, second=0)
    meeting = convert(subject="  ", start=com_start, end=com_end)
    assert meeting["subject"] == outlook_service.NO_SUBJECT
    assert meeting["start"] == "11:00"
    assert meeting["end"] == "11:30"


def test_unreadable_start_means_skipped():
    appt = BrokenStartAppointment()
    assert outlook_service._appointment_to_meeting(appt, DAY_START, DAY_END, ALLDAY_MINUTES) is None


# ---------------------------------------------------------------------------
# Collecting from Items.Restrict
# ---------------------------------------------------------------------------

def test_collect_meetings_dedupes_and_skips_rejected_filters():
    first = FakeAppointment(global_id="A", start=dt(9), end=dt(9, 30))
    second = FakeAppointment(global_id="B", start=dt(11), end=dt(12), duration=60)
    items = FakeItems([first, second], reject_filters=("[End]",))
    filters = outlook_service._day_filters(DAY_START, DAY_END)
    meetings = outlook_service._collect_meetings(items, filters, DAY_START, DAY_END, ALLDAY_MINUTES)
    assert [m["meeting_id"] for m in meetings] == ["A", "B"]
    assert len(items.filters) == 4


def test_collect_meetings_skips_unreadable_appointments(caplog):
    broken = FakeAppointment(global_id="X", duration="not-a-number")
    good = FakeAppointment(global_id="Y")
    items = FakeItems([broken, good])
    with caplog.at_level("WARNING"):
        meetings = outlook_service._collect_meetings(items, ["f"], DAY_START, DAY_END, ALLDAY_MINUTES)
    assert [m["meeting_id"] for m in meetings] == ["Y"]
    assert "Skipping unreadable Outlook appointment" in caplog.text


# ---------------------------------------------------------------------------
# Stores & calendar folder
# ---------------------------------------------------------------------------

def test_find_store_matches_account_email_case_insensitively():
    wanted = FakeStore("Mailbox - Mario.Rossi@Contoso.com")
    namespace = FakeNamespace([FakeStore("Archivio"), wanted], default_store=FakeStore("Default"))
    assert outlook_service._find_store(namespace, "mario.rossi@contoso.com") is wanted


def test_find_store_falls_back_to_default_store():
    default = FakeStore("Default")
    namespace = FakeNamespace([FakeStore("Other")], default_store=default)
    assert outlook_service._find_store(namespace, "nobody@example.com") is default
    assert outlook_service._find_store(namespace, "") is default


def test_open_calendar_errors():
    assert outlook_service._open_calendar(FakeNamespace([]), "") == (None, "Nessun account Outlook trovato.")
    locked = FakeNamespace([], default_store=FakeStore("Default", calendar_error="locked"))
    assert outlook_service._open_calendar(locked, "") == (None, "Calendario Outlook non accessibile.")


def test_open_calendar_success():
    items = FakeItems([])
    namespace = FakeNamespace([], default_store=FakeStore("Default", items))
    calendar, error = outlook_service._open_calendar(namespace, "")
    assert error is None
    assert calendar.Items is items


# ---------------------------------------------------------------------------
# get_outlook_meetings
# ---------------------------------------------------------------------------

def test_get_outlook_meetings_when_pywin32_missing(monkeypatch):
    monkeypatch.setattr(outlook_service, "OUTLOOK_AVAILABLE", False)
    assert outlook_service.get_outlook_meetings(DATE) == (None, UNAVAILABLE_MESSAGE)


def test_get_outlook_meetings_invalid_date(monkeypatch):
    install_outlook(monkeypatch, FakeNamespace([]))
    assert outlook_service.get_outlook_meetings("garbage") == (None, "Data non valida.")
    assert outlook_service.get_outlook_meetings(None) == (None, "Data non valida.")


def test_get_outlook_meetings_success_sorted_and_allday_from_settings(monkeypatch):
    settings_service.update_settings({"general": {"daily_hours": 7.5}})
    late = FakeAppointment(global_id="L", subject="Retro", start=dt(15), end=dt(16), duration=60)
    early = FakeAppointment(global_id="E", subject="Stand-up", start=dt(9), end=dt(9, 30))
    allday = FakeAppointment(global_id="F", subject="Ferie", start=DAY_START, end=dt(0, day=17),
                             duration=1440, all_day=True)
    items = FakeItems([late, early, allday])
    install_outlook(monkeypatch, FakeNamespace([FakeStore("Mailbox - user@contoso.com", items)]))

    meetings, error = outlook_service.get_outlook_meetings(DATE, account_email="user@contoso.com")

    assert error is None
    assert [m["subject"] for m in meetings] == ["Ferie", "Stand-up", "Retro"]
    assert meetings[0]["duration"] == 450
    assert meetings[1]["meeting_id"] == "E"
    assert items.sorted_by == "[Start]"
    assert items.IncludeRecurrences is True


def test_get_outlook_meetings_filters_other_days(monkeypatch):
    other_day = FakeAppointment(global_id="O", start=dt(9, day=17), end=dt(10, day=17))
    items = FakeItems([other_day, FakeAppointment(global_id="T")])
    install_outlook(monkeypatch, FakeNamespace([], default_store=FakeStore("Default", items)))
    meetings, error = outlook_service.get_outlook_meetings(DATE)
    assert error is None
    assert [m["meeting_id"] for m in meetings] == ["T"]


def test_get_outlook_meetings_without_store(monkeypatch):
    install_outlook(monkeypatch, FakeNamespace([]))
    assert outlook_service.get_outlook_meetings(DATE) == (None, "Nessun account Outlook trovato.")


def test_get_outlook_meetings_calendar_not_accessible(monkeypatch):
    namespace = FakeNamespace([], default_store=FakeStore("Default", calendar_error="denied"))
    install_outlook(monkeypatch, namespace)
    assert outlook_service.get_outlook_meetings(DATE) == (None, "Calendario Outlook non accessibile.")


def test_get_outlook_meetings_com_error_is_reported(monkeypatch):
    install_outlook(monkeypatch, dispatch_error=RuntimeError("RPC server unavailable"))
    meetings, error = outlook_service.get_outlook_meetings(DATE)
    assert meetings is None
    assert error == "Errore Outlook: RPC server unavailable"

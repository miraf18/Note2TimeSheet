"""Unit tests for services.state_service (daily history, entries, dedup, elaboration, migrations)."""

from __future__ import annotations

import json
import os
import re

import pytest

import config
from services import settings_service, state_service

TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _write_day(data_dir, date: str, payload: dict) -> str:
    path = os.path.join(config.history_dir(), f"{date}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def _read_day(date: str) -> dict:
    with open(os.path.join(config.history_dir(), f"{date}.json"), encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Dates & fresh state
# ---------------------------------------------------------------------------

class TestResolveDate:
    def test_valid_date_is_kept(self):
        assert state_service.resolve_date("2026-01-05") == "2026-01-05"

    @pytest.mark.parametrize("value", [None, "", "2026-13-01", "2026/01/05", "20260105", 123, "not a date"])
    def test_invalid_dates_fall_back_to_today(self, value):
        assert state_service.resolve_date(value) == config.today_iso()

    def test_whitespace_is_stripped(self):
        assert state_service.resolve_date(" 2026-01-05 ") == "2026-01-05"


class TestLoadState:
    def test_fresh_state_when_no_file(self):
        state = state_service.load_state("2026-03-01")
        assert state == {
            "date": "2026-03-01",
            "entries": [],
            "imported_source_ids": [],
            "elaborated": None,
            "elaborated_at": None,
            "elaborated_edited_at": None,
        }

    def test_default_date_is_today(self):
        assert state_service.load_state()["date"] == config.today_iso()

    def test_corrupt_file_gives_fresh_state(self, data_dir):
        path = os.path.join(config.history_dir(), "2026-03-02.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        assert state_service.load_state("2026-03-02")["entries"] == []

    def test_load_does_not_mutate_source_data(self, data_dir):
        _write_day(data_dir, "2026-03-03", {"entries": [{"id": "a", "type": "outlook", "text": "x"}]})
        first = state_service.load_state("2026-03-03")
        first["entries"].append({"id": "junk"})
        assert len(state_service.load_state("2026-03-03")["entries"]) == 1


# ---------------------------------------------------------------------------
# Entries CRUD
# ---------------------------------------------------------------------------

class TestAddEntry:
    def test_manual_entry_shape_and_persistence(self):
        entry = state_service.add_entry("  Scritto codice  ", date="2026-04-01")
        assert entry["type"] == "manual"
        assert entry["text"] == "Scritto codice"
        assert entry["duration_min"] is None
        assert entry["source_id"] is None
        assert entry["meta"] == {}
        assert TIME_RE.match(entry["time"])
        assert len(entry["id"]) == 36
        assert state_service.get_entries("2026-04-01") == [entry]
        assert _read_day("2026-04-01")["entries"][0]["id"] == entry["id"]

    def test_meeting_entry_with_meta_and_source(self):
        meta = {"provider": "graph", "start": "09:00", "end": "09:30", "is_allday": False}
        entry = state_service.add_entry("Stand-up", entry_type="meeting", duration_min=30,
                                        source_id="meeting:abc", meta=meta, date="2026-04-01")
        assert entry["type"] == "meeting"
        assert entry["duration_min"] == 30
        assert entry["source_id"] == "meeting:abc"
        assert entry["meta"] == meta
        meta["start"] = "10:00"  # caller mutation must not leak into stored entry
        assert state_service.get_entries("2026-04-01")[0]["meta"]["start"] == "09:00"

    def test_legacy_outlook_type_maps_to_meeting(self):
        entry = state_service.add_entry("Call", entry_type="outlook", date="2026-04-01")
        assert entry["type"] == "meeting"

    @pytest.mark.parametrize("text", ["", "   ", None, 42])
    def test_empty_text_rejected(self, text):
        with pytest.raises(ValueError):
            state_service.add_entry(text, date="2026-04-01")

    def test_unknown_type_rejected(self):
        with pytest.raises(ValueError):
            state_service.add_entry("x", entry_type="jira", date="2026-04-01")

    @pytest.mark.parametrize("duration", ["abc", -5, True])
    def test_invalid_duration_rejected(self, duration):
        with pytest.raises(ValueError):
            state_service.add_entry("x", entry_type="meeting", duration_min=duration, date="2026-04-01")

    def test_invalid_meta_rejected(self):
        with pytest.raises(ValueError):
            state_service.add_entry("x", meta="nope", date="2026-04-01")

    def test_invalid_date_goes_to_today(self):
        state_service.add_entry("x", date="bad-date")
        assert len(state_service.get_entries(config.today_iso())) == 1


class TestUpdateRemoveFind:
    def test_update_only_provided_fields(self):
        entry = state_service.add_entry("Vecchio", entry_type="meeting", duration_min=30,
                                        meta={"a": 1}, date="2026-04-02")
        updated = state_service.update_entry(entry["id"], text=" Nuovo ", date="2026-04-02")
        assert updated["text"] == "Nuovo"
        assert updated["duration_min"] == 30
        assert updated["meta"] == {"a": 1}
        assert updated["id"] == entry["id"]
        stored = state_service.get_entries("2026-04-02")[0]
        assert stored == updated

    def test_update_meta_and_duration(self):
        entry = state_service.add_entry("x", date="2026-04-02")
        updated = state_service.update_entry(entry["id"], meta={"kind": "commits"}, duration_min=45, date="2026-04-02")
        assert updated["text"] == "x"
        assert updated["meta"] == {"kind": "commits"}
        assert updated["duration_min"] == 45

    def test_update_unknown_id_returns_none(self):
        assert state_service.update_entry("missing", text="x", date="2026-04-02") is None

    def test_update_with_empty_text_rejected(self):
        entry = state_service.add_entry("x", date="2026-04-02")
        with pytest.raises(ValueError):
            state_service.update_entry(entry["id"], text="  ", date="2026-04-02")

    def test_remove_entry(self):
        entry = state_service.add_entry("x", date="2026-04-02")
        state_service.add_entry("y", date="2026-04-02")
        assert state_service.remove_entry(entry["id"], date="2026-04-02") is True
        assert [e["text"] for e in state_service.get_entries("2026-04-02")] == ["y"]
        assert state_service.remove_entry(entry["id"], date="2026-04-02") is False

    def test_find_entry(self):
        state_service.add_entry("a", date="2026-04-02")
        target = state_service.add_entry("repo", entry_type="github", meta={"repo": "o/r"}, date="2026-04-02")
        found = state_service.find_entry(lambda e: e["meta"].get("repo") == "o/r", date="2026-04-02")
        assert found["id"] == target["id"]
        assert state_service.find_entry(lambda e: False, date="2026-04-02") is None


# ---------------------------------------------------------------------------
# Imported source ids
# ---------------------------------------------------------------------------

class TestImportedSources:
    def test_mark_accepts_iterables_and_dedupes(self):
        date = "2026-04-03"
        state_service.mark_sources_imported(iter(["github:commit:a", "github:commit:b", "github:commit:a"]), date=date)
        state_service.mark_sources_imported(("github:commit:b", "meeting:m1"), date=date)
        state_service.mark_sources_imported({"meeting:m1"}, date=date)
        ids = state_service.load_state(date)["imported_source_ids"]
        assert ids == ["github:commit:a", "github:commit:b", "meeting:m1"]
        assert state_service.is_source_imported("meeting:m1", date=date)
        assert not state_service.is_source_imported("meeting:m2", date=date)

    def test_bare_string_counts_as_single_id(self):
        state_service.mark_sources_imported("meeting:solo", date="2026-04-03")
        assert state_service.load_state("2026-04-03")["imported_source_ids"] == ["meeting:solo"]

    def test_empty_and_invalid_ids_ignored(self, data_dir):
        state_service.mark_sources_imported(["", None, "  ", 5], date="2026-04-03")
        assert not os.path.exists(os.path.join(config.history_dir(), "2026-04-03.json"))


# ---------------------------------------------------------------------------
# Elaboration
# ---------------------------------------------------------------------------

class TestElaboration:
    RESULT = {"timesheet": [{"pratica": "1", "ore": 8.0, "descrizione": "Lavoro."}], "totale_ore": 8.0, "note": ""}

    def test_set_and_get(self):
        state_service.set_elaboration(self.RESULT, date="2026-04-04")
        view = state_service.get_elaboration("2026-04-04")
        assert view["result"] == self.RESULT
        assert view["elaborated_at"] and "T" in view["elaborated_at"]
        assert view["elaborated_edited_at"] is None

    def test_get_when_missing(self):
        assert state_service.get_elaboration("2026-04-05") == {
            "result": None, "elaborated_at": None, "elaborated_edited_at": None}

    def test_update_sets_edited_at_and_recomputes_total(self):
        state_service.set_elaboration(self.RESULT, date="2026-04-04")
        before = state_service.get_elaboration("2026-04-04")["elaborated_at"]
        edited = {"timesheet": [{"pratica": "1", "ore": 3, "descrizione": "A."},
                                {"pratica": "2", "ore": 4.5, "descrizione": "B."}], "note": "n"}
        view = state_service.update_elaboration(edited, date="2026-04-04")
        assert view["elaborated_at"] == before
        assert view["elaborated_edited_at"] is not None
        assert view["result"]["totale_ore"] == 7.5
        assert view["result"]["note"] == "n"

    def test_update_without_previous_elaboration_sets_both_timestamps(self):
        view = state_service.update_elaboration(self.RESULT, date="2026-04-06")
        assert view["elaborated_at"] == view["elaborated_edited_at"]

    def test_new_elaboration_resets_edited_at(self):
        state_service.update_elaboration(self.RESULT, date="2026-04-04")
        state_service.set_elaboration(self.RESULT, date="2026-04-04")
        assert state_service.get_elaboration("2026-04-04")["elaborated_edited_at"] is None

    def test_invalid_result_rejected(self):
        with pytest.raises(ValueError):
            state_service.set_elaboration("nope", date="2026-04-04")


# ---------------------------------------------------------------------------
# History listing & pruning
# ---------------------------------------------------------------------------

class TestListDays:
    def test_today_always_present_and_newest_first(self):
        state_service.add_entry("a", date="2020-01-01")
        state_service.add_entry("b", date="2021-06-15")
        state_service.add_entry("c", date="2021-06-15")
        state_service.set_elaboration({"timesheet": []}, date="2020-01-01")
        days = state_service.list_days()
        dates = [d["date"] for d in days]
        assert dates[0] == config.today_iso()
        assert dates == sorted(dates, reverse=True)
        by_date = {d["date"]: d for d in days}
        assert by_date["2021-06-15"] == {"date": "2021-06-15", "entry_count": 2, "elaborated": False}
        assert by_date["2020-01-01"]["elaborated"] is True
        assert by_date[config.today_iso()]["entry_count"] == 0

    def test_ignores_non_day_files(self, data_dir):
        with open(os.path.join(config.history_dir(), "notes.json"), "w") as handle:
            handle.write("{}")
        assert [d["date"] for d in state_service.list_days()] == [config.today_iso()]


class TestPruning:
    def test_keeps_newest_n_days_from_settings(self, data_dir):
        settings_service.update_settings({"general": {"history_keep_days": 3}})
        for day in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"):
            _write_day(data_dir, day, {"entries": []})
        state_service.add_entry("x", date="2026-01-05")
        remaining = sorted(n for n in os.listdir(config.history_dir()) if n.endswith(".json"))
        assert remaining == ["2026-01-03.json", "2026-01-04.json", "2026-01-05.json"]

    def test_default_keep_days_is_30(self, data_dir):
        for i in range(1, 32):
            _write_day(data_dir, f"2026-01-{i:02d}", {"entries": []})
        state_service.add_entry("x", date="2026-02-01")
        remaining = [n for n in os.listdir(config.history_dir()) if n.endswith(".json")]
        assert len(remaining) == 30
        assert "2026-01-01.json" not in remaining
        assert "2026-01-02.json" not in remaining


# ---------------------------------------------------------------------------
# Legacy migrations
# ---------------------------------------------------------------------------

class TestLegacyNormalisation:
    def test_outlook_entries_and_meeting_ids(self, data_dir):
        _write_day(data_dir, "2026-05-01", {
            "date": "2026-05-01",
            "entries": [
                {"id": "e1", "type": "outlook", "text": "Call", "time": "10:00", "duration_min": 30, "meeting_id": "abc"},
                {"id": "e2", "type": "outlook", "text": "Call2", "time": "11:00", "duration_min": 15,
                 "meeting_id": "meeting:def"},
                {"id": "e3", "type": "manual", "text": "Nota", "time": "12:00", "duration_min": None, "meeting_id": None},
            ],
            "imported_meeting_ids": ["abc", "meeting:def"],
            "elaborated": None,
            "elaborated_at": None,
        })
        state = state_service.load_state("2026-05-01")
        e1, e2, e3 = state["entries"]
        assert e1["type"] == "meeting" and e1["source_id"] == "meeting:abc" and e1["meta"] == {}
        assert e2["source_id"] == "meeting:def"
        assert e3["type"] == "manual" and e3["source_id"] is None
        assert all("meeting_id" not in e for e in state["entries"])
        assert state["imported_source_ids"] == ["meeting:abc", "meeting:def"]
        assert "imported_meeting_ids" not in state
        assert state["elaborated_edited_at"] is None
        assert state_service.is_source_imported("meeting:abc", date="2026-05-01")

    def test_existing_source_id_wins_over_meeting_id(self, data_dir):
        _write_day(data_dir, "2026-05-02", {"entries": [
            {"id": "e1", "type": "meeting", "text": "x", "source_id": "meeting:keep", "meeting_id": "other"}]})
        assert state_service.load_state("2026-05-02")["entries"][0]["source_id"] == "meeting:keep"

    def test_missing_fields_get_defaults(self, data_dir):
        _write_day(data_dir, "2026-05-03", {"entries": [{"text": "solo testo"}]})
        entry = state_service.load_state("2026-05-03")["entries"][0]
        assert entry["type"] == "manual"
        assert entry["time"] == "00:00"
        assert entry["meta"] == {}
        assert len(entry["id"]) == 36

    def test_v1_activities_format(self, data_dir):
        _write_day(data_dir, "2026-05-04", {
            "date": "2026-05-04",
            "activities": [
                {"type": "manual", "text": "Sviluppo", "time": "09:00"},
                {"type": "outlook", "time": "10:00", "meetings": [
                    {"subject": "Riunione A", "duration": 30}, {"subject": "Riunione B", "duration": 60}]},
            ],
        })
        entries = state_service.load_state("2026-05-04")["entries"]
        assert [(e["type"], e["text"], e["duration_min"]) for e in entries] == [
            ("manual", "Sviluppo", None), ("meeting", "Riunione A", 30), ("meeting", "Riunione B", 60)]
        assert entries[1]["time"] == "10:00"

    def test_saving_rewrites_file_in_v2_shape(self, data_dir):
        _write_day(data_dir, "2026-05-05", {"entries": [
            {"id": "e1", "type": "outlook", "text": "x", "meeting_id": "m"}], "imported_meeting_ids": ["m"]})
        state_service.add_entry("nuova", date="2026-05-05")
        raw = _read_day("2026-05-05")
        assert raw["entries"][0]["type"] == "meeting"
        assert raw["entries"][0]["source_id"] == "meeting:m"
        assert raw["imported_source_ids"] == ["meeting:m"]
        assert "imported_meeting_ids" not in raw


class TestLegacySingleFile:
    def test_legacy_state_file_is_migrated_and_removed(self, data_dir):
        legacy = config.legacy_state_file()
        with open(legacy, "w", encoding="utf-8") as handle:
            json.dump({"date": "2026-06-01", "entries": [
                {"id": "e1", "type": "outlook", "text": "Old", "meeting_id": "m1"}],
                "imported_meeting_ids": ["m1"]}, handle)
        state = state_service.load_state("2026-06-01")
        assert state["entries"][0]["type"] == "meeting"
        assert state["imported_source_ids"] == ["meeting:m1"]
        assert not os.path.exists(legacy)
        assert os.path.exists(os.path.join(config.history_dir(), "2026-06-01.json"))

    def test_legacy_file_does_not_overwrite_existing_day(self, data_dir):
        _write_day(data_dir, "2026-06-02", {"entries": [{"id": "keep", "type": "manual", "text": "keep"}]})
        with open(config.legacy_state_file(), "w", encoding="utf-8") as handle:
            json.dump({"date": "2026-06-02", "entries": [{"id": "old", "type": "manual", "text": "old"}]}, handle)
        assert [e["id"] for e in state_service.get_entries("2026-06-02")] == ["keep"]
        assert not os.path.exists(config.legacy_state_file())

    def test_corrupt_legacy_file_is_kept_as_backup(self, data_dir):
        legacy = config.legacy_state_file()
        with open(legacy, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        state_service.list_days()
        assert not os.path.exists(legacy)
        assert os.path.exists(legacy + ".corrupt")

    def test_legacy_file_without_date_goes_to_today(self, data_dir):
        with open(config.legacy_state_file(), "w", encoding="utf-8") as handle:
            json.dump({"entries": [{"id": "x", "type": "manual", "text": "hello"}]}, handle)
        assert state_service.get_entries()[0]["text"] == "hello"

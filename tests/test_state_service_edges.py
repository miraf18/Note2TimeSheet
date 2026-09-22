"""Edge cases of ``services.state_service``: odd inputs, unreadable files, failing OS calls."""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

import config
from services import settings_service, state_service

DAY = "2026-03-10"


def _write_day(date: str, payload) -> str:
    path = os.path.join(config.history_dir(), f"{date}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def _raise_os_error(*args, **kwargs):
    raise OSError("simulated failure")


# ---------------------------------------------------------------------------
# resolve_date / load_state / save_state
# ---------------------------------------------------------------------------

def test_resolve_date_accepts_date_and_datetime_objects():
    assert state_service.resolve_date(dt.date(2026, 1, 5)) == "2026-01-05"
    assert state_service.resolve_date(dt.datetime(2026, 1, 5, 10, 30)) == "2026-01-05"


def test_unknown_entry_type_in_file_is_treated_as_manual(caplog):
    _write_day(DAY, {"entries": [{"id": "a", "type": "alien", "text": "x"}]})
    with caplog.at_level("WARNING"):
        entries = state_service.get_entries(DAY)
    assert entries[0]["type"] == "manual"
    assert "Unknown entry type" in caplog.text


def test_day_file_with_non_object_content_starts_fresh(caplog):
    _write_day(DAY, [1, 2, 3])
    with caplog.at_level("WARNING"):
        state = state_service.load_state(DAY)
    assert state["entries"] == []
    assert "unexpected content" in caplog.text


def test_save_state_rejects_non_dict():
    with pytest.raises(ValueError):
        state_service.save_state("not a state")


# ---------------------------------------------------------------------------
# Entries: invalid arguments
# ---------------------------------------------------------------------------

def test_add_entry_rejects_non_string_source_id():
    with pytest.raises(ValueError):
        state_service.add_entry("x", source_id=123, date=DAY)


def test_update_entry_rejects_non_dict_meta():
    entry = state_service.add_entry("x", date=DAY)
    with pytest.raises(ValueError):
        state_service.update_entry(entry["id"], meta="nope", date=DAY)


def test_update_entry_rejects_negative_duration():
    entry = state_service.add_entry("x", date=DAY)
    with pytest.raises(ValueError):
        state_service.update_entry(entry["id"], duration_min=-5, date=DAY)


# ---------------------------------------------------------------------------
# Retention & directory errors
# ---------------------------------------------------------------------------

def test_keep_days_invalid_setting_falls_back_to_default(monkeypatch, caplog):
    monkeypatch.setattr(settings_service, "get", lambda path, default=None: "many")
    with caplog.at_level("WARNING"):
        assert state_service._keep_days() == config.DEFAULT_HISTORY_KEEP_DAYS
    assert "Invalid history_keep_days" in caplog.text


def test_stored_dates_survives_listdir_error(monkeypatch, caplog):
    monkeypatch.setattr(state_service.os, "listdir", _raise_os_error)
    with caplog.at_level("WARNING"):
        assert state_service._stored_dates() == []
    assert "Cannot list history dir" in caplog.text


def test_prune_survives_remove_error(monkeypatch, caplog):
    settings_service.update_settings({"general": {"history_keep_days": 1}})
    for date in ("2026-03-01", "2026-03-02"):
        _write_day(date, {"entries": []})
    monkeypatch.setattr(state_service.os, "remove", _raise_os_error)
    with caplog.at_level("WARNING"):
        state_service.add_entry("keep going", date="2026-03-03")
    assert "Cannot prune history file" in caplog.text
    assert len(state_service.get_entries("2026-03-03")) == 1


# ---------------------------------------------------------------------------
# Legacy single-file migration failures
# ---------------------------------------------------------------------------

def test_corrupt_legacy_file_rename_failure_is_logged(monkeypatch, caplog):
    path = config.legacy_state_file()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{broken")
    monkeypatch.setattr(state_service.os, "replace", _raise_os_error)
    with caplog.at_level("WARNING"):
        state = state_service.load_state(DAY)
    assert state["entries"] == []
    assert "cannot be moved" in caplog.text
    assert os.path.exists(path)


def test_legacy_file_remove_failure_is_logged(monkeypatch, caplog):
    config.atomic_write_json(config.legacy_state_file(), {"date": DAY, "entries": [{"text": "legacy"}]})
    monkeypatch.setattr(state_service.os, "remove", _raise_os_error)
    with caplog.at_level("WARNING"):
        entries = state_service.get_entries(DAY)
    assert [e["text"] for e in entries] == ["legacy"]
    assert "Cannot remove legacy state file" in caplog.text


# ---------------------------------------------------------------------------
# Elaboration helpers
# ---------------------------------------------------------------------------

def test_with_total_leaves_non_numeric_results_untouched():
    assert state_service._with_total({"timesheet": "x"}) == {"timesheet": "x"}
    odd = {"timesheet": [{"ore": "abc"}], "totale_ore": 9}
    assert state_service._with_total(odd) == odd
    assert state_service._with_total({"timesheet": [{"pratica": "1"}]}) == {"timesheet": [{"pratica": "1"}]}


def test_with_total_recomputes_and_does_not_mutate_input():
    source = {"timesheet": [{"ore": 1.5}, {"ore": "2,5".replace(",", ".")}], "totale_ore": 0}
    result = state_service._with_total(source)
    assert result["totale_ore"] == 4.0
    assert source["totale_ore"] == 0

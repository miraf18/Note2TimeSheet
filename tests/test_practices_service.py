"""Unit tests for services.practices_service."""

from __future__ import annotations

import json
import logging
import os

import pytest

import config
from services import practices_service
from services.practices_service import PracticeNotFoundError


def _write_practices(payload) -> None:
    with open(config.practices_file(), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _read_practices():
    with open(config.practices_file(), encoding="utf-8") as handle:
        return json.load(handle)


SAMPLE = [
    {"code": "100", "name": "Analisi", "description": "Analisi requisiti"},
    {"code": "200", "name": "Sviluppo", "description": ""},
]


class TestFirstRun:
    def test_default_file_is_copied_on_first_load(self):
        assert not os.path.exists(config.practices_file())
        practices = practices_service.load_practices()
        assert os.path.exists(config.practices_file())
        with open(config.default_practices_file(), encoding="utf-8") as handle:
            defaults = json.load(handle)
        assert [p["code"] for p in practices] == [p["code"] for p in defaults]
        assert len(practices) >= 1

    def test_existing_file_is_not_overwritten(self):
        _write_practices(SAMPLE)
        assert practices_service.load_practices() == SAMPLE

    def test_missing_default_file_gives_empty_list(self, monkeypatch, caplog):
        monkeypatch.setattr(config, "default_practices_file", lambda: str(config.data_dir()) + "/nope.json")
        with caplog.at_level(logging.WARNING):
            assert practices_service.load_practices() == []
        assert "not found" in caplog.text


class TestLoad:
    def test_malformed_items_are_skipped(self, caplog):
        _write_practices([
            {"code": " 100 ", "name": " Analisi ", "description": None},
            {"code": "", "name": "no code"},
            {"name": "no code either"},
            {"code": "300", "name": ""},
            "not an object",
            {"code": "100", "name": "duplicate"},
            {"code": "400", "name": "Ok", "description": "d"},
        ])
        with caplog.at_level(logging.WARNING):
            practices = practices_service.load_practices()
        assert practices == [
            {"code": "100", "name": "Analisi", "description": ""},
            {"code": "400", "name": "Ok", "description": "d"},
        ]
        assert "duplicate" in caplog.text

    def test_non_list_content_gives_empty(self):
        _write_practices({"code": "x"})
        assert practices_service.load_practices() == []

    def test_corrupt_file_gives_empty(self):
        with open(config.practices_file(), "w", encoding="utf-8") as handle:
            handle.write("[[[")
        assert practices_service.load_practices() == []

    def test_load_returns_independent_copies(self):
        _write_practices(SAMPLE)
        first = practices_service.load_practices()
        first[0]["name"] = "mutated"
        first.append({"code": "x", "name": "y"})
        assert practices_service.load_practices() == SAMPLE

    def test_find_practice(self):
        _write_practices(SAMPLE)
        assert practices_service.find_practice("200") == SAMPLE[1]
        assert practices_service.find_practice(" 100 ") == SAMPLE[0]
        assert practices_service.find_practice("999") is None
        assert practices_service.find_practice("100", practices=[]) is None


class TestAdd:
    def test_add_trims_and_persists(self):
        _write_practices(SAMPLE)
        result = practices_service.add_practice("  300 ", "  Test  ", "  desc  ")
        assert result[-1] == {"code": "300", "name": "Test", "description": "desc"}
        assert len(result) == 3
        assert _read_practices() == result

    def test_add_without_description(self):
        _write_practices([])
        result = practices_service.add_practice("1", "Uno")
        assert result == [{"code": "1", "name": "Uno", "description": ""}]

    def test_duplicate_code_rejected(self):
        _write_practices(SAMPLE)
        with pytest.raises(ValueError, match="già esistente"):
            practices_service.add_practice("100", "Altro")
        assert _read_practices() == SAMPLE

    @pytest.mark.parametrize("code,name,description", [
        ("", "Nome", ""),
        ("   ", "Nome", ""),
        (None, "Nome", ""),
        ("1", "", ""),
        ("1", "   ", ""),
        ("1", None, ""),
        ("c" * 33, "Nome", ""),
        ("1", "n" * 101, ""),
        ("1", "Nome", "d" * 2001),
        ("1", "Nome", 42),
        ("a\nb", "Nome", ""),
    ])
    def test_invalid_input_rejected(self, code, name, description):
        _write_practices([])
        with pytest.raises(ValueError):
            practices_service.add_practice(code, name, description)
        assert _read_practices() == []

    def test_does_not_mutate_loaded_list(self):
        _write_practices(SAMPLE)
        before = practices_service.load_practices()
        practices_service.add_practice("300", "Nuova")
        assert before == SAMPLE


class TestUpdate:
    def test_update_only_provided_fields(self):
        _write_practices(SAMPLE)
        result = practices_service.update_practice("100", name=" Nuovo nome ")
        assert result[0] == {"code": "100", "name": "Nuovo nome", "description": "Analisi requisiti"}
        assert result[1] == SAMPLE[1]
        result = practices_service.update_practice("100", description="Nuova desc")
        assert result[0] == {"code": "100", "name": "Nuovo nome", "description": "Nuova desc"}
        assert _read_practices() == result

    def test_description_can_be_cleared(self):
        _write_practices(SAMPLE)
        assert practices_service.update_practice("100", description="")[0]["description"] == ""

    def test_missing_code_raises_key_error(self):
        _write_practices(SAMPLE)
        with pytest.raises(KeyError) as excinfo:
            practices_service.update_practice("999", name="x")
        assert isinstance(excinfo.value, PracticeNotFoundError)
        assert str(excinfo.value) == "Pratica 999 non trovata"
        assert excinfo.value.code == "999"

    def test_invalid_name_rejected(self):
        _write_practices(SAMPLE)
        with pytest.raises(ValueError):
            practices_service.update_practice("100", name="   ")
        assert _read_practices() == SAMPLE

    def test_update_with_no_changes_keeps_data(self):
        _write_practices(SAMPLE)
        assert practices_service.update_practice("200") == SAMPLE


class TestDelete:
    def test_delete_removes_and_persists(self):
        _write_practices(SAMPLE)
        result = practices_service.delete_practice(" 100 ")
        assert result == [SAMPLE[1]]
        assert _read_practices() == [SAMPLE[1]]

    def test_delete_missing_raises(self):
        _write_practices(SAMPLE)
        with pytest.raises(PracticeNotFoundError):
            practices_service.delete_practice("999")
        assert _read_practices() == SAMPLE


class TestSave:
    def test_save_rejects_non_list(self):
        with pytest.raises(ValueError):
            practices_service.save_practices({"code": "x"})

    def test_save_writes_copies(self):
        practices_service.save_practices(SAMPLE)
        assert _read_practices() == SAMPLE

    def test_reset_cache_is_noop(self):
        assert practices_service.reset_cache() is None

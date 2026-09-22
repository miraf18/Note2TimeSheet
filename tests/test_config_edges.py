"""Edge cases of ``config`` helpers: malformed settings.json, failing writes, unreadable paths."""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

import pytest

import config


def test_settings_timezone_name_ignores_malformed_settings(data_dir):
    config.atomic_write_json(config.settings_file(), ["not", "a", "dict"])
    assert config._settings_timezone_name() is None
    config.atomic_write_json(config.settings_file(), {"general": "x"})
    assert config._settings_timezone_name() is None
    config.atomic_write_json(config.settings_file(), {"general": {"timezone": "   "}})
    assert config._settings_timezone_name() is None
    assert config.local_tz() == ZoneInfo(config.DEFAULT_TIMEZONE)


def test_settings_timezone_name_reads_valid_value(data_dir):
    config.atomic_write_json(config.settings_file(), {"general": {"timezone": " Europe/London "}})
    assert config._settings_timezone_name() == "Europe/London"
    assert config.local_tz() == ZoneInfo("Europe/London")


def test_atomic_write_json_cleans_up_temp_file_on_failure(data_dir):
    target = os.path.join(str(data_dir), "out.json")
    with pytest.raises(TypeError):
        config.atomic_write_json(target, {"x": object()})
    assert not os.path.exists(target)
    assert [name for name in os.listdir(str(data_dir)) if name.startswith(".tmp-")] == []


def test_atomic_write_json_creates_missing_directories(data_dir):
    target = os.path.join(str(data_dir), "nested", "deeper", "out.json")
    config.atomic_write_json(target, {"ok": True})
    assert config.read_json(target, None) == {"ok": True}


def test_read_json_returns_default_on_os_error(data_dir, caplog):
    directory = os.path.join(str(data_dir), "a-directory")
    os.makedirs(directory)
    with caplog.at_level("WARNING"):
        assert config.read_json(directory, {"default": 1}) == {"default": 1}
    assert "Cannot read" in caplog.text


def test_env_int_garbage_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("APP_PORT", "not-a-port")
    with caplog.at_level("WARNING"):
        assert config.app_port() == config.DEFAULT_PORT
    assert "Invalid integer" in caplog.text
    monkeypatch.setenv("APP_PORT", "   ")
    assert config.app_port() == config.DEFAULT_PORT


def test_is_valid_timezone_rejects_non_strings():
    assert config.is_valid_timezone(None) is False
    assert config.is_valid_timezone(42) is False
    assert config.is_valid_timezone("") is False
    assert config.is_valid_timezone("Europe/Rome") is True

"""Unit tests for config helpers and services.settings_service (settings + secrets)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from zoneinfo import ZoneInfo

import pytest

import config
from services import settings_service
from services.settings_service import SettingsValidationError


def _read(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------

class TestConfigPaths:
    def test_data_dir_follows_env_and_is_created(self, data_dir, monkeypatch):
        assert config.data_dir() == os.path.abspath(str(data_dir))
        nested = data_dir / "nested" / "dir"
        monkeypatch.setenv("TIMESHEET_DATA_DIR", str(nested))
        assert config.data_dir() == str(nested)
        assert nested.is_dir()

    def test_derived_paths(self, data_dir):
        base = os.path.abspath(str(data_dir))
        assert config.settings_file() == os.path.join(base, "settings.json")
        assert config.secrets_file() == os.path.join(base, "secrets.json")
        assert config.practices_file() == os.path.join(base, "practices.json")
        assert config.history_dir() == os.path.join(base, ".timesheet_history")
        assert os.path.isdir(config.history_dir())
        assert config.graph_cache_file() == os.path.join(base, ".graph_token_cache.bin")
        assert config.legacy_state_file() == os.path.join(base, ".timesheet_state.json")
        assert config.default_practices_file() == os.path.join(config.BASE_DIR, "practices.json")

    def test_constants(self):
        assert config.APP_NAME == "Note2TimeSheet"
        assert config.APP_VERSION == "2.0.0"
        assert config.DEFAULT_PORT == 5600
        assert config.DEFAULT_TIMEZONE == "Europe/Rome"
        assert config.DEFAULT_MODEL == "gpt-4.1-mini"


class TestConfigEnv:
    @pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("YES", True), ("on", True),
                                              ("0", False), ("false", False), ("No", False), ("off", False)])
    def test_env_flag_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("SOME_FLAG", raw)
        assert config.env_flag("SOME_FLAG", not expected) is expected

    def test_env_flag_default_on_missing_or_garbage(self, monkeypatch):
        monkeypatch.delenv("SOME_FLAG", raising=False)
        assert config.env_flag("SOME_FLAG", True) is True
        monkeypatch.setenv("SOME_FLAG", "maybe")
        assert config.env_flag("SOME_FLAG", False) is False

    def test_app_port_and_browser(self, monkeypatch):
        assert config.app_port() == 5600
        assert config.auto_open_browser() is True
        monkeypatch.setenv("APP_PORT", "7000")
        monkeypatch.setenv("AUTO_OPEN_BROWSER", "false")
        assert config.app_port() == 7000
        assert config.auto_open_browser() is False
        monkeypatch.setenv("APP_PORT", "abc")
        assert config.app_port() == 5600


class TestConfigTime:
    def test_local_tz_default(self):
        assert config.local_tz().key == "Europe/Rome"

    def test_local_tz_explicit_and_invalid(self):
        assert config.local_tz("America/New_York").key == "America/New_York"
        assert config.local_tz("Mars/Olympus").key == "Europe/Rome"

    def test_local_tz_reads_settings_file(self, data_dir):
        config.atomic_write_json(config.settings_file(), {"general": {"timezone": "Asia/Tokyo"}})
        assert config.local_tz().key == "Asia/Tokyo"
        config.atomic_write_json(config.settings_file(), {"general": {"timezone": "Nowhere/Land"}})
        assert config.local_tz().key == "Europe/Rome"

    def test_local_tz_env_seed_between_settings_and_default(self, monkeypatch):
        monkeypatch.setenv("TIMESHEET_TIMEZONE", "Europe/London")
        assert config.local_tz().key == "Europe/London"

    def test_local_tz_falls_back_to_utc(self, monkeypatch):
        monkeypatch.setattr(config, "DEFAULT_TIMEZONE", "Bad/Zone")
        assert config.local_tz("Also/Bad") == dt.timezone.utc

    def test_now_local_and_today(self):
        now = config.now_local()
        assert now.tzinfo is not None
        assert now.utcoffset() == dt.datetime.now(ZoneInfo("Europe/Rome")).utcoffset()
        assert config.today_iso() == now.date().isoformat()

    def test_day_bounds_utc_summer_and_winter(self):
        assert config.day_bounds_utc("2026-07-15") == ("2026-07-14T22:00:00", "2026-07-15T22:00:00")
        assert config.day_bounds_utc("2026-01-15") == ("2026-01-14T23:00:00", "2026-01-15T23:00:00")

    def test_day_bounds_utc_uses_settings_timezone(self, data_dir):
        config.atomic_write_json(config.settings_file(), {"general": {"timezone": "UTC"}})
        assert config.day_bounds_utc("2026-07-15") == ("2026-07-15T00:00:00", "2026-07-16T00:00:00")

    @pytest.mark.parametrize("value", ["2026-7-15", "20260715", "", None, "2026-02-30"])
    def test_day_bounds_utc_rejects_invalid(self, value):
        with pytest.raises(ValueError):
            config.day_bounds_utc(value)

    def test_is_valid_date_iso(self):
        assert config.is_valid_date_iso("2026-02-28")
        assert not config.is_valid_date_iso("2026-02-30")
        assert not config.is_valid_date_iso(20260228)


class TestConfigJson:
    def test_atomic_write_and_read(self, data_dir):
        path = str(data_dir / "sub" / "file.json")
        config.atomic_write_json(path, {"a": "è", "b": [1, 2]})
        assert config.read_json(path, None) == {"a": "è", "b": [1, 2]}
        assert [n for n in os.listdir(data_dir / "sub") if n.startswith(".tmp-")] == []

    def test_atomic_write_replaces_existing(self, data_dir):
        path = str(data_dir / "file.json")
        config.atomic_write_json(path, {"v": 1})
        config.atomic_write_json(path, {"v": 2})
        assert config.read_json(path, None) == {"v": 2}

    def test_read_json_missing_and_corrupt(self, data_dir, caplog):
        assert config.read_json(str(data_dir / "missing.json"), "dflt") == "dflt"
        path = data_dir / "bad.json"
        path.write_text("{oops", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            assert config.read_json(str(path), {"x": 1}) == {"x": 1}
        assert "Corrupt JSON" in caplog.text


# ---------------------------------------------------------------------------
# settings_service: defaults & load
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_defaults_shape(self):
        defaults = settings_service.DEFAULTS
        assert defaults["version"] == 2
        assert defaults["general"] == {"user_name": "User", "daily_hours": 8.0, "timezone": "Europe/Rome",
                                       "history_keep_days": 30, "openai_model": "gpt-4.1-mini"}
        assert defaults["ai"] == {"system_intro": None, "system_rules": None, "system_output": None,
                                  "user_template": None, "temperature": 0.2}
        assert defaults["github"] == {"client_id": "", "repos": [], "include_commits": True,
                                      "include_pull_requests": True, "include_issues": False}
        assert defaults["microsoft"] == {"client_id": "", "tenant_id": "common", "source": "auto"}

    def test_defaults_are_seeded_lazily_from_env(self, monkeypatch):
        monkeypatch.setenv("USER_NAME", " Raffaele ")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
        monkeypatch.setenv("TIMESHEET_TIMEZONE", "Europe/London")
        monkeypatch.setenv("HISTORY_KEEP_DAYS", "12")
        monkeypatch.setenv("GITHUB_CLIENT_ID", "gh123")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "ms123")
        monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-x")
        defaults = settings_service.DEFAULTS
        assert defaults["general"]["user_name"] == "Raffaele"
        assert defaults["general"]["openai_model"] == "gpt-5"
        assert defaults["general"]["timezone"] == "Europe/London"
        assert defaults["general"]["history_keep_days"] == 12
        assert defaults["github"]["client_id"] == "gh123"
        assert defaults["microsoft"] == {"client_id": "ms123", "tenant_id": "tenant-x", "source": "auto"}

    def test_bad_history_seed_falls_back(self, monkeypatch):
        monkeypatch.setenv("HISTORY_KEEP_DAYS", "many")
        assert settings_service.DEFAULTS["general"]["history_keep_days"] == 30

    def test_defaults_are_fresh_copies(self):
        first = settings_service.DEFAULTS
        first["general"]["user_name"] = "mutated"
        assert settings_service.DEFAULTS["general"]["user_name"] == "User"

    def test_unknown_module_attribute_still_raises(self):
        with pytest.raises(AttributeError):
            getattr(settings_service, "NOT_A_THING")


class TestLoadSettings:
    def test_no_file_gives_defaults(self):
        assert settings_service.load_settings() == settings_service.DEFAULTS

    def test_file_values_deep_merge_over_defaults(self):
        config.atomic_write_json(config.settings_file(), {
            "general": {"daily_hours": 7.5}, "github": {"repos": ["a/b"]}, "extra": {"x": 1}})
        settings = settings_service.load_settings()
        assert settings["general"]["daily_hours"] == 7.5
        assert settings["general"]["user_name"] == "User"
        assert settings["github"]["repos"] == ["a/b"]
        assert settings["github"]["include_commits"] is True
        assert "extra" not in settings

    def test_corrupt_file_never_raises(self, caplog):
        with open(config.settings_file(), "w", encoding="utf-8") as handle:
            handle.write("{{{")
        with caplog.at_level(logging.WARNING):
            assert settings_service.load_settings() == settings_service.DEFAULTS
        assert "Corrupt JSON" in caplog.text

    def test_non_object_file_gives_defaults(self):
        config.atomic_write_json(config.settings_file(), [1, 2, 3])
        assert settings_service.load_settings() == settings_service.DEFAULTS

    def test_invalid_values_reset_to_defaults_with_warning(self, caplog):
        config.atomic_write_json(config.settings_file(), {
            "general": {"daily_hours": "abc", "timezone": "Nope/Nope", "user_name": "Ok"},
            "ai": {"temperature": 9},
            "microsoft": "not-an-object",
        })
        with caplog.at_level(logging.WARNING):
            settings = settings_service.load_settings()
        assert settings["general"]["daily_hours"] == 8.0
        assert settings["general"]["timezone"] == "Europe/Rome"
        assert settings["general"]["user_name"] == "Ok"
        assert settings["ai"]["temperature"] == 0.2
        assert settings["microsoft"]["source"] == "auto"
        assert "general.daily_hours" in caplog.text
        assert "ai.temperature" in caplog.text

    def test_get_dotted_path(self):
        config.atomic_write_json(config.settings_file(), {"general": {"daily_hours": 6}})
        assert settings_service.get("general.daily_hours") == 6
        assert settings_service.get("general.user_name") == "User"
        assert settings_service.get("ai.system_intro") is None
        assert settings_service.get("nope.deeper", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# settings_service: update & validate
# ---------------------------------------------------------------------------

class TestUpdateSettings:
    def test_partial_patch_deep_merges_and_persists(self):
        result = settings_service.update_settings({"general": {"daily_hours": 7.5}, "ai": {"system_rules": "Regole"}})
        assert result["general"]["daily_hours"] == 7.5
        assert result["general"]["user_name"] == "User"
        assert result["ai"]["system_rules"] == "Regole"
        assert result["ai"]["system_intro"] is None
        on_disk = _read(config.settings_file())
        assert on_disk["version"] == 2
        assert on_disk["general"]["daily_hours"] == 7.5
        assert settings_service.load_settings() == result

    def test_unknown_top_level_keys_ignored_and_logged(self, caplog):
        with caplog.at_level(logging.INFO):
            result = settings_service.update_settings({"bogus": {"a": 1}, "general": {"user_name": "X"}})
        assert "bogus" not in result
        assert "bogus" in caplog.text
        assert result["general"]["user_name"] == "X"

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_prompt_fields_become_none(self, value):
        settings_service.update_settings({"ai": {"system_intro": "custom"}})
        result = settings_service.update_settings({"ai": {"system_intro": value}})
        assert result["ai"]["system_intro"] is None

    def test_numeric_strings_are_coerced(self):
        result = settings_service.update_settings({"general": {"daily_hours": "7,5", "history_keep_days": "10"},
                                                   "ai": {"temperature": "0.7"}})
        assert result["general"]["daily_hours"] == 7.5
        assert result["general"]["history_keep_days"] == 10
        assert result["ai"]["temperature"] == 0.7

    def test_repos_are_trimmed_and_deduped(self):
        result = settings_service.update_settings({"github": {"repos": [" owner/repo ", "owner/repo", "", "a/b"]}})
        assert result["github"]["repos"] == ["owner/repo", "a/b"]

    def test_source_is_normalised(self):
        assert settings_service.update_settings({"microsoft": {"source": " Graph "}})["microsoft"]["source"] == "graph"

    def test_non_dict_patch_rejected(self):
        with pytest.raises(SettingsValidationError):
            settings_service.update_settings(["not", "a", "dict"])

    def test_invalid_patch_does_not_persist(self):
        with pytest.raises(SettingsValidationError):
            settings_service.update_settings({"general": {"daily_hours": 30}})
        assert not os.path.exists(config.settings_file())

    def test_version_in_patch_is_ignored(self):
        assert settings_service.update_settings({"version": 99})["version"] == 2


class TestValidation:
    @pytest.mark.parametrize("patch,fragment", [
        ({"general": {"daily_hours": 0}}, "ore giornaliere"),
        ({"general": {"daily_hours": 24.5}}, "ore giornaliere"),
        ({"general": {"daily_hours": "otto"}}, "ore giornaliere"),
        ({"general": {"timezone": "Europe/Atlantis"}}, "Fuso orario"),
        ({"general": {"history_keep_days": 0}}, "storico"),
        ({"general": {"history_keep_days": 366}}, "storico"),
        ({"general": {"history_keep_days": 2.5}}, "storico"),
        ({"general": {"user_name": "x" * 101}}, "nome utente"),
        ({"general": {"openai_model": ""}}, "modello"),
        ({"ai": {"temperature": 2.1}}, "temperatura"),
        ({"ai": {"temperature": -0.1}}, "temperatura"),
        ({"ai": {"system_rules": 123}}, "prompt"),
        ({"github": {"repos": ["no-slash"]}}, "owner/nome"),
        ({"github": {"repos": "owner/repo"}}, "owner/nome"),
        ({"github": {"repos": ["a/b/c"]}}, "owner/nome"),
        ({"github": {"client_id": "c" * 201}}, "Client ID"),
        ({"github": {"include_issues": "yes"}}, "include_issues"),
        ({"microsoft": {"client_id": 5}}, "Client ID"),
        ({"microsoft": {"tenant_id": ""}}, "Tenant"),
        ({"microsoft": {"source": "teams"}}, "sorgente"),
    ])
    def test_rules(self, patch, fragment):
        with pytest.raises(SettingsValidationError) as excinfo:
            settings_service.update_settings(patch)
        assert any(fragment.lower() in err.lower() for err in excinfo.value.errors), excinfo.value.errors

    def test_multiple_errors_collected(self):
        with pytest.raises(SettingsValidationError) as excinfo:
            settings_service.update_settings({"general": {"daily_hours": 0}, "ai": {"temperature": 5}})
        assert len(excinfo.value.errors) == 2
        assert isinstance(excinfo.value, ValueError)

    def test_validate_on_valid_defaults_is_empty(self):
        assert settings_service.validate(settings_service.DEFAULTS) == []

    def test_validate_on_garbage(self):
        assert settings_service.validate("nope")
        assert any("general" in e for e in settings_service.validate({}))

    def test_accepts_boundaries(self):
        result = settings_service.update_settings({
            "general": {"daily_hours": 24, "history_keep_days": 365},
            "ai": {"temperature": 2}, "microsoft": {"source": "outlook_com"},
            "github": {"repos": ["my.org/re-po_1"]}})
        assert result["general"]["daily_hours"] == 24.0


# ---------------------------------------------------------------------------
# settings_service: secrets
# ---------------------------------------------------------------------------

class TestSecrets:
    def test_defaults_when_missing(self):
        assert settings_service.load_secrets() == {"openai_api_key": "", "github_token": None}
        assert settings_service.get_secret("github_token") is None
        assert settings_service.get_secret("missing", "d") == "d"

    def test_set_and_get_secret_persist_in_separate_file(self):
        settings_service.set_secret("github_token", {"access_token": "t", "login": "me"})
        settings_service.set_secret("openai_api_key", "sk-test")
        secrets = _read(config.secrets_file())
        assert secrets == {"openai_api_key": "sk-test", "github_token": {"access_token": "t", "login": "me"}}
        assert not os.path.exists(config.settings_file()) or "openai_api_key" not in _read(config.settings_file())

    def test_openai_key_prefers_secrets_over_env(self, monkeypatch):
        assert settings_service.get_openai_api_key() == ""
        assert settings_service.openai_key_source() is None
        monkeypatch.setenv("OPENAI_API_KEY", " sk-env ")
        assert settings_service.get_openai_api_key() == "sk-env"
        assert settings_service.openai_key_source() == "env"
        settings_service.set_secret("openai_api_key", " sk-settings ")
        assert settings_service.get_openai_api_key() == "sk-settings"
        assert settings_service.openai_key_source() == "settings"

    def test_update_settings_stores_and_clears_openai_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        result = settings_service.update_settings({"openai_api_key": "sk-new", "general": {"user_name": "A"}})
        assert "openai_api_key" not in result
        assert "openai_api_key" not in _read(config.settings_file())
        assert settings_service.get_openai_api_key() == "sk-new"
        settings_service.update_settings({"openai_api_key": ""})
        assert settings_service.get_openai_api_key() == "sk-env"
        assert settings_service.openai_key_source() == "env"

    def test_update_settings_rejects_non_string_key_without_saving(self):
        with pytest.raises(SettingsValidationError):
            settings_service.update_settings({"openai_api_key": 123})
        assert settings_service.get_openai_api_key() == ""

    def test_corrupt_secrets_file_gives_defaults(self):
        with open(config.secrets_file(), "w", encoding="utf-8") as handle:
            handle.write("nope")
        assert settings_service.load_secrets() == {"openai_api_key": "", "github_token": None}

    def test_invalid_secret_key_rejected(self):
        with pytest.raises(ValueError):
            settings_service.set_secret("", "x")

    def test_reset_cache_is_noop(self):
        assert settings_service.reset_cache() is None

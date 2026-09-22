"""Unit tests for services.ai_service (prompts, rendering, balancing, OpenAI elaboration)."""

from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock, patch

import pytest

from services import ai_service

PRACTICES = [
    {"code": "100", "name": "Analisi", "description": "Raccolta requisiti"},
    {"code": "200", "name": "Sviluppo", "description": "Scrittura codice"},
]

MANUAL = {"id": "1", "type": "manual", "text": "Sviluppo API", "time": "09:15", "duration_min": None,
          "source_id": None, "meta": {}}
MEETING = {"id": "2", "type": "meeting", "text": "Stand-up", "time": "09:00", "duration_min": 30,
           "source_id": "meeting:x", "meta": {"provider": "graph", "start": "09:00", "end": "09:30"}}

ELABORATE_ARGS = dict(practices=PRACTICES, user_name="Raffaele", date="2026-09-16", daily_hours=8.0,
                      ai_settings={"temperature": 0.2}, model="gpt-4.1-mini", api_key="sk-test")


def _response(content):
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    return response


def _client(*contents):
    client = MagicMock()
    client.chat.completions.create.side_effect = [_response(c) for c in contents]
    return client


def _ai_json(items, note=""):
    numeric = [i.get("ore", 0) for i in items if isinstance(i.get("ore", 0), (int, float))]
    return json.dumps({"timesheet": items, "totale_ore": sum(numeric), "note": note})


# ---------------------------------------------------------------------------
# Prompt sections & placeholders
# ---------------------------------------------------------------------------

class TestPrompts:
    def test_defaults_contain_mandatory_rules_and_placeholders(self):
        assert set(ai_service.DEFAULT_PROMPTS) == {"system_intro", "system_rules", "system_output", "user_template"}
        rules = ai_service.DEFAULT_PROMPTS["system_rules"]
        assert "{{daily_hours}}" in rules and "{{daily_minutes}}" in rules and "{{codes}}" in rules
        assert "Riunione:" in rules
        assert "GitHub" in rules and "hash" in rules.lower()
        assert "0.25" in rules
        assert "{{practices}}" in ai_service.DEFAULT_PROMPTS["system_intro"]
        assert "JSON" in ai_service.DEFAULT_PROMPTS["system_output"]
        assert "{{entries}}" in ai_service.DEFAULT_PROMPTS["user_template"]
        assert "inisieme" not in rules and "similli" not in rules  # v1 typos fixed

    def test_placeholders_documented(self):
        names = {p["name"] for p in ai_service.PLACEHOLDERS}
        assert names == {"practices", "codes", "daily_hours", "daily_minutes", "user_name", "date", "weekday", "entries"}
        assert all(p["description"] and p["token"] == "{{" + p["name"] + "}}" for p in ai_service.PLACEHOLDERS)

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_effective_prompts_blank_means_default(self, value):
        effective = ai_service.effective_prompts({"system_rules": value, "system_intro": "Mio intro"})
        assert effective["system_rules"] == ai_service.DEFAULT_PROMPTS["system_rules"]
        assert effective["system_intro"] == "Mio intro"
        assert effective["user_template"] == ai_service.DEFAULT_PROMPTS["user_template"]

    def test_effective_prompts_with_none_settings(self):
        assert ai_service.effective_prompts(None) == ai_service.DEFAULT_PROMPTS

    def test_build_prompts_fills_every_placeholder(self):
        system, user = ai_service.build_prompts([MANUAL, MEETING], PRACTICES, "Raffaele", "2026-09-16", 7.5, None)
        assert "{{" not in system and "{{" not in user
        assert "- 100 (Analisi): Raccolta requisiti\n- 200 (Sviluppo): Scrittura codice" in system
        assert "100, 200" in system
        assert "ESATTAMENTE 7.50 ore (450 minuti)" in system
        assert '"totale_ore": 7.50' in system
        assert system.count("\n\n") >= 2
        assert user.startswith("Data: 2026-09-16 (mercoledì)\nUtente: Raffaele\n\nATTIVITÀ DELLA GIORNATA:\n- [09:15] Sviluppo API\n")
        assert user.endswith("Il totale DEVE essere esattamente 7.50 ore.")

    def test_custom_sections_and_json_braces_survive(self):
        custom = {"system_output": 'Rispondi {"a": {{daily_hours}}, "b": {{ codes }} } {{unknown}}'}
        system, _ = ai_service.build_prompts([MANUAL], PRACTICES, "U", "2026-01-01", 8, custom)
        assert 'Rispondi {"a": 8.00, "b": 100, 200 } {{unknown}}' in system

    @pytest.mark.parametrize("date_iso, expected", [
        ("2026-09-16", "mercoledì"), ("2026-09-21", "lunedì"), ("2026-09-27", "domenica"),
        ("not-a-date", ""), (None, ""), ("", ""),
    ])
    def test_weekday_name(self, date_iso, expected):
        assert ai_service.weekday_name(date_iso) == expected

    def test_rules_classify_meetings_by_topic_not_by_practice_name(self):
        rules = ai_service.DEFAULT_PROMPTS["system_rules"]
        assert "solo perché il suo nome contiene" in rules
        assert "ARGOMENTO" in rules and "giorno della settimana" in rules
        assert "{{weekday}}" in ai_service.DEFAULT_PROMPTS["user_template"]

    def test_fill_placeholders_leaves_unknown_tokens(self):
        assert ai_service.fill_placeholders("{{x}} {{date}}", {"date": "d"}) == "{{x}} d"


# ---------------------------------------------------------------------------
# Entry rendering
# ---------------------------------------------------------------------------

class TestRenderEntries:
    def test_manual(self):
        assert ai_service.render_entries([MANUAL]) == "- [09:15] Sviluppo API"

    def test_manual_multiline_is_indented(self):
        entry = {**MANUAL, "text": "Prima riga\n\n  seconda riga  "}
        assert ai_service.render_entries([entry]) == "- [09:15] Prima riga\n    seconda riga"

    def test_meeting_with_meta_times(self):
        assert ai_service.render_entries([MEETING]) == "- [09:00–09:30] Riunione: Stand-up (durata: 30 min) [Riunione]"

    def test_meeting_without_meta_uses_time_and_no_duration(self):
        entry = {**MEETING, "meta": {}, "duration_min": None, "time": "14:00"}
        assert ai_service.render_entries([entry]) == "- [14:00] Riunione: Stand-up [Riunione]"

    def test_meeting_text_already_prefixed_is_not_doubled(self):
        entry = {**MEETING, "text": "Riunione: Kick-off"}
        assert ai_service.render_entries([entry]).startswith("- [09:00–09:30] Riunione: Kick-off ")

    def test_legacy_outlook_type_renders_as_meeting(self):
        entry = {**MEETING, "type": "outlook"}
        assert "[Riunione]" in ai_service.render_entries([entry])

    def test_github_commits(self):
        commits = [{"sha": "a" * 40, "message": "feat: login\n\nbody", "url": "u"},
                   {"sha": "b" * 40, "message": "x" * 150, "url": "u"}]
        entry = {"type": "github", "text": "ignored", "time": "10:00",
                 "meta": {"kind": "commits", "repo": "owner/repo", "commits": commits, "url": "https://github.com/owner/repo"}}
        rendered = ai_service.render_entries([entry])
        lines = rendered.split("\n")
        assert lines[0] == "- [GitHub owner/repo] 2 commit:"
        assert lines[1] == "    • feat: login"
        assert lines[2].startswith("    • " + "x" * 100) and lines[2].endswith("…") and len(lines[2]) == 6 + 120

    def test_github_commits_capped_at_30(self):
        commits = [{"sha": str(i), "message": f"commit {i}", "url": ""} for i in range(35)]
        entry = {"type": "github", "text": "", "meta": {"kind": "commits", "repo": "o/r", "commits": commits}}
        lines = ai_service.render_entries([entry]).split("\n")
        assert lines[0] == "- [GitHub o/r] 35 commit:"
        assert len(lines) == 32
        assert lines[-1] == "    • … e altri 5"

    def test_github_pull_request_strips_repo_prefix(self):
        entry = {"type": "github", "text": "owner/repo · Pull request #12 aperta: Titolo", "time": "10:00",
                 "meta": {"kind": "pull_request", "repo": "owner/repo", "number": 12, "action": "opened", "url": "u"}}
        assert ai_service.render_entries([entry]) == "- [GitHub owner/repo] Pull request #12 aperta: Titolo"

    def test_github_without_meta_repo(self):
        entry = {"type": "github", "text": "Qualcosa", "meta": {}}
        assert ai_service.render_entries([entry]) == "- [GitHub GitHub] Qualcosa"

    def test_empty_list_and_unknown_type(self):
        assert ai_service.render_entries([]) == "(nessuna attività)"
        assert ai_service.render_entries([{"type": "weird", "text": "t", "time": "08:00"}]) == "- [08:00] t"

    def test_render_practices_without_description(self):
        assert ai_service.render_practices([{"code": "1", "name": "N", "description": ""}]) == "- 1 (N)"
        assert ai_service.render_practices([]) == "(nessuna pratica configurata)"


# ---------------------------------------------------------------------------
# Balancing
# ---------------------------------------------------------------------------

def _hours(items):
    return [i["ore"] for i in items]


class TestBalanceToTotal:
    def test_single_item_takes_total(self):
        assert _hours(ai_service.balance_to_total([{"pratica": "1", "ore": 3.1}], 8)) == [8.0]

    def test_already_balanced_untouched(self):
        items = [{"ore": 2.5, "pratica": "a"}, {"ore": 5.5, "pratica": "b"}]
        assert _hours(ai_service.balance_to_total(items, 8)) == [2.5, 5.5]

    def test_under_total_adds_to_largest(self):
        items = [{"ore": 1.0}, {"ore": 4.0}, {"ore": 2.0}]
        assert _hours(ai_service.balance_to_total(items, 8)) == [1.0, 5.0, 2.0]

    def test_over_total_removes_from_largest(self):
        items = [{"ore": 6.0}, {"ore": 4.0}]
        assert _hours(ai_service.balance_to_total(items, 8)) == [4.0, 4.0]

    def test_rounding_to_quarters_then_balance(self):
        items = [{"ore": 2.6}, {"ore": 5.3}]
        result = _hours(ai_service.balance_to_total(items, 8))
        assert result == [2.5, 5.5]
        assert all(round(h * 4) == h * 4 for h in result)

    def test_tiny_values_raised_to_minimum(self):
        items = [{"ore": 0.05}, {"ore": 7.9}]
        assert _hours(ai_service.balance_to_total(items, 8)) == [0.25, 7.75]

    def test_cascades_when_largest_cannot_absorb(self):
        items = [{"ore": 3.0}, {"ore": 3.0}, {"ore": 3.0}]
        result = _hours(ai_service.balance_to_total(items, 1.0))
        # Equal items: which one keeps the extra quarter is an implementation detail.
        assert sorted(result) == [0.25, 0.25, 0.5]
        assert sum(result) == 1.0

    def test_never_below_quarter_even_when_impossible(self):
        items = [{"ore": 1.0}, {"ore": 1.0}, {"ore": 1.0}]
        result = _hours(ai_service.balance_to_total(items, 0.5))
        assert result == [0.25, 0.25, 0.25]

    def test_sum_matches_total_for_many_shapes(self):
        cases = [([0.1, 0.1, 0.1], 8), ([12, 1], 8), ([1, 1, 1, 1, 1, 1, 1, 1, 1], 8), ([2.33, 2.33, 2.34], 7.5)]
        for hours, total in cases:
            result = ai_service.balance_to_total([{"ore": h} for h in hours], total)
            assert round(sum(_hours(result)), 2) == total, (hours, total)
            assert all(h >= 0.25 for h in _hours(result))

    def test_is_pure_and_preserves_other_fields(self):
        items = [{"pratica": "1", "ore": 3.1, "descrizione": "A."}, {"pratica": "2", "ore": 3.0, "descrizione": "B."}]
        snapshot = copy.deepcopy(items)
        result = ai_service.balance_to_total(items, 8)
        assert items == snapshot
        assert result[0]["descrizione"] == "A." and result[0]["pratica"] == "1"
        assert result is not items and result[0] is not items[0]

    def test_empty_and_invalid_total(self):
        assert ai_service.balance_to_total([], 8) == []
        with pytest.raises(ValueError):
            ai_service.balance_to_total([{"ore": 1}], 0)

    def test_invalid_hours_treated_as_minimum(self):
        assert _hours(ai_service.balance_to_total([{"ore": "abc"}, {"ore": 7}], 8)) == [0.25, 7.75]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

class TestFormatDescription:
    @pytest.mark.parametrize("raw,expected", [
        ("sviluppo api", "Sviluppo api."),
        ("Già ok.", "Già ok."),
        ("  spazi   multipli \n", "Spazi multipli."),
        ("domanda?", "Domanda?"),
        ("", "Attività non descritta."),
        (None, "Attività non descritta."),
        ("è accentata", "È accentata."),
    ])
    def test_cases(self, raw, expected):
        assert ai_service.format_description(raw) == expected


# ---------------------------------------------------------------------------
# elaborate_timesheet
# ---------------------------------------------------------------------------

class TestElaborateValidation:
    @pytest.mark.parametrize("api_key", ["", "   ", None])
    def test_missing_key(self, api_key):
        with pytest.raises(ValueError, match="OpenAI"):
            ai_service.elaborate_timesheet([MANUAL], **{**ELABORATE_ARGS, "api_key": api_key})

    def test_empty_entries(self):
        with pytest.raises(ValueError, match="Nessuna attività"):
            ai_service.elaborate_timesheet([], **ELABORATE_ARGS)

    def test_no_practices(self):
        with pytest.raises(ValueError, match="pratica"):
            ai_service.elaborate_timesheet([MANUAL], **{**ELABORATE_ARGS, "practices": []})

    @pytest.mark.parametrize("hours", [0, -1, "otto", None])
    def test_invalid_daily_hours(self, hours):
        with pytest.raises(ValueError, match="Ore giornaliere"):
            ai_service.elaborate_timesheet([MANUAL], **{**ELABORATE_ARGS, "daily_hours": hours})

    def test_validation_happens_before_client_creation(self):
        with patch("openai.OpenAI") as openai_cls:
            with pytest.raises(ValueError):
                ai_service.elaborate_timesheet([], **ELABORATE_ARGS)
        openai_cls.assert_not_called()


class TestElaborateWithMockedOpenAI:
    def test_success_normalises_and_balances(self):
        client = _client(_ai_json([
            {"pratica": 100, "ore": "2,6", "descrizione": "analisi requisiti"},
            {"pratica": "200", "ore": 4.0, "descrizione": "Sviluppo API."},
        ], note="tutto ok"))
        with patch("openai.OpenAI", return_value=client) as openai_cls:
            result = ai_service.elaborate_timesheet([MANUAL, MEETING], **ELABORATE_ARGS)
        openai_cls.assert_called_once_with(api_key="sk-test")
        assert result["timesheet"] == [
            {"pratica": "100", "ore": 2.5, "descrizione": "Analisi requisiti."},
            {"pratica": "200", "ore": 5.5, "descrizione": "Sviluppo API."},
        ]
        assert result["totale_ore"] == 8.0
        assert result["note"] == "tutto ok"
        assert set(result) == {"timesheet", "totale_ore", "note"}

    def test_request_parameters(self):
        client = _client(_ai_json([{"pratica": "100", "ore": 8, "descrizione": "X."}]))
        settings = {"temperature": 0.7, "system_rules": "REGOLE MIE {{daily_hours}}"}
        with patch("openai.OpenAI", return_value=client):
            ai_service.elaborate_timesheet([MANUAL], **{**ELABORATE_ARGS, "ai_settings": settings, "model": "gpt-x"})
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-x"
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == 1500
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["messages"][0]["role"] == "system" and "REGOLE MIE 8.00" in kwargs["messages"][0]["content"]
        assert kwargs["messages"][1]["role"] == "user" and "- [09:15] Sviluppo API" in kwargs["messages"][1]["content"]

    def test_invalid_temperature_falls_back_and_blank_model_uses_default(self):
        client = _client(_ai_json([{"pratica": "100", "ore": 8, "descrizione": "X."}]))
        with patch("openai.OpenAI", return_value=client):
            ai_service.elaborate_timesheet([MANUAL], **{**ELABORATE_ARGS, "ai_settings": {"temperature": 7}, "model": " "})
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.2
        assert kwargs["model"] == "gpt-4.1-mini"

    def test_unknown_practice_codes_flagged_in_note(self):
        client = _client(_ai_json([
            {"pratica": "999", "ore": 4, "descrizione": "A"},
            {"pratica": "100", "ore": 2, "descrizione": "B"},
            {"pratica": "999", "ore": 2, "descrizione": "C"},
        ], note="Nota AI"))
        with patch("openai.OpenAI", return_value=client):
            result = ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert [i["pratica"] for i in result["timesheet"]] == ["999", "100", "999"]
        assert result["note"] == "Nota AI Codice 999 non presente nell'elenco pratiche."

    def test_markdown_fences_are_stripped(self):
        client = _client("```json\n" + _ai_json([{"pratica": "100", "ore": 8, "descrizione": "X"}]) + "\n```")
        with patch("openai.OpenAI", return_value=client):
            result = ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert result["totale_ore"] == 8.0
        assert client.chat.completions.create.call_count == 1

    def test_json_embedded_in_prose_is_extracted(self):
        client = _client("Ecco il risultato: " + _ai_json([{"pratica": "100", "ore": 8, "descrizione": "X"}]) + " Fine.")
        with patch("openai.OpenAI", return_value=client):
            assert ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)["totale_ore"] == 8.0

    def test_retry_once_on_unparsable_json(self):
        client = _client("non sono json", _ai_json([{"pratica": "100", "ore": 8, "descrizione": "Ok"}]))
        with patch("openai.OpenAI", return_value=client):
            result = ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert client.chat.completions.create.call_count == 2
        second_system = client.chat.completions.create.call_args_list[1].kwargs["messages"][0]["content"]
        assert second_system.endswith(ai_service.RETRY_INSTRUCTION)
        assert result["timesheet"][0]["descrizione"] == "Ok."

    def test_unparsable_twice_raises_value_error(self):
        client = _client("no", "[1, 2, 3]")
        with patch("openai.OpenAI", return_value=client):
            with pytest.raises(ValueError, match="JSON"):
                ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert client.chat.completions.create.call_count == 2

    @pytest.mark.parametrize("payload", [
        json.dumps({"note": "senza timesheet"}),
        json.dumps({"timesheet": []}),
        json.dumps({"timesheet": "non lista"}),
        json.dumps({"timesheet": [{"pratica": "100", "ore": 0, "descrizione": "x"}]}),
        json.dumps({"timesheet": [{"pratica": "100", "ore": "molte", "descrizione": "x"}, "junk"]}),
    ])
    def test_invalid_structure_raises_after_one_retry(self, payload):
        client = _client(payload, payload)
        with patch("openai.OpenAI", return_value=client):
            with pytest.raises(ValueError, match="nuovo tentativo"):
                ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert client.chat.completions.create.call_count == 2
        second_system = client.chat.completions.create.call_args_list[1].kwargs["messages"][0]["content"]
        assert second_system.endswith(ai_service.RETRY_INSTRUCTION)

    def test_invalid_structure_then_valid_reply_succeeds(self):
        client = _client(json.dumps({"result": "wrong shape"}),
                         _ai_json([{"pratica": "100", "ore": 8, "descrizione": "Ok"}]))
        with patch("openai.OpenAI", return_value=client):
            result = ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert client.chat.completions.create.call_count == 2
        assert result["timesheet"][0]["descrizione"] == "Ok."

    def test_zero_hour_items_dropped_but_others_kept(self):
        client = _client(_ai_json([{"pratica": "100", "ore": 0, "descrizione": "nulla"},
                                   {"pratica": "200", "ore": 3, "descrizione": "ok"}]))
        with patch("openai.OpenAI", return_value=client):
            result = ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert result["timesheet"] == [{"pratica": "200", "ore": 8.0, "descrizione": "Ok."}]

    def test_empty_response_content_triggers_retry(self):
        client = _client(None, _ai_json([{"pratica": "100", "ore": 8, "descrizione": "X"}]))
        with patch("openai.OpenAI", return_value=client):
            assert ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)["totale_ore"] == 8.0

    def test_response_format_type_error_falls_back(self):
        client = MagicMock()
        good = _response(_ai_json([{"pratica": "100", "ore": 8, "descrizione": "X"}]))

        def create(**kwargs):
            if "response_format" in kwargs:
                raise TypeError("unexpected keyword argument 'response_format'")
            return good

        client.chat.completions.create.side_effect = create
        with patch("openai.OpenAI", return_value=client):
            result = ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)
        assert result["totale_ore"] == 8.0
        assert client.chat.completions.create.call_count == 2
        assert "response_format" not in client.chat.completions.create.call_args.kwargs

    def test_response_format_rejected_by_model_falls_back(self):
        from openai import BadRequestError

        client = MagicMock()
        good = _response(_ai_json([{"pratica": "100", "ore": 8, "descrizione": "X"}]))
        error = BadRequestError("Invalid parameter: 'response_format' not supported",
                                response=MagicMock(status_code=400), body=None)

        def create(**kwargs):
            if "response_format" in kwargs:
                raise error
            return good

        client.chat.completions.create.side_effect = create
        with patch("openai.OpenAI", return_value=client):
            assert ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)["totale_ore"] == 8.0

    def test_openai_errors_are_reraised(self):
        from openai import AuthenticationError

        client = MagicMock()
        client.chat.completions.create.side_effect = AuthenticationError(
            "bad key", response=MagicMock(status_code=401), body=None)
        with patch("openai.OpenAI", return_value=client):
            with pytest.raises(AuthenticationError):
                ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)

    def test_generic_exception_is_reraised_unchanged(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("openai.OpenAI", return_value=client):
            with pytest.raises(RuntimeError, match="boom"):
                ai_service.elaborate_timesheet([MANUAL], **ELABORATE_ARGS)

    def test_reset_cache_is_noop(self):
        assert ai_service.reset_cache() is None

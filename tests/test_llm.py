"""
Tests for exercise_log.llm.

All Ollama network calls are mocked so these tests run offline.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import exercise_log.llm as llm_module
from exercise_log.llm import (
    _parse_response,
    full_log_parse,
    identify_exercise,
    load_config,
    sets_reps_notes,
    _call_llm_parsed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_config():
    """Reset the cached config so each test gets a clean state."""
    llm_module._config = None


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_defaults_when_no_file(self):
        _reset_config()
        cfg = load_config(config_path=Path("/nonexistent/config.yaml"))
        assert cfg["llm"]["enabled"] is False

    def test_loads_yaml_file(self, tmp_path):
        _reset_config()
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n  enabled: true\n  model: mistral\n  response_format: csv\n",
            encoding="utf-8",
        )
        cfg = load_config(config_path=cfg_file)
        assert cfg["llm"]["enabled"] is True
        assert cfg["llm"]["model"] == "mistral"
        assert cfg["llm"]["response_format"] == "csv"

    def test_missing_keys_use_defaults(self, tmp_path):
        _reset_config()
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("llm:\n  enabled: true\n", encoding="utf-8")
        cfg = load_config(config_path=cfg_file)
        assert cfg["llm"]["model"] == "llama3"
        assert cfg["llm"]["base_url"] == "http://localhost:11434"

    def test_caches_config(self, tmp_path):
        _reset_config()
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("llm:\n  enabled: false\n", encoding="utf-8")
        load_config(config_path=cfg_file)
        # get_config() returns the cached dict without re-reading the file
        cfg1 = llm_module.get_config()
        cfg2 = llm_module.get_config()
        assert cfg1 is cfg2


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_json_all_fields(self):
        text = json.dumps({"exercise": "face pull", "weight": "30", "units": "lb",
                            "reps": "15", "sets": "3", "notes": "hard"})
        fields = ["exercise", "weight", "units", "reps", "sets", "notes"]
        result = _parse_response(text, fields)
        assert result["exercise"] == "face pull"
        assert result["weight"] == "30"
        assert result["units"] == "lb"
        assert result["reps"] == "15"
        assert result["sets"] == "3"
        assert result["notes"] == "hard"

    def test_json_missing_field_returns_empty_string(self):
        text = json.dumps({"exercise": "bench press"})
        result = _parse_response(text, ["exercise", "weight", "units"])
        assert result["exercise"] == "bench press"
        assert result["weight"] == ""
        assert result["units"] == ""

    def test_json_in_markdown_code_block(self):
        text = "```json\n{\"exercise\": \"squat\"}\n```"
        result = _parse_response(text, ["exercise"])
        assert result["exercise"] == "squat"

    def test_csv_with_header(self):
        text = "exercise,weight,units\ndeadlift,80,kg"
        result = _parse_response(text, ["exercise", "weight", "units"])
        assert result["exercise"] == "deadlift"
        assert result["weight"] == "80"
        assert result["units"] == "kg"

    def test_csv_without_header_matching_field_count(self):
        text = "3,10,was hard"
        result = _parse_response(text, ["sets", "reps", "notes"])
        assert result["sets"] == "3"
        assert result["reps"] == "10"
        assert result["notes"] == "was hard"

    def test_unparseable_returns_empty_strings(self):
        result = _parse_response("this is not valid", ["sets", "reps"])
        assert result == {"sets": "", "reps": ""}

    # --- Lenient JSON (unquoted string values) ---

    def test_json_unquoted_single_word_value(self):
        # LLM sometimes forgets to quote simple values.
        result = _parse_response('{"exercise": Squat}', ["exercise"])
        assert result["exercise"] == "Squat"

    def test_json_unquoted_multiword_value(self):
        # The exact failure case reported: pretty-printed JSON, unquoted value.
        text = '{\n  "exercise": Face Pull\n}'
        result = _parse_response(text, ["exercise"])
        assert result["exercise"] == "Face Pull"

    def test_json_mixed_quoted_and_unquoted(self):
        # Quoted numeric fields alongside an unquoted string field.
        text = '{"exercise": Dumbbell Pullover, "weight": "35", "units": "lb"}'
        result = _parse_response(text, ["exercise", "weight", "units"])
        assert result["exercise"] == "Dumbbell Pullover"
        assert result["weight"] == "35"
        assert result["units"] == "lb"


# ---------------------------------------------------------------------------
# _call_llm_parsed – retry logic
# ---------------------------------------------------------------------------


class TestCallLlmParsed:
    def _cfg_with_prompt(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n  enabled: true\n  model: llama3\n  max_retries: 2\n"
            "  response_format: json\n"
            "prompts:\n  identify_exercise_prompt: |\n    ID: {{exercise}}\n",
            encoding="utf-8",
        )
        return cfg_file

    def test_returns_first_good_response(self, tmp_path):
        """No retry needed when first response parses fine."""
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        good = json.dumps({"exercise": "bench press"})
        with patch.object(llm_module, "_ollama_chat", return_value=good) as mock_chat:
            result = _call_llm_parsed("prompt", ["exercise"], max_retries=2)

        assert result == {"exercise": "bench press"}
        assert mock_chat.call_count == 1

    def test_retries_on_unparseable_then_succeeds(self, tmp_path):
        """Should retry and succeed on the second attempt."""
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        bad = "not parseable at all !!!"
        good = json.dumps({"exercise": "face pull"})
        with patch.object(
            llm_module, "_ollama_chat", side_effect=[bad, good]
        ) as mock_chat:
            result = _call_llm_parsed("prompt", ["exercise"], max_retries=2)

        assert result == {"exercise": "face pull"}
        assert mock_chat.call_count == 2

    def test_gives_up_after_max_retries(self, tmp_path):
        """Should return all-empty dict after exhausting retries."""
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        bad = "???"
        with patch.object(
            llm_module, "_ollama_chat", return_value=bad
        ) as mock_chat:
            result = _call_llm_parsed("prompt", ["exercise"], max_retries=2)

        assert result == {"exercise": ""}
        # 1 initial attempt + 2 retries = 3 total
        assert mock_chat.call_count == 3

    def test_returns_none_when_ollama_unavailable(self, tmp_path):
        """Should return None immediately if Ollama itself is unavailable."""
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        with patch.object(llm_module, "_ollama_chat", return_value=None) as mock_chat:
            result = _call_llm_parsed("prompt", ["exercise"], max_retries=2)

        assert result is None
        assert mock_chat.call_count == 1

    def test_identify_exercise_uses_max_retries_from_config(self, tmp_path):
        """identify_exercise() picks up max_retries from config.yaml."""
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        bad = "not valid"
        good = json.dumps({"exercise": "face pull"})
        with patch.object(
            llm_module, "_ollama_chat", side_effect=[bad, good]
        ) as mock_chat:
            result = identify_exercise("facebook")

        assert result == "face pull"
        assert mock_chat.call_count == 2


# ---------------------------------------------------------------------------
# full_log_parse (mocked Ollama)
# ---------------------------------------------------------------------------


class TestFullLogParse:
    def _cfg_with_prompt(self, tmp_path, response_format="json"):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            f"llm:\n  enabled: true\n  model: llama3\n  response_format: {response_format}\n"
            "prompts:\n"
            "  full_log_parse_prompt: |\n"
            "    Parse: {{text}} format={{response_format}}\n",
            encoding="utf-8",
        )
        return cfg_file

    def test_returns_empty_dict_when_llm_disabled(self, tmp_path):
        _reset_config()
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("llm:\n  enabled: false\n", encoding="utf-8")
        load_config(config_path=cfg_file)
        result = full_log_parse("Shoulder press")
        assert result == {f: "" for f in ["exercise", "weight", "units", "reps", "sets", "notes"]}

    def test_calls_ollama_and_parses_json(self, tmp_path):
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        llm_response = json.dumps({
            "exercise": "shoulder press", "weight": "50", "units": "lb",
            "reps": "10", "sets": "3", "notes": "",
        })
        with patch.object(llm_module, "_ollama_chat", return_value=llm_response):
            result = full_log_parse("Shoulder press 50 lbs 3x10")

        assert result["exercise"] == "shoulder press"
        assert result["weight"] == "50"
        assert result["units"] == "lb"
        assert result["sets"] == "3"
        assert result["reps"] == "10"

    def test_returns_empty_dict_when_ollama_returns_none(self, tmp_path):
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        with patch.object(llm_module, "_ollama_chat", return_value=None):
            result = full_log_parse("Some exercise")

        assert all(v == "" for v in result.values())


# ---------------------------------------------------------------------------
# identify_exercise (mocked Ollama)
# ---------------------------------------------------------------------------


class TestIdentifyExercise:
    def _cfg_with_prompt(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n  enabled: true\n  model: llama3\n  response_format: json\n"
            "prompts:\n"
            "  identify_exercise_prompt: |\n"
            "    Identify: {{exercise}} format={{response_format}}\n",
            encoding="utf-8",
        )
        return cfg_file

    def test_corrects_garbled_exercise_name(self, tmp_path):
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        with patch.object(llm_module, "_ollama_chat",
                          return_value=json.dumps({"exercise": "face pull"})):
            result = identify_exercise("facebook")

        assert result == "face pull"

    def test_returns_original_when_ollama_returns_none(self, tmp_path):
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        with patch.object(llm_module, "_ollama_chat", return_value=None):
            result = identify_exercise("Gumball pullovers")

        assert result == "Gumball pullovers"

    def test_returns_original_when_llm_disabled(self, tmp_path):
        _reset_config()
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("llm:\n  enabled: false\n", encoding="utf-8")
        load_config(config_path=cfg_file)
        result = identify_exercise("Gumball pullovers")
        assert result == "Gumball pullovers"


# ---------------------------------------------------------------------------
# sets_reps_notes (mocked Ollama)
# ---------------------------------------------------------------------------


class TestSetsRepsNotes:
    def _cfg_with_prompt(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n  enabled: true\n  model: llama3\n  response_format: json\n"
            "prompts:\n"
            "  sets_reps_notes_prompt: |\n"
            "    Parse sets/reps: {{remainder}} format={{response_format}}\n",
            encoding="utf-8",
        )
        return cfg_file

    def test_extracts_sets_reps_notes_from_json(self, tmp_path):
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        with patch.object(llm_module, "_ollama_chat",
                          return_value=json.dumps({"sets": "3", "reps": "15", "notes": "was hard"})):
            result = sets_reps_notes("3×15 was hard")

        assert result["sets"] == "3"
        assert result["reps"] == "15"
        assert result["notes"] == "was hard"

    def test_word_count_three_times(self, tmp_path):
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        with patch.object(llm_module, "_ollama_chat",
                          return_value=json.dumps({"sets": "3", "reps": "", "notes": ""})):
            result = sets_reps_notes("three times")

        assert result["sets"] == "3"
        assert result["reps"] == ""

    def test_returns_empty_dict_when_ollama_returns_none(self, tmp_path):
        _reset_config()
        load_config(config_path=self._cfg_with_prompt(tmp_path))

        with patch.object(llm_module, "_ollama_chat", return_value=None):
            result = sets_reps_notes("3x10")

        assert result == {"sets": "", "reps": "", "notes": ""}

    def test_csv_response_format(self, tmp_path):
        _reset_config()
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n  enabled: true\n  model: llama3\n  response_format: csv\n"
            "prompts:\n"
            "  sets_reps_notes_prompt: |\n"
            "    Parse: {{remainder}} format={{response_format}}\n",
            encoding="utf-8",
        )
        load_config(config_path=cfg_file)

        with patch.object(llm_module, "_ollama_chat",
                          return_value="sets,reps,notes\n4,12,easy"):
            result = sets_reps_notes("4×12 easy")

        assert result["sets"] == "4"
        assert result["reps"] == "12"
        assert result["notes"] == "easy"

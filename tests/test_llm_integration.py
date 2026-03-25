"""
Integration tests for exercise_log.llm.

These tests call the *real* Ollama server configured in config.yaml (model and
base_url are read from there).  They are automatically skipped when the server
is unreachable so the regular test suite can run without a running Ollama
instance.

Run only integration tests:
    pytest -m integration -v

Skip integration tests (default when Ollama is not running):
    pytest -m "not integration" -v
"""

import socket
import urllib.parse
from pathlib import Path

import pytest

import exercise_log.llm as llm_module
from exercise_log.llm import (
    full_log_parse,
    identify_exercise,
    load_config,
    sets_reps_notes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"


def _ollama_reachable() -> bool:
    """Return True if the Ollama server from config.yaml is reachable."""
    try:
        llm_module._config = None
        cfg = load_config(config_path=_CONFIG_PATH)
        if not cfg["llm"].get("enabled", False):
            return False
        raw_url: str = cfg["llm"].get("base_url", "http://localhost:11434")
        parsed = urllib.parse.urlparse(raw_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 11434
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


_SKIP_REASON = (
    "Ollama is not reachable at the base_url configured in config.yaml "
    "(set llm.enabled: true and ensure Ollama is running to run these tests)"
)
pytestmark = pytest.mark.integration


def _load_real_config():
    """Load the real config.yaml and return it."""
    llm_module._config = None
    return load_config(config_path=_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def require_ollama():
    """Skip every test in this module when Ollama is not reachable."""
    if not _ollama_reachable():
        pytest.skip(_SKIP_REASON)
    _load_real_config()
    yield
    llm_module._config = None


# ---------------------------------------------------------------------------
# full_log_parse  – fallback when regex cannot find weight/unit
# ---------------------------------------------------------------------------


class TestFullLogParseIntegration:
    def test_basic_weight_and_exercise(self):
        """LLM should return a structured dict from a natural-language entry."""
        result = full_log_parse("Shoulder press 50 pounds 3 sets of 10 reps")
        assert result.get("exercise"), "exercise should not be empty"
        assert result.get("weight"), "weight should not be empty"
        assert result.get("units") in ("lb", "kg"), "units should be 'lb' or 'kg'"

    def test_kilograms_entry(self):
        """LLM should correctly identify kg units."""
        result = full_log_parse("Deadlift 100 kilograms 5 reps")
        assert result.get("units") == "kg"
        assert result.get("weight") == "100"

    def test_returns_dict_with_all_fields(self):
        """All six expected fields must be present in the response."""
        result = full_log_parse("Bench press 60 kg 4 sets 8 reps felt strong")
        expected_fields = {"exercise", "weight", "units", "reps", "sets", "notes"}
        assert expected_fields == set(result.keys())

    def test_garbled_speech_to_text_entry(self):
        """LLM should handle garbled text and still return a reasonable exercise."""
        result = full_log_parse("Dumbbell curls thirty five lbs three times twelve reps")
        assert result.get("exercise"), "exercise should not be empty"
        assert result.get("weight"), "weight should not be empty"


# ---------------------------------------------------------------------------
# identify_exercise  – speech-recognition correction
# ---------------------------------------------------------------------------


class TestIdentifyExerciseIntegration:
    def test_corrects_obvious_garble(self):
        """'Gumball pullovers' should be corrected to a real exercise name."""
        result = identify_exercise("Gumball pullovers")
        assert result, "result should not be empty"
        assert result.lower() != "gumball pullovers", (
            "LLM should correct the garbled name"
        )

    def test_preserves_correct_name(self):
        """A correctly spelled exercise name should be returned unchanged or equivalent."""
        result = identify_exercise("bench press")
        assert "bench press" in result.lower(), (
            "LLM should recognize a correct exercise name"
        )

    def test_returns_string(self):
        """identify_exercise must always return a non-empty string."""
        result = identify_exercise("tricep push downs")
        assert isinstance(result, str)
        assert result.strip()


# ---------------------------------------------------------------------------
# sets_reps_notes  – extract structured fields from remainder text
# ---------------------------------------------------------------------------


class TestSetsRepsNotesIntegration:
    def test_standard_notation(self):
        """'3x12' should yield sets=3, reps=12."""
        result = sets_reps_notes("3x12")
        assert result.get("sets") == "3"
        assert result.get("reps") == "12"

    def test_unicode_times_notation(self):
        """'3×15 was hard' should yield sets=3, reps=15, non-empty notes."""
        result = sets_reps_notes("3×15 was hard")
        assert result.get("sets") == "3"
        assert result.get("reps") == "15"
        assert result.get("notes"), "notes should mention the extra text"

    def test_word_number_sets(self):
        """'three sets of ten' should yield sets=3, reps=10."""
        result = sets_reps_notes("three sets of ten")
        assert result.get("sets") == "3"
        assert result.get("reps") == "10"

    def test_returns_dict_with_all_fields(self):
        """All three expected fields must be present."""
        result = sets_reps_notes("4x8 easy day")
        expected_fields = {"sets", "reps", "notes"}
        assert expected_fields == set(result.keys())

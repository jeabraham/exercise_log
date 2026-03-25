"""
llm.py – Ollama integration for the exercise-log parser.

This module loads the LLM prompts and settings from ``config.yaml`` (searched
in the working directory and the package root) and provides three public
functions:

  full_log_parse(text)         – parse a complete log entry when the pattern
                                  parser could not extract weight/units.
  identify_exercise(exercise)  – correct a (possibly garbled) exercise name.
  sets_reps_notes(remainder)   – extract sets, reps and notes from the text
                                  that follows the weight token.

All three functions return a plain ``dict[str, str]``.  If the LLM is
disabled or the call fails, they return an empty dict so the caller can
apply sensible defaults.
"""

import csv as _csv
import io
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "enabled": False,
        "model": "llama3",
        "base_url": "http://localhost:11434",
        "response_format": "json",
    },
    "prompts": {
        "full_log_parse_prompt": "",
        "identify_exercise_prompt": "",
        "sets_reps_notes_prompt": "",
    },
}

_config: Optional[Dict[str, Any]] = None


def _find_config_path() -> Optional[Path]:
    """Search for config.yaml in common locations."""
    candidates: List[Path] = [
        Path(os.getcwd()) / "config.yaml",
        Path(__file__).parent.parent / "config.yaml",
        Path(__file__).parent / "config.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load and return the configuration from *config_path* (or search default
    locations).  Always returns a complete dict; missing keys fall back to
    ``_DEFAULT_CONFIG``.
    """
    global _config  # noqa: PLW0603

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pyyaml is not installed; LLM integration disabled.")
        _config = _DEFAULT_CONFIG
        return _config

    if config_path is None:
        config_path = _find_config_path()

    if config_path is None or not config_path.exists():
        logger.debug("config.yaml not found; using defaults (LLM disabled).")
        _config = _DEFAULT_CONFIG
        return _config

    try:
        with config_path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse %s: %s – using defaults.", config_path, exc)
        _config = _DEFAULT_CONFIG
        return _config

    # Deep-merge with defaults so that missing sections still work.
    merged: Dict[str, Any] = {}
    for key, default_val in _DEFAULT_CONFIG.items():
        if isinstance(default_val, dict):
            merged[key] = {**default_val, **(raw.get(key) or {})}
        else:
            merged[key] = raw.get(key, default_val)

    _config = merged
    return _config


def get_config() -> Dict[str, Any]:
    """Return cached config, loading it on first call."""
    if _config is None:
        load_config()
    return _config  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Ollama client helpers
# ---------------------------------------------------------------------------

def _ollama_chat(prompt: str) -> Optional[str]:
    """
    Send *prompt* to Ollama and return the response text, or ``None`` on
    failure.
    """
    cfg = get_config()
    llm_cfg = cfg.get("llm", {})

    if not llm_cfg.get("enabled", False):
        return None

    try:
        import ollama  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("ollama package is not installed; LLM query skipped.")
        return None

    model: str = llm_cfg.get("model", "llama3")
    base_url: str = llm_cfg.get("base_url", "http://localhost:11434")

    logger.info(
        "LLM request  → model=%s  prompt=%r…",
        model,
        prompt[:100],
    )
    try:
        client = ollama.Client(host=base_url)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        content: str = response["message"]["content"]
        logger.info("LLM response ← %r…", content[:120])
        return content
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ollama request failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def _parse_response(text: str, expected_fields: List[str]) -> Dict[str, str]:
    """
    Try to parse *text* as JSON first, then as CSV, and return a dict
    containing only the *expected_fields* (with empty strings for missing
    keys).
    """
    result: Dict[str, str] = {f: "" for f in expected_fields}

    stripped = text.strip()

    # --- Try JSON ---
    # Sometimes the model wraps JSON in a markdown code block.
    json_text = stripped
    if json_text.startswith("```"):
        lines = json_text.splitlines()
        # Strip opening and closing fence lines
        inner = [
            ln for ln in lines[1:]
            if not ln.strip().startswith("```")
        ]
        json_text = "\n".join(inner).strip()

    try:
        obj = json.loads(json_text)
        if isinstance(obj, dict):
            for field in expected_fields:
                result[field] = str(obj.get(field, "")).strip()
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # --- Try CSV ---
    # Expect either a header+data row or a plain data row matching the fields.
    try:
        reader = _csv.reader(io.StringIO(stripped))
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        if len(rows) == 2:
            # First row is header.
            header = [h.strip().lower() for h in rows[0]]
            values = [v.strip() for v in rows[1]]
            for field in expected_fields:
                try:
                    idx = header.index(field.lower())
                    result[field] = values[idx] if idx < len(values) else ""
                except ValueError:
                    pass
            return result
        if len(rows) == 1 and len(rows[0]) == len(expected_fields):
            # Plain data row, assume same order as expected_fields.
            for field, value in zip(expected_fields, rows[0]):
                result[field] = value.strip()
            return result
    except Exception:  # noqa: BLE001
        pass

    logger.warning("Could not parse LLM response: %r", text[:200])
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_FULL_LOG_FIELDS = ["exercise", "weight", "units", "reps", "sets", "notes"]
_EXERCISE_FIELDS = ["exercise"]
_SETS_REPS_FIELDS = ["sets", "reps", "notes"]


def full_log_parse(text: str) -> Dict[str, str]:
    """
    Ask the LLM to parse a complete exercise log entry that the pattern
    parser could not handle.

    Returns a dict with keys: exercise, weight, units, reps, sets, notes.
    Any field the LLM could not determine will be an empty string.
    """
    cfg = get_config()
    response_format: str = cfg["llm"].get("response_format", "json")
    template: str = cfg["prompts"].get("full_log_parse_prompt", "")
    if not template:
        return {f: "" for f in _FULL_LOG_FIELDS}

    prompt = template.format(text=text, response_format=response_format)
    raw = _ollama_chat(prompt)
    if raw is None:
        return {f: "" for f in _FULL_LOG_FIELDS}

    return _parse_response(raw, _FULL_LOG_FIELDS)


def identify_exercise(exercise: str) -> str:
    """
    Ask the LLM to correct a possibly-garbled exercise name.

    Returns the corrected name, or *exercise* unchanged if the LLM is
    unavailable or returns an empty result.
    """
    cfg = get_config()
    response_format: str = cfg["llm"].get("response_format", "json")
    template: str = cfg["prompts"].get("identify_exercise_prompt", "")
    if not template:
        return exercise

    prompt = template.format(exercise=exercise, response_format=response_format)
    raw = _ollama_chat(prompt)
    if raw is None:
        return exercise

    parsed = _parse_response(raw, _EXERCISE_FIELDS)
    return parsed.get("exercise") or exercise


def sets_reps_notes(remainder: str) -> Dict[str, str]:
    """
    Ask the LLM to extract sets, reps, and notes from the text that follows
    the weight token in a successfully-parsed log entry.

    Returns a dict with keys: sets, reps, notes.
    """
    cfg = get_config()
    response_format: str = cfg["llm"].get("response_format", "json")
    template: str = cfg["prompts"].get("sets_reps_notes_prompt", "")
    if not template:
        return {f: "" for f in _SETS_REPS_FIELDS}

    prompt = template.format(remainder=remainder, response_format=response_format)
    raw = _ollama_chat(prompt)
    if raw is None:
        return {f: "" for f in _SETS_REPS_FIELDS}

    return _parse_response(raw, _SETS_REPS_FIELDS)

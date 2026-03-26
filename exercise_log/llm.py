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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
        "max_retries": 2,
    },
    "prompts": {
        "full_log_parse_prompt": "",
        "identify_exercise_prompt": "",
        "sets_reps_notes_prompt": "",
    },
}

_config: Optional[Dict[str, Any]] = None

# Regex for extracting the leading numeric token from a weight string.
# Handles integers ("100"), decimals ("22.5"), and mixed fractions ("27 1/2").
_WEIGHT_NUM_RE = re.compile(r"^(\d+(?:\s+\d+/\d+|\.\d+)?)")

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

def _lenient_json_extract(text: str, expected_fields: List[str]) -> Dict[str, str]:
    """
    Regex-based fallback for responses that are *almost* valid JSON but have
    unquoted string values, e.g. ``"exercise": Face Pull``.

    For each *expected_field* the function tries:
    1. A properly-quoted value: ``"field": "value"``
    2. An unquoted value that stops at the next comma, newline, or ``}``.
    """
    result: Dict[str, str] = {f: "" for f in expected_fields}
    for field in expected_fields:
        # Prefer a properly-quoted value.
        m = re.search(
            rf'"{re.escape(field)}"\s*:\s*"([^"]*)"',
            text,
            re.IGNORECASE,
        )
        if m:
            result[field] = m.group(1).strip()
            continue
        # Fall back to an unquoted value (stop at comma, newline, or closing brace).
        m = re.search(
            rf'"{re.escape(field)}"\s*:\s*([^"\n,}}][^,\n}}]*)',
            text,
            re.IGNORECASE,
        )
        if m:
            result[field] = m.group(1).strip()
    return result


def _try_parse_response(
    text: str, expected_fields: List[str]
) -> Tuple[Dict[str, str], bool]:
    """
    Parse *text* and return ``(result_dict, success)``.

    *success* is ``True`` when at least one parser produced a usable result;
    ``False`` means every parser failed and the caller should consider retrying.
    Tries in order: standard JSON → CSV → lenient-regex JSON fallback.
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
            return result, True
    except (json.JSONDecodeError, ValueError):
        pass

    # --- Lenient regex fallback (handles unquoted JSON string values) ---
    # Try this before CSV so that JSON-like text is never mis-parsed as CSV.
    lenient = _lenient_json_extract(json_text, expected_fields)
    if any(v for v in lenient.values()):
        return lenient, True

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
            return result, True
        # Plain data row: require multiple fields to avoid treating any
        # single-word string as a valid single-field response (e.g. any
        # random text would appear to match the single "exercise" field).
        # Single-field responses are handled by the JSON/lenient-JSON paths.
        if (
            len(rows) == 1
            and len(rows[0]) == len(expected_fields)
            and len(expected_fields) > 1
        ):
            for field, value in zip(expected_fields, rows[0]):
                result[field] = value.strip()
            return result, True
    except Exception:  # noqa: BLE001
        pass

    logger.warning("Could not parse LLM response: %r", text[:200])
    return result, False


def _parse_response(text: str, expected_fields: List[str]) -> Dict[str, str]:
    """
    Parse *text* and return a dict of *expected_fields*.  Kept for backward
    compatibility; prefer ``_try_parse_response`` when the parse-success flag
    is needed.
    """
    result, _ = _try_parse_response(text, expected_fields)
    return result


def _call_llm_parsed(
    prompt: str,
    expected_fields: List[str],
    max_retries: int,
) -> Optional[Dict[str, str]]:
    """
    Send *prompt* to the LLM, parse the response, and retry up to
    *max_retries* additional times whenever parsing fails.

    Returns ``None`` if the LLM is unavailable; otherwise returns the parsed
    dict (fields may be empty strings if the LLM could not determine them).
    """
    for attempt in range(1 + max_retries):
        if attempt > 0:
            logger.warning(
                "LLM response could not be parsed; retrying (attempt %d of %d)…",
                attempt + 1,
                1 + max_retries,
            )
        raw = _ollama_chat(prompt)
        if raw is None:
            return None
        result, ok = _try_parse_response(raw, expected_fields)
        if ok:
            return result
    logger.warning(
        "LLM response still unparseable after %d attempt(s); giving up.",
        1 + max_retries,
    )
    return {f: "" for f in expected_fields}


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
    max_retries: int = cfg["llm"].get("max_retries", 2)
    template: str = cfg["prompts"].get("full_log_parse_prompt", "")
    if not template:
        return {f: "" for f in _FULL_LOG_FIELDS}

    prompt = template.format(text=text, response_format=response_format)
    result = _call_llm_parsed(prompt, _FULL_LOG_FIELDS, max_retries)
    if result is None:
        return {f: "" for f in _FULL_LOG_FIELDS}

    # Normalise weight: the prompt requests a numeric-only string but some
    # LLMs include a trailing unit (e.g. "100 kg").  Extract the leading
    # numeric token so callers always receive a pure number.
    weight_raw = result.get("weight", "").strip()
    if weight_raw:
        m = _WEIGHT_NUM_RE.match(weight_raw)
        result["weight"] = m.group(1) if m else weight_raw

    return result


def identify_exercise(exercise: str) -> str:
    """
    Ask the LLM to correct a possibly-garbled exercise name.

    Returns the corrected name, or *exercise* unchanged if the LLM is
    unavailable or returns an empty result.
    """
    cfg = get_config()
    response_format: str = cfg["llm"].get("response_format", "json")
    max_retries: int = cfg["llm"].get("max_retries", 2)
    template: str = cfg["prompts"].get("identify_exercise_prompt", "")
    if not template:
        return exercise

    prompt = template.format(exercise=exercise, response_format=response_format)
    result = _call_llm_parsed(prompt, _EXERCISE_FIELDS, max_retries)
    if result is None:
        return exercise
    return result.get("exercise") or exercise


def sets_reps_notes(remainder: str) -> Dict[str, str]:
    """
    Ask the LLM to extract sets, reps, and notes from the text that follows
    the weight token in a successfully-parsed log entry.

    Returns a dict with keys: sets, reps, notes.
    """
    cfg = get_config()
    response_format: str = cfg["llm"].get("response_format", "json")
    max_retries: int = cfg["llm"].get("max_retries", 2)
    template: str = cfg["prompts"].get("sets_reps_notes_prompt", "")
    if not template:
        return {f: "" for f in _SETS_REPS_FIELDS}

    prompt = template.format(remainder=remainder, response_format=response_format)
    result = _call_llm_parsed(prompt, _SETS_REPS_FIELDS, max_retries)
    if result is None:
        return {f: "" for f in _SETS_REPS_FIELDS}
    return result

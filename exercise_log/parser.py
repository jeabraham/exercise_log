"""
parser.py – parse exercise-log rows from the input CSV and write results to
the output CSV.

Input CSV format (produced by the Siri shortcut):
    <ISO-8601 timestamp>, <free-text description>

Output CSV fields:
    timestamp, exercise, weight, units, lb-weight, reps, sets, notes,
    original text

For this phase:
 * The timestamp is treated as a unique ID for deduplication.
 * A weight unit keyword is located in the free-text description.
 * The numeric value immediately before the unit is the weight.
 * Everything before that value is the exercise name.
 * Everything after the unit keyword is stored as "notes" (remainder) for
   future LLM-based parsing.
 * Weights given in kg / kilograms are converted to pounds.
 * reps and sets are left empty until a later phase.
"""

import csv
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from word2number import w2n

from exercise_log.config import OUTPUT_FIELDS
from exercise_log.llm import full_log_parse, identify_exercise, sets_reps_notes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex for recognising weight-unit keywords (order matters – longer tokens
# first so that "pounds" matches before "pound").
_UNIT_RE = (
    r"(?P<unit>"
    r"kilograms?|kgs?"
    r"|pounds?|lbs?\.?"
    r"|£"
    r")"
)

# Numeric value patterns, from most-specific to least-specific:
#   1. Mixed number / fraction  e.g. "27 1/2"
#   2. Decimal                  e.g. "22.5"
#   3. Integer                  e.g. "22"
_NUM_RE = r"(?P<num>\d+\s+\d+/\d+|\d+\.?\d*)"

# Full weight pattern: <exercise text> <number> [space] <unit> <remainder>
# Non-greedy first group so we match the *first* weight unit found.
_WEIGHT_PATTERN = re.compile(
    r"^(?P<exercise>.+?)\s+" + _NUM_RE + r"\s*" + _UNIT_RE + r"(?P<remainder>.*)",
    re.IGNORECASE | re.DOTALL,
)

# Standalone unit pattern (for word-number fallback).
_UNIT_ONLY_PATTERN = re.compile(r"\b" + _UNIT_RE + r"\b", re.IGNORECASE)

# KG → LB conversion factor
_KG_TO_LB = 2.20462


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _parse_fraction(s: str) -> Optional[float]:
    """Return float for a mixed-number string like '27 1/2', else None."""
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", s.strip())
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den == 0:
            return None
        return whole + num / den
    return None


def _parse_number(s: str) -> Optional[float]:
    """
    Parse a numeric string that may be:
      - a mixed fraction  ("27 1/2")
      - a decimal / integer ("22", "22.5")
      - a word number ("twenty", "twenty two")
    Returns None if parsing fails.
    """
    s = s.strip()

    frac = _parse_fraction(s)
    if frac is not None:
        return frac

    try:
        return float(s)
    except ValueError:
        pass

    try:
        return float(w2n.word_to_num(s))
    except ValueError:
        pass

    return None


def _normalize_unit(raw: str) -> str:
    """Return canonical unit string: 'kg' or 'lb'."""
    raw = raw.lower().rstrip(".")
    if raw in ("kg", "kgs", "kilogram", "kilograms"):
        return "kg"
    return "lb"


def _to_pounds(value: float, unit: str) -> float:
    """Convert *value* in *unit* to pounds."""
    if unit == "kg":
        return value * _KG_TO_LB
    return value


def _format_weight(value: float) -> str:
    """Format a weight value for the output CSV (avoid trailing zeros)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Row-level parser
# ---------------------------------------------------------------------------

def parse_row(row: List[str]) -> Dict[str, str]:
    """
    Parse one CSV row (a list of strings) into the output field dictionary.

    The first element is the ISO-8601 timestamp; remaining elements are
    joined back into the original free-text description.
    """
    timestamp = row[0].strip() if row else ""
    original_text = ", ".join(col.strip() for col in row[1:]) if len(row) > 1 else ""

    result: Dict[str, str] = {
        "timestamp": timestamp,
        "exercise": "",
        "weight": "",
        "units": "",
        "lb-weight": "",
        "reps": "",
        "sets": "",
        "notes": "",
        "original text": original_text,
    }

    if not original_text:
        return result

    # --- Attempt 1: numeric value immediately before unit ---
    m = _WEIGHT_PATTERN.match(original_text)
    if m:
        exercise = m.group("exercise").strip()
        weight_str = m.group("num").strip()
        unit_raw = m.group("unit")
        remainder = m.group("remainder").strip()

        weight_val = _parse_number(weight_str)
        if weight_val is not None:
            unit = _normalize_unit(unit_raw)
            lb_weight = _to_pounds(weight_val, unit)
            result["exercise"] = exercise
            result["weight"] = weight_str
            result["units"] = unit
            result["lb-weight"] = _format_weight(lb_weight)
            # LLM: correct exercise name and extract sets/reps/notes
            result["exercise"] = identify_exercise(exercise)
            srn = sets_reps_notes(remainder)
            result["sets"] = srn.get("sets", "")
            result["reps"] = srn.get("reps", "")
            result["notes"] = srn.get("notes", "") or remainder
            return result

    # --- Attempt 2: word number immediately before unit ---
    unit_match = _UNIT_ONLY_PATTERN.search(original_text)
    if unit_match:
        before_unit = original_text[: unit_match.start()].strip()
        after_unit = original_text[unit_match.end() :].strip()
        unit_raw = unit_match.group("unit")

        words = before_unit.split()
        # Try progressively longer word sequences from the end of before_unit.
        for window in range(1, len(words) + 1):
            candidate_words = words[-window:]
            candidate = " ".join(candidate_words)
            num_val = _parse_number(candidate)
            if num_val is not None:
                exercise = " ".join(words[: len(words) - window]).strip()
                unit = _normalize_unit(unit_raw)
                lb_weight = _to_pounds(num_val, unit)
                result["exercise"] = exercise
                result["weight"] = candidate
                result["units"] = unit
                result["lb-weight"] = _format_weight(lb_weight)
                # LLM: correct exercise name and extract sets/reps/notes
                result["exercise"] = identify_exercise(exercise)
                srn = sets_reps_notes(after_unit)
                result["sets"] = srn.get("sets", "")
                result["reps"] = srn.get("reps", "")
                result["notes"] = srn.get("notes", "") or after_unit
                return result

    # --- No weight found: fall back to full LLM parse ---
    llm_result = full_log_parse(original_text)
    if llm_result.get("weight") and llm_result.get("units"):
        # LLM successfully identified weight and units.
        weight_str = llm_result["weight"]
        unit = _normalize_unit(llm_result["units"])
        weight_val = _parse_number(weight_str)
        if weight_val is not None:
            lb_weight = _to_pounds(weight_val, unit)
            result["exercise"] = llm_result.get("exercise", "")
            result["weight"] = weight_str
            result["units"] = unit
            result["lb-weight"] = _format_weight(lb_weight)
            result["reps"] = llm_result.get("reps", "")
            result["sets"] = llm_result.get("sets", "")
            result["notes"] = llm_result.get("notes", "")
            return result

    # If LLM provided useful fields, use them; otherwise use original_text
    if llm_result:
        result["exercise"] = llm_result.get("exercise", "")
        result["reps"] = llm_result.get("reps", "")
        result["sets"] = llm_result.get("sets", "")
        result["notes"] = llm_result.get("notes", "")
        if not result["weight"]:
            result["weight"] = llm_result.get("weight", "")
        if not result["units"]:
            result["units"] = llm_result.get("units", "")
    else:
        result["exercise"] = original_text
    return result


# ---------------------------------------------------------------------------
# CSV I/O helpers
# ---------------------------------------------------------------------------

def _load_seen_timestamps(output_path: Path) -> Set[str]:
    """
    Read the output CSV (if it exists) and return the set of already-processed
    timestamps so that reruns do not produce duplicate rows.
    """
    seen: Set[str] = set()
    if not output_path.exists():
        return seen
    try:
        with output_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ts = row.get("timestamp", "").strip()
                if ts:
                    seen.add(ts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read output file %s: %s", output_path, exc)
    return seen


def _ensure_output_header(output_path: Path) -> None:
    """Write the CSV header row if the output file does not yet exist."""
    if not output_path.exists():
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()


def process_input_csv(input_path: Path, output_path: Path) -> int:
    """
    Read *input_path*, find rows whose timestamps have not yet been written
    to *output_path*, parse them, and append the results.

    Returns the number of new rows written.
    """
    seen = _load_seen_timestamps(output_path)
    _ensure_output_header(output_path)

    new_rows: List[Dict[str, str]] = []
    try:
        with input_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            for raw_row in reader:
                if not raw_row:
                    continue
                timestamp = raw_row[0].strip()
                if not timestamp or timestamp in seen:
                    continue
                parsed = parse_row(raw_row)
                new_rows.append(parsed)
                seen.add(timestamp)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read input file %s: %s", input_path, exc)
        return 0

    if not new_rows:
        logger.debug("No new rows found in %s", input_path)
        return 0

    with output_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        writer.writerows(new_rows)

    logger.info("Wrote %d new row(s) to %s", len(new_rows), output_path)
    return len(new_rows)

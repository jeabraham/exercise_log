"""
Tests for exercise_log.parser.

Each test row is taken from (or derived from) the problem-statement examples.
The goal is to verify:
  * Timestamp extraction
  * Exercise-name extraction
  * Weight value and unit parsing (including fractions)
  * Unit conversion from kg to lb
  * Rows with no weight unit are handled gracefully
  * Word-number weights are recognised
  * CSV round-trip: process_input_csv produces a correctly structured file
"""

import csv
import io
from pathlib import Path

import pytest

from exercise_log.parser import (
    _normalize_unit,
    _parse_fraction,
    _parse_number,
    _to_pounds,
    parse_row,
    process_input_csv,
)


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestParseFraction:
    def test_simple_fraction(self):
        assert _parse_fraction("27 1/2") == pytest.approx(27.5)

    def test_thirds(self):
        assert _parse_fraction("1 1/3") == pytest.approx(1 + 1 / 3)

    def test_plain_integer_returns_none(self):
        assert _parse_fraction("22") is None

    def test_empty_string_returns_none(self):
        assert _parse_fraction("") is None

    def test_zero_denominator_returns_none(self):
        assert _parse_fraction("5 1/0") is None


class TestParseNumber:
    def test_integer(self):
        assert _parse_number("22") == 22.0

    def test_decimal(self):
        assert _parse_number("22.5") == pytest.approx(22.5)

    def test_fraction(self):
        assert _parse_number("27 1/2") == pytest.approx(27.5)

    def test_word_number_simple(self):
        assert _parse_number("twenty") == 20.0

    def test_word_number_compound(self):
        assert _parse_number("twenty two") == 22.0

    def test_word_number_eighty_seven(self):
        assert _parse_number("eighty seven") == 87.0

    def test_invalid_returns_none(self):
        assert _parse_number("banana") is None


class TestNormalizeUnit:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("kg", "kg"),
            ("kgs", "kg"),
            ("KG", "kg"),
            ("kilogram", "kg"),
            ("kilograms", "kg"),
            ("Kilograms", "kg"),
            ("lb", "lb"),
            ("lbs", "lb"),
            ("lbs.", "lb"),
            ("LBS", "lb"),
            ("pound", "lb"),
            ("pounds", "lb"),
            ("£", "lb"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_unit(raw) == expected


class TestToPounds:
    def test_lb_unchanged(self):
        assert _to_pounds(100.0, "lb") == pytest.approx(100.0)

    def test_kg_conversion(self):
        assert _to_pounds(10.0, "kg") == pytest.approx(22.0462)


# ---------------------------------------------------------------------------
# Integration tests for parse_row
# ---------------------------------------------------------------------------


class TestParseRow:
    """Test parse_row with real examples from the problem statement."""

    def _row(self, timestamp, text):
        return [timestamp, text]

    def test_no_text(self):
        r = parse_row(["2026-03-09T12:28:58-06:00"])
        assert r["timestamp"] == "2026-03-09T12:28:58-06:00"
        assert r["exercise"] == ""
        assert r["weight"] == ""

    def test_no_weight_unit(self):
        """Shoulder press row has no weight – exercise should be the full text."""
        r = parse_row(self._row("2026-03-09T12:28:58-06:00", "Shoulder press"))
        assert r["timestamp"] == "2026-03-09T12:28:58-06:00"
        assert r["exercise"] == "Shoulder press"
        assert r["weight"] == ""
        assert r["units"] == ""
        assert r["lb-weight"] == ""

    def test_pound_symbol(self):
        """'Step ups 20 £.03' – £ used as unit, weight is 20."""
        r = parse_row(self._row("2026-03-09T12:44:38-06:00", "Step ups 20 £.03"))
        assert r["exercise"] == "Step ups"
        assert r["weight"] == "20"
        assert r["units"] == "lb"
        assert r["lb-weight"] == "20"
        assert r["notes"] == ".03"

    def test_fraction_weight(self):
        """'27 1/2 pounds' should yield weight=27.5."""
        r = parse_row(
            self._row("2026-03-09T12:53:06-06:00", "Upright Road 27 1/2 pounds 3×12")
        )
        assert r["exercise"] == "Upright Road"
        assert r["weight"] == "27 1/2"
        assert r["units"] == "lb"
        assert float(r["lb-weight"]) == pytest.approx(27.5)
        assert "3×12" in r["notes"]

    def test_integer_pounds(self):
        r = parse_row(
            self._row(
                "2026-03-09T19:10:43-06:00",
                "I feel like I could eat shoulder press 87 pounds 5×10 reps",
            )
        )
        assert r["exercise"] == "I feel like I could eat shoulder press"
        assert r["weight"] == "87"
        assert r["units"] == "lb"
        assert r["lb-weight"] == "87"
        assert "5×10 reps" in r["notes"]

    def test_lbs_abbreviation(self):
        r = parse_row(
            self._row(
                "2026-03-18T13:03:19-06:00",
                "Tricep push downs 22 lbs. 3×12",
            )
        )
        assert r["exercise"] == "Tricep push downs"
        assert r["weight"] == "22"
        assert r["units"] == "lb"
        assert r["lb-weight"] == "22"
        assert "3×12" in r["notes"]

    def test_incline_bench(self):
        r = parse_row(
            self._row("2026-03-11T11:21:30-06:00", "Incline bench press 25 pounds three times")
        )
        assert r["exercise"] == "Incline bench press"
        assert r["weight"] == "25"
        assert r["units"] == "lb"
        assert r["lb-weight"] == "25"

    def test_goblet_squat(self):
        r = parse_row(
            self._row("2026-03-18T12:42:56-06:00", "Goblet squats 30 pounds 3×15")
        )
        assert r["exercise"] == "Goblet squats"
        assert r["weight"] == "30"
        assert r["units"] == "lb"
        assert r["lb-weight"] == "30"

    def test_kg_conversion(self):
        """Verify kg to lb conversion."""
        r = parse_row(self._row("2026-01-01T00:00:00-00:00", "Bench press 100 kg 3x10"))
        assert r["weight"] == "100"
        assert r["units"] == "kg"
        assert float(r["lb-weight"]) == pytest.approx(220.462, abs=0.01)

    def test_kilograms_unit(self):
        r = parse_row(self._row("2026-01-01T00:00:01-00:00", "Squat 60 kilograms 4x8"))
        assert r["units"] == "kg"
        assert float(r["lb-weight"]) == pytest.approx(60 * 2.20462, abs=0.01)

    def test_word_number_weight(self):
        """'twenty pounds' should be recognised as weight=20."""
        r = parse_row(
            self._row("2026-01-01T00:00:02-00:00", "Curl twenty pounds 3x12")
        )
        assert r["units"] == "lb"
        assert float(r["lb-weight"]) == pytest.approx(20.0)

    def test_original_text_preserved(self):
        raw = "Romanian dead lift 30 pounds 3×12"
        r = parse_row(self._row("2026-03-18T12:20:52-06:00", raw))
        assert r["original text"] == raw

    def test_multi_field_input_row(self):
        """CSV rows with more than two fields still work."""
        r = parse_row(["2026-03-18T12:20:52-06:00", "Romanian dead lift 30", "pounds 3×12"])
        assert r["original text"] == "Romanian dead lift 30, pounds 3×12"


# ---------------------------------------------------------------------------
# CSV round-trip test
# ---------------------------------------------------------------------------


class TestProcessInputCsv:
    def _write_input(self, path: Path, rows):
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            for row in rows:
                writer.writerow(row)

    def test_basic_round_trip(self, tmp_path):
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"

        self._write_input(
            input_csv,
            [
                ["2026-03-18T12:20:52-06:00", "Romanian dead lift 30 pounds 3×12"],
                ["2026-03-18T12:28:23-06:00", "Incline chest press 25 pounds 3×12"],
                ["2026-03-18T12:54:18-06:00", "Gumball pullovers"],
            ],
        )

        n = process_input_csv(input_csv, output_csv)
        assert n == 3

        with output_csv.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        assert len(rows) == 3
        assert rows[0]["exercise"] == "Romanian dead lift"
        assert rows[0]["weight"] == "30"
        assert rows[0]["units"] == "lb"
        assert rows[1]["exercise"] == "Incline chest press"
        assert rows[2]["exercise"] == "Gumball pullovers"
        assert rows[2]["weight"] == ""

    def test_deduplication(self, tmp_path):
        """Running process_input_csv twice must not add duplicate rows."""
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"

        self._write_input(
            input_csv,
            [["2026-03-18T12:20:52-06:00", "Squat 100 kg 5x5"]],
        )

        process_input_csv(input_csv, output_csv)
        n = process_input_csv(input_csv, output_csv)
        assert n == 0  # second call should add nothing

        with output_csv.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1

    def test_new_rows_appended(self, tmp_path):
        """Adding new rows to the input CSV should only write the new ones."""
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"

        self._write_input(
            input_csv,
            [["2026-03-18T12:20:52-06:00", "Deadlift 80 kg 3x5"]],
        )
        process_input_csv(input_csv, output_csv)

        # Append a new row to the input
        self._write_input(
            input_csv,
            [
                ["2026-03-18T12:20:52-06:00", "Deadlift 80 kg 3x5"],
                ["2026-03-18T13:00:00-06:00", "Bench 60 kg 3x8"],
            ],
        )
        n = process_input_csv(input_csv, output_csv)
        assert n == 1

        with output_csv.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        assert rows[1]["exercise"] == "Bench"

    def test_empty_input(self, tmp_path):
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"
        input_csv.write_text("", encoding="utf-8")

        n = process_input_csv(input_csv, output_csv)
        assert n == 0
        # Header should still be created
        assert output_csv.exists()
        with output_csv.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
        assert "timestamp" in header
        assert "exercise" in header
        assert "original text" in header

    def test_full_example_set(self, tmp_path):
        """Smoke-test with all problem-statement example rows."""
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"

        example_rows = [
            ["2026-03-09T12:28:58-06:00", "Shoulder press"],
            ["2026-03-09T12:44:38-06:00", "Step ups 20 £.03"],
            ["2026-03-09T12:53:06-06:00", "Upright Road 27 1/2 pounds 3×12"],
            ["2026-03-09T13:00:11-06:00", "Single weight hip thrust three sets"],
            ["2026-03-09T19:10:43-06:00", "I feel like I could eat shoulder press 87 pounds 5×10 reps"],
            ["2026-03-11T11:21:30-06:00", "Incline bench press 25 pounds three times"],
            ["2026-03-11T11:31:25-06:00", "Goblet got 25 pounds 3×15"],
            ["2026-03-11T11:44:14-06:00", "Dumbbell pull over 25 pounds 3×15"],
            ["2026-03-11T11:55:11-06:00", "Face pole 27 1/2 pounds 3×15"],
            ["2026-03-16T12:10:48-06:00", "Play Christmas Time"],
            ["2026-03-16T12:39:59-06:00", "Step up 20 pounds 3×12"],
            ["2026-03-16T12:48:50-06:00", "That pulled down 93 pounds 3×10"],
            ["2026-03-18T12:20:52-06:00", "Romanian dead lift 30 pounds 3×12"],
            ["2026-03-18T12:28:23-06:00", "Incline chest press 25 pounds 3×12"],
            ["2026-03-18T12:42:56-06:00", "Goblet squats 30 pounds 3×15"],
            ["2026-03-18T12:54:18-06:00", "Gumball pullovers"],
            ["2026-03-18T13:02:57-06:00", "Face pulls 33 pounds 3×15"],
            ["2026-03-18T13:03:19-06:00", "Tricep push downs 22 lbs. 3×12"],
        ]

        self._write_input(input_csv, example_rows)
        n = process_input_csv(input_csv, output_csv)
        assert n == len(example_rows)

        with output_csv.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        assert len(rows) == len(example_rows)

        # Rows WITH weight should have lb-weight populated
        weighted_rows = [r for r in rows if r["weight"]]
        for r in weighted_rows:
            assert r["lb-weight"] != "", f"lb-weight missing for: {r['original text']}"

        # Rows WITHOUT weight unit should have exercise == original text
        no_weight_timestamps = {
            "2026-03-09T12:28:58-06:00",  # Shoulder press
            "2026-03-09T13:00:11-06:00",  # Single weight hip thrust three sets
            "2026-03-16T12:10:48-06:00",  # Play Christmas Time
            "2026-03-18T12:54:18-06:00",  # Gumball pullovers
        }
        for r in rows:
            if r["timestamp"] in no_weight_timestamps:
                assert r["weight"] == "", f"Unexpected weight for: {r['original text']}"

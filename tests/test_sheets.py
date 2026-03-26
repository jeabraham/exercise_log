"""
Tests for exercise_log.sheets and the Google Sheets integration path in
exercise_log.config and exercise_log.__main__.

Unit tests use mocking to avoid requiring a live Google Sheets connection.
Integration tests (marked with ``sheets_integration``) write to and read
from the real sheet configured in config.yaml / configuration.json.

Running
-------
    # All tests (unit only, skipping integration):
    pytest tests/test_sheets.py

    # Unit + live integration tests (requires Google Sheets credentials):
    pytest tests/test_sheets.py -m sheets_integration

    # Or run this file directly:
    python tests/test_sheets.py
"""

import csv
import io
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, call, patch

import pytest

from exercise_log.sheets import (
    _build_update_range,
    _extract_spreadsheet_id,
    _find_first_empty_row,
    _resolve_auth_path,
    _to_sheet_value,
    append_rows_to_sheet,
    load_existing_timestamps,
    process_input_csv_to_sheet,
)
from exercise_log.config import load_sheets_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

# Resolve the auth file path from config.yaml so that any credentials file
# named in `authorization:` is used (not just the hardcoded configuration.json).
def _resolve_sheets_config():
    """Return the sheets config dict (from config.yaml) or None."""
    try:
        from exercise_log.config import load_sheets_config
        return load_sheets_config(_CONFIG_PATH)
    except Exception:
        return None

_LIVE_SHEETS_CONFIG = _resolve_sheets_config()

# For unit tests that don't need real credentials, keep a convenient fixture.
_SHEET_CONFIG = {
    "sheet_link": "https://docs.google.com/spreadsheets/d/1018gxdlQd_CGn-DidfDmVud2prcNAplomnMokbZrRdE/edit?usp=drivesdk",
    "authorization": str(Path(__file__).parent.parent / "exercise_log" / "configuration.json"),
    "range": "RawLog!A:I",
    "timestamp": "RawLog!A",
}

_SPREADSHEET_ID = "1018gxdlQd_CGn-DidfDmVud2prcNAplomnMokbZrRdE"


# ---------------------------------------------------------------------------
# Unit tests: _extract_spreadsheet_id
# ---------------------------------------------------------------------------


class TestExtractSpreadsheetId:
    def test_standard_url(self):
        url = "https://docs.google.com/spreadsheets/d/abc123/edit?usp=sharing"
        assert _extract_spreadsheet_id(url) == "abc123"

    def test_url_with_long_id(self):
        url = (
            "https://docs.google.com/spreadsheets/d/"
            "1018gxdlQd_CGn-DidfDmVud2prcNAplomnMokbZrRdE/edit?usp=drivesdk"
        )
        assert _extract_spreadsheet_id(url) == "1018gxdlQd_CGn-DidfDmVud2prcNAplomnMokbZrRdE"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot extract spreadsheet ID"):
            _extract_spreadsheet_id("https://example.com/not-a-sheet")


# ---------------------------------------------------------------------------
# Unit tests: _resolve_auth_path
# ---------------------------------------------------------------------------


class TestResolveAuthPath:
    def test_absolute_path_unchanged(self, tmp_path):
        abs_path = str(tmp_path / "key.json")
        # Even if the file doesn't exist, absolute paths are returned as-is.
        assert _resolve_auth_path(abs_path) == abs_path

    def test_relative_path_resolved_to_cwd(self, tmp_path, monkeypatch):
        key_file = tmp_path / "key.json"
        key_file.write_text("{}")
        monkeypatch.chdir(tmp_path)
        result = _resolve_auth_path("key.json")
        assert result == str(key_file)

    def test_relative_path_resolved_to_package(self, monkeypatch):
        # The real configuration.json lives next to the package modules.
        _pkg_auth = Path(__file__).parent.parent / "exercise_log" / "configuration.json"
        if not _pkg_auth.exists():
            pytest.skip("configuration.json not present in package directory")
        result = _resolve_auth_path("configuration.json")
        assert Path(result).exists()


# ---------------------------------------------------------------------------
# Unit tests: load_sheets_config
# ---------------------------------------------------------------------------


class TestLoadSheetsConfig:
    def test_loads_from_config_yaml(self):
        if not _CONFIG_PATH.exists():
            pytest.skip("config.yaml not present at repo root")
        cfg = load_sheets_config(_CONFIG_PATH)
        assert cfg is not None
        assert "docs.google.com/spreadsheets" in cfg["sheet_link"]
        assert cfg["authorization"]  # any non-empty path is valid
        assert cfg["range"] == "RawLog!A:I"
        assert cfg["timestamp"] == "RawLog!A:A"

    def test_missing_file_returns_none(self, tmp_path):
        cfg = load_sheets_config(tmp_path / "nonexistent.yaml")
        assert cfg is None

    def test_no_sheets_section_returns_none(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("llm:\n  enabled: false\n", encoding="utf-8")
        assert load_sheets_config(cfg_file) is None

    def test_partial_sheets_section_returns_none(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "sheets:\n  sheet_link: https://example.com\n", encoding="utf-8"
        )
        assert load_sheets_config(cfg_file) is None

    def test_complete_sheets_section(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "sheets:\n"
            "  sheet_link: https://docs.google.com/spreadsheets/d/SHEET_ID/edit\n"
            "  authorization: /tmp/key.json\n"
            "  range: RawLog!A:I\n"
            "  timestamp: RawLog!A\n",
            encoding="utf-8",
        )
        cfg = load_sheets_config(cfg_file)
        assert cfg is not None
        assert cfg["sheet_link"] == "https://docs.google.com/spreadsheets/d/SHEET_ID/edit"
        assert cfg["authorization"] == "/tmp/key.json"
        assert cfg["range"] == "RawLog!A:I"
        assert cfg["timestamp"] == "RawLog!A"


# ---------------------------------------------------------------------------
# Unit tests: load_existing_timestamps (mocked)
# ---------------------------------------------------------------------------


class TestLoadExistingTimestampsMocked:
    def _make_service(self, values):
        """Build a mock service whose values().get().execute() returns values."""
        execute_mock = MagicMock(return_value={"values": values})
        get_mock = MagicMock(return_value=MagicMock(execute=execute_mock))
        values_mock = MagicMock(return_value=MagicMock(get=get_mock))
        service = MagicMock()
        service.spreadsheets.return_value.values.return_value.get.return_value.execute = (
            execute_mock
        )
        service.spreadsheets.return_value.values.return_value.get = get_mock
        return service

    @patch("exercise_log.sheets._build_service")
    def test_returns_set_of_timestamps(self, mock_build):
        mock_service = self._make_service(
            [["2026-03-18T12:20:52-06:00"], ["2026-03-18T13:00:00-06:00"]]
        )
        mock_build.return_value = mock_service

        result = load_existing_timestamps(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A",
        )
        assert result == {
            "2026-03-18T12:20:52-06:00",
            "2026-03-18T13:00:00-06:00",
        }

    @patch("exercise_log.sheets._build_service")
    def test_empty_sheet_returns_empty_set(self, mock_build):
        mock_service = self._make_service([])
        mock_build.return_value = mock_service

        result = load_existing_timestamps(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A",
        )
        assert result == set()

    @patch("exercise_log.sheets._build_service")
    def test_api_error_returns_empty_set(self, mock_build):
        service = MagicMock()
        service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = (
            Exception("network error")
        )
        mock_build.return_value = service

        result = load_existing_timestamps(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A",
        )
        assert result == set()


# ---------------------------------------------------------------------------
# Unit tests: _to_sheet_value (numeric conversion)
# ---------------------------------------------------------------------------


class TestToSheetValue:
    def test_integer_weight_converted(self):
        assert _to_sheet_value("weight", "20") == 20
        assert isinstance(_to_sheet_value("weight", "20"), int)

    def test_float_weight_converted(self):
        assert _to_sheet_value("weight", "27.5") == 27.5
        assert isinstance(_to_sheet_value("weight", "27.5"), float)

    def test_lb_weight_converted(self):
        assert _to_sheet_value("lb-weight", "44") == 44

    def test_reps_converted(self):
        assert _to_sheet_value("reps", "12") == 12

    def test_sets_converted(self):
        assert _to_sheet_value("sets", "3") == 3

    def test_empty_numeric_field_stays_empty(self):
        assert _to_sheet_value("weight", "") == ""
        assert _to_sheet_value("reps", "") == ""

    def test_text_field_unchanged(self):
        assert _to_sheet_value("exercise", "Bench Press") == "Bench Press"
        assert _to_sheet_value("timestamp", "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
        assert _to_sheet_value("notes", "some note") == "some note"

    def test_numeric_string_in_text_field_stays_string(self):
        # "original text" should never be coerced to a number
        assert _to_sheet_value("original text", "30") == "30"

    def test_non_numeric_value_in_numeric_field_stays_string(self):
        assert _to_sheet_value("weight", "n/a") == "n/a"


# ---------------------------------------------------------------------------
# Unit tests: _build_update_range
# ---------------------------------------------------------------------------


class TestBuildUpdateRange:
    def test_basic(self):
        assert _build_update_range("RawLog!A:I", 11, 3) == "RawLog!A11:I13"

    def test_single_row(self):
        assert _build_update_range("RawLog!A:I", 5, 1) == "RawLog!A5:I5"

    def test_no_sheet_name(self):
        assert _build_update_range("A:I", 2, 2) == "A2:I3"

    def test_single_column_range(self):
        assert _build_update_range("Sheet1!A", 3, 2) == "Sheet1!A3:A4"


# ---------------------------------------------------------------------------
# Unit tests: _find_first_empty_row
# ---------------------------------------------------------------------------


class TestFindFirstEmptyRow:
    def _make_service(self, values):
        service = MagicMock()
        (
            service.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.return_value
        ) = {"values": values}
        return service

    def test_first_data_row_empty(self):
        # Row 1 header, row 2 empty → first_empty = 2
        service = self._make_service([["Timestamp"], []])
        assert _find_first_empty_row(service, "SID", "RawLog!A") == 2

    def test_empty_in_middle(self):
        # Row 1 header, rows 2-3 data, row 4 empty → first_empty = 4
        service = self._make_service([
            ["Timestamp"],
            ["2026-01-01"],
            ["2026-01-02"],
            [],
        ])
        assert _find_first_empty_row(service, "SID", "RawLog!A") == 4

    def test_all_rows_filled_returns_next_row(self):
        # 3 returned rows (header + 2 data) → first_empty = 4
        service = self._make_service([
            ["Timestamp"],
            ["2026-01-01"],
            ["2026-01-02"],
        ])
        assert _find_first_empty_row(service, "SID", "RawLog!A") == 4

    def test_no_rows_returned_returns_1(self):
        # Completely empty sheet (not even a header returned) → start at row 1.
        service = self._make_service([])
        assert _find_first_empty_row(service, "SID", "RawLog!A") == 1

    def test_api_error_returns_none(self):
        service = MagicMock()
        (
            service.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.side_effect
        ) = Exception("network error")
        assert _find_first_empty_row(service, "SID", "RawLog!A") is None


# ---------------------------------------------------------------------------
# Unit tests: append_rows_to_sheet (mocked)
# ---------------------------------------------------------------------------


class TestAppendRowsToSheetMocked:
    @patch("exercise_log.sheets._build_service")
    def test_insert_fallback_when_no_timestamp_range(self, mock_build):
        """Without timestamp_range, should fall back to append (INSERT_ROWS)."""
        service = MagicMock()
        mock_build.return_value = service

        rows = [
            {
                "timestamp": "2026-03-18T12:20:52-06:00",
                "exercise": "Romanian Deadlift",
                "weight": "30",
                "units": "lb",
                "lb-weight": "30",
                "reps": "12",
                "sets": "3",
                "notes": "",
                "original text": "Romanian dead lift 30 pounds 3×12",
            }
        ]
        from exercise_log.config import OUTPUT_FIELDS

        n = append_rows_to_sheet(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A:I",
            rows,
            OUTPUT_FIELDS,
        )
        assert n == 1

        append_call = service.spreadsheets.return_value.values.return_value.append
        assert append_call.called
        kwargs = append_call.call_args.kwargs
        assert kwargs["spreadsheetId"] == "SHEET_ID"
        assert kwargs["range"] == "RawLog!A:I"
        assert kwargs["valueInputOption"] == "RAW"
        assert kwargs["insertDataOption"] == "INSERT_ROWS"
        expected_values = [
            [
                "2026-03-18T12:20:52-06:00",
                "Romanian Deadlift",
                30,
                "lb",
                30,
                12,
                3,
                "",
                "Romanian dead lift 30 pounds 3×12",
            ]
        ]
        assert kwargs["body"] == {"values": expected_values}

    # Keep the old name as an alias so no history is lost.
    test_appends_correct_values = test_insert_fallback_when_no_timestamp_range

    @patch("exercise_log.sheets._build_service")
    def test_uses_update_when_empty_row_found(self, mock_build):
        """With timestamp_range, rows should be written via update() to the first empty row."""
        service = MagicMock()
        # Simulate timestamp column: header + 2 data rows → first empty = row 4.
        (
            service.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.return_value
        ) = {"values": [["Timestamp"], ["2026-01-01"], ["2026-01-02"]]}
        mock_build.return_value = service

        from exercise_log.config import OUTPUT_FIELDS

        rows = [{"timestamp": "2026-01-03", "exercise": "Squat", "weight": "100",
                 "units": "kg", "lb-weight": "220", "reps": "5", "sets": "3",
                 "notes": "", "original text": "Squat 100 kg 3x5"}]
        n = append_rows_to_sheet(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A:I",
            rows,
            OUTPUT_FIELDS,
            timestamp_range="RawLog!A",
        )
        assert n == 1

        update_call = service.spreadsheets.return_value.values.return_value.update
        assert update_call.called
        kwargs = update_call.call_args.kwargs
        assert kwargs["range"] == "RawLog!A4:I4"
        assert kwargs["valueInputOption"] == "RAW"
        # append() should NOT have been called
        append_call = service.spreadsheets.return_value.values.return_value.append
        assert not append_call.called

    @patch("exercise_log.sheets._build_service")
    def test_insert_fallback_when_empty_row_detection_fails(self, mock_build):
        """If _find_first_empty_row returns None (API error), fall back to append."""
        service = MagicMock()
        # Make the get() call (used by _find_first_empty_row) raise an exception.
        (
            service.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.side_effect
        ) = Exception("network error")
        mock_build.return_value = service

        from exercise_log.config import OUTPUT_FIELDS

        n = append_rows_to_sheet(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A:I",
            [{"timestamp": "ts1"}],
            OUTPUT_FIELDS,
            timestamp_range="RawLog!A",
        )
        assert n == 1
        append_call = service.spreadsheets.return_value.values.return_value.append
        assert append_call.called

    @patch("exercise_log.sheets._build_service")
    def test_empty_rows_returns_zero(self, mock_build):
        service = MagicMock()
        mock_build.return_value = service

        from exercise_log.config import OUTPUT_FIELDS

        n = append_rows_to_sheet(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A:I",
            [],
            OUTPUT_FIELDS,
        )
        assert n == 0
        service.spreadsheets.assert_not_called()

    @patch("exercise_log.sheets._build_service")
    def test_api_error_returns_zero(self, mock_build):
        service = MagicMock()
        service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = (
            Exception("quota exceeded")
        )
        mock_build.return_value = service

        from exercise_log.config import OUTPUT_FIELDS

        n = append_rows_to_sheet(
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit",
            "/tmp/key.json",
            "RawLog!A:I",
            [{"timestamp": "ts1"}],
            OUTPUT_FIELDS,
        )
        assert n == 0


# ---------------------------------------------------------------------------
# Unit tests: process_input_csv_to_sheet (mocked)
# ---------------------------------------------------------------------------


class TestProcessInputCsvToSheetMocked:
    def _write_input(self, path: Path, rows):
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            for row in rows:
                writer.writerow(row)

    @patch("exercise_log.sheets.append_rows_to_sheet")
    @patch("exercise_log.sheets.load_existing_timestamps")
    def test_new_rows_appended(self, mock_load_ts, mock_append, tmp_path):
        mock_load_ts.return_value = set()
        mock_append.return_value = 2

        input_csv = tmp_path / "input.csv"
        self._write_input(
            input_csv,
            [
                ["2026-03-18T12:20:52-06:00", "Squat 100 kg 3x5"],
                ["2026-03-18T13:00:00-06:00", "Bench 60 kg 3x8"],
            ],
        )

        cfg = {
            "sheet_link": "https://docs.google.com/spreadsheets/d/ID/edit",
            "authorization": "/tmp/key.json",
            "range": "RawLog!A:I",
            "timestamp": "RawLog!A",
        }
        n = process_input_csv_to_sheet(input_csv, cfg)
        assert n == 2
        assert mock_append.called
        # Verify timestamp_range is passed so the overwrite path can be used.
        call_args = mock_append.call_args
        assert call_args.args[5] == "RawLog!A"

    @patch("exercise_log.sheets.append_rows_to_sheet")
    @patch("exercise_log.sheets.load_existing_timestamps")
    def test_already_seen_rows_skipped(self, mock_load_ts, mock_append, tmp_path):
        mock_load_ts.return_value = {"2026-03-18T12:20:52-06:00"}
        mock_append.return_value = 1

        input_csv = tmp_path / "input.csv"
        self._write_input(
            input_csv,
            [
                ["2026-03-18T12:20:52-06:00", "Squat 100 kg 3x5"],
                ["2026-03-18T13:00:00-06:00", "Bench 60 kg 3x8"],
            ],
        )

        cfg = {
            "sheet_link": "https://docs.google.com/spreadsheets/d/ID/edit",
            "authorization": "/tmp/key.json",
            "range": "RawLog!A:I",
            "timestamp": "RawLog!A",
        }
        process_input_csv_to_sheet(input_csv, cfg)

        # Only 1 new row should be passed to append_rows_to_sheet.
        rows_passed = mock_append.call_args[0][3]
        assert len(rows_passed) == 1
        assert rows_passed[0]["timestamp"] == "2026-03-18T13:00:00-06:00"

    @patch("exercise_log.sheets.append_rows_to_sheet")
    @patch("exercise_log.sheets.load_existing_timestamps")
    def test_no_new_rows_returns_zero(self, mock_load_ts, mock_append, tmp_path):
        mock_load_ts.return_value = {"2026-03-18T12:20:52-06:00"}
        mock_append.return_value = 0

        input_csv = tmp_path / "input.csv"
        self._write_input(
            input_csv,
            [["2026-03-18T12:20:52-06:00", "Squat 100 kg 3x5"]],
        )

        cfg = {
            "sheet_link": "https://docs.google.com/spreadsheets/d/ID/edit",
            "authorization": "/tmp/key.json",
            "range": "RawLog!A:I",
            "timestamp": "RawLog!A",
        }
        n = process_input_csv_to_sheet(input_csv, cfg)
        assert n == 0
        mock_append.assert_not_called()


# ---------------------------------------------------------------------------
# Unit tests: __main__ CLI routing (mocked)
# ---------------------------------------------------------------------------


class TestMainCLI:
    @patch("exercise_log.__main__.process_input_csv")
    def test_output_flag_uses_csv(self, mock_process, tmp_path):
        from exercise_log.__main__ import main

        input_csv = tmp_path / "in.csv"
        input_csv.write_text("2026-01-01T00:00:00-00:00,Squat 100 kg\n", encoding="utf-8")
        output_csv = tmp_path / "out.csv"
        mock_process.return_value = 1

        rc = main(["--input", str(input_csv), "--output", str(output_csv), "--once"])
        assert rc == 0
        mock_process.assert_called_once_with(input_csv, output_csv)

    @patch("exercise_log.sheets.process_input_csv_to_sheet")
    @patch("exercise_log.__main__.load_sheets_config")
    def test_no_output_uses_sheets(self, mock_cfg, mock_process_sheet, tmp_path):
        from exercise_log.__main__ import main

        input_csv = tmp_path / "in.csv"
        input_csv.write_text("2026-01-01T00:00:00-00:00,Squat 100 kg\n", encoding="utf-8")
        mock_cfg.return_value = {
            "sheet_link": "https://docs.google.com/spreadsheets/d/ID/edit",
            "authorization": "/tmp/key.json",
            "range": "RawLog!A:I",
            "timestamp": "RawLog!A",
        }
        mock_process_sheet.return_value = 1

        rc = main(["--input", str(input_csv), "--once"])
        assert rc == 0
        mock_process_sheet.assert_called_once()

    @patch("exercise_log.__main__.load_sheets_config")
    def test_no_output_no_sheets_config_returns_error(self, mock_cfg, tmp_path):
        from exercise_log.__main__ import main

        input_csv = tmp_path / "in.csv"
        input_csv.write_text("", encoding="utf-8")
        mock_cfg.return_value = None

        rc = main(["--input", str(input_csv), "--once"])
        assert rc == 1


# ---------------------------------------------------------------------------
# Integration tests (require live Google Sheets connection)
# ---------------------------------------------------------------------------

_sheets_available = (
    _LIVE_SHEETS_CONFIG is not None
    and Path(_LIVE_SHEETS_CONFIG["authorization"]).exists()
)


@pytest.mark.sheets_integration
@pytest.mark.skipif(
    not _sheets_available,
    reason=(
        "Google Sheets integration not configured: "
        "ensure config.yaml has a valid 'sheets:' section "
        "and the 'authorization' key file exists"
    ),
)
class TestSheetsIntegration:
    """
    Live tests against the Google Sheet specified in config.yaml.

    These tests read from and write to the real spreadsheet.  They are
    designed to be idempotent: rows written during a test use timestamps that
    are clearly marked as test data and are NOT re-appended on a second run
    because deduplication prevents it.
    """

    _TEST_TIMESTAMPS = [
        "__TEST__2099-01-01T00:00:00Z",
        "__TEST__2099-01-01T00:00:01Z",
    ]

    def _cleanup(self):
        """Remove test sentinel timestamps from the sheet (best-effort)."""
        # We don't implement deletion in this integration as the Sheets API
        # would require finding and deleting specific rows.  The test
        # timestamps use a ``__TEST__`` prefix so they are easily identified.
        pass  # cleanup is manual / out-of-scope for this integration

    def test_load_existing_timestamps_returns_set(self):
        """Reading existing timestamps from the live sheet should succeed."""
        cfg = _LIVE_SHEETS_CONFIG
        assert cfg is not None, "sheets: config not loaded"

        timestamps = load_existing_timestamps(
            cfg["sheet_link"],
            cfg["authorization"],
            cfg["timestamp"],
        )
        # The result should be a set (may be empty for a fresh sheet).
        assert isinstance(timestamps, set)

    def test_append_and_deduplicate(self, tmp_path):
        """
        Write a test row to the live sheet and confirm that a second call
        with the same timestamp does NOT append a duplicate.

        A UUID suffix ensures each test run uses a fresh timestamp so a
        previously-run test cannot cause a false "0 rows appended" on the
        first call.  Rows written are identifiable by the ``__TEST__`` prefix
        for manual clean-up.
        """
        cfg = _LIVE_SHEETS_CONFIG
        assert cfg is not None

        input_csv = tmp_path / "test_input.csv"
        test_ts = f"__TEST__2099-01-01T00:00:00Z_{uuid.uuid4()}"

        with input_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([test_ts, "Test Exercise 10 lb"])

        # First call – should append exactly 1 row.
        n1 = process_input_csv_to_sheet(input_csv, cfg)
        assert n1 == 1, f"Expected 1 row appended, got {n1}"

        # Second call – the timestamp is already in the sheet; should append 0.
        n2 = process_input_csv_to_sheet(input_csv, cfg)
        assert n2 == 0, f"Expected 0 rows on second call (dedup), got {n2}"


if __name__ == "__main__":
    import pytest
    import sys

    sys.exit(pytest.main([__file__, "-v"]))

"""
Tests for exercise_log.url_watcher and the URL-related parts of config.py
and __main__.py.
"""

import csv
import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from exercise_log.url_watcher import _content_hash, _fetch_url, _process_content, watch_url
from exercise_log.config import load_input_url, load_url_poll_interval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_CSV = b"2024-01-01T10:00:00,bench press 135 lbs 3x10\n"


# ---------------------------------------------------------------------------
# _content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_returns_hex_string(self):
        result = _content_hash(b"hello")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_deterministic(self):
        assert _content_hash(b"abc") == _content_hash(b"abc")

    def test_different_content_different_hash(self):
        assert _content_hash(b"abc") != _content_hash(b"xyz")

    def test_matches_hashlib_directly(self):
        data = b"some csv content"
        expected = hashlib.sha256(data).hexdigest()
        assert _content_hash(data) == expected


# ---------------------------------------------------------------------------
# _fetch_url
# ---------------------------------------------------------------------------


class TestFetchUrl:
    def test_returns_bytes_on_success(self):
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"data"

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = _fetch_url("http://example.com/file.csv")

        assert result == b"data"

    def test_returns_none_on_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = _fetch_url("http://example.com/file.csv")

        assert result is None

    def test_rejects_non_http_scheme(self):
        result = _fetch_url("file:///etc/passwd")
        assert result is None

    def test_rejects_ftp_scheme(self):
        result = _fetch_url("ftp://example.com/file.csv")
        assert result is None

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b""

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            _fetch_url("http://example.com/file.csv", timeout=15)

        mock_open.assert_called_once_with("http://example.com/file.csv", timeout=15)


# ---------------------------------------------------------------------------
# _process_content
# ---------------------------------------------------------------------------


class TestProcessContent:
    def test_calls_process_input_csv_with_temp_file(self, tmp_path):
        output_path = tmp_path / "out.csv"

        with patch("exercise_log.parser.process_input_csv") as mock_parse:
            _process_content(_SAMPLE_CSV, output_path, sheet_config=None)

        mock_parse.assert_called_once()
        tmp_arg = mock_parse.call_args[0][0]
        assert isinstance(tmp_arg, Path)
        # Temp file should be deleted after processing
        assert not tmp_arg.exists()

    def test_temp_file_deleted_even_on_error(self, tmp_path):
        output_path = tmp_path / "out.csv"
        created_tmp: list = []

        def mock_parse(tmp_path_arg, _out):
            created_tmp.append(tmp_path_arg)
            raise RuntimeError("parse error")

        with patch("exercise_log.parser.process_input_csv", mock_parse):
            _process_content(_SAMPLE_CSV, output_path, sheet_config=None)

        assert len(created_tmp) == 1
        assert not created_tmp[0].exists()

    def test_calls_sheets_when_sheet_config_provided(self, tmp_path):
        sheet_cfg = {"sheet_link": "x", "authorization": "y", "range": "z", "timestamp": "t"}

        with patch("exercise_log.sheets.process_input_csv_to_sheet") as mock_sheets:
            _process_content(_SAMPLE_CSV, None, sheet_config=sheet_cfg)

        mock_sheets.assert_called_once()


# ---------------------------------------------------------------------------
# watch_url
# ---------------------------------------------------------------------------


class TestWatchUrl:
    def _make_fetch_side_effect(self, responses):
        """Yield successive responses then raise KeyboardInterrupt."""
        call_count = 0

        def side_effect(url, timeout=30):
            nonlocal call_count
            if call_count >= len(responses):
                raise KeyboardInterrupt
            value = responses[call_count]
            call_count += 1
            return value

        return side_effect

    def test_processes_on_first_fetch(self, tmp_path):
        output_path = tmp_path / "out.csv"

        fetch_calls = [_SAMPLE_CSV]  # one result then stop
        with patch(
            "exercise_log.url_watcher._fetch_url",
            side_effect=self._make_fetch_side_effect(fetch_calls),
        ), patch("exercise_log.url_watcher._process_content") as mock_proc, patch(
            "time.sleep"
        ):
            watch_url("http://example.com/file.csv", output_path, poll_interval=0)

        mock_proc.assert_called_once_with(_SAMPLE_CSV, output_path, None)

    def test_processes_only_when_content_changes(self, tmp_path):
        output_path = tmp_path / "out.csv"
        content_v1 = b"row1\n"
        content_v2 = b"row1\nrow2\n"

        fetch_calls = [content_v1, content_v1, content_v2]
        with patch(
            "exercise_log.url_watcher._fetch_url",
            side_effect=self._make_fetch_side_effect(fetch_calls),
        ), patch("exercise_log.url_watcher._process_content") as mock_proc, patch(
            "time.sleep"
        ):
            watch_url("http://example.com/file.csv", output_path, poll_interval=0)

        # Should have processed content_v1 (first time) and content_v2 (changed)
        assert mock_proc.call_count == 2
        mock_proc.assert_any_call(content_v1, output_path, None)
        mock_proc.assert_any_call(content_v2, output_path, None)

    def test_skips_processing_when_fetch_fails(self, tmp_path):
        output_path = tmp_path / "out.csv"

        fetch_calls = [None]  # failure
        with patch(
            "exercise_log.url_watcher._fetch_url",
            side_effect=self._make_fetch_side_effect(fetch_calls),
        ), patch("exercise_log.url_watcher._process_content") as mock_proc, patch(
            "time.sleep"
        ):
            watch_url("http://example.com/file.csv", output_path, poll_interval=0)

        mock_proc.assert_not_called()

    def test_passes_sheet_config(self, tmp_path):
        sheet_cfg = {"sheet_link": "x", "authorization": "y", "range": "z", "timestamp": "t"}

        fetch_calls = [_SAMPLE_CSV]
        with patch(
            "exercise_log.url_watcher._fetch_url",
            side_effect=self._make_fetch_side_effect(fetch_calls),
        ), patch("exercise_log.url_watcher._process_content") as mock_proc, patch(
            "time.sleep"
        ):
            watch_url(
                "http://example.com/file.csv",
                None,
                poll_interval=0,
                sheet_config=sheet_cfg,
            )

        mock_proc.assert_called_once_with(_SAMPLE_CSV, None, sheet_cfg)


# ---------------------------------------------------------------------------
# load_input_url (config.py)
# ---------------------------------------------------------------------------


class TestLoadInputUrl:
    def test_returns_url_from_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_url: https://example.com/data.csv\n")
        result = load_input_url(cfg)
        assert result == "https://example.com/data.csv"

    def test_returns_none_when_field_absent(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("sheets:\n  sheet_link: x\n")
        result = load_input_url(cfg)
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path):
        result = load_input_url(tmp_path / "nonexistent.yaml")
        assert result is None

    def test_returns_none_for_empty_url(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_url: \n")
        result = load_input_url(cfg)
        assert result is None


# ---------------------------------------------------------------------------
# load_url_poll_interval (config.py)
# ---------------------------------------------------------------------------


class TestLoadUrlPollInterval:
    def test_returns_interval_from_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("url_poll_interval: 120\n")
        result = load_url_poll_interval(cfg)
        assert result == 120.0

    def test_returns_float(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("url_poll_interval: 30.5\n")
        result = load_url_poll_interval(cfg)
        assert result == pytest.approx(30.5)

    def test_returns_none_when_field_absent(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_url: https://example.com/data.csv\n")
        result = load_url_poll_interval(cfg)
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path):
        result = load_url_poll_interval(tmp_path / "nonexistent.yaml")
        assert result is None

    def test_returns_none_for_invalid_value(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("url_poll_interval: not_a_number\n")
        result = load_url_poll_interval(cfg)
        assert result is None


# ---------------------------------------------------------------------------
# __main__.py CLI integration
# ---------------------------------------------------------------------------


class TestMainCliUrl:
    """Smoke-tests for the --input-url path in __main__.main()."""

    def test_input_url_once_fetches_and_processes(self, tmp_path):
        output_path = tmp_path / "out.csv"

        with patch("exercise_log.url_watcher._fetch_url", return_value=_SAMPLE_CSV), patch(
            "exercise_log.url_watcher._process_content"
        ) as mock_proc:
            from exercise_log.__main__ import main

            rc = main(
                [
                    "--input-url",
                    "http://example.com/file.csv",
                    "--output",
                    str(output_path),
                    "--once",
                ]
            )

        assert rc == 0
        mock_proc.assert_called_once()

    def test_input_url_once_returns_error_on_fetch_failure(self, tmp_path):
        output_path = tmp_path / "out.csv"

        with patch("exercise_log.url_watcher._fetch_url", return_value=None):
            from exercise_log.__main__ import main

            rc = main(
                [
                    "--input-url",
                    "http://example.com/file.csv",
                    "--output",
                    str(output_path),
                    "--once",
                ]
            )

        assert rc == 1

    def test_no_input_returns_error(self, tmp_path):
        """With no --input, --input-url, and no config.yaml input_url, should error."""
        output_path = tmp_path / "out.csv"

        with patch("exercise_log.__main__.load_input_url", return_value=None):
            from exercise_log.__main__ import main

            rc = main(["--output", str(output_path)])

        assert rc == 1

    def test_input_url_loaded_from_config(self, tmp_path):
        """When neither --input nor --input-url is given, use input_url from config."""
        output_path = tmp_path / "out.csv"

        with patch(
            "exercise_log.__main__.load_input_url",
            return_value="http://example.com/file.csv",
        ), patch(
            "exercise_log.url_watcher._fetch_url", return_value=_SAMPLE_CSV
        ), patch(
            "exercise_log.url_watcher._process_content"
        ) as mock_proc:
            from exercise_log.__main__ import main

            rc = main(["--output", str(output_path), "--once"])

        assert rc == 0
        mock_proc.assert_called_once()

    def test_poll_interval_loaded_from_config(self, tmp_path):
        """url_poll_interval in config.yaml is used when --url-poll-interval not given."""
        output_path = tmp_path / "out.csv"
        captured_intervals: list = []

        def fake_watch_url(url, out, poll_interval, sheet_config):
            captured_intervals.append(poll_interval)
            # return immediately (don't loop or raise KeyboardInterrupt)

        with patch(
            "exercise_log.__main__.load_input_url",
            return_value="http://example.com/file.csv",
        ), patch(
            "exercise_log.__main__.load_url_poll_interval",
            return_value=90.0,
        ), patch(
            "exercise_log.url_watcher.watch_url", side_effect=fake_watch_url
        ):
            from exercise_log.__main__ import main

            rc = main(["--output", str(output_path)])

        assert rc == 0
        assert captured_intervals == [90.0]

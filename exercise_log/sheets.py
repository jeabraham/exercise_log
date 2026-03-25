"""
sheets.py – Google Sheets output for exercise-log.

Reads existing timestamps from the configured sheet range to determine which
entries have already been written, then appends new rows to the sheet.

Configuration (from config.yaml ``sheets:`` section):
    sheet_link   – full URL of the Google Sheet
    authorization – path to the service-account JSON key file
    range        – full A1 range to read/append (e.g. ``RawLog!A:I``)
    timestamp    – column range for existing timestamps (e.g. ``RawLog!A``)
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Lazily imported so the package can still be imported without the Google
# libraries installed (they are only needed at runtime when sheets are used).
try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    _GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    _GOOGLE_LIBS_AVAILABLE = False

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_INVALID_GRANT_HINT = (
    "\nHint: 'invalid_grant' / 'Invalid JWT Signature' usually means the service "
    "account key has been revoked or rotated.  Download a fresh JSON key from the "
    "Google Cloud Console (IAM & Admin → Service Accounts → Keys) and update the "
    "'authorization' path in config.yaml."
)


def _log_api_error(operation: str, spreadsheet_id: str, exc: Exception) -> None:
    """Log a Google API error with an actionable hint when it looks like an auth failure."""
    msg = str(exc)
    if "invalid_grant" in msg or "Invalid JWT" in msg or "invalid_client" in msg:
        logger.error(
            "Authentication failed while trying to %s Google Sheet %s: %s%s",
            operation,
            spreadsheet_id,
            exc,
            _INVALID_GRANT_HINT,
        )
    else:
        logger.warning(
            "Could not %s Google Sheet %s: %s",
            operation,
            spreadsheet_id,
            exc,
        )


def _extract_spreadsheet_id(sheet_link: str) -> str:
    """
    Extract the spreadsheet ID from a full Google Sheets URL.

    >>> _extract_spreadsheet_id(
    ...     "https://docs.google.com/spreadsheets/d/abc123/edit?usp=sharing"
    ... )
    'abc123'
    """
    m = re.search(r"/spreadsheets/d/([^/]+)", sheet_link)
    if not m:
        raise ValueError(f"Cannot extract spreadsheet ID from URL: {sheet_link!r}")
    return m.group(1)


def _resolve_auth_path(auth_path: str) -> str:
    """
    Return an absolute path for *auth_path*.

    If *auth_path* is already absolute it is returned unchanged.  Otherwise it
    is resolved relative to the directory that contains ``config.yaml`` (i.e.
    the current working directory at startup, which is typically the repo
    root).
    """
    p = Path(auth_path)
    if p.is_absolute():
        return str(p)
    # Try current working directory first, then the package directory.
    cwd_candidate = Path.cwd() / p
    if cwd_candidate.exists():
        return str(cwd_candidate)
    pkg_candidate = Path(__file__).parent / p
    if pkg_candidate.exists():
        return str(pkg_candidate)
    # Fall back to cwd-relative (will raise a helpful error later).
    return str(cwd_candidate)


def _build_service(auth_path: str):
    """Return an authenticated Google Sheets API service object."""
    if not _GOOGLE_LIBS_AVAILABLE:
        raise ImportError(
            "Google API libraries are not installed. "
            "Install them with: pip install -e '.[sheets]'"
        )
    resolved = _resolve_auth_path(auth_path)
    if not Path(resolved).exists():
        raise FileNotFoundError(
            f"Service account key file not found: {resolved!r}\n"
            "Ensure 'authorization' in config.yaml points to a valid "
            "service-account JSON key file."
        )
    try:
        creds = Credentials.from_service_account_file(resolved, scopes=_SCOPES)
    except Exception as exc:
        raise ValueError(
            f"Failed to load service account credentials from {resolved!r}: {exc}\n"
            "If you see 'Invalid JWT Signature', the key may have been revoked "
            "or rotated.  Download a fresh key from the Google Cloud Console "
            "(IAM & Admin → Service Accounts) and update the authorization file."
        ) from exc
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def load_existing_timestamps(
    sheet_link: str,
    auth_path: str,
    timestamp_range: str,
) -> Set[str]:
    """
    Read the timestamp column from the Google Sheet and return the set of
    already-present timestamp strings.

    Parameters
    ----------
    sheet_link:
        Full URL of the Google Spreadsheet.
    auth_path:
        Path to the service-account JSON key file.
    timestamp_range:
        A1 notation for the timestamp column (e.g. ``RawLog!A``).
    """
    spreadsheet_id = _extract_spreadsheet_id(sheet_link)
    service = _build_service(auth_path)

    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=timestamp_range)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        _log_api_error("read timestamps from", spreadsheet_id, exc)
        return set()

    rows = result.get("values", [])
    timestamps: Set[str] = set()
    for row in rows:
        if row:
            ts = str(row[0]).strip()
            if ts:
                timestamps.add(ts)
    logger.debug(
        "Loaded %d existing timestamps from sheet %s range %s",
        len(timestamps),
        spreadsheet_id,
        timestamp_range,
    )
    return timestamps


def append_rows_to_sheet(
    sheet_link: str,
    auth_path: str,
    append_range: str,
    rows: List[Dict[str, str]],
    fields: List[str],
) -> int:
    """
    Append *rows* to the Google Sheet.

    Parameters
    ----------
    sheet_link:
        Full URL of the Google Spreadsheet.
    auth_path:
        Path to the service-account JSON key file.
    append_range:
        A1 notation for the target range (e.g. ``RawLog!A:I``).
    rows:
        List of row dicts (keys are field names in *fields* order).
    fields:
        Ordered list of field names that map to sheet columns.

    Returns the number of rows appended.
    """
    if not rows:
        return 0

    spreadsheet_id = _extract_spreadsheet_id(sheet_link)
    service = _build_service(auth_path)

    values = [[row.get(f, "") for f in fields] for row in rows]

    body = {"values": values}
    try:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=append_range,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()
    except Exception as exc:  # noqa: BLE001
        _log_api_error(f"append {len(rows)} row(s) to", spreadsheet_id, exc)
        return 0

    logger.info(
        "Appended %d new row(s) to Google Sheet %s range %s",
        len(rows),
        spreadsheet_id,
        append_range,
    )
    return len(rows)


def process_input_csv_to_sheet(
    input_path: Path,
    sheet_config: Dict,
) -> int:
    """
    Read *input_path*, determine which rows are already in the Google Sheet,
    parse the new rows, and append them to the sheet.

    Parameters
    ----------
    input_path:
        Path to the input CSV file produced by the Siri shortcut.
    sheet_config:
        Dict with keys ``sheet_link``, ``authorization``, ``range``,
        and ``timestamp`` (all from the ``sheets:`` section of config.yaml).

    Returns the number of new rows appended.
    """
    import csv

    from exercise_log.config import OUTPUT_FIELDS
    from exercise_log.parser import parse_row

    sheet_link = sheet_config["sheet_link"]
    auth_path = sheet_config["authorization"]
    append_range = sheet_config["range"]
    timestamp_range = sheet_config["timestamp"]

    seen = load_existing_timestamps(sheet_link, auth_path, timestamp_range)

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

    return append_rows_to_sheet(
        sheet_link, auth_path, append_range, new_rows, OUTPUT_FIELDS
    )

"""
url_watcher.py – poll a remote URL for changes and process new CSV content.

When the content at *url* changes the module writes it to a temporary file and
calls ``parser.process_input_csv`` (CSV output) or
``sheets.process_input_csv_to_sheet`` (Google Sheets output), then removes the
temporary file.

Typical usage
-------------
    from exercise_log.url_watcher import watch_url
    watch_url("https://dl.dropboxusercontent.com/…/Weightlifting_queue.csv",
              output_path=Path("parsed.csv"),
              poll_interval=60)
"""

import hashlib
import logging
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _fetch_url(url: str, timeout: int = 30) -> Optional[bytes]:
    """Download *url* and return the raw bytes, or ``None`` on any error.

    Only ``http`` and ``https`` schemes are permitted.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        logger.error(
            "Refusing to fetch URL with unsupported scheme %r: %s",
            parsed.scheme,
            url,
        )
        return None

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch URL %s: %s", url, exc)
        return None


def _content_hash(data: bytes) -> str:
    """Return a hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def watch_url(
    url: str,
    output_path: Optional[Path],
    poll_interval: float = 60.0,
    sheet_config: Optional[Dict] = None,
) -> None:
    """
    Poll *url* for changes indefinitely and process the CSV when it changes.

    Parameters
    ----------
    url:
        HTTP/HTTPS URL to the input CSV file (e.g. a Dropbox shared link).
    output_path:
        Path to the output CSV file where parsed rows are appended.
        Must be provided when *sheet_config* is ``None``.
    poll_interval:
        Seconds to wait between consecutive polls.
    sheet_config:
        Optional dict with Google Sheets configuration (from config.yaml
        ``sheets:`` section).  When provided, rows are appended to the
        sheet instead of *output_path*.
    """
    last_hash: Optional[str] = None

    logger.info("Polling URL %s every %.0fs …", url, poll_interval)

    try:
        while True:
            content = _fetch_url(url)
            if content is not None:
                current_hash = _content_hash(content)
                if current_hash != last_hash:
                    if last_hash is None:
                        logger.info("Initial fetch from %s – processing …", url)
                    else:
                        logger.info("Change detected at %s – processing …", url)

                    _process_content(content, output_path, sheet_config)
                    last_hash = current_hash
                else:
                    logger.debug("No change at %s", url)

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("URL watcher stopped.")


def _process_content(
    content: bytes,
    output_path: Optional[Path],
    sheet_config: Optional[Dict],
) -> None:
    """Write *content* to a temp file, process it, then remove the temp file."""
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".csv", delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        if sheet_config is not None:
            from exercise_log.sheets import process_input_csv_to_sheet  # noqa: PLC0415

            process_input_csv_to_sheet(tmp_path, sheet_config)
        else:
            from exercise_log.parser import process_input_csv  # noqa: PLC0415

            process_input_csv(tmp_path, output_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Error processing URL content: %s", exc)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

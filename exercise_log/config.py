"""Configuration defaults for the exercise-log watcher."""

import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Number of seconds to wait after a file-change event before re-reading
# the input CSV.  A short delay lets cloud-sync tools finish writing.
DEFAULT_WATCH_DELAY: float = 5.0

# CSV field names used in the output file.
OUTPUT_FIELDS = [
    "timestamp",
    "exercise",
    "weight",
    "units",
    "lb-weight",
    "reps",
    "sets",
    "notes",
    "original text",
]


def load_sheets_config(config_path: Optional[Path] = None) -> Optional[Dict]:
    """
    Load the ``sheets:`` section from *config_path* (defaults to
    ``config.yaml`` in the current working directory).

    Returns a dict with keys ``sheet_link``, ``authorization``, ``range``,
    and ``timestamp``, or ``None`` if the section is absent or the file
    cannot be read.
    """
    if config_path is None:
        config_path = Path.cwd() / "config.yaml"

    if not config_path.exists():
        return None

    try:
        import yaml  # type: ignore[import]

        with config_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load config file %s: %s", config_path, exc)
        return None

    if not isinstance(data, dict):
        return None

    sheets = data.get("sheets")
    if not sheets:
        return None

    required = {"sheet_link", "authorization", "range", "timestamp"}
    missing = required - set(sheets.keys())
    if missing:
        logger.warning(
            "sheets: config is missing required keys: %s", ", ".join(sorted(missing))
        )
        return None

    return {
        "sheet_link": sheets["sheet_link"],
        "authorization": sheets["authorization"],
        "range": sheets["range"],
        "timestamp": sheets["timestamp"],
    }

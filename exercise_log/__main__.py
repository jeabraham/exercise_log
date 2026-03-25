"""
__main__.py – entry point for ``python -m exercise_log`` and the
``exercise-log`` console script.

Usage
-----
    python -m exercise_log --input workouts.csv --output parsed.csv
    python -m exercise_log --input workouts.csv --output parsed.csv --delay 10
    python -m exercise_log --input workouts.csv --output parsed.csv --once
    python -m exercise_log --input workouts.csv --once
    (when no --output is given and config.yaml defines a sheets: section,
     new rows are appended to the configured Google Sheet)
"""

import argparse
import logging
import sys
from pathlib import Path

from exercise_log.config import DEFAULT_WATCH_DELAY, load_sheets_config
from exercise_log.parser import process_input_csv
from exercise_log.watcher import watch


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="exercise-log",
        description=(
            "Watch a Siri-shortcut exercise CSV file for new entries and "
            "parse them into a structured output CSV or Google Sheet."
        ),
    )
    p.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="Path to the input CSV file (written by the Siri shortcut).",
    )
    p.add_argument(
        "--output",
        required=False,
        default=None,
        metavar="PATH",
        help=(
            "Path to the output (parsed) CSV file. "
            "If omitted and a sheets: section is defined in config.yaml, "
            "rows are appended to the configured Google Sheet instead."
        ),
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_WATCH_DELAY,
        metavar="SECONDS",
        help=(
            f"Seconds to wait after a file-change event before reading the "
            f"input file (default: {DEFAULT_WATCH_DELAY})."
        ),
    )
    p.add_argument(
        "--once",
        action="store_true",
        help=(
            "Process the input file once and exit without starting the "
            "file watcher.  Useful for one-off imports."
        ),
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    input_path = Path(args.input)

    # Determine output destination.
    if args.output is not None:
        output_path = Path(args.output)
        use_sheets = False
        sheet_config = None
    else:
        sheet_config = load_sheets_config()
        if sheet_config is None:
            logging.error(
                "No --output file given and no sheets: section found in "
                "config.yaml. Please provide --output or configure sheets:."
            )
            return 1
        use_sheets = True
        output_path = None  # type: ignore[assignment]

    if args.once:
        if not input_path.exists():
            logging.error("Input file not found: %s", input_path)
            return 1
        if use_sheets:
            from exercise_log.sheets import process_input_csv_to_sheet

            n = process_input_csv_to_sheet(input_path, sheet_config)
            print(f"Wrote {n} new row(s) to Google Sheet.")
        else:
            n = process_input_csv(input_path, output_path)
            print(f"Wrote {n} new row(s) to {output_path}.")
        return 0

    watch(input_path, output_path, args.delay, sheet_config=sheet_config)
    return 0


if __name__ == "__main__":
    sys.exit(main())

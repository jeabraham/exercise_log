"""
__main__.py – entry point for ``python -m exercise_log`` and the
``exercise-log`` console script.

Usage
-----
    python -m exercise_log --input workouts.csv --output parsed.csv
    python -m exercise_log --input workouts.csv --output parsed.csv --delay 10
    python -m exercise_log --input workouts.csv --output parsed.csv --once
    python -m exercise_log --input workouts.csv --once
    python -m exercise_log --input-url https://example.com/workouts.csv --output parsed.csv
    python -m exercise_log --output parsed.csv  (uses input_url from config.yaml)
    (when no --output is given and config.yaml defines a sheets: section,
     new rows are appended to the configured Google Sheet)
"""

import argparse
import logging
import sys
from pathlib import Path

from exercise_log.config import (
    DEFAULT_URL_POLL_INTERVAL,
    DEFAULT_WATCH_DELAY,
    load_input_url,
    load_sheets_config,
    load_url_poll_interval,
)
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
    input_group = p.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input",
        required=False,
        default=None,
        metavar="PATH",
        help="Path to the input CSV file (written by the Siri shortcut).",
    )
    input_group.add_argument(
        "--input-url",
        required=False,
        default=None,
        metavar="URL",
        help=(
            "HTTP/HTTPS URL of the input CSV file to poll for changes "
            "(e.g. a Dropbox shared link).  Takes precedence over "
            "input_url in config.yaml."
        ),
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
        "--url-poll-interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            f"Seconds between URL polls when using --input-url or "
            f"input_url from config.yaml.  Falls back to url_poll_interval "
            f"in config.yaml, then to the built-in default "
            f"({DEFAULT_URL_POLL_INTERVAL}s)."
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

    # ------------------------------------------------------------------
    # Resolve input source: --input (file), --input-url, or config.yaml
    # ------------------------------------------------------------------
    input_url: str | None = args.input_url
    if input_url is None and args.input is None:
        # Neither flag given – try to load URL from config.yaml
        input_url = load_input_url()

    use_url = input_url is not None
    input_path = Path(args.input) if args.input is not None else None

    if not use_url and input_path is None:
        logging.error(
            "No input source specified.  Provide --input PATH, --input-url URL, "
            "or set input_url in config.yaml."
        )
        return 1

    # Resolve URL poll interval: CLI flag → config.yaml → built-in default
    url_poll_interval: float = (
        args.url_poll_interval
        if args.url_poll_interval is not None
        else (load_url_poll_interval() or DEFAULT_URL_POLL_INTERVAL)
    )

    # ------------------------------------------------------------------
    # Determine output destination
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # URL-based workflow
    # ------------------------------------------------------------------
    if use_url:
        if args.once:
            from exercise_log.url_watcher import _fetch_url, _process_content

            content = _fetch_url(input_url)  # type: ignore[arg-type]
            if content is None:
                logging.error("Could not fetch URL: %s", input_url)
                return 1
            _process_content(content, output_path, sheet_config if use_sheets else None)
            print(f"Processed URL content from {input_url}.")
            return 0

        from exercise_log.url_watcher import watch_url

        watch_url(
            input_url,  # type: ignore[arg-type]
            output_path,
            poll_interval=url_poll_interval,
            sheet_config=sheet_config if use_sheets else None,
        )
        return 0

    # ------------------------------------------------------------------
    # File-based workflow (original behaviour)
    # ------------------------------------------------------------------
    if args.once:
        if not input_path.exists():  # type: ignore[union-attr]
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

    watch(input_path, output_path, args.delay, sheet_config=sheet_config if use_sheets else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

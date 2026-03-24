"""
watcher.py – cross-platform file-system watcher for the exercise-log CSV.

Uses the *watchdog* library so that the same code works on macOS, Linux, and
Windows (watchdog selects the best available backend automatically).

When the watched file is modified the handler waits *watch_delay* seconds
(to let cloud-sync tools finish writing) and then calls
``parser.process_input_csv``.
"""

import logging
import time
from pathlib import Path

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from exercise_log.parser import process_input_csv

logger = logging.getLogger(__name__)


class _CsvChangeHandler(FileSystemEventHandler):
    """Watchdog event handler that reacts to modifications of a single file."""

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        watch_delay: float,
    ) -> None:
        super().__init__()
        self._input_path = input_path.resolve()
        self._output_path = output_path
        self._watch_delay = watch_delay

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        if Path(event.src_path).resolve() != self._input_path:
            return
        logger.info(
            "Change detected in %s – waiting %.1fs before processing…",
            self._input_path,
            self._watch_delay,
        )
        time.sleep(self._watch_delay)
        process_input_csv(self._input_path, self._output_path)


def watch(
    input_path: Path,
    output_path: Path,
    watch_delay: float,
) -> None:
    """
    Block and watch *input_path* for modifications indefinitely.

    Parameters
    ----------
    input_path:
        Path to the input CSV file produced by the Siri shortcut.
    output_path:
        Path to the output CSV file where parsed rows are appended.
    watch_delay:
        Seconds to wait after a file-change event before reading the file.
    """
    input_path = Path(input_path).resolve()
    output_path = Path(output_path)

    if not input_path.exists():
        logger.warning(
            "Input file %s does not yet exist – watching its directory anyway.",
            input_path,
        )

    # Do an initial pass so that any rows already in the file are processed
    # before we start watching for changes.
    logger.info("Running initial scan of %s …", input_path)
    if input_path.exists():
        process_input_csv(input_path, output_path)

    handler = _CsvChangeHandler(input_path, output_path, watch_delay)
    observer = Observer()
    observer.schedule(handler, str(input_path.parent), recursive=False)
    observer.start()
    logger.info("Watching %s for changes (delay=%.1fs) …", input_path, watch_delay)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        logger.info("Watcher stopped.")

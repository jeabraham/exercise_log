"""Configuration defaults for the exercise-log watcher."""

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

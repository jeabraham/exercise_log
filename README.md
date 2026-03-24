# exercise_log

Works with a Siri shortcut to build a structured log of gym exercises.  The Siri shortcut appends speech-to-text entries (timestamp + free-text description) to a cloud CSV file.  This tool watches that file, parses each new row to extract exercise name, weight, and unit, converts everything to pounds, and writes the results to a structured output CSV.

---

## Requirements

* Python 3.9 or later
* `make` (standard on macOS and Linux; on Windows use [Git Bash](https://git-scm.com) or [WSL](https://learn.microsoft.com/en-us/windows/wsl/))

---

## Quick start

```bash
# 1. Clone / download the repository, then enter it
cd exercise_log

# 2. Create a virtual environment, install all dependencies, and run the tests
make install

# 3. Watch your input CSV for new entries (Ctrl-C to stop)
make run INPUT=~/path/to/workouts.csv OUTPUT=~/path/to/parsed.csv

# Or process the file once and exit
make run-once INPUT=~/path/to/workouts.csv OUTPUT=~/path/to/parsed.csv
```

### What `make install` does

1. Creates `.venv/` in the project root using `python3 -m venv`.
2. Upgrades `pip` inside the venv.
3. Installs the package (including `watchdog` and `word2number`) in **editable** mode.
4. Installs the development dependency (`pytest`).
5. Runs the test suite to confirm everything works.

---

## All Makefile targets

| Target | Description |
|--------|-------------|
| `make install` | Create `.venv`, install all deps, run tests |
| `make test` | Run the test suite inside the venv |
| `make run INPUT=… OUTPUT=…` | Watch the input CSV continuously (Ctrl-C to stop) |
| `make run-once INPUT=… OUTPUT=…` | Process the input CSV once and exit |
| `make clean` | Remove `.venv` and cached Python files |
| `make help` | Print a short help message |

> **Tip – custom delay:** Pass `DELAY=<seconds>` to override the default 5-second
> post-change wait, e.g. `make run INPUT=… OUTPUT=… DELAY=10`.

---

## Input CSV format

The Siri shortcut appends rows in this format:

```
<ISO-8601 timestamp>, <free-text description>
```

Example rows (some will fail to parse – that is expected):

```
2026-03-09T12:28:58-06:00, Shoulder press
2026-03-09T12:53:06-06:00, Upright Road 27 1/2 pounds 3×12
2026-03-09T19:10:43-06:00, I feel like I could eat shoulder press 87 pounds 5×10 reps
2026-03-18T13:03:19-06:00, Tricep push downs 22 lbs. 3×12
```

---

## Output CSV fields

| Field | Description |
|-------|-------------|
| `timestamp` | ISO-8601 timestamp (unique row ID) |
| `exercise` | Everything before the weight number |
| `weight` | Numeric value as recognised (e.g. `27 1/2`, `22`, `twenty`) |
| `units` | Canonical unit: `kg` or `lb` |
| `lb-weight` | Weight converted to pounds |
| `reps` | *(reserved for future LLM-based parsing)* |
| `sets` | *(reserved for future LLM-based parsing)* |
| `notes` | Everything after the weight unit keyword |
| `original text` | The full free-text description, unmodified |

---

## Running without `make`

If you prefer to manage the venv yourself:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"

# Watch continuously
python -m exercise_log --input workouts.csv --output parsed.csv

# One-shot import
python -m exercise_log --input workouts.csv --output parsed.csv --once

# Extra options
python -m exercise_log --help
```

---

## Project layout

```
exercise_log/
├── exercise_log/
│   ├── __init__.py       # package marker
│   ├── __main__.py       # CLI entry point
│   ├── config.py         # defaults (watch delay, output field names)
│   ├── parser.py         # row-level CSV parsing and weight extraction
│   └── watcher.py        # cross-platform file-system monitor (watchdog)
├── tests/
│   └── test_parser.py    # 45 unit and integration tests
├── pyproject.toml
├── requirements.txt
├── Makefile
└── README.md
```

---

## Supported weight units

| You say… | Recognised as |
|----------|---------------|
| `kg`, `kgs`, `kilogram`, `kilograms` | kg → converted to lb |
| `lb`, `lbs`, `lbs.`, `pound`, `pounds` | lb (no conversion needed) |
| `£` | lb (Siri sometimes hears "pounds" as "£") |

Numeric values may be integers (`22`), decimals (`22.5`), mixed fractions (`27 1/2`), or word numbers (`twenty`, `eighty seven`).

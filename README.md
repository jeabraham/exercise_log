# exercise_log

Works with a Siri shortcut to build a structured log of gym exercises.  The Siri shortcut prompts you for "Exercise, weight, reps?", prepends the current timestamp, and appends the result as a new line to `Weightlifting_queue.csv` in iCloud (Numbers).  This tool watches that file, parses each new row to extract exercise name, weight, and unit, converts everything to pounds, and writes the results to a structured output CSV or directly to a Google Sheet.

An optional [Ollama](https://ollama.com) integration corrects garbled speech-recognition text and extracts sets, reps, and notes using a local LLM.

---

## Siri Shortcut

The **Gym Log** shortcut prompts you to say (or type) your exercise details, then appends a timestamped line to `Weightlifting_queue.csv` in iCloud automatically.

**[⬇ Install the Gym Log shortcut](https://www.icloud.com/shortcuts/c118bd928fd54323bc59fca8a6a00cc5)**

![Gym Log shortcut](exercise_log/shortcuts/Gym%20Log%20Shortcut.png)

The shortcut does three things:
1. **Ask for Text** – prompts *"Exercise, weight, reps?"* (works with Siri dictation or typed input).
2. **Current Date** – captures the timestamp.
3. **Append to File** – writes `<timestamp>, <text>` as a new line to `Weightlifting_queue.csv` (in your iCloud Numbers folder).

Point `exercise_log` at that CSV file:

```bash
make run INPUT=~/Library/Mobile\ Documents/com~apple~Numbers/Documents/Weightlifting_queue.csv \
         OUTPUT=~/workouts_parsed.csv
```

Or, to write directly to Google Sheets instead of a local CSV:

```bash
make run-sheets INPUT=~/Library/Mobile\ Documents/com~apple~Numbers/Documents/Weightlifting_queue.csv
```

---

## Requirements

* Python 3.9 or later
* `make` (standard on macOS and Linux; on Windows use [Git Bash](https://git-scm.com) or [WSL](https://learn.microsoft.com/en-us/windows/wsl/))
* *(Optional)* [Ollama](https://ollama.com) running locally for LLM-assisted parsing
* *(Optional)* A Google service-account key for Google Sheets output

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

### Enabling LLM support (optional)

To use the Ollama integration, install the extra dependencies and start Ollama:

```bash
# Install with LLM extras (adds pyyaml + ollama Python client)
make install-llm

# Pull your preferred model (example)
ollama pull llama3

# Edit config.yaml to enable LLM and choose your model
# llm:
#   enabled: true
#   model: "llama3"
```

### What `make install` does

1. Creates `.venv/` in the project root using `python3 -m venv`.
2. Upgrades `pip` inside the venv.
3. Installs the package in **editable** mode with test and YAML dependencies (`.[dev]`).
4. Runs the test suite to confirm everything works.

---

## Google Sheets output (optional)

Instead of writing to a local CSV file, `exercise_log` can append new rows directly to a Google Sheet.

### 1. Create a service account

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Go to **IAM & Admin → Service Accounts** and create a new service account.
3. On the **Keys** tab, click **Add Key → Create new key → JSON** and download the file.
4. Save the downloaded JSON key file somewhere accessible (e.g. `configuration.json` next to `config.yaml`).

### 2. Share the spreadsheet

Open your Google Sheet and share it (as **Editor**) with the service-account email shown in the JSON file (the `client_email` field).

### 3. Configure `config.yaml`

Add (or update) the `sheets:` section:

```yaml
sheets:
  sheet_link: https://docs.google.com/spreadsheets/d/<YOUR_SHEET_ID>/edit
  authorization: configuration.json   # relative to repo root or absolute path
  range: RawLog!A:I                   # target range to append rows
  timestamp: RawLog!A                 # column used for deduplication
```

### 4. Install Sheets dependencies and run

```bash
make install-sheets

# Watch continuously (Ctrl-C to stop)
make run-sheets INPUT=~/path/to/workouts.csv

# One-shot import
make run-once-sheets INPUT=~/path/to/workouts.csv
```

> **Key revoked?**  If you see `invalid_grant: Invalid JWT Signature`, the service-account key in your JSON file has been revoked or rotated.  Generate a new key in the Cloud Console (**IAM & Admin → Service Accounts → Keys**) and replace the file referenced by `authorization` in `config.yaml`.

---

## All Makefile targets

| Target | Description |
|--------|-------------|
| `make install` | Create `.venv`, install all deps (including test deps), run tests |
| `make install-llm` | Like `install` but also installs Ollama client for LLM support |
| `make install-sheets` | Like `install` but also installs Google API client for Sheets output |
| `make test` | Run the test suite inside the venv |
| `make test-sheets` | Run the Google Sheets integration tests (requires live credentials) |
| `make run INPUT=… OUTPUT=…` | Watch the input CSV continuously (Ctrl-C to stop) |
| `make run-once INPUT=… OUTPUT=…` | Process the input CSV once and exit |
| `make run-sheets INPUT=…` | Watch and append to Google Sheet continuously (Ctrl-C to stop) |
| `make run-once-sheets INPUT=…` | Process once and append to Google Sheet, then exit |
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
| `exercise` | Exercise name (LLM-corrected if enabled, e.g. "face pull" from "facebook") |
| `weight` | Numeric value as recognised (e.g. `27 1/2`, `22`, `twenty`) |
| `units` | Canonical unit: `kg` or `lb` |
| `lb-weight` | Weight converted to pounds |
| `reps` | Reps per set (filled by LLM when enabled) |
| `sets` | Number of sets (filled by LLM when enabled) |
| `notes` | Free-text notes after the weight (filled by LLM when enabled) |
| `original text` | The full free-text description, unmodified |

These same fields (in this order) are used as the columns when writing to Google Sheets.

---

## LLM integration

When Ollama is running and `llm.enabled: true` is set in `config.yaml`, the parser makes up to two extra LLM queries per row:

1. **`identify_exercise_prompt`** – corrects the exercise name extracted by the regex parser (e.g. `"Gumball pullovers"` → `"Dumbbell pullovers"`, `"facebook"` → `"face pull"`).
2. **`sets_reps_notes_prompt`** – parses the text after the weight unit to fill in `sets`, `reps`, and `notes` (e.g. `"3×15 was hard"` → sets=3, reps=15, notes="was hard").

If the regex parser **cannot** find a weight at all, a single fallback query is sent using **`full_log_parse_prompt`**, which asks the LLM to return all six fields: exercise, weight, units, reps, sets, notes.

The LLM response format (JSON or CSV) is configurable via `llm.response_format` in `config.yaml`.

### `config.yaml`

```yaml
llm:
  enabled: true          # set to false to disable all LLM queries
  model: "llama3"        # any model available in your Ollama installation
  base_url: "http://localhost:11434"
  response_format: "json"  # "json" or "csv"

prompts:
  full_log_parse_prompt: |
    ...
  identify_exercise_prompt: |
    ...
  sets_reps_notes_prompt: |
    ...
```

The default prompts are provided in the `config.yaml` file at the project root.  You can edit them to tune the LLM's behaviour.

---

## Running without `make`

If you prefer to manage the venv yourself:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Core install
pip install -e ".[dev]"

# With LLM support
pip install -e ".[dev,llm]"

# With Google Sheets support
pip install -e ".[dev,sheets]"

# Watch continuously – write to a local CSV
python -m exercise_log --input workouts.csv --output parsed.csv

# Watch continuously – write to Google Sheets (sheets: must be set in config.yaml)
python -m exercise_log --input workouts.csv

# One-shot import to CSV
python -m exercise_log --input workouts.csv --output parsed.csv --once

# One-shot import to Google Sheets
python -m exercise_log --input workouts.csv --once

# Extra options
python -m exercise_log --help
```

### Running the tests

```bash
# All unit tests (no live services required)
pytest tests/

# Or via make
make test

# Google Sheets integration tests (requires valid credentials in config.yaml)
pytest tests/test_sheets.py -m sheets_integration
make test-sheets

# Run test_sheets.py directly (also works)
python tests/test_sheets.py
```

---

## Project layout

```
exercise_log/
├── exercise_log/
│   ├── __init__.py          # package marker
│   ├── __main__.py          # CLI entry point
│   ├── config.py            # defaults and config.yaml loader
│   ├── configuration.json   # service-account key (for testing)
│   ├── llm.py               # Ollama LLM integration (optional)
│   ├── parser.py            # row-level CSV parsing and weight extraction
│   ├── sheets.py            # Google Sheets output (optional)
│   ├── shortcuts/
│   │   ├── Gym Log Shortcut.png   # screenshot of the Siri shortcut
│   │   └── Gym Log.webloc         # iCloud install link
│   └── watcher.py           # cross-platform file-system monitor (watchdog)
├── tests/
│   ├── test_llm.py          # unit tests for the LLM module (mocked)
│   ├── test_parser.py       # unit and integration tests for the parser
│   └── test_sheets.py       # unit tests for Sheets integration (mocked)
├── config.yaml              # LLM prompts, settings, and Sheets config
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


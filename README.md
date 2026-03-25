# exercise_log

Works with a Siri shortcut to build a structured log of gym exercises.  The Siri shortcut appends speech-to-text entries (timestamp + free-text description) to a cloud CSV file.  This tool watches that file, parses each new row to extract exercise name, weight, and unit, converts everything to pounds, and writes the results to a structured output CSV.

An optional [Ollama](https://ollama.com) integration corrects garbled speech-recognition text and extracts sets, reps, and notes using a local LLM.

---

## Requirements

* Python 3.9 or later
* `make` (standard on macOS and Linux; on Windows use [Git Bash](https://git-scm.com) or [WSL](https://learn.microsoft.com/en-us/windows/wsl/))
* *(Optional)* [Ollama](https://ollama.com) running locally for LLM-assisted parsing

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

## All Makefile targets

| Target | Description |
|--------|-------------|
| `make install` | Create `.venv`, install all deps (including test deps), run tests |
| `make install-llm` | Like `install` but also installs Ollama client for LLM support |
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
| `exercise` | Exercise name (LLM-corrected if enabled, e.g. "face pull" from "facebook") |
| `weight` | Numeric value as recognised (e.g. `27 1/2`, `22`, `twenty`) |
| `units` | Canonical unit: `kg` or `lb` |
| `lb-weight` | Weight converted to pounds |
| `reps` | Reps per set (filled by LLM when enabled) |
| `sets` | Number of sets (filled by LLM when enabled) |
| `notes` | Free-text notes after the weight (filled by LLM when enabled) |
| `original text` | The full free-text description, unmodified |

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
│   ├── llm.py            # Ollama LLM integration (optional)
│   ├── parser.py         # row-level CSV parsing and weight extraction
│   └── watcher.py        # cross-platform file-system monitor (watchdog)
├── tests/
│   ├── test_llm.py       # 20 unit tests for the LLM module (mocked)
│   └── test_parser.py    # 45 unit and integration tests
├── config.yaml           # LLM prompts and settings
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

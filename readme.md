# Restaurant Rankings

Scripts to statistically rank restaurants in a city using Google Places data and the [Wilson Score Interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval). Based on this [blog post](https://mattsayar.com/where-are-the-best-restaurants-in-my-city-a-statistical-analysis/).

## Project Structure

The project follows the Python [**src layout**](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/) for proper packaging and import isolation:

```
.
├── pyproject.toml                        # Project metadata, deps & tool config
├── requirements.txt                      # Pip-compatible dependency lock
├── .env.example                          # Template for API key config
├── .python-version                       # Pinned Python version (pyenv)
│
│   ── Source package ─────────────────────────────────────
├── src/
│   └── restaurant_rankings/
│       ├── __init__.py
│       ├── scraper.py                    # Step 1 — scrape via Google Places API
│       ├── ranker.py                     # Step 2 — Wilson Score ranking & export
│       ├── pagination.py                 # Pagination helpers for Telegram bot
│       └── bot.py                        # Step 3 — Telegram bot entry point
│
│   ── Tests ──────────────────────────────────────────────
├── tests/
│   ├── __init__.py
│   ├── test_scraper.py                   # Tests for scraper module
│   ├── test_ranker.py                    # Tests for ranker module
│   ├── test_pagination.py               # Tests for pagination module
│   └── fixtures/
│       └── sample_restaurants.json       # 10-restaurant fixture
│
│   ── Output ─────────────────────────────────────────────
├── output/                               # All generated data (gitignored)
│   ├── restaurant_98005_2026-02-26.json
│   └── restaurant_98005_2026-02-26.csv
└── readme.md
```

### Module overview

| Module | Responsibility | Heavy deps |
|---|---|---|
| `restaurant_rankings.scraper` | Scrape restaurants via Google Places API; reverse-geocode zip codes | `requests` |
| `restaurant_rankings.ranker` | Wilson Score ranking, filtering, CSV / JSON / map export | `scipy` |
| `restaurant_rankings.pagination` | Format paginated Telegram messages with inline navigation buttons | `python-telegram-bot` (lightweight) |
| `restaurant_rankings.bot` | Bot entry point — wires handlers, caching, and the scraper/ranker together | all of the above |

Each module (except the bot entry point) is deliberately self-contained so it can be imported and tested without loading API keys, `.env`, or unrelated third-party libraries.

## Setup

1. **Install Python 3.11+ via [pyenv](https://github.com/pyenv/pyenv)** (recommended):

    ```bash
    pyenv install 3.11.11
    pyenv local 3.11.11
    ```

2. **Create a virtual environment and install the package:**

    ```bash
    python -m venv .venv
    source .venv/bin/activate

    # Install in editable mode with test dependencies
    pip install -e ".[test]"

    # Optional: install folium for HTML map generation (--map)
    pip install -e ".[map]"
    ```

3. **Configure your API key:**

    ```bash
    cp .env.example .env
    ```

    Edit `.env` and paste your Google Cloud Platform API key. Get one from [Google Cloud Platform](https://developers.google.com/maps/documentation/javascript/cloud-setup).

    For the Telegram bot, also add your bot token:

    ```
    GCP_API_KEY=your_key_here
    TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
    ```

4. **Enable required GCP APIs:**

    The following APIs must be enabled on your GCP project. Click the links below to enable them directly (or find them in the [API Library](https://console.cloud.google.com/apis/library)):

    | API | Purpose |
    |---|---|
    | [Places API (New)](https://console.cloud.google.com/apis/library/places.googleapis.com) | Restaurant search via `searchNearby` |
    | [Geocoding API](https://console.cloud.google.com/apis/library/geocoding-backend.googleapis.com) | Reverse-geocode coordinates to zip code |
    | [Maps JavaScript API](https://console.cloud.google.com/apis/library/maps-backend.googleapis.com) | Required backend for Maps platform |

## Usage

### Step 1: Scrape restaurant data

```bash
# Default: Bellevue, WA — 15 km radius
python -m restaurant_rankings.scraper

# Custom coordinates and radius
python -m restaurant_rankings.scraper --lat 47.6754 --lng -122.3808 --radius 5
```

| Flag | Default | Description |
|---|---|---|
| `--lat` | `47.625435` | Center latitude |
| `--lng` | `-122.154905` | Center longitude |
| `--radius` | `15` | Search radius in km |
| `--types` | `restaurant` | Place types to search (space-separated) |
| `-v` / `--verbose` | off | Enable debug logging |

This writes a file to `output/` named `{category}_{zip_code}_{date}.json` (e.g. `output/restaurant_98005_2026-02-26.json`) with a crude sorting. Do some cleanup to remove fake restaurants at the bottom that don't have reviews.

### Step 2: Rank restaurants & export CSV

```bash
# Simplest — auto-derives output CSV from input filename
python -m restaurant_rankings.ranker output/restaurant_98005_2026-02-26.json

# Explicit CSV path
python -m restaurant_rankings.ranker output/restaurant_98005_2026-02-26.json top_restaurants.csv

# Also export ranked JSON and/or an interactive HTML map
python -m restaurant_rankings.ranker output/restaurant_98005_2026-02-26.json \
  --json ranked.json \
  --map ranked_map.html
```

By default this writes a filtered CSV to `output/` (e.g. `output/restaurant_98005_2026-02-26.csv`).

| Flag | Default | Description |
|---|---|---|
| `output_csv` | auto-derived | CSV output path (positional, optional) |
| `--json` | — | Also write full ranked data as JSON |
| `--map` | — | Generate an interactive Folium HTML map (requires `folium`) |
| `--confidence` | `0.95` | Wilson Score confidence (0.90 / 0.95 / 0.99) |
| `--min-rating` | `4.0` | Minimum star rating filter (exclusive `>`) |
| `--min-reviews` | `10` | Minimum review count filter (exclusive `>`) |
| `-v` / `--verbose` | off | Enable debug logging |

#### Confidence levels

| Level | Flag | Behavior |
|---|---|---|
| 0.90 | `--confidence 0.90` | Aggressive — favors newer places with fewer reviews |
| 0.95 | `--confidence 0.95` | Balanced (default) |
| 0.99 | `--confidence 0.99` | Conservative — favors established places with many reviews |

### Step 3: Telegram bot

An interactive Telegram bot that accepts a zip code (or shared location) and returns a paginated, Wilson-Score-ranked list of restaurants.

```bash
python -m restaurant_rankings.bot
```

**How it works:**

1. Send the bot a **5-digit US zip code** (e.g. `98005`) or share your **location** via the attachment menu.
2. The bot checks for a cached scrape in `output/`; if none exists, it scrapes live via the Google Places API.
3. Restaurants are ranked by Wilson Score and filtered (rating > 4.2, reviews > 20).
4. Results are displayed **10 per page** with inline **⬅️ Prev** / **Next ➡️** buttons to navigate through all results.

### Viewing on mobile (Google My Maps)

The CSV output is designed for [Google My Maps](https://mymaps.google.com), which renders pins as a native overlay in the Google Maps app on your phone:

1. Go to [mymaps.google.com](https://mymaps.google.com) and create a new map
2. Click **Import** → upload the CSV file from `output/`
3. Choose **Latitude** and **Longitude** as position columns, **Name** as the title
4. The map auto-syncs to the **Google Maps** app on your phone — open Google Maps → **Saved** → **Maps** to find it

## Testing

```bash
source .venv/bin/activate
python -m pytest -v
```

The test suite covers all core modules (**76 tests**) and runs in under 1 second with no network calls or API keys required:

| Test file | Module under test | Tests | What's covered |
|---|---|---|---|
| `tests/test_scraper.py` | `restaurant_rankings.scraper` | 20 | GCP response validation, coordinate geometry, result processing & deduplication |
| `tests/test_ranker.py` | `restaurant_rankings.ranker` | 41 | Wilson Score math, ranking from fixture file, filtering, color mapping, interpretation text, CSV export, JSON export |
| `tests/test_pagination.py` | `restaurant_rankings.pagination` | 15 | Page formatting, button visibility, callback data, page clamping, HTML escaping, async handler |

### Test fixtures

`tests/fixtures/sample_restaurants.json` contains a curated set of 10 restaurants with varying ratings, review counts, and edge cases (null rating, null location, very few reviews) used by both `test_ranker.py` and available for any future tests.

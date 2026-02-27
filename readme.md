# Restaurant Rankings

Scripts to statistically rank restaurants in a city using Google Places data and the [Wilson Score Interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval). Based on this [blog post](https://mattsayar.com/where-are-the-best-restaurants-in-my-city-a-statistical-analysis/).

## Project Structure

```
.
├── .env.example                  # Template for API key config
├── .python-version               # Pinned Python version (pyenv)
├── requirements.txt              # Python dependencies
├── gcp_places_api_scraper.py     # Step 1 — scrape restaurants via Google Places API
├── wilson_script.py              # Step 2 — rank & generate interactive map
├── output/                       # All generated data (gitignored)
│   ├── restaurant_98005_2026-02-26.json
│   ├── restaurant_98005_2026-02-26_wilson_ranked.json
│   └── restaurant_98005_2026-02-26_map.html
└── readme.md
```

## Setup

1. **Install Python 3.11+ via [pyenv](https://github.com/pyenv/pyenv)** (recommended):

    ```bash
    pyenv install 3.11.11
    pyenv local 3.11.11
    ```

2. **Create a virtual environment and install dependencies:**

    ```bash
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

3. **Configure your API key:**

    ```bash
    cp .env.example .env
    ```

    Edit `.env` and paste your Google Cloud Platform API key. Get one from [Google Cloud Platform](https://developers.google.com/maps/documentation/javascript/cloud-setup).

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
python gcp_places_api_scraper.py

# Custom coordinates and radius
python gcp_places_api_scraper.py --lat 47.6754 --lng -122.3808 --radius 5
```

| Flag | Default | Description |
|---|---|---|
| `--lat` | `47.625435` | Center latitude |
| `--lng` | `-122.154905` | Center longitude |
| `--radius` | `15` | Search radius in km |
| `--types` | `restaurant` | Place types to search (space-separated) |
| `-v` / `--verbose` | off | Enable debug logging |

This writes a file to `output/` named `{category}_{zip_code}_{date}.json` (e.g. `output/restaurant_98005_2026-02-26.json`) with a crude sorting. Do some cleanup to remove fake restaurants at the bottom that don't have reviews.

### Step 2: Rank restaurants and generate map

```bash
python wilson_script.py \
  output/restaurant_98005_2026-02-26.json \
  restaurant_98005_2026-02-26_wilson_ranked.json \
  --confidence 0.95 \
  --map restaurant_98005_2026-02-26_map.html
```

This writes two files to `output/`:

- **`*_wilson_ranked.json`** — all restaurants ranked by Wilson Score Interval
- **`*_map.html`** — interactive map with restaurants filtered by rating and review count

Use `--verbose` / `-v` to see detailed debug output.

#### Confidence levels

| Level | Flag | Behavior |
|---|---|---|
| 0.90 | `--confidence 0.90` | Aggressive — favors newer places with fewer reviews |
| 0.95 | `--confidence 0.95` | Balanced (default) |
| 0.99 | `--confidence 0.99` | Conservative — favors established places with many reviews |

#### Map filters

| Flag | Default | Description |
|---|---|---|
| `--min-rating` | `4.0` | Minimum star rating (exclusive `>`) |
| `--min-reviews` | `10` | Minimum review count (exclusive `>`) |

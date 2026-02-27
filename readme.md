# Restaurant Rankings

Scripts to statistically rank restaurants in a city using Google Places data and the [Wilson Score Interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval). Based on this [blog post](https://mattsayar.com/where-are-the-best-restaurants-in-my-city-a-statistical-analysis/).

## Project Structure

```
.
├── .env.example                  # Template for API key config
├── .python-version               # Pinned Python version (pyenv)
├── requirements.txt              # Python dependencies
├── gcp_places_api_scraper.py     # Step 1 — scrape restaurants via Google Places API
├── wilson_script.py              # Step 2 — rank & export CSV (+ optional map/JSON)
├── output/                       # All generated data (gitignored)
│   ├── restaurant_98005_2026-02-26.json   # scraped data
│   └── restaurant_98005_2026-02-26.csv    # ranked CSV (default output)
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

    # Optional: install folium for HTML map generation (--map)
    pip install folium
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

### Step 2: Rank restaurants & export CSV

```bash
# Simplest — auto-derives output CSV from input filename
python wilson_script.py output/restaurant_98005_2026-02-26.json

# Explicit CSV path
python wilson_script.py output/restaurant_98005_2026-02-26.json top_restaurants.csv

# Also export ranked JSON and/or an interactive HTML map
python wilson_script.py output/restaurant_98005_2026-02-26.json \
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

### Viewing on mobile (Google My Maps)

The CSV output is designed for [Google My Maps](https://mymaps.google.com), which renders pins as a native overlay in the Google Maps app on your phone:

1. Go to [mymaps.google.com](https://mymaps.google.com) and create a new map
2. Click **Import** → upload the CSV file from `output/`
3. Choose **Latitude** and **Longitude** as position columns, **Name** as the title
4. The map auto-syncs to the **Google Maps** app on your phone — open Google Maps → **Saved** → **Maps** to find it

# Restaurant Rankings

Scripts to statistically rank restaurants in a city using Google Places data and the [Wilson Score Interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval). Based on this [blog post](https://mattsayar.com/where-are-the-best-restaurants-in-my-city-a-statistical-analysis/).

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

    Edit `.env` and paste your Google Cloud Platform API key. Get one from [Google Cloud Platform](https://developers.google.com/maps/documentation/javascript/cloud-setup) (Places API must be enabled).

## Usage

### Step 1: Scrape restaurant data

Optionally update the center coordinates and radius in `gcp_places_api_scraper.py` for your city:

```python
# Bellevue, WA coordinates
CENTER_LAT = 47.625435
CENTER_LNG = -122.154905
RADIUS_KM = 15
```

Then run:

```bash
python gcp_places_api_scraper.py
```

This outputs `restaurants.json` with a crude sorting. Do some cleanup to remove fake restaurants at the bottom that don't have reviews.

### Step 2: Rank restaurants using Wilson Score

```bash
python wilson_script.py restaurants.json restaurants_wilson_ranked.json --confidence 0.95
```

This outputs a `.json` file with all the restaurants ranked by Wilson Score Interval.

Use `--verbose` / `-v` to see detailed debug output.

#### Confidence levels

| Level | Flag | Behavior |
|---|---|---|
| 0.90 | `--confidence 0.90` | Aggressive — favors newer places with fewer reviews |
| 0.95 | `--confidence 0.95` | Balanced (default) |
| 0.99 | `--confidence 0.99` | Conservative — favors established places with many reviews |

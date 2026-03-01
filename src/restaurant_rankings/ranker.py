import csv
import json
import logging
import math
import os
from typing import List, Dict, Tuple

from scipy.stats import norm

OUTPUT_DIR = "output"

logger = logging.getLogger(__name__)


def wilson_score(positive_ratings: float, total_ratings: int, confidence_level: float = 0.95) -> float:
    """
    Calculate Wilson Score Interval for a given rating and number of ratings.

    Args:
        positive_ratings: Number of positive ratings (converted from overall rating).
        total_ratings: Total number of ratings received.
        confidence_level: Statistical confidence level (0.90 / 0.95 / 0.99).

    Returns:
        Lower bound of the Wilson Score Interval.
    """
    if total_ratings == 0:
        return 0

    if not 0 < confidence_level < 1:
        raise ValueError("Confidence level must be between 0 and 1")

    z_score = norm.ppf(1 - (1 - confidence_level) / 2)
    observed_proportion = positive_ratings / total_ratings
    z_squared = z_score * z_score

    numerator = (observed_proportion +
                 (z_squared / (2 * total_ratings)) -
                 (z_score * math.sqrt((observed_proportion * (1 - observed_proportion) +
                                       z_squared / (4 * total_ratings)) / total_ratings)))
    denominator = 1 + z_squared / total_ratings

    result = numerator / denominator
    logger.debug(
        "wilson_score: obs=%.4f z²=%.4f → %.4f",
        observed_proportion, z_squared, result,
    )
    return result


def rank_restaurants(input_file: str, confidence_level: float = 0.95) -> List[Dict]:
    """Read restaurants from *input_file*, score & sort by Wilson Score.

    Returns the ranked list (highest score first).  Each restaurant dict
    is augmented with ``wilson_score`` and ``ranking_metadata`` keys.
    """
    with open(input_file, "r") as fh:
        data = json.load(fh)

    restaurant_list = data.get("restaurants", [])

    for restaurant in restaurant_list:
        star_rating = restaurant.get("rating") or 0
        rating_count = restaurant.get("user_ratings_total") or 0

        positive_ratio = max(0, (star_rating - 3) / 2)
        positive_rating_count = positive_ratio * rating_count

        logger.debug("Restaurant: %s  ⭐ %s  (%d reviews)",
                     restaurant.get("name"), star_rating, rating_count)

        wilson_lower_bound = wilson_score(
            positive_ratings=positive_rating_count,
            total_ratings=rating_count,
            confidence_level=confidence_level,
        )
        restaurant["wilson_score"] = wilson_lower_bound
        restaurant["ranking_metadata"] = {
            "confidence_level": confidence_level,
            "positive_ratio": positive_ratio,
            "positive_ratings": positive_rating_count,
        }

    sorted_restaurants = sorted(
        restaurant_list,
        key=lambda r: r.get("wilson_score", 0),
        reverse=True,
    )
    logger.info("Ranked %d restaurants (confidence=%.2f)", len(sorted_restaurants), confidence_level)
    return sorted_restaurants

def get_ranking_interpretation(confidence_level: float) -> str:
    """Human-readable description of how *confidence_level* biases rankings."""
    if confidence_level >= 0.99:
        return "Conservative: strongly favors established places with many ratings"
    if confidence_level >= 0.95:
        return "Balanced: moderate balance between rating and review count"
    if confidence_level >= 0.90:
        return "Aggressive: gives more weight to high ratings with fewer reviews"
    return "Very aggressive: strongly favors high ratings regardless of review count"


# ---------------------------------------------------------------------------
# Filtering helper (shared by CSV + map)
# ---------------------------------------------------------------------------

def _filter_restaurants(
    restaurants: List[Dict],
    min_rating: float = 4.0,
    min_reviews: int = 10,
) -> List[Dict]:
    """Return restaurants that pass rating/review thresholds, sorted by Wilson score."""
    filtered = [
        r for r in restaurants
        if (r.get("rating") or 0) > min_rating
        and (r.get("user_ratings_total") or 0) > min_reviews
        and r.get("location")
    ]
    filtered.sort(key=lambda r: r.get("wilson_score", 0), reverse=True)
    return filtered


# ---------------------------------------------------------------------------
# CSV export  (default output)
# ---------------------------------------------------------------------------

def export_csv(
    restaurants: List[Dict],
    output_csv: str,
    min_rating: float = 4.0,
    min_reviews: int = 10,
) -> None:
    """Export qualifying restaurants to CSV for Google My Maps import.

    Columns are chosen so that Google My Maps auto-detects location and labels.
    """
    filtered = _filter_restaurants(restaurants, min_rating, min_reviews)
    logger.info(
        "CSV: %d / %d restaurants pass filters (rating > %s, reviews > %d)",
        len(filtered), len(restaurants), min_rating, min_reviews,
    )
    if not filtered:
        logger.warning("No restaurants passed the filter — skipping CSV export.")
        return

    fieldnames = [
        "Rank", "Name", "Latitude", "Longitude",
        "Rating", "Reviews", "Wilson Score", "Address", "Google Maps URL",
    ]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rank, r in enumerate(filtered, start=1):
            writer.writerow({
                "Rank": rank,
                "Name": r.get("name", "Unknown"),
                "Latitude": r["location"]["latitude"],
                "Longitude": r["location"]["longitude"],
                "Rating": r.get("rating", ""),
                "Reviews": r.get("user_ratings_total", 0),
                "Wilson Score": round(r.get("wilson_score", 0), 4),
                "Address": r.get("address", ""),
                "Google Maps URL": r.get("maps_url", ""),
            })
    logger.info("Wrote %d restaurants to %s", len(filtered), output_csv)


# ---------------------------------------------------------------------------
# JSON export  (optional, --json)
# ---------------------------------------------------------------------------

def export_json(
    restaurants: List[Dict],
    output_json: str,
    confidence_level: float = 0.95,
) -> None:
    """Write the full ranked list with metadata to a JSON file."""
    output_data = {
        "restaurants": restaurants,
        "metadata": {
            "ranking_method": "Wilson Score Interval",
            "confidence_level": confidence_level,
            "total_restaurants": len(restaurants),
            "ranking_interpretation": get_ranking_interpretation(confidence_level),
        },
    }
    with open(output_json, "w") as fh:
        json.dump(output_data, fh, indent=2)
    logger.info("Wrote ranked JSON (%d restaurants) to %s", len(restaurants), output_json)


# ---------------------------------------------------------------------------
# Map generation  (optional, --map)  —  folium imported lazily
# ---------------------------------------------------------------------------

def _rating_color(wilson_score_val: float) -> str:
    """Return a marker color based on the Wilson score."""
    if wilson_score_val >= 0.85:
        return "darkgreen"
    if wilson_score_val >= 0.70:
        return "green"
    if wilson_score_val >= 0.55:
        return "orange"
    return "lightgray"


def generate_map(
    restaurants: List[Dict],
    output_html: str,
    min_rating: float = 4.0,
    min_reviews: int = 10,
) -> None:
    """Generate an interactive Folium map of qualifying restaurants.

    ``folium`` is imported lazily so it is only required when this
    function is actually called (i.e. when ``--map`` is passed).
    """
    try:
        import folium  # optional dependency
    except ImportError:
        raise SystemExit(
            "Error: 'folium' is required for map generation.\n"
            "Install it with:  pip install folium"
        )

    filtered = _filter_restaurants(restaurants, min_rating, min_reviews)
    logger.info(
        "Map: %d / %d restaurants pass filters (rating > %s, reviews > %d)",
        len(filtered), len(restaurants), min_rating, min_reviews,
    )
    if not filtered:
        logger.warning("No restaurants passed the filter — skipping map generation.")
        return

    mean_lat = sum(r["location"]["latitude"] for r in filtered) / len(filtered)
    mean_lng = sum(r["location"]["longitude"] for r in filtered) / len(filtered)
    m = folium.Map(location=[mean_lat, mean_lng], zoom_start=13)

    for rank, r in enumerate(filtered, start=1):
        lat = r["location"]["latitude"]
        lng = r["location"]["longitude"]
        name = r.get("name", "Unknown")
        rating = r.get("rating", "N/A")
        reviews = r.get("user_ratings_total", 0)
        w_score = r.get("wilson_score", 0)
        address = r.get("address", "")
        maps_url = r.get("maps_url", "")

        popup_html = (
            f"<b>#{rank} {name}</b><br>"
            f"⭐ {rating} ({reviews} reviews)<br>"
            f"Wilson: {w_score:.4f}<br>"
            f"{address}<br>"
        )
        if maps_url:
            popup_html += f'<a href="{maps_url}" target="_blank">Google Maps</a>'

        folium.Marker(
            location=[lat, lng],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"#{rank} {name} — ⭐ {rating}",
            icon=folium.Icon(color=_rating_color(w_score), icon="cutlery", prefix="fa"),
        ).add_to(m)

    m.save(output_html)
    logger.info("Wrote map with %d restaurants to %s", len(filtered), output_html)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Rank restaurants using Wilson Score and export to CSV.",
    )
    parser.add_argument(
        "input_file",
        help="Scraped JSON file (e.g. output/restaurant_98005_2026-02-26.json)",
    )
    parser.add_argument(
        "output_csv", nargs="?", default=None,
        help="CSV output path (default: auto-derived from input, written to output/)",
    )
    parser.add_argument(
        "--json", dest="json_file", metavar="JSON_FILE",
        help="Also write the full ranked data as JSON (written to output/ by default)",
    )
    parser.add_argument(
        "--map", dest="map_file", metavar="HTML_FILE",
        help="Generate an interactive HTML map (requires folium; written to output/ by default)",
    )
    parser.add_argument(
        "--confidence", type=float, default=0.95,
        help="Confidence level for Wilson Score (0.90 / 0.95 / 0.99, default: 0.95)",
    )
    parser.add_argument(
        "--min-rating", type=float, default=4.0,
        help="Minimum star rating filter (default: 4.0)",
    )
    parser.add_argument(
        "--min-reviews", type=int, default=10,
        help="Minimum review count filter (default: 10)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging output",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    def _resolve(path: str) -> str:
        """Put bare filenames inside OUTPUT_DIR."""
        return path if os.sep in path else os.path.join(OUTPUT_DIR, path)

    input_file = args.input_file
    base_name = os.path.splitext(os.path.basename(input_file))[0]

    # CSV (primary output — auto-derive from input if omitted)
    csv_file = _resolve(args.output_csv) if args.output_csv else os.path.join(OUTPUT_DIR, f"{base_name}.csv")

    # Optional JSON
    json_file = _resolve(args.json_file) if args.json_file else None

    # Optional map
    map_file = _resolve(args.map_file) if args.map_file else None

    # ---- rank ----
    ranked = rank_restaurants(input_file, args.confidence)

    # ---- CSV (always) ----
    export_csv(ranked, csv_file, min_rating=args.min_rating, min_reviews=args.min_reviews)

    # ---- JSON (optional) ----
    if json_file:
        export_json(ranked, json_file, confidence_level=args.confidence)

    # ---- Map (optional, lazy-imports folium) ----
    if map_file:
        generate_map(ranked, map_file, min_rating=args.min_rating, min_reviews=args.min_reviews)

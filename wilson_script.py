import csv
import json
import logging
import math
import os
from typing import List, Dict

import folium
from scipy.stats import norm

OUTPUT_DIR = os.path.join(os.path.dirname(__file__) or ".", "output")

logger = logging.getLogger(__name__)

def wilson_score(positive_ratings: float, total_ratings: int, confidence_level: float = 0.95) -> float:
    """
    Calculate Wilson Score Interval for a given rating and number of ratings.
    
    Args:
        positive_ratings (float): The number of positive ratings (converted from overall rating)
        total_ratings (int): Total number of ratings received
        confidence_level (float): Statistical confidence level between 0 and 1:
            - 0.90 for more aggressive rankings (favors newer places)
            - 0.95 for balanced rankings (default)
            - 0.99 for conservative rankings (favors established places)
    
    Returns:
        float: Lower bound of Wilson Score Interval
    """
    if total_ratings == 0:
        return 0

    if not 0 < confidence_level < 1:
        raise ValueError("Confidence level must be between 0 and 1")
    
    # Z-score for the given confidence level
    z_score = norm.ppf(1 - (1 - confidence_level) / 2)
    logger.debug("z_score = %s", z_score)
    
    # Observed proportion of positive ratings
    observed_proportion = positive_ratings / total_ratings
    logger.debug("observed_proportion = %s", observed_proportion)
    
    # Wilson score calculation components
    z_squared = z_score * z_score
    logger.debug("z_squared = %s", z_squared)
    
    numerator = (observed_proportion + 
                 (z_squared / (2 * total_ratings)) - 
                 (z_score * math.sqrt((observed_proportion * (1 - observed_proportion) + 
                                     z_squared / (4 * total_ratings)) / total_ratings)))
    logger.debug("numerator = %s", numerator)
    
    denominator = 1 + z_squared / total_ratings
    logger.debug("denominator = %s", denominator)
    
    result = numerator / denominator
    logger.debug("result = %s", result)
    
    return result

def rank_restaurants(input_file: str, output_file: str, confidence_level: float = 0.95):
    """
    Read restaurants from input JSON file, rank them using Wilson Score,
    and write sorted results to output JSON file.
    
    Args:
        input_file (str): Path to input JSON file
        output_file (str): Path to output JSON file
        confidence_level (float): Confidence level for Wilson Score calculation:
            - 0.90 favors newer places with fewer ratings
            - 0.95 provides balanced rankings (default)
            - 0.99 favors established places with many ratings
    """
    # Read input JSON file
    with open(input_file, 'r') as file:
        data = json.load(file)
    
    restaurant_list = data.get('restaurants', [])
    
    # Calculate Wilson score for each restaurant
    for restaurant in restaurant_list:
        star_rating = restaurant.get('rating') or 0
        rating_count = restaurant.get('user_ratings_total') or 0
        
        # Convert 5-star rating to proportion of positive ratings
        logger.debug("star_rating: %s", star_rating)
        positive_ratio = max(0, (star_rating - 3) / 2)
        positive_rating_count = positive_ratio * rating_count
        
        # Calculate Wilson score with specified confidence level
        logger.debug("Restaurant: %s", restaurant.get('name'))
        wilson_lower_bound = wilson_score(
            positive_ratings=positive_rating_count,
            total_ratings=rating_count,
            confidence_level=confidence_level
        )
        restaurant['wilson_score'] = wilson_lower_bound
        
        # Add additional metadata about the calculation
        restaurant['ranking_metadata'] = {
            'confidence_level': confidence_level,
            'positive_ratio': positive_ratio,
            'positive_ratings': positive_rating_count
        }
    
    # Sort restaurants by Wilson score in descending order
    sorted_restaurants = sorted(
        restaurant_list,
        key=lambda restaurant: restaurant.get('wilson_score', 0),
        reverse=True
    )
    
    # Prepare output data with metadata
    output_data = {
        'restaurants': sorted_restaurants,
        'metadata': {
            'ranking_method': 'Wilson Score Interval',
            'confidence_level': confidence_level,
            'total_restaurants': len(restaurant_list),
            'ranking_interpretation': get_ranking_interpretation(confidence_level)
        }
    }
    
    # Write sorted results to output file
    with open(output_file, 'w') as file:
        json.dump(output_data, file, indent=2)

def get_ranking_interpretation(confidence_level: float) -> str:
    """
    Provides an interpretation of how the confidence level affects rankings.
    """
    if confidence_level >= 0.99:
        return "Conservative ranking: Strongly favors established places with many ratings"
    elif confidence_level >= 0.95:
        return "Balanced ranking: Moderate balance between ratings and rating count"
    elif confidence_level >= 0.90:
        return "Aggressive ranking: Gives more weight to places with high ratings but fewer reviews"
    else:
        return "Very aggressive ranking: Strongly favors high ratings regardless of review count"


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

    Args:
        restaurants: List of restaurant dicts (already scored with ``wilson_score``).
        output_html: Path for the output HTML file.
        min_rating: Minimum star rating to include (exclusive >).
        min_reviews: Minimum review count to include (exclusive >).
    """
    # Filter restaurants
    filtered = [
        r for r in restaurants
        if (r.get("rating") or 0) > min_rating
        and (r.get("user_ratings_total") or 0) > min_reviews
        and r.get("location")
    ]

    # Sort by Wilson score descending
    filtered.sort(key=lambda r: r.get("wilson_score", 0), reverse=True)

    logger.info(
        "Map: %d / %d restaurants pass filters (rating > %s, reviews > %d)",
        len(filtered), len(restaurants), min_rating, min_reviews,
    )

    if not filtered:
        logger.warning("No restaurants passed the filter — skipping map generation.")
        return

    # Center the map on the mean location of filtered restaurants
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


def export_csv(
    restaurants: List[Dict],
    output_csv: str,
    min_rating: float = 4.0,
    min_reviews: int = 10,
) -> None:
    """Export qualifying restaurants to CSV for Google My Maps import.

    The CSV uses columns that Google My Maps recognizes automatically:
    - ``Name`` and ``Address`` for labels
    - ``Latitude`` and ``Longitude`` for pin placement
    - Extra columns (Rating, Reviews, Wilson Score, Google Maps URL) become
      info-window fields when you click a pin.

    Args:
        restaurants: List of restaurant dicts (already scored with ``wilson_score``).
        output_csv: Path for the output CSV file.
        min_rating: Minimum star rating to include (exclusive >).
        min_reviews: Minimum review count to include (exclusive >).
    """
    filtered = [
        r for r in restaurants
        if (r.get("rating") or 0) > min_rating
        and (r.get("user_ratings_total") or 0) > min_reviews
        and r.get("location")
    ]
    filtered.sort(key=lambda r: r.get("wilson_score", 0), reverse=True)

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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Rank restaurants using Wilson Score Interval')
    parser.add_argument('input_file', help='Input JSON file path (e.g. output/restaurant_98005_2026-02-26.json)')
    parser.add_argument('output_file', help='Output JSON file path (written to output/ by default)')
    parser.add_argument('--confidence', type=float, default=0.95,
                        help='Confidence level (0.90, 0.95, or 0.99)')
    parser.add_argument('--map', dest='map_file', metavar='HTML_FILE',
                        help='Generate an interactive HTML map of top restaurants (written to output/ by default)')
    parser.add_argument('--csv', dest='csv_file', metavar='CSV_FILE', default=None,
                        help='CSV output filename (default: auto-derived from input filename)')
    parser.add_argument('--no-csv', dest='no_csv', action='store_true',
                        help='Skip CSV generation')
    parser.add_argument('--min-rating', type=float, default=4.0,
                        help='Minimum star rating for map/CSV filter (default: 4.0)')
    parser.add_argument('--min-reviews', type=int, default=10,
                        help='Minimum review count for map/CSV filter (default: 10)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging output')
    
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Resolve paths — place bare filenames inside output/
    input_file = args.input_file
    output_file = (
        args.output_file if os.sep in args.output_file
        else os.path.join(OUTPUT_DIR, args.output_file)
    )
    map_file = None
    if args.map_file:
        map_file = (
            args.map_file if os.sep in args.map_file
            else os.path.join(OUTPUT_DIR, args.map_file)
        )
    # CSV: on by default (auto-derive filename from input), unless --no-csv
    csv_file = None
    if not args.no_csv:
        if args.csv_file:
            csv_file = (
                args.csv_file if os.sep in args.csv_file
                else os.path.join(OUTPUT_DIR, args.csv_file)
            )
        else:
            # Auto-derive: input "restaurant_98005_2026-02-26.json" → "restaurant_98005_2026-02-26.csv"
            base = os.path.splitext(os.path.basename(args.input_file))[0]
            csv_file = os.path.join(OUTPUT_DIR, f"{base}.csv")

    rank_restaurants(input_file, output_file, args.confidence)

    # Load ranked data once for map and/or CSV export
    if map_file or csv_file:
        with open(output_file, 'r') as f:
            ranked_data = json.load(f)
        ranked_restaurants = ranked_data['restaurants']

        if map_file:
            generate_map(
                ranked_restaurants, map_file,
                min_rating=args.min_rating, min_reviews=args.min_reviews,
            )
        if csv_file:
            export_csv(
                ranked_restaurants, csv_file,
                min_rating=args.min_rating, min_reviews=args.min_reviews,
            )

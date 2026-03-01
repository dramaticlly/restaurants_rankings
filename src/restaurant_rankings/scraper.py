import json
import logging
import math
import os
from datetime import date
from typing import List, Dict, Set
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

OUTPUT_DIR = "output"


def _check_gcp_response(response: requests.Response, api_name: str) -> dict:
    """Validate a GCP API response and return parsed JSON.

    Handles error formats for both REST-style APIs (Geocoding — errors in
    the JSON body with HTTP 200) and gRPC-transcoded APIs (Places New —
    errors as HTTP 4xx with an ``error`` object).

    Raises ``SystemExit`` with a descriptive message on any API failure so
    the process terminates early.
    """
    # Places API (New) returns HTTP 4xx/5xx on errors
    if not response.ok:
        try:
            error_data = response.json()
            error_msg = error_data.get("error", {}).get("message", response.text)
        except (ValueError, KeyError):
            error_msg = response.text
        raise SystemExit(f"{api_name} HTTP {response.status_code}: {error_msg}")

    data = response.json()

    # Geocoding-style: status field in JSON body (HTTP is always 200)
    status = data.get("status")
    if status == "REQUEST_DENIED":
        raise SystemExit(
            f"{api_name} request denied: {data.get('error_message', 'unknown error')}. "
            f"Ensure the {api_name} is enabled in your GCP project."
        )
    if status and status not in ("OK", "ZERO_RESULTS"):
        raise SystemExit(
            f"{api_name} error: {status} — {data.get('error_message', '')}"
        )

    return data


def reverse_geocode_zip(api_key: str, lat: float, lng: float) -> str:
    """Derive the zip/postal code from coordinates using Google Geocoding API.

    Also serves as an early API key validation — if the key is invalid or
    the Geocoding API is not enabled, this will raise and abort before the
    expensive Places API crawl begins.
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "latlng": f"{lat},{lng}",
        "result_type": "postal_code",
        "key": api_key,
    }
    response = requests.get(url, params=params)
    data = _check_gcp_response(response, "Geocoding API")

    if data.get("status") == "OK" and data.get("results"):
        for component in data["results"][0].get("address_components", []):
            if "postal_code" in component.get("types", []):
                return component["long_name"]

    logger.warning("Could not derive zip code from coordinates; falling back to 'unknown'")
    return "unknown"


@dataclass
class Coordinates:
    latitude: float
    longitude: float


class RestaurantFinder:
    def __init__(self, api_key: str, center_lat: float, center_lng: float, radius_km: float,
                 included_types: List[str] | None = None):
        self.api_key = api_key
        self.center = Coordinates(latitude=center_lat, longitude=center_lng)
        self.radius_km = radius_km
        self.included_types = included_types or ["restaurant"]
        self.seen_place_ids: Set[str] = set()
        self.results: List[Dict] = []
        self.base_url = "https://places.googleapis.com/v1/places:searchNearby"
        self.headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "places.displayName.text,places.primaryTypeDisplayName.text,places.rating,places.id,places.shortFormattedAddress,places.userRatingCount,places.location,places.googleMapsUri"
        }

    def _calculate_new_coordinates(self, center: Coordinates, distance_km: float, bearing: float) -> Coordinates:
        """Calculate new coordinates given a starting point, distance, and bearing."""
        R = 6371  # Earth's radius in kilometers

        lat1 = math.radians(center.latitude)
        lon1 = math.radians(center.longitude)
        bearing = math.radians(bearing)

        lat2 = math.asin(
            math.sin(lat1) * math.cos(distance_km / R) +
            math.cos(lat1) * math.sin(distance_km / R) * math.cos(bearing)
        )

        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(distance_km / R) * math.cos(lat1),
            math.cos(distance_km / R) - math.sin(lat1) * math.sin(lat2)
        )

        return Coordinates(
            latitude=math.degrees(lat2),
            longitude=math.degrees(lon2)
        )

    def _get_restaurants_for_location(self, location: Coordinates, radius_meters: float) -> List[Dict]:
        """Make API call to get restaurants for a specific location and radius."""
        payload = {
            "includedTypes": self.included_types,
            "maxResultCount": 20,
            "rankPreference": "DISTANCE",
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": location.latitude,
                        "longitude": location.longitude
                    },
                    "radius": radius_meters
                }
            }
        }

        response = requests.post(self.base_url, headers=self.headers, json=payload)
        data = _check_gcp_response(response, "Places API")
        return data.get("places", [])

    def _process_results(self, places: List[Dict]) -> None:
        """Process and deduplicate restaurant results."""
        logger.debug("Processing %d places.", len(places))
        for place in places:
            place_id = place.get("id")
            if place_id and place_id not in self.seen_place_ids:
                self.seen_place_ids.add(place_id)

                processed_result = {
                    "name": place.get("displayName", {}).get("text"),
                    "place_id": place_id,
                    "type": place.get("primaryTypeDisplayName", {}).get("text"),
                    "rating": place.get("rating"),
                    "user_ratings_total": place.get("userRatingCount"),
                    "location": place.get("location"),
                    "address": place.get("shortFormattedAddress"),
                    "maps_url": place.get("googleMapsUri")
                }

                self.results.append(processed_result)

    def find_all_restaurants(self) -> List[Dict]:
        """Find all restaurants within the specified radius."""
        # Calculate smaller search radius to handle API limit
        # Using 500m radius for each search to ensure overlap and complete coverage
        search_radius_km = 0.5
        search_radius_meters = search_radius_km * 1000

        # Calculate number of circles needed
        num_circles = math.ceil(self.radius_km / (search_radius_km * 1.5))  # 1.5 for overlap

        # Create grid of search points
        for ring in range(num_circles):
            if ring == 0:
                # Search center point
                restaurants = self._get_restaurants_for_location(
                    self.center,
                    search_radius_meters
                )
                self._process_results(restaurants)
            else:
                # Calculate points around the ring
                ring_radius_km = ring * (search_radius_km * 1.5)
                num_points = max(8 * ring, 8)  # Increase points for outer rings

                for i in range(num_points):
                    bearing = (360 / num_points) * i
                    location = self._calculate_new_coordinates(
                        self.center,
                        ring_radius_km,
                        bearing
                    )

                    restaurants = self._get_restaurants_for_location(
                        location,
                        search_radius_meters
                    )
                    self._process_results(restaurants)

        # Sort results by rating (highest first)
        logger.info("Found %d restaurants.", len(self.results))
        self.results.sort(
            key=lambda x: (
                x.get("rating") if x.get("rating") is not None else 0,
                x.get("user_ratings_total") if x.get("user_ratings_total") is not None else 0
            ),
            reverse=True
        )
        return self.results


def main(lat: float, lng: float, radius_km: float, included_types: List[str]):
    load_dotenv()

    API_KEY = os.environ.get("GCP_API_KEY")
    if not API_KEY:
        raise SystemExit(
            "Error: GCP_API_KEY not set. "
            "Copy .env.example to .env and fill in your API key."
        )

    # Derive zip code first — validates the API key before the expensive crawl
    zip_code = reverse_geocode_zip(API_KEY, lat, lng)
    logger.info("Resolved zip code: %s", zip_code)

    finder = RestaurantFinder(API_KEY, lat, lng, radius_km,
                              included_types=included_types)
    results = finder.find_all_restaurants()

    # Build output path: output/{category}_{zip_code}_{date}.json
    category = "_".join(included_types)
    today = date.today().isoformat()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_file = os.path.join(OUTPUT_DIR, f"{category}_{zip_code}_{today}.json")

    # Save results to JSON file
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"restaurants": results}, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %d restaurants to %s", len(results), output_file)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape restaurants from Google Places API within a radius of given coordinates."
    )
    parser.add_argument('--lat', type=float, default=47.625435,
                        help='Center latitude (default: 47.625435 — Bellevue, WA)')
    parser.add_argument('--lng', type=float, default=-122.154905,
                        help='Center longitude (default: -122.154905 — Bellevue, WA)')
    parser.add_argument('--radius', type=float, default=15,
                        help='Search radius in km (default: 15)')
    parser.add_argument('--types', nargs='+', default=['restaurant'],
                        help='Place types to search for (default: restaurant)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging output')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )

    main(args.lat, args.lng, args.radius, args.types)

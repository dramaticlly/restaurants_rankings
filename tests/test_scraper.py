"""Tests for the scraper module (restaurant_rankings.scraper).

Tests cover the pure/testable logic:
- GCP API response validation  (_check_gcp_response)
- Coordinate calculations       (Coordinates, _calculate_new_coordinates)
- Result processing & dedup     (_process_results)
"""

import math
from unittest.mock import MagicMock

import pytest

from restaurant_rankings.scraper import (
    Coordinates,
    RestaurantFinder,
    _check_gcp_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Build a fake ``requests.Response``."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 400
    resp.text = text
    resp.json.return_value = json_data or {}
    return resp


def _make_finder(**kwargs) -> RestaurantFinder:
    """Create a ``RestaurantFinder`` with sensible defaults (no real API calls)."""
    defaults = dict(api_key="FAKE_KEY", center_lat=47.61, center_lng=-122.20, radius_km=5)
    defaults.update(kwargs)
    return RestaurantFinder(**defaults)


# ---------------------------------------------------------------------------
# _check_gcp_response
# ---------------------------------------------------------------------------

class TestCheckGcpResponse:
    """Tests for the GCP API response validator."""

    def test_ok_response_returns_json(self):
        data = {"status": "OK", "results": [{"place_id": "abc"}]}
        resp = _mock_response(200, data)
        assert _check_gcp_response(resp, "Test API") == data

    def test_zero_results_is_ok(self):
        data = {"status": "ZERO_RESULTS"}
        resp = _mock_response(200, data)
        assert _check_gcp_response(resp, "Test API") == data

    def test_http_error_raises_system_exit(self):
        error_body = {"error": {"message": "API key not valid"}}
        resp = _mock_response(403, error_body)
        with pytest.raises(SystemExit, match="HTTP 403"):
            _check_gcp_response(resp, "Places API")

    def test_http_error_fallback_to_text(self):
        resp = _mock_response(500, text="Internal Server Error")
        resp.json.side_effect = ValueError("not JSON")
        with pytest.raises(SystemExit, match="HTTP 500"):
            _check_gcp_response(resp, "Places API")

    def test_request_denied_raises_system_exit(self):
        data = {"status": "REQUEST_DENIED", "error_message": "key disabled"}
        resp = _mock_response(200, data)
        with pytest.raises(SystemExit, match="request denied"):
            _check_gcp_response(resp, "Geocoding API")

    def test_unknown_status_raises_system_exit(self):
        data = {"status": "OVER_QUERY_LIMIT", "error_message": "quota exceeded"}
        resp = _mock_response(200, data)
        with pytest.raises(SystemExit, match="OVER_QUERY_LIMIT"):
            _check_gcp_response(resp, "Geocoding API")

    def test_no_status_field_returns_json(self):
        """Places API (New) responses don't include a 'status' field."""
        data = {"places": [{"id": "xyz"}]}
        resp = _mock_response(200, data)
        assert _check_gcp_response(resp, "Places API") == data


# ---------------------------------------------------------------------------
# Coordinates & geometry
# ---------------------------------------------------------------------------

class TestCoordinates:
    """Tests for the Coordinates dataclass."""

    def test_creation(self):
        c = Coordinates(latitude=47.61, longitude=-122.20)
        assert c.latitude == 47.61
        assert c.longitude == -122.20


class TestCalculateNewCoordinates:
    """Tests for RestaurantFinder._calculate_new_coordinates."""

    def setup_method(self):
        self.finder = _make_finder()

    def test_zero_distance_returns_same_point(self):
        origin = Coordinates(47.61, -122.20)
        result = self.finder._calculate_new_coordinates(origin, 0.0, 0)
        assert abs(result.latitude - origin.latitude) < 1e-6
        assert abs(result.longitude - origin.longitude) < 1e-6

    def test_north_increases_latitude(self):
        origin = Coordinates(47.0, -122.0)
        result = self.finder._calculate_new_coordinates(origin, 10.0, 0)  # bearing=0 → north
        assert result.latitude > origin.latitude
        assert abs(result.longitude - origin.longitude) < 0.01  # longitude barely changes

    def test_south_decreases_latitude(self):
        origin = Coordinates(47.0, -122.0)
        result = self.finder._calculate_new_coordinates(origin, 10.0, 180)  # bearing=180 → south
        assert result.latitude < origin.latitude

    def test_east_increases_longitude(self):
        origin = Coordinates(47.0, -122.0)
        result = self.finder._calculate_new_coordinates(origin, 10.0, 90)  # bearing=90 → east
        assert result.longitude > origin.longitude

    def test_distance_is_approximately_correct(self):
        """Verify the haversine round-trip: 10 km north should be ~10 km away."""
        origin = Coordinates(47.0, -122.0)
        result = self.finder._calculate_new_coordinates(origin, 10.0, 0)

        # Haversine distance check
        R = 6371
        dlat = math.radians(result.latitude - origin.latitude)
        dlon = math.radians(result.longitude - origin.longitude)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(origin.latitude)) *
             math.cos(math.radians(result.latitude)) *
             math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        distance_km = R * c
        assert abs(distance_km - 10.0) < 0.05  # within 50 m

    def test_symmetry_opposite_bearings(self):
        """Going 5 km north then 5 km south should roughly return to origin."""
        origin = Coordinates(47.0, -122.0)
        north = self.finder._calculate_new_coordinates(origin, 5.0, 0)
        back = self.finder._calculate_new_coordinates(north, 5.0, 180)
        assert abs(back.latitude - origin.latitude) < 0.001
        assert abs(back.longitude - origin.longitude) < 0.001


# ---------------------------------------------------------------------------
# _process_results — deduplication & dict transformation
# ---------------------------------------------------------------------------

class TestProcessResults:
    """Tests for RestaurantFinder._process_results."""

    def _sample_places(self) -> list[dict]:
        """Return a minimal list mimicking the Google Places API response."""
        return [
            {
                "id": "place_A",
                "displayName": {"text": "Restaurant A"},
                "primaryTypeDisplayName": {"text": "Italian Restaurant"},
                "rating": 4.5,
                "userRatingCount": 200,
                "location": {"latitude": 47.61, "longitude": -122.20},
                "shortFormattedAddress": "123 Main St, Bellevue",
                "googleMapsUri": "https://maps.google.com/?cid=A",
            },
            {
                "id": "place_B",
                "displayName": {"text": "Restaurant B"},
                "primaryTypeDisplayName": {"text": "Sushi Restaurant"},
                "rating": 4.8,
                "userRatingCount": 100,
                "location": {"latitude": 47.62, "longitude": -122.21},
                "shortFormattedAddress": "456 Oak Ave, Bellevue",
                "googleMapsUri": "https://maps.google.com/?cid=B",
            },
        ]

    def test_basic_processing(self):
        finder = _make_finder()
        finder._process_results(self._sample_places())

        assert len(finder.results) == 2
        assert finder.results[0]["name"] == "Restaurant A"
        assert finder.results[0]["place_id"] == "place_A"
        assert finder.results[0]["rating"] == 4.5
        assert finder.results[0]["user_ratings_total"] == 200
        assert finder.results[0]["location"] == {"latitude": 47.61, "longitude": -122.20}
        assert finder.results[0]["address"] == "123 Main St, Bellevue"
        assert finder.results[0]["maps_url"] == "https://maps.google.com/?cid=A"

    def test_deduplication(self):
        """Processing the same places twice should not create duplicates."""
        finder = _make_finder()
        places = self._sample_places()

        finder._process_results(places)
        finder._process_results(places)  # duplicate call

        assert len(finder.results) == 2
        assert len(finder.seen_place_ids) == 2

    def test_missing_id_is_skipped(self):
        """A place without an 'id' field should be silently skipped."""
        finder = _make_finder()
        finder._process_results([{"displayName": {"text": "No ID"}}])
        assert len(finder.results) == 0

    def test_partial_data_fills_nones(self):
        """A place with only an id should still produce a result with None fields."""
        finder = _make_finder()
        finder._process_results([{"id": "sparse"}])

        assert len(finder.results) == 1
        r = finder.results[0]
        assert r["place_id"] == "sparse"
        assert r["name"] is None
        assert r["rating"] is None

    def test_incremental_processing(self):
        """Two separate batches should merge correctly."""
        finder = _make_finder()
        batch1 = [self._sample_places()[0]]
        batch2 = [self._sample_places()[1]]

        finder._process_results(batch1)
        assert len(finder.results) == 1

        finder._process_results(batch2)
        assert len(finder.results) == 2

    def test_mixed_duplicates_and_new(self):
        """A batch containing both new and duplicate entries."""
        finder = _make_finder()
        places = self._sample_places()
        finder._process_results(places)

        # New batch: one duplicate + one new
        new_place = {
            "id": "place_C",
            "displayName": {"text": "Restaurant C"},
            "rating": 4.0,
            "userRatingCount": 50,
            "location": {"latitude": 47.63, "longitude": -122.22},
            "shortFormattedAddress": "789 Pine St, Bellevue",
            "googleMapsUri": "https://maps.google.com/?cid=C",
        }
        finder._process_results([places[0], new_place])

        assert len(finder.results) == 3
        names = {r["name"] for r in finder.results}
        assert "Restaurant C" in names

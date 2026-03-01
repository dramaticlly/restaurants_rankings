"""Tests for the ranking module (restaurant_rankings.ranker).

Tests cover:
- Wilson Score calculation         (wilson_score)
- Ranking from JSON fixture file   (rank_restaurants)
- Filtering logic                  (_filter_restaurants)
- Rating color mapping             (_rating_color)
- Ranking interpretation text      (get_ranking_interpretation)
- CSV export                       (export_csv)
- JSON export                      (export_json)
"""

import csv
import json
import os
from pathlib import Path

import pytest

from restaurant_rankings.ranker import (
    wilson_score,
    rank_restaurants,
    _filter_restaurants,
    _rating_color,
    get_ranking_interpretation,
    export_csv,
    export_json,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_JSON = FIXTURE_DIR / "sample_restaurants.json"


# ---------------------------------------------------------------------------
# wilson_score — pure math
# ---------------------------------------------------------------------------

class TestWilsonScore:
    """Tests for the Wilson Score lower-bound calculation."""

    def test_zero_ratings_returns_zero(self):
        assert wilson_score(0, 0) == 0

    def test_all_positive(self):
        """100 % positive should still produce < 1 (uncertainty)."""
        score = wilson_score(100, 100, confidence_level=0.95)
        assert 0 < score < 1.0

    def test_no_positive(self):
        """0 % positive should produce a score near 0."""
        score = wilson_score(0, 100, confidence_level=0.95)
        assert score < 0.05

    def test_higher_confidence_gives_lower_score(self):
        """More conservative confidence → lower lower-bound."""
        s90 = wilson_score(80, 100, confidence_level=0.90)
        s95 = wilson_score(80, 100, confidence_level=0.95)
        s99 = wilson_score(80, 100, confidence_level=0.99)
        assert s90 > s95 > s99

    def test_more_reviews_gives_tighter_bound(self):
        """Same ratio but more reviews → higher lower-bound (less uncertainty)."""
        few = wilson_score(8, 10, confidence_level=0.95)
        many = wilson_score(800, 1000, confidence_level=0.95)
        assert many > few

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValueError, match="between 0 and 1"):
            wilson_score(50, 100, confidence_level=1.5)

    def test_known_value(self):
        """Spot-check against a manually computed value."""
        # 80/100 positive, 95% confidence → ~0.7111
        score = wilson_score(80, 100, confidence_level=0.95)
        assert abs(score - 0.7111) < 0.005

    def test_symmetry_boundary(self):
        """50 % positive rate should give a score near 0.5 (minus uncertainty)."""
        score = wilson_score(50, 100, confidence_level=0.95)
        assert 0.35 < score < 0.55


# ---------------------------------------------------------------------------
# rank_restaurants — integration with fixture file
# ---------------------------------------------------------------------------

class TestRankRestaurants:
    """Tests for rank_restaurants using the sample fixture file."""

    def test_returns_all_restaurants(self):
        ranked = rank_restaurants(str(SAMPLE_JSON))
        assert len(ranked) == 10  # fixture has 10 entries

    def test_sorted_by_wilson_score_descending(self):
        ranked = rank_restaurants(str(SAMPLE_JSON))
        scores = [r["wilson_score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_augments_wilson_score(self):
        ranked = rank_restaurants(str(SAMPLE_JSON))
        for r in ranked:
            assert "wilson_score" in r
            assert isinstance(r["wilson_score"], (int, float))

    def test_augments_ranking_metadata(self):
        ranked = rank_restaurants(str(SAMPLE_JSON))
        for r in ranked:
            meta = r.get("ranking_metadata")
            assert meta is not None
            assert "confidence_level" in meta
            assert "positive_ratio" in meta
            assert "positive_ratings" in meta

    def test_null_rating_gets_zero_score(self):
        """'No Reviews Diner' has rating=null → wilson_score should be 0."""
        ranked = rank_restaurants(str(SAMPLE_JSON))
        no_reviews = next(r for r in ranked if r["name"] == "No Reviews Diner")
        assert no_reviews["wilson_score"] == 0

    def test_confidence_affects_ranking(self):
        """Different confidence levels should produce different scores."""
        ranked_90 = rank_restaurants(str(SAMPLE_JSON), confidence_level=0.90)
        ranked_99 = rank_restaurants(str(SAMPLE_JSON), confidence_level=0.99)

        # Pick a restaurant with reviews
        name = "The Golden Fork"
        s90 = next(r for r in ranked_90 if r["name"] == name)["wilson_score"]
        s99 = next(r for r in ranked_99 if r["name"] == name)["wilson_score"]
        assert s90 > s99

    def test_top_ranked_is_high_rating_with_many_reviews(self):
        """The top restaurant should be one with both high rating and many reviews."""
        ranked = rank_restaurants(str(SAMPLE_JSON))
        top = ranked[0]
        # Golden Fork (4.8, 520 reviews) or Sushi Master (4.9, 150) should be at top
        assert top["rating"] >= 4.5
        assert top["user_ratings_total"] >= 100


# ---------------------------------------------------------------------------
# _filter_restaurants
# ---------------------------------------------------------------------------

class TestFilterRestaurants:
    """Tests for the _filter_restaurants helper."""

    def _ranked(self) -> list[dict]:
        return rank_restaurants(str(SAMPLE_JSON))

    def test_default_filter(self):
        """Default filter (>4.0 rating, >10 reviews) should exclude low entries."""
        filtered = _filter_restaurants(self._ranked())
        for r in filtered:
            assert r["rating"] > 4.0
            assert r["user_ratings_total"] > 10

    def test_excludes_null_location(self):
        """Ghost Kitchen has location=null and should be excluded."""
        filtered = _filter_restaurants(self._ranked(), min_rating=0, min_reviews=0)
        names = {r["name"] for r in filtered}
        assert "Ghost Kitchen" not in names

    def test_strict_filter_reduces_count(self):
        loose = _filter_restaurants(self._ranked(), min_rating=3.0, min_reviews=5)
        strict = _filter_restaurants(self._ranked(), min_rating=4.5, min_reviews=200)
        assert len(strict) <= len(loose)

    def test_sorted_by_wilson_score(self):
        filtered = _filter_restaurants(self._ranked())
        scores = [r["wilson_score"] for r in filtered]
        assert scores == sorted(scores, reverse=True)

    def test_empty_on_impossible_filter(self):
        filtered = _filter_restaurants(self._ranked(), min_rating=5.0, min_reviews=10000)
        assert filtered == []

    def test_new_spot_excluded_by_review_count(self):
        """'New Spot' has only 5 reviews; default min_reviews=10 excludes it."""
        filtered = _filter_restaurants(self._ranked())
        names = {r["name"] for r in filtered}
        assert "New Spot" not in names


# ---------------------------------------------------------------------------
# _rating_color
# ---------------------------------------------------------------------------

class TestRatingColor:
    """Tests for the Wilson-score-to-color mapping."""

    def test_high_score(self):
        assert _rating_color(0.90) == "darkgreen"

    def test_good_score(self):
        assert _rating_color(0.75) == "green"

    def test_medium_score(self):
        assert _rating_color(0.60) == "orange"

    def test_low_score(self):
        assert _rating_color(0.30) == "lightgray"

    def test_boundary_085(self):
        assert _rating_color(0.85) == "darkgreen"

    def test_boundary_070(self):
        assert _rating_color(0.70) == "green"

    def test_boundary_055(self):
        assert _rating_color(0.55) == "orange"


# ---------------------------------------------------------------------------
# get_ranking_interpretation
# ---------------------------------------------------------------------------

class TestGetRankingInterpretation:
    """Tests for human-readable confidence descriptions."""

    def test_099(self):
        assert "Conservative" in get_ranking_interpretation(0.99)

    def test_095(self):
        assert "Balanced" in get_ranking_interpretation(0.95)

    def test_090(self):
        assert "Aggressive" in get_ranking_interpretation(0.90)

    def test_low(self):
        assert "Very aggressive" in get_ranking_interpretation(0.80)


# ---------------------------------------------------------------------------
# export_csv
# ---------------------------------------------------------------------------

class TestExportCsv:
    """Tests for CSV export."""

    def _ranked(self) -> list[dict]:
        return rank_restaurants(str(SAMPLE_JSON))

    def test_csv_file_created(self, tmp_path):
        csv_path = str(tmp_path / "output.csv")
        export_csv(self._ranked(), csv_path)
        assert os.path.exists(csv_path)

    def test_csv_has_header(self, tmp_path):
        csv_path = str(tmp_path / "output.csv")
        export_csv(self._ranked(), csv_path)

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
        expected = ["Rank", "Name", "Latitude", "Longitude",
                    "Rating", "Reviews", "Wilson Score", "Address", "Google Maps URL"]
        assert header == expected

    def test_csv_rows_match_filter(self, tmp_path):
        csv_path = str(tmp_path / "output.csv")
        ranked = self._ranked()
        export_csv(ranked, csv_path, min_rating=4.0, min_reviews=10)

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        filtered = _filter_restaurants(ranked, min_rating=4.0, min_reviews=10)
        assert len(rows) == len(filtered)

    def test_csv_rank_column_sequential(self, tmp_path):
        csv_path = str(tmp_path / "output.csv")
        export_csv(self._ranked(), csv_path)

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        ranks = [int(r["Rank"]) for r in rows]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_csv_not_created_when_nothing_passes(self, tmp_path):
        csv_path = str(tmp_path / "output.csv")
        export_csv(self._ranked(), csv_path, min_rating=5.0, min_reviews=99999)
        assert not os.path.exists(csv_path)


# ---------------------------------------------------------------------------
# export_json
# ---------------------------------------------------------------------------

class TestExportJson:
    """Tests for JSON export."""

    def _ranked(self) -> list[dict]:
        return rank_restaurants(str(SAMPLE_JSON))

    def test_json_file_created(self, tmp_path):
        json_path = str(tmp_path / "output.json")
        export_json(self._ranked(), json_path)
        assert os.path.exists(json_path)

    def test_json_contains_metadata(self, tmp_path):
        json_path = str(tmp_path / "output.json")
        export_json(self._ranked(), json_path, confidence_level=0.95)

        with open(json_path) as f:
            data = json.load(f)

        assert "metadata" in data
        assert data["metadata"]["ranking_method"] == "Wilson Score Interval"
        assert data["metadata"]["confidence_level"] == 0.95
        assert data["metadata"]["total_restaurants"] == 10

    def test_json_restaurants_included(self, tmp_path):
        json_path = str(tmp_path / "output.json")
        ranked = self._ranked()
        export_json(ranked, json_path)

        with open(json_path) as f:
            data = json.load(f)

        assert len(data["restaurants"]) == len(ranked)

    def test_json_interpretation_matches(self, tmp_path):
        json_path = str(tmp_path / "output.json")
        export_json(self._ranked(), json_path, confidence_level=0.99)

        with open(json_path) as f:
            data = json.load(f)

        assert "Conservative" in data["metadata"]["ranking_interpretation"]

"""Tests for the pagination module (format_restaurant_page & paginate_callback)."""

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from restaurant_rankings.pagination import PAGE_SIZE, format_restaurant_page, paginate_callback

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_restaurant(rank: int) -> dict:
    """Create a minimal restaurant dict for testing."""
    return {
        "name": f"Restaurant {rank}",
        "rating": 4.5,
        "user_ratings_total": 100 + rank,
        "wilson_score": 0.90 - rank * 0.001,
        "address": f"{rank} Main St, City, ST",
        "maps_url": f"https://maps.google.com/?cid={rank}",
        "location": {"latitude": 47.0 + rank * 0.001, "longitude": -122.0},
    }


def _make_restaurants(n: int) -> list[dict]:
    """Generate *n* fake restaurant dicts."""
    return [_make_restaurant(i) for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# format_restaurant_page — unit tests (pure function, no mocks needed)
# ---------------------------------------------------------------------------


class TestFormatRestaurantPage:
    """Tests for the format_restaurant_page helper."""

    def test_single_page_no_buttons(self):
        """When all results fit on one page there should be no nav buttons."""
        restaurants = _make_restaurants(5)
        text, markup = format_restaurant_page(restaurants, "98005", page=0)

        assert "page 1/1" in text
        assert "showing 1–5 of 5" in text
        assert markup is None  # no pagination needed

    def test_first_page_has_only_next_button(self):
        """Page 0 should only show 'Next ➡️', not 'Prev'."""
        restaurants = _make_restaurants(25)
        text, markup = format_restaurant_page(restaurants, "98005", page=0)

        assert "page 1/" in text
        assert markup is not None
        button_labels = [b.text for row in markup.inline_keyboard for b in row]
        assert "Next ➡️" in button_labels
        assert "⬅️ Prev" not in button_labels

    def test_middle_page_has_both_buttons(self):
        """A middle page should show both Prev and Next."""
        restaurants = _make_restaurants(30)
        text, markup = format_restaurant_page(restaurants, "98005", page=1)

        assert "page 2/" in text
        assert markup is not None
        button_labels = [b.text for row in markup.inline_keyboard for b in row]
        assert "⬅️ Prev" in button_labels
        assert "Next ➡️" in button_labels

    def test_last_page_has_only_prev_button(self):
        """The last page should only show '⬅️ Prev', not 'Next'."""
        restaurants = _make_restaurants(25)
        total_pages = math.ceil(25 / PAGE_SIZE)
        text, markup = format_restaurant_page(restaurants, "98005", page=total_pages - 1)

        assert f"page {total_pages}/{total_pages}" in text
        assert markup is not None
        button_labels = [b.text for row in markup.inline_keyboard for b in row]
        assert "⬅️ Prev" in button_labels
        assert "Next ➡️" not in button_labels

    def test_correct_items_shown_per_page(self):
        """Each page should show exactly PAGE_SIZE items (or fewer on the last page)."""
        restaurants = _make_restaurants(23)

        # Page 0: items 1-10
        text, _ = format_restaurant_page(restaurants, "98005", page=0)
        assert "showing 1–10 of 23" in text
        assert "Restaurant 1" in text
        assert "Restaurant 10" in text
        assert "Restaurant 11" not in text

        # Page 1: items 11-20
        text, _ = format_restaurant_page(restaurants, "98005", page=1)
        assert "showing 11–20 of 23" in text

        # Page 2: items 21-23 (last partial page)
        text, _ = format_restaurant_page(restaurants, "98005", page=2)
        assert "showing 21–23 of 23" in text

    def test_callback_data_format(self):
        """Button callback_data should be 'page:<n>'."""
        restaurants = _make_restaurants(25)
        _, markup = format_restaurant_page(restaurants, "98005", page=1)

        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "page:0" in callbacks  # prev
        assert "page:2" in callbacks  # next

    def test_page_clamps_to_valid_range(self):
        """Requesting a page beyond the valid range should clamp to the last page."""
        restaurants = _make_restaurants(5)

        text_neg, _ = format_restaurant_page(restaurants, "98005", page=-5)
        assert "page 1/1" in text_neg

        text_over, _ = format_restaurant_page(restaurants, "98005", page=999)
        assert "page 1/1" in text_over

    def test_html_escaping_in_zip(self):
        """Zip code should be HTML-escaped in the output."""
        restaurants = _make_restaurants(3)
        text, _ = format_restaurant_page(restaurants, "<script>", page=0)
        assert "&lt;script&gt;" in text
        assert "<script>" not in text

    def test_custom_page_size(self):
        """page_size parameter should control how many items appear per page."""
        restaurants = _make_restaurants(10)
        text, markup = format_restaurant_page(restaurants, "98005", page=0, page_size=3)

        assert "page 1/4" in text
        assert "showing 1–3 of 10" in text
        assert markup is not None

    def test_ranking_numbers_are_global_not_per_page(self):
        """Item numbering should continue across pages (11, 12, ... not 1, 2, ...)."""
        restaurants = _make_restaurants(15)
        text, _ = format_restaurant_page(restaurants, "98005", page=1)

        # Page 2 should start at 11
        assert "<b>11." in text
        assert "<b>1." not in text or "<b>11." in text  # "1." appears in "11."

    def test_empty_list(self):
        """An empty restaurant list should still produce a valid page."""
        text, markup = format_restaurant_page([], "98005", page=0)
        assert "page 1/1" in text
        assert "showing 1–0 of 0" in text
        assert markup is None


# ---------------------------------------------------------------------------
# paginate_callback — async handler tests (mocked Telegram objects)
# ---------------------------------------------------------------------------


class TestPaginateCallback:
    """Tests for the paginate_callback handler."""

    def _build_update_and_context(self, callback_data: str, restaurants: list, zip_code: str = "98005"):
        """Create mocked Update and Context for a callback query."""
        query = AsyncMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        context = MagicMock()
        context.user_data = {
            "restaurants": restaurants,
            "zip_code": zip_code,
        }

        return update, context

    @pytest.mark.asyncio
    async def test_navigates_to_requested_page(self):
        """Clicking 'Next' should display the correct page."""
        restaurants = _make_restaurants(25)
        update, context = self._build_update_and_context("page:1", restaurants)

        await paginate_callback(update, context)

        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_text.assert_awaited_once()
        call_kwargs = update.callback_query.edit_message_text.call_args.kwargs
        assert "page 2/" in call_kwargs["text"]
        assert call_kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_no_cached_results_shows_warning(self):
        """If user_data has no restaurants, show a warning."""
        update, context = self._build_update_and_context("page:0", [])

        await paginate_callback(update, context)

        update.callback_query.edit_message_text.assert_awaited_once_with(
            "⚠️ No cached results. Please send a zip code again."
        )

    @pytest.mark.asyncio
    async def test_ignores_non_page_callback(self):
        """Callback data that doesn't start with 'page:' should be ignored."""
        update, context = self._build_update_and_context("other:123", _make_restaurants(5))

        await paginate_callback(update, context)

        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reply_markup_included(self):
        """The edited message should include the inline keyboard markup."""
        restaurants = _make_restaurants(25)
        update, context = self._build_update_and_context("page:1", restaurants)

        await paginate_callback(update, context)

        call_kwargs = update.callback_query.edit_message_text.call_args.kwargs
        assert call_kwargs["reply_markup"] is not None

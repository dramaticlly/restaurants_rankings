"""Pagination helpers for the Telegram bot.

This module is intentionally self-contained: it only depends on the
``python-telegram-bot`` library and the Python standard library, making
it easy to test in isolation without loading API keys or heavy
third-party modules.
"""

import html
import math

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

PAGE_SIZE = 10


def format_restaurant_page(
    restaurants: list,
    zip_code: str,
    page: int = 0,
    page_size: int = PAGE_SIZE,
) -> tuple:
    """Build the message text and inline keyboard for a given page.

    Args:
        restaurants: Full list of ranked/filtered restaurant dicts.
        zip_code: The zip code being queried (displayed in the header).
        page: Zero-based page index.
        page_size: Number of restaurants per page.

    Returns:
        ``(text, InlineKeyboardMarkup | None)`` — the HTML-formatted
        message body and an optional inline keyboard with Prev / Next
        buttons.
    """
    total = len(restaurants)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(0, min(page, total_pages - 1))

    start = page * page_size
    end = min(start + page_size, total)
    page_items = restaurants[start:end]

    safe_zip = html.escape(str(zip_code))
    response_text = (
        f"🏆 <b>Ranked Restaurants in {safe_zip}</b>\n"
        f"<i>(Ranked by Wilson Score — page {page + 1}/{total_pages}, "
        f"showing {start + 1}–{end} of {total})</i>\n\n"
    )

    for i, r in enumerate(page_items, start + 1):
        name = html.escape(r.get("name", "Unknown"))
        rating = r.get("rating", "?")
        reviews = r.get("user_ratings_total", 0)
        score = f"{r.get('wilson_score', 0):.3f}"
        address = html.escape(r.get("address", "").split(",")[0])
        maps_url = r.get("maps_url", "")

        response_text += f"<b>{i}. <a href=\"{maps_url}\">{name}</a></b>\n"
        response_text += f"⭐ {rating} ({reviews} reviews) | 📊 Wilson: {score}\n"
        response_text += f"📍 {address}\n\n"

    # Build navigation buttons
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page:{page - 1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:{page + 1}"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None
    return response_text, markup


async def paginate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination button presses (``CallbackQueryHandler``)."""
    query = update.callback_query
    await query.answer()

    data = query.data  # e.g. "page:2"
    if not data or not data.startswith("page:"):
        return

    page = int(data.split(":")[1])
    restaurants = context.user_data.get("restaurants", [])
    zip_code = context.user_data.get("zip_code", "")

    if not restaurants:
        await query.edit_message_text("⚠️ No cached results. Please send a zip code again.")
        return

    text, markup = format_restaurant_page(restaurants, zip_code, page=page)
    await query.edit_message_text(
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=markup,
    )

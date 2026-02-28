import asyncio
import glob
import html
import logging
import os
import pprint
from datetime import date

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Import your existing logic!
from gcp_places_api_scraper import main as run_scraper, reverse_geocode_zip
from wilson_script import rank_restaurants, _filter_restaurants

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
GCP_API_KEY = os.environ.get("GCP_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")


def forward_geocode_zip(zip_code: str, api_key: str):
    """Convert a zip code to lat/lng using Google Geocoding API."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": zip_code, "key": api_key}
    response = requests.get(url, params=params).json()

    if response.get("status") == "OK" and response.get("results"):
        location = response["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    return None, None


def get_cached_file(zip_code: str, category: str = "restaurant"):
    """Check if a JSON file already exists for this zip code."""
    search_pattern = f"output/{category}_{zip_code}_*.json"
    matching_files = glob.glob(search_pattern)

    if matching_files:
        # Sort to get the most recent file
        matching_files.sort(reverse=True)
        return matching_files[0]
    return None


async def process_restaurant_request(update: Update, context: ContextTypes.DEFAULT_TYPE, lat: float, lng: float,
                                     zip_code: str = None):
    """Core logic to handle the scrape/cache and rank pipeline."""
    chat_id = update.effective_chat.id

    # If no zip code was provided (user sent a location pin), reverse geocode it
    if not zip_code:
        zip_code = reverse_geocode_zip(GCP_API_KEY, lat, lng)
        if zip_code == "unknown":
            await context.bot.send_message(chat_id, "Sorry, I couldn't determine the zip code for that location.")
            return

    await context.bot.send_message(chat_id, f"🔍 Looking up best restaurants for **{zip_code}**...",
                                   parse_mode="Markdown")

    category = "restaurant"

    # --- CACHING LOGIC ---
    cached_file = get_cached_file(zip_code, category)

    if cached_file:
        await context.bot.send_message(chat_id, f"⚡ Found recently cached data! Ranking now...")
        json_file_path = cached_file
    else:
        await context.bot.send_message(chat_id,
                                       f"⏳ No recent cache found. Scraping Google Places API (this takes ~15-30 seconds)...")
        try:
            # Run your existing blocking scraper in a separate thread so it doesn't freeze the bot
            await asyncio.to_thread(run_scraper, lat, lng, 5.0, [category])
            today = date.today().isoformat()
            json_file_path = f"output/{category}_{zip_code}_{today}.json"
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ Scraping failed: {e}")
            return

    # --- RANKING LOGIC ---
    try:
        # 1. Score using Wilson Interval
        ranked_restaurants = rank_restaurants(json_file_path, confidence_level=0.95)

        # 2. Filter out low ratings/reviews using your existing logic
        filtered_restaurants = _filter_restaurants(ranked_restaurants, min_rating=4.2, min_reviews=20)
        logger.info(f"Found total of {len(filtered_restaurants)}")

        # 3. Grab the Top 10
        top_10 = filtered_restaurants[:10]

        logger.info(f"Top 10 found: {pprint.pformat(top_10)}")

        if not top_10:
            await context.bot.send_message(chat_id, "No restaurants passed the strict quality filters in this area.")
            return

        # --- FORMAT RESULT CARD ---
        safe_zip = html.escape(str(zip_code))

        response_text = f"🏆 <b>Top 10 Restaurants in {safe_zip}</b>\n<i>(Ranked by Wilson Score)</i>\n\n"

        for i, r in enumerate(top_10, 1):
            # html.escape automatically handles characters like &, <, > safely.
            # We don't need to escape normal punctuation like (), -, or . anymore!
            name = html.escape(r.get("name", "Unknown"))
            rating = r.get("rating", "?")
            reviews = r.get("user_ratings_total", 0)
            score = f"{r.get('wilson_score', 0):.3f}"
            address = html.escape(r.get("address", "").split(",")[0])
            maps_url = r.get("maps_url", "")

            # Use standard HTML tags: <b> for bold, <a> for links
            response_text += f"<b>{i}. <a href=\"{maps_url}\">{name}</a></b>\n"
            response_text += f"⭐ {rating} ({reviews} reviews) | 📊 Wilson: {score}\n"
            response_text += f"📍 {address}\n\n"

        # Send the formatted card using HTML parse mode
        await context.bot.send_message(
            chat_id=chat_id,
            text=response_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Error during ranking: {e}")
        await context.bot.send_message(chat_id, "❌ Something went wrong while ranking the data.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input (Zip Codes)."""
    text = update.message.text.strip()

    # Basic validation for 5 digit US zip code
    if len(text) == 5 and text.isdigit():
        lat, lng = forward_geocode_zip(text, GCP_API_KEY)
        if lat and lng:
            await process_restaurant_request(update, context, lat, lng, zip_code=text)
        else:
            await update.message.reply_text("❌ Could not find coordinates for that zip code.")
    else:
        await update.message.reply_text(
            "Please send a valid 5-digit US zip code, or share your location via the attachment menu 📎.")


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Telegram location attachments."""
    lat = update.message.location.latitude
    lng = update.message.location.longitude
    await process_restaurant_request(update, context, lat, lng)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message."""
    welcome_text = (
        "🍽️ *Welcome to the Restaurant Ranker!*\n\n"
        "Send me a **Zip Code** (e.g. `98005`) or tap 📎 to send your **Location**.\n"
        "I will scientifically rank the top restaurants around you using the Wilson Score Interval!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


if __name__ == "__main__":
    if not GCP_API_KEY or not TELEGRAM_BOT_TOKEN:
        print("Missing GCP_API_KEY or TELEGRAM_BOT_TOKEN in .env")
        exit(1)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))

    print("Bot is running...")
    app.run_polling()

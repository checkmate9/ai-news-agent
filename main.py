"""
main.py

Entry point for the AI news agent.

Usage:
    python3 main.py            # Start scheduled twice-daily operation + Telegram /links bot
    python3 main.py --run-now  # Run a single digest immediately and exit
"""

import argparse
import asyncio
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from scraper import scrape_all
from summarizer import summarize_items
from telegram_bot import send_digest, send_error_alert
from scheduler import create_scheduler
from config import SCHEDULE_TIMES, TIMEZONE, TELEGRAM_BOT_TOKEN

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/agent.log"),
    ],
)
logger = logging.getLogger(__name__)

LINKS_FILE    = Path("logs/latest_links.json")
SEEN_FILE     = Path("logs/seen_urls.json")
ACTIVITY_FILE = Path("logs/activity.json")

# Lock prevents two digest runs overlapping (scheduled + /runnow at the same time)
_digest_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Seen-URL deduplication (persisted across runs, 24 h TTL)
# ---------------------------------------------------------------------------

def load_seen_urls() -> set:
    """Return the set of item URLs already sent in the past 24 hours."""
    if not SEEN_FILE.exists():
        return set()
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        data = json.loads(SEEN_FILE.read_text())
        return {e["url"] for e in data if e.get("ts", 0) > cutoff}
    except Exception:
        return set()


def save_seen_urls(items: list) -> None:
    """Add this run's item URLs to the store, pruning entries older than 24 h."""
    try:
        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff = now_ts - 86400

        # Load existing entries, drop expired ones
        entries: list[dict] = []
        if SEEN_FILE.exists():
            try:
                entries = [e for e in json.loads(SEEN_FILE.read_text())
                           if e.get("ts", 0) > cutoff]
            except Exception:
                pass

        # Append URLs from this run that aren't already stored
        existing = {e["url"] for e in entries}
        for item in items:
            url = item.get("url", "")
            if url and url not in existing:
                entries.append({"url": url, "ts": now_ts})

        SEEN_FILE.write_text(json.dumps(entries))
    except Exception as exc:
        logger.warning("Failed to save seen URLs: %s", exc)


# ---------------------------------------------------------------------------
# Activity log (shown in dashboard)
# ---------------------------------------------------------------------------

def log_activity(event: dict) -> None:
    """Prepend an activity event to logs/activity.json, keeping the last 50."""
    try:
        events: list[dict] = []
        if ACTIVITY_FILE.exists():
            try:
                events = json.loads(ACTIVITY_FILE.read_text())
            except Exception:
                pass
        events.insert(0, {"ts": datetime.now().strftime("%Y-%m-%d %H:%M"), **event})
        ACTIVITY_FILE.write_text(json.dumps(events[:50], indent=2))
    except Exception as exc:
        logger.warning("Failed to log activity: %s", exc)


# ---------------------------------------------------------------------------
# Trending links persistence
# ---------------------------------------------------------------------------

def save_trending_links(items: list) -> None:
    """Save top 3 items by score to logs/latest_links.json for the /links command."""
    try:
        scored = sorted(items, key=lambda x: x.get("score") or 0, reverse=True)
        top3 = scored[:3]
        data = {
            "updated": datetime.now().strftime("%a %b %d at %H:%M"),
            "links": [
                {
                    "title": it["title"],
                    "url": it["url"],
                    "source": it["source"],
                    "score": it.get("score"),
                }
                for it in top3
            ],
        }
        LINKS_FILE.write_text(json.dumps(data, indent=2))
        logger.info("Saved top 3 trending links to %s", LINKS_FILE)
    except Exception as exc:
        logger.warning("Failed to save trending links: %s", exc)


# ---------------------------------------------------------------------------
# Telegram /links command handler
# ---------------------------------------------------------------------------

async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /links with the top 3 trending links from the last digest run."""
    log_activity({"type": "command", "command": "/links"})

    if not LINKS_FILE.exists():
        await update.message.reply_text(
            "No digest has run yet. Check back after the next scheduled run."
        )
        return

    try:
        data = json.loads(LINKS_FILE.read_text())
        links = data.get("links", [])
        updated = data.get("updated", "unknown")

        if not links:
            await update.message.reply_text("No links available yet.")
            return

        lines = [f"🔗 Top {len(links)} Trending Links\n📅 from digest on {updated}\n"]
        for i, link in enumerate(links, 1):
            score_str = f"  ⬆️ {link['score']} pts" if link.get("score") else ""
            lines.append(
                f"{i}. {link['title']}\n"
                f"   📰 {link['source']}{score_str}\n"
                f"   {link['url']}"
            )

        await update.message.reply_text("\n\n".join(lines))
        logger.info("Sent /links response to chat %s", update.effective_chat.id)

    except Exception as exc:
        logger.error("Error handling /links command: %s", exc, exc_info=True)
        await update.message.reply_text("Error fetching links. Please try again later.")


# ---------------------------------------------------------------------------
# Telegram /help command handler
# ---------------------------------------------------------------------------

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with a list of all available bot commands."""
    log_activity({"type": "command", "command": "/help"})
    text = (
        "🤖 AI News Agent — available commands\n\n"
        "/runnow  — Fetch & send an AI news digest right now\n"
        "/links   — Show top 3 trending links from the last digest\n"
        "/help    — Show this help message"
    )
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# Telegram /runnow command handler
# ---------------------------------------------------------------------------

async def runnow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger an immediate digest run on demand."""
    if not _digest_lock.acquire(blocking=False):
        await update.message.reply_text(
            "⏳ A digest run is already in progress — check back in a minute."
        )
        return

    _digest_lock.release()   # release so run_digest() can reacquire it properly

    await update.message.reply_text(
        "⏳ Fetching AI news now...\nThis usually takes 30–60 seconds. Digest incoming!"
    )

    try:
        result = await asyncio.to_thread(run_digest, "/runnow")
        status     = result.get("status", "unknown")
        items_new  = result.get("items_new", 0)
        n_sources  = result.get("n_sources", 0)
        scraped    = result.get("items_scraped", 0)

        if status == "sent":
            await update.message.reply_text(
                f"✅ Done! Found {items_new} new stories from {n_sources} sources "
                f"(scraped {scraped} total — digest sent above)."
            )
        elif status == "all_seen":
            await update.message.reply_text(
                f"✅ Done — but no new news.\n"
                f"Scraped {scraped} stories, all already sent in a previous run today.\n"
                f"Check back after the next scheduled digest."
            )
        elif status == "no_items":
            await update.message.reply_text(
                "⚠️ Ran, but fetched 0 items — all sources may be unreachable right now."
            )
        elif status == "fetch_error":
            await update.message.reply_text("❌ Fetch failed — check the logs.")
        elif status == "summarize_error":
            await update.message.reply_text(
                f"⚠️ Fetched {items_new} new stories but summarization failed — check the logs."
            )
        elif status == "send_error":
            await update.message.reply_text(
                f"⚠️ Built digest from {items_new} stories but Telegram delivery failed."
            )
        else:
            await update.message.reply_text(f"✅ Done ({status}).")
    except Exception as exc:
        logger.error("Error in /runnow command: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Something went wrong: {exc}")


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run_digest(trigger: str = "scheduler") -> dict:
    """Execute one full fetch → summarize → send cycle (acquires _digest_lock)."""
    if not _digest_lock.acquire(blocking=False):
        logger.warning("Digest already running — skipping duplicate trigger.")
        return {"status": "already_running", "items_scraped": 0, "items_new": 0, "n_sources": 0}
    try:
        return _run_digest_inner(trigger)
    finally:
        _digest_lock.release()


def _run_digest_inner(trigger: str = "scheduler") -> dict:
    """Inner pipeline — called only when lock is already held. Returns a result dict."""
    logger.info("=== Starting digest run (trigger: %s) ===", trigger)

    # 1. Fetch from all sources
    try:
        items, failed_sources = scrape_all()
    except Exception as exc:
        msg = f"Fetching failed entirely: {exc}"
        logger.critical(msg, exc_info=True)
        send_error_alert(msg)
        log_activity({"type": "digest", "trigger": trigger,
                      "items_scraped": 0, "items_new": 0, "status": "fetch_error"})
        return {"status": "fetch_error", "items_scraped": 0, "items_new": 0, "n_sources": 0}

    items_scraped = len(items)

    if not items:
        msg = (
            "Digest run collected zero items. "
            "All sources may be unreachable or had no recent content."
        )
        logger.warning(msg)
        send_error_alert(msg)
        log_activity({"type": "digest", "trigger": trigger,
                      "items_scraped": 0, "items_new": 0, "status": "no_items"})
        return {"status": "no_items", "items_scraped": 0, "items_new": 0, "n_sources": 0}

    if failed_sources:
        logger.warning("These sources failed: %s", ", ".join(failed_sources))

    # 1b. Deduplicate — drop items already sent in a previous run
    seen_urls = load_seen_urls()
    new_items = [it for it in items if it.get("url") not in seen_urls]
    items_new = len(new_items)
    skipped   = items_scraped - items_new

    if skipped:
        logger.info("Deduplication: %d scraped → %d new (%d already sent before)",
                    items_scraped, items_new, skipped)

    # Persist all scraped URLs so next run won't repeat them
    save_seen_urls(items)

    if not new_items:
        logger.info("All scraped items were already sent in a previous run — skipping digest.")
        log_activity({"type": "digest", "trigger": trigger,
                      "items_scraped": items_scraped, "items_new": 0, "status": "all_seen"})
        return {"status": "all_seen", "items_scraped": items_scraped, "items_new": 0, "n_sources": 0}

    # Use only the new (unseen) items from here on
    items = new_items
    n_sources = len({it["source"] for it in items})

    # 2. Summarize — returns (digest_text, top3_items Claude selected as trending)
    try:
        digest, top_items = summarize_items(items)
    except Exception as exc:
        msg = f"Claude summarization failed: {exc}"
        logger.error(msg, exc_info=True)
        send_error_alert(msg)
        log_activity({"type": "digest", "trigger": trigger,
                      "items_scraped": items_scraped, "items_new": items_new,
                      "status": "summarize_error"})
        return {"status": "summarize_error", "items_scraped": items_scraped,
                "items_new": items_new, "n_sources": n_sources}

    # Persist Claude-selected top 3 links for /links command
    save_trending_links(top_items if top_items else items)

    # 3. Send
    try:
        send_digest(digest, len(items), n_sources, failed_sources)
        log_activity({"type": "digest", "trigger": trigger,
                      "items_scraped": items_scraped, "items_new": items_new,
                      "status": "sent"})
    except Exception as exc:
        logger.error("Telegram delivery failed: %s", exc, exc_info=True)
        log_activity({"type": "digest", "trigger": trigger,
                      "items_scraped": items_scraped, "items_new": items_new,
                      "status": "send_error"})
        return {"status": "send_error", "items_scraped": items_scraped,
                "items_new": items_new, "n_sources": n_sources}

    logger.info("=== Digest run complete ===")
    return {"status": "sent", "items_scraped": items_scraped,
            "items_new": items_new, "n_sources": n_sources}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI News Agent — fetches AI news and sends digest via Telegram."
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run a single digest immediately and exit (skips scheduler).",
    )
    args = parser.parse_args()

    if args.run_now:
        run_digest()
        return

    logger.info("Starting scheduler. Times: %s | Timezone: %s", SCHEDULE_TIMES, TIMEZONE)

    # Run the blocking scheduler in a background daemon thread so the main
    # thread is free to run the Telegram Application polling loop.
    scheduler = create_scheduler(run_digest)
    scheduler_thread = threading.Thread(
        target=scheduler.start, daemon=True, name="scheduler"
    )
    scheduler_thread.start()
    logger.info("Scheduler started in background thread.")

    # Build and run the Telegram bot (handles commands in main thread)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("links", links_command))
    app.add_handler(CommandHandler("runnow", runnow_command))
    app.add_handler(CommandHandler(["help", "start"], help_command))
    logger.info("Telegram bot listening for commands (/runnow, /links, /help)...")

    try:
        app.run_polling(drop_pending_updates=True)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        logger.info("Agent stopped.")


if __name__ == "__main__":
    main()

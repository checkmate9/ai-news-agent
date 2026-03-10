"""
telegram_bot.py

Sends the AI news digest (and error alerts) to a Telegram chat via Bot API.
Uses python-telegram-bot v21+ async interface wrapped in asyncio.run().
"""

import asyncio
import logging
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

MAX_TELEGRAM_LENGTH = 4096  # Telegram hard limit per message


# ---------------------------------------------------------------------------
# Message building helpers
# ---------------------------------------------------------------------------

def build_digest_header(n_items: int, n_sources: int, failed: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    lines = [
        f"*AI News Digest* — {now}",
        f"_{n_items} items from {n_sources} sources_",
    ]
    if failed:
        names = ", ".join(failed[:5])
        if len(failed) > 5:
            names += f" (+{len(failed) - 5} more)"
        lines.append(f"_Failed sources: {names}_")
    lines.append("")  # blank line before body
    return "\n".join(lines)


def split_message(text: str, max_length: int = MAX_TELEGRAM_LENGTH) -> list[str]:
    """
    Split text into Telegram-safe chunks on paragraph boundaries (\n\n).
    Falls back to hard character splitting for very long single paragraphs.
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_length:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            if len(paragraph) > max_length:
                # Hard split for extremely long paragraphs
                for i in range(0, len(paragraph), max_length):
                    chunks.append(paragraph[i : i + max_length])
                current = ""
            else:
                current = paragraph

    if current:
        chunks.append(current.strip())

    return chunks


# ---------------------------------------------------------------------------
# Async send helpers
# ---------------------------------------------------------------------------

async def _send_chunks(chunks: list[str]) -> None:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        for i, chunk in enumerate(chunks):
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=chunk,
                disable_web_page_preview=True,
                # No parse_mode — plain text avoids markdown parse errors from Claude output
            )
            logger.info("Sent Telegram chunk %d/%d.", i + 1, len(chunks))
            if i < len(chunks) - 1:
                await asyncio.sleep(1)  # avoid rate limiting between chunks


async def _send_raw(text: str) -> None:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    async with bot:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_digest(digest_body: str, n_items: int, n_sources: int, failed: list[str]) -> None:
    """
    Assemble header + body, split if needed, and send to Telegram.

    Raises:
        TelegramError on delivery failure.
    """
    header = build_digest_header(n_items, n_sources, failed)
    full_text = header + digest_body
    chunks = split_message(full_text)

    if len(chunks) > 1:
        logger.info("Digest split into %d Telegram messages.", len(chunks))

    try:
        asyncio.run(_send_chunks(chunks))
        logger.info("Digest delivered successfully.")
    except TelegramError as exc:
        logger.error("Telegram delivery failed: %s", exc)
        raise


def send_error_alert(message: str) -> None:
    """Send a short error notification to Telegram (plain text, no markdown)."""
    # Strip/escape any characters that break Telegram Markdown parsing
    safe_message = message.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")
    alert = f"⚠️ AI News Agent ERROR\n\n{safe_message}"

    async def _send_plain():
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        async with bot:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=alert,
                # No parse_mode — plain text avoids all markdown issues
            )

    try:
        asyncio.run(_send_plain())
        logger.info("Error alert sent to Telegram.")
    except TelegramError as exc:
        logger.error("Failed to send error alert: %s", exc)

"""
summarizer.py

Sends collected news items to the Anthropic Claude API and returns a
formatted AI news digest string.
"""

import logging
import time
from datetime import datetime, timezone

from anthropic import Anthropic, APIStatusError

from config import (
    CLAUDE_MODEL,
    MAX_TOKENS_SUMMARY,
    LOOKBACK_HOURS,
    SUMMARIZATION_SYSTEM_PROMPT,
    SUMMARIZATION_USER_PROMPT_TEMPLATE,
)
import os

logger = logging.getLogger(__name__)

def _get_client() -> Anthropic:
    """Create client at call time so it always picks up the loaded env var."""
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def format_items_for_prompt(items: list[dict]) -> str:
    """Convert news item dicts into a readable block grouped by source type."""
    if not items:
        return "(no items collected)"

    by_source: dict[str, list[dict]] = {}
    for item in items:
        by_source.setdefault(item["source"], []).append(item)

    lines: list[str] = []
    for source, source_items in by_source.items():
        lines.append(f"\n--- {source} ---")
        for it in source_items:
            ts = it.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                readable_ts = dt.strftime("%b %d %H:%M UTC")
            except (ValueError, TypeError):
                readable_ts = ""

            score_str = f" [score: {it['score']}]" if it.get("score") else ""
            header = f"[{readable_ts}]{score_str}" if readable_ts else ""
            lines.append(f"{header} {it['text']}")
            lines.append(f"  {it['url']}")

    return "\n".join(lines)


def _extract_top_links(digest_raw: str, items: list[dict]) -> tuple[str, list[dict]]:
    """
    Parse the TOP_LINKS line Claude appends to the digest.
    Returns (clean_digest_text, top3_items).
    Falls back to score-sorted items if parsing fails.
    """
    lines = digest_raw.split("\n")
    top_items: list[dict] = []
    clean_lines: list[str] = []

    for line in lines:
        if line.startswith("TOP_LINKS:"):
            raw = line[len("TOP_LINKS:"):].strip()
            urls = [u.strip() for u in raw.split("|") if u.strip()]
            url_map = {it["url"]: it for it in items if it.get("url")}
            top_items = [url_map[u] for u in urls if u in url_map]
        else:
            clean_lines.append(line)

    if not top_items:
        # Fallback: pick by score (Reddit/HN), then just first 3
        top_items = sorted(items, key=lambda x: x.get("score") or 0, reverse=True)[:3]

    return "\n".join(clean_lines).strip(), top_items[:3]


def summarize_items(items: list[dict]) -> tuple[str, list[dict]]:
    """
    Send news items to Claude and return (digest_text, top3_items).
    top3_items are the 3 most trending stories Claude selected.

    Raises:
        anthropic.APIError on API failure.
    """
    items_block = format_items_for_prompt(items)
    n_sources = len({it["source"] for it in items})

    user_prompt = SUMMARIZATION_USER_PROMPT_TEMPLATE.format(
        hours=LOOKBACK_HOURS,
        items_block=items_block,
    )

    logger.info(
        "Calling Claude (%s) with %d items from %d sources (~%d estimated input tokens).",
        CLAUDE_MODEL, len(items), n_sources, len(user_prompt) // 4,
    )

    # Retry up to 7 times on transient overload (529) or rate-limit (429) errors
    # Waits: 30s, 60s, 90s, 120s, 150s, 180s (up to ~10 min total)
    max_attempts = 7
    for attempt in range(1, max_attempts + 1):
        try:
            response = _get_client().messages.create(
                model=CLAUDE_MODEL,
                max_tokens=MAX_TOKENS_SUMMARY,
                system=SUMMARIZATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            break  # success
        except APIStatusError as exc:
            if exc.status_code in (429, 529) and attempt < max_attempts:
                wait = 30 * attempt   # 30s, 60s, 90s, 120s, 150s, 180s
                logger.warning(
                    "Claude API returned %d (attempt %d/%d) — retrying in %ds...",
                    exc.status_code, attempt, max_attempts, wait,
                )
                time.sleep(wait)
            else:
                raise   # non-retryable or out of retries

    raw = response.content[0].text.strip()
    logger.info(
        "Summarization complete. Input tokens: %d, output tokens: %d.",
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

    digest, top_items = _extract_top_links(raw, items)
    logger.info("Top trending URLs selected: %s", [it.get("url","") for it in top_items])
    return digest, top_items

"""
scraper.py

Fetches AI news from three sources:
  1. RSS feeds  — AI labs, newsletters, tech media, research papers
  2. Reddit     — top posts via PRAW (official API, requires credentials)
  3. Hacker News — AI-related top stories (free Algolia API)

Returns a unified list of item dicts.
After each run, writes logs/source_status.json with per-source fetch results.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser

from config import (
    RSS_FEEDS,
    REDDIT_SUBREDDITS,
    HN_MIN_SCORE,
    HN_MAX_STORIES,
    HN_SEARCH_QUERY,
    LOOKBACK_HOURS,
    MAX_ITEMS_PER_SOURCE,
)

logger = logging.getLogger(__name__)

STATUS_FILE  = Path(__file__).parent / "logs" / "source_status.json"
SOURCES_FILE = Path(__file__).parent / "sources.json"


def _load_sources() -> list[dict]:
    """Load sources.json if present, else fall back to config defaults."""
    if SOURCES_FILE.exists():
        try:
            return json.loads(SOURCES_FILE.read_text())
        except Exception as exc:
            logger.warning("Failed to read sources.json, using config defaults: %s", exc)
    # Fallback: build from config.py values
    base = [{"name": f["source"], "url": f["url"], "category": "RSS", "type": "rss", "enabled": True, "new": False}
            for f in RSS_FEEDS]
    for sub in REDDIT_SUBREDDITS:
        base.append({"name": f"r/{sub}", "url": f"https://www.reddit.com/r/{sub}/hot/.rss", "category": "Reddit", "type": "reddit", "enabled": True, "new": False})
    base.append({"name": "Hacker News", "url": "", "category": "Community", "type": "hn", "enabled": True, "new": False})
    return base

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Populated during each scrape run, saved to STATUS_FILE by scrape_all()
_source_stats: list[dict] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)


def _fetch_url(url: str) -> bytes | None:
    """Simple HTTP GET with a User-Agent header. Returns bytes or None on error."""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def _parse_dt(dt_str: str | None) -> datetime | None:
    """Try to parse an RFC 2822 or ISO 8601 datetime string."""
    if not dt_str:
        return None
    try:
        return parsedate_to_datetime(dt_str)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _ensure_tz(dt: datetime) -> datetime:
    """Add UTC timezone if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _save_source_status() -> None:
    """Write current _source_stats to logs/source_status.json."""
    try:
        STATUS_FILE.parent.mkdir(exist_ok=True)
        data = {
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "sources": _source_stats,
        }
        STATUS_FILE.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        logger.warning("Failed to save source status: %s", exc)


# ---------------------------------------------------------------------------
# 1. RSS Feeds
# ---------------------------------------------------------------------------

def fetch_rss_feeds() -> list[dict]:
    """Parse all enabled RSS feeds from sources.json and return recent items."""
    global _source_stats
    cutoff = _cutoff()
    all_items: list[dict] = []
    rss_sources = [s for s in _load_sources() if s.get("type") == "rss"]

    for feed_cfg in rss_sources:
        url = feed_cfg["url"]
        source = feed_cfg["name"]
        enabled = feed_cfg.get("enabled", True)
        logger.info("Fetching RSS: %s", source)

        if not enabled:
            _source_stats.append({
                "name": source, "url": url, "type": "rss",
                "status": "disabled", "count": 0, "error": None,
            })
            logger.info("  %s: disabled", source)
            continue

        try:
            # Fetch via _fetch_url (15s timeout) then parse content —
            # feedparser.parse(url) has no timeout and can hang indefinitely.
            raw = _fetch_url(url)
            if raw is None:
                logger.warning("  %s: fetch failed (timeout/network), skipping", source)
                _source_stats.append({
                    "name": source, "url": url, "type": "rss",
                    "status": "error", "count": 0, "error": "fetch timeout/network error",
                })
                continue
            feed = feedparser.parse(raw)
            items_added = 0

            for entry in feed.entries:
                if items_added >= MAX_ITEMS_PER_SOURCE:
                    break

                # Published time
                pub_str = entry.get("published") or entry.get("updated")
                pub_dt = _parse_dt(pub_str)
                if pub_dt:
                    pub_dt = _ensure_tz(pub_dt)
                    if pub_dt < cutoff:
                        continue

                # Text: prefer summary, fall back to title
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "") or entry.get("description", "")
                # Strip basic HTML tags from summary
                import re
                summary = re.sub(r"<[^>]+>", " ", summary).strip()
                summary = re.sub(r"\s+", " ", summary)[:500]

                text = f"{title}. {summary}" if summary else title
                link = entry.get("link", "")

                if not title:
                    continue

                all_items.append({
                    "source": source,
                    "type": "rss",
                    "title": title,
                    "text": text,
                    "url": link,
                    "timestamp": pub_dt.isoformat() if pub_dt else "",
                    "score": None,
                })
                items_added += 1

            logger.info("  %s: %d item(s)", source, items_added)
            _source_stats.append({
                "name": source, "url": url, "type": "rss",
                "status": "ok", "count": items_added, "error": None,
            })

        except Exception as exc:
            logger.error("Error parsing RSS feed %s: %s", source, exc)
            _source_stats.append({
                "name": source, "url": url, "type": "rss",
                "status": "error", "count": 0, "error": str(exc)[:120],
            })

    return all_items


# ---------------------------------------------------------------------------
# 2. Reddit (public RSS feeds — no credentials required)
# ---------------------------------------------------------------------------

# Reddit blocks browser and generic UAs for RSS — a real RSS-reader UA is required
_REDDIT_HEADERS = {
    "User-Agent": "Feedbin/2208 +https://feedbin.com/requests"
}


def _fetch_via_curl(url: str, extra_headers: dict | None = None) -> str:
    """Fetch URL via curl subprocess (bypasses Python TLS fingerprinting blocks).
    Returns raw text content or empty string on failure."""
    cmd = ["curl", "-s", "--max-time", "15"]
    if extra_headers:
        for k, v in extra_headers.items():
            cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return result.stdout
    except Exception as exc:
        logger.warning("curl fetch failed for %s: %s", url, exc)
        return ""


def fetch_reddit_rss() -> list[dict]:
    """Fetch hot posts from AI subreddits via Reddit's public RSS feeds.
    Uses curl to bypass Reddit's TLS fingerprint blocking of Python HTTP clients.
    Uses a 72h cutoff since 'hot' posts can be days old."""
    global _source_stats
    import re

    # Reddit hot posts can be 2-3 days old — use 72h cutoff instead of LOOKBACK_HOURS
    reddit_cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    all_items: list[dict] = []
    reddit_sources = [s for s in _load_sources() if s.get("type") == "reddit"]

    for source_cfg in reddit_sources:
        name    = source_cfg["name"]
        url     = source_cfg["url"]
        enabled = source_cfg.get("enabled", True)
        logger.info("Fetching Reddit RSS: %s", name)

        if not enabled:
            _source_stats.append({
                "name": name, "url": url, "type": "reddit",
                "status": "disabled", "count": 0, "error": None,
            })
            logger.info("  %s: disabled", name)
            continue

        try:
            # Reddit blocks Python TLS fingerprint — fetch via curl, parse with feedparser
            raw = _fetch_via_curl(url, extra_headers=_REDDIT_HEADERS)
            feed = feedparser.parse(raw)
            items_added = 0

            for entry in feed.entries:
                if items_added >= MAX_ITEMS_PER_SOURCE:
                    break

                title = entry.get("title", "").strip()
                if not title:
                    continue

                # Published time — skip posts older than 72h
                pub_str = entry.get("published") or entry.get("updated")
                pub_dt  = _parse_dt(pub_str)
                if pub_dt:
                    pub_dt = _ensure_tz(pub_dt)
                    if pub_dt < reddit_cutoff:
                        continue

                link = entry.get("link", "")

                # Summary text — strip HTML tags
                summary = entry.get("summary", "") or ""
                summary = re.sub(r"<[^>]+>", " ", summary).strip()
                summary = re.sub(r"\s+", " ", summary)[:300]
                if summary.lower() in ("[removed]", "[deleted]"):
                    summary = ""

                text = f"{title}. {summary}" if summary else title

                all_items.append({
                    "source": name,
                    "type":   "reddit",
                    "title":  title,
                    "text":   text,
                    "url":    link,
                    "timestamp": pub_dt.isoformat() if pub_dt else "",
                    "score":  None,   # Reddit public RSS doesn't expose vote counts
                })
                items_added += 1

            logger.info("  %s: %d item(s)", name, items_added)
            _source_stats.append({
                "name": name, "url": url, "type": "reddit",
                "status": "ok", "count": items_added, "error": None,
            })

        except Exception as exc:
            logger.error("Error fetching Reddit RSS %s: %s", name, exc)
            _source_stats.append({
                "name": name, "url": url, "type": "reddit",
                "status": "error", "count": 0, "error": str(exc)[:120],
            })

    return all_items


# ---------------------------------------------------------------------------
# 3. Hacker News (Algolia API)
# ---------------------------------------------------------------------------

def fetch_hackernews() -> list[dict]:
    """Fetch recent AI-related stories from Hacker News via the free Algolia API."""
    global _source_stats
    sources = _load_sources()
    hn_entry = next((s for s in sources if s.get("type") == "hn"), None)
    if hn_entry and not hn_entry.get("enabled", True):
        logger.info("Hacker News skipped — disabled in sources.json.")
        _source_stats.append({"name": "Hacker News", "url": "news.ycombinator.com", "type": "hn",
                               "status": "disabled", "count": 0, "error": None})
        return []
    cutoff = _cutoff()
    cutoff_ts = int(cutoff.timestamp())

    # Note: combining a text query with numericFilters in Algolia returns 0 results
    # (Algolia treats query words as AND-required with numeric filters).
    # Instead: fetch top stories by score/date only — HN front page is naturally tech/AI heavy.
    params = urllib.parse.urlencode({
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff_ts},points>={HN_MIN_SCORE}",
        "hitsPerPage": HN_MAX_STORIES,
    })
    url = f"https://hn.algolia.com/api/v1/search?{params}"

    logger.info("Fetching Hacker News...")
    data = _fetch_url(url)
    if not data:
        _source_stats.append({
            "name": "Hacker News", "url": "hn.algolia.com", "type": "hn",
            "status": "error", "count": 0, "error": "Failed to fetch",
        })
        return []

    all_items: list[dict] = []
    try:
        parsed = json.loads(data)
        hits = parsed.get("hits", [])

        for hit in hits:
            title = hit.get("title", "").strip()
            story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            points = hit.get("points", 0)
            created_at = hit.get("created_at")
            pub_dt = _parse_dt(created_at)
            if pub_dt:
                pub_dt = _ensure_tz(pub_dt)

            if not title:
                continue

            all_items.append({
                "source": "Hacker News",
                "type": "hn",
                "title": title,
                "text": title,
                "url": story_url,
                "timestamp": pub_dt.isoformat() if pub_dt else "",
                "score": points,
            })

        logger.info("  Hacker News: %d item(s)", len(all_items))
        _source_stats.append({
            "name": "Hacker News", "url": "news.ycombinator.com", "type": "hn",
            "status": "ok", "count": len(all_items), "error": None,
        })

    except Exception as exc:
        logger.error("Error parsing HN response: %s", exc)
        _source_stats.append({
            "name": "Hacker News", "url": "news.ycombinator.com", "type": "hn",
            "status": "error", "count": 0, "error": str(exc)[:120],
        })

    return all_items


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scrape_all() -> tuple[list[dict], list[str]]:
    """
    Fetch from all sources. Returns (items, failed_sources).
    Never raises — individual source failures are logged and skipped.
    Saves per-source status to logs/source_status.json after each run.
    """
    global _source_stats
    _source_stats = []   # reset for this run

    all_items: list[dict] = []
    failed: list[str] = []

    sources = [
        ("RSS Feeds",    fetch_rss_feeds),
        ("Reddit",       fetch_reddit_rss),
        ("Hacker News",  fetch_hackernews),
    ]

    for name, fn in sources:
        try:
            items = fn()
            all_items.extend(items)
        except Exception as exc:
            logger.error("Source '%s' failed entirely: %s", name, exc)
            failed.append(name)

    _save_source_status()

    logger.info(
        "Scraping complete: %d total items from %d/%d sources.",
        len(all_items), len(sources) - len(failed), len(sources),
    )
    return all_items, failed

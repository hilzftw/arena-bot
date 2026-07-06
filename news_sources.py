"""
WoW news RSS ingestion.

Fetches a small set of RSS feeds with timeouts + graceful failure (a dead feed
never breaks the loop), parses them with feedparser off the event loop, and
returns normalized article dicts. Classification/dedup happen in the news cog.

Feed URLs are best-effort and can be swapped without touching logic.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp
import feedparser

log = logging.getLogger("news")

# Source colours (spec): Blizzard blue, Wowhead orange, Icy Veins purple, Ironforge gray.
SOURCES: tuple[dict[str, Any], ...] = (
    {"name": "Blizzard", "url": "https://news.blizzard.com/en-us/feed", "color": 0x00AEFF},
    {"name": "Wowhead", "url": "https://www.wowhead.com/news/rss/all", "color": 0xFF8000},
    {"name": "Icy Veins", "url": "https://www.icy-veins.com/wow/feed/", "color": 0x8B5CF6},
)

_HEADERS = {"User-Agent": "NightslayerArenasBot/2.0 (+news)"}


def _parse(content: bytes, source: dict[str, Any]) -> list[dict[str, Any]]:
    feed = feedparser.parse(content)
    out: list[dict[str, Any]] = []
    for e in feed.entries[:25]:
        link = e.get("link", "")
        guid = e.get("id") or link
        if not guid:
            continue
        out.append({
            "guid": guid,
            "title": (e.get("title") or "").strip(),
            "url": link,
            "summary": (e.get("summary") or "")[:400],
            "source": source["name"],
            "color": source["color"],
        })
    return out


async def _fetch_one(session: aiohttp.ClientSession, source: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        async with session.get(source["url"], headers=_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                log.warning("News %s -> HTTP %s", source["name"], r.status)
                return []
            content = await r.read()
        return await asyncio.to_thread(_parse, content, source)
    except Exception as exc:  # noqa: BLE001 - one bad feed must not break the rest
        log.warning("News fetch failed for %s: %s", source["name"], exc)
        return []


async def fetch_all(session: Optional[aiohttp.ClientSession] = None) -> list[dict[str, Any]]:
    """Return newest-first articles across all sources."""
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        results = await asyncio.gather(*(_fetch_one(session, s) for s in SOURCES))
    finally:
        if own:
            await session.close()
    articles: list[dict[str, Any]] = []
    for group in results:
        articles.extend(group)
    return articles

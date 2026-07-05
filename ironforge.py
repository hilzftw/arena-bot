"""
Ironforge.pro API client + in-memory ladder cache.

Public endpoints (no auth):
  GET /api/anniversary/leaderboards/{season}/{region}/{bracket}/
  GET /api/anniversary/cutoffs/{season}/{region}/{bracket}/

The ladder is cached and rebuilt periodically; /verify searches the cache instead
of hitting the API per user. Cutoffs are only pulled for 3v3 (bracket 3), which is
all that's needed to award Gladiator / Merciless Gladiator titles.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp

from config import settings, BRACKETS, BRACKET_NAMES

log = logging.getLogger("ironforge")

# ladder_cache[region][bracket] = { (name_lower, realm_lower): entry }
_ladder_cache: dict[str, dict[int, dict[tuple[str, str], dict]]] = {}
# cutoffs_cache[region] = [[rating, title, rank_str], ...] for 3v3
_cutoffs_cache: dict[str, list] = {}

_session: Optional[aiohttp.ClientSession] = None
_CUTOFF_BRACKET = 3  # 3v3 drives title roles


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={"User-Agent": "NightslayerArenasBot/2.0"},
            timeout=aiohttp.ClientTimeout(total=15),
        )
    return _session


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()


async def _fetch_json(url: str) -> Any:
    try:
        async with _get_session().get(url) as r:
            if r.status == 200:
                return await r.json()
            log.warning("Ironforge %s -> HTTP %s", url, r.status)
    except Exception as exc:  # noqa: BLE001 - network layer, log and continue
        log.error("Ironforge fetch error %s: %s", url, exc)
    return None


async def refresh_ladder(season: Optional[int] = None, region: Optional[str] = None) -> None:
    season = season or settings.current_season
    region = region or settings.region
    _ladder_cache.setdefault(region, {})
    for bracket in BRACKETS:
        url = f"{settings.ironforge_base}/api/anniversary/leaderboards/{season}/{region}/{bracket}/"
        data = await _fetch_json(url)
        if data and "data" in data:
            _ladder_cache[region][bracket] = {
                (e["name"].lower(), e["server"].lower()): {**e, "bracket_num": bracket}
                for e in data["data"]
            }
            log.info("Ladder cached: %s %s S%s -> %d entries",
                     region, BRACKET_NAMES[bracket], season, len(data["data"]))
        else:
            _ladder_cache[region].setdefault(bracket, {})
            log.warning("No ladder data for %s %s S%s", region, BRACKET_NAMES[bracket], season)


async def refresh_cutoffs(season: Optional[int] = None, region: Optional[str] = None) -> None:
    season = season or settings.current_season
    region = region or settings.region
    url = f"{settings.ironforge_base}/api/anniversary/cutoffs/{season}/{region}/{_CUTOFF_BRACKET}/"
    data = await _fetch_json(url)
    if data and "cutoff" in data:
        _cutoffs_cache[region] = data["cutoff"]
        log.info("Cutoffs cached: %s S%s 3v3", region, season)


async def refresh_all(season: Optional[int] = None, region: Optional[str] = None) -> None:
    await asyncio.gather(refresh_ladder(season, region), refresh_cutoffs(season, region))


def lookup_character(character: str, realm: str, region: Optional[str] = None) -> Optional[dict]:
    """Return the highest-rated ladder entry for this character across brackets, or None."""
    region = region or settings.region
    key = (character.lower(), realm.lower())
    hits = [
        entry
        for entries in _ladder_cache.get(region, {}).values()
        if (entry := entries.get(key)) is not None
    ]
    return max(hits, key=lambda e: e["rating"]) if hits else None


def determine_role_key(rating: int, region: Optional[str] = None) -> str:
    """Map a rating to a rating-role key, using live 3v3 cutoffs when available."""
    region = region or settings.region
    cutoffs = _cutoffs_cache.get(region, [])
    if len(cutoffs) >= 2:
        if rating >= cutoffs[0][0]:
            return "Merciless Gladiator"
        if rating >= cutoffs[1][0]:
            return "Gladiator"
    if rating >= 2100:
        return "2100+"
    if rating >= 1800:
        return "1800+"
    if rating >= 1400:
        return "1400+"
    return "Unranked"

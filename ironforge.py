"""
Ironforge.pro API client + in-memory ladder cache.

Endpoints (no auth required):
  GET /api/anniversary/leaderboards/{season}/{region}/{bracket}/
  GET /api/anniversary/cutoffs/{season}/{region}/{bracket}/

Cache is rebuilt every CACHE_REFRESH_MINUTES.
/verify searches the cache â fo real-time call per user.
"""
import logging
import asyncio
import aiohttp
from config import IRONFORGE_BASE, CURRENT_SEASON, BRACKETS, BRACKET_NAMES, REGION

log = logging.getLogger("ironforge")

# ladder_cache[region][bracket] = { (name_lower, server_lower): entry_dict }
ladder_cache: dict[str, dict[int, dict]] = {REGION: {b: {} for b in BRACKETS}}
# cutoffs_cache[region][bracket] = [(rating, title, rank_str), ...]
cutoffs_cache: dict[str, dict[int, list]] = {REGION: {b: [] for b in BRACKETS}}

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={"User-Agent": "WoWArenaBot/1.0"},
            timeout=aiohttp.ClientTimeout(total=15),
        )
    return _session


async def close():
    global _session
    if _session and not _session.closed:
        await _session.close()


async def _fetch_json(url: str) -> dict | list | None:
    try:
        async with _get_session().get(url) as r:
            if r.status == 200:
                return await r.json()
            log.warning("Ironforge %s â HTTP %s", url, r.status)
    except Exception as exc:
        log.error("Ironforge fetch error %s: %s", url, exc)
    return None


async def refresh_ladder(season: int = CURRENT_SEASON, region: str = REGION):
    """Pull all brackets and rebuild ladder_cache for this region."""
    if region not in ladder_cache:
        ladder_cache[region] = {}
    for bracket in BRACKETS:
        url = f"{IRONFORGE_BASE}/api/anniversary/leaderboards/{season}/{region}/{bracket}/"
        data = await _fetch_json(url)
        if data and "data" in data:
            ladder_cache[region][bracket] = {
                (e["name"].lower(), e["server"].lower()): {**e, "bracket_num": bracket}
                for e in data["data"]
            }
            log.info(
                "Ladder cache refreshed: %s %s %s â %d entries",
                region, BRACKET_NAMES[bracket], f"S{season}", len(data["data"])
            )
        else:
            log.warning("No data for %s %s %s", region, BRACKET_NAMES[bracket], f"S{season}")


async def refresh_cutoffs(season: int = CURRENT_SEASON, region: str = REGION):
    """Pull cutoffs for 3v3 (bracket=3) â used for Gladiator role assignment."""
    if region not in cutoffs_cache:
        cutoffs_cache[region] = {}
    for bracket in BRACKETS:
        url = f"{IRONFORGE_BASE}/api/anniversary/cutoffs/{season}/{region}/{bracket}/"
        data = await _fetch_json(url)
        if data and "cutoff" in data:
            cutoffs_cache[region][bracket] = data["cutoff"]


async def refresh_all(season: int = CURRENT_SEASON, region: str = REGION):
    await asyncio.gather(
        refresh_ladder(season, region),
        refresh_cutoffs(season, region),
    )


def lookup_character(character: str, server: str, region: str = REGION) -> dict | None:
    """
    Search cached ladders for this character.
    Returns the entry with the highest rating across all brackets, or None.
    """
    key = (character.lower(), server.lower())
    hits = []
    for bracket, entries in ladder_cache.get(region, {}).items():
        entry = entries.get(key)
        if entry:
            hits.append(entry)
    if not hits:
        return None
    return max(hits, key=lambda e: e["rating"])


def get_cutoffs(region: str = REGION, bracket: int = 3) -> list:
    """
    Returns list of [rating, title, rank_str] sorted descending by rating.
    Example S2 3v3 US:
      [[2390, "Merciless Gladiator", "Rank ~43"],
       [2226, "Gladiator", "Ranks ~271-276"],
       [1941, "Duelist", ...], ...]
    """
    return cutoffs_cache.get(region, {}).get(bracket, [])


def determine_role(rating: int, region: str = REGION) -> str:
    """
    Determine Discord role name from rating using live cutoffs.
    Falls back to static thresholds if cutoffs aren't loaded.
    """
    cutoffs = get_cutoffs(region, bracket=3)
    if cutoffs:
        if rating >= cutoffs[0][0]:
            return "Merciless Gladiator"
        if rating >= cutoffs[1][0]:
            return "Gladiator"
    # Static fallback (season-agnostic)
    if rating >= 2100:
        return "2100+"
    if rating >= 1800:
        return "1800+"
    if rating >= 1400:
        return "1400+"
    return "Unranked"


def get_all_ladder_entries(region: str = REGION) -> list[dict]:
    """Flat list of all cached entries across all brackets â used for leaderboard post."""
    entries = []
    for bracket, cache in ladder_cache.get(region, {}).items():
        for entry in cache.values():
            entries.append(entry)
    return entries

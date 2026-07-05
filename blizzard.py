"""
Blizzard Classic API â fallback for unranked characters.
Used only when a character is NOT found on the Ironforge ladder.
Confirms the character exists and returns basic PvP summary.

Auth: client_credentials (no user OAuth needed for read-only data).
Namespace: profile-classic-us
"""
import time
import logging
import aiohttp
from config import BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET, REGION

log = logging.getLogger("blizzard")

BLIZZARD_BASE = "https://us.api.blizzard.com"
NAMESPACE_PROFILE = f"profile-classic-{REGION.lower()}"
LOCALE = "en_US"

_token: str | None = None
_token_expires: float = 0.0
_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    return _session


async def _get_token() -> str | None:
    global _token, _token_expires
    if _token and time.time() < _token_expires - 60:
        return _token
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        log.warning("Blizzard credentials not set â fallback disabled")
        return None
    try:
        async with _get_session().post(
            "https://oauth.battle.net/token",
            data={"grant_type": "client_credentials"},
            auth=aiohttp.BasicAuth(BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET),
        ) as r:
            if r.status == 200:
                data = await r.json()
                _token = data["access_token"]
                _token_expires = time.time() + data.get("expires_in", 86400)
                return _token
            log.error("Blizzard token error: %s", r.status)
    except Exception as exc:
        log.error("Blizzard token fetch failed: %s", exc)
    return None


async def get_pvp_summary(character: str, realm: str) -> dict | None:
    """
    Returns PvP summary dict if the character exists, None otherwise.
    Keys of interest: honor_level, brackets (list)
    """
    token = await _get_token()
    if not token:
        return None
    url = (
        f"{BLIZZARD_BASE}/profile/wow/character"
        f"/{realm.lower().replace(' ', '-')}/{character.lower()}/pvp-summary"
    )
    params = {"namespace": NAMESPACE_PROFILE, "locale": LOCALE}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with _get_session().get(url, headers=headers, params=params) as r:
            if r.status == 200:
                return await r.json()
            if r.status == 404:
                return None  # character not found
            log.warning("Blizzard pvp-summary %s/%s â %s", character, realm, r.status)
    except Exception as exc:
        log.error("Blizzard pvp-summary error: %s", exc)
    return None


async def character_exists(character: str, realm: str) -> bool:
    """Quick existence check â falls back to checking the character profile endpoint."""
    token = await _get_token()
    if not token:
        return False
    url = (
        f"{BLIZZARD_BASE}/profile/wow/character"
        f"/{realm.lower().replace(' ', '-')}/{character.lower()}"
    )
    params = {"namespace": NAMESPACE_PROFILE, "locale": LOCALE}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with _get_session().get(url, headers=headers, params=params) as r:
            return r.status == 200
    except Exception as exc:
        log.error("Blizzard character_exists error: %s", exc)
    return False


async def close():
    global _session
    if _session and not _session.closed:
        await _session.close()

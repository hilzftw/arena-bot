"""
Blizzard Classic API — fallback existence check only.

Used when a character is NOT on the Ironforge ladder: confirms the character
exists so we can still verify them as Unranked. Auth uses client_credentials
(no user OAuth needed for read-only profile data). If credentials aren't set,
the fallback is simply disabled and verify relies on Ironforge alone.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import aiohttp

from config import settings

log = logging.getLogger("blizzard")

_LOCALE = "en_US"
_token: Optional[str] = None
_token_expires: float = 0.0
_session: Optional[aiohttp.ClientSession] = None


def _region_host() -> str:
    return f"https://{settings.region.lower()}.api.blizzard.com"


def _namespace() -> str:
    return f"profile-classic-{settings.region.lower()}"


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    return _session


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()


async def _get_token() -> Optional[str]:
    global _token, _token_expires
    if _token and time.time() < _token_expires - 60:
        return _token
    if not settings.blizzard_client_id or not settings.blizzard_client_secret:
        log.info("Blizzard credentials not set — fallback disabled")
        return None
    try:
        async with _get_session().post(
            "https://oauth.battle.net/token",
            data={"grant_type": "client_credentials"},
            auth=aiohttp.BasicAuth(settings.blizzard_client_id, settings.blizzard_client_secret),
        ) as r:
            if r.status == 200:
                data = await r.json()
                _token = data["access_token"]
                _token_expires = time.time() + data.get("expires_in", 86400)
                return _token
            log.error("Blizzard token error: %s", r.status)
    except Exception as exc:  # noqa: BLE001
        log.error("Blizzard token fetch failed: %s", exc)
    return None


async def character_exists(character: str, realm: str) -> bool:
    """True if the character profile resolves on the Blizzard Classic API."""
    token = await _get_token()
    if not token:
        return False
    url = (
        f"{_region_host()}/profile/wow/character"
        f"/{realm.lower().replace(' ', '-')}/{character.lower()}"
    )
    params = {"namespace": _namespace(), "locale": _LOCALE}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with _get_session().get(url, headers=headers, params=params) as r:
            return r.status == 200
    except Exception as exc:  # noqa: BLE001
        log.error("Blizzard character_exists error: %s", exc)
    return False

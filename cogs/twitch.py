"""
Twitch "now live" notifications.

Polls the Twitch Helix API for a configured list of streamers and posts an embed
to #twitchy-p-clips when one transitions from offline to live. State is tracked in
memory; on startup the currently-live set is "primed" so a restart never re-announces
a stream that was already live. Only loaded when Twitch credentials are configured.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import aiohttp
import discord
from discord.ext import commands, tasks

from config import settings, COLOR_PURPLE

log = logging.getLogger("twitch")

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_STREAMS_URL = "https://api.twitch.tv/helix/streams"


class Twitch(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._live: set[str] = set()
        self._primed = False
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session

    async def _get_token(self) -> Optional[str]:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        try:
            async with self._sess().post(_TOKEN_URL, params={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "grant_type": "client_credentials",
            }) as r:
                if r.status == 200:
                    data = await r.json()
                    self._token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 3600)
                    return self._token
                log.error("Twitch token error: HTTP %s", r.status)
        except Exception as exc:  # noqa: BLE001
            log.error("Twitch token fetch failed: %s", exc)
        return None

    async def _fetch_live(self) -> dict[str, dict]:
        """Return {login_lower: stream} for streamers currently live."""
        token = await self._get_token()
        if not token:
            return {}
        headers = {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {token}"}
        params = [("user_login", s) for s in settings.twitch_streamers[:100]]
        try:
            async with self._sess().get(_STREAMS_URL, headers=headers, params=params) as r:
                if r.status != 200:
                    log.warning("Twitch streams HTTP %s", r.status)
                    return {}
                data = await r.json()
                return {s["user_login"].lower(): s for s in data.get("data", [])}
        except Exception as exc:  # noqa: BLE001
            log.error("Twitch streams fetch failed: %s", exc)
            return {}

    def _channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(settings.guild_id)
        if guild is None:
            return None
        if settings.twitch_channel_id:
            ch = guild.get_channel(settings.twitch_channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
        return discord.utils.get(guild.text_channels, name="twitchy-p-clips")

    async def _announce(self, login: str, stream: dict) -> None:
        channel = self._channel()
        if channel is None:
            log.warning("Twitch announce channel not found")
            return
        name = stream.get("user_name", login)
        url = f"https://twitch.tv/{login}"
        embed = discord.Embed(
            title=f"🔴 {name} is now live on Twitch!",
            url=url,
            description=stream.get("title") or "",
            color=COLOR_PURPLE,
        )
        if stream.get("game_name"):
            embed.add_field(name="Playing", value=stream["game_name"], inline=True)
        thumb = stream.get("thumbnail_url", "")
        if thumb:
            embed.set_image(url=thumb.replace("{width}", "640").replace("{height}", "360"))
        embed.add_field(name="Watch", value=f"[{url}]({url})", inline=False)
        await channel.send(content=f"🎮 **{name}** just went live — {url}", embed=embed)
        log.info("Announced Twitch live: %s", login)

    @tasks.loop(minutes=1)
    async def poll(self) -> None:
        live_now = await self._fetch_live()
        current = set(live_now.keys())
        if not self._primed:
            self._live = current  # don't announce streams already live at startup
            self._primed = True
            return
        for login in current - self._live:
            await self._announce(login, live_now[login])
        self._live = current

    @poll.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()
        # Align loop interval to the configured cadence.
        self.poll.change_interval(minutes=max(1, settings.twitch_poll_minutes))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Twitch(bot))

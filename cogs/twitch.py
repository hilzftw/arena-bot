"""
Twitch integration: "now live" notifications + automatic clip posting.

Two independent pollers sharing one app access token:

  • go-live  — polls /helix/streams; announces an offline→live transition.
  • clips    — polls /helix/clips for every configured streamer and posts any new
               clip to Discord. Clips made by *viewers* count, so this surfaces
               community clips automatically.

Both use a client-credentials app token against public endpoints, which means
**no broadcaster authorization is required** — a moderator (or anyone) can run
this without the streamer granting anything. Only loaded when Twitch credentials
are configured.

Clip dedup is by Twitch clip id in the DB (not in memory), so a redeploy can't
re-post a clip. On the first run against an empty table the feed primes — marks
current clips seen without posting — so a fresh deploy doesn't dump the backlog.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from config import settings, COLOR_PURPLE

log = logging.getLogger("twitch")

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_STREAMS_URL = "https://api.twitch.tv/helix/streams"
_USERS_URL = "https://api.twitch.tv/helix/users"
_CLIPS_URL = "https://api.twitch.tv/helix/clips"

# DB key for the runtime channel override set by /clips channel.
CLIPS_CHANNEL_KEY = "clips_channel_id"


class Twitch(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self._live: set[str] = set()
        self._primed = False
        self._user_ids: dict[str, str] = {}   # login -> broadcaster_id (cached)
        self.poll.start()
        if settings.clips_enabled:
            self.clips_poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        self.clips_poll.cancel()
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
        for name in ("twitchy-p", "twitchy-p-clips"):
            ch = discord.utils.get(guild.text_channels, name=name)
            if ch:
                return ch
        return None

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

    # ── clips ────────────────────────────────────────────────────────────────
    async def _resolve_user_ids(self) -> dict[str, str]:
        """login -> broadcaster_id. Cached; /helix/clips needs the id, not the login."""
        missing = [s for s in settings.twitch_streamers if s not in self._user_ids]
        if not missing:
            return self._user_ids
        token = await self._get_token()
        if not token:
            return self._user_ids
        headers = {"Client-Id": settings.twitch_client_id,
                   "Authorization": f"Bearer {token}"}
        params = [("login", s) for s in missing[:100]]
        try:
            async with self._sess().get(_USERS_URL, headers=headers, params=params) as r:
                if r.status != 200:
                    log.warning("Twitch users HTTP %s", r.status)
                    return self._user_ids
                for u in (await r.json()).get("data", []):
                    self._user_ids[u["login"].lower()] = u["id"]
        except Exception as exc:  # noqa: BLE001
            log.error("Twitch user id lookup failed: %s", exc)
        unresolved = [s for s in settings.twitch_streamers if s not in self._user_ids]
        if unresolved:
            log.warning("Twitch logins not found: %s", ", ".join(unresolved))
        return self._user_ids

    async def _fetch_clips(self, broadcaster_id: str) -> list[dict]:
        """Clips created in the lookback window, newest-relevant first.

        started_at/ended_at bound the query; without ended_at Twitch defaults to a
        1-week window, which would re-scan far more than we need every poll.
        """
        token = await self._get_token()
        if not token:
            return []
        now = datetime.now(timezone.utc)
        since = now - timedelta(minutes=max(5, settings.clips_lookback_minutes))
        headers = {"Client-Id": settings.twitch_client_id,
                   "Authorization": f"Bearer {token}"}
        params = {
            "broadcaster_id": broadcaster_id,
            "started_at": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "first": "100",
        }
        try:
            async with self._sess().get(_CLIPS_URL, headers=headers, params=params) as r:
                if r.status != 200:
                    log.warning("Twitch clips HTTP %s", r.status)
                    return []
                return (await r.json()).get("data", [])
        except Exception as exc:  # noqa: BLE001
            log.error("Twitch clips fetch failed: %s", exc)
            return []

    async def _clips_channel(self) -> Optional[discord.TextChannel]:
        """Runtime override → CLIPS_CHANNEL_ID → the go-live channel."""
        guild = self.bot.get_guild(settings.guild_id)
        if guild is None:
            return None
        raw = await db.get_setting(CLIPS_CHANNEL_KEY, "")
        if raw.isdigit():
            ch = guild.get_channel(int(raw))
            if isinstance(ch, discord.TextChannel):
                return ch
            log.warning("clips_channel_id=%s in DB but no such channel", raw)
        if settings.clips_channel_id:
            ch = guild.get_channel(settings.clips_channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
            log.warning("CLIPS_CHANNEL_ID=%s but no such channel",
                        settings.clips_channel_id)
        return self._channel()   # fall back to the go-live channel

    async def _run_clips(self, prime: bool = False) -> tuple[int, int]:
        """Fetch → dedup → post. Returns (posted, skipped_by_cap)."""
        ids = await self._resolve_user_ids()
        if not ids:
            return 0, 0
        # Auto-prime on a genuinely empty table (fresh deploy): mark seen, don't post.
        priming = prime or not sum((await db.clip_stats()).values())
        channel = None if priming else await self._clips_channel()
        if not priming and channel is None:
            log.warning("No clips channel resolved — set CLIPS_CHANNEL_ID or /clips channel")
            return 0, 0

        posted = skipped = 0
        for login, bid in ids.items():
            for clip in await self._fetch_clips(bid):
                cid = clip.get("id")
                if not cid or await db.clip_seen(cid):
                    continue
                if clip.get("view_count", 0) < settings.clips_min_views:
                    continue   # not marked seen — it may cross the threshold later

                if priming:
                    await self._mark(clip, login)
                    continue
                if posted >= settings.clips_max_per_run:
                    await self._mark(clip, login)   # consume, don't replay forever
                    skipped += 1
                    continue

                creator = clip.get("creator_name") or "someone"
                caster = clip.get("broadcaster_name") or login
                title = clip.get("title") or "Clip"
                url = clip.get("url") or ""
                try:
                    # Post the bare URL so Discord renders its native, playable clip
                    # embed. A custom embed here would produce a second, static one.
                    await channel.send(
                        f"🎬 **{creator}** clipped **{caster}** — {title}\n{url}")
                    await self._mark(clip, login)
                    posted += 1
                except discord.HTTPException as exc:
                    log.warning("Clip post failed: %s", exc)

        if priming:
            log.info("Clips primed (existing clips marked seen, none posted)")
        if skipped:
            log.info("Clip cap hit — %d marked seen, not posted", skipped)
        return posted, skipped

    @staticmethod
    async def _mark(clip: dict, login: str) -> None:
        await db.mark_clip_posted(
            clip["id"], login, clip.get("creator_name") or "",
            clip.get("title") or "", clip.get("url") or "",
            int(clip.get("view_count", 0)), clip.get("created_at") or "")

    @tasks.loop(minutes=10)
    async def clips_poll(self) -> None:
        if (await db.get_setting("clips_enabled", "true")) == "false":
            return
        posted, _ = await self._run_clips()
        if posted:
            log.info("Posted %d new Twitch clip(s)", posted)

    @clips_poll.before_loop
    async def _before_clips(self) -> None:
        await self.bot.wait_until_ready()
        self.clips_poll.change_interval(minutes=max(1, settings.clips_poll_minutes))

    # ── commands ─────────────────────────────────────────────────────────────
    clips = app_commands.Group(name="clips", description="Twitch clip feed controls.")

    @clips.command(name="stats", description="Clips posted, and where they post.")
    async def clips_stats(self, interaction: discord.Interaction) -> None:
        s = await db.clip_stats()
        body = "\n".join(f"**{k}** — {v}" for k, v in sorted(s.items())) or "None yet."
        embed = discord.Embed(title="🎬 Twitch clips", description=body,
                              color=COLOR_PURPLE)
        channel = await self._clips_channel()
        embed.add_field(
            name="Posting to",
            value=channel.mention if channel else "❌ unresolved — set `CLIPS_CHANNEL_ID`",
            inline=False)
        embed.add_field(name="Watching",
                        value=", ".join(settings.twitch_streamers) or "nobody",
                        inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @clips.command(name="channel", description="[Staff] Set the channel clips post to.")
    async def clips_channel_cmd(self, interaction: discord.Interaction,
                                channel: discord.TextChannel) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await db.set_setting(CLIPS_CHANNEL_KEY, str(channel.id))
        await interaction.response.send_message(
            f"✅ Twitch clips will now post to {channel.mention}.", ephemeral=True)

    @clips.command(name="refresh", description="[Staff] Check for new clips now.")
    async def clips_refresh(self, interaction: discord.Interaction) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        posted, skipped = await self._run_clips()
        msg = f"✅ Posted {posted} new clip(s)."
        if skipped:
            msg += (f"\n⚠️ {skipped} held back by the per-run cap "
                    f"(`CLIPS_MAX_PER_RUN={settings.clips_max_per_run}`).")
        await interaction.followup.send(msg, ephemeral=True)

    @clips.command(name="prime",
                   description="[Staff] Mark existing clips as seen without posting.")
    async def clips_prime(self, interaction: discord.Interaction) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._run_clips(prime=True)
        await interaction.followup.send(
            "✅ Backlog cleared — only genuinely new clips will post from here.",
            ephemeral=True)

    @clips.command(name="toggle", description="[Staff] Enable or disable the clip feed.")
    async def clips_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await db.set_setting("clips_enabled", "true" if enabled else "false")
        await interaction.response.send_message(
            f"✅ Clip feed {'enabled' if enabled else 'disabled'}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Twitch(bot))

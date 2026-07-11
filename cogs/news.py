"""
WoW News module.

Polls RSS feeds, classifies each article (TBC-PvP-first), drops anything that
isn't real news, dedups against the DB, and posts clean embeds to the matching
read-only news channel. Feature-flagged: NEWS_ENABLED (env) plus a runtime
`news_enabled` flag in bot_settings that /news toggle flips without a redeploy.

On the first run (empty news table) it primes — marking current articles as seen
without posting — so a fresh deploy doesn't dump the whole backlog.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import news_sources
from config import settings
from news_classifier import classify

log = logging.getLogger("news")

_TAGS = re.compile(r"<[^>]+>")

# `legacy_channel` is only used as a last-resort fallback for servers that still
# run the old three-channel layout. Current servers point every category at one
# merged channel via NEWS_CHANNEL_ID (env) or /news channel (runtime, stored in DB).
# The emoji + label stay per-category so the merged feed is still skimmable.
CATEGORY_META = {
    "tbc-pvp": {"emoji": "🏆", "label": "TBC PvP Update", "legacy_channel": "tbc-pvp-news"},
    "tbc-pve": {"emoji": "⚔️", "label": "TBC PvE Update", "legacy_channel": "tbc-pve-news"},
    "retail": {"emoji": "🌍", "label": "Retail WoW Update", "legacy_channel": "retail-wow-news"},
}

CATEGORIES = ("tbc-pvp", "tbc-pve", "retail")

# DB key holding the runtime channel override set by /news channel.
CHANNEL_KEY = "news_channel_id"


def _clean(summary: str) -> str:
    text = _TAGS.sub("", summary or "").strip()
    return (text[:300] + "…") if len(text) > 300 else text


class News(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _enabled(self) -> bool:
        if not settings.news_enabled:
            return False
        return (await db.get_setting("news_enabled", "true")) != "false"

    async def _channel(self, guild: discord.Guild,
                       category: str) -> Optional[discord.TextChannel]:
        """Resolve the target channel: DB override → env id → legacy name.

        Resolving by ID means a rename or a category reshuffle can never break the
        feed again — only deleting the channel outright can. Every failure path
        logs; the old code returned None silently, which is why a merged/renamed
        channel killed the feed with no error anywhere.
        """
        # 1. Runtime override (/news channel) — highest priority, no redeploy needed.
        raw = await db.get_setting(CHANNEL_KEY, "")
        if raw.isdigit():
            ch = guild.get_channel(int(raw))
            if isinstance(ch, discord.TextChannel):
                return ch
            log.warning("news_channel_id=%s in DB but no such text channel — "
                        "falling back", raw)

        # 2. Env: the single merged news channel.
        if settings.news_channel_id:
            ch = guild.get_channel(settings.news_channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
            log.warning("NEWS_CHANNEL_ID=%s but no such text channel in guild %s — "
                        "falling back", settings.news_channel_id, guild.id)

        # 3. Legacy three-channel layout, by name.
        ch = discord.utils.get(guild.text_channels,
                               name=CATEGORY_META[category]["legacy_channel"])
        if ch is None:
            log.warning("No news channel resolved for %s. Set NEWS_CHANNEL_ID or "
                        "run /news channel.", category)
        return ch

    @staticmethod
    def _can_post(channel: discord.TextChannel) -> bool:
        perms = channel.permissions_for(channel.guild.me)
        return perms.send_messages and perms.embed_links

    def _embed(self, category: str, article: dict) -> discord.Embed:
        meta = CATEGORY_META[category]
        embed = discord.Embed(
            title=article["title"][:256] or "Update",
            url=article["url"] or None,
            description=_clean(article["summary"]),
            color=article["color"],
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=f"{meta['emoji']} {meta['label']}")
        embed.set_footer(text=f"Nightslayer Arenas News • {article['source']}")
        return embed

    async def _run(self, guild: discord.Guild, force: bool = False,
                   prime: bool = False) -> tuple[dict[str, int], int]:
        """Fetch → classify → dedup → post.

        Returns (per-category posted counts, number skipped by the per-run cap).
        `prime` marks everything currently in the feeds as seen without posting —
        used to clear a backlog that built up while the feed was misconfigured.
        """
        articles = await news_sources.fetch_all(self._sess())
        # Auto-prime only on a genuinely empty table (fresh deploy).
        priming = prime or (not force and sum((await db.news_stats()).values()) == 0)
        posted = {c: 0 for c in CATEGORIES}
        skipped = 0
        primed = 0

        for art in articles:
            category = classify(art["title"], art["summary"])
            if category is None:
                continue
            # Category allowlist (NEWS_CATEGORIES). Default: TBC Classic only.
            # Deliberately NOT marked seen — flipping retail back on later should
            # surface those articles rather than having silently consumed them.
            if category not in settings.news_categories:
                continue
            if await db.news_seen(art["guid"]):
                continue

            if priming:
                await db.mark_news_posted(art["guid"], category, art["title"],
                                          art["url"], art["source"])
                primed += 1
                continue

            # Per-run cap: after downtime the feeds hold a backlog. Posting it all
            # at once would dump dozens of embeds into the channel, so cap the run
            # and mark the overflow seen — the channel stays readable and the next
            # poll starts clean rather than replaying the same backlog forever.
            if sum(posted.values()) >= settings.news_max_per_run:
                await db.mark_news_posted(art["guid"], category, art["title"],
                                          art["url"], art["source"])
                skipped += 1
                continue

            channel = await self._channel(guild, category)
            if channel is None:
                # Do NOT mark seen — once the channel is configured we still want
                # these to post rather than being silently lost.
                continue
            if not self._can_post(channel):
                log.warning("Missing send_messages/embed_links in #%s — news blocked",
                            channel.name)
                continue

            try:
                await channel.send(embed=self._embed(category, art))
                await db.mark_news_posted(art["guid"], category, art["title"],
                                          art["url"], art["source"])
                posted[category] += 1
            except discord.HTTPException as exc:
                log.warning("News post failed in #%s: %s", channel.name, exc)

        if priming:
            log.info("News primed (%d articles marked seen, none posted)", primed)
        if skipped:
            log.info("News backlog cap hit — %d article(s) marked seen, not posted",
                     skipped)
        return posted, skipped

    @tasks.loop(minutes=settings.news_poll_minutes)
    async def poll(self) -> None:
        if not await self._enabled():
            return
        guild = self.bot.get_guild(settings.guild_id)
        if guild is None:
            return
        posted, _ = await self._run(guild)
        if any(posted.values()):
            log.info("News posted: %s", posted)

    @poll.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    # ── commands ─────────────────────────────────────────────────────────────
    news = app_commands.Group(name="news", description="WoW news feed controls.")

    @news.command(name="refresh", description="[Staff] Fetch and post new news now.")
    async def refresh(self, interaction: discord.Interaction) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        if not await self._enabled():
            await interaction.response.send_message(
                "News is disabled. Enable with `/news toggle`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        posted, skipped = await self._run(interaction.guild, force=True)
        total = sum(posted.values())
        breakdown = ", ".join(
            f"{CATEGORY_META[c]['label']} {posted[c]}"
            for c in CATEGORIES if c in settings.news_categories
        )
        msg = f"✅ Checked feeds — posted {total} new article(s) ({breakdown})."
        if skipped:
            msg += (f"\n⚠️ {skipped} more were held back by the per-run cap "
                    f"(`NEWS_MAX_PER_RUN={settings.news_max_per_run}`) and marked as seen.")
        await interaction.followup.send(msg, ephemeral=True)

    @news.command(name="stats", description="Show news counts and the target channel.")
    async def stats(self, interaction: discord.Interaction) -> None:
        s = await db.news_stats()
        lines = []
        for c in CATEGORIES:
            on = c in settings.news_categories
            suffix = "" if on else "  *(off)*"
            lines.append(f"{CATEGORY_META[c]['emoji']} **{CATEGORY_META[c]['label']}** — "
                         f"{s.get(c, 0)}{suffix}")
        embed = discord.Embed(title="📰 News stats", description="\n".join(lines),
                              color=0xC79C6E)

        # Surface where the feed actually resolves to — this is the diagnostic that
        # was missing when a renamed channel silently killed the feed.
        channel = await self._channel(interaction.guild, "tbc-pvp")
        if channel is None:
            target = "❌ **unresolved** — set `NEWS_CHANNEL_ID` or run `/news channel`"
        elif not self._can_post(channel):
            target = f"⚠️ {channel.mention} — missing Send Messages / Embed Links"
        else:
            target = channel.mention
        embed.add_field(name="Posting to", value=target, inline=False)
        embed.add_field(name="Feed", value="enabled" if await self._enabled() else "disabled",
                        inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @news.command(name="channel",
                  description="[Staff] Set the channel the news feed posts to.")
    async def channel(self, interaction: discord.Interaction,
                      channel: discord.TextChannel) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await db.set_setting(CHANNEL_KEY, str(channel.id))
        warn = ("" if self._can_post(channel) else
                "\n⚠️ I can't Send Messages / Embed Links there yet — fix the channel "
                "permissions or nothing will post.")
        await interaction.response.send_message(
            f"✅ News will now post to {channel.mention}.{warn}", ephemeral=True)

    @news.command(name="prime",
                  description="[Staff] Mark all current articles as seen without posting.")
    async def prime(self, interaction: discord.Interaction) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._run(interaction.guild, force=True, prime=True)
        await interaction.followup.send(
            "✅ Backlog cleared — current articles marked as seen. Only genuinely new "
            "articles will post from here.", ephemeral=True)

    @news.command(name="toggle", description="[Staff] Enable or disable the news feed.")
    async def toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await db.set_setting("news_enabled", "true" if enabled else "false")
        await interaction.response.send_message(
            f"✅ News feed {'enabled' if enabled else 'disabled'}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(News(bot))

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

CATEGORY_META = {
    "tbc-pvp": {"emoji": "🏆", "label": "TBC PvP Update", "channel": "tbc-pvp-news"},
    "tbc-pve": {"emoji": "⚔️", "label": "TBC PvE Update", "channel": "tbc-pve-news"},
    "retail": {"emoji": "🌍", "label": "Retail WoW Update", "channel": "retail-wow-news"},
}


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

    def _channel(self, guild: discord.Guild, category: str) -> Optional[discord.TextChannel]:
        name = CATEGORY_META[category]["channel"]
        return discord.utils.get(guild.text_channels, name=name)

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

    async def _run(self, guild: discord.Guild, force: bool = False) -> dict[str, int]:
        """Fetch → classify → dedup → post. Returns per-category posted counts."""
        articles = await news_sources.fetch_all(self._sess())
        priming = not force and sum((await db.news_stats()).values()) == 0
        posted = {"tbc-pvp": 0, "tbc-pve": 0, "retail": 0}
        for art in articles:
            category = classify(art["title"], art["summary"])
            if category is None:
                continue
            if await db.news_seen(art["guid"]):
                continue
            if priming:
                await db.mark_news_posted(art["guid"], category, art["title"],
                                          art["url"], art["source"])
                continue
            channel = self._channel(guild, category)
            if channel is None:
                continue
            try:
                await channel.send(embed=self._embed(category, art))
                await db.mark_news_posted(art["guid"], category, art["title"],
                                          art["url"], art["source"])
                posted[category] += 1
            except discord.HTTPException as exc:
                log.warning("News post failed: %s", exc)
        if priming:
            log.info("News primed (%d articles marked seen, none posted)", len(articles))
        return posted

    @tasks.loop(minutes=settings.news_poll_minutes)
    async def poll(self) -> None:
        if not await self._enabled():
            return
        guild = self.bot.get_guild(settings.guild_id)
        if guild is None:
            return
        posted = await self._run(guild)
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
        posted = await self._run(interaction.guild, force=True)
        total = sum(posted.values())
        await interaction.followup.send(
            f"✅ Checked feeds — posted {total} new article(s) "
            f"(PvP {posted['tbc-pvp']}, PvE {posted['tbc-pve']}, Retail {posted['retail']}).",
            ephemeral=True)

    @news.command(name="stats", description="Show how many articles have been posted.")
    async def stats(self, interaction: discord.Interaction) -> None:
        s = await db.news_stats()
        desc = "\n".join(
            f"{CATEGORY_META[c]['emoji']} **{CATEGORY_META[c]['label']}** — {s.get(c, 0)}"
            for c in ("tbc-pvp", "tbc-pve", "retail")
        )
        embed = discord.Embed(title="📰 News stats", description=desc, color=0xC79C6E)
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

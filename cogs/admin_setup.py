"""
Channel admin: the welcome panel and moving channels between categories.

This used to also carry `/setup-server`, a one-shot provisioner that built the
whole server layout and DELETED channels and roles it considered dead. That was
removed: the server is long since provisioned, the layout it built no longer
matched reality (it would have recreated COMMUNITY / ARENA / NEWS / BIS
alongside the real BUSITWIDE / WORLD OF WARCRAFT categories), and its cleanup
step deleted by name — a destructive command that could only do harm. Nothing
here creates or deletes channels or roles any more.

What's left:
  /welcome refresh  — replace the bot's welcome post + verify panel
  /welcome here     — print this channel's id for WELCOME_CHANNEL_ID
  /move-channel     — move a channel into a category, preserving its overwrites
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from config import settings, COLOR_GOLD

log = logging.getLogger("setup")

RULES_BODY = (
    "# 📜 Rules\n"
    "Keep it simple:\n\n"
    "**1. No toxicity.** Respect everyone here.\n"
    "**2. No account sharing.** Verify your own character.\n"
    "**3. No spam.** Keep channels on-topic.\n"
    "**4. Have fun and win games.** 🏆"
)

WELCOME_TITLE = "⚔️ NIGHTSLAYER ARENAS"
WELCOME_BODY = (
    "A private **TBC Classic** PvP hub — arena partners only.\n\n"
    "Tap **Get Started** below and pick your path:\n"
    "🎮 **WoW** → **⚔️ PvP** (verify your character) or **🛡️ PvE**\n"
    "😎 **Chill** → community access, no WoW required\n\n"
    "Verified PvP players get their rating, class and spec roles automatically."
)


class AdminSetup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.log: list[str] = []

    def _note(self, msg: str) -> None:
        self.log.append(msg)
        log.info(msg)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _find_category(self, guild: discord.Guild,
                       label: str) -> Optional[discord.CategoryChannel]:
        needle = label.lower().strip()
        return discord.utils.find(
            lambda c: needle in c.name.lower(), guild.categories)

    @staticmethod
    async def _configured_news_channel_id() -> int:
        """Merged news channel id: /news channel override, else NEWS_CHANNEL_ID."""
        raw = await db.get_setting("news_channel_id", "")
        if raw.isdigit():
            return int(raw)
        return settings.news_channel_id

    @staticmethod
    def _by_id_or_name(guild: discord.Guild, channel_id: int,
                       name: str) -> Optional[discord.TextChannel]:
        """ID first (rename-proof), then name (legacy servers)."""
        if channel_id:
            ch = guild.get_channel(channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
            log.warning("Configured channel %s not found — falling back to #%s",
                        channel_id, name)
        return discord.utils.get(guild.text_channels, name=name)

    def _welcome_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        return self._by_id_or_name(guild, settings.welcome_channel_id, "welcome")

    def _rules_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        return self._by_id_or_name(guild, settings.rules_channel_id, "rules")

    async def _welcome_embed(self, guild: discord.Guild) -> discord.Embed:
        """Intro embed. Links the merged news channel by mention when it resolves."""
        embed = discord.Embed(title=WELCOME_TITLE, description=WELCOME_BODY,
                              color=COLOR_GOLD)
        news_id = await self._configured_news_channel_id()
        news = guild.get_channel(news_id) if news_id else None
        if isinstance(news, discord.TextChannel):
            embed.add_field(
                name="📰 News",
                value=f"TBC Classic PvP & PvE news posts automatically in {news.mention}.",
                inline=False)
        return embed

    async def _purge_own(self, channel: discord.TextChannel, limit: int = 50) -> int:
        """Delete the bot's own messages in a channel. Never touches anyone else's."""
        deleted = 0
        async for msg in channel.history(limit=limit):
            if msg.author.id == self.bot.user.id:
                try:
                    await msg.delete()
                    deleted += 1
                except discord.HTTPException:
                    pass
        return deleted

    @staticmethod
    async def _is_empty(channel: discord.TextChannel) -> bool:
        async for _ in channel.history(limit=1):
            return False
        return True

    async def _seed(self, guild: discord.Guild, force: bool = False) -> None:
        """Post the welcome intro + panel and the rules body.

        Without `force`, only writes into an *empty* channel, so this can never
        spam a live one. `force` (from /welcome refresh) first deletes the bot's
        own old messages, then re-posts — the only way to replace a stale or
        duplicated panel.
        """
        from cogs.verification import VerifyPanel

        welcome = self._welcome_channel(guild)
        rules = self._rules_channel(guild)

        if welcome is None:
            self._note("⚠️ No welcome channel found — set WELCOME_CHANNEL_ID")
        else:
            if force:
                n = await self._purge_own(welcome)
                self._note(f"Cleared {n} old bot message(s) from #{welcome.name}")
            if force or await self._is_empty(welcome):
                await welcome.send(embed=await self._welcome_embed(guild),
                                   view=VerifyPanel())
                self._note(f"Posted #{welcome.name} intro + verify panel")

        if rules is None:
            self._note("⚠️ No rules channel found — set RULES_CHANNEL_ID")
        else:
            if force:
                await self._purge_own(rules)
            if force or await self._is_empty(rules):
                await rules.send(RULES_BODY)
                self._note(f"Posted #{rules.name} content")

    # ── commands ─────────────────────────────────────────────────────────────
    welcome = app_commands.Group(name="welcome",
                                 description="Welcome channel + verify panel controls.")

    @welcome.command(name="refresh",
                     description="[Staff] Delete the bot's old welcome posts and re-post "
                                 "a fresh intro + verify panel.")
    async def welcome_refresh(self, interaction: discord.Interaction) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        guild = interaction.guild
        channel = self._welcome_channel(guild)
        if channel is None:
            await interaction.response.send_message(
                "❌ No welcome channel resolved. Set `WELCOME_CHANNEL_ID`, or use "
                "`/welcome here` in the channel you want.", ephemeral=True)
            return

        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.manage_messages):
            await interaction.response.send_message(
                f"❌ I need **Send Messages** and **Manage Messages** in {channel.mention} "
                f"to replace the old panel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        self.log.clear()
        await self._seed(guild, force=True)
        await interaction.followup.send(
            f"✅ {channel.mention} refreshed:\n" + "\n".join(f"• {n}" for n in self.log),
            ephemeral=True)

    @welcome.command(name="here",
                     description="[Staff] Use THIS channel as the welcome channel.")
    async def welcome_here(self, interaction: discord.Interaction) -> None:
        from core import is_staff
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"ℹ️ Set `WELCOME_CHANNEL_ID={interaction.channel_id}` in your environment "
            f"and redeploy, then run `/welcome refresh`.\n"
            f"(Channel ID is stored in env, not the DB, so it survives a DB reset.)",
            ephemeral=True)

    @app_commands.command(name="move-channel",
                          description="[Owner] Move a channel into a category, keeping its permissions.")
    @app_commands.describe(channel="Channel to move",
                           category="Target category name, e.g. WORLD OF WARCRAFT")
    async def move_channel(self, interaction: discord.Interaction,
                           channel: discord.abc.GuildChannel, category: str) -> None:
        guild = interaction.guild
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message("❌ Server owner only.", ephemeral=True)
            return
        if isinstance(channel, discord.CategoryChannel):
            await interaction.response.send_message("That's a category, not a channel.",
                                                    ephemeral=True)
            return
        cat = self._find_category(guild, category)
        if cat is None:
            names = ", ".join(c.name for c in guild.categories)
            await interaction.response.send_message(
                f"❌ No category matching **{category}**. Have: {names}", ephemeral=True)
            return
        try:
            # category= keeps the channel's own overwrites (no sync) → stays hidden if it was.
            await channel.edit(category=cat)
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Missing Manage Channels permission.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Moved {channel.mention} into **{cat.name}** (permissions preserved).",
            ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminSetup(bot))

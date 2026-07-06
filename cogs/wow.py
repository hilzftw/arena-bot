"""
WoW quality-of-life commands: reset timers, arena-season countdown, and
Wowhead item/spell lookups. All lightweight — timers are computed locally and
lookups are just links, so there are no extra dependencies or API calls.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote_plus

import discord
from discord import app_commands
from discord.ext import commands

import core
import db
from config import settings, COLOR_GOLD, COLOR_BLUE, COLOR_PURPLE

# US daily reset is 15:00 UTC; weekly reset is Tuesday 15:00 UTC. Configurable.
RESET_HOUR = 15
WOWHEAD = "https://www.wowhead.com/tbc"


def _next_daily() -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    reset = now.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if now >= reset:
        reset += dt.timedelta(days=1)
    return reset


def _next_weekly() -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    days = (1 - now.weekday()) % 7            # Tuesday = 1
    reset = (now + dt.timedelta(days=days)).replace(
        hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if reset <= now:
        reset += dt.timedelta(days=7)
    return reset


def _ts(when: dt.datetime, style: str = "R") -> str:
    return f"<t:{int(when.timestamp())}:{style}>"


class WoW(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="reset", description="Time until the next daily and weekly reset.")
    async def reset(self, interaction: discord.Interaction) -> None:
        daily, weekly = _next_daily(), _next_weekly()
        embed = discord.Embed(title="⏰ WoW Resets", color=COLOR_BLUE)
        embed.add_field(name="Daily", value=f"{_ts(daily)} ({_ts(daily, 't')})", inline=False)
        embed.add_field(name="Weekly (Tue)", value=f"{_ts(weekly)} ({_ts(weekly, 'f')})",
                        inline=False)
        embed.set_footer(text="US reset times (15:00 UTC)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    season = app_commands.Group(name="season", description="Arena season countdown.")

    @season.command(name="show", description="Show the arena season countdown.")
    async def season_show(self, interaction: discord.Interaction) -> None:
        raw = await db.get_setting("arena_season_end")
        if not raw:
            await interaction.response.send_message(
                "No arena season end set. An admin can set it with `/season set`.", ephemeral=True)
            return
        try:
            end = dt.datetime.fromisoformat(raw).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            await interaction.response.send_message("Season end date is malformed.", ephemeral=True)
            return
        embed = discord.Embed(
            title="🏆 Arena Season", color=COLOR_PURPLE,
            description=f"Season ends {_ts(end)} ({_ts(end, 'D')})")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @season.command(name="set", description="[Staff] Set the arena season end date (YYYY-MM-DD).")
    async def season_set(self, interaction: discord.Interaction, date: str) -> None:
        if not core.is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            await interaction.response.send_message("Use `YYYY-MM-DD`.", ephemeral=True)
            return
        await db.set_setting("arena_season_end", f"{date}T{RESET_HOUR:02d}:00:00")
        await interaction.response.send_message(f"✅ Arena season set to end {date}.", ephemeral=True)

    @app_commands.command(name="item", description="Look up a TBC item on Wowhead.")
    @app_commands.describe(name="Item name")
    async def item(self, interaction: discord.Interaction, name: str) -> None:
        url = f"{WOWHEAD}/items?filter=na={quote_plus(name)}"
        await interaction.response.send_message(
            f"🔎 **{name}** on Wowhead (TBC): {url}", ephemeral=True)

    @app_commands.command(name="spell", description="Look up a TBC spell/talent on Wowhead.")
    @app_commands.describe(name="Spell or talent name")
    async def spell(self, interaction: discord.Interaction, name: str) -> None:
        url = f"{WOWHEAD}/spells?filter=na={quote_plus(name)}"
        await interaction.response.send_message(
            f"🔎 **{name}** on Wowhead (TBC): {url}", ephemeral=True)

    @app_commands.command(name="realm", description="Realm status links.")
    async def realm(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="🌐 Realm Status", color=COLOR_GOLD, description=(
            "• [Blizzard Classic Realm Status]"
            "(https://worldofwarcraft.blizzard.com/en-us/game/status/classic-us)\n"
            "• [Ironforge.pro](https://ironforge.pro)"))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WoW(bot))

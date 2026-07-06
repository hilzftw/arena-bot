"""
Bot health monitoring → #bot-logs.

Posts an "online" note on startup and watches gateway latency, alerting only when
something looks wrong (no spam). Slash-command errors are surfaced here too via the
tree error handler wired up in bot.py.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

import core
from config import settings, COLOR_GREEN, COLOR_RED

log = logging.getLogger("health")

LATENCY_WARN = 1.0  # seconds


class Health(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._announced = False
        self._warned = False
        self.heartbeat.start()

    async def cog_unload(self) -> None:
        self.heartbeat.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._announced:
            return
        self._announced = True
        guild = self.bot.get_guild(settings.guild_id)
        await core.log_event(
            guild, f"✅ **{self.bot.user}** online — {len(self.bot.cogs)} modules, "
            f"{round(self.bot.latency * 1000)}ms latency.", COLOR_GREEN)

    @tasks.loop(minutes=15)
    async def heartbeat(self) -> None:
        latency = self.bot.latency
        guild = self.bot.get_guild(settings.guild_id)
        if latency and latency > LATENCY_WARN:
            if not self._warned:
                self._warned = True
                await core.log_event(
                    guild, f"⚠️ High gateway latency: {round(latency * 1000)}ms.", COLOR_RED)
        elif self._warned:
            self._warned = False
            await core.log_event(guild, "✅ Latency recovered.", COLOR_GREEN)

    @heartbeat.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Health(bot))

"""
Nightslayer Arenas — TBC Anniversary Classic PvP bot.

A lightweight personal assistant for finding arena partners:
  • /verify + a persistent panel assign PvP roles
  • /lfg posts a Join card; the bot DMs both matched players to team up
  • Guests expire automatically; Friends never do
  • /friend, /blacklist, /whois, /cleanup for light moderation

Startup work lives in setup_hook (runs once), not on_ready (fires on every
reconnect). Slash commands are synced to the single configured guild.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

import blizzard
import db
import ironforge
from config import settings
from cogs.lfg import LFGJoinButton
from cogs.verification import VerifyPanel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

COGS = ["cogs.verification", "cogs.lfg", "cogs.moderation", "cogs.admin_setup"]
if settings.twitch_enabled:
    COGS.append("cogs.twitch")


class ArenaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True          # required for role assignment & member events
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.guild_obj = discord.Object(id=settings.guild_id)

    async def setup_hook(self) -> None:
        await db.init_db()
        await ironforge.refresh_all()

        # Register persistent interaction handlers.
        self.add_view(VerifyPanel())
        self.add_dynamic_items(LFGJoinButton)

        for ext in COGS:
            await self.load_extension(ext)
            log.info("Loaded %s", ext)

        # Sync commands to the guild for instant availability.
        self.tree.copy_global_to(guild=self.guild_obj)
        synced = await self.tree.sync(guild=self.guild_obj)
        log.info("Synced %d slash commands to guild %s", len(synced), settings.guild_id)

        self.cache_refresh.start()

    async def on_ready(self) -> None:
        log.info("Online as %s (%s)", self.user, self.user.id)

    async def close(self) -> None:
        # Graceful shutdown: release network + db handles.
        await ironforge.close()
        await blizzard.close()
        await db.close()
        await super().close()

    @tasks.loop(minutes=settings.cache_refresh_minutes)
    async def cache_refresh(self) -> None:
        await ironforge.refresh_all()
        log.info("Ironforge ladder cache refreshed")

    @cache_refresh.before_loop
    async def _before_cache(self) -> None:
        await self.wait_until_ready()


async def main() -> None:
    settings.validate()
    async with ArenaBot() as bot:
        await bot.start(settings.token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")

"""
Nightslayer Arenas — TBC Anniversary Classic PvP bot.

A lightweight personal assistant for the arena community:
  • /verify + a persistent panel assign PvP roles
  • Filtered TBC Classic news auto-posts to the news channel
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
from cogs.verification import VerifyPanel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

COGS = ["cogs.verification", "cogs.moderation", "cogs.admin_setup",
        "cogs.wow", "cogs.health"]
if settings.twitch_enabled:
    COGS.append("cogs.twitch")
if settings.news_enabled:
    COGS.append("cogs.news")


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

        for ext in COGS:
            await self.load_extension(ext)
            log.info("Loaded %s", ext)

        self.tree.on_error = self._on_app_error

        # Sync commands to the guild for instant availability.
        self.tree.copy_global_to(guild=self.guild_obj)
        synced = await self.tree.sync(guild=self.guild_obj)
        log.info("Synced %d slash commands to guild %s", len(synced), settings.guild_id)

        self.cache_refresh.start()

    async def on_ready(self) -> None:
        log.info("Online as %s (%s)", self.user, self.user.id)

    async def _on_app_error(self, interaction: discord.Interaction,
                            error: discord.app_commands.AppCommandError) -> None:
        import core
        cmd = interaction.command.name if interaction.command else "?"
        log.exception("App command error in /%s", cmd, exc_info=error)
        await core.log_event(
            interaction.guild, f"⚠️ Error in `/{cmd}`: {error}", 0xC41E3A)
        msg = "⚠️ Something went wrong — logged to #bot-logs."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

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

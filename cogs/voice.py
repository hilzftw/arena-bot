"""
Temporary voice rooms.

On an LFG match the bot creates a private voice channel for the two players under
the configured voice category, moves them in if they're already connected, and
deletes the room automatically once it empties out. Temp rooms are identified by a
name prefix so permanent channels (Waiting Room, AFK) are never touched.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import settings

log = logging.getLogger("voice")

TEMP_PREFIX = "⚔️ "


class Voice(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _category(self, guild: discord.Guild) -> discord.CategoryChannel | None:
        return guild.get_channel(settings.voice_category_id) if settings.voice_category_id else None

    def _is_temp(self, channel: discord.abc.GuildChannel | None) -> bool:
        return (isinstance(channel, discord.VoiceChannel)
                and channel.name.startswith(TEMP_PREFIX)
                and (self._category(channel.guild) is None
                     or channel.category_id == settings.voice_category_id))

    @commands.Cog.listener()
    async def on_lfg_match(self, poster: discord.Member,
                           joiner: discord.Member, bracket: str) -> None:
        guild = poster.guild
        category = self._category(guild)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=False),
            poster: discord.PermissionOverwrite(connect=True, view_channel=True),
            joiner: discord.PermissionOverwrite(connect=True, view_channel=True),
            guild.me: discord.PermissionOverwrite(connect=True, view_channel=True, move_members=True),
        }
        try:
            vc = await guild.create_voice_channel(
                f"{TEMP_PREFIX}{poster.display_name} {bracket}",
                category=category, overwrites=overwrites,
                reason="LFG match temp room",
            )
        except discord.Forbidden:
            log.warning("Missing Manage Channels permission — cannot create temp voice")
            return

        for member in (poster, joiner):
            if member.voice and member.voice.channel:
                try:
                    await member.move_to(vc, reason="LFG match")
                except discord.HTTPException:
                    pass
        await _safe_dm(joiner, f"🔊 Voice room ready: **{vc.name}**")
        log.info("Created temp voice %s for %s + %s", vc.name, poster, joiner)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        # Only care when someone leaves a channel.
        left = before.channel
        if left is None or left == after.channel:
            return
        if self._is_temp(left) and len(left.members) == 0:
            try:
                await left.delete(reason="Temp room empty")
                log.info("Deleted empty temp voice %s", left.name)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Startup sweep: clear any empty temp rooms left over from a crash/restart.
        guild = self.bot.get_guild(settings.guild_id)
        if guild is None:
            return
        for channel in guild.voice_channels:
            if self._is_temp(channel) and len(channel.members) == 0:
                try:
                    await channel.delete(reason="Startup cleanup of empty temp room")
                except discord.HTTPException:
                    pass


async def _safe_dm(member: discord.Member, content: str) -> None:
    try:
        await member.send(content)
    except discord.Forbidden:
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice(bot))

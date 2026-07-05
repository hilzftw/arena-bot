"""
Moderation & membership: friend promotion, blacklist, whois, and cleanup.

Cleanup is the durable replacement for the old asyncio.sleep auto-kick: an hourly
task (plus a manual /cleanup) that removes expired guests and never-verified
stragglers, while always ignoring Friend, Moderator, and Admin.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core
import db
from cogs.verification import profile_embed
from config import settings, COLOR_GREEN, COLOR_RED, COLOR_BLUE

log = logging.getLogger("moderation")


def staff_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if core.is_staff(interaction.user):
            return True
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return False
    return app_commands.check(predicate)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cleanup_loop.start()

    async def cog_unload(self) -> None:
        self.cleanup_loop.cancel()

    # ── friend ────────────────────────────────────────────────────────────────
    friend = app_commands.Group(name="friend", description="Manage permanent Friends.")

    @friend.command(name="add", description="Promote a member to permanent Friend.")
    @staff_only()
    async def friend_add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        role = core.resolve_role(interaction.guild, settings.friend_role_id)
        if role:
            await member.add_roles(role, reason=f"Friend by {interaction.user}")
        # Mark permanent in DB (NULL expiry) if they have a record.
        if await db.get_user(str(member.id)):
            await db.set_expiry(str(member.id), None)
        await interaction.response.send_message(
            f"✅ {member.mention} is now a permanent **Friend** and will never expire.",
            ephemeral=True)

    @friend.command(name="remove", description="Demote a Friend back to Guest.")
    @staff_only()
    async def friend_remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        role = core.resolve_role(interaction.guild, settings.friend_role_id)
        if role and role in member.roles:
            await member.remove_roles(role, reason=f"Friend removed by {interaction.user}")
        if await db.get_user(str(member.id)):
            await db.set_expiry(str(member.id), core.guest_expiry_ts())
        await interaction.response.send_message(
            f"✅ {member.mention} is back to **Guest** "
            f"(expires in {settings.guest_expiration_days} days).", ephemeral=True)

    # ── blacklist ───────────────────────────────────────────────────────────────
    blacklist = app_commands.Group(name="blacklist", description="Ban members from the server.")

    @blacklist.command(name="add", description="Blacklist and ban a member.")
    @app_commands.describe(member="Member to blacklist", reason="Optional reason")
    @staff_only()
    async def blacklist_add(self, interaction: discord.Interaction,
                            member: discord.Member, reason: Optional[str] = None) -> None:
        await db.add_blacklist(str(member.id), reason)
        await db.delete_user(str(member.id))
        try:
            await member.ban(reason=f"Blacklisted by {interaction.user}: {reason or 'n/a'}",
                             delete_message_days=0)
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Recorded, but I lack Ban Members permission.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ {member.mention} blacklisted and banned.", ephemeral=True)

    @blacklist.command(name="remove", description="Remove a user id from the blacklist and unban.")
    @app_commands.describe(user_id="The user's Discord ID")
    @staff_only()
    async def blacklist_remove(self, interaction: discord.Interaction, user_id: str) -> None:
        await db.remove_blacklist(user_id)
        try:
            await interaction.guild.unban(discord.Object(id=int(user_id)))
        except (discord.NotFound, discord.HTTPException, ValueError):
            pass
        await interaction.response.send_message(
            f"✅ Removed `{user_id}` from the blacklist.", ephemeral=True)

    # ── whois ────────────────────────────────────────────────────────────────────
    @app_commands.command(name="whois", description="Show a member's verified character.")
    async def whois(self, interaction: discord.Interaction, member: discord.Member) -> None:
        data = await db.get_user(str(member.id))
        if not data:
            await interaction.response.send_message(
                f"{member.mention} hasn't verified.", ephemeral=True)
            return
        await interaction.response.send_message(embed=profile_embed(member, data), ephemeral=True)

    # ── cleanup ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="cleanup", description="[Staff] Remove expired guests now.")
    @staff_only()
    async def cleanup(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guests, stragglers = await self._run_cleanup(interaction.guild)
        await interaction.followup.send(
            f"✅ Removed {guests} expired guest(s) and {stragglers} unverified straggler(s).",
            ephemeral=True)

    async def _run_cleanup(self, guild: discord.Guild) -> tuple[int, int]:
        """Remove expired guests + never-verified stragglers. Returns (guests, stragglers)."""
        protected = {settings.friend_role_id, settings.mod_role_id, settings.admin_role_id}
        guest_role_id = settings.guest_role_id

        def is_protected(m: discord.Member) -> bool:
            return (m.bot
                    or (settings.owner_id and m.id == settings.owner_id)
                    or any(r.id in protected for r in m.roles if r.id))

        removed_guests = 0
        for row in await db.get_expired_guests():
            member = guild.get_member(int(row["discord_id"]))
            if member is None:
                await db.delete_user(row["discord_id"])
                continue
            if is_protected(member):
                continue
            try:
                await member.kick(reason="Guest expired")
                await db.delete_user(row["discord_id"])
                removed_guests += 1
            except discord.Forbidden:
                log.warning("Cannot kick expired guest %s", member)

        # Never-verified stragglers: no guest/friend/staff role, joined long enough ago.
        cutoff = time.time() - settings.verify_timeout_hours * 3600
        removed_stragglers = 0
        access_ids = {guest_role_id} | protected
        for member in guild.members:
            if is_protected(member) or member.bot:
                continue
            if any(r.id in access_ids for r in member.roles if r.id):
                continue
            if member.joined_at and member.joined_at.timestamp() < cutoff:
                try:
                    await member.kick(reason=f"Not verified within {settings.verify_timeout_hours}h")
                    removed_stragglers += 1
                except discord.Forbidden:
                    pass
        return removed_guests, removed_stragglers

    @tasks.loop(hours=1)
    async def cleanup_loop(self) -> None:
        guild = self.bot.get_guild(settings.guild_id)
        if guild:
            g, s = await self._run_cleanup(guild)
            if g or s:
                log.info("Auto-cleanup: %d guests, %d stragglers removed", g, s)

    @cleanup_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    # ── join gate ────────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if await db.is_blacklisted(str(member.id)):
            try:
                await member.ban(reason="Blacklisted", delete_message_days=0)
                log.info("Auto-banned blacklisted member %s on join", member)
            except discord.Forbidden:
                log.warning("Cannot ban blacklisted %s — missing permission", member)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))

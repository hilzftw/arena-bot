"""
One-time, owner-only server provisioning.

`/setup-server confirm:True` builds the full Nightslayer Arenas layout in one pass:
categories, channels, the verification permission gate, hidden ADMIN/BIS areas,
native AFK, welcome/rules content, and cleanup of dead channels/roles. It is
idempotent — safe to re-run; it adopts anything that already exists by name.

Requires the bot to have Manage Channels, Manage Roles, and Manage Server, and its
role to sit above the roles/channels it edits. Each step is wrapped so a single
failure (e.g. a missing permission) is reported rather than aborting the whole run.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import settings, COLOR_GOLD, COLOR_BLUE, COLOR_RED

log = logging.getLogger("setup")

# Canonical category names (emoji + label).
INFO = "📌 INFORMATION"
COMMUNITY = "💬 COMMUNITY"
ARENA = "⚔️ ARENA"
ADMIN = "🔒 ADMIN"
BIS = "⭐ BIS"
VOICE = "🔊 VOICE"

# Channels to delete outright (lowercased names). "arena" handled separately by type.
DEAD_TEXT = {"fuck-12", "announcements", "5v5-push"}
DEAD_VOICE = {"pug q's", "2v", "3v", "5v", "arena"}
DEAD_CATEGORIES = {"snoozin", "tbc arena"}
DEAD_ROLES = {"bros", "rank 1"}

WELCOME_BODY = (
    "# ⚔️ NIGHTSLAYER ARENAS\n"
    "A private TBC Anniversary Classic PvP hub — arena partners only.\n\n"
    "**How it works**\n"
    "1. Head to <#{verify}> and verify your character.\n"
    "2. Receive your class, spec, faction, and rating roles automatically.\n"
    "3. When I'm looking for partners I post an LFG in ⚔️ ARENA — click **Join Queue**.\n\n"
    "Verification is required to unlock the server. Inactive guests are removed "
    "automatically; regulars get promoted to **Friend** and stay forever."
)

RULES_BODY = (
    "# 📜 Rules\n"
    "Keep it simple:\n\n"
    "**1. No toxicity.** Respect everyone here.\n"
    "**2. No account sharing.** Verify your own character.\n"
    "**3. No spam.** Keep channels on-topic.\n"
    "**4. Have fun and win games.** 🏆"
)


def ow(**perms) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(**perms)


class AdminSetup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.log: list[str] = []

    def _note(self, msg: str) -> None:
        self.log.append(msg)
        log.info(msg)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _role(self, guild: discord.Guild, *names: str) -> Optional[discord.Role]:
        for n in names:
            r = discord.utils.find(lambda x: x.name.lower() == n.lower(), guild.roles)
            if r:
                return r
        return None

    def _find_category(self, guild: discord.Guild, label: str) -> Optional[discord.CategoryChannel]:
        key = label.split(" ", 1)[-1].lower()  # text part, ignore emoji
        return discord.utils.find(
            lambda c: c.name.split(" ", 1)[-1].lower() == key, guild.categories)

    async def _category(self, guild: discord.Guild, label: str,
                        overwrites: dict) -> discord.CategoryChannel:
        cat = self._find_category(guild, label)
        if cat is None:
            cat = await guild.create_category(label, overwrites=overwrites)
            self._note(f"Created category {label}")
        else:
            await cat.edit(name=label, overwrites=overwrites)
            self._note(f"Updated category {label}")
        return cat

    async def _text(self, guild: discord.Guild, name: str,
                    category: discord.CategoryChannel, sync: bool = True,
                    overwrites: Optional[dict] = None) -> discord.TextChannel:
        ch = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel) and c.name == name,
            guild.channels)
        if ch is None:
            ch = await guild.create_text_channel(name, category=category)
            self._note(f"Created #{name}")
        elif ch.category_id != category.id:
            await ch.edit(category=category)
            self._note(f"Moved #{name} into {category.name}")
        if overwrites is not None:
            await ch.edit(overwrites=overwrites)
        elif sync:
            await ch.edit(sync_permissions=True)
        return ch

    # ── command ──────────────────────────────────────────────────────────────
    @app_commands.command(name="setup-server",
                          description="[Owner] Build/repair the full server layout. Destructive.")
    @app_commands.describe(confirm="Must be True to run — deletes dead channels and roles.")
    async def setup_server(self, interaction: discord.Interaction, confirm: bool = False) -> None:
        guild = interaction.guild
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message("❌ Server owner only.", ephemeral=True)
            return
        if not confirm:
            await interaction.response.send_message(
                "⚠️ This rebuilds the whole server and deletes dead channels/roles. "
                "Re-run with `confirm: True` to proceed.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        self.log = []
        everyone = guild.default_role
        me = guild.me
        guest = self._role(guild, "Guest")
        friend = self._role(guild, "Friend")
        bis = self._role(guild, "BIS")
        admin = self._role(guild, "admin", "Admin")
        mod = self._role(guild, "Moderator", "Mod")
        social = self._role(guild, "Social")
        if social is None:
            try:
                social = await guild.create_role(
                    name="Social", colour=discord.Colour(0x95A5A6), hoist=True,
                    reason="Non-PvP / spectator access")
                self._note("Created role Social")
            except discord.HTTPException:
                social = None

        def gated(include_social: bool = False) -> dict:
            """Hidden from @everyone, visible to verified/staff roles. Community and
            voice include Social (non-PvP); the arena stays PvP-verified only."""
            o = {everyone: ow(view_channel=False), me: ow(view_channel=True)}
            roles = [guest, friend, bis, admin, mod]
            if include_social:
                roles.append(social)
            for r in roles:
                if r:
                    o[r] = ow(view_channel=True, connect=True)
            return o

        try:
            # 1. INFORMATION — public, but posting locked down.
            info = await self._category(guild, INFO, {everyone: ow(view_channel=True)})
            await self._text(guild, "welcome", info, overwrites={
                everyone: ow(view_channel=True, send_messages=False),
                me: ow(view_channel=True, send_messages=True)})
            verify = await self._text(guild, "verify", info, overwrites={
                everyone: ow(view_channel=True, send_messages=False),
                me: ow(view_channel=True, send_messages=True)})
            await self._text(guild, "rules", info, overwrites={
                everyone: ow(view_channel=True, send_messages=False),
                me: ow(view_channel=True, send_messages=True)})

            # 2. COMMUNITY — gated (includes Social / non-PvP).
            community = await self._category(guild, COMMUNITY, gated(include_social=True))
            await self._text(guild, "general", community)
            await self._text(guild, "twitchy-p-clips", community)
            await self._text(guild, "music", community)

            # 3. ARENA — gated. Rename LFG channels (preserves IDs used by Railway).
            arena = await self._category(guild, ARENA, gated())
            await self._rename_text(guild, "2v2-lfg", "2v2")
            await self._rename_text(guild, "3v3-lfg", "3v3")
            await self._rename_text(guild, "5v5-lfg", "5v5")
            await self._text(guild, "arena", arena)   # new general arena text channel
            for nm in ("2v2", "3v3", "5v5"):
                await self._text(guild, nm, arena)

            # 4. ADMIN — hidden to all but staff. bot-logs is bot-write-only.
            staff_ovw = {everyone: ow(view_channel=False), me: ow(view_channel=True)}
            for r in (admin, mod):
                if r:
                    staff_ovw[r] = ow(view_channel=True)
            adm = await self._category(guild, ADMIN, staff_ovw)
            await self._text(guild, "war-room", adm)
            botlogs_ovw = dict(staff_ovw)
            botlogs_ovw[me] = ow(view_channel=True, send_messages=True)
            for r in (admin, mod):
                if r:
                    botlogs_ovw[r] = ow(view_channel=True, send_messages=False)
            await self._text(guild, "bot-logs", adm, overwrites=botlogs_ovw)

            # 5. BIS — hidden; BIS + admin only.
            bis_ovw = {everyone: ow(view_channel=False), me: ow(view_channel=True)}
            for r in (bis, admin):
                if r:
                    bis_ovw[r] = ow(view_channel=True)
            bis_cat = await self._category(guild, BIS, bis_ovw)
            await self._text(guild, "bis-lounge", bis_cat)

            # 6. VOICE — gated (includes Social); Mike P's restricted to owner + admin + BIS.
            voice = await self._category(guild, VOICE, gated(include_social=True))
            await self._move_voice(guild, "gnome lives matter", voice, sync=True)
            mike = await self._move_voice(guild, "mike p's self play", voice, sync=False,
                                          rename="Mike P's Self Play Service")
            if mike:
                mvw = {everyone: ow(view_channel=False, connect=False),
                       me: ow(view_channel=True, connect=True),
                       guild.owner: ow(view_channel=True, connect=True)}
                if guest:
                    mvw[guest] = ow(view_channel=False)
                for r in (admin, bis):
                    if r:
                        mvw[r] = ow(view_channel=True, connect=True)
                await mike.edit(overwrites=mvw)

            # Ordering.
            for i, cat in enumerate((info, community, arena, adm, bis_cat, voice)):
                try:
                    await cat.edit(position=i)
                except discord.HTTPException:
                    pass

            # Hoist structural roles so the member-list groups them separately.
            await self._hoist_roles(guild)

            # Native AFK.
            await self._setup_afk(guild)

            # Content.
            await self._seed(interaction.channel_id, guild, verify)

            # Cleanup (destructive).
            if confirm:
                await self._cleanup(guild)

        except discord.Forbidden:
            self._note("⚠️ Forbidden — grant the bot Manage Channels, Manage Roles, "
                       "Manage Server, and move its role to the top.")
        except Exception as exc:  # noqa: BLE001
            self._note(f"⚠️ Error: {exc}")

        report = "\n".join(f"• {l}" for l in self.log) or "Nothing to do."
        if len(report) > 1900:
            report = report[:1900] + "\n… (truncated)"
        await interaction.followup.send(
            embed=discord.Embed(title="Server setup complete", description=report,
                                color=COLOR_GOLD), ephemeral=True)

    async def _rename_text(self, guild: discord.Guild, old: str, new: str) -> None:
        ch = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel) and c.name == old, guild.channels)
        if ch:
            await ch.edit(name=new)
            self._note(f"Renamed #{old} → #{new}")

    async def _move_voice(self, guild: discord.Guild, name_prefix: str,
                          category: discord.CategoryChannel, sync: bool,
                          rename: Optional[str] = None) -> Optional[discord.VoiceChannel]:
        ch = discord.utils.find(
            lambda c: isinstance(c, discord.VoiceChannel)
            and c.name.lower().startswith(name_prefix), guild.channels)
        if ch is None:
            return None
        kwargs = {"category": category}
        if rename and ch.name != rename:
            kwargs["name"] = rename
        await ch.edit(**kwargs)
        if sync:
            await ch.edit(sync_permissions=True)
        self._note(f"Configured voice {ch.name}")
        return ch

    async def _hoist_roles(self, guild: discord.Guild) -> None:
        """Show structural roles as separate groups in the member list; keep the
        cosmetic rating/class/spec roles out of that grouping."""
        hoist = {"admin", "moderator", "bis", "friend", "guest", "social"}
        flat = {"unranked", "1400+", "1800+", "2100+", "gladiator", "merciless gladiator"}
        for role in guild.roles:
            nm = role.name.lower()
            try:
                if nm in hoist and not role.hoist:
                    await role.edit(hoist=True)
                    self._note(f"Separated role {role.name} in member list")
                elif nm in flat and role.hoist:
                    await role.edit(hoist=False)
            except discord.HTTPException:
                pass

    async def _setup_afk(self, guild: discord.Guild) -> None:
        afk = discord.utils.find(
            lambda c: isinstance(c, discord.VoiceChannel) and c.name.lower() == "afk",
            guild.channels)
        if afk is None:
            afk = await guild.create_voice_channel("AFK")
        if afk.category is not None:
            await afk.edit(category=None)
        await guild.edit(afk_channel=afk, afk_timeout=300)
        self._note("Set native AFK channel (5 min timeout)")

    async def _seed(self, invoking_channel_id: int, guild: discord.Guild,
                    verify: discord.TextChannel) -> None:
        welcome = discord.utils.get(guild.text_channels, name="welcome")
        rules = discord.utils.get(guild.text_channels, name="rules")
        if welcome:
            async for _ in welcome.history(limit=1):
                break
            else:
                await welcome.send(WELCOME_BODY.format(verify=verify.id))
                self._note("Posted #welcome content")
        if rules:
            async for _ in rules.history(limit=1):
                break
            else:
                await rules.send(RULES_BODY)
                self._note("Posted #rules content")

    async def _cleanup(self, guild: discord.Guild) -> None:
        for ch in list(guild.channels):
            nm = ch.name.lower()
            try:
                if isinstance(ch, discord.TextChannel) and nm in DEAD_TEXT:
                    await ch.delete(reason="setup cleanup")
                    self._note(f"Deleted #{ch.name}")
                elif isinstance(ch, discord.VoiceChannel) and nm in DEAD_VOICE:
                    await ch.delete(reason="setup cleanup")
                    self._note(f"Deleted voice {ch.name}")
                elif isinstance(ch, discord.CategoryChannel) and nm in DEAD_CATEGORIES:
                    if not ch.channels:
                        await ch.delete(reason="setup cleanup")
                        self._note(f"Deleted category {ch.name}")
            except discord.HTTPException:
                pass
        for role in list(guild.roles):
            if role.name.lower() in DEAD_ROLES:
                try:
                    await role.delete(reason="setup cleanup")
                    self._note(f"Deleted role {role.name}")
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminSetup(bot))

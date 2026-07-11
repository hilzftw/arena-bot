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

import db
from config import settings, COLOR_GOLD, COLOR_BLUE, COLOR_RED

log = logging.getLogger("setup")

# Canonical category names (emoji + label).
INFO = "📌 INFORMATION"
COMMUNITY = "💬 COMMUNITY"
ARENA = "⚔️ ARENA"
NEWS = "📰 NEWS"
ADMIN = "🔒 ADMIN"
BIS = "⭐ BIS"
VOICE = "🔊 VOICE"

# Legacy three-channel news layout. Only recreated when NEWS_CHANNEL_ID is unset —
# otherwise /setup-server would rebuild the old channels and undo a merged feed.
LEGACY_NEWS_CHANNELS = ("tbc-pvp-news", "tbc-pve-news", "retail-wow-news")

# Channels to delete outright (lowercased names). "arena" handled separately by type.
DEAD_TEXT = {"fuck-12", "announcements", "5v5-push"}
DEAD_VOICE = {"pug q's", "2v", "3v", "5v", "arena"}
DEAD_CATEGORIES = {"snoozin", "tbc arena"}
DEAD_ROLES = {"bros", "rank 1"}

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
    "🎮 **WoW** → **⚔️ PvP** (verify your character, unlock LFG) "
    "or **🛡️ PvE**\n"
    "😎 **Chill** → community access, no WoW required\n\n"
    "Verified PvP players get their rating, class and spec roles automatically, "
    "and can **Join Queue** on any LFG post."
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
                           category="Target category name, e.g. COMMUNITY")
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
        social = await self._ensure_casual_role(guild, "Social", 0x95A5A6)
        pve = await self._ensure_casual_role(guild, "PvE", 0x2ECC71)

        def gated(include_casual: bool = False) -> dict:
            """Hidden from @everyone, visible to verified/staff roles. Community and
            voice also include the casual Social/PvE roles; the arena stays
            PvP-verified only."""
            o = {everyone: ow(view_channel=False), me: ow(view_channel=True)}
            roles = [guest, friend, bis, admin, mod]
            if include_casual:
                roles += [social, pve]
            for r in roles:
                if r:
                    o[r] = ow(view_channel=True, connect=True)
            return o

        try:
            # 1. INFORMATION — public, read-only. #welcome holds the intro + panel.
            info = await self._category(guild, INFO, {everyone: ow(view_channel=True)})
            await self._text(guild, "welcome", info, overwrites={
                everyone: ow(view_channel=True, send_messages=False),
                me: ow(view_channel=True, send_messages=True)})
            await self._text(guild, "rules", info, overwrites={
                everyone: ow(view_channel=True, send_messages=False),
                me: ow(view_channel=True, send_messages=True)})

            # 2. COMMUNITY — gated (includes casual Social / PvE).
            community = await self._category(guild, COMMUNITY, gated(include_casual=True))
            await self._text(guild, "general", community)
            await self._text(guild, "twitchy-p", community)
            await self._text(guild, "music", community)

            # 3. ARENA — gated. Rename LFG channels (preserves IDs used by Railway).
            arena = await self._category(guild, ARENA, gated())
            await self._rename_text(guild, "2v2-lfg", "2v2")
            await self._rename_text(guild, "3v3-lfg", "3v3")
            await self._rename_text(guild, "5v5-lfg", "5v5")
            for nm in ("2v2", "3v3", "5v5"):
                await self._text(guild, nm, arena)

            # 3b. NEWS — gated + read-only; only the bot posts.
            news = await self._category(guild, NEWS, gated(include_casual=True))
            news_ovw = gated(include_casual=True)
            for role, o in news_ovw.items():
                if role != me:
                    o.update(send_messages=False)
            news_ovw[me] = ow(view_channel=True, send_messages=True)

            # If a merged news channel is configured, adopt it: move it under NEWS
            # and apply the read-only overwrites. Recreating the legacy trio here
            # would silently undo the merge, so it only runs when nothing is set.
            merged_id = await self._configured_news_channel_id()
            merged = guild.get_channel(merged_id) if merged_id else None
            if isinstance(merged, discord.TextChannel):
                if merged.category_id != news.id:
                    await merged.edit(category=news)
                    self._note(f"Moved #{merged.name} into {news.name}")
                await merged.edit(overwrites=news_ovw)
                self._note(f"Adopted #{merged.name} as the news channel (read-only)")
            else:
                if merged_id:
                    self._note(f"⚠️ Configured news channel {merged_id} not found — "
                               f"rebuilding the legacy news channels")
                for nm in LEGACY_NEWS_CHANNELS:
                    await self._text(guild, nm, news, overwrites=news_ovw)

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

            # 6. VOICE — gated (includes casual); Mike P's restricted to owner + admin + BIS.
            voice = await self._category(guild, VOICE, gated(include_casual=True))
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
            for i, cat in enumerate((info, community, arena, news, adm, bis_cat, voice)):
                try:
                    await cat.edit(position=i)
                except discord.HTTPException:
                    pass

            # Hoist structural roles so the member-list groups them separately.
            await self._hoist_roles(guild)

            # Native AFK.
            await self._setup_afk(guild)

            # Content + verify panel (in #welcome).
            await self._seed(guild)

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

    async def _ensure_casual_role(self, guild: discord.Guild, name: str,
                                  colour: int) -> Optional[discord.Role]:
        """Find or create a casual (non-arena) access role like Social / PvE."""
        role = self._role(guild, name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=name, colour=discord.Colour(colour), hoist=True,
                    reason="Non-arena / casual access")
                self._note(f"Created role {name}")
            except discord.HTTPException:
                role = None
        return role

    async def _hoist_roles(self, guild: discord.Guild) -> None:
        """Show structural roles as separate groups in the member list; keep the
        cosmetic rating/class/spec roles out of that grouping."""
        hoist = {"admin", "moderator", "bis", "friend", "guest", "social", "pve"}
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

    async def _seed(self, guild: discord.Guild, force: bool = False) -> None:
        """Post the welcome intro + panel and the rules body.

        Without `force` this only writes into an *empty* channel, so a re-run of
        /setup-server never spams a live channel. `force` (used by /welcome refresh)
        first deletes the bot's own old messages, then re-posts — that's the only way
        to replace a stale or duplicated panel, which the old empty-channel-only
        check made impossible.
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

    @staticmethod
    async def _is_empty(channel: discord.TextChannel) -> bool:
        async for _ in channel.history(limit=1):
            return False
        return True

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

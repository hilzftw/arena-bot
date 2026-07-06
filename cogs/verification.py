"""
Verification: /verify slash command plus a persistent panel in #verify with
Verify / My Profile / Help buttons so members never need to remember a command.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import core
import db
from config import settings, COLOR_GREEN, COLOR_BLUE, COLOR_PURPLE, COLOR_RED, COLOR_GOLD

log = logging.getLogger("verification")


def _verify_result_embed(character: str, realm: str, r: core.VerifyResult) -> discord.Embed:
    color = COLOR_PURPLE if r.rating >= 2100 else COLOR_GREEN if r.rating >= 1800 else COLOR_BLUE
    embed = discord.Embed(title="✅ Verified", color=color)
    embed.add_field(name="Character", value=f"{character}-{realm}", inline=True)
    embed.add_field(name="Rating", value=f"{r.rating:,}" if r.rating else "Unranked", inline=True)
    if r.class_ or r.spec:
        embed.add_field(name="Spec", value=f"{r.spec or ''} {r.class_ or ''}".strip(), inline=True)
    if r.role_key:
        embed.add_field(name="Role", value=r.role_key, inline=True)
    if r.source == "blizzard_fallback":
        embed.set_footer(text="Not on the Ironforge ladder — assigned Unranked. "
                              "Play ranked games and re-verify.")
    return embed


async def _do_verify(interaction: discord.Interaction, character_realm: str) -> None:
    """Shared flow for both the slash command and the modal."""
    parsed = core.parse_character_realm(character_realm)
    if not parsed:
        await interaction.followup.send(
            "❌ Format: `CharacterName-RealmName`  e.g. `Brutus-Whitemane`", ephemeral=True)
        return
    character, realm = parsed

    if await db.is_blacklisted(str(interaction.user.id)):
        await interaction.followup.send("❌ You are not permitted to verify here.", ephemeral=True)
        return

    existing = await db.get_user(str(interaction.user.id))
    expires = -1 if existing else core.guest_expiry_ts()  # keep expiry on re-verify

    result = await core.run_verify(str(interaction.user.id), character, realm, expires)
    if not result.found:
        await interaction.followup.send(f"❌ {result.error}", ephemeral=True)
        return

    try:
        await core.apply_verify_roles(interaction.user, result)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Verified, but I couldn't assign roles — my role must sit above the "
            "roles I assign. Ask an admin to fix the role hierarchy.", ephemeral=True)
        return

    await interaction.followup.send(embed=_verify_result_embed(character, realm, result),
                                    ephemeral=True)
    log.info("Verified %s -> %s-%s (rating=%s)", interaction.user, character, realm, result.rating)
    await core.log_event(
        interaction.guild,
        f"✅ {interaction.user.mention} verified as **{character}-{realm}** — "
        f"{result.role_key or 'Unranked'} ({result.rating:,})", COLOR_GREEN)


class VerifyModal(discord.ui.Modal, title="Verify your character"):
    character_realm = discord.ui.TextInput(
        label="Character-Realm",
        placeholder="Brutus-Whitemane",
        required=True,
        max_length=64,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _do_verify(interaction, str(self.character_realm.value))


async def _grant_access_role(interaction: discord.Interaction, role_name: str,
                             success_msg: str) -> None:
    """Grant a non-arena access role (Social / PvE) from the verify panel."""
    role = discord.utils.get(interaction.guild.roles, name=role_name)
    if role is None:
        await interaction.response.send_message(
            "That access isn't set up yet — ask an admin to run `/setup-server`.",
            ephemeral=True)
        return
    if role in interaction.user.roles:
        await interaction.response.send_message("You already have that access. ✅", ephemeral=True)
        return
    try:
        await interaction.user.add_roles(role, reason=f"{role_name} (non-arena) access")
    except discord.Forbidden:
        await interaction.response.send_message(
            f"⚠️ Couldn't assign the role — my role needs to sit above **{role_name}**.",
            ephemeral=True)
        return
    await interaction.response.send_message(success_msg, ephemeral=True)


class PvPvEView(discord.ui.View):
    """Step 2 (WoW branch): PvP → character verify, PvE → PvE role."""

    def __init__(self) -> None:
        super().__init__(timeout=180)

    @discord.ui.button(label="PvP", emoji="⚔️", style=discord.ButtonStyle.danger)
    async def pvp(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(VerifyModal())

    @discord.ui.button(label="PvE", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def pve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await _grant_access_role(
            interaction, "PvE",
            "🛡️ Welcome, PvE'er! Community channels and PvE news are unlocked. The "
            "**arena** stays PvP-only — verify a character anytime to unlock LFG.")


class WoWChillView(discord.ui.View):
    """Step 1: WoW → PvP/PvE choice, Chill → Social (regular) access."""

    def __init__(self) -> None:
        super().__init__(timeout=180)

    @discord.ui.button(label="WoW", emoji="🎮", style=discord.ButtonStyle.success)
    async def wow(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Nice — **PvP** or **PvE**?\n"
                    "⚔️ PvP verifies your arena character and unlocks LFG.\n"
                    "🛡️ PvE gives you community + PvE news access.",
            view=PvPvEView())

    @discord.ui.button(label="Chill", emoji="😎", style=discord.ButtonStyle.secondary)
    async def chill(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await _grant_access_role(
            interaction, "Social",
            "😎 You're in — community channels unlocked, no WoW required. Hit "
            "**Get Started → WoW** anytime you want to play.")


class VerifyPanel(discord.ui.View):
    """Persistent panel — registered once via bot.add_view()."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Get Started", emoji="🎮",
                       style=discord.ButtonStyle.green, custom_id="ns:start")
    async def start(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Welcome to **Nightslayer Arenas** 👋\nAre you here for **WoW**, or just to **chill**?",
            view=WoWChillView(), ephemeral=True)

    @discord.ui.button(label="My Profile", emoji="👤",
                       style=discord.ButtonStyle.secondary, custom_id="ns:profile")
    async def profile(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        data = await db.get_user(str(interaction.user.id))
        if not data:
            await interaction.followup.send(
                "You haven't verified a character yet — click **Get Started → WoW → PvP**.",
                ephemeral=True)
            return
        await interaction.followup.send(embed=profile_embed(interaction.user, data), ephemeral=True)

    @discord.ui.button(label="Help", emoji="❓",
                       style=discord.ButtonStyle.secondary, custom_id="ns:help")
    async def help(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="How Nightslayer Arenas works", color=COLOR_GOLD,
            description=(
                "**1.** Click **Get Started**.\n"
                "**2.** Pick **WoW** (then **PvP** to verify your character, or **PvE**) "
                "or **Chill** for plain community access.\n"
                "**3.** PvP players receive their rating/class/spec roles and can click "
                "**Join Queue** on my LFG posts — I'll DM us both.\n\n"
                "Guests are removed after a period of inactivity; regulars get promoted "
                "to **Friend** and never expire."),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def profile_embed(user: discord.abc.User, data: dict) -> discord.Embed:
    is_friend = data.get("expires_at") is None
    embed = discord.Embed(title=f"{user.display_name}'s profile", color=COLOR_BLUE)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Character", value=f"{data['character']}-{data['realm']}", inline=True)
    embed.add_field(name="Faction", value=data.get("faction") or "—", inline=True)
    embed.add_field(name="Rating",
                    value=f"{data['rating']:,}" if data['rating'] else "Unranked", inline=True)
    embed.add_field(name="Class", value=data.get("class") or "—", inline=True)
    embed.add_field(name="Spec", value=data.get("spec") or "—", inline=True)
    embed.add_field(name="Status", value="Friend (permanent)" if is_friend else "Guest", inline=True)
    if data.get("verified_at"):
        embed.add_field(name="Verified", value=f"<t:{data['verified_at']}:D>", inline=True)
    src = {"ironforge": "Ironforge.pro", "blizzard_fallback": "Blizzard API"}.get(
        data.get("source"), data.get("source") or "—")
    embed.add_field(name="Source", value=src, inline=True)
    return embed


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="verify", description="Verify your TBC Classic arena character.")
    @app_commands.describe(character_realm="Your character and realm: CharacterName-RealmName")
    async def verify(self, interaction: discord.Interaction, character_realm: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await _do_verify(interaction, character_realm)

    @app_commands.command(name="setup-verify",
                          description="[Admin] Post the persistent verify panel in this channel.")
    async def setup_verify(self, interaction: discord.Interaction) -> None:
        if not core.is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        embed = discord.Embed(
            title="NIGHTSLAYER ARENAS", color=COLOR_GOLD,
            description=(
                "Welcome.\n\n"
                "**1.** Verify your character.\n"
                "**2.** Receive your PvP roles.\n"
                "**3.** Queue whenever I'm looking for partners."),
        )
        await interaction.channel.send(embed=embed, view=VerifyPanel())
        await interaction.response.send_message("✅ Panel posted.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Verification(bot))

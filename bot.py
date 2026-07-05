"""
WoW Arena Discord Bot \u2014 TBC Anniversary Classic
Season 2 \u2192 3  |  discord.py 2.x  |  July 2026

Commands:
  /verify <character-realm>    \u2014 gate + role assignment
  /lfg                         \u2014 post LFG queue card
  /session start               \u2014 create temp voice channel
  /session end <wins> <losses> \u2014 post session summary
  /re-verify <member>          \u2014 admin: force re-verify
  /purge-unverified            \u2014 admin: kick all unverified
  /grant-bis <member>          \u2014 admin: grant BIS role
  /revoke-bis <member>         \u2014 admin: revoke BIS role
"""
import asyncio
import logging
import time
import discord
from discord import app_commands, ui
from discord.ext import commands

import config
import db
import ironforge
import blizzard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

# \u2500\u2500 Bot setup \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
guild_obj = discord.Object(id=config.GUILD_ID)


# \u2500\u2500 Helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def _get_guild(interaction: discord.Interaction) -> discord.Guild:
    return interaction.guild


def _is_admin(member: discord.Member) -> bool:
    """True if member has the configured admin role OR manage_guild permission."""
    return (
        any(r.name == config.ROLE_ADMIN for r in member.roles)
        or member.guild_permissions.manage_guild
    )


async def _clear_pvp_roles(member: discord.Member):
    """Remove all PvP roles from a member before assigning the correct one."""
    to_remove = [r for r in member.roles if r.name in config.ALL_PVP_ROLES]
    if to_remove:
        await member.remove_roles(*to_remove, reason="PvP role reassignment")


async def _assign_role(member: discord.Member, role_name: str):
    guild = member.guild
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        log.warning("Role '%s' not found in guild \u2014 create it first", role_name)
        return
    await _clear_pvp_roles(member)
    await member.add_roles(role, reason=f"Arena verify: {role_name}")


def _parse_character_realm(arg: str) -> tuple[str, str] | None:
    """Parse 'CharacterName-RealmName' \u2192 (character, realm). Returns None on bad format."""
    if "-" not in arg:
        return None
    idx = arg.index("-")
    character = arg[:idx].strip()
    realm = arg[idx + 1:].strip()
    if not character or not realm:
        return None
    return character, realm


async def _run_verify(discord_id: str, character: str, server: str,
                      region: str = config.REGION) -> dict:
    """
    Core verify logic. Returns:
      { found: bool, rating: int, role: str, spec: str|None, class_: str|None,
        faction: str|None, source: str, error: str|None }
    """
    # 1. Search Ironforge cache
    entry = ironforge.lookup_character(character, server, region)
    if entry:
        rating  = entry["rating"]
        spec    = entry.get("spec")
        class_  = entry.get("class")
        faction = entry.get("faction")
        bracket = config.BRACKET_NAMES.get(entry.get("bracket_num"), None)
        role    = ironforge.determine_role(rating, region)
        await db.upsert_user(discord_id, character, server, region,
                             rating, bracket, spec, class_, faction, "ironforge")
        return dict(found=True, rating=rating, role=role, spec=spec,
                    class_=class_, faction=faction, bracket=bracket, source="ironforge", error=None)

    # 2. Fallback: Blizzard Classic API
    exists = await blizzard.character_exists(character, server)
    if exists:
        await db.upsert_user(discord_id, character, server, region,
                             0, None, None, None, None, "blizzard_fallback")
        return dict(found=True, rating=0, role=config.ROLE_UNRANKED, spec=None,
                    class_=None, faction=None, bracket=None, source="blizzard_fallback", error=None)

    return dict(found=False, rating=0, role=None, spec=None,
                class_=None, faction=None, bracket=None, source=None,
                error="Character not found on Ironforge ladder or Blizzard API. "
                      "Check spelling and realm name.")


# \u2500\u2500 /verify \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@bot.tree.command(name="verify", description="Verify your TBC Classic arena character.",
                  guild=guild_obj)
@app_commands.describe(character_realm='Your character and realm: CharacterName-RealmName')
async def cmd_verify(interaction: discord.Interaction, character_realm: str):
    await interaction.response.defer(ephemeral=True)

    parsed = _parse_character_realm(character_realm)
    if not parsed:
        await interaction.followup.send(
            "\u274C Format: `/verify CharacterName-RealmName`  e.g. `/verify Brutus-Whitemane`",
            ephemeral=True
        )
        return

    character, realm = parsed
    result = await _run_verify(str(interaction.user.id), character, realm)

    if not result["found"]:
        await interaction.followup.send(f"\u274C {result['error']}", ephemeral=True)
        return

    await _assign_role(interaction.user, result["role"])

    rating_str = f"{result['rating']:,}" if result["rating"] else "Unranked"
    spec_str   = f"{result['spec']} {result['class_']}" if result["spec"] else "Unknown spec"
    bracket_str = f" ({result['bracket']})" if result.get("bracket") else ""

    embed = discord.Embed(
        title="\u2705 Verified",
        color=discord.Color.green() if result["rating"] >= 1800 else discord.Color.blue(),
    )
    embed.add_field(name="Character", value=f"{character}-{realm}", inline=True)
    embed.add_field(name="Rating",    value=f"{rating_str}{bracket_str}", inline=True)
    embed.add_field(name="Spec",      value=spec_str, inline=True)
    embed.add_field(name="Role",      value=result["role"], inline=True)
    if result["source"] == "blizzard_fallback":
        embed.set_footer(text="Not on Ironforge ladder \u2014 assigned Unranked. Play ranked games and re-verify.")

    await interaction.followup.send(embed=embed, ephemeral=True)
    log.info("Verified %s \u2192 %s (%s) rating=%s", interaction.user, character, realm, result["rating"])


# \u2500\u2500 /re-verify (admin) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@bot.tree.command(name="re-verify", description="[Admin] Force re-verify a member.",
                  guild=guild_obj)
@app_commands.describe(member="The member to re-verify")
async def cmd_reverify(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not _is_admin(interaction.user):
        await interaction.followup.send("\u274C You need the Officer role to use this.", ephemeral=True)
        return
    user_data = await db.get_user(str(member.id))
    if not user_data:
        await interaction.followup.send(f"\u274C {member.mention} has no verified character on record.", ephemeral=True)
        return

    result = await _run_verify(str(member.id), user_data["character"], user_data["server"])
    if not result["found"]:
        await interaction.followup.send(f"\u274C Character lookup failed: {result['error']}", ephemeral=True)
        return

    await _assign_role(member, result["role"])
    await interaction.followup.send(
        f"\u2705 {member.mention} re-verified \u2192 **{result['role']}** ({result['rating']:,})", ephemeral=True
    )


# \u2500\u2500 /purge-unverified (admin) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@bot.tree.command(name="purge-unverified",
                  description="[Admin] Kick all members with no PvP role.",
                  guild=guild_obj)
async def cmd_purge(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not _is_admin(interaction.user):
        await interaction.followup.send("\u274C You need the Officer role to use this.", ephemeral=True)
        return
    guild = _get_guild(interaction)
    kicked = 0
    for member in guild.members:
        if member.bot:
            continue
        has_pvp_role = any(r.name in config.ALL_PVP_ROLES for r in member.roles)
        if not has_pvp_role:
            try:
                await member.kick(reason="Purge: no PvP role")
                kicked += 1
            except discord.Forbidden:
                pass
    await interaction.followup.send(f"\u2705 Kicked {kicked} unverified member(s).", ephemeral=True)


# \u2500\u2500 BIS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@bot.tree.command(name="grant-bis", description="[Admin] Grant BIS role to a member.",
                  guild=guild_obj)
@app_commands.describe(member="Member to grant BIS")
async def cmd_grant_bis(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not _is_admin(interaction.user):
        await interaction.followup.send("\u274C You need the Officer role to use this.", ephemeral=True)
        return
    guild = _get_guild(interaction)
    bis_role = discord.utils.get(guild.roles, name=config.ROLE_BIS)
    if bis_role is None:
        await interaction.followup.send(
            f"\u274C Role `{config.ROLE_BIS}` not found \u2014 create it in your server first.", ephemeral=True
        )
        return
    if bis_role in member.roles:
        await interaction.followup.send(f"\u2139\uFE0F {member.mention} already has BIS.", ephemeral=True)
        return
    await member.add_roles(bis_role, reason=f"BIS granted by {interaction.user}")
    await interaction.followup.send(f"\u2705 BIS granted to {member.mention}.", ephemeral=True)
    log.info("BIS granted to %s by %s", member, interaction.user)


@bot.tree.command(name="revoke-bis", description="[Admin] Revoke BIS role from a member.",
                  guild=guild_obj)
@app_commands.describe(member="Member to revoke BIS from")
async def cmd_revoke_bis(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not _is_admin(interaction.user):
        await interaction.followup.send("\u274C You need the Officer role to use this.", ephemeral=True)
        return
    guild = _get_guild(interaction)
    bis_role = discord.utils.get(guild.roles, name=config.ROLE_BIS)
    if bis_role is None or bis_role not in member.roles:
        await interaction.followup.send(f"\u2139\uFE0F {member.mention} doesn't have BIS.", ephemeral=True)
        return
    await member.remove_roles(bis_role, reason=f"BIS revoked by {interaction.user}")
    await interaction.followup.send(f"\u2705 BIS revoked from {member.mention}.", ephemeral=True)
    log.info("BIS revoked from %s by %s", member, interaction.user)


# \u2500\u2500 LFG \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

BRACKET_CHOICES = [
    app_commands.Choice(name="2v2", value="2v2"),
    app_commands.Choice(name="3v3", value="3v3"),
    app_commands.Choice(name="5v5", value="5v5"),
]

SPEC_CHOICES = [
    # Druid
    app_commands.Choice(name="Balance",       value="Balance"),
    app_commands.Choice(name="Feral Combat",  value="Feral Combat"),
    app_commands.Choice(name="Restoration",   value="Restoration"),
    # Hunter
    app_commands.Choice(name="Beast Mastery", value="Beast Mastery"),
    app_commands.Choice(name="Marksmanship",  value="Marksmanship"),
    app_commands.Choice(name="Survival",      value="Survival"),
    # Mage
    app_commands.Choice(name="Arcane",        value="Arcane"),
    app_commands.Choice(name="Fire",          value="Fire"),
    app_commands.Choice(name="Frost",         value="Frost"),
    # Paladin
    app_commands.Choice(name="Holy",          value="Holy"),
    app_commands.Choice(name="Retribution",   value="Retribution"),
    app_commands.Choice(name="Protection",    value="Protection"),
    # Priest
    app_commands.Choice(name="Discipline",    value="Discipline"),
    app_commands.Choice(name="Shadow",        value="Shadow"),
    # Rogue
    app_commands.Choice(name="Assassination", value="Assassination"),
    app_commands.Choice(name="Combat",        value="Combat"),
    app_commands.Choice(name="Subtlety",      value="Subtlety"),
    # Shaman
    app_commands.Choice(name="Elemental",     value="Elemental"),
    app_commands.Choice(name="Enhancement",   value="Enhancement"),
    # Warlock
    app_commands.Choice(name="Affliction",    value="Affliction"),
    app_commands.Choice(name="Demonology",    value="Demonology"),
    app_commands.Choice(name="Destruction",   value="Destruction"),
    # Warrior
    app_commands.Choice(name="Arms",          value="Arms"),
    app_commands.Choice(name="Fury",          value="Fury"),
]


def _lfg_embed(member: discord.Member, bracket: str, spec: str, rating: int,
               target_min: int, target_max: int, active: bool = True) -> discord.Embed:
    color = discord.Color.green() if active else discord.Color.dark_gray()
    embed = discord.Embed(
        title=f"{'\U0001F7E2' if active else '\u231B'} LFG \u2014 {bracket}",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.add_field(name="Spec",         value=spec,                     inline=True)
    embed.add_field(name="Rating",       value=f"{rating:,}",            inline=True)
    embed.add_field(name="Target Range", value=f"{target_min}\u2013{target_max}", inline=True)
    if not active:
        embed.set_footer(text="\u231B Expired")
    return embed


class LFGJoinView(ui.View):
    def __init__(self, entry_id: int, poster_id: int, bracket: str):
        super().__init__(timeout=None)  # persistence handled by expiry job
        self.entry_id  = entry_id
        self.poster_id = poster_id
        self.bracket   = bracket

    @ui.button(label="Join Queue", style=discord.ButtonStyle.green, custom_id="lfg_join")
    async def join(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id == self.poster_id:
            await interaction.response.send_message("You can't join your own queue.", ephemeral=True)
            return

        # Get both users' data
        joiner_data = await db.get_user(str(interaction.user.id))
        poster_data = await db.get_user(str(self.poster_id))
        if not joiner_data:
            await interaction.response.send_message(
                "You need to `/verify` first before joining a queue.", ephemeral=True
            )
            return

        # Check rating range compatibility
        active = await db.get_active_queue(self.bracket)
        poster_entry = next((e for e in active if e["id"] == self.entry_id), None)
        if not poster_entry:
            await interaction.response.send_message("This queue entry has expired.", ephemeral=True)
            return

        joiner_rating = joiner_data.get("highest_rating", 0)
        if not (poster_entry["target_min"] <= joiner_rating <= poster_entry["target_max"]):
            await interaction.response.send_message(
                f"Your rating ({joiner_rating:,}) is outside this queue's target range "
                f"({poster_entry['target_min']}\u2013{poster_entry['target_max']}).",
                ephemeral=True
            )
            return

        # DM both parties
        poster = interaction.guild.get_member(self.poster_id)
        joiner = interaction.user
        char_p = f"{poster_data['character']}-{poster_data['server']}" if poster_data else "Unknown"
        char_j = f"{joiner_data['character']}-{joiner_data['server']}"
        try:
            await poster.send(
                f"\U0001F3AF **LFG Match \u2014 {self.bracket}**\n"
                f"{joiner.display_name} ({char_j}) wants to queue with you!\n"
                f"Spec: {joiner_data.get('spec','?')} | Rating: {joiner_rating:,}"
            )
        except discord.Forbidden:
            pass
        try:
            await joiner.send(
                f"\U0001F3AF **LFG Match \u2014 {self.bracket}**\n"
                f"{poster.display_name} ({char_p}) is your match!\n"
                f"Spec: {poster_entry['spec']} | Rating: {poster_entry['rating']:,}"
            )
        except discord.Forbidden:
            pass

        await interaction.response.send_message(
            f"\u2705 Match found! Both players have been DM'd. Good luck! \U0001F3C6", ephemeral=True
        )
        await db.deactivate_queue_entry(self.entry_id)


@bot.tree.command(name="lfg", description="Post a Looking for Group card.", guild=guild_obj)
@app_commands.describe(
    bracket="Arena bracket",
    spec="Your spec",
    rating="Your current rating",
    target_min="Minimum rating you're looking for",
    target_max="Maximum rating you're looking for",
)
@app_commands.choices(bracket=BRACKET_CHOICES, spec=SPEC_CHOICES)
async def cmd_lfg(interaction: discord.Interaction,
                  bracket: app_commands.Choice[str],
                  spec: app_commands.Choice[str],
                  rating: int,
                  target_min: int,
                  target_max: int):
    await interaction.response.defer(ephemeral=True)

    user_data = await db.get_user(str(interaction.user.id))
    if not user_data:
        await interaction.followup.send("\u274C You must `/verify` before using LFG.", ephemeral=True)
        return

    guild  = _get_guild(interaction)
    ch_name = config.LFG_CHANNELS.get(bracket.value)
    channel = discord.utils.get(guild.text_channels, name=ch_name)
    if channel is None:
        await interaction.followup.send(f"\u274C Channel `#{ch_name}` not found.", ephemeral=True)
        return

    role_val   = config.spec_role(spec.value)
    class_val  = user_data.get("class", "Unknown")
    embed      = _lfg_embed(interaction.user, bracket.value, spec.value, rating, target_min, target_max)

    # Placeholder message \u2014 get ID before inserting to DB
    msg = await channel.send(embed=embed)

    entry_id = await db.add_queue_entry(
        discord_id=str(interaction.user.id),
        bracket=bracket.value,
        spec=spec.value,
        class_=class_val,
        role=role_val,
        rating=rating,
        target_min=target_min,
        target_max=target_max,
        message_id=str(msg.id),
        channel_id=str(channel.id),
    )

    view = LFGJoinView(entry_id, interaction.user.id, bracket.value)
    await msg.edit(view=view)
    await interaction.followup.send(f"\u2705 Queue card posted in {channel.mention}.", ephemeral=True)


# \u2500\u2500 /session \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@bot.tree.command(name="session-start", description="Start an arena session and create a voice channel.",
                  guild=guild_obj)
@app_commands.describe(bracket="Bracket", comp="Comp name (e.g. 'Warlock-Resto Druid')")
@app_commands.choices(bracket=BRACKET_CHOICES)
async def cmd_session_start(interaction: discord.Interaction,
                             bracket: app_commands.Choice[str],
                             comp: str):
    await interaction.response.defer(ephemeral=True)
    guild = _get_guild(interaction)

    # Create temporary voice channel
    channel_name = f"\U0001F525 {comp} {bracket.value}"
    category = discord.utils.get(guild.categories, name="Arena Sessions")
    vc = await guild.create_voice_channel(channel_name, category=category)

    session_id = await db.start_session(
        str(interaction.user.id), bracket.value, comp, str(vc.id)
    )
    await interaction.followup.send(
        f"\u2705 Session started! Voice: {vc.mention}\nSession ID: `{session_id}`",
        ephemeral=True
    )


@bot.tree.command(name="session-end", description="End arena session and post summary.",
                  guild=guild_obj)
@app_commands.describe(wins="Wins this session", losses="Losses this session")
async def cmd_session_end(interaction: discord.Interaction, wins: int, losses: int):
    await interaction.response.defer(ephemeral=True)
    session = await db.get_active_session(str(interaction.user.id))
    if not session:
        await interaction.followup.send("\u274C No active session found.", ephemeral=True)
        return

    await db.end_session(session["id"], wins, losses)

    # Delete the voice channel if it still exists
    guild = _get_guild(interaction)
    vc = guild.get_channel(int(session["channel_id"]))
    if vc:
        try:
            await vc.delete(reason="Session ended")
        except discord.NotFound:
            pass

    total   = wins + losses
    wr      = round(wins / total * 100) if total else 0
    elapsed = int(time.time()) - session["started_at"]
    mins    = elapsed // 60

    embed = discord.Embed(title="\U0001F4CA Session Summary", color=discord.Color.gold())
    embed.add_field(name="Comp",    value=session["comp"],   inline=True)
    embed.add_field(name="Bracket", value=session["bracket"],inline=True)
    embed.add_field(name="Record",  value=f"{wins}W\u2013{losses}L ({wr}%)", inline=True)
    embed.add_field(name="Duration",value=f"{mins} min",    inline=True)
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

    await interaction.followup.send(embed=embed)


# \u2500\u2500 Events \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@bot.event
async def on_member_join(member: discord.Member):
    """Auto-kick if not verified within 24 hours."""
    await asyncio.sleep(config.VERIFY_TIMEOUT_HOURS * 3600)
    # Re-fetch member in case they left already
    guild = bot.get_guild(config.GUILD_ID)
    fresh = guild.get_member(member.id) if guild else None
    if fresh is None:
        return
    has_pvp_role = any(r.name in config.ALL_PVP_ROLES for r in fresh.roles)
    if not has_pvp_role:
        try:
            await fresh.kick(reason=f"Did not verify within {config.VERIFY_TIMEOUT_HOURS}h")
            log.info("Auto-kicked %s \u2014 no verify", fresh)
        except discord.Forbidden:
            log.warning("Couldn't kick %s \u2014 check bot permissions", fresh)


@bot.event
async def on_ready():
    log.info("Bot ready: %s (%s)", bot.user, bot.user.id)
    await db.init_db()
    await ironforge.refresh_all()

    # Sync slash commands to guild
    bot.tree.copy_global_to(guild=guild_obj)
    synced = await bot.tree.sync(guild=guild_obj)
    log.info("Synced %d slash commands", len(synced))

    # Start background jobs
    from scheduler import start_scheduler
    start_scheduler(bot)
    log.info("Scheduler started")


# \u2500\u2500 Error handlers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@cmd_verify.error
@cmd_purge.error
@cmd_reverify.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("\u274C You don't have permission to use this command.", ephemeral=True)
    else:
        log.error("Command error: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message("\u274C An error occurred. Check logs.", ephemeral=True)


# \u2500\u2500 Entry point \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN not set in .env")
    if not config.GUILD_ID:
        raise RuntimeError("GUILD_ID not set in .env")
    bot.run(config.DISCORD_TOKEN, log_handler=None)

"""
Looking For Group.

/lfg posts a clean card in the bracket channel. Everything but the bracket and an
optional rating preference is pulled from the poster's verify record. Interested
players click Join Queue (a persistent DynamicItem button keyed by entry id, so it
survives restarts) and the bot DMs both players to connect.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core
import db
from config import settings, BRACKET_NAMES, COLOR_GREEN, COLOR_GOLD

log = logging.getLogger("lfg")

BRACKET_CHOICES = [
    app_commands.Choice(name="2v2", value="2v2"),
    app_commands.Choice(name="3v3", value="3v3"),
    app_commands.Choice(name="5v5", value="5v5"),
]


def _card_embed(member: discord.Member, bracket: str, rating: int, spec: Optional[str],
                class_: Optional[str], pref: Optional[tuple[int, int]]) -> discord.Embed:
    embed = discord.Embed(title=f"🟢 LFG — {bracket}", color=COLOR_GREEN,
                          timestamp=discord.utils.utcnow())
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    if spec or class_:
        embed.add_field(name="Spec", value=f"{spec or ''} {class_ or ''}".strip(), inline=True)
    embed.add_field(name="Rating", value=f"{rating:,}" if rating else "Unranked", inline=True)
    if pref:
        embed.add_field(name="Looking for", value=f"{pref[0]:,}–{pref[1]:,}", inline=True)
    embed.set_footer(text="Click Join Queue to team up.")
    return embed


class LFGJoinButton(discord.ui.DynamicItem[discord.ui.Button],
                    template=r"ns:lfg:join:(?P<entry_id>\d+)"):
    """Persistent Join button. custom_id encodes the DB entry id."""

    def __init__(self, entry_id: int) -> None:
        self.entry_id = entry_id
        super().__init__(
            discord.ui.Button(
                label="Join Queue", emoji="⚔️",
                style=discord.ButtonStyle.green,
                custom_id=f"ns:lfg:join:{entry_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match["entry_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        entry = await db.get_lfg(self.entry_id)
        if not entry or not entry["active"]:
            await interaction.followup.send("This queue has closed.", ephemeral=True)
            return

        poster_id = int(entry["discord_id"])
        if interaction.user.id == poster_id:
            await interaction.followup.send("You can't join your own queue.", ephemeral=True)
            return

        joiner = await db.get_user(str(interaction.user.id))
        if not joiner:
            await interaction.followup.send(
                "Verify first (see #verify) before joining a queue.", ephemeral=True)
            return

        if entry["pref_min"] is not None and entry["pref_max"] is not None:
            jr = joiner.get("rating", 0)
            if not (entry["pref_min"] <= jr <= entry["pref_max"]):
                await interaction.followup.send(
                    f"Your rating ({jr:,}) is outside the requested range "
                    f"({entry['pref_min']:,}–{entry['pref_max']:,}).", ephemeral=True)
                return

        poster_member = interaction.guild.get_member(poster_id)
        poster_data = await db.get_user(str(poster_id))
        await db.deactivate_lfg(self.entry_id)

        # DM both parties.
        j_char = f"{joiner['character']}-{joiner['realm']}"
        p_char = (f"{poster_data['character']}-{poster_data['realm']}"
                  if poster_data else "your partner")
        if poster_member:
            await _safe_dm(poster_member,
                           f"🎯 **LFG Match — {entry['bracket']}**\n"
                           f"{interaction.user.display_name} ({j_char}) joined your queue.\n"
                           f"Spec: {joiner.get('spec') or '?'} • Rating: {joiner.get('rating', 0):,}")
        await _safe_dm(interaction.user,
                       f"🎯 **LFG Match — {entry['bracket']}**\n"
                       f"You're matched with {p_char}. Good luck! 🏆")

        # Retire the card visually.
        try:
            msg = await interaction.channel.fetch_message(int(entry["message_id"]))
            done = discord.Embed(title=f"⚔️ Matched — {entry['bracket']}", color=COLOR_GOLD)
            done.set_footer(text="Queue closed.")
            await msg.edit(embed=done, view=None)
        except discord.HTTPException:
            pass

        await interaction.followup.send(
            "✅ Match found! Both players have been DM'd — hop into an arena voice channel.",
            ephemeral=True)


async def _safe_dm(member: discord.Member, content: str) -> None:
    try:
        await member.send(content)
    except discord.Forbidden:
        pass


class LFG(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.expire_cards.start()

    async def cog_unload(self) -> None:
        self.expire_cards.cancel()

    @app_commands.command(name="lfg", description="Post a Looking For Group card.")
    @app_commands.describe(
        bracket="Arena bracket",
        pref_min="Optional: minimum partner rating",
        pref_max="Optional: maximum partner rating",
    )
    @app_commands.choices(bracket=BRACKET_CHOICES)
    async def lfg(self, interaction: discord.Interaction,
                  bracket: app_commands.Choice[str],
                  pref_min: Optional[int] = None,
                  pref_max: Optional[int] = None) -> None:
        await interaction.response.defer(ephemeral=True)

        data = await db.get_user(str(interaction.user.id))
        if not data:
            await interaction.followup.send("❌ Verify first before using LFG.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(settings.lfg_channel_ids.get(bracket.value, 0))
        if channel is None:
            await interaction.followup.send(
                f"❌ No channel configured for {bracket.value}. "
                "Set CHANNEL_{}_ID.".format(bracket.value.upper()), ephemeral=True)
            return

        pref = (pref_min, pref_max) if pref_min is not None and pref_max is not None else None
        embed = _card_embed(interaction.user, bracket.value, data.get("rating", 0),
                            data.get("spec"), data.get("class"), pref)

        msg = await channel.send(embed=embed)
        entry_id = await db.add_lfg(
            discord_id=str(interaction.user.id), bracket=bracket.value,
            rating=data.get("rating", 0), pref_min=pref_min, pref_max=pref_max,
            message_id=str(msg.id), channel_id=str(channel.id),
            expiry_minutes=settings.queue_expiry_minutes,
        )
        view = discord.ui.View(timeout=None)
        view.add_item(LFGJoinButton(entry_id))
        await msg.edit(view=view)
        await interaction.followup.send(f"✅ Posted in {channel.mention}.", ephemeral=True)

    @tasks.loop(minutes=2)
    async def expire_cards(self) -> None:
        expired = await db.take_expired_lfg()
        if not expired:
            return
        guild = self.bot.get_guild(settings.guild_id)
        if guild is None:
            return
        for entry in expired:
            try:
                channel = guild.get_channel(int(entry["channel_id"]))
                if channel is None:
                    continue
                msg = await channel.fetch_message(int(entry["message_id"]))
                embed = discord.Embed(title=f"⌛ LFG — {entry['bracket']} (expired)",
                                      color=discord.Color.dark_gray())
                await msg.edit(embed=embed, view=None)
            except discord.HTTPException as exc:
                log.warning("Couldn't expire LFG %s: %s", entry["id"], exc)

    @expire_cards.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LFG(bot))

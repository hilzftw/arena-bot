"""
Background jobs via APScheduler.
  - Refresh Ironforge ladder cache every 60 min
  - Post daily leaderboard at 12:00 UTC
  - Expire LFG queue entries every 2 min
"""
import logging
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import db
import ironforge

log = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler(timezone="UTC")


def start_scheduler(bot: discord.ext.commands.Bot):
    scheduler.add_job(
        _refresh_cache, "interval",
        minutes=config.CACHE_REFRESH_MINUTES,
        id="cache_refresh", replace_existing=True,
    )
    scheduler.add_job(
        lambda: _post_leaderboard(bot), "cron",
        hour=12, minute=0,
        id="daily_leaderboard", replace_existing=True,
    )
    scheduler.add_job(
        lambda: _expire_lfg(bot), "interval",
        minutes=2,
        id="lfg_expiry", replace_existing=True,
    )
    scheduler.start()


async def _refresh_cache():
    log.info("Refreshing Ironforge ladder cache...")
    await ironforge.refresh_all()


async def _post_leaderboard(bot: discord.ext.commands.Bot):
    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        return

    # Post in #announcements
    channel = discord.utils.get(guild.text_channels, name="announcements")
    if channel is None:
        log.warning("No #announcements channel found for leaderboard post")
        return

    members = await db.get_all_verified()
    known   = {(m["character"].lower(), m["server"].lower()): m for m in members}
    all_entries = ironforge.get_all_ladder_entries()

    # Cross-reference: find ladder entries that match Discord members
    hits = []
    for entry in all_entries:
        key = (entry["name"].lower(), entry["server"].lower())
        if key in known:
            member_data = known[key]
            discord_member = guild.get_member(int(member_data["discord_id"]))
            hits.append({
                **entry,
                "discord_mention": discord_member.mention if discord_member else entry["name"],
            })

    if not hits:
        return

    # Deduplicate â keep highest rating per Discord member
    seen: dict[str, dict] = {}
    for h in hits:
        key = h["discord_mention"]
        if key not in seen or h["rating"] > seen[key]["rating"]:
            seen[key] = h
    top = sorted(seen.values(), key=lambda x: x["rating"], reverse=True)[:10]

    embed = discord.Embed(
        title="ð Server Arena Leaderboard",
        description=f"Season {config.CURRENT_SEASON} | Top rated members",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    for i, e in enumerate(top, 1):
        bracket_name = config.BRACKET_NAMES.get(e.get("bracket_num"), "?")
        wr = e.get("stats", {}).get("wr", "?")
        embed.add_field(
            name=f"#{i}  {e['name']}-{e['server']}",
            value=(
                f"{e['discord_mention']} â **{e['rating']:,}** ({bracket_name})\n"
                f"{e.get('spec','')} {e.get('class','')} | {wr}% WR"
            ),
            inline=False,
        )

    await channel.send(embed=embed)
    log.info("Daily leaderboard posted (%d members)", len(top))


async def _expire_lfg(bot: discord.ext.commands.Bot):
    """Edit expired LFG embeds to show â Expired."""
    expired = await db.expire_queue_entries()
    if not expired:
        return
    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        return
    for entry in expired:
        try:
            channel = guild.get_channel(int(entry["channel_id"]))
            if channel is None:
                continue
            msg = await channel.fetch_message(int(entry["message_id"]))
            embed = discord.Embed(
                title=f"â LFG â {entry['bracket']} (Expired)",
                color=discord.Color.dark_gray(),
            )
            embed.add_field(name="Spec",   value=entry["spec"],        inline=True)
            embed.add_field(name="Rating", value=f"{entry['rating']:,}", inline=True)
            await msg.edit(embed=embed, view=None)
        except Exception as exc:
            log.warning("Couldn't expire LFG entry %s: %s", entry["id"], exc)

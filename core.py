"""
Shared domain logic: verification, role resolution, and permission checks.

Kept separate from the cogs so both verification and moderation can reuse it
without circular imports.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import discord

import blizzard
import db
import ironforge
from config import settings, RATING_ROLE_KEYS

log = logging.getLogger("core")

# WoW class colors for auto-created cosmetic roles.
CLASS_COLORS: dict[str, int] = {
    "Druid": 0xFF7D0A, "Hunter": 0xABD473, "Mage": 0x69CCF0, "Paladin": 0xF58CBA,
    "Priest": 0xFFFFFF, "Rogue": 0xFFF569, "Shaman": 0x0070DE, "Warlock": 0x9482C9,
    "Warrior": 0xC79C6E,
}


@dataclass
class VerifyResult:
    found: bool
    rating: int = 0
    role_key: Optional[str] = None
    spec: Optional[str] = None
    class_: Optional[str] = None
    faction: Optional[str] = None
    bracket: Optional[str] = None
    source: Optional[str] = None
    error: Optional[str] = None


def parse_character_realm(arg: str) -> Optional[tuple[str, str]]:
    """'Name-Realm' -> ('Name', 'Realm'); None if malformed."""
    if "-" not in arg:
        return None
    name, _, realm = arg.partition("-")
    name, realm = name.strip(), realm.strip()
    return (name, realm) if name and realm else None


# ── Role resolution ────────────────────────────────────────────────────────────

def resolve_role(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    return guild.get_role(role_id) if role_id else None


async def ensure_named_role(guild: discord.Guild, name: str,
                            color: Optional[int] = None) -> Optional[discord.Role]:
    """Find a role by name; create it if missing and allowed. Cosmetic only."""
    role = discord.utils.get(guild.roles, name=name)
    if role:
        return role
    if not settings.manage_class_spec_roles:
        return None
    try:
        return await guild.create_role(
            name=name,
            color=discord.Color(color) if color else discord.Color.default(),
            reason="Auto-created cosmetic PvP role",
        )
    except discord.Forbidden:
        log.warning("Missing permission to create role %r", name)
        return None


async def _clear_rating_roles(member: discord.Member) -> None:
    rating_ids = {rid for rid in settings.rating_role_ids.values() if rid}
    to_remove = [r for r in member.roles if r.id in rating_ids]
    if to_remove:
        await member.remove_roles(*to_remove, reason="Rating role reassignment")


async def apply_verify_roles(member: discord.Member, result: VerifyResult) -> None:
    """Assign Guest + rating + (optional) class/spec roles from a verify result."""
    guild = member.guild
    add: list[discord.Role] = []

    guest = resolve_role(guild, settings.guest_role_id)
    friend = resolve_role(guild, settings.friend_role_id)
    # Only grant Guest access if they aren't already a permanent Friend.
    if guest and (friend is None or friend not in member.roles):
        add.append(guest)

    if result.role_key:
        await _clear_rating_roles(member)
        rr = resolve_role(guild, settings.rating_role_ids.get(result.role_key, 0))
        if rr:
            add.append(rr)

    if settings.manage_class_spec_roles:
        if result.class_:
            cr = await ensure_named_role(guild, result.class_,
                                         CLASS_COLORS.get(result.class_))
            if cr:
                add.append(cr)
        if result.spec:
            sr = await ensure_named_role(guild, result.spec)
            if sr:
                add.append(sr)

    add = [r for r in add if r not in member.roles]
    if add:
        await member.add_roles(*add, reason="Arena verification")


# ── Verification ───────────────────────────────────────────────────────────────

async def run_verify(discord_id: str, character: str, realm: str,
                     expires_at: Optional[int]) -> VerifyResult:
    """Look up a character (Ironforge, then Blizzard), persist, return result.

    expires_at: unix ts for a new guest, None for Friend, or -1 to keep existing.
    """
    region = settings.region
    entry = ironforge.lookup_character(character, realm, region)
    if entry:
        rating = int(entry.get("rating", 0))
        spec = entry.get("spec")
        class_ = entry.get("class")
        faction = entry.get("faction")
        role_key = ironforge.determine_role_key(rating, region)
        await db.upsert_user(discord_id, character, realm, region, faction, class_,
                             spec, rating, role_key, "ironforge", expires_at)
        return VerifyResult(True, rating, role_key, spec, class_, faction,
                            source="ironforge")

    if await blizzard.character_exists(character, realm):
        await db.upsert_user(discord_id, character, realm, region, None, None,
                             None, 0, "Unranked", "blizzard_fallback", expires_at)
        return VerifyResult(True, 0, "Unranked", source="blizzard_fallback")

    return VerifyResult(
        False,
        error=("Character not found on the Ironforge ladder or Blizzard API. "
               "Check the spelling and realm name."),
    )


# ── Permissions ────────────────────────────────────────────────────────────────

def is_staff(member: discord.Member) -> bool:
    """Owner, configured Mod/Admin role, or manage_guild permission."""
    if settings.owner_id and member.id == settings.owner_id:
        return True
    staff_ids = {settings.mod_role_id, settings.admin_role_id}
    if any(r.id in staff_ids for r in member.roles if r.id):
        return True
    return member.guild_permissions.manage_guild


def guest_expiry_ts() -> int:
    return int(time.time()) + settings.guest_expiration_days * 86400

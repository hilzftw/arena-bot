"""
Configuration for the Nightslayer Arenas bot.

Everything is loaded from environment variables — no IDs are hardcoded.
Values are validated once at startup via Settings.load(); a missing required
value raises immediately so the bot fails fast instead of misbehaving live.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int | None = None, *, required: bool = False) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        if required:
            raise RuntimeError(f"Missing required env var: {name}")
        return default if default is not None else 0
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Env var {name}={raw!r} is not an integer") from exc


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


# ── Rating role keys (internal names). Map to Discord role IDs via env. ─────────
RATING_ROLE_KEYS = (
    "Unranked",
    "1400+",
    "1800+",
    "2100+",
    "Gladiator",
    "Merciless Gladiator",
)

# Healer specs → used to tag a role preference on LFG cards.
HEALER_SPECS = frozenset({"Holy", "Discipline", "Restoration"})

BRACKETS = (2, 3, 5)
BRACKET_NAMES = {2: "2v2", 3: "3v3", 5: "5v5"}
BRACKET_SIZE = {"2v2": 2, "3v3": 3, "5v5": 5}

# WoW-flavored embed colors.
COLOR_GOLD = 0xC79C6E      # warrior tan / neutral header
COLOR_GREEN = 0x1EFF00     # "uncommon" green — success
COLOR_BLUE = 0x0070DD      # "rare" blue — info
COLOR_PURPLE = 0xA335EE    # "epic" purple — high rating
COLOR_RED = 0xC41E3A       # "death knight" red — errors / removal


@dataclass(frozen=True)
class Settings:
    # Discord
    token: str
    guild_id: int
    owner_id: int

    # Structural roles
    guest_role_id: int
    friend_role_id: int
    mod_role_id: int
    admin_role_id: int

    # Rating roles: key -> role id (0 = not configured, skip assignment)
    rating_role_ids: dict[str, int]

    # Channels
    verify_channel_id: int
    lfg_channel_ids: dict[str, int]        # "2v2" -> channel id
    voice_category_id: int

    # Class/spec cosmetic roles resolved by name (auto-created if enabled)
    manage_class_spec_roles: bool

    # Behavior
    guest_expiration_days: int
    verify_timeout_hours: int
    queue_expiry_minutes: int
    cache_refresh_minutes: int
    # When False (default), the bot NEVER kicks members who simply never verified.
    # Only turn this on after the verify gate is established, or it will remove
    # existing members who predate the bot. Expired-guest cleanup is unaffected.
    enforce_verification: bool

    # Ironforge
    ironforge_base: str
    current_season: int
    region: str

    # Blizzard fallback
    blizzard_client_id: str
    blizzard_client_secret: str

    # Storage
    db_path: str

    @classmethod
    def load(cls) -> "Settings":
        region = _str("REGION", "US").upper()
        rating_role_ids = {
            "Unranked": _int("ROLE_UNRANKED_ID"),
            "1400+": _int("ROLE_1400_ID"),
            "1800+": _int("ROLE_1800_ID"),
            "2100+": _int("ROLE_2100_ID"),
            "Gladiator": _int("ROLE_GLADIATOR_ID"),
            "Merciless Gladiator": _int("ROLE_MERCILESS_ID"),
        }
        lfg_channel_ids = {
            "2v2": _int("CHANNEL_2V2_ID"),
            "3v3": _int("CHANNEL_3V3_ID"),
            "5v5": _int("CHANNEL_5V5_ID"),
        }
        return cls(
            token=_str("DISCORD_TOKEN"),
            guild_id=_int("GUILD_ID", required=True),
            owner_id=_int("OWNER_ID"),
            guest_role_id=_int("GUEST_ROLE_ID", required=True),
            friend_role_id=_int("FRIEND_ROLE_ID", required=True),
            mod_role_id=_int("MOD_ROLE_ID"),
            admin_role_id=_int("ADMIN_ROLE_ID"),
            rating_role_ids=rating_role_ids,
            verify_channel_id=_int("VERIFY_CHANNEL_ID"),
            lfg_channel_ids=lfg_channel_ids,
            voice_category_id=_int("VOICE_CATEGORY_ID"),
            manage_class_spec_roles=_str("MANAGE_CLASS_SPEC_ROLES", "true").lower() == "true",
            guest_expiration_days=_int("GUEST_EXPIRATION_DAYS", 30),
            verify_timeout_hours=_int("VERIFY_TIMEOUT_HOURS", 24),
            queue_expiry_minutes=_int("QUEUE_EXPIRY_MINUTES", 30),
            cache_refresh_minutes=_int("CACHE_REFRESH_MINUTES", 60),
            enforce_verification=_str("ENFORCE_VERIFICATION", "false").lower() == "true",
            ironforge_base=_str("IRONFORGE_BASE", "https://ironforge.pro"),
            current_season=_int("CURRENT_SEASON", 2),
            region=region,
            blizzard_client_id=_str("BLIZZARD_CLIENT_ID"),
            blizzard_client_secret=_str("BLIZZARD_CLIENT_SECRET"),
            db_path=_str("DB_PATH", "bot.db"),
        )

    def validate(self) -> None:
        if not self.token:
            raise RuntimeError("DISCORD_TOKEN not set")
        if not self.guild_id:
            raise RuntimeError("GUILD_ID not set")


def spec_role_kind(spec: str) -> str:
    """LFG tag: 'healer' or 'dps'."""
    return "healer" if spec in HEALER_SPECS else "dps"


# Single shared instance imported across modules.
settings = Settings.load()

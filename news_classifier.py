"""
Lightweight keyword classifier for WoW news.

Maps an article to one of: "tbc-pvp", "tbc-pve", "retail", or None (ignore).
The server is TBC-PvP-first, so PvP wins ties and unclassified Classic news falls
into the PvP channel. Guides, tier lists, opinion, gold/farming, and fluff are
dropped entirely.
"""
from __future__ import annotations

from typing import Optional

# Anything matching these is not "news" — drop it.
IGNORE = (
    "guide", "tier list", "how to", "leveling", "gold farm", "gold making",
    "farming route", "boost", "boosting", "wallpaper", "giveaway", "sweepstake",
    "cosplay", "fan art", "meme", "opinion", "review", "top 10", "best races",
    "best professions", "transmog", "mount guide", "gold guide", "addon guide",
)

# Note: bare "anniversary" is intentionally excluded — the WoW 20th Anniversary is a
# *retail* event, so it would misroute retail news into the Classic channels.
CLASSIC = ("classic", "burning crusade", "tbc", "anniversary classic", "classic era",
           "hardcore")

# Strong RETAIL markers. If any appear, the article is retail — this overrides a stray
# "classic" mention so retail news never lands in a TBC channel.
RETAIL = (
    "the war within", "war within", "midnight", "the last titan", "dragonflight",
    "shadowlands", "delve", "mythic+", "mythic plus", "great vault", "warband",
    "hero talent", "worldsoul", "retail wow", "modern wow", "20th anniversary",
    "anniversary event", "season of discovery",
)

PVP = (
    "arena", "pvp", "honor", "battleground", " bg ", "rated", "mmr", "rating",
    "gladiator", "brutal", "vengeful", "merciless", "conquest", "arena season",
    "world pvp", "premade",
)

PVE = (
    "raid", "dungeon", "attunement", "badge", "loot table", "boss", "tuning",
    "profession", "black temple", "sunwell", "hyjal", "karazhan", "gruul",
    "magtheridon", "zul'aman", "heroic", "tier set", "class set",
)

# General Classic news that isn't clearly PvP/PvE still matters (maintenance,
# blue posts, realm status, hotfixes, seasons) — route to the PvP channel.
CLASSIC_GENERAL = (
    "hotfix", "maintenance", "realm", "blue post", "ptr", "patch", "season",
    "outage", "server", "scheduled",
)

# Retail is a small side feed — only genuinely major items.
RETAIL_MAJOR = (
    "expansion", "the war within", "midnight", "the last titan", "major patch",
    "season launch", "class tuning", "raid opens", "expansion announcement",
)


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def classify(title: str, summary: str = "") -> Optional[str]:
    text = f" {title} {summary} ".lower()

    if _has(text, IGNORE):
        return None

    # Strong retail markers win first — keeps retail out of the TBC channels.
    if _has(text, RETAIL):
        return "retail"

    if _has(text, CLASSIC):
        if _has(text, PVP):
            return "tbc-pvp"
        if _has(text, PVE):
            return "tbc-pve"
        if _has(text, CLASSIC_GENERAL):
            return "tbc-pvp"        # PvP-first default for general Classic news
        return None

    # Not clearly Classic → only surface genuinely major retail news.
    if _has(text, RETAIL_MAJOR):
        return "retail"
    return None

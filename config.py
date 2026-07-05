import os
from dotenv import load_dotenv

load_dotenv()

# ── Discord ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN        = os.getenv("DISCORD_TOKEN")
GUILD_ID             = int(os.getenv("GUILD_ID", "0"))

# ── Blizzard (fallback only) ───────────────────────────────────────────────────
BLIZZARD_CLIENT_ID     = os.getenv("BLIZZARD_CLIENT_ID", "")
BLIZZARD_CLIENT_SECRET = os.getenv("BLIZZARD_CLIENT_SECRET", "")
REGION                 = os.getenv("REGION", "US").upper()

# ── Ironforge ─────────────────────────────────────────────────────────────────
IRONFORGE_BASE   = "https://ironforge.pro"
CURRENT_SEASON   = int(os.getenv("CURRENT_SEASON", "2"))
BRACKETS         = [2, 3, 5]
BRACKET_NAMES    = {2: "2v2", 3: "3v3", 5: "5v5"}
BRACKET_IDS      = {"2v2": 2, "3v3": 3, "5v5": 5}

# ── Timings ────────────────────────────────────────────────────────────────────
CACHE_REFRESH_MINUTES = 60
VERIFY_TIMEOUT_HOURS  = 24
QUEUE_EXPIRY_MINUTES  = 30

# ── DB ─────────────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "bot.db")

# ── Role names ─────────────────────────────────────────────────────────────────
ROLE_UNRANKED   = "Unranked"
ROLE_1400       = "1400+"
ROLE_1800       = "1800+"
ROLE_2100       = "2100+"
ROLE_GLADIATOR  = "Gladiator"
ROLE_MERCILESS  = "Merciless Gladiator"
ALL_PVP_ROLES   = [ROLE_MERCILESS, ROLE_GLADIATOR, ROLE_2100, ROLE_1800, ROLE_1400, ROLE_UNRANKED]

ROLE_ADMIN = os.getenv("ROLE_ADMIN", "Officer")
ROLE_BIS = os.getenv("ROLE_BIS", "BIS")

# ── LFG channels ──────────────────────────────────────────────────────────────
LFG_CHANNELS = {
    "2v2": "2v2-lfg",
    "3v3": "3v3-lfg",
    "5v5": "5v5-lfg",
}

# ── Spec → role mapping ────────────────────────────────────────────────────────
HEALER_SPECS = {
    "Holy",
    "Discipline",
    "Restoration",
}

def spec_role(spec: str) -> str:
    return "healer" if spec in HEALER_SPECS else "dps"

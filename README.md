# Nightslayer Arenas Bot

A lightweight personal Discord bot for finding **TBC Anniversary Classic** arena
partners. Members verify their character, receive PvP roles, and click **Join
Queue** when you post an LFG — the bot DMs both players and opens a private voice
room. Guests expire automatically; Friends never do.

## Features

- **Verification** — `/verify Name-Realm` or the persistent panel in `#verify`. Looks
  up the character on Ironforge.pro, falls back to the Blizzard Classic API, and
  assigns rating + class + spec roles.
- **LFG** — `/lfg <bracket>` posts a clean card. Optional rating preference. One
  click matches players and DMs them both.
- **Voice** — a private temp channel is created per match and auto-deleted when empty.
- **Friends** — `/friend add|remove @user` toggles permanent membership.
- **Blacklist** — `/blacklist add @user` / `/blacklist remove <id>` bans/unbans.
- **Whois** — `/whois @user` shows a member's verified character.
- **Cleanup** — hourly + `/cleanup`; removes expired guests and never-verified
  stragglers, always ignoring Friend, Moderator, and Admin.

## Project structure

```
bot.py                 bootstrap, setup_hook, cache-refresh loop, graceful shutdown
config.py              env-driven Settings (no hardcoded IDs)
db.py                  aiosqlite: users, active_lfg, blacklist (single WAL connection)
core.py                verification + role logic + permission checks
ironforge.py           Ironforge.pro ladder cache + rating-role mapping
blizzard.py            Blizzard Classic existence-check fallback
cogs/verification.py   /verify, persistent Verify/Profile/Help panel
cogs/lfg.py            /lfg, persistent Join button, expiry loop
cogs/voice.py          temp room creation + auto-delete
cogs/moderation.py     /friend, /blacklist, /whois, /cleanup, join gate
```

## Setup

1. `cp .env.example .env` and fill in the IDs (right-click → Copy ID in Discord with
   Developer Mode on). Only `DISCORD_TOKEN`, `GUILD_ID`, `GUEST_ROLE_ID`, and
   `FRIEND_ROLE_ID` are strictly required; rating/channel IDs are optional but
   enable the matching features.
2. `pip install -r requirements.txt`
3. `python bot.py`
4. In Discord, run `/setup-verify` in your `#verify` channel to post the panel.

### Required bot permissions & intents

- **Privileged intent:** Server Members Intent (enable in the Developer Portal).
- **Permissions:** Manage Roles, Kick Members, Ban Members, Manage Channels,
  Move Members, Manage Messages.
- **Hierarchy:** the bot's own role must sit **above** every role it assigns.

### Permission model

New members should only see `#welcome`, `#verify`, `#rules` (deny `@everyone` on
everything else). The **Guest** role grants access to LFG / music / voice and is
granted on verification. **Friend** carries the same access but never expires.
Rating/class/spec roles are cosmetic and unrelated to access.

## Deploy (Railway)

Uses the `Procfile` (`worker: python bot.py`) or the included `Dockerfile`. Set the
environment variables in the Railway dashboard and mount a volume for the SQLite
file (`DB_PATH=/app/data/bot.db`) so data survives redeploys.

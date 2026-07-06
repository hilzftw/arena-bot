# Nightslayer Arenas Bot

A lightweight personal Discord bot for a **World of Warcraft: TBC Anniversary Classic**
PvP server. It exists to support one workflow — **join → verify → queue → play →
cleanup** — plus a small, tightly-filtered WoW news feed. It is intentionally not a
big public-community bot.

Bot user: **Lou**. Single guild: **Nightslayer Arenas**.

---

## Features

- **Verification** — a persistent panel in `#welcome` with a guided flow:
  **Get Started → WoW / Chill**, and if WoW, **PvP / PvE**.
  - *PvP* → character lookup (Ironforge.pro, Blizzard Classic API fallback) →
    assigns **Guest** + rating/class/spec roles + arena access.
  - *PvE* → **PvE** role (community + PvE news, no arena).
  - *Chill* → **Social** role (plain community access).
- **LFG** — `/lfg` posts a card; a persistent **Join Queue** button DMs both players.
  Resolves its bracket channel by name, so a server reorg can't break it.
- **Moderation** — `/friend`, `/bis`, `/blacklist` (add/remove/list), `/whois`,
  `/cleanup`. Guests expire after a configurable window; Admin/Mod/Friend/BIS are
  always exempt.
- **Auto-cleanup** — hourly; removes expired guests (never-verified kicking is
  opt-in via `ENFORCE_VERIFICATION`, off by default).
- **Twitch alerts** *(optional)* — posts to `#twitchy-p` when a watched streamer
  goes live. Primes on startup so it never re-announces.
- **News** *(optional)* — polls Blizzard / Wowhead / Icy Veins RSS, classifies
  TBC-PvP-first, drops guides/tier-lists/opinion, dedups, and posts to the read-only
  `#tbc-pvp-news` / `#tbc-pve-news` / `#retail-wow-news` channels.
- **WoW QoL** — `/reset`, `/season`, `/item`, `/spell`, `/realm`.
- **Health** — startup + latency alerts and slash-command errors reported to
  `#bot-logs`.
- **Server scaffolding** — `/setup-server` builds the full category/channel/permission
  layout idempotently; `/move-channel` relocates a channel keeping its permissions.
- **Feature flags** — News/Twitch gated by env; `news_enabled` also toggleable live
  via `/news toggle` (stored in `bot_settings`).

## Commands

| Command | Who | Purpose |
|---|---|---|
| `/setup-server confirm:True` | Owner | Build/repair the whole server layout |
| `/setup-verify` | Staff | Post the Get Started panel (run in `#welcome`) |
| `/move-channel channel category` | Owner | Move a channel to a category, keep perms |
| `/verify Name-Realm` | Anyone | Direct character verification |
| `/lfg bracket [pref_min] [pref_max]` | Verified | Post an LFG card |
| `/whois @user` | Anyone | Show a member's verified character |
| `/friend add\|remove` | Staff | Manage permanent Friends |
| `/bis add\|remove` | Staff | Manage BIS role |
| `/blacklist add\|remove\|list` | Staff | Ban/track blacklisted users |
| `/cleanup` | Staff | Remove expired guests now |
| `/news refresh\|stats\|toggle` | Staff/Anyone | News controls |
| `/reset`, `/season`, `/item`, `/spell`, `/realm` | Anyone | WoW QoL |

## Project structure

```
bot.py                 bootstrap, setup_hook, cache-refresh loop, error handler
config.py              env-driven Settings (no hardcoded IDs)
core.py                verification + role logic, permission checks, #bot-logs logging
db.py                  aiosqlite (WAL, single connection): users, active_lfg,
                       blacklist, news_articles, bot_settings
ironforge.py           Ironforge.pro ladder cache + rating-role mapping
blizzard.py            Blizzard Classic existence-check fallback
news_sources.py        RSS ingestion
news_classifier.py     TBC-PvP-first news classifier
cogs/
  verification.py      verify flow + persistent Get Started panel
  lfg.py               /lfg + persistent Join button
  moderation.py        friend/bis/blacklist/whois/cleanup + join gate
  admin_setup.py       /setup-server, /move-channel
  wow.py               reset/season/item/spell/realm
  health.py            health monitoring → #bot-logs
  twitch.py            live-stream alerts (loaded if configured)
  news.py              news polling + /news (loaded if configured)
scripts/
  backup_db.py         online SQLite backup + pruning
```

## Setup

1. `cp .env.example .env` and fill it in (Developer Mode → right-click → Copy ID).
   **Required:** `DISCORD_TOKEN`, `GUILD_ID`, `GUEST_ROLE_ID`, `FRIEND_ROLE_ID`.
   Rating-role / channel / voice IDs are optional but enable those features.
2. `pip install -r requirements.txt`
3. `python bot.py`
4. In Discord: `/setup-server confirm:True`, then `/setup-verify` in `#welcome`.

### Permissions & intents (required)

- **Privileged intent:** Server Members Intent (enable in the Developer Portal).
- **Bot permissions:** Manage Roles, Manage Channels, Manage Server, Kick, Ban,
  Manage Messages.
- **Hierarchy:** Lou's role must sit **above** every role it assigns
  (Guest/Friend/BIS/Social/PvE/rating). This is the #1 cause of silent failures.

### Permission model

New members see only `#welcome` + `#rules`. Verifying (or picking PvE/Chill) grants a
role that unlocks the gated categories via **category-level** overwrites with synced
channels. `#bis-lounge` and the `🔒 ADMIN` channels stay hidden; news channels are
read-only, bot-post-only.

## Deploy (Railway)

- Builds from the repo (Railpack) or the included `Dockerfile`.
- **Persistence (critical):** attach a **Volume** mounted at `/data` and set
  `DB_PATH=/data/arena.db`, or the SQLite DB resets on every deploy.
- Set the environment variables in the Railway dashboard.
- `requirements.txt` pulls `feedparser` for the news module.

### Backups

`python scripts/backup_db.py` makes a timestamped online copy next to the DB and keeps
the newest 14. Point a scheduled job at it.

---

## Testing checklist

- [ ] Bot boots: `#bot-logs` shows "online", `/` commands appear in the guild.
- [ ] `/setup-server confirm:True` builds all categories/channels; `#bis-lounge` and
      `#bot-logs` are hidden; LFG channels exist.
- [ ] Verify flow: Get Started → WoW → PvP → real `Name-Realm` assigns Guest + roles;
      an unranked/nonexistent name is handled gracefully.
- [ ] PvE and Chill grant PvE/Social and unlock community but **not** arena.
- [ ] New (no-role) member sees only `#welcome`/`#rules`; verified sees the rest.
- [ ] `/lfg 2v2` posts a card; a second verified user's **Join Queue** DMs both.
- [ ] `/friend`, `/bis`, `/blacklist add|remove|list`, `/whois`, `/cleanup` behave.
- [ ] `/news refresh` posts filtered items to the right channels and dedups on re-run;
      guides/tier-lists are dropped.
- [ ] Twitch: watched streamer going live posts once to `#twitchy-p`.
- [ ] `/reset`, `/season set`+`show`, `/item`, `/spell`, `/realm` respond.
- [ ] Redeploy → verified users and blacklist persist (volume works).
- [ ] Trigger a command error → it surfaces in `#bot-logs`.

## Roadmap (optional, not required)

- Per-bracket LFG matchmaking with rating windows and multi-player parties.
- More WoW timers (Darkmoon Faire, world events) and Warcraft Logs lookup.
- Ironforge announcements as a news source.
- `/news` per-category browse + summaries.
- Web dashboard / stats export (only if it stays lightweight).

# Channel Map — Nightslayer Arenas Bot

How every Discord channel the bot touches is resolved, what breaks it, and how to fix it.
Written after the news feed went silent following the news-channel merge + server reorg.

## The rule

**Resolve channels by ID, never by name.** A rename or a category move changes a
channel's name; it never changes its ID. Only deleting and recreating a channel
changes the ID. Every name-based lookup in this bot is a latent outage waiting for
the next reorg.

Get an ID: Discord → Settings → Advanced → Developer Mode on → right-click channel → Copy Channel ID.

## Current wiring

| Channel | Resolved by | Config key | Fallback if unset |
|---|---|---|---|
| News (merged) | ID | `NEWS_CHANNEL_ID` = `1523684148407959737` | Legacy names `tbc-pvp-news` / `tbc-pve-news` / `retail-wow-news` |
| Welcome | ID | `WELCOME_CHANNEL_ID` | channel named `welcome` |
| Rules | ID | `RULES_CHANNEL_ID` | channel named `rules` |
| Verify | ID | `VERIFY_CHANNEL_ID` | none |
| Voice category | ID | `VOICE_CATEGORY_ID` | none |
| Twitch go-live | ID | `TWITCH_CHANNEL_ID` | channel named `twitchy-p-clips` |
| Audit log | ID | `LOG_CHANNEL_ID` | channel named `bot-logs` |

Name fallbacks are kept only for backwards compatibility. Set the IDs and none of
them are ever used.

## News feed

**TBC Classic only.** Everything posts to **one** channel (`NEWS_CHANNEL_ID`), and each
embed keeps its own emoji + category header so the merged feed stays skimmable:

| Category | Posts? | Header |
|---|---|---|
| `tbc-pvp` | yes | 🏆 **TBC PvP Update** |
| `tbc-pve` | yes | ⚔️ **TBC PvE Update** |
| `retail` | **no — dropped** | 🌍 Retail WoW Update |

Controlled by `NEWS_CATEGORIES` (default `tbc-pvp,tbc-pve`). Retail is still *classified*
— that's deliberate, it's how the classifier keeps a "War Within" article from being
mistaken for Classic news and landing in a TBC embed — it's just never posted. Dropped
retail articles are **not** marked seen, so adding `retail` back to `NEWS_CATEGORIES`
surfaces them rather than having silently consumed them.

Resolution order, highest priority first:

1. **DB override** — set live with `/news channel #x`, survives redeploys, wins over env.
2. **`NEWS_CHANNEL_ID`** env var — the normal path.
3. **Legacy per-category names** — only for servers still on the old three-channel layout.

### Commands

| Command | Who | What |
|---|---|---|
| `/news stats` | anyone | Post counts per category (disabled ones marked *(off)*) **and the channel the feed currently resolves to** — first thing to check when news goes quiet |
| `/news channel #x` | staff | Repoint the feed without a redeploy |
| `/news refresh` | staff | Fetch + post now |
| `/news prime` | staff | Mark everything currently in the feeds as seen **without posting** — use to clear a backlog |
| `/news toggle` | staff | Enable / disable |

### Flood guard

`NEWS_MAX_PER_RUN` (default 8) caps posts per poll. After downtime the RSS feeds
hold a backlog; without the cap, the first successful poll would dump dozens of
embeds at once. Overflow is marked seen and skipped, so the next poll starts clean
instead of replaying the same backlog forever.

## What broke, and why it was invisible

`cogs/news.py` resolved its target channel by hardcoded name:

```python
discord.utils.get(guild.text_channels, name="tbc-pvp-news")   # → None after the merge
```

`_run()` then hit `if channel is None: continue`. The article was dropped **and never
marked seen**, with no log line. Net effect: bot online, feeds fetching, classifier
working, zero posts, zero errors. Nothing in the logs pointed at the cause.

Fixed by resolving via ID, logging every failure path, and surfacing the resolved
target in `/news stats`.

## Welcome channel

`#welcome` holds the intro embed + the persistent **Get Started** verify panel
(`VerifyPanel`, `timeout=None`, registered in `bot.setup_hook` via `add_view` — the
buttons survive restarts).

### Commands

| Command | Who | What |
|---|---|---|
| `/welcome refresh` | staff | Delete the bot's own old posts in the channel and re-post a fresh intro + panel |
| `/welcome here` | staff | Print the current channel's ID for `WELCOME_CHANNEL_ID` |

### The stale-panel trap

`_seed()` used to post **only into a completely empty channel** (`history(limit=1)`,
post in the `else`). Once `#welcome` had any message in it — including a panel left
over from before a reorg — nothing could ever overwrite it. `/setup-server` would
skip it silently and there was no other command that could touch it.

`/welcome refresh` is the escape hatch: it deletes **only the bot's own** messages
(member messages are never touched) and re-posts. Needs **Manage Messages** in the
channel. `/setup-server` still uses the safe non-forcing path, so re-running it never
spams a live channel.

## Removed: LFG

The `/lfg` command, the queue, and the persistent **Join Queue** buttons are gone,
along with the `2v2` / `3v3` / `5v5` channels they posted into. Removed:

- `cogs/lfg.py` (deleted), its cog registration and `add_dynamic_items(LFGJoinButton)`
- `CHANNEL_2V2_ID` / `CHANNEL_3V3_ID` / `CHANNEL_5V5_ID`, `QUEUE_EXPIRY_MINUTES`
- `HEALER_SPECS`, `BRACKET_SIZE`, `spec_role_kind()` in `config.py`
- the `active_lfg` DB helpers and its `CREATE TABLE`
- every bit of copy promising "Join Queue on any LFG post"

`BRACKETS` / `BRACKET_NAMES` **stay** — `ironforge.py` uses them for ladder rating
lookups during `/verify`. They are not LFG.

The `active_lfg` table is **not** dropped on existing databases — no destructive
migrations. It's simply never read or written again.

## Removed: `/setup-server`

Gone, and worth knowing why. It was a one-shot provisioner that built the whole
layout and then **deleted** channels and roles matching a hardcoded "dead" list
(`DEAD_TEXT`, `DEAD_VOICE`, `DEAD_CATEGORIES`, `DEAD_ROLES`). Two problems:

1. The layout it built no longer matched the real server. It would have created
   `💬 COMMUNITY`, `⚔️ ARENA`, `📰 NEWS`, `⭐ BIS` **alongside** the actual
   `BUSITWIDE` / `WORLD OF WARCRAFT` / `ADMIN` categories, duplicating everything.
2. It recreated the three legacy news channels, silently undoing the merged feed.

The server has been provisioned for months. A destructive rebuild command that
no longer matches the thing it rebuilds can only cause damage, so it was deleted
along with `_cleanup()`, `_hoist_roles()`, `_ensure_casual_role()`, `_setup_afk()`,
`_rename_text()` and `_move_voice()`.

**Nothing in the bot creates or deletes channels or roles any more.** The only
`delete()` call left is `_purge_own()`, which removes the bot's own messages in
`#welcome` during `/welcome refresh`.

Still available from the admin cog:

| Command | Who | What |
|---|---|---|
| `/welcome refresh` | staff | Replace the bot's welcome post + verify panel |
| `/welcome here` | staff | Print this channel's ID for `WELCOME_CHANNEL_ID` |
| `/move-channel` | owner | Move a channel into a category, preserving its overwrites |

## If news goes quiet again

1. `/news stats` — check the **Posting to** field. `❌ unresolved` means the ID is wrong
   or the channel was deleted; `⚠️` means the bot lacks Send Messages / Embed Links there.
2. Confirm the bot's role can see and post in the channel.
3. `/news refresh` to force a poll.
4. Check logs for `NEWS_CHANNEL_ID=... but no such text channel` or
   `No news channel resolved for ...`.

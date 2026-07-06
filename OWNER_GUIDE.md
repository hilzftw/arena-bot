# 🏹 Nightslayer Arenas — Owner's Guide

Everything you need to run **Lou** (your bot). No coding — just slash commands.
Type `/` in any channel to see them.

---

## 🚀 First-time setup (do once)

1. **Check Lou's role.** Server Settings → Roles → drag **Lou** near the top of the
   list (above Guest/Friend/BIS/rating roles) and make sure it has *Manage Roles*,
   *Manage Channels*, and *Ban Members*. If Lou is too low in the list, verification
   and setup will silently fail — this fixes 90% of "it's not working."
2. **Build the server:** type `/setup-server confirm:True`. Lou creates every
   category, channel, and permission and posts the welcome Script in **#welcome**.
3. **Post the panel (if needed):** `/setup-verify` in **#welcome**.

That's it. New people can now join and set themselves up.

---

## 👋 How members join (what they see)

They land in **#welcome** and click **Get Started**:

- **WoW → PvP** → they enter `Character-Realm`, and Lou gives them their rating,
  class, and spec roles + arena access.
- **WoW → PvE** → community access + PvE news (no arena).
- **Chill** → plain community access.

You don't have to do anything — it's automatic.

---

## 🎯 Finding arena partners

- Post a group: **`/lfg 2v2`** (or 3v3 / 5v5). Add `pref_min` / `pref_max` to filter
  by rating.
- Lou posts a card with a **Join Queue** button. When someone clicks it, Lou **DMs
  both of you** to connect. Done.

---

## 🛡️ Managing people

| You want to… | Type |
|---|---|
| Make someone a permanent regular | `/friend add @user` |
| Give a trusted player VIP (#bis-lounge) | `/bis add @user` |
| Ban a troublemaker | `/blacklist add @user reason:...` |
| See who's blacklisted | `/blacklist list` |
| Look up someone's character | `/whois @user` |
| Remove inactive guests now | `/cleanup` |

**Good to know:** Guests are auto-removed after a while if inactive. **Friend, BIS,
Moderator, and Admin are never removed** and never need to verify — so you (as admin)
are always safe.

---

## 📰 News feed

Lou automatically posts filtered WoW news to **#tbc-pvp-news**, **#tbc-pve-news**, and
**#retail-wow-news** — only real news (arena, patches, raids, maintenance), never
guides or tier lists.

- Force a check now: `/news refresh`
- See how much it's posted: `/news stats`
- Turn it off/on: `/news toggle enabled:false`

---

## 🎥 Twitch alerts

When your streamer goes live, Lou posts to **#twitchy-p** automatically. Nothing to do.

---

## 🐉 Handy WoW commands (anyone can use)

- `/reset` — time until daily & weekly reset
- `/season set date:YYYY-MM-DD` then `/season show` — arena season countdown
- `/item name:` / `/spell name:` — Wowhead lookups
- `/realm` — realm status links

---

## 🧹 Housekeeping

- **Move a channel** (keeps its permissions): `/move-channel channel:#x category:COMMUNITY`
- **Health:** Lou reports its status and any errors to **#bot-logs** — glance there if
  something seems off.
- **Backups:** your data is saved on a Railway volume, so it survives restarts. A
  backup script (`scripts/backup_db.py`) can make dated copies if you schedule it.

---

## 🆘 If something's not working

- **Roles aren't being assigned / setup fails** → Lou's role is too low or missing
  permissions. Drag it up, give it Manage Roles + Manage Channels.
- **News isn't posting** → run `/news refresh`; if still nothing, a source's feed may
  be down (tell me and I'll swap it).
- **A member can't see channels** → they haven't picked an option in #welcome yet, or
  their role is missing — check with `/whois`.

That's the whole thing. Join → Verify → Queue → Play. 🏆

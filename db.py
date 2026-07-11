"""
SQLite persistence layer.

One shared aiosqlite connection (WAL mode) for the whole process — this bot is a
single-guild personal tool, so a connection pool is unnecessary overhead.

Tables
    users          one row per verified member (guest expiry lives here as expires_at)
    blacklist      banned discord ids
    news_articles  dedup for the news feed — one row per posted article
    bot_settings   feature flags + small key/value settings (no redeploy needed)

Guest vs Friend is expressed purely by expires_at:
    expires_at IS NULL  -> Friend (never expires)
    expires_at > now     -> active Guest
    expires_at <= now    -> Guest due for cleanup
"""
from __future__ import annotations

import time
from typing import Any, Optional

import aiosqlite

from config import settings

_conn: Optional[aiosqlite.Connection] = None


async def connect() -> aiosqlite.Connection:
    """Open (once) and return the shared connection."""
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(settings.db_path)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.execute("PRAGMA foreign_keys=ON")
        await _conn.commit()
    return _conn


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def init_db() -> None:
    conn = await connect()
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            discord_id  TEXT PRIMARY KEY,
            character   TEXT NOT NULL,
            realm       TEXT NOT NULL,
            region      TEXT NOT NULL DEFAULT 'US',
            faction     TEXT,
            class       TEXT,
            spec        TEXT,
            rating      INTEGER NOT NULL DEFAULT 0,
            role_key    TEXT,
            source      TEXT,
            verified_at INTEGER NOT NULL,
            expires_at  INTEGER            -- NULL = Friend / permanent
        );

        -- NOTE: the active_lfg table is intentionally no longer created. The LFG
        -- feature was removed with the 2v2/3v3/5v5 channels. Existing databases keep
        -- their table (we don't drop it — no destructive migrations), it's just unused.

        CREATE TABLE IF NOT EXISTS blacklist (
            discord_id  TEXT PRIMARY KEY,
            reason      TEXT,
            added_by    TEXT,
            added_at    INTEGER NOT NULL
        );

        -- Dedup for the news module: one row per posted article.
        CREATE TABLE IF NOT EXISTS news_articles (
            guid        TEXT PRIMARY KEY,      -- stable id/link from the feed
            category    TEXT NOT NULL,         -- tbc-pvp | tbc-pve | retail
            title       TEXT,
            url         TEXT,
            source      TEXT,
            posted_at   INTEGER NOT NULL
        );

        -- Feature flags + small key/value settings, toggleable without a redeploy.
        CREATE TABLE IF NOT EXISTS bot_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_users_expiry ON users(expires_at);
        CREATE INDEX IF NOT EXISTS idx_news_cat     ON news_articles(category, posted_at);
        """
    )
    # Backfill added_by for pre-existing blacklist rows (older schema).
    try:
        await conn.execute("ALTER TABLE blacklist ADD COLUMN added_by TEXT")
    except Exception:
        pass
    await conn.commit()


# ── users ──────────────────────────────────────────────────────────────────────

async def upsert_user(
    discord_id: str,
    character: str,
    realm: str,
    region: str,
    faction: Optional[str],
    class_: Optional[str],
    spec: Optional[str],
    rating: int,
    role_key: Optional[str],
    source: str,
    expires_at: Optional[int],
) -> None:
    """Insert or update a verified user. Preserves existing expires_at on re-verify
    unless a new value is explicitly provided (pass -1 sentinel to keep current)."""
    conn = await connect()
    keep_expiry = expires_at == -1
    await conn.execute(
        """
        INSERT INTO users
            (discord_id, character, realm, region, faction, class, spec,
             rating, role_key, source, verified_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET
            character=excluded.character, realm=excluded.realm, region=excluded.region,
            faction=excluded.faction, class=excluded.class, spec=excluded.spec,
            rating=excluded.rating, role_key=excluded.role_key, source=excluded.source,
            verified_at=excluded.verified_at,
            expires_at=CASE WHEN ? THEN users.expires_at ELSE excluded.expires_at END
        """,
        (
            discord_id, character, realm, region, faction, class_, spec,
            rating, role_key, source, int(time.time()),
            None if keep_expiry else expires_at,
            1 if keep_expiry else 0,
        ),
    )
    await conn.commit()


async def get_user(discord_id: str) -> Optional[dict[str, Any]]:
    conn = await connect()
    async with conn.execute("SELECT * FROM users WHERE discord_id=?", (discord_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def delete_user(discord_id: str) -> None:
    conn = await connect()
    await conn.execute("DELETE FROM users WHERE discord_id=?", (discord_id,))
    await conn.commit()


async def set_expiry(discord_id: str, expires_at: Optional[int]) -> None:
    """Set expires_at (None = permanent / Friend)."""
    conn = await connect()
    await conn.execute(
        "UPDATE users SET expires_at=? WHERE discord_id=?", (expires_at, discord_id)
    )
    await conn.commit()


async def get_expired_guests(now: Optional[int] = None) -> list[dict[str, Any]]:
    now = now or int(time.time())
    conn = await connect()
    async with conn.execute(
        "SELECT * FROM users WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ── active_lfg ─────────────────────────────────────────────────────────────────
# Removed with the LFG feature (add_lfg / get_lfg / deactivate_lfg /
# take_expired_lfg). The table is left in place on existing databases but is
# no longer read or written.


# ── blacklist ──────────────────────────────────────────────────────────────────

async def add_blacklist(discord_id: str, reason: Optional[str],
                        added_by: Optional[str] = None) -> None:
    conn = await connect()
    await conn.execute(
        "INSERT INTO blacklist (discord_id, reason, added_by, added_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(discord_id) DO UPDATE SET reason=excluded.reason, added_by=excluded.added_by",
        (discord_id, reason, added_by, int(time.time())),
    )
    await conn.commit()


async def remove_blacklist(discord_id: str) -> None:
    conn = await connect()
    await conn.execute("DELETE FROM blacklist WHERE discord_id=?", (discord_id,))
    await conn.commit()


async def is_blacklisted(discord_id: str) -> bool:
    conn = await connect()
    async with conn.execute(
        "SELECT 1 FROM blacklist WHERE discord_id=?", (discord_id,)
    ) as cur:
        return await cur.fetchone() is not None


async def list_blacklist() -> list[dict[str, Any]]:
    conn = await connect()
    async with conn.execute(
        "SELECT * FROM blacklist ORDER BY added_at DESC"
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ── news dedup ───────────────────────────────────────────────────────────────

async def news_seen(guid: str) -> bool:
    conn = await connect()
    async with conn.execute("SELECT 1 FROM news_articles WHERE guid=?", (guid,)) as cur:
        return await cur.fetchone() is not None


async def mark_news_posted(guid: str, category: str, title: str,
                           url: str, source: str) -> None:
    conn = await connect()
    await conn.execute(
        "INSERT OR IGNORE INTO news_articles (guid, category, title, url, source, posted_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (guid, category, title, url, source, int(time.time())),
    )
    await conn.commit()


async def news_stats() -> dict[str, int]:
    conn = await connect()
    async with conn.execute(
        "SELECT category, COUNT(*) AS n FROM news_articles GROUP BY category"
    ) as cur:
        return {r["category"]: r["n"] for r in await cur.fetchall()}


# ── bot_settings (feature flags) ─────────────────────────────────────────────

async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = await connect()
    async with conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
        return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    conn = await connect()
    await conn.execute(
        "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await conn.commit()

"""
SQLite persistence layer.

One shared aiosqlite connection (WAL mode) for the whole process — this bot is a
single-guild personal tool, so a connection pool is unnecessary overhead.

Tables
    users        one row per verified member (guest expiry lives here as expires_at)
    active_lfg   open LFG cards; custom_id survives restarts for persistent buttons
    blacklist    banned discord ids

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

        CREATE TABLE IF NOT EXISTS active_lfg (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id  TEXT NOT NULL,
            bracket     TEXT NOT NULL,
            rating      INTEGER NOT NULL DEFAULT 0,
            pref_min    INTEGER,
            pref_max    INTEGER,
            message_id  TEXT,
            channel_id  TEXT,
            created_at  INTEGER NOT NULL,
            expires_at  INTEGER NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS blacklist (
            discord_id  TEXT PRIMARY KEY,
            reason      TEXT,
            added_at    INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_lfg_active   ON active_lfg(active, bracket);
        CREATE INDEX IF NOT EXISTS idx_users_expiry ON users(expires_at);
        """
    )
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

async def add_lfg(
    discord_id: str,
    bracket: str,
    rating: int,
    pref_min: Optional[int],
    pref_max: Optional[int],
    message_id: str,
    channel_id: str,
    expiry_minutes: int,
) -> int:
    now = int(time.time())
    conn = await connect()
    # One active card per user per bracket — retire older ones.
    await conn.execute(
        "UPDATE active_lfg SET active=0 WHERE discord_id=? AND bracket=? AND active=1",
        (discord_id, bracket),
    )
    cur = await conn.execute(
        """
        INSERT INTO active_lfg
            (discord_id, bracket, rating, pref_min, pref_max,
             message_id, channel_id, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (discord_id, bracket, rating, pref_min, pref_max,
         message_id, channel_id, now, now + expiry_minutes * 60),
    )
    await conn.commit()
    return cur.lastrowid


async def get_lfg(entry_id: int) -> Optional[dict[str, Any]]:
    conn = await connect()
    async with conn.execute("SELECT * FROM active_lfg WHERE id=?", (entry_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def deactivate_lfg(entry_id: int) -> None:
    conn = await connect()
    await conn.execute("UPDATE active_lfg SET active=0 WHERE id=?", (entry_id,))
    await conn.commit()


async def take_expired_lfg(now: Optional[int] = None) -> list[dict[str, Any]]:
    """Return + retire all active cards past expiry (idempotent)."""
    now = now or int(time.time())
    conn = await connect()
    async with conn.execute(
        "SELECT * FROM active_lfg WHERE active=1 AND expires_at <= ?", (now,)
    ) as cur:
        expired = [dict(r) for r in await cur.fetchall()]
    if expired:
        ids = [e["id"] for e in expired]
        await conn.execute(
            f"UPDATE active_lfg SET active=0 WHERE id IN ({','.join('?' * len(ids))})", ids
        )
        await conn.commit()
    return expired


# ── blacklist ──────────────────────────────────────────────────────────────────

async def add_blacklist(discord_id: str, reason: Optional[str]) -> None:
    conn = await connect()
    await conn.execute(
        "INSERT INTO blacklist (discord_id, reason, added_at) VALUES (?, ?, ?) "
        "ON CONFLICT(discord_id) DO UPDATE SET reason=excluded.reason",
        (discord_id, reason, int(time.time())),
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

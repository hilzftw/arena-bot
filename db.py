"""
SQLite layer — all async via aiosqlite.
"""
import time
import aiosqlite
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id  TEXT PRIMARY KEY,
                character   TEXT NOT NULL,
                server      TEXT NOT NULL,
                region      TEXT NOT NULL DEFAULT 'US',
                verified_at INTEGER NOT NULL,
                highest_rating INTEGER DEFAULT 0,
                bracket     TEXT,
                spec        TEXT,
                class       TEXT,
                faction     TEXT,
                source      TEXT
            );

            CREATE TABLE IF NOT EXISTS lfg_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id  TEXT NOT NULL,
                bracket     TEXT NOT NULL,
                spec        TEXT NOT NULL,
                class       TEXT NOT NULL,
                role        TEXT NOT NULL,
                rating      INTEGER NOT NULL,
                target_min  INTEGER,
                target_max  INTEGER,
                message_id  TEXT,
                channel_id  TEXT,
                created_at  INTEGER NOT NULL,
                expires_at  INTEGER NOT NULL,
                active      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id  TEXT NOT NULL,
                bracket     TEXT NOT NULL,
                comp        TEXT,
                channel_id  TEXT,
                started_at  INTEGER NOT NULL,
                ended_at    INTEGER,
                wins        INTEGER DEFAULT 0,
                losses      INTEGER DEFAULT 0
            );
        """)
        await db.commit()


async def upsert_user(discord_id, character, server, region,
                      highest_rating, bracket, spec, class_, faction, source):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users
                (discord_id, character, server, region, verified_at,
                 highest_rating, bracket, spec, class, faction, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                character=excluded.character, server=excluded.server,
                region=excluded.region, verified_at=excluded.verified_at,
                highest_rating=excluded.highest_rating, bracket=excluded.bracket,
                spec=excluded.spec, class=excluded.class, faction=excluded.faction,
                source=excluded.source
        """, (discord_id, character, server, region, int(time.time()),
              highest_rating, bracket, spec, class_, faction, source))
        await db.commit()


async def get_user(discord_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_verified():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_user(discord_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE discord_id = ?", (discord_id,))
        await db.commit()


async def add_queue_entry(discord_id, bracket, spec, class_, role, rating,
                          target_min, target_max, message_id, channel_id):
    now = int(time.time())
    expires = now + 30 * 60
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE lfg_queue SET active=0 WHERE discord_id=? AND bracket=? AND active=1",
            (discord_id, bracket)
        )
        cur = await db.execute("""
            INSERT INTO lfg_queue
                (discord_id, bracket, spec, class, role, rating,
                 target_min, target_max, message_id, channel_id, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (discord_id, bracket, spec, class_, role, rating,
              target_min, target_max, message_id, channel_id, now, expires))
        await db.commit()
        return cur.lastrowid


async def get_active_queue(bracket):
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM lfg_queue WHERE bracket=? AND active=1 AND expires_at > ?
            ORDER BY created_at DESC
        """, (bracket, now)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def deactivate_queue_entry(entry_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE lfg_queue SET active=0 WHERE id=?", (entry_id,))
        await db.commit()


async def expire_queue_entries():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM lfg_queue WHERE active=1 AND expires_at <= ?", (now,)
        ) as cur:
            expired = [dict(r) for r in await cur.fetchall()]
        if expired:
            ids = [e["id"] for e in expired]
            await db.execute(
                f"UPDATE lfg_queue SET active=0 WHERE id IN ({','.join('?'*len(ids))})", ids
            )
            await db.commit()
        return expired


async def start_session(discord_id, bracket, comp, channel_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO sessions (discord_id, bracket, comp, channel_id, started_at)
            VALUES (?, ?, ?, ?, ?)
        """, (discord_id, bracket, comp, channel_id, int(time.time())))
        await db.commit()
        return cur.lastrowid


async def end_session(session_id, wins, losses):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET ended_at=?, wins=?, losses=? WHERE id=?",
            (int(time.time()), wins, losses, session_id)
        )
        await db.commit()


async def get_active_session(discord_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE discord_id=? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (discord_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

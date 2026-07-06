#!/usr/bin/env python3
"""
Back up the SQLite database using the online backup API (safe while the bot runs).

Usage:
    python scripts/backup_db.py                 # uses $DB_PATH or bot.db
    python scripts/backup_db.py /data/arena.db  # explicit source

Writes a timestamped copy next to the DB (e.g. arena.db.2026-07-06T12-00-00.bak)
and prunes to the newest KEEP backups. Point a cron/Railway scheduled job at this.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

KEEP = 14


def backup(src: str) -> Path:
    src_path = Path(src)
    if not src_path.exists():
        raise SystemExit(f"Database not found: {src_path}")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    dest = src_path.with_suffix(src_path.suffix + f".{stamp}.bak")
    with sqlite3.connect(src_path) as source, sqlite3.connect(dest) as target:
        source.backup(target)
    return dest


def prune(src: str) -> None:
    src_path = Path(src)
    backups = sorted(src_path.parent.glob(src_path.name + ".*.bak"), reverse=True)
    for old in backups[KEEP:]:
        old.unlink(missing_ok=True)


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DB_PATH", "bot.db")
    dest = backup(src)
    prune(src)
    print(f"Backed up {src} -> {dest}")


if __name__ == "__main__":
    main()

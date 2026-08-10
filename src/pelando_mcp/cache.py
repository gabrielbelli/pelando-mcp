"""Tiny sqlite-backed key/value cache with per-key TTLs."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


class Cache:
    """Synchronous sqlite cache, keyed by URL.

    Threadsafe enough for this app: HTTP is serialised through the rate limiter, so writes never
    race in practice.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("PELANDO_CACHE_PATH", "data/cache.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS http_cache (
                url TEXT PRIMARY KEY,
                body TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )"""
        )
        self._conn.commit()

    def get(self, url: str) -> str | None:
        cur = self._conn.execute("SELECT body, expires_at FROM http_cache WHERE url = ?", (url,))
        row = cur.fetchone()
        if not row:
            return None
        body, expires_at = row
        if expires_at < time.time():
            return None
        return body

    def put(self, url: str, body: str, ttl_seconds: float) -> None:
        now = time.time()
        self._conn.execute(
            """INSERT INTO http_cache(url, body, fetched_at, expires_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 body = excluded.body,
                 fetched_at = excluded.fetched_at,
                 expires_at = excluded.expires_at""",
            (url, body, now, now + ttl_seconds),
        )
        self._conn.commit()

    def purge_expired(self) -> int:
        cur = self._conn.execute("DELETE FROM http_cache WHERE expires_at < ?", (time.time(),))
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()

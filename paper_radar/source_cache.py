# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Bounded cross-process cache for *public* metadata search results.

No account IDs, credential values, or raw search text are stored in cache keys.
Positive results only; outage fallback is explicit and never labeled fresh.
All SQL parameters are bound; SQLite provides process-safe transactions.
"""
from __future__ import annotations

from dataclasses import asdict, fields
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from .models import Paper

CACHE_FIELDS = ('keywords', 'categories', 'lookback_days', 'max_results',
                'max_queries_per_source', 'research_goal',
                '_window_start', '_window_end', '_watch_arxiv_ids', 'openreview_invitation')
CACHE_VERSION = 2
FRESH_SECONDS = {'arxiv': 3600, 'openalex': 3600, 'crossref': 7200}
MAX_STALE_SECONDS = 24 * 3600
MAX_BYTES = 1_750_000
MAX_ENTRIES = 250

class SourceResponse(list):
    def __init__(self, papers=(), *, cache_state='miss', age_seconds=0,
                 partial=False, warning_code=''):
        super().__init__(papers)
        self.cache_state = cache_state
        self.cache_age_seconds = age_seconds
        self.partial = bool(partial)
        self.warning_code = warning_code


def cache_key(source: str, cfg: dict, *, clock=None) -> str:
    if source not in FRESH_SECONDS:
        raise ValueError('Unknown source')
    # Publication-date filters change at midnight UTC; never reuse yesterday's
    # query for today's date window. Public metadata is shared, not private.
    day = time.strftime('%Y-%m-%d', time.gmtime(clock if clock is not None else time.time()))
    safe_cfg = {key: cfg.get(key) for key in CACHE_FIELDS if key in cfg}
    raw = json.dumps([CACHE_VERSION, source, day, safe_cfg],
                     ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(raw.encode('utf-8')).hexdigest()


class SourceCache:
    def __init__(self, root: Path, *, clock=None):
        provided=Path(root).expanduser()
        if provided.is_symlink():
            raise ValueError('Unsafe source cache root symlink')
        self.root = provided.resolve()
        self.path = self.root / 'source_metadata_cache.sqlite3'
        self.clock = clock or time.time

    @contextmanager
    def _connect(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.path.is_symlink():
            raise ValueError('Unsafe source cache path')
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA busy_timeout=5000')
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS source_cache (
                cache_key TEXT PRIMARY KEY, source TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                payload TEXT NOT NULL)''')
            with db:yield db
        finally:db.close()

    def get(self, source: str, cfg: dict, *, allow_stale=False) -> SourceResponse | None:
        if source not in FRESH_SECONDS: return None
        try:
            with self._connect() as db:
                row = db.execute('SELECT created_at,expires_at,payload FROM source_cache WHERE cache_key=?',
                                 (cache_key(source, cfg, clock=self.clock()),)).fetchone()
            if not row:return None
            current = int(self.clock())
            if current >= int(row[1]) and not allow_stale:return None
            if current-int(row[0])>MAX_STALE_SECONDS or current<int(row[0])-60:return None
            data = json.loads(row[2])
            if not isinstance(data, list) or len(data)>100:return None
            if not data and allow_stale:return None  # An outage should not return stale emptiness.
            allowed = {f.name for f in fields(Paper)}
            papers = [Paper(**{k:v for k,v in item.items() if k in allowed})
                      for item in data if isinstance(item,dict)]
            if len(papers)!=len(data):return None
            return SourceResponse(papers, cache_state='hit' if current<int(row[1]) else 'stale',
                                  age_seconds=max(0,current-int(row[0])))
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            # A corrupt cache must never block legitimate network discovery.
            return None

    def put(self, source: str, cfg: dict, papers: list) -> None:
        if source not in FRESH_SECONDS or not isinstance(papers,list) or len(papers)>100:return
        try:
            value=json.dumps([asdict(p) for p in papers],ensure_ascii=False,separators=(',',':'))
            if len(value.encode('utf-8'))>MAX_BYTES:return
            now=int(self.clock())
            with self._connect() as db:
                db.execute('''INSERT INTO source_cache(cache_key,source,created_at,expires_at,payload)
                    VALUES (?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                    created_at=excluded.created_at,expires_at=excluded.expires_at,payload=excluded.payload''',
                    (cache_key(source,cfg,clock=self.clock()),source,now,
                     now+(min(300,FRESH_SECONDS[source]) if not papers else FRESH_SECONDS[source]),value))
                # Prune stale entries and bound total row count (also bounds data on disk).
                db.execute('DELETE FROM source_cache WHERE created_at<?', (now-MAX_STALE_SECONDS,))
                db.execute('''DELETE FROM source_cache WHERE cache_key IN (
                    SELECT cache_key FROM source_cache ORDER BY created_at DESC LIMIT -1 OFFSET ?)''',
                    (MAX_ENTRIES,))
            if os.name!='nt':os.chmod(self.path,0o600)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            pass  # Caching is best effort; research must keep working.

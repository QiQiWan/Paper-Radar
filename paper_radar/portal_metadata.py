# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Conservative arXiv metadata refresh without resetting per-topic reading state.

Each account owns its own source revision cursor. Older arXiv records must not
roll back a newer observed source revision. No user-supplied metadata is merged.
"""
from __future__ import annotations
from datetime import datetime, timezone


def _timestamp(value: str) -> datetime | None:
    try:
        value = str(value or '').strip()
        if not value:
            return None
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def merge_arxiv_paper(db, paper, at: str) -> str:
    """Return ``inserted``, ``updated``, ``unchanged`` or ``stale``.

    Publication date is not source update time. A newer source timestamp wins;
    a result with no update timestamp cannot overwrite a known newer version.
    Metadata refresh never touches the topic_paper_flags table.
    """
    pid = str(paper.paper_id or '')[:150]
    title = str(paper.title or '')[:400]
    if not pid or not title:
        return 'unchanged'
    attrs = {
        'title': title,
        'abstract': str(paper.abstract or '')[:10000],
        'published': str(paper.published or '')[:40],
        'url': str(paper.url or '')[:600],
        'doi': str(paper.doi or '')[:200],
    }
    updated = str(getattr(paper, 'updated', '') or '')[:50]
    updated_dt = _timestamp(updated)
    # Invalid/future timestamps cannot be used as authoritative ordering.
    if updated and updated_dt is None:
        updated = ''
    old = db.execute('SELECT * FROM papers WHERE id=?', (pid,)).fetchone()
    previous = db.execute('SELECT source_updated FROM paper_metadata_versions WHERE paper_id=?',
                          (pid,)).fetchone()
    previous_stamp = str(previous['source_updated'] if previous else '')
    old_dt = _timestamp(previous_stamp)
    if old_dt is not None and (updated_dt is None or updated_dt < old_dt):
        return 'stale'
    if old is None:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
                   (pid, attrs['title'], attrs['abstract'], attrs['published'],
                    attrs['url'], attrs['doi'], at))
        outcome = 'inserted'
    else:
        # A temporary empty field in the source response must not erase data.
        merged = {k: attrs[k] or str(old[k]) for k in attrs}
        if all(merged[k] == old[k] for k in attrs):
            outcome = 'unchanged'
        else:
            db.execute('''UPDATE papers SET title=?,abstract=?,published=?,url=?,doi=? WHERE id=?''',
                       (merged['title'], merged['abstract'], merged['published'],
                        merged['url'], merged['doi'], pid))
            db.execute('''INSERT INTO paper_metadata_history
                          (paper_id,observed_at,source_updated,previous_title,new_title)
                          VALUES (?,?,?,?,?)''',
                       (pid, at, updated if updated_dt else previous_stamp,
                        str(old['title']), merged['title']))
            outcome = 'updated'
    cursor = updated if updated_dt is not None else previous_stamp
    db.execute('''INSERT INTO paper_metadata_versions(paper_id,source_updated,observed_at)
                  VALUES (?,?,?) ON CONFLICT(paper_id) DO UPDATE SET
                  source_updated=excluded.source_updated,observed_at=excluded.observed_at''',
               (pid, cursor, at))
    return outcome

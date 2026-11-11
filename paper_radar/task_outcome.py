"""Authoritative search-result status, independent from execution progress."""
from __future__ import annotations


def retrieval_outcome(rows: list[dict] | None, *, found: int = 0, added: int = 0) -> dict:
    """Return a stable per-source outcome for UI and monitoring.

    An absent source report is explicitly unknown. An empty successful query is
    distinguishable from all-sources-failed. A cached/stale or truncated source
    never counts as fully covered.
    """
    sources = rows if isinstance(rows, list) else []
    def capped(s):
        return s.get('status') == 'limit_reached'
    complete = sum(bool(s.get('ok')) and (capped(s) or not bool(s.get('partial')))
                   and s.get('cache_state') != 'stale' for s in sources)
    partial = sum(bool(s.get('ok')) and not capped(s) and
                  (bool(s.get('partial')) or s.get('cache_state') == 'stale')
                  for s in sources)
    failed = len(sources) - complete - partial
    if not sources:
        outcome = 'unknown'
    elif not complete and not partial:
        outcome = 'failed'
    elif failed or partial:
        outcome = 'partial'
    elif not found and not added:
        outcome = 'empty'
    else:
        outcome = 'success'
    return {'outcome': outcome, 'sources_total': len(sources),
            'sources_ok': complete, 'sources_partial': partial,
            'sources_capped': sum(capped(s) for s in sources),
            'sources_failed': failed, 'papers_found': max(0, int(found)),
            'papers_added': max(0, int(added))}

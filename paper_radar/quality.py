# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Deterministic relevance and publication-date quality gates.

Keep this layer independent from source adapters, local storage and report
renderers so a changed scoring policy can be tested before reindexing data.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import re
import unicodedata

# Generic technique/subject vocabulary should not, alone, qualify a paper
# for a specialized research topic. Multiword concepts remain specific.
BROAD_TERMS = frozenset({
    'numerical', 'numerical analysis', 'numerical simulation',
    'simulation', 'simulations', 'computational', 'computational modeling',
    'modeling', 'modelling', 'model', 'models', 'analysis', 'study', 'studies',
    'method', 'methods', 'approach', 'research', 'engineering', 'scientific',
})


def normalized(text: str) -> str:
    """Prepare metadata for case-insensitive phrase matching."""
    text = unicodedata.normalize('NFKC', str(text or '')).casefold()
    text = re.sub('[\u2010-\u2015\u2212]', '-', text)
    return re.sub(r'\s+', ' ', text).strip()


@lru_cache(maxsize=1024)
def _term_regex(phrase: str):
    parts = [re.escape(w) for w in re.split(r'[\s\-]+', normalized(phrase)) if w]
    if not parts:
        return re.compile(r'(?!)')
    return re.compile(r'(?<![\w])' + r'[\s\-]+'.join(parts) + r'(?![\w])')


def matches(text: str, phrase: str) -> bool:
    return bool(_term_regex(phrase).search(normalized(text)))


def is_broad(term: str) -> bool:
    return normalized(term) in BROAD_TERMS


def matched_keywords(title: str, abstract: str, keywords: list[str], *, include_broad=False) -> list[str]:
    return [k for k in keywords if (include_broad or not is_broad(k))
            and (matches(title, k) or matches(abstract, k))]


def score_relevance(title: str, abstract: str, keywords: list[str], ranker=None, paper=None) -> float:
    """Require at least one recognizable topic anchor (or strong Zotero similarity).

    Generic terms may refine scores after a specific topic has matched, but
    cannot make an unrelated article enter a report by themselves.
    """
    score = 0.0
    specific_hits = 0
    generic_boost = 0.0
    for raw in keywords:
        term = normalized(raw)
        if not term:
            continue
        in_title, in_abstract = matches(title, term), matches(abstract, term)
        if is_broad(term):
            generic_boost += 0.20 if in_title else (0.08 if in_abstract else 0)
        elif in_title:
            score += 4.0 + (0.25 if len(term.split()) > 1 else 0)
            specific_hits += 1
        elif in_abstract:
            score += 1.6
            specific_hits += 1
    zotero_similarity = 0.0
    if ranker is not None and paper is not None:
        try:
            zotero_similarity = float(ranker.score(paper))
        except (ValueError, TypeError):
            zotero_similarity = 0.0
    # Strongly similar papers can be included even when the wording differs;
    # this threshold also prevents weak semantic matches from dominating.
    if not specific_hits and zotero_similarity < 0.34:
        return 0.0
    score += min(generic_boost, 0.45)
    if zotero_similarity > 0:
        score += min(8.0, max(0, zotero_similarity) * 8.0)
    return round(score, 4)


def valid_recent_publication(value: str, now: datetime | None = None, days: int = 7) -> bool:
    """Accept only complete calendar dates within [today-days, today].

    Crossref can supply future print/issue dates or just a year/month. Those
    records belong in the full metadata index, not a 'last 7 days' report.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today = current.astimezone(timezone.utc).date()
    stamp = str(value or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?', stamp):
        return False
    try:
        published = date.fromisoformat(stamp[:10])
    except ValueError:
        return False
    return today - timedelta(days=max(1, days)) <= published <= today

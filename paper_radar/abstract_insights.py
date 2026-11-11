# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Grounded research leads from abstracts, independent of PDF availability.

Statements are exact spans of author/publisher metadata, not model-generated
scientific findings. They remain unverified and can never enter the PDF evidence
chain or numerical comparison without independent full-text checks.
"""
from __future__ import annotations

import re

# Existing, generic scientific language; these are extraction heuristics only.
_CUES = (
    ('method', '研究方法', re.compile(r'\b(?:we\s+(?:propose|present|introduce|develop|design)|propos(?:e|ed)|introduc(?:e|ed)|new\s+(?:framework|method)|architecture|approach|novel\s+model)\b', re.I)),
    ('evaluation', '实验设置', re.compile(r'\b(?:we\s+(?:evaluate|test|compare)|experiment(?:s|al)?|benchmark(?:s)?|training|test(?:ed|ing)?\s+(?:on|across)|dataset(?:s)?|mesh(?:es)?|geometr(?:y|ies)|evaluation|cross[- ](?:mesh|geometry))\b', re.I)),
    ('reported_result', '摘要报告的结果', re.compile(r'\b(?:we\s+(?:find|show|demonstrate|report)|results?\s+(?:show|indicate|demonstrate)|outperform(?:s|ed)?|improv(?:e|es|ed|ement)|reduc(?:e|es|ed|tion)|achiev(?:e|es|ed)|higher\s+(?:accuracy|performance)|lower\s+(?:error|cost)|comparable\s+performance)\b', re.I)),
    ('limitations', '局限与适用范围', re.compile(r'\b(?:however|limitation(?:s)?|challeng(?:e|es|ing)|trade[- ]off|at\s+(?:substantially\s+)?(?:high|higher)\s+\w*\s*cost|fails?\s+to|not\s+(?:evaluate|tested|considered)|restricted\s+to|only\s+(?:tested|evaluated))\b', re.I)),
)
_LABELS = {key: label for key, label, _ in _CUES}


def abstract_sentences(abstract: str) -> list[str]:
    """Split conventional scholarly prose without breaking decimal numbers."""
    text = ' '.join(re.sub(r'<[^>]*>', ' ', str(abstract or '')).split())
    if not text:
        return []
    # Respect sentence-ending punctuation while keeping numerical decimals.
    pieces = re.split(r'(?<=[.!?。！？])\s+(?=[A-Z\u4e00-\u9fff])', text)
    return [part.strip() for part in pieces if len(part.strip()) >= 38]


def extract_abstract_insights(papers: list[dict], selected: list[dict], *, max_per_type: int = 4, max_papers: int = 16) -> list[dict]:
    """Return exact, source-labeled abstract passages relevant to a query.

    No PDF claim/anchor is created. The result is safe to show even if no PDF
    exists and no LLM is configured. All citations point to selected paper IDs.
    """
    by_id = {str(row['paper_id']): row for row in papers}
    seen: set[tuple[str, str]] = set()
    buckets: dict[str, list[dict]] = {kind: [] for kind in _LABELS}
    # Limit to relevant abstracts, and avoid drowning the screen in generic snippets.
    for candidate in selected[:max_papers]:
        pid = str(candidate['paper_id'])
        source = by_id.get(pid)
        if not source:
            continue
        for sentence in abstract_sentences(str(source.get('abstract') or '')):
            if len(sentence) > 820:
                continue  # An unsegmented, large paragraph should not be called a sentence.
            for key, _, cue in _CUES:
                if not cue.search(sentence) or (pid, key) in seen or len(buckets[key]) >= max_per_type:
                    continue
                # Preserve original text, source paper and metadata-level status.
                buckets[key].append({'paper_id': pid, 'title': str(source.get('title') or '')[:230],
                    'kind': key, 'label': _LABELS[key], 'quote': sentence,
                    'source': 'paper_abstract', 'verification': 'metadata_only_unverified'})
                seen.add((pid, key))
                break
    return [item for key in _LABELS for item in buckets[key]]

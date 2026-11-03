# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Zotero collection records used only for local, read-only research profiles."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Collection:
    key: str
    name: str
    parent_key: str = ""
    library_id: str = "user"
    path: str = ""


@dataclass(frozen=True)
class Reference:
    key: str
    title: str
    abstract: str = ""
    tags: list[str] = field(default_factory=list)
    authors: str = ""
    date: str = ""
    doi: str = ""


@dataclass
class ResearchProfile:
    collection_key: str
    collection_name: str
    references: list[Reference]
    keywords: list[str]
    source: str
    total_items: int = 0

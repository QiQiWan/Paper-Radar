# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Shared literature record. New source adapters return Paper objects."""
from dataclasses import dataclass

@dataclass
class Paper:
    paper_id: str
    title: str
    abstract: str
    authors: str
    published: str
    updated: str
    category: str
    url: str
    pdf_url: str
    score: float = 0.0
    summary: str = ""
    first_seen: str = ""
    is_starred: int = 0
    reading_status: str = "unread"
    source: str = "arxiv"
    doi: str = ""
    venue: str = ""
    is_oa: int = 0
    oa_license: str = ""
    download_url: str = ""
    local_file: str = ""
    download_status: str = ""
    download_error: str = ""
    downloaded_at: str = ""


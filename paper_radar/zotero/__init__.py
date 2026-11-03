# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Read-only Zotero integration: Local API and SQLite fallback."""
from .connector import ZoteroReader, ZoteroConnectionError, detect_database
from .models import Collection, Reference, ResearchProfile
from .keywords import extract_keywords, SimilarityRanker


def build_profile(mode:str,db_path:str,collection_key:str,include_children=True,keyword_limit=10):
    reader=ZoteroReader(mode,db_path)
    collections=reader.collections()
    chosen=next((c for c in collections if c.key==collection_key),None)
    if not chosen:
        raise ZoteroConnectionError('所选收藏夹不存在，请重新选择。')
    refs,total=reader.references(collection_key,include_children)
    if not refs:
        raise ZoteroConnectionError('收藏夹中没有带标题的文献。请先在 Zotero 中添加论文。')
    return ResearchProfile(collection_key,chosen.path or chosen.name,refs,
                           extract_keywords(refs,keyword_limit),reader.backend,total)

# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Download engines. A stored Paper is the only input to URL selection."""
from __future__ import annotations
from typing import Protocol
from ..models import Paper

class DownloadEngine(Protocol):
    name: str
    def supports(self, paper: Paper) -> bool: ...
    def url_for(self, paper: Paper) -> str: ...

_ENGINES: dict[str, type] = {}

def register(cls):
    if cls.name in _ENGINES:
        raise ValueError('下载引擎重复注册')
    _ENGINES[cls.name] = cls
    return cls

def resolve(paper: Paper) -> tuple[str, str] | None:
    for name, cls in _ENGINES.items():
        engine = cls()
        if engine.supports(paper):
            return (name, engine.url_for(paper))
    return None

def available_downloaders() -> list[str]:
    return list(_ENGINES)

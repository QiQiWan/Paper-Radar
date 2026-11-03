# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Built-in source plugins. Importing modules registers them."""
from .base import SourceAdapter, register, available_sources, fetch_enabled
from .arxiv import ArxivSource
from .openalex import OpenAlexSource
register(ArxivSource)
register(OpenAlexSource)

from .crossref import CrossrefSource
from .springer import SpringerSource
from .elsevier import ElsevierSource
register(CrossrefSource)
register(SpringerSource)
register(ElsevierSource)

from .sciencedirect import ScienceDirectSource
register(ScienceDirectSource)

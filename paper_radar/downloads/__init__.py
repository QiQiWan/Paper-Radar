# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
from .base import register, resolve, available_downloaders
from .arxiv import ArxivPDF
from .oa import OpenAccessPDF
register(ArxivPDF)
register(OpenAccessPDF)

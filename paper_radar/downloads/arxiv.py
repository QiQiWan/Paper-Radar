# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""arXiv public preprint PDF engine."""
from urllib.parse import urlsplit
from ..models import Paper

class ArxivPDF:
    name='arxiv_pdf'
    def supports(self,p:Paper)->bool:
        url = p.download_url or p.pdf_url
        return 'arxiv' in p.source.split(',') and urlsplit(url).hostname in {'arxiv.org','export.arxiv.org'}
    def url_for(self,p:Paper)->str:
        return p.download_url or p.pdf_url

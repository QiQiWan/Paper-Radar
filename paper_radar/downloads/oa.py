# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Direct open-access PDF URL from a trusted metadata source."""
from urllib.parse import urlsplit
from ..models import Paper

class OpenAccessPDF:
    name='open_access_pdf'
    def supports(self,p:Paper)->bool:
        return bool(p.is_oa and p.download_url and urlsplit(p.download_url).scheme=='https')
    def url_for(self,p:Paper)->str:
        return p.download_url

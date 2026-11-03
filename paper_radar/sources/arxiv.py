# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""arXiv Atom API adapter. Retains the original query and parsing behavior."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from urllib import parse, request, error
import re
import time
import xml.etree.ElementTree as ET
from ..models import Paper

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

def build_query(config: dict, now: datetime | None = None) -> str:
    """Use arXiv's documented boolean, phrase, category and submittedDate grammar."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    since = now - timedelta(days=config["lookback_days"])
    clauses = []
    terms = []
    for keyword in config["keywords"][:12]:
        term = keyword.replace('"', '').replace('\\', '').strip()
        if term:
            # Prefix each field separately, allowing an exact multi-word phrase.
            terms.extend([f'ti:"{term}"', f'abs:"{term}"'])
    if terms:
        clauses.append("(" + " OR ".join(terms) + ")")
    cats = [f"cat:{cat}" for cat in config["categories"][:16] if re.fullmatch(r"[a-zA-Z.\-]+", cat)]
    if cats:
        clauses.append("(" + " OR ".join(cats) + ")")
    clauses.append("submittedDate:[" + since.strftime("%Y%m%d%H%M") +
                   " TO " + now.strftime("%Y%m%d%H%M") + "]")
    return " AND ".join(clauses)


def parse_atom(data: bytes) -> list[Paper]:
    root = ET.fromstring(data)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        def get(tag: str) -> str:
            return " ".join((entry.findtext(f"{ATOM}{tag}") or "").split())
        title, abstract = get("title"), get("summary")
        url = get("id")
        if not title or not url:
            continue
        ident = url.split("/abs/")[-1].split("?")[0]
        ident = re.sub(r"v\d+$", "", ident)
        pdf_url = ""
        for link in entry.findall(f"{ATOM}link"):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href", "")
        primary = entry.find(f"{ARXIV}primary_category")
        doi = (entry.findtext(f"{ARXIV}doi") or '').lower().strip()
        cat = primary.attrib.get("term", "") if primary is not None else ""
        authors = ", ".join((a.findtext(f"{ATOM}name") or "").strip()
                            for a in entry.findall(f"{ATOM}author"))
        authors = ", ".join(a for a in authors.split(", ") if a)
        papers.append(Paper(
            paper_id=ident, title=title, abstract=abstract,
            authors=authors, published=get("published"),
            updated=get("updated"), category=cat, url=url,
            pdf_url=pdf_url or f"https://arxiv.org/pdf/{ident}",
            source="arxiv", doi=doi, is_oa=1,
            download_url=pdf_url or f"https://arxiv.org/pdf/{ident}",
        ))
    return papers


def fetch_arxiv(config: dict, logger=print, now: datetime | None = None) -> list[Paper]:
    query = build_query(config, now)
    output = {}
    page_size = min(100, config["max_results"])
    for start in range(0, config["max_results"], page_size):
        if start:
            time.sleep(3.2)  # arXiv requests at least three seconds apart
        params = parse.urlencode({
            "search_query": query, "start": start,
            "max_results": min(page_size, config["max_results"] - start),
            "sortBy": "submittedDate", "sortOrder": "descending",
        })
        url = "https://export.arxiv.org/api/query?" + params
        req = request.Request(url, headers={
            "User-Agent": "Paper-Radar/1.9 (local literature research; arXiv API)",
            "Accept": "application/atom+xml",
        })
        logger(f"检索 arXiv（第 {start // page_size + 1} 页）")
        raw = None
        for attempt in range(3):
            try:
                with request.urlopen(req, timeout=35) as resp:
                    raw = resp.read()
                break
            except (error.HTTPError, error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"arXiv 检索失败：{exc}") from exc
                logger("arXiv 暂时无法访问，正在重试。")
                time.sleep(3.5 * (attempt + 1))
        batch = parse_atom(raw)
        for paper in batch:
            output[paper.paper_id] = paper
        if len(batch) < min(page_size, config["max_results"] - start):
            break
    if len(output) >= config["max_results"]:
        logger("结果达到检索上限，可在更多设置中调整。")
    return list(output.values())


class ArxivSource:
    name = 'arxiv'
    label = 'arXiv 预印本'
    access = 'public'
    guide_url = 'https://info.arxiv.org/help/api/index.html'
    note = '开放预印本检索，公开 PDF 可直接下载。'
    def fetch(self, config, logger=print, now=None):
        return fetch_arxiv(config, logger=logger, now=now)

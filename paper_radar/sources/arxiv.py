# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""arXiv Atom API adapter. Retains the original query and parsing behavior."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from urllib import parse, request, error
import re
import time
from time import monotonic as _monotonic
import xml.etree.ElementTree as ET
from .. import __version__
from ..models import Paper
from .request_policy import request_timeout, is_configuration_network_error

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

def build_query(config: dict, now: datetime | None = None) -> str:
    """Use arXiv's documented boolean, phrase, category and submittedDate grammar."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    since = now - timedelta(days=config["lookback_days"])
    if config.get('_window_start'):
        since = datetime.fromisoformat(config['_window_start'].replace('Z','+00:00')).astimezone(timezone.utc)
    if config.get('_window_end'):
        now = datetime.fromisoformat(config['_window_end'].replace('Z','+00:00')).astimezone(timezone.utc)
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
    started = _monotonic()
    wall_budget = float(config.get('_source_wall_timeout_seconds',0) or 0)
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
            "User-Agent": f"Paper-Radar/{__version__} (local literature research; arXiv API)",
            "Accept": "application/atom+xml",
        })
        logger(f"检索 arXiv（第 {start // page_size + 1} 页）")
        raw = None
        for attempt in range(2):
            try:
                with request.urlopen(req, timeout=request_timeout(config,26)) as resp:
                    raw = resp.read()
                break
            except (error.HTTPError, error.URLError, TimeoutError) as exc:
                if is_configuration_network_error(exc):
                    if output:
                        from .supervisor import SourceBatch
                        return SourceBatch(list(output.values()), partial=True,
                                           warning_code='SOURCE_PARTIAL')
                    raise RuntimeError('arXiv 网络解析、证书或代理失败') from exc
                if isinstance(exc, error.HTTPError) and 400 <= exc.code < 500 and exc.code != 429:
                    raise RuntimeError(f"arXiv 拒绝查询（HTTP {exc.code}）") from None
                if attempt == 1:
                    raise RuntimeError(f"arXiv 检索失败：{exc}") from exc
                logger("arXiv 暂时无法访问，正在重试。")
                time.sleep(1.2 * (attempt + 1))
        batch = parse_atom(raw)
        for paper in batch:
            output[paper.paper_id] = paper
        if len(batch) < min(page_size, config["max_results"] - start):
            break
    if len(output) >= config["max_results"]:
        logger("结果达到检索上限，可在更多设置中调整。")
    # Explicit ID polling finds new versions of followed papers even when the
    # original submission is older than the normal date window. The official
    # API has documented id_list version handling; do not guess an unsupported
    # lastUpdatedDate range filter.
    watched=[str(x) for x in config.get('_watch_arxiv_ids', [])
             if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-zA-Z.\-]+/\d{7})',str(x))][:24]
    if watched:
        from .supervisor import SourceBatch
        def incomplete():
            logger('arXiv 主检索已完成，版本更新检查留待下一轮。')
            return SourceBatch(list(output.values()), partial=True,warning_code='SOURCE_PARTIAL')
        # An optional version request must never consume the remaining child
        # process budget and cause the successful main query to be discarded.
        # Allow 3.2 s for arXiv spacing, 2 s to connect, 1 s to serialize.
        if wall_budget and wall_budget-(_monotonic()-started) < 6.5:
            return incomplete()
        time.sleep(3.2)  # Respect the arXiv request spacing.
        remaining = wall_budget - (_monotonic()-started) if wall_budget else 0
        if wall_budget and remaining < 3.0:
            return incomplete()
        version_timeout = min(request_timeout(config,26), max(2.0,remaining-1.0)) if wall_budget else request_timeout(config,26)
        req=request.Request('https://export.arxiv.org/api/query?'+parse.urlencode({
            'id_list':','.join(watched),'max_results':len(watched),
            'sortBy':'lastUpdatedDate','sortOrder':'descending'}),headers={
            'User-Agent':f'Paper-Radar/{__version__} (followed paper version check)',
            'Accept':'application/atom+xml'})
        try:
            with request.urlopen(req,timeout=version_timeout) as response:
                for paper in parse_atom(response.read()):
                    # Prefer the more recent Atom update timestamp.
                    old=output.get(paper.paper_id)
                    if old is None or str(old.updated or '')<str(paper.updated or ''):
                        output[paper.paper_id]=paper
        except (error.HTTPError,error.URLError,TimeoutError,OSError,ET.ParseError):
            # Preserve the main query and leave the follow-up checkpoint pending.
            return incomplete()
    return list(output.values())


class ArxivSource:
    name = 'arxiv'
    label = 'arXiv 预印本'
    access = 'public'
    guide_url = 'https://info.arxiv.org/help/api/index.html'
    note = '开放预印本检索，公开 PDF 可直接下载。'
    def fetch(self, config, logger=print, now=None):
        return fetch_arxiv(config, logger=logger, now=now)

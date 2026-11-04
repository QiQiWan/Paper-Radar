# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Configuration, local paper storage and pipeline coordination."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from urllib import parse, request, error
import html
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
APP_ROOT = Path(__file__).resolve().parent.parent
APP_CONFIG = APP_ROOT / "settings.json"
DB_FILE = APP_ROOT / "data" / "papers.sqlite3"
LOCK_FILE = APP_ROOT / "data" / "running.lock"
DEFAULTS = {
    "keywords": ["neural operator", "operator learning", "physics informed"],
    "categories": [],
    "lookback_days": 7,
    "max_results": 30,
    "report_limit": 30,
    "interval_hours": 24,
    "schedule_time": "08:30",
    "ai_enabled": False,
    "ai_limit": 5,
    "api_provider": "DeepSeek",
    "api_base_url": "https://api.deepseek.com",
    "api_model": "deepseek-flash",
    "output_dir": "reports",
    "sort_mode": "relevance",
    "sources": ["arxiv", "openalex", "crossref"],
    "auto_download": False,
    "download_limit": 3,
    "max_pdf_mb": 30,
    "zotero_enabled": False,
    "zotero_mode": "auto",
    "zotero_db_path": "",
    "zotero_collection_key": "",
    "zotero_include_children": True,
    "zotero_refresh_keywords": False,
    "zotero_keyword_limit": 8,
    "max_queries_per_source": 8,
    "update_check_enabled": True,
    "update_check_interval_hours": 24,
    "dismissed_update_tag": "",
    "active_topic_id": "default",
    "topics": [],
}

# Fields that belong to a research topic; model credentials, updates and
# default output folder remain global. Existing flat settings stay compatible.
TOPIC_FIELDS = (
    'keywords', 'categories', 'sources', 'lookback_days', 'max_results',
    'report_limit', 'sort_mode', 'max_queries_per_source', 'zotero_enabled',
    'zotero_mode', 'zotero_db_path', 'zotero_collection_key',
    'zotero_include_children', 'zotero_refresh_keywords', 'zotero_keyword_limit',
)
TOPIC_ID_RE = re.compile(r'^[a-z0-9_-]{1,48}$')



from .models import Paper
from .quality import score_relevance, valid_recent_publication, matched_keywords


def read_config(path: Path = APP_CONFIG) -> dict:
    config = dict(DEFAULTS)
    if path.exists():
        content = json.loads(path.read_text(encoding="utf-8"))
        config.update({k: v for k, v in content.items() if k in DEFAULTS})
    return validate_config(config)


def validate_config(config: dict) -> dict:
    c = dict(config)
    for key in ("keywords", "categories", "sources"):
        raw = c.get(key, [])
        if isinstance(raw, str):
            raw = re.split(r"[,，;；\n]", raw)
        if not isinstance(raw, (tuple, list)):
            raise ValueError(f'{key} 必须是列表。')
        c[key] = list(dict.fromkeys(str(x).strip()[:120] for x in raw if str(x).strip()))
        if len(c[key]) > (20 if key == 'keywords' else 24):
            raise ValueError(f'{key} 数量过多，请精简后重试。')
    from .sources import available_sources
    names = {s['name'] for s in available_sources()}
    c['sources'] = list(dict.fromkeys(c['sources']))
    if not c['sources'] or any(src not in names for src in c['sources']):
        raise ValueError('请选择有效的文献来源。')
    if not c["keywords"] and not c["categories"]:
        raise ValueError("请输入关键词或 arXiv 分类。")
    for k, lo, hi in (("lookback_days", 1, 60), ("max_results", 1, 500),
                      ("report_limit", 1, 100), ("interval_hours", 1, 168),
                      ("ai_limit", 1, 30), ("download_limit", 1, 20), ("max_pdf_mb", 1, 50), ("zotero_keyword_limit", 1, 15), ("max_queries_per_source", 1, 20), ("update_check_interval_hours", 6, 168)):
        c[k] = max(lo, min(hi, int(c.get(k, DEFAULTS[k]))))
    c["ai_enabled"] = bool(c.get("ai_enabled", False))
    c["auto_download"] = bool(c.get("auto_download", False))
    c["zotero_enabled"] = bool(c.get("zotero_enabled", False))
    c["zotero_include_children"] = bool(c.get("zotero_include_children", True))
    c["zotero_refresh_keywords"] = bool(c.get("zotero_refresh_keywords", DEFAULTS["zotero_refresh_keywords"]))
    c["zotero_mode"] = str(c.get("zotero_mode", "auto"))
    if c["zotero_mode"] not in ("auto", "api", "sqlite"):
        raise ValueError("Zotero 连接方式无效。")
    c["zotero_db_path"] = str(c.get("zotero_db_path") or "").strip()[:2000]
    c["zotero_collection_key"] = str(c.get("zotero_collection_key") or "").strip()[:64]
    if c["zotero_enabled"] and not c["zotero_collection_key"]:
        raise ValueError("请先在 Zotero 页面选择一个收藏夹。")
    c["schedule_time"] = str(c.get("schedule_time", "08:30"))
    datetime.strptime(c["schedule_time"], "%H:%M")
    from .cloud_api import PRESETS, completion_endpoint
    c["api_provider"] = str(c.get("api_provider", "DeepSeek")).strip()
    if c["api_provider"] not in PRESETS:
        raise ValueError("请选择 API 服务商。")
    c["api_base_url"] = str(c.get("api_base_url", "")).strip()
    c["api_model"] = str(c.get("api_model", "")).strip()
    if c["ai_enabled"]:
        completion_endpoint(c["api_base_url"])
        if not c["api_model"]:
            raise ValueError("请填写模型名称。")
    c["output_dir"] = str(c.get("output_dir", "reports")).strip() or "reports"
    c["sort_mode"] = "newest" if c.get("sort_mode") == "newest" else "relevance"
    c['update_check_enabled'] = bool(c.get('update_check_enabled', True))
    c['dismissed_update_tag'] = str(c.get('dismissed_update_tag') or '')[:40]
    from copy import deepcopy
    raw_topics = c.get('topics') or []
    if not isinstance(raw_topics, list) or len(raw_topics) > 20:
        raise ValueError('研究主题配置格式不正确，最多允许20个主题。')
    entries = []
    used = set()
    for raw in raw_topics:
        if not isinstance(raw, dict):
            raise ValueError('研究主题配置条目无效。')
        tid = str(raw.get('id') or '')
        if not TOPIC_ID_RE.fullmatch(tid) or tid in used:
            raise ValueError('研究主题标识无效或重复。')
        name = str(raw.get('name') or '').strip()
        if not 1 <= len(name) <= 55:
            raise ValueError('研究主题名称需要1至55个字符。')
        used.add(tid)
        # Missing fields in older saved snapshots inherit the current settings.
        item = {'id': tid, 'name': name}
        for field in TOPIC_FIELDS:
            item[field] = deepcopy(raw.get(field, c[field]))
        entries.append(item)
    if not entries:
        entries = [{'id': 'default', 'name': '默认研究主题',
                    **{field: deepcopy(c[field]) for field in TOPIC_FIELDS}}]
    active = str(c.get('active_topic_id') or 'default')
    if active not in {item['id'] for item in entries}:
        active = entries[0]['id']
    c['active_topic_id'] = active
    c['topics'] = entries
    return c


def save_config(config: dict, path: Path = APP_CONFIG) -> dict:
    c = validate_config(config)
    from copy import deepcopy
    for topic in c['topics']:
        if topic['id'] == c['active_topic_id']:
            topic.update({field: deepcopy(c[field]) for field in TOPIC_FIELDS})
            break
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return c


def get_output_dir(config: dict) -> Path:
    p = Path(config["output_dir"]).expanduser()
    base = p if p.is_absolute() else APP_ROOT / p
    active = str(config.get('active_topic_id') or 'default')
    if active == 'default':
        return base  # Preserve all pre-v1.10 reports at their original paths.
    if not TOPIC_ID_RE.fullmatch(active):
        raise ValueError('研究主题标识无效。')
    return base / 'themes' / active


def normalize_doi(value: str) -> str:
    """Canonicalize DOI URL / prefix variants for cross-source matching."""
    value = parse.unquote(str(value or '').strip()).casefold()
    value = re.sub(r'^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)', '', value)
    return value.split('?', 1)[0].split('#', 1)[0].strip().rstrip(' .')


def richer_abstract(existing: str, incoming: str) -> str:
    """Preserve the more informative source abstract on metadata upsert."""
    old, new = str(existing or ''), str(incoming or '').strip()
    if not new:
        return old
    # Crossref/other index abstracts can be shorter metadata snippets. Longer
    # text is a conservative proxy for completeness; retain cached analysis if
    # a shorter copy arrives from a secondary source.
    return new if len(new) > len(old.strip()) else old


def richer_title(existing: str, incoming: str) -> str:
    old, new = (existing or '').strip(), (incoming or '').strip()
    return new if len(new) > len(old) else old


# Backwards-compatible exports; source logic lives in sources/arxiv.py.
from .sources.arxiv import build_query, parse_atom, fetch_arxiv


def rank(paper: Paper, keywords: list[str], ranker=None) -> float:
    """Explainable score: specific research terms first, Zotero similarity second."""
    return score_relevance(paper.title, paper.abstract, keywords, ranker, paper)


def prepare_zotero(config:dict,logger=print):
    """Refresh the configured Zotero profile for scheduled and manual searches."""
    effective=dict(config)
    if not config.get('zotero_enabled'):
        return effective,None
    from .zotero import build_profile,SimilarityRanker
    try:
        profile=build_profile(config['zotero_mode'],config['zotero_db_path'],
                              config['zotero_collection_key'],config['zotero_include_children'],
                              config['zotero_keyword_limit'])
    except Exception as exc:
        logger(f'Zotero 收藏夹读取失败，继续使用已保存的关键词：{exc}')
        return effective,None
    if config.get('zotero_refresh_keywords') and profile.keywords:
        effective['keywords']=profile.keywords
    effective['zotero_collection_label']=profile.collection_name
    effective['zotero_reference_count']=len(profile.references)
    effective['zotero_keywords']=profile.keywords
    logger(f'Zotero：已读取 {len(profile.references)} 篇文献，提炼 {len(profile.keywords)} 个关键词。')
    return effective,SimilarityRanker(profile.references)


def connect(db_path: Path = DB_FILE) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS papers (
      paper_id TEXT PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL,
      authors TEXT, published TEXT, updated TEXT, category TEXT,
      url TEXT, pdf_url TEXT, score REAL NOT NULL DEFAULT 0,
      summary TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL,
      is_starred INTEGER NOT NULL DEFAULT 0, reading_status TEXT NOT NULL DEFAULT 'unread'
    )""")
    columns = {row[1] for row in conn.execute('PRAGMA table_info(papers)')}
    if 'is_starred' not in columns:
        conn.execute('ALTER TABLE papers ADD COLUMN is_starred INTEGER NOT NULL DEFAULT 0')
    if 'reading_status' not in columns:
        conn.execute("ALTER TABLE papers ADD COLUMN reading_status TEXT NOT NULL DEFAULT 'unread'")
    upgrades = {
        'source': "TEXT NOT NULL DEFAULT 'arxiv'",
        'doi': "TEXT NOT NULL DEFAULT ''",
        'venue': "TEXT NOT NULL DEFAULT ''",
        'is_oa': "INTEGER NOT NULL DEFAULT 0",
        'oa_license': "TEXT NOT NULL DEFAULT ''",
        'download_url': "TEXT NOT NULL DEFAULT ''",
        'local_file': "TEXT NOT NULL DEFAULT ''",
        'download_status': "TEXT NOT NULL DEFAULT ''",
        'download_error': "TEXT NOT NULL DEFAULT ''",
        'downloaded_at': "TEXT NOT NULL DEFAULT ''",
        'summary_fingerprint': "TEXT NOT NULL DEFAULT ''",
        'summary_model': "TEXT NOT NULL DEFAULT ''",
    }
    for key, definition in upgrades.items():
        if key not in columns:
            conn.execute(f'ALTER TABLE papers ADD COLUMN {key} {definition}')
    # Older v1.2 records predate the PDF field; restore arXiv download links.
    conn.execute("""UPDATE papers SET download_url=pdf_url,is_oa=1
        WHERE download_url='' AND source='arxiv'
          AND (pdf_url LIKE 'https://arxiv.org/pdf/%' OR pdf_url LIKE 'https://export.arxiv.org/pdf/%')""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published)')
    conn.commit()
    return conn


@contextmanager
def open_database(db_path: Path = DB_FILE):
    connection = connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def save_papers(conn: sqlite3.Connection, papers: list[Paper]) -> list[Paper]:
    """Idempotent upsert with DOI matching across source adapters.

    Existing local annotations and downloaded file state are preserved.
    """
    inserted = []
    seen_at = datetime.now().isoformat(timespec='seconds')
    for p in papers:
        p.doi = normalize_doi(p.doi)
        prior = conn.execute('SELECT * FROM papers WHERE paper_id=?', (p.paper_id,)).fetchone()
        if prior is None and p.doi:
            # Old records sometimes store DOI URLs; avoid duplicate rows when
            # a later data source returns the canonical DOI form.
            prior = conn.execute("SELECT * FROM papers WHERE lower(doi) IN (?,?,?) LIMIT 1",
                                 (p.doi, 'https://doi.org/' + p.doi,
                                  'http://dx.doi.org/' + p.doi)).fetchone()
        if prior:
            pid = prior['paper_id']
            p.paper_id = pid  # Preserve canonical local identifier on DOI matches.
            # Prefer the richer abstract, preserve user flags and local files.
            effective_abstract = richer_abstract(prior['abstract'], p.abstract)
            effective_title = richer_title(prior['title'], p.title)
            changed = (effective_abstract != prior['abstract'] or effective_title != prior['title'])
            fields = {
                'title': effective_title, 'abstract':effective_abstract,
                'authors':p.authors or prior['authors'],
                'published':p.published or prior['published'],
                'updated':p.updated or prior['updated'],
                'category':p.category or prior['category'],
                'url':prior['url'] or p.url, 'pdf_url':p.pdf_url or prior['pdf_url'],
                'score':max(p.score, prior['score']),
                'source':','.join(dict.fromkeys(x for x in (prior['source'] + ',' + p.source).split(',') if x)),
                'doi':p.doi or prior['doi'], 'venue':p.venue or prior['venue'],
                'is_oa':int(bool(p.is_oa or prior['is_oa'])),
                'oa_license':p.oa_license or prior['oa_license'],
                'download_url':p.download_url or prior['download_url'],
                'summary': '' if changed else prior['summary'],
                'summary_fingerprint': '' if changed else prior['summary_fingerprint'],
                'summary_model': '' if changed else prior['summary_model'],
            }
            assignments = ','.join(f'{k}=?' for k in fields)
            conn.execute(f'UPDATE papers SET {assignments} WHERE paper_id=?', (*fields.values(),pid))
        else:
            p.first_seen = seen_at
            keys = ('paper_id','title','abstract','authors','published','updated','category','url',
                    'pdf_url','score','summary','first_seen','is_starred','reading_status',
                    'source','doi','venue','is_oa','oa_license','download_url',
                    'local_file','download_status','download_error','downloaded_at',
                    'summary_fingerprint','summary_model')
            conn.execute(f'INSERT INTO papers ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',
                         tuple(getattr(p,k) for k in keys))
            inserted.append(p)
    conn.commit()
    return inserted


def get_recent(conn: sqlite3.Connection, days=7, limit=20,
               keywords: list[str] | None = None, now: datetime | None = None,
               sort_mode: str = "relevance", ranker=None) -> list[Paper]:
    """Read recent saved papers and score against the current topic."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today = current.astimezone(timezone.utc).date()
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = conn.execute("SELECT * FROM papers WHERE substr(published,1,10)>=? AND substr(published,1,10)<=?", (cutoff, today.isoformat())).fetchall()
    papers = [Paper(**dict(row)) for row in rows if valid_recent_publication(row['published'], current, days)]
    if ranker is not None:
        papers = [paper for paper in papers if not ranker.already_collected(paper)]
    if keywords is not None:
        for paper in papers:
            paper.score = rank(paper, keywords, ranker)
        if keywords:
            papers = [paper for paper in papers if paper.score > 0]
    if sort_mode == "newest":
        papers.sort(key=lambda p: (p.published, p.score), reverse=True)
    else:
        papers.sort(key=lambda p: (p.score, p.published), reverse=True)
    return papers[:limit]


def get_selected(conn: sqlite3.Connection, ids: list[str]) -> list[Paper]:
    return [Paper(**dict(row)) for pid in ids if (row := conn.execute(
        "SELECT * FROM papers WHERE paper_id=?", (pid,)).fetchone()) is not None]


def annotate_papers(conn: sqlite3.Connection, config: dict, logger=print, now: datetime | None = None, ranker=None, progress=None) -> int:
    """Summaries are best effort; errors do not prevent Word export."""
    if not config["ai_enabled"]:
        return 0
    from .cloud_api import get_api_key, summarize_paper, summary_fingerprint, validate_summary_evidence
    if not get_api_key(config["api_provider"]):
        logger("未配置 API Key，本次跳过中文速览。")
        return 0
    recent = get_recent(conn, days=config["lookback_days"],
                        limit=max(config["report_limit"], config["ai_limit"]),
                        keywords=config["keywords"], now=now, sort_mode=config.get("sort_mode", "relevance"),ranker=ranker)
    candidates = [p for p in recent if p.abstract and (
        not p.summary or p.summary_fingerprint != summary_fingerprint(p, config)
    )][:config["ai_limit"]]
    successes = 0
    if progress:
        progress(0, len(candidates), '正在准备中文速览')
    for i, paper in enumerate(candidates, start=1):
        try:
            logger(f"生成中文速览 {i}/{len(candidates)}：{paper.title[:40]}")
            result = summarize_paper(paper, config)
            if result:
                problems = validate_summary_evidence(result, paper)
                # Reject unsupported quantitative assertions. Older models may
                # omit section headings; report that separately without
                # discarding an otherwise useful qualitative explanation.
                if any(p.startswith('定量信息') for p in problems):
                    logger('速览包含未经原摘要支持的量化信息，保留原始摘要：' + '；'.join(problems[:3]))
                    continue
                if problems:
                    logger('速览结构需人工核查：' + '；'.join(problems[:2]))
                conn.execute("UPDATE papers SET summary=?,summary_fingerprint=?,summary_model=? WHERE paper_id=?",
                             (result, summary_fingerprint(paper, config),
                              config['api_provider'] + '/' + config['api_model'], paper.paper_id))
                conn.commit()
                successes += 1
        except Exception as exc:
            logger(f"中文速览失败：{exc}")
        finally:
            if progress:
                progress(i, len(candidates), paper.title[:75])
    return successes


@contextmanager
def single_instance(lock_path: Path = LOCK_FILE):
    """Cross-process OS file lock; released automatically after crashes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as file:
        try:
            if os.name == "nt":
                import msvcrt
                file.seek(0)
                file.write(b"0")
                file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("已有检索任务在运行，请稍后再试。") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _library_filter(query: str = "", only_starred: bool = False,
                    reading_status: str = "") -> tuple[str, list]:
    """Shared, parameterized filtering for the page and its total count."""
    pieces = ["1=1"]
    args: list = []
    if query.strip():
        pieces.append("(title LIKE ? OR abstract LIKE ? OR authors LIKE ? OR doi LIKE ?)")
        needle = "%" + query.strip()[:100].replace("%", "\\%").replace("_", "\\_") + "%"
        pieces[-1] = pieces[-1].replace(" LIKE ?", " LIKE ? ESCAPE '\\'")
        args.extend([needle] * 4)
    if only_starred:
        pieces.append("is_starred=1")
    if reading_status in {"unread", "reading", "done"}:
        pieces.append("reading_status=?")
        args.append(reading_status)
    return " AND ".join(pieces), args


def count_library(conn: sqlite3.Connection, *, query: str = "", only_starred: bool = False,
                  reading_status: str = "") -> int:
    clause, args = _library_filter(query, only_starred, reading_status)
    return int(conn.execute("SELECT COUNT(*) FROM papers WHERE " + clause, args).fetchone()[0])


def list_library(conn: sqlite3.Connection, *, limit: int = 300, offset: int = 0,
                 query: str = "", only_starred: bool = False,
                 reading_status: str = "", sort: str = "default") -> list[Paper]:
    """Paged local library. Sort ties by stable paper ID so pages never skip rows."""
    clause, args = _library_filter(query, only_starred, reading_status)
    order = ("first_seen DESC, paper_id ASC" if sort == "recent" else
             "score DESC, published DESC, paper_id ASC" if sort == "relevance" else
             "is_starred DESC, published DESC, paper_id ASC")
    args.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
    rows = conn.execute("SELECT * FROM papers WHERE " + clause +
                        f" ORDER BY {order} LIMIT ? OFFSET ?", args).fetchall()
    return [Paper(**dict(r)) for r in rows]


def update_paper_flags(conn: sqlite3.Connection, paper_id: str, *, starred=None, status=None) -> bool:
    row = conn.execute("SELECT paper_id FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
    if row is None:
        return False
    if starred is not None:
        conn.execute("UPDATE papers SET is_starred=? WHERE paper_id=?", (int(bool(starred)), paper_id))
    if status is not None:
        if status not in {"unread", "reading", "done"}:
            raise ValueError("阅读状态无效")
        conn.execute("UPDATE papers SET reading_status=? WHERE paper_id=?", (status, paper_id))
    conn.commit()
    return True


def run_cycle(config: dict | None = None, logger=print, *,
              db_path: Path = DB_FILE, fetcher=fetch_arxiv, now: datetime | None = None,
              progress=None) -> dict:
    """One automated cycle; network failure never creates a false-success report."""
    from .report import write_report, write_markdown, write_csv
    from .progress import TaskProgress
    task = TaskProgress(progress)
    task.emit(1, "准备运行", detail="检查检索范围与报告目录")
    config = validate_config(config or read_config())
    folder = get_output_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    with single_instance(db_path.parent / "running.lock"):
        task.emit(5, "Zotero 收藏夹", detail="正在读取参考文献；无需 Zotero 时将直接跳过")
        config, profile_ranker = prepare_zotero(config,logger=logger)
        task.emit(12, "检索文献", detail="正在连接文献来源")
        new: list[Paper] = []
        if fetcher is fetch_arxiv:
            from .sources import fetch_enabled
            def source_progress(done, total, detail):
                task.emit(12 + 33 * done / max(1, total), '检索文献',
                          current=done, total=total, detail=detail)
            retrieved = fetch_enabled(config, logger=logger, now=now, progress=source_progress)
        else:
            retrieved = fetcher(config, logger=logger, now=now)
        task.emit(45, '整理文献', detail=f'检索完成，共获取 {len(retrieved)} 条记录')
        original_count = len(retrieved)
        retrieved = [p for p in retrieved if valid_recent_publication(
            p.published, now, config['lookback_days'])]
        dropped_dates = original_count - len(retrieved)
        if dropped_dates:
            logger(f'日期质量检查：{dropped_dates} 篇未来、过期或日期不完整的题录不进入本期结果。')
        if profile_ranker is not None:
            count_before=len(retrieved)
            retrieved=[paper for paper in retrieved if not profile_ranker.already_collected(paper)]
            if count_before>len(retrieved):
                logger(f'已排除 Zotero 收藏夹中已有的 {count_before-len(retrieved)} 篇论文。')
        for paper in retrieved:
            paper.score = rank(paper, config["keywords"], profile_ranker)
        if config['keywords']:
            before_rank = len(retrieved)
            retrieved = [p for p in retrieved if p.score > 0]
            removed = before_rank - len(retrieved)
            if removed:
                logger(f'研究相关性检查：{removed} 篇仅匹配宽泛词或缺少研究主题证据的题录已排除。')
        config['_quality_filters'] = {'date_removed': dropped_dates,
                                      'topic_removed': original_count - dropped_dates - len(retrieved)}
        retrieved.sort(key=lambda p: (p.score,p.published), reverse=True)
        if len(retrieved) > config['max_results']:
            logger(f"综合相关度排序后保留前 {config['max_results']} 篇，其他结果不写入本次论文库。")
            retrieved = retrieved[:config['max_results']]
        with open_database(db_path) as conn:
            new = save_papers(conn, retrieved)
            task.emit(55, "整理文献", detail=f"写入本地文献库；新增 {len(new)} 篇")
            new.sort(key=lambda x: (x.score, x.published), reverse=True)
            task.emit(58, '中文速览', detail='检查需要生成速览的论文')
            if config["ai_enabled"]:
                try:
                    annotate_papers(conn, config, logger=logger, now=now, ranker=profile_ranker,
                        progress=lambda done, total, detail: task.emit(
                            58 + 16 * done / max(1,total), '中文速览',
                            current=done, total=total, detail=detail))
                except RuntimeError as exc:
                    logger(f"云端 AI 跳过：{exc}")
            task.emit(75, 'PDF 下载', detail='检查自动下载设置')
            if config.get('auto_download'):
                from .downloads.manager import download_selected
                targets = [p.paper_id for p in sorted(retrieved, key=lambda x:x.score, reverse=True)
                           if p.download_url][:config['download_limit']]
                if targets:
                    results = download_selected(conn, targets, config, logger=logger, db_path=db_path,
                        progress=lambda done,total,detail: task.emit(
                            75 + 11 * done / max(1,total), 'PDF 下载',
                            current=done, total=total, detail=detail))
                    logger(f"PDF：成功 {sum(r['status']=='downloaded' for r in results)} / {len(results)} 篇")
            recent = get_recent(conn, days=config["lookback_days"], limit=config["report_limit"],
                                keywords=config["keywords"], now=now, sort_mode=config.get("sort_mode", "relevance"), ranker=profile_ranker)
            if new:
                new_papers = get_selected(conn, [p.paper_id for p in new[:config["report_limit"]]])
                new_papers.sort(key=lambda x: (x.score, x.published), reverse=True)
            else:
                new_papers = []
        task.emit(87, "整理研究报告", detail="正在生成 Word、Markdown 和 CSV")
        file_new = None
        if new_papers:
            file_new = folder / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_新增论文.docx")
            write_report(file_new, "本次新增论文", new_papers, config, len(new), demo=False)
        task.emit(90, '生成 Word', detail='写入研究综述、优先阅读和论文详目')
        file_weekly = folder / "近期文献汇总.docx"
        write_report(file_weekly, "近期文献汇总", recent, config, len(recent), demo=False)
        task.emit(95, '生成 Markdown', detail='正在整理可预览的 Markdown 报告')
        file_md = folder / "近期文献汇总.md"
        file_csv = folder / "文献索引.csv"
        write_markdown(file_md, recent, config)
        task.emit(98, '生成 CSV', detail='正在保存文献索引')
        write_csv(file_csv, recent)
        task.emit(100, '已完成', detail=f'共检索 {len(retrieved)} 篇；报告收录 {len(recent)} 篇')
        logger(f"已检索 {len(retrieved)} 篇，新增 {len(new)} 篇。")
        logger(f"汇总文档：{file_weekly}")
        return {"retrieved": len(retrieved), "new": len(new), "selected": len(recent),
                "weekly": file_weekly, "markdown": file_md, "csv": file_csv,
                "new_file": file_new}


def report_from_cache(config: dict | None = None, logger=print, db_path: Path = DB_FILE, progress=None) -> Path:
    """Generate a Word report from saved papers with the current keyword filters."""
    from .progress import TaskProgress
    task = TaskProgress(progress, '本地报告')
    task.emit(5, '读取缓存', detail='检查当前检索范围')
    from .report import write_report, write_markdown, write_csv
    config = validate_config(config or read_config())
    folder = get_output_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    with single_instance(db_path.parent / "running.lock"):
        config, profile_ranker = prepare_zotero(config,logger=logger)
        task.emit(28, '读取缓存', detail='筛选并排序本地论文')
        with open_database(db_path) as conn:
            papers = get_recent(conn, days=config["lookback_days"], limit=config["report_limit"],
                                keywords=config["keywords"], sort_mode=config.get("sort_mode", "relevance"),ranker=profile_ranker)
        task.emit(53, '生成 Word', detail=f'整理 {len(papers)} 篇论文')
        target = folder / "近期文献汇总.docx"
        write_report(target, "近期文献汇总", papers, config, len(papers))
        task.emit(78, '生成 Markdown', detail='更新可在线预览的 Markdown 文档')
        write_markdown(folder / "近期文献汇总.md", papers, config)
        task.emit(93, '生成 CSV', detail='更新文献索引')
        write_csv(folder / "文献索引.csv", papers)
        task.emit(100, '已完成', detail='本地报告已更新')
    logger(f"报告已保存：{target}")
    return target


def make_demo(config: dict | None = None) -> Path:
    """Create a clearly labelled, fictional formatting preview (no network)."""
    from .report import write_report
    config = validate_config(config or read_config())
    folder = get_output_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    papers = [
        Paper("demo-a", "DEMO ONLY: Neural Operator for Complex Geometries",
              "This is an invented abstract for demonstrating document layout. It is not a real research paper.",
              "Example Author A, Example Author B", "2026-10-20T12:00:00Z", "2026-10-20T12:00:00Z",
              "cs.LG", "", "", 8.0, "示例：研究面向复杂几何的算子学习；仅用于演示排版，不代表真实论文结果。"),
        Paper("demo-b", "DEMO ONLY: Multiphysics Surrogate Simulation",
              "This fictional summary demonstrates how the research radar presents an abstract.",
              "Example Author C", "2026-10-21T12:00:00Z", "2026-10-21T12:00:00Z",
              "cs.AI", "", "", 4.0, "示例：论文内容均为虚构，正式使用会显示真实检索结果。"),
    ]
    target = folder / "演示_文献汇总_非真实论文.docx"
    write_report(target, "功能演示：研究前沿文献汇总", papers, config, len(papers), demo=True)
    return target

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
import sys
import re
import sqlite3
import time
import xml.etree.ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
# Code and research data can be kept in different directories. Existing source
# installations retain their original paths until the owner explicitly migrates.
from .storage_layout import resolve_data_root, INSTALL_ROOT
APP_ROOT = resolve_data_root()
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
    "ai_enabled": True,
    "ai_limit": 5,
    "api_provider": "DeepSeek",
    "api_base_url": "https://api.deepseek.com",
    "api_model": "deepseek-flash",
    "llm_input_usd_per_million": 0.0,
    "llm_output_usd_per_million": 0.0,
    "llm_data_consent": False,
    "output_dir": "reports",
    "sort_mode": "relevance",
    "sources": ["arxiv", "openalex", "crossref"],
    "openreview_invitation": "",
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
    "max_queries_per_source": 4,
    "source_query_timeout_seconds": 35,
    "source_total_timeout_seconds": 210,
    "research_goal": "",
    "ai_rerank_enabled": True,
    "ai_rerank_limit": 15,
    "schedule_all_topics": False,
    "update_check_enabled": True,
    "auto_update_on_startup": False,
    "service_run_mode": "foreground",
    "onboarding_completed": False,
    "update_check_interval_hours": 24,
    "dismissed_update_tag": "",
    "active_topic_id": "default",
    "topics": [],
}

# Fields that belong to a research topic; model credentials, updates and
# default output folder remain global. Existing flat settings stay compatible.
TOPIC_FIELDS = (
    'keywords', 'categories', 'sources', 'lookback_days', 'max_results',
    'report_limit', 'sort_mode', 'max_queries_per_source', 'research_goal',
    'openreview_invitation',
    'zotero_enabled',
    'zotero_mode', 'zotero_db_path', 'zotero_collection_key',
    'zotero_include_children', 'zotero_refresh_keywords', 'zotero_keyword_limit',
)
TOPIC_ID_RE = re.compile(r'^[a-z0-9_-]{1,48}$')



from .models import Paper
from .quality import score_relevance, valid_recent_publication, matched_keywords


def read_config(path: Path = APP_CONFIG) -> dict:
    from .config_safety import load_config
    return load_config(Path(path), DEFAULTS, validate_config)


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
    # Upgrade the v1.39 default pair. Users who set a different pair retain
    # their preference; new installations use the longer network budgets.
    if (int(c.get('source_query_timeout_seconds', 10)) == 10 and
            int(c.get('source_total_timeout_seconds', 75)) == 75):
        c['source_query_timeout_seconds'] = DEFAULTS['source_query_timeout_seconds']
        c['source_total_timeout_seconds'] = DEFAULTS['source_total_timeout_seconds']
    for k, lo, hi in (("lookback_days", 1, 60), ("max_results", 1, 500),
                      ("report_limit", 1, 100), ("interval_hours", 1, 168),
                      ("ai_limit", 1, 30), ("ai_rerank_limit", 1, 30), ("download_limit", 1, 20), ("max_pdf_mb", 1, 50), ("zotero_keyword_limit", 1, 15), ("max_queries_per_source", 1, 20), ("source_query_timeout_seconds", 20, 90),
                      ("source_total_timeout_seconds", 60, 600), ("update_check_interval_hours", 6, 168)):
        c[k] = max(lo, min(hi, int(c.get(k, DEFAULTS[k]))))
    for rate_key in ('llm_input_usd_per_million', 'llm_output_usd_per_million'):
        try:
            rate = float(c.get(rate_key, 0.0))
        except (ValueError, TypeError):
            raise ValueError('模型费用单价必须为非负数。') from None
        from math import isfinite
        if not isfinite(rate) or not 0 <= rate <= 10000:
            raise ValueError('模型费用单价必须为0至10000美元/百万 Token。')
        c[rate_key] = rate
    if type(c.get("llm_data_consent", False)) is not bool:
        raise ValueError("模型数据传输授权必须为明确的布尔选项。")
    c["llm_data_consent"] = c.get("llm_data_consent", False)
    c["ai_enabled"] = bool(c.get("ai_enabled", False))
    c["ai_rerank_enabled"] = bool(c.get("ai_rerank_enabled", False))
    c["schedule_all_topics"] = bool(c.get("schedule_all_topics", False))
    c["research_goal"] = str(c.get("research_goal") or "").strip()[:1500]
    c["auto_download"] = bool(c.get("auto_download", False))
    c["zotero_enabled"] = bool(c.get("zotero_enabled", False))
    c["zotero_include_children"] = bool(c.get("zotero_include_children", True))
    c["zotero_refresh_keywords"] = bool(c.get("zotero_refresh_keywords", DEFAULTS["zotero_refresh_keywords"]))
    c["zotero_mode"] = str(c.get("zotero_mode", "auto"))
    if c["zotero_mode"] not in ("auto", "api", "sqlite"):
        raise ValueError("Zotero 连接方式无效。")
    c['openreview_invitation'] = str(c.get('openreview_invitation') or '').strip()
    if c['openreview_invitation'] and not re.fullmatch(r'[A-Za-z0-9_.\-+/]{5,220}', c['openreview_invitation']):
        raise ValueError('OpenReview 投稿邀请 ID 格式无效。')
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
    if c["ai_enabled"] or c["ai_rerank_enabled"]:
        completion_endpoint(c["api_base_url"])
        if not c["api_model"]:
            raise ValueError("请填写模型名称。")
    c["output_dir"] = str(c.get("output_dir", "reports")).strip() or "reports"
    c["sort_mode"] = "newest" if c.get("sort_mode") == "newest" else "relevance"
    c['update_check_enabled'] = bool(c.get('update_check_enabled', True))
    c['auto_update_on_startup'] = bool(c.get('auto_update_on_startup', False))
    mode = str(c.get('service_run_mode', 'foreground'))
    if mode not in ('foreground', 'background'):
        raise ValueError('本地服务运行方式只能选择前台或后台。')
    c['service_run_mode'] = mode
    c['onboarding_completed'] = bool(c.get('onboarding_completed', False))
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
    # Validate any pre-existing primary before writing. A syntactically valid
    # JSON object with invalid settings must not be replaced unnoticed.
    read_config(Path(path))
    from .config_safety import save_config_json
    save_config_json(Path(path), c)
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


# SQLite schema initialization is a write operation. Do not perform it on every
# dashboard refresh. The cache uses file identity; each new database still
# receives the required migrations.
from threading import RLock as _DatabaseRLock
_DB_INIT_LOCK = _DatabaseRLock()
_DB_INITIALIZED: dict[str, tuple[int, int, int]] = {}


def _db_identity(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return (int(stat.st_dev), int(stat.st_ino),
            int(stat.st_ctime_ns) if not stat.st_ino else 0)


def _initialize_schema(conn: sqlite3.Connection) -> None:
    from .migrations import assert_compatible
    assert_compatible(conn,'research')
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
    # Topic-specific flags and recommendation values. The canonical paper table
    # keeps metadata and download state shared across research topics.
    conn.execute("""CREATE TABLE IF NOT EXISTS topic_papers (
      topic_id TEXT NOT NULL, paper_id TEXT NOT NULL, score REAL NOT NULL DEFAULT 0,
      is_starred INTEGER NOT NULL DEFAULT 0,
      reading_status TEXT NOT NULL DEFAULT 'unread',
      feedback INTEGER NOT NULL DEFAULT 0,
      recommend_reason TEXT NOT NULL DEFAULT '',
      ai_relevance INTEGER NOT NULL DEFAULT -1,
      recommend_evidence TEXT NOT NULL DEFAULT '',
      rerank_fingerprint TEXT NOT NULL DEFAULT '',
      matched_at TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(topic_id, paper_id), FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
    )""")
    cols={row[1] for row in conn.execute('PRAGMA table_info(topic_papers)')}
    for col in ('recommend_evidence','rerank_fingerprint'):
        if col not in cols:
            conn.execute(f"ALTER TABLE topic_papers ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_topic_papers_rank ON topic_papers(topic_id, score DESC)')
    conn.execute('CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    # Preserve legacy starred/reading flags once, without making future papers
    # collected for other topics automatically part of the default theme.
    if not conn.execute("SELECT 1 FROM app_meta WHERE key='topic_migrated_v111'").fetchone():
        conn.execute("""INSERT OR IGNORE INTO topic_papers
          (topic_id,paper_id,score,is_starred,reading_status,matched_at)
          SELECT 'default',paper_id,score,is_starred,reading_status,first_seen FROM papers""")
        conn.execute("INSERT INTO app_meta VALUES ('topic_migrated_v111','1')")
    conn.commit()
    from .migrations import migrate
    migrate(conn,'research')


def _legacy_topic_repair_pending(conn: sqlite3.Connection) -> bool:
    """Read-only marker check preserves old databases created before topics.

    Normally one indexed SELECT replaces repeated CREATE/ALTER statements.
    Older clients (or interrupted migrations) may lack the marker; repair is
    triggered only for that state, including after a reused connection.
    """
    try:
        if conn.execute("SELECT 1 FROM app_meta WHERE key='topic_migrated_v111'").fetchone() is None:
            return True
        # Read-only schema probe for interrupted upgrades and legacy tables.
        # LIMIT 0 compiles column names without reading any paper rows.
        conn.execute('SELECT recommend_evidence, rerank_fingerprint FROM topic_papers LIMIT 0')
        return False
    except sqlite3.OperationalError:
        return True


def connect(db_path: Path = DB_FILE) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    try:
        from .migrations import assert_compatible
        assert_compatible(conn, 'research')  # Read-only: reject future DB even after cache hit.
        key = str(db_path.resolve())
        identity = _db_identity(db_path)
        if _DB_INITIALIZED.get(key) != identity or _legacy_topic_repair_pending(conn):
            with _DB_INIT_LOCK:
                if _DB_INITIALIZED.get(key) != identity or _legacy_topic_repair_pending(conn):
                    _initialize_schema(conn)
                    _DB_INITIALIZED[key] = _db_identity(db_path)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def open_database(db_path: Path = DB_FILE):
    connection = connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def save_papers(conn: sqlite3.Connection, papers: list[Paper], *, topic_id: str = "default",
                commit: bool = True) -> list[Paper]:
    """Idempotent upsert with DOI matching across source adapters.

    Existing local annotations and downloaded file state are preserved.
    """
    if not TOPIC_ID_RE.fullmatch(topic_id):
        raise ValueError('研究主题标识无效。')
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
        conn.execute("""INSERT INTO topic_papers
          (topic_id,paper_id,score,matched_at,recommend_reason,ai_relevance,recommend_evidence,rerank_fingerprint)
          VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(topic_id,paper_id) DO UPDATE SET
          score=excluded.score, matched_at=excluded.matched_at,
          recommend_reason=excluded.recommend_reason,
          ai_relevance=excluded.ai_relevance,
          recommend_evidence=excluded.recommend_evidence,
          rerank_fingerprint=excluded.rerank_fingerprint""",
          (topic_id,p.paper_id,p.score,seen_at,p.recommend_reason,p.ai_relevance,
           p.recommend_evidence,p.rerank_fingerprint))
    if commit:
        conn.commit()
    return inserted


def get_recent(conn: sqlite3.Connection, days=7, limit=20,
               keywords: list[str] | None = None, now: datetime | None = None,
               sort_mode: str = "relevance", ranker=None, topic_id: str | None = None,
               rerank_enabled: bool = False) -> list[Paper]:
    """Read recent saved papers and score against the current topic."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today = current.astimezone(timezone.utc).date()
    cutoff = (today - timedelta(days=days)).isoformat()
    if topic_id is not None:
        if not TOPIC_ID_RE.fullmatch(topic_id):
            raise ValueError('研究主题标识无效。')
        rows = conn.execute("""SELECT p.*,tp.feedback AS topic_feedback,
          tp.recommend_reason AS topic_reason,tp.ai_relevance AS topic_ai_relevance,
          tp.recommend_evidence AS topic_evidence,tp.rerank_fingerprint AS topic_fingerprint,
          tp.is_starred AS topic_starred,tp.reading_status AS topic_reading
          FROM papers p JOIN topic_papers tp ON tp.paper_id=p.paper_id
          WHERE tp.topic_id=? AND substr(p.published,1,10)>=? AND substr(p.published,1,10)<=?""",
          (topic_id,cutoff,today.isoformat())).fetchall()
    else:
        rows = conn.execute("SELECT * FROM papers WHERE substr(published,1,10)>=? AND substr(published,1,10)<=?", (cutoff, today.isoformat())).fetchall()
    papers=[]
    for row in rows:
        if not valid_recent_publication(row['published'], current, days):
            continue
        values=dict(row)
        if topic_id is not None:
            values['feedback']=values.pop('topic_feedback')
            values['recommend_reason']=values.pop('topic_reason')
            values['ai_relevance']=values.pop('topic_ai_relevance')
            values['recommend_evidence']=values.pop('topic_evidence')
            values['rerank_fingerprint']=values.pop('topic_fingerprint')
            values['is_starred']=values.pop('topic_starred')
            values['reading_status']=values.pop('topic_reading')
        papers.append(Paper(**values))
    if ranker is not None:
        papers = [paper for paper in papers if not ranker.already_collected(paper)]
    if keywords is not None:
        for paper in papers:
            paper.score = rank(paper, keywords, ranker)
            if paper.score <= 0 and 'openalex_semantic' in paper.source.split(',') and paper.abstract:
                paper.score=0.35
            if rerank_enabled and paper.ai_relevance in (0,1,2,3):
                # The stored AI result is a supplementary signal, never sole proof
                # of methods/experimental evidence. Re-evaluate when re-fetched.
                from .recommend import bonus
                paper.score=max(0,round(paper.score+bonus(paper.ai_relevance),4))
            if paper.feedback == 1:
                paper.score += 2.0  # Explicit user feedback, isolated to this topic.
        if keywords:
            papers = [paper for paper in papers if paper.score > 0]
    papers = [p for p in papers if p.feedback >= 0]  # respect user suppression per topic
    if sort_mode == "newest":
        papers.sort(key=lambda p: (p.published, p.score), reverse=True)
    else:
        papers.sort(key=lambda p: (p.score, p.published), reverse=True)
    return papers[:limit]


def get_selected(conn: sqlite3.Connection, ids: list[str], topic_id: str | None = None) -> list[Paper]:
    """Return requested papers, including the selected topic's recommendation and flags."""
    if topic_id is None:
        return [Paper(**dict(row)) for pid in ids if (row := conn.execute(
            'SELECT * FROM papers WHERE paper_id=?',(pid,)).fetchone()) is not None]
    if not TOPIC_ID_RE.fullmatch(topic_id):
        raise ValueError('研究主题标识无效。')
    rows=[]
    for pid in ids:
        row=conn.execute("""SELECT p.*,tp.score AS theme_score,tp.is_starred AS theme_starred,
            tp.reading_status AS theme_reading,tp.feedback AS theme_feedback,
            tp.recommend_reason AS theme_reason,tp.ai_relevance AS theme_relevance,
            tp.recommend_evidence AS theme_evidence,tp.rerank_fingerprint AS theme_fingerprint
            FROM papers p JOIN topic_papers tp ON tp.paper_id=p.paper_id
            WHERE p.paper_id=? AND tp.topic_id=?""",(pid,topic_id)).fetchone()
        if row is None:
            continue
        data=dict(row)
        for col,dest in (('theme_score','score'),('theme_starred','is_starred'),
                        ('theme_reading','reading_status'),('theme_feedback','feedback'),
                        ('theme_reason','recommend_reason'),('theme_relevance','ai_relevance'),
                        ('theme_evidence','recommend_evidence'),('theme_fingerprint','rerank_fingerprint')):
            data[dest]=data.pop(col)
        rows.append(Paper(**data))
    return rows


def annotate_papers(conn: sqlite3.Connection, config: dict, logger=print, now: datetime | None = None, ranker=None, progress=None) -> int:
    """Summaries are best effort; errors do not prevent Word export."""
    if not config["ai_enabled"] or not config.get("llm_data_consent", False):
        return 0
    from .cloud_api import get_api_key, summarize_paper, summary_fingerprint, validate_summary_evidence
    if not get_api_key(config["api_provider"]):
        logger("未配置 API Key，本次跳过中文速览。")
        return 0
    recent = get_recent(conn, days=config["lookback_days"],
                        limit=max(config["report_limit"], config["ai_limit"]),
                        keywords=config["keywords"], now=now, sort_mode=config.get("sort_mode", "relevance"),ranker=ranker,
                        topic_id=config.get("active_topic_id", "default"),
                        rerank_enabled=config.get("ai_rerank_enabled",False))
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


def _topic_library_parts(topic_id: str, topic_only: bool, query: str,
                         only_starred: bool, reading_status: str, triage_filter: str = ""):
    if not TOPIC_ID_RE.fullmatch(topic_id):
        raise ValueError('研究主题标识无效。')
    join = ('JOIN' if topic_only else 'LEFT JOIN') + ' topic_papers tp ON tp.paper_id=p.paper_id AND tp.topic_id=?'
    if triage_filter and triage_filter not in {'inbox','skipped','actionable'}:
        raise ValueError('论文筛选条件无效')
    if triage_filter and not topic_only:
        raise ValueError('阅读决策仅支持当前研究主题')
    if triage_filter:
        join += ' LEFT JOIN local_paper_notes n ON n.topic_id=tp.topic_id AND n.paper_id=tp.paper_id'
    where, args = _library_filter(query, False, '')
    for col in ('title','abstract','authors','doi'):
        where=where.replace(col+' LIKE', 'p.'+col+' LIKE')
    if topic_only:
        where += ' AND tp.paper_id IS NOT NULL'
    if only_starred:
        where += ' AND COALESCE(tp.is_starred,0)=1'
    if reading_status in {'unread','reading','done'}:
        where += " AND COALESCE(tp.reading_status,'unread')=?"
        args.append(reading_status)
    if triage_filter == 'inbox':
        where += " AND COALESCE(tp.is_starred,0)=0 AND COALESCE(tp.feedback,0)=0"
        where += " AND COALESCE(tp.reading_status,'unread')='unread'"
        where += " AND COALESCE(n.next_action,'none')='none' AND COALESCE(n.note,'')=''"
    elif triage_filter == 'skipped':
        where += ' AND COALESCE(tp.feedback,0)=-1'
    elif triage_filter == 'actionable':
        where += " AND COALESCE(n.next_action,'none')<>'none'"
    return join,where,[topic_id,*args]


def count_library(conn: sqlite3.Connection, *, query: str = "", only_starred: bool = False,
                  reading_status: str = "", topic_id: str | None = None,
                  topic_only: bool = False, triage_filter: str = "") -> int:
    if topic_id is not None:
        join,where,args=_topic_library_parts(topic_id,topic_only,query,only_starred,reading_status,triage_filter)
        return int(conn.execute('SELECT COUNT(*) FROM papers p '+join+' WHERE '+where,args).fetchone()[0])
    clause,args=_library_filter(query,only_starred,reading_status)
    return int(conn.execute('SELECT COUNT(*) FROM papers WHERE '+clause,args).fetchone()[0])


def list_library(conn: sqlite3.Connection, *, limit: int = 300, offset: int = 0,
                 query: str = "", only_starred: bool = False,
                 reading_status: str = "", sort: str = "default",
                 topic_id: str | None = None, topic_only: bool = False, triage_filter: str = "") -> list[Paper]:
    """Page the shared paper index; display selected theme's independent flags."""
    if topic_id is not None:
        join,where,args=_topic_library_parts(topic_id,topic_only,query,only_starred,reading_status,triage_filter)
        order = ('p.first_seen DESC,p.paper_id ASC' if sort=='recent' else
                 '(COALESCE(tp.score,0)+CASE WHEN tp.feedback=1 THEN 2 WHEN tp.feedback=-1 THEN -100 ELSE 0 END) DESC,p.published DESC,p.paper_id ASC' if sort=='relevance' else
                 'COALESCE(tp.is_starred,0) DESC,p.published DESC,p.paper_id ASC')
        rows=conn.execute("""SELECT p.*,COALESCE(tp.score,0) AS topic_score,
         COALESCE(tp.is_starred,0) AS topic_starred,
         COALESCE(tp.reading_status,'unread') AS topic_reading,
         COALESCE(tp.feedback,0) AS topic_feedback,
         COALESCE(tp.recommend_reason,'') AS topic_reason,
         COALESCE(tp.ai_relevance,-1) AS topic_ai_relevance,
         COALESCE(tp.recommend_evidence,'') AS topic_evidence,
         COALESCE(tp.rerank_fingerprint,'') AS topic_fingerprint
         FROM papers p """+join+' WHERE '+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',
         [*args,max(1,min(int(limit),500)),max(0,int(offset))]).fetchall()
        result=[]
        for row in rows:
            data=dict(row)
            for column,field in (('topic_score','score'),('topic_starred','is_starred'),
                                 ('topic_reading','reading_status'),('topic_feedback','feedback'),
                                 ('topic_reason','recommend_reason'),('topic_ai_relevance','ai_relevance'),
                                 ('topic_evidence','recommend_evidence'),('topic_fingerprint','rerank_fingerprint')):
                data[field]=data.pop(column)
            result.append(Paper(**data))
        return result
    clause,args=_library_filter(query,only_starred,reading_status)
    order=('first_seen DESC,paper_id ASC' if sort=='recent' else
           'score DESC,published DESC,paper_id ASC' if sort=='relevance' else
           'is_starred DESC,published DESC,paper_id ASC')
    rows=conn.execute('SELECT * FROM papers WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',
                     [*args,max(1,min(int(limit),500)),max(0,int(offset))]).fetchall()
    return [Paper(**dict(r)) for r in rows]


def update_paper_flags(conn: sqlite3.Connection, paper_id: str, *, starred=None,
                       status=None, topic_id: str | None = None, feedback=None) -> bool:
    row=conn.execute('SELECT paper_id,score FROM papers WHERE paper_id=?',(paper_id,)).fetchone()
    if row is None:
        return False
    if status is not None and status not in {'unread','reading','done'}:
        raise ValueError('阅读状态无效')
    if feedback is not None and (type(feedback) is not int or feedback not in (-1,0,1)):
        raise ValueError('相关性反馈只能是 -1、0、1')
    if topic_id is None:
        if starred is not None:
            conn.execute('UPDATE papers SET is_starred=? WHERE paper_id=?',(int(bool(starred)),paper_id))
        if status is not None:
            conn.execute('UPDATE papers SET reading_status=? WHERE paper_id=?',(status,paper_id))
    else:
        if not TOPIC_ID_RE.fullmatch(topic_id):
            raise ValueError('研究主题标识无效')
        conn.execute("""INSERT OR IGNORE INTO topic_papers(topic_id,paper_id,score,matched_at)
            VALUES (?,?,?,?)""", (topic_id,paper_id,float(row['score']),datetime.now().isoformat(timespec='seconds')))
        if starred is not None:
            conn.execute('UPDATE topic_papers SET is_starred=? WHERE topic_id=? AND paper_id=?',
                         (int(bool(starred)),topic_id,paper_id))
        if status is not None:
            conn.execute('UPDATE topic_papers SET reading_status=? WHERE topic_id=? AND paper_id=?',
                         (status,topic_id,paper_id))
        if feedback is not None:
            conn.execute('UPDATE topic_papers SET feedback=? WHERE topic_id=? AND paper_id=?',
                         (feedback,topic_id,paper_id))
    conn.commit()
    return True


def run_cycle(config: dict | None = None, logger=print, *,
              db_path: Path = DB_FILE, fetcher=fetch_arxiv, now: datetime | None = None,
              progress=None) -> dict:
    """Backward-compatible public API; specialist stages live in agents/.

    Do not move this wrapper: CLI, scheduled jobs, webapp and earlier tests
    import the historical core.run_cycle function.
    """
    from .agents.pipeline import run_research_cycle
    return run_research_cycle(config, logger, db_path=db_path, fetcher=fetcher,
                              now=now, progress=progress)


def report_from_cache(config: dict | None = None, logger=print, db_path: Path = DB_FILE, progress=None) -> Path:
    """Generate a Word report from saved papers with the current keyword filters."""
    from .progress import TaskProgress
    task = TaskProgress(progress, '本地报告')
    task.emit(5, '读取缓存', detail='检查当前检索范围')
    from .report import write_report, write_markdown, write_csv
    config = validate_config(config or read_config())
    if not config.get("llm_data_consent", False):
        config = {**config,"ai_enabled":False,"ai_rerank_enabled":False}
    folder = get_output_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    with single_instance(db_path.parent / "running.lock"):
        config, profile_ranker = prepare_zotero(config,logger=logger)
        task.emit(28, '读取缓存', detail='筛选并排序本地论文')
        with open_database(db_path) as conn:
            papers = get_recent(conn, days=config["lookback_days"], limit=config["report_limit"],
                                keywords=config["keywords"], sort_mode=config.get("sort_mode", "relevance"),ranker=profile_ranker,
                                topic_id=config.get("active_topic_id", "default"),
                                rerank_enabled=config.get("ai_rerank_enabled",False))
            from .fulltext import load_report_cards
            config['_fulltext_cards']=load_report_cards(conn,[p.paper_id for p in papers])
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

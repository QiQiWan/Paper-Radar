# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Human-first, account-scoped literature triage and research reading notes.

No model calls, external requests, or cross-account data access are made here.
The UI intentionally reuses the existing starred/reading/feedback semantics.
"""
from __future__ import annotations

import csv
from io import StringIO
import re
from urllib.parse import urlsplit

from .portal_export import _safe_cell

NEXT_ACTIONS = frozenset({'none', 'read', 'replicate', 'cite', 'discuss'})
BULK_ACTIONS = frozenset({'star', 'unstar', 'done', 'reading', 'skip', 'unskip'})
ACTION_LABELS = {'none': '无', 'read': '精读原文', 'replicate': '尝试复现',
                 'cite': '准备引用', 'discuss': '与同事讨论'}
MAX_BULK = 100
MAX_NOTE = 1600
MAX_EXPORT = 2000


class NoteConflict(Exception):
    """A second tab modified the note since it was last loaded."""


def _topic_paper(db, topic: str, paper_id: str) -> bool:
    return db.execute('SELECT 1 FROM topic_papers WHERE topic_id=? AND paper_id=?',
                      (topic, paper_id)).fetchone() is not None


def _validate_id(paper_id):
    if type(paper_id) is not str or not 1 <= len(paper_id) <= 150 or '\x00' in paper_id:
        raise ValueError('论文 ID 不正确')
    return paper_id


def save_note(portal, uid: str, topic: str, paper_id: str, note: str,
              next_action: str, expected_revision: int) -> dict:
    """Optimistic revision prevents silently losing notes edited in another tab."""
    paper_id = _validate_id(paper_id)
    if type(note) is not str or len(note) > MAX_NOTE or '\x00' in note:
        raise ValueError('阅读笔记不得超过 1600 字，不允许 NUL 字符')
    if type(next_action) is not str or next_action not in NEXT_ACTIONS:
        raise ValueError('请选择有效的下一步行动')
    if type(expected_revision) is not int or not 0 <= expected_revision < 1_000_000_000:
        raise ValueError('笔记修订版本不正确，请刷新重试')
    portal.available(uid, topic)
    note = note.replace('\r\n', '\n').replace('\r', '\n')
    from .public_portal import utc_now
    stamp = utc_now()
    with portal.workspace(uid) as db:
        if not _topic_paper(db, topic, paper_id):
            raise ValueError('论文不存在或无权访问')
        if expected_revision == 0:
            result = db.execute('''INSERT OR IGNORE INTO paper_notes
                (topic_id,paper_id,note,next_action,revision,updated_at)
                VALUES (?,?,?,?,1,?)''', (topic, paper_id, note, next_action, stamp))
        else:
            result = db.execute('''UPDATE paper_notes SET note=?,next_action=?,
                revision=revision+1,updated_at=?
                WHERE topic_id=? AND paper_id=? AND revision=?''',
                (note, next_action, stamp, topic, paper_id, expected_revision))
        if result.rowcount != 1:
            raise NoteConflict('笔记已在其他窗口修改，当前内容未覆盖。请复制草稿后刷新核对。')
        row = db.execute('''SELECT note,next_action,revision,updated_at
            FROM paper_notes WHERE topic_id=? AND paper_id=?''', (topic, paper_id)).fetchone()
    return {'topic_id': topic, 'paper_id': paper_id, 'note': row['note'],
            'next_action': row['next_action'], 'note_revision': row['revision'],
            'note_updated_at': row['updated_at']}


def bulk_flags(portal, uid: str, topic: str, ids, action: str) -> dict:
    """All-or-nothing batch triage; invalid IDs never cause partial updates."""
    if type(ids) is not list or not 1 <= len(ids) <= MAX_BULK:
        raise ValueError('每次请选择 1—100 篇论文')
    if any(type(x) is not str or not 1 <= len(x) <= 150 or '\x00' in x for x in ids):
        raise ValueError('论文 ID 不正确')
    if len(set(ids)) != len(ids):
        raise ValueError('批量操作包含重复论文')
    if type(action) is not str or action not in BULK_ACTIONS:
        raise ValueError('批量操作不支持该动作')
    portal.available(uid, topic)
    placeholders = ','.join('?' for _ in ids)
    from .public_portal import utc_now
    stamp = utc_now()
    with portal.workspace(uid) as db:
        rows = db.execute('''SELECT tp.paper_id,
            COALESCE(f.is_starred,0) AS is_starred,
            COALESCE(f.reading_status,'unread') AS reading_status,
            COALESCE(f.feedback,0) AS feedback
            FROM topic_papers tp LEFT JOIN topic_paper_flags f
            ON f.topic_id=tp.topic_id AND f.paper_id=tp.paper_id
            WHERE tp.topic_id=? AND tp.paper_id IN (''' + placeholders + ')',
            (topic, *ids)).fetchall()
        if len(rows) != len(ids):
            raise ValueError('部分论文已不在当前研究主题，未修改任何记录')
        updates = []
        for r in rows:
            starred, status, feedback = int(r['is_starred']), r['reading_status'], int(r['feedback'])
            if action == 'star':
                starred = 1
                if feedback == -1: feedback = 0
            elif action == 'unstar': starred = 0
            elif action == 'done': status = 'done'
            elif action == 'reading': status = 'reading'
            elif action == 'skip': starred, feedback = 0, -1
            elif action == 'unskip':
                if feedback == -1: feedback = 0
            updates.append((topic, r['paper_id'], starred, status, feedback, stamp))
        db.executemany('''INSERT INTO topic_paper_flags
            (topic_id,paper_id,is_starred,reading_status,feedback,updated_at)
            VALUES (?,?,?,?,?,?) ON CONFLICT(topic_id,paper_id) DO UPDATE SET
            is_starred=excluded.is_starred,reading_status=excluded.reading_status,
            feedback=excluded.feedback,updated_at=excluded.updated_at''', updates)
    return {'topic_id': topic, 'action': action, 'updated': len(updates), 'paper_ids': ids}


def _md(value) -> str:
    """Plain-text content placed inside Markdown, with structure characters escaped."""
    cleaned = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(value or '')).strip()
    return re.sub(r'([\\`*_{}\[\]()#+.!<>|~-])', r'\\\1', cleaned)


def _safe_link(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return ''
        if any(ord(c)<33 or ord(c)==127 or c in '<>\\' for c in url):
            return ''
        return url.replace('(', '%28').replace(')', '%29').replace(' ', '%20')
    except ValueError:
        return ''


def export_reading(portal, uid: str, topic: str, *, scope: str = 'starred',
                   format_: str = 'md') -> tuple[bytes, str, str]:
    """Export a *human* reading list; no AI-derived findings are invented."""
    if scope not in ('starred', 'actionable', 'all'):
        raise ValueError('无效的导出范围')
    if format_ not in ('md', 'csv'):
        raise ValueError('仅支持 Markdown 或 CSV 导出')
    _, topic_record = portal.available(uid, topic)
    where = ['tp.topic_id=?']
    if scope == 'starred': where.append('COALESCE(f.is_starred,0)=1')
    if scope == 'actionable': where.append("COALESCE(n.next_action,'none')<>'none'")
    with portal.workspace(uid) as db:
        rows = db.execute('''SELECT p.id,p.title,p.published,p.doi,p.url,
            COALESCE(f.is_starred,0) AS is_starred,
            COALESCE(f.reading_status,'unread') AS reading_status,
            COALESCE(f.feedback,0) AS feedback,
            COALESCE(n.note,'') AS note,
            COALESCE(n.next_action,'none') AS next_action,
            COALESCE(n.updated_at,'') AS note_updated_at
            FROM topic_papers tp JOIN papers p ON p.id=tp.paper_id
            LEFT JOIN topic_paper_flags f ON f.topic_id=tp.topic_id AND f.paper_id=tp.paper_id
            LEFT JOIN paper_notes n ON n.topic_id=tp.topic_id AND n.paper_id=tp.paper_id
            WHERE ''' + ' AND '.join(where) +
            ' ORDER BY p.published DESC,p.id DESC LIMIT ?', (topic, MAX_EXPORT + 1)).fetchall()
    if len(rows) > MAX_EXPORT:
        raise ValueError('导出超过 2000 篇。请使用星标或下一步行动筛选后分批导出')
    if format_ == 'csv':
        out = StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['论文ID', '题名', '发表日期', 'DOI', '原文网址', '星标', '阅读状态',
                         '人工价值判断', '下一步行动', '个人阅读笔记', '笔记修改时间'])
        for row in rows:
            values = [row['id'], row['title'], row['published'], row['doi'], row['url'],
                      '是' if row['is_starred'] else '否', row['reading_status'],
                      row['feedback'], ACTION_LABELS[row['next_action']], row['note'], row['note_updated_at']]
            writer.writerow([_safe_cell(v) for v in values])
        return ('\ufeff' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', 'paper-radar-reading.csv'
    lines = ['# ' + _md(topic_record['name']) + ' · 个人阅读清单', '',
             f'共 {len(rows)} 篇 · 导出范围：{ {"starred":"星标论文","actionable":"待行动论文","all":"全部论文"}[scope]}', '',
             '内容为用户保存的阅读状态与笔记；实验结论仍须核查论文原文。', '']
    status_labels = {'unread': '未阅读', 'reading': '阅读中', 'done': '已读完'}
    for n, row in enumerate(rows, 1):
        lines += [f'## {n}. {_md(row["title"])}', '',
                  f'- 论文 ID：`{_md(row["id"] )}`',
                  f'- 发表日期：{_md(row["published"] or "未注明")}',
                  f'- 状态：{status_labels.get(row["reading_status"],"未阅读")}；' +
                  f'星标：{"是" if row["is_starred"] else "否"}；下一步：{ACTION_LABELS[row["next_action"]]}']
        if row['doi']: lines.append('- DOI：' + _md(row['doi']))
        if link := _safe_link(row['url']): lines.append(f'- [查看论文原文]({link})')
        if row['note']:
            lines += ['', '**个人笔记（人工填写）**', '',
                      *('> ' + _md(text) for text in row['note'].splitlines()), '']
        else: lines.append('')
    return ('\n'.join(lines) + '\n').encode('utf-8'), 'text/markdown; charset=utf-8', 'paper-radar-reading.md'

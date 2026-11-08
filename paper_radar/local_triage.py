# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline desktop reading notes and atomic triage for the active research topic."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO

from .core import TOPIC_ID_RE
from .portal_triage import (MAX_NOTE, MAX_BULK, MAX_EXPORT, NEXT_ACTIONS,
                            BULK_ACTIONS, ACTION_LABELS, NoteConflict, _md, _safe_link)
from .portal_export import _safe_cell


def _topic(value: str) -> str:
    if type(value) is not str or not TOPIC_ID_RE.fullmatch(value):
        raise ValueError('研究主题标识无效')
    return value


def _id(value: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 150 or '\x00' in value:
        raise ValueError('论文 ID 无效')
    return value


def _belongs(db, topic: str, paper_id: str) -> bool:
    return db.execute('SELECT 1 FROM topic_papers WHERE topic_id=? AND paper_id=?',
                      (topic, paper_id)).fetchone() is not None


def read_notes(db, topic: str, paper_ids: list[str]) -> dict:
    """One indexed query for a whole paginated library page, never N+1 reads."""
    _topic(topic)
    if not paper_ids: return {}
    if len(paper_ids) > 100 or any(type(p) is not str or len(p)>150 for p in paper_ids):
        raise ValueError('论文页大小无效')
    placeholders=','.join('?' for _ in paper_ids)
    rows=db.execute('''SELECT paper_id,note,next_action,revision,updated_at FROM local_paper_notes
        WHERE topic_id=? AND paper_id IN (''' + placeholders + ')', (topic,*paper_ids)).fetchall()
    return {r['paper_id']:{'note':r['note'],'next_action':r['next_action'],
            'note_revision':r['revision'],'note_updated_at':r['updated_at']} for r in rows}


def save_note(db, topic: str, paper_id: str, note: str,
              next_action: str, expected_revision: int) -> dict:
    _topic(topic);_id(paper_id)
    if type(note) is not str or len(note)>MAX_NOTE or '\x00' in note:
        raise ValueError('阅读笔记不得超过 1600 字')
    if type(next_action) is not str or next_action not in NEXT_ACTIONS:
        raise ValueError('下一步行动无效')
    if type(expected_revision) is not int or not 0 <= expected_revision < 1_000_000_000:
        raise ValueError('修订版本无效')
    if not _belongs(db,topic,paper_id):raise ValueError('论文不属于当前研究主题')
    note=note.replace('\r\n','\n').replace('\r','\n')
    at=datetime.now(timezone.utc).isoformat(timespec='seconds')
    if expected_revision==0:
        result=db.execute('''INSERT OR IGNORE INTO local_paper_notes
            (topic_id,paper_id,note,next_action,revision,updated_at)
            VALUES (?,?,?,?,1,?)''',(topic,paper_id,note,next_action,at))
    else:
        result=db.execute('''UPDATE local_paper_notes SET note=?,next_action=?,
            revision=revision+1,updated_at=? WHERE topic_id=? AND paper_id=? AND revision=?''',
            (note,next_action,at,topic,paper_id,expected_revision))
    if result.rowcount!=1:
        raise NoteConflict('其他窗口已经更新该笔记；请保留草稿并刷新核对')
    row=db.execute('''SELECT note,next_action,revision,updated_at FROM local_paper_notes
        WHERE topic_id=? AND paper_id=?''',(topic,paper_id)).fetchone()
    return {'paper_id':paper_id,'note':row['note'],'next_action':row['next_action'],
            'note_revision':row['revision'],'note_updated_at':row['updated_at']}


def bulk_flags(db, topic: str, ids, action: str) -> dict:
    _topic(topic)
    if type(ids) is not list or not 1<=len(ids)<=MAX_BULK:
        raise ValueError('每次请选择 1—100 篇论文')
    if any(type(x) is not str or not 1<=len(x)<=150 or '\x00' in x for x in ids):
        raise ValueError('论文 ID 无效')
    if len(set(ids))!=len(ids): raise ValueError('重复的论文 ID')
    if type(action) is not str or action not in BULK_ACTIONS:
        raise ValueError('不支持的批量动作')
    placeholders=','.join('?' for _ in ids)
    rows=db.execute('''SELECT paper_id,is_starred,reading_status,feedback FROM topic_papers
        WHERE topic_id=? AND paper_id IN (''' + placeholders + ')',(topic,*ids)).fetchall()
    if len(rows)!=len(ids):raise ValueError('部分论文不属于当前主题，本批次未进行修改')
    values=[]
    for row in rows:
        star,status,feedback=int(row['is_starred']),row['reading_status'],int(row['feedback'])
        if action=='star':
            star=1
            if feedback==-1:feedback=0
        elif action=='unstar':star=0
        elif action=='done':status='done'
        elif action=='reading':status='reading'
        elif action=='skip':star,feedback=0,-1
        elif action=='unskip':
            if feedback==-1:feedback=0
        values.append((star,status,feedback,topic,row['paper_id']))
    db.executemany('''UPDATE topic_papers SET is_starred=?,reading_status=?,feedback=?
        WHERE topic_id=? AND paper_id=?''', values)
    return {'topic_id':topic,'action':action,'updated':len(values),'paper_ids':ids}


def export_reading(db, topic: str, scope: str='starred', format_: str='md',
                   topic_name: str='当前研究主题') -> tuple[bytes,str,str]:
    _topic(topic)
    if scope not in ('starred','actionable','all') or format_ not in ('md','csv'):
        raise ValueError('导出格式或范围无效')
    criterion={'starred':'tp.is_starred=1','actionable':"COALESCE(n.next_action,'none')<>'none'",
               'all':'1=1'}[scope]
    rows=db.execute('''SELECT p.paper_id,p.title,p.published,p.doi,p.url,
        tp.is_starred,tp.reading_status,tp.feedback,
        COALESCE(n.note,'') AS note,COALESCE(n.next_action,'none') AS next_action,
        COALESCE(n.updated_at,'') AS note_updated_at
        FROM topic_papers tp JOIN papers p ON p.paper_id=tp.paper_id
        LEFT JOIN local_paper_notes n ON n.topic_id=tp.topic_id AND n.paper_id=tp.paper_id
        WHERE tp.topic_id=? AND '''+criterion+
        ' ORDER BY p.published DESC,p.paper_id LIMIT ?', (topic,MAX_EXPORT+1)).fetchall()
    if len(rows)>MAX_EXPORT:
        raise ValueError('导出超过 2000 篇，请先缩小导出范围')
    if format_=='csv':
        out=StringIO(newline='')
        writer=csv.writer(out)
        writer.writerow(['论文ID','题名','发表日期','DOI','原文网址','星标','阅读状态','人工价值判断',
                         '下一步行动','个人阅读笔记','笔记修改时间'])
        for r in rows:
            writer.writerow([_safe_cell(x) for x in (r['paper_id'],r['title'],r['published'],r['doi'],
                r['url'],'是' if r['is_starred'] else '否',r['reading_status'],r['feedback'],
                ACTION_LABELS[r['next_action']],r['note'],r['note_updated_at'])])
        return ('\ufeff'+out.getvalue()).encode('utf-8'),'text/csv; charset=utf-8','paper-radar-reading.csv'
    lines=['# '+_md(topic_name)+' · 个人阅读清单','','共 '+str(len(rows))+' 篇', '',
           '以下为用户保存的阅读记录，实验结论仍须核对原文。','']
    labels={'unread':'未阅读','reading':'阅读中','done':'已读完'}
    for i,r in enumerate(rows,1):
        lines += [f'## {i}. {_md(r["title"])}','',f'- 论文 ID：`{_md(r["paper_id"] )}`',
                  f'- 发表日期：{_md(r["published"] or "未知")}',
                  '- 阅读：'+labels.get(r['reading_status'],'未阅读')+'；后续：'+ACTION_LABELS[r['next_action']]]
        if r['doi']:lines.append('- DOI：'+_md(r['doi']))
        if link:=_safe_link(r['url']):lines.append(f'- [原文]({link})')
        if r['note']:
            lines += ['','**个人笔记（人工填写）**','',*('> '+_md(line) for line in r['note'].splitlines())]
        lines.append('')
    return ('\n'.join(lines)+'\n').encode('utf-8'),'text/markdown; charset=utf-8','paper-radar-reading.md'

# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.33: audited research claims grounded in immutable PDF SHA/page quotations.

All extraction remains local to the user's workspace. A human claim is never
upgraded to trusted merely because text resembles a PDF quote. Confirmation is
an explicit, revision-checked action; source and experiment reviews are checked
again on every read/export. No LLM is invoked by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
from urllib.parse import quote as url_quote

from .portal_triage import _md

KIND_LABELS = {'finding': '研究发现', 'comparison': '跨论文比较',
               'limitation': '研究局限', 'next_step': '后续工作'}
MAX_QUOTE = 650
MAX_CLAIM = 1200
MAX_ANCHORS_PER_CLAIM = 12
HEX_ID = re.compile(r'^[a-f0-9]{32}$')
HEX_SHA = re.compile(r'^[a-f0-9]{64}$')


class RevisionConflict(ValueError):
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _clean(value, limit: int, label: str, minimum: int = 1) -> str:
    if type(value) is not str or '\x00' in value or not minimum <= len(value.strip()) <= limit:
        raise ValueError(f'{label}长度必须为 {minimum}–{limit} 个字符')
    return value.strip().replace('\r\n', '\n').replace('\r', '\n')


def _normalized(text: str) -> tuple[str, list[int]]:
    """Collapse Unicode whitespace while keeping offsets in *source* text."""
    chars = []
    offsets = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != ' ':
                chars.append(' ')
                offsets.append(index)
        else:
            chars.append(char)
            offsets.append(index)
    if chars and chars[-1] == ' ':
        chars.pop(); offsets.pop()
    return ''.join(chars), offsets


def _locate(source: str, quote: str) -> tuple[int, int]:
    haystack, mapping = _normalized(source)
    needle, _ = _normalized(quote)
    if not needle or len(needle) < 10:
        raise ValueError('请粘贴至少 10 字符的连续 PDF 原文，保留标点和数值精度')
    start = haystack.find(needle)
    if start < 0:
        raise ValueError('引文未在当前 PDF 页的解析文本中找到；请复制连续原文并检查页码')
    if haystack.find(needle, start + 1) >= 0:
        raise ValueError('同页出现多次相同引文，请增加上下文使原文定位唯一')
    return mapping[start], mapping[start + len(needle) - 1] + 1


class EvidenceChain:
    """Runs inside a caller-owned SQLite transaction. Portal is account-scoped.

    backend='portal' uses parsed pdf_pages and its PDF file; backend='local'
    uses fulltext_chunks and the local downloaded file. The two sources cannot
    silently claim the same level of experiment relationship validation.
    """

    def __init__(self, db, topic: str, *, backend: str, portal=None, uid='', db_path=None):
        if backend not in ('portal', 'local'):
            raise ValueError('Unknown evidence backend')
        from .core import TOPIC_ID_RE
        if type(topic) is not str or not TOPIC_ID_RE.fullmatch(topic):
            raise ValueError('无效的研究主题')
        self.db, self.topic, self.backend = db, topic, backend
        self.portal, self.uid, self.db_path = portal, uid, Path(db_path) if db_path else None
        self._docs = {}
        self._source = {}
        self._experiments = {}
        if backend == 'portal' and portal is None:
            raise ValueError('Account scope required')
        if backend == 'local' and self.db_path is None:
            raise ValueError('Database path required')

    def member(self, paper_id):
        if type(paper_id) is not str or not 1 <= len(paper_id) <= 150 or '\x00' in paper_id:
            raise ValueError('论文 ID 无效')
        exists = self.db.execute('SELECT 1 FROM topic_papers WHERE topic_id=? AND paper_id=?',
                                 (self.topic, paper_id)).fetchone()
        if not exists:
            raise ValueError('论文不存在或不属于当前研究主题')
        return paper_id

    def doc(self, pid):
        if pid in self._docs:
            return self._docs[pid]
        if self.backend == 'portal':
            from .portal_research import path_for, digest
            row = self.db.execute('SELECT sha256,pages FROM pdf_documents WHERE paper_id=?', (pid,)).fetchone()
            sha = row['sha256'] if row else ''
            path = path_for(self.portal, self.uid, pid) if row else None
            ok = bool(row and row['pages'] and path.is_file() and digest(path) == sha)
        else:
            from .downloads.manager import resolve_saved_file
            from .fulltext import _digest_file
            try:
                row = self.db.execute('SELECT pdf_sha256,state FROM fulltext_documents WHERE paper_id=?',
                                      (pid,)).fetchone()
            except __import__('sqlite3').OperationalError:
                row = None
            paper = self.db.execute('SELECT local_file FROM papers WHERE paper_id=?', (pid,)).fetchone()
            path = resolve_saved_file(self.db_path, paper['local_file']) if paper else None
            sha = row['pdf_sha256'] if row else ''
            try:
                ok = bool(sha and row['state'] in ('indexed', 'partial') and path and
                          _digest_file(path) == sha)
            except OSError:
                ok = False
        result = {'sha256': sha, 'valid': ok}
        self._docs[pid] = result
        return result

    def text_candidates(self, pid, sha, page):
        key = (pid, sha, page)
        if key in self._source:
            return self._source[key]
        if self.backend == 'portal':
            rows = self.db.execute('''SELECT 0 AS chunk_id,text FROM pdf_pages
                WHERE paper_id=? AND sha256=? AND page=?''', (pid, sha, page)).fetchall()
        else:
            rows = self.db.execute('''SELECT c.id AS chunk_id,c.content AS text
                FROM fulltext_chunks c JOIN fulltext_documents d ON d.paper_id=c.paper_id
                WHERE c.paper_id=? AND d.pdf_sha256=? AND c.page=? ORDER BY c.id''',
                (pid, sha, page)).fetchall()
        self._source[key] = [(int(row['chunk_id']), row['text']) for row in rows]
        return self._source[key]

    def page_source(self, pid, page):
        """Local/portal source text with paper membership, hash and page checks."""
        pid = self.member(pid)
        if type(page) is not int or not 1 <= page <= 120:
            raise ValueError('PDF 页码须为 1–120 的整数')
        doc = self.doc(pid)
        if not doc['valid']:
            return {'paper_id':pid,'page':page,'sha256':doc['sha256'],
                    'valid':False,'chunks':[]}
        items = self.text_candidates(pid,doc['sha256'],page)
        return {'paper_id':pid,'page':page,'sha256':doc['sha256'],'valid':True,
                'chunks':[{'chunk_id':i,'text':text[:12000]} for i,text in items[:12]],
                'truncated':len(items)>12 or any(len(text)>12000 for _,text in items[:12])}

    def experiment(self, pid, exp_id, sha):
        key = (pid, exp_id, sha)
        if key in self._experiments:
            return self._experiments[key]
        if self.backend == 'portal':
            row = self.db.execute('''SELECT v.id,v.page,v.quote,v.sha256,v.dataset,v.metric,v.value,
                v.baseline,v.setting,COALESCE(r.decision,'needs_review') AS review_decision
                FROM verified_evidence v LEFT JOIN evidence_reviews r
                    ON r.evidence_id=v.id AND r.sha256=v.sha256
                WHERE v.id=? AND v.paper_id=? AND v.sha256=?''', (exp_id,pid,sha)).fetchone()
        else:
            try:
                row = self.db.execute('''SELECT CAST(id AS TEXT) AS id,page,quote,pdf_sha256 AS sha256,
                    dataset,metric,value,baseline,setting,'needs_review' AS review_decision
                    FROM experimental_findings WHERE id=? AND paper_id=? AND pdf_sha256=?''',
                    (int(exp_id),pid,sha)).fetchone()
            except __import__('sqlite3').OperationalError:
                row = None
        result = dict(row) if row else None
        self._experiments[key] = result
        return result

    def add_anchor(self, pid, page, quote, *, experiment_id='', expected_sha256=''):
        pid = self.member(pid)
        if type(page) is not int or not 1 <= page <= 120:
            raise ValueError('PDF 页码须为 1–120 的整数')
        quote = _clean(quote, MAX_QUOTE, '原文引文', minimum=10)
        if type(experiment_id) is not str or len(experiment_id) > 80:
            raise ValueError('实验记录编号无效')
        if type(expected_sha256) is not str or (expected_sha256 and not HEX_SHA.fullmatch(expected_sha256)):
            raise ValueError('PDF 指纹格式无效')
        doc = self.doc(pid)
        if not doc['valid']:
            raise ValueError('PDF 未完成全文索引、文件已经变更或无法核对')
        if expected_sha256 and expected_sha256 != doc['sha256']:
            raise ValueError('PDF 已更新，请重新打开当前页面')
        matches = []
        for chunk_id, source in self.text_candidates(pid, doc['sha256'], page):
            try:
                a,b = _locate(source, quote)
                matches.append((chunk_id,a,b))
            except ValueError as exc:
                if '同页出现多次' in str(exc):
                    raise
        if len(matches) != 1:
            raise ValueError('无法唯一定位原文：请核对 PDF 页码，或增加前后文')
        if experiment_id:
            if self.backend == 'portal' and not HEX_SHA.fullmatch(experiment_id):
                raise ValueError('实验候选 ID 不正确')
            if self.backend == 'local' and (not experiment_id.isdigit() or len(experiment_id)>12):
                raise ValueError('实验候选 ID 不正确')
            experiment = self.experiment(pid, experiment_id, doc['sha256'])
            if not experiment or experiment['page'] != page or _normalized(experiment['quote'])[0] != _normalized(quote)[0]:
                raise ValueError('实验记录与引用的 PDF 原文不一致，请使用该实验卡片的完整引文')
        note_table = 'paper_notes' if self.backend == 'portal' else 'local_paper_notes'
        note_row = self.db.execute(
            f'SELECT revision,note,next_action FROM {note_table} WHERE topic_id=? AND paper_id=?',
            (self.topic, pid)).fetchone()
        note_revision = int(note_row['revision']) if note_row else 0
        note_snapshot = note_row['note'] if note_row else ''
        note_action_snapshot = note_row['next_action'] if note_row else 'none'
        anchor_id = secrets.token_hex(16)
        created = stamp()
        chunk_id, start, end = matches[0]
        self.db.execute('''INSERT INTO research_anchors
            (id,topic_id,paper_id,pdf_sha256,page,quote,quote_start,quote_end,chunk_id,experiment_id,note_revision,note_snapshot,note_action_snapshot,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (anchor_id,self.topic,pid,doc['sha256'],page,quote,start,end,chunk_id,experiment_id,note_revision,note_snapshot,note_action_snapshot,created))
        return {'id':anchor_id,'topic_id':self.topic,'paper_id':pid,'pdf_sha256':doc['sha256'],
                'page':page,'quote':quote,'quote_start':start,'quote_end':end,'chunk_id':chunk_id,
                'experiment_id':experiment_id,'note_revision':note_revision,
                'note_snapshot':note_snapshot,'note_action_snapshot':note_action_snapshot,'created_at':created}

    def anchor_status(self, row):
        """Never preserve an old approval when PDF bytes or experiment review changes."""
        pid = row['paper_id']
        doc = self.doc(pid)
        if not doc['valid'] or row['pdf_sha256'] != doc['sha256']:
            return 'stale', None
        candidates = self.text_candidates(pid, doc['sha256'], row['page'])
        # Require the same parsed chunk and the same character span, not a new
        # occurrence of a matching sentence elsewhere in the document.
        fresh = False
        for chunk_id, content in candidates:
            if chunk_id != row['chunk_id']:
                continue
            start,end = row['quote_start'],row['quote_end']
            if start >= 0 and end <= len(content) and start < end:
                if _normalized(content[start:end])[0] == _normalized(row['quote'])[0]:
                    fresh = True
                    break
        if not fresh:
            return 'stale', None
        if row['experiment_id']:
            exp = self.experiment(pid,row['experiment_id'],doc['sha256'])
            if not exp or exp['page'] != row['page'] or _normalized(exp['quote'])[0] != _normalized(row['quote'])[0]:
                return 'stale', None
            if exp['review_decision'] != 'approved':
                return 'needs_review', exp
            return 'ready', exp
        return 'ready', None

    def _rows(self, *, paper_id=''):
        where='WHERE a.topic_id=?'
        args=[self.topic]
        if paper_id:
            self.member(paper_id)
            where+=' AND a.paper_id=?';args.append(paper_id)
        return self.db.execute('''SELECT a.*,p.title,p.doi FROM research_anchors a
            JOIN papers p ON p.''' + ('id' if self.backend=='portal' else 'paper_id') + '''=a.paper_id
            '''+where+' ORDER BY a.created_at DESC,a.id DESC LIMIT 1200',args).fetchall()

    def read(self, *, paper_id=''):
        rows=[dict(row) for row in self._rows(paper_id=paper_id)]
        for row in rows:
            status, exp = self.anchor_status(row)
            row['status'] = status
            row['experiment'] = {k:exp[k] for k in ('dataset','metric','value','baseline','setting','review_decision')} if exp else None
        q='SELECT * FROM research_claims c WHERE c.topic_id=?'
        args=[self.topic]
        if paper_id:
            q+=''' AND EXISTS(SELECT 1 FROM research_claim_links l
                JOIN research_anchors a ON a.id=l.anchor_id AND a.topic_id=l.topic_id
                WHERE l.claim_id=c.id AND l.topic_id=c.topic_id AND a.paper_id=?)'''
            args.append(paper_id)
        q+=' ORDER BY c.updated_at DESC,c.id DESC LIMIT 501'
        claim_rows=self.db.execute(q,args).fetchall()
        claims=[dict(row) for row in claim_rows[:500]]
        ids=[c['id'] for c in claims]
        links=(self.db.execute('''SELECT l.claim_id,l.anchor_id FROM research_claim_links l
            WHERE l.topic_id=? AND l.claim_id IN ('''+','.join('?' for _ in ids)+')',
            (self.topic,*ids)).fetchall() if ids else [])
        by_claim={}
        for row in links:by_claim.setdefault(row['claim_id'],[]).append(row['anchor_id'])
        # For filtered view we still need all anchors referenced by a cross-paper claim.
        needed={aid for c in claims for aid in by_claim.get(c['id'],[])}
        known={r['id']:r for r in rows}
        missing=needed-known.keys()
        if missing:
            extra=self.db.execute('''SELECT a.*,p.title,p.doi FROM research_anchors a
                JOIN papers p ON p.''' + ('id' if self.backend=='portal' else 'paper_id') + '''=a.paper_id
                WHERE a.topic_id=? AND a.id IN ('''+','.join('?' for _ in missing)+')',
                (self.topic,*missing)).fetchall()
            all_rows=[dict(r) for r in extra]
            for row in all_rows:
                row['status'],exp=self.anchor_status(row)
                row['experiment']={k:exp[k] for k in ('dataset','metric','value','baseline','setting','review_decision')} if exp else None
                known[row['id']]=row
        note_table = 'paper_notes' if self.backend == 'portal' else 'local_paper_notes'
        note_rows=self.db.execute(f'SELECT paper_id,revision,note,next_action FROM {note_table} WHERE topic_id=?',
                                  (self.topic,)).fetchall()
        notes={n['paper_id']:n for n in note_rows}
        for evidence in known.values():
            saved=notes.get(evidence['paper_id'])
            version=int(evidence.get('note_revision') or 0)
            evidence['reading_note']={
                'linked_revision':version,
                'current_revision':int(saved['revision']) if saved else 0,
                'state':('unlinked' if version==0 else 'removed' if not saved else
                         'current' if version==saved['revision'] else 'changed'),
                'next_action':saved['next_action'] if saved else 'none',
                'excerpt':(saved['note'][:240] if saved else ''),
                'snapshot_excerpt':(evidence.get('note_snapshot') or '')[:240],
                'snapshot_action':evidence.get('note_action_snapshot') or 'none'}
        for claim in claims:
            claim['anchor_ids']=by_claim.get(claim['id'],[])
            claim['anchors']=[known[aid] for aid in claim['anchor_ids'] if aid in known]
            claim['effective_status'], claim['blockers']=self.claim_effective(claim)
        return {'topic_id':self.topic,'paper_id':paper_id,'anchors':rows,'claims':claims,
                'counts':{'anchors':len(rows),'claims':len(claims),
                  'confirmed_current':sum(c['effective_status']=='confirmed' for c in claims),
                  'requires_attention':sum(c['effective_status'] in ('stale','needs_review') for c in claims)},
                'limits':{'anchors':1200,'claims':500},
                'truncated':len(claim_rows)>500 or len(rows)>=1200,
                'notice':'原文定位来自已解析的 PDF；实验字段和跨论文数值可比性须人工核查。'}

    def history(self, claim_id, limit=40):
        # Return immutable, topic-scoped revision history for one human claim.
        if type(claim_id) is not str or not HEX_ID.fullmatch(claim_id):
            raise ValueError('无效的研究结论编号')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('历史记录条数须在 1–100 之间')
        exists=self.db.execute('SELECT 1 FROM research_claims WHERE id=? AND topic_id=?',
                               (claim_id,self.topic)).fetchone()
        if not exists:raise ValueError('当前主题中不存在这条研究结论')
        rows=self.db.execute('''SELECT revision,decision,body,anchors_json,review_note,event_at
            FROM research_claim_audit WHERE topic_id=? AND claim_id=?
            ORDER BY seq DESC LIMIT ?''',(self.topic,claim_id,limit)).fetchall()
        return {'claim_id':claim_id,'history':[{'revision':r['revision'],
            'action':r['decision'],'body':r['body'],
            'anchor_ids':json.loads(r['anchors_json']),
            'review_note':r['review_note'],'event_at':r['event_at']} for r in rows]}

    def _ids(self, anchor_ids):
        if type(anchor_ids) is not list or not 1 <= len(anchor_ids) <= MAX_ANCHORS_PER_CLAIM:
            raise ValueError('结论必须关联 1–12 条原文证据')
        if len(set(anchor_ids)) != len(anchor_ids) or any(type(x) is not str or not HEX_ID.fullmatch(x) for x in anchor_ids):
            raise ValueError('证据编号无效或存在重复')
        rows=self.db.execute('SELECT * FROM research_anchors WHERE topic_id=? AND id IN ('+
            ','.join('?' for _ in anchor_ids)+')',(self.topic,*anchor_ids)).fetchall()
        if len(rows)!=len(anchor_ids):raise ValueError('证据不属于当前主题或不存在')
        by_id={r['id']:dict(r) for r in rows}
        return [by_id[x] for x in anchor_ids]

    def _event(self, row, action, anchor_ids):
        self.db.execute('''INSERT INTO research_claim_audit
            (topic_id,claim_id,revision,decision,body,anchors_json,review_note,event_at)
            VALUES (?,?,?,?,?,?,?,?)''',(self.topic,row['id'],row['revision'],action,
                row['body'],json.dumps(anchor_ids,ensure_ascii=False),row['review_note'],stamp()))

    def save_claim(self, body, kind, anchor_ids, expected_revision, *, claim_id=''):
        body=_clean(body,MAX_CLAIM,'研究结论',minimum=12)
        if kind not in KIND_LABELS:raise ValueError('研究结论类型无效')
        if type(expected_revision) is not int or not 0<=expected_revision<1_000_000_000:
            raise ValueError('结论版本编号无效')
        self._ids(anchor_ids)
        at=stamp()
        if expected_revision == 0:
            if claim_id:raise ValueError('新建结论不得指定已有编号')
            claim_id=secrets.token_hex(16)
            self.db.execute('''INSERT INTO research_claims
                (id,topic_id,body,kind,decision,revision,review_note,created_at,updated_at)
                VALUES (?,?,?,?,'draft',1,'',?,?)''',(claim_id,self.topic,body,kind,at,at))
        else:
            if type(claim_id) is not str or not HEX_ID.fullmatch(claim_id):
                raise ValueError('结论编号无效')
            result=self.db.execute('''UPDATE research_claims SET body=?,kind=?,decision='draft',
                revision=revision+1,review_note='',updated_at=?
                WHERE topic_id=? AND id=? AND revision=?''',
                (body,kind,at,self.topic,claim_id,expected_revision))
            if result.rowcount != 1:
                raise RevisionConflict('研究结论已在其他窗口修改，请保存草稿并刷新核对')
            self.db.execute('DELETE FROM research_claim_links WHERE claim_id=? AND topic_id=?',(claim_id,self.topic))
        self.db.executemany('''INSERT INTO research_claim_links (claim_id,anchor_id,topic_id)
            VALUES (?,?,?)''',[(claim_id,aid,self.topic) for aid in anchor_ids])
        row=dict(self.db.execute('SELECT * FROM research_claims WHERE id=?',(claim_id,)).fetchone())
        self._event(row,'saved_draft',anchor_ids)
        return {'id':claim_id,'revision':row['revision'],'decision':'draft'}

    def _pair_ready(self, anchors, experiment_rows):
        if self.backend!='portal':
            return ['桌面版实验结果尚无实验条件人工复核，跨论文数值比较不能确认']
        # One reviewed experiment anchor per paper is sufficient for a pair;
        # all paper-pairs must have an explicit current comparable review.
        if len({a['paper_id'] for a in anchors})<2:
            return ['跨论文比较至少需要两篇不同论文']
        by_paper={}
        for anchor in anchors:
            if not anchor['experiment_id']:
                return ['跨论文数值比较仅允许使用已复核的实验记录证据']
            by_paper.setdefault(anchor['paper_id'],[]).append(anchor)
        from .comparison_policy import pair_key
        papers=sorted(by_paper)
        for i,pa in enumerate(papers):
            for pb in papers[i+1:]:
                exists=False
                for left in by_paper[pa]:
                    for right in by_paper[pb]:
                        ids=sorted((left['experiment_id'],right['experiment_id']))
                        pair=self.db.execute('''SELECT decision,sha_a,sha_b FROM comparison_pair_reviews
                            WHERE id=? AND topic_id=?''',
                            (self.topic+'|'+pair_key(*ids),self.topic)).fetchone()
                        if (pair and pair['decision']=='comparable' and
                              pair['sha_a']==(left if ids[0]==left['experiment_id'] else right)['pdf_sha256'] and
                              pair['sha_b']==(right if ids[1]==right['experiment_id'] else left)['pdf_sha256']):
                            exists=True
                if not exists:return ['缺少 '+pa+' 与 '+pb+' 的有效可比性人工复核']
        return []

    def claim_effective(self, claim):
        anchors=claim.get('anchors') or []
        if not anchors:return 'stale',['结论关联的原文证据不存在']
        status=[a['status'] for a in anchors]
        if 'stale' in status:return 'stale',['有原文引文或 PDF 指纹失效']
        if 'needs_review' in status:return 'needs_review',['实验字段尚未通过人工复核']
        if claim['kind']=='comparison':
            issues=self._pair_ready(anchors,{})
            if issues:return 'needs_review',issues
        return ('confirmed' if claim['decision']=='confirmed' else 'draft'),[]

    def confirm(self, claim_id, expected_revision, review_note, *, decision='confirmed'):
        if type(claim_id) is not str or not HEX_ID.fullmatch(claim_id):
            raise ValueError('结论编号无效')
        if type(expected_revision) is not int or expected_revision<1:
            raise ValueError('修订版本号无效')
        if decision not in ('confirmed','draft'):
            raise ValueError('确认动作无效')
        review_note=_clean(review_note,360,'人工复核说明',minimum=8) if decision=='confirmed' else ''
        claim=self.db.execute('SELECT * FROM research_claims WHERE id=? AND topic_id=?',
                              (claim_id,self.topic)).fetchone()
        if not claim or claim['revision']!=expected_revision:
            raise RevisionConflict('研究结论版本已更新，请刷新后重新核对')
        anchors=self._ids([row['anchor_id'] for row in self.db.execute('''SELECT anchor_id
            FROM research_claim_links WHERE claim_id=? AND topic_id=? ORDER BY rowid''',
            (claim_id,self.topic)).fetchall()])
        entries=[]
        for r in anchors:
            status,exp=self.anchor_status(r)
            entries.append({**r,'status':status,'experiment':exp})
        effective,issues=self.claim_effective({**dict(claim),'anchors':entries})
        if decision=='confirmed' and issues:
            raise ValueError('无法确认：'+'；'.join(issues))
        at=stamp()
        updated=self.db.execute('''UPDATE research_claims SET decision=?,review_note=?,
            revision=revision+1,updated_at=? WHERE id=? AND topic_id=? AND revision=?''',
            (decision,review_note,at,claim_id,self.topic,expected_revision))
        if updated.rowcount!=1:
            raise RevisionConflict('研究结论版本已更新，请刷新后重新核对')
        row=dict(self.db.execute('SELECT * FROM research_claims WHERE id=?',(claim_id,)).fetchone())
        self._event(row,'confirmed' if decision=='confirmed' else 'reopened',
                    [a['id'] for a in anchors])
        return {'id':claim_id,'revision':row['revision'],'decision':decision,
                'effective_status':decision,'reviewed_at':at}

    def report_section(self) -> tuple[str, dict]:
        """Only live, explicitly human-confirmed findings enter a report snapshot."""
        data=self.read()
        trusted=[c for c in data['claims'] if c['effective_status']=='confirmed']
        blocked=[c for c in data['claims'] if c['effective_status'] in ('stale','needs_review')]
        result={'confirmed':len(trusted),'blocked':len(blocked),
                'draft':sum(c['effective_status']=='draft' for c in data['claims']),
                'truncated':bool(data['truncated'])}
        lines=['## 人工核对的研究结论（生成时快照）','',
               '下列结论由用户填写并在生成本报告时重新核对了 PDF 指纹、原文引文与已登记的实验复核。'
               '后续 PDF 或人工复核发生变化时，应重新打开证据链检查当前状态。','']
        if not trusted:lines.append('暂无当前有效的人工确认结论。')
        if data['truncated']:
            lines.append('**警告：工作区超出单次证据链读取上限。本节仅覆盖已读取的结论，请分主题整理并重新生成。**')
            lines.append('')
        for index,claim in enumerate(trusted,1):
            lines.extend([f'### {index}. '+_md(KIND_LABELS[claim['kind']]),'',_md(claim['body']),''])
            for a in claim['anchors']:
                lines.extend(['- '+_md(a['title']+' · PDF 第 '+str(a['page'])+' 页 · SHA256 '+a['pdf_sha256'][:16]+'…')+
                              ' · [证据 '+a['id'][:12]+']',
                              '  > '+_md(a['quote'])])
                if a.get('reading_note',{}).get('linked_revision'):
                    linked=a['reading_note']
                    lines.append('  - 阅读笔记关联：v'+str(linked['linked_revision'])+
                        ('（当前笔记已修改，请复核原有解读）' if linked['state']!='current' else ''))
            lines.append('')
        if blocked:lines.extend(['',f'**{len(blocked)} 条人工结论的原文或实验条件需要复核，未纳入本节有效结论。**'])
        if result['draft']:lines.append(f'另有 {result["draft"]} 条待确认草稿。')
        return '\n'.join(lines)+'\n',result

    def export_markdown(self) -> str:
        data=self.read()
        claims=data['claims'];anchors={a['id']:a for a in data['anchors']}
        lines=['# 研究结论与 PDF 证据链','',
               '本清单由用户编写并明确复核的结论与原文证据构成。实验比较需另外满足实验条件复核。',
               'PDF 更新会使原有引文自动失效；下列状态为导出时重新核验的结果。','']
        labels={'confirmed':'当前有效 · 人工已确认','draft':'人工草稿 · 未确认',
                'needs_review':'待人工复核','stale':'原文或实验依据已失效'}
        if data['truncated']:
            lines.extend(['**警告：证据链条目超出当前导出上限，下文并非完整清单。请分主题导出。**',''])
        for claim in claims:
            lines.extend(['## '+_md(KIND_LABELS[claim['kind']])+' · '+labels[claim['effective_status']],
                          '',_md(claim['body']),''])
            if claim['review_note']:lines.append('复核记录：'+_md(claim['review_note']))
            if claim['blockers']:lines.append('需处理：'+_md('；'.join(claim['blockers'])))
            for index,aid in enumerate(claim['anchor_ids'],1):
                r=anchors.get(aid)
                if not r:continue
                ref=f'{r["paper_id"]} · PDF 第 {r["page"]} 页 · SHA256 {r["pdf_sha256"][:16]}…'
                lines += ['',f'- [{index}] '+_md(ref)+' · '+_md(r['status']),
                          '  > '+_md(r['quote'])]
                if r.get('reading_note'):
                    n=r['reading_note']
                    if n['linked_revision']:
                        lines.append('  关联阅读笔记：版本 '+str(n['linked_revision'])+' · '+n['state'])
                        if r.get('note_snapshot'):
                            lines.append('  建立证据时的阅读笔记：'+_md(r['note_snapshot']))
                        if n['state']=='changed' and n['excerpt']:
                            lines.append('  当前阅读笔记摘录：'+_md(n['excerpt']))
                if r['experiment']:
                    exp=r['experiment']
                    lines.append('  实验记录：'+_md(exp['dataset']+' / '+exp['metric']+' = '+exp['value']))
            lines.append('')
        return '\n'.join(lines)+'\n'


def portal_chain(portal, uid, topic, operation, *args, **kwargs):
    """One account-scoped transaction; writers take the PDF replacement lock."""
    portal.available(uid, topic)
    from contextlib import nullcontext
    guard = portal.pdf_lock if operation in ('add_anchor','confirm','report_section') else nullcontext()
    with guard:
        with portal.workspace(uid) as db:
            chain=EvidenceChain(db,topic,backend='portal',portal=portal,uid=uid)
            return getattr(chain,operation)(*args,**kwargs)


def local_chain(topic, operation, *args, db_path=None, **kwargs):
    from .core import DB_FILE, open_database
    path=Path(db_path) if db_path is not None else DB_FILE
    with open_database(path) as db:
        chain=EvidenceChain(db,topic,backend='local',db_path=path)
        return getattr(chain,operation)(*args,**kwargs)

# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Topic-scoped, resumable PDF -> experiment -> human-review workflow.

All provisional records are tied to an on-disk PDF digest, page and exact
chunk quotation. Machine extraction never marks a scientific result approved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import csv
import io
import json
import re
import sqlite3

from .core import TOPIC_ID_RE, open_database
from .downloads.manager import download_selected
from .fulltext import _digest_file, index_paper, paper_status
from .experiments import read_findings, selected_experiment_chunks, EXPERIMENT_HINT, NUMBER
from .evidence_chain import EvidenceChain, _locate
from .research_screening import active_facets, screen_paper

BATCH_SIZE = 12
MAX_CANDIDATES_PER_PAPER = 12
MAX_QUOTE = 550
STEPS = ('download', 'index', 'extract', 'review')
METRIC = re.compile(r'\b(?:relative\s+L\s*2\s+error|L\s*2\s+error|RMSE|NRMSE|MSE|MAE|MRE|R\^?2|accuracy|F1(?:-score)?|PSNR|SSIM|relative\s+error)\b', re.I)
TERMINAL = frozenset(('ready_for_review', 'no_pdf', 'no_text', 'no_experiment', 'failed'))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def init_schema(db: sqlite3.Connection) -> None:
    db.execute('''CREATE TABLE IF NOT EXISTS evidence_batch_runs (
        id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, question TEXT NOT NULL,
        use_model INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'queued',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        model_status TEXT NOT NULL DEFAULT 'not_requested'
    )''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(evidence_batch_runs)')}
    if 'model_status' not in columns:
        db.execute("ALTER TABLE evidence_batch_runs ADD COLUMN model_status TEXT NOT NULL DEFAULT 'not_requested'")
    db.execute('''CREATE TABLE IF NOT EXISTS evidence_batch_items (
        run_id TEXT NOT NULL, paper_id TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'queued',
        state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '', candidate_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY(run_id,paper_id)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS evidence_batch_candidates (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, topic_id TEXT NOT NULL,
        paper_id TEXT NOT NULL, pdf_sha256 TEXT NOT NULL, chunk_id INTEGER NOT NULL,
        page INTEGER NOT NULL, quote TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'metric',
        dataset TEXT NOT NULL DEFAULT '', metric TEXT NOT NULL DEFAULT '',
        value TEXT NOT NULL DEFAULT '', baseline TEXT NOT NULL DEFAULT '',
        setting TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
        decision TEXT NOT NULL DEFAULT 'pending', revision INTEGER NOT NULL DEFAULT 1,
        anchor_id TEXT NOT NULL DEFAULT '', review_note TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_batch_review ON evidence_batch_candidates(topic_id,decision,run_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS evidence_batch_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, paper_id TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_batch_events_run ON evidence_batch_events(run_id,id)')
    db.commit()


def _topic(topic: str) -> str:
    if type(topic) is not str or not TOPIC_ID_RE.fullmatch(topic):
        raise ValueError('研究主题无效')
    return topic


def _ids(value: list[str]) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= BATCH_SIZE:
        raise ValueError(f'一次请选择 1–{BATCH_SIZE} 篇论文')
    if any(type(item) is not str or not 1 <= len(item) <= 150 or '\x00' in item for item in value):
        raise ValueError('论文编号无效')
    result = list(dict.fromkeys(value))
    if len(result) != len(value):
        raise ValueError('请勿重复选择论文')
    return result


def preview(db, topic: str, question: str, limit: int = BATCH_SIZE) -> dict:
    _topic(topic)
    if type(question) is not str or not 8 <= len(question.strip()) <= 500:
        raise ValueError('请先输入 8–500 字的研究问题')
    if type(limit) is not int or not 1 <= limit <= BATCH_SIZE:
        raise ValueError('论文数量超过上限')
    selected = active_facets(question)
    if not selected:
        from .research_intelligence import _aligned_terms
        words = _aligned_terms(question)
    else:
        words = set()
    rows = db.execute('''SELECT p.paper_id,p.title,p.abstract,p.local_file,p.download_status,
                       COALESCE(t.score,0) AS topic_score
                  FROM papers p JOIN topic_papers t ON t.paper_id=p.paper_id
                  WHERE t.topic_id=? ORDER BY t.score DESC, p.paper_id LIMIT 2000''', (topic,)).fetchall()
    results=[]
    for row in rows:
        data=dict(row)
        screening=screen_paper(data,selected,set()) if selected else None
        if selected and screening is None:
            continue
        if not selected:
            text=(str(data.get('title') or '')+' '+str(data.get('abstract') or '')).casefold()
            hits=sum(1 for word in words if len(word)>=4 and word in text)
            if not hits: continue
            screening={'score':float(hits),'level':'related','reason':'题录关键词匹配','labels':[]}
        results.append({'paper_id':data['paper_id'],'title':data['title'],
            'score':screening['score'],'level':screening['level'],
            'reason':screening['reason'],'has_pdf':bool(data.get('local_file')),
            'download_status':data.get('download_status') or ''})
    results.sort(key=lambda x:(-x['score'],x['paper_id']))
    return {'topic_id':topic,'matches':len(results),'papers':results[:limit],
            'note':'匹配只用于筛选阅读顺序，不能证明实验结论。'}


def _event(db,run_id,pid,stage,outcome,detail=''):
    # Log bounded status only, not paper text, prompts, API credentials or PDFs.
    db.execute('''INSERT INTO evidence_batch_events(run_id,paper_id,stage,outcome,detail,created_at)
                  VALUES (?,?,?,?,?,?)''',(run_id,pid,stage,outcome,str(detail)[:180],now()))


def create_run(db,topic,question,paper_ids,*,use_model=False) -> dict:
    init_schema(db)
    topic=_topic(topic)
    ids=_ids(paper_ids)
    if type(question) is not str or not 8 <= len(question.strip()) <= 500:
        raise ValueError('研究问题长度应在 8–500 字之间')
    if type(use_model) is not bool:raise ValueError('模型开关无效')
    found=db.execute('''SELECT paper_id FROM topic_papers WHERE topic_id=?
        AND paper_id IN ('''+','.join('?' for _ in ids)+')',(topic,*ids)).fetchall()
    if len(found)!=len(ids):raise ValueError('包含不属于当前研究主题的论文')
    run_id=uuid4().hex;stamp=now()
    db.execute('''INSERT INTO evidence_batch_runs(id,topic_id,question,use_model,state,created_at,updated_at)
                  VALUES (?,?,?,?,'queued',?,?)''',(run_id,topic,question.strip(),int(use_model),stamp,stamp))
    db.executemany('''INSERT INTO evidence_batch_items(run_id,paper_id,updated_at) VALUES (?,?,?)''',
                   [(run_id,pid,stamp) for pid in ids])
    _event(db,run_id,'','batch','created',f'papers={len(ids)} model_requested={int(use_model)}')
    db.commit()
    return {'run_id':run_id,'total':len(ids),'state':'queued'}


def resume_run(db,topic,run_id,*,retry_unavailable=False,db_path=None):
    init_schema(db);_topic(topic)
    if type(run_id) is not str or not re.fullmatch(r'[0-9a-f]{32}',run_id):raise ValueError('任务编号无效')
    if type(retry_unavailable) is not bool:raise ValueError('重试选项无效')
    row=db.execute('SELECT * FROM evidence_batch_runs WHERE id=? AND topic_id=?',(run_id,topic)).fetchone()
    if not row:raise ValueError('当前主题没有该批量任务')
    statuses=('queued','running','failed','interrupted','no_text') + (('no_pdf','no_experiment') if retry_unavailable else ())
    q=','.join('?' for _ in statuses)
    result=db.execute(f'''UPDATE evidence_batch_items SET stage='queued',state='queued',message='',updated_at=?
        WHERE run_id=? AND state IN ({q})''',(now(),run_id,*statuses))
    queued=result.rowcount
    if retry_unavailable and db_path is not None:
        # Previously completed paper may have a replaced PDF. Restart only
        # its now-stale candidates; never edit or reuse prior approvals.
        cache={};stale_ids=set()
        for candidate in db.execute('''SELECT * FROM evidence_batch_candidates
                WHERE run_id=? AND topic_id=?''',(run_id,topic)):
            item=dict(candidate)
            if not _current(db,item,Path(db_path),cache):
                stale_ids.add(item['paper_id'])
        if stale_ids:
            ids=sorted(stale_ids)
            marks=','.join('?' for _ in ids)
            retry=db.execute(f'''UPDATE evidence_batch_items SET
                  stage='queued',state='queued',message='',updated_at=?
                  WHERE run_id=? AND paper_id IN ({marks}) AND state='ready_for_review' ''',
                  (now(),run_id,*ids))
            queued+=retry.rowcount
    db.execute("UPDATE evidence_batch_runs SET state='queued',updated_at=? WHERE id=?",(now(),run_id))
    _event(db,run_id,'','batch','resume',f'items_requeued={queued}')
    db.commit()
    return {'run_id':run_id,'queued':queued}


def _stage(db,run_id,pid,stage,state,detail='',*,increment=False,count=None):
    parts=['stage=?','state=?','message=?','updated_at=?']
    args=[stage,state,str(detail)[:180],now()]
    if increment:parts.append('attempts=attempts+1')
    if count is not None:parts.append('candidate_count=?');args.append(int(count))
    db.execute('UPDATE evidence_batch_items SET '+','.join(parts)+' WHERE run_id=? AND paper_id=?',(*args,run_id,pid))
    _event(db,run_id,pid,stage,state,detail)
    db.commit()


def _local_excerpts(db,pid):
    """Unstructured review leads only; do not pretend an automated metric was verified."""
    result=[]
    for row in selected_experiment_chunks(db,pid,max_items=20):
        txt=' '.join(str(row['content'] or '').split())
        if not EXPERIMENT_HINT.search(txt) or not NUMBER.search(txt):continue
        clauses=re.split(r'(?<=[.!?])\s+',txt)
        clause=next((c for c in clauses if EXPERIMENT_HINT.search(c) and NUMBER.search(c) and 24<=len(c)<=MAX_QUOTE),None)
        quote=(clause or txt[:MAX_QUOTE]).strip()
        if len(quote)<24:continue
        metric=METRIC.search(quote)
        value=''
        if metric:
            after=quote[metric.end():metric.end()+100]
            n=NUMBER.search(after)
            if n:value=n.group(0).strip()
        result.append({'chunk_id':row['id'],'page':row['page'],'quote':quote,
                       'kind':'metric','dataset':'','metric':metric.group(0) if metric else '',
                       'value':value,'baseline':'','setting':'','source':'local_excerpt'})
        if len(result)>=MAX_CANDIDATES_PER_PAPER:break
    return result


def _candidate_insert(db,run_id,topic,pid,doc,rows):
    created=0
    for row in rows[:MAX_CANDIDATES_PER_PAPER]:
        quote=str(row.get('quote') or '').strip()
        if not 18<=len(quote)<=MAX_QUOTE:continue
        chunk=db.execute('SELECT page,content FROM fulltext_chunks WHERE id=? AND paper_id=?',
                         (row['chunk_id'],pid)).fetchone()
        if not chunk or int(chunk['page'])!=int(row['page']):continue
        try:_locate(chunk['content'],quote)
        except ValueError:continue
        key='\x1f'.join((topic,pid,doc['pdf_sha256'],str(row['chunk_id']),quote,
                         str(row.get('metric','')),str(row.get('value',''))))
        cid=sha256(key.encode('utf-8')).hexdigest()[:32]
        cur=db.execute('''INSERT OR IGNORE INTO evidence_batch_candidates
            (id,run_id,topic_id,paper_id,pdf_sha256,chunk_id,page,quote,kind,dataset,
             metric,value,baseline,setting,source,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (cid,run_id,topic,pid,doc['pdf_sha256'],row['chunk_id'],row['page'],quote,
             row.get('kind','metric'),row.get('dataset',''),row.get('metric',''),row.get('value',''),
             row.get('baseline',''),row.get('setting',''),row.get('source','local_excerpt'),now()))
        if cur.rowcount:
            created+=1
        else:
            # A second batch may recover an earlier unfinished review. Move
            # that pending item to the latest queue without duplicating it.
            moved=db.execute('''UPDATE evidence_batch_candidates SET run_id=?
                WHERE id=? AND topic_id=? AND decision='pending' ''',
                (run_id,cid,topic))
            if moved.rowcount:created+=1
    db.commit()
    return created


def _model_available(cfg):
    if not cfg.get('llm_data_consent'):
        return False
    from .cloud_api import get_api_key
    try:return bool(get_api_key(cfg.get('api_provider','')))
    except (ValueError,RuntimeError,OSError):return False


def process_run(db_path: Path,run_id: str,config: dict,*,progress=None,logger=None,
                downloader=None,indexer=None,extractor=None) -> dict:
    """Sequential, restartable processing. Failures are recorded per paper."""
    from .agents.research.pipeline import run_experiment_pipeline
    db_path=Path(db_path)
    downloader=downloader or download_selected
    indexer=indexer or index_paper
    extractor=extractor or run_experiment_pipeline
    logger=logger or (lambda message:None)
    with open_database(db_path) as db:
        init_schema(db)
        run=db.execute('SELECT * FROM evidence_batch_runs WHERE id=?',(run_id,)).fetchone()
        if not run:raise ValueError('批量任务不存在')
        ids=[r['paper_id'] for r in db.execute("SELECT paper_id FROM evidence_batch_items WHERE run_id=? AND state='queued' ORDER BY rowid",(run_id,))]
        db.execute("UPDATE evidence_batch_runs SET state='running',updated_at=? WHERE id=?",(now(),run_id))
        _event(db,run_id,'','batch','running',f'queued={len(ids)}')
        db.commit()
        topic=run['topic_id'];use_model=bool(run['use_model']) and _model_available(config)
    failures=0;no_pdf=0;prepared=0;pending=0
    model_attempts=0;model_results=0;model_failures=0
    for index,pid in enumerate(ids,1):
        if progress:progress(index-1,len(ids),'正在准备第 '+str(index)+' 篇论文')
        try:
            with open_database(db_path) as db:
                _stage(db,run_id,pid,'download','running',increment=True)
                result=downloader(db,[pid],config,db_path=db_path,logger=lambda msg:None)[0]
                if result['status'] not in ('downloaded','skipped'):
                    _stage(db,run_id,pid,'download','no_pdf',result.get('status','unavailable'))
                    no_pdf+=1;continue
                _stage(db,run_id,pid,'index','running')
                indexed=indexer(db,pid,db_path=db_path)
                doc=paper_status(db,pid,db_path=db_path)
                if not doc['indexed']:
                    _stage(db,run_id,pid,'index','no_text','PDF 无可读取的索引文本')
                    failures+=1;continue
                _stage(db,run_id,pid,'extract','running',str(indexed.get('pages',0))+' pages')
            model_error=''
            if use_model:
                model_attempts+=1
                try:extractor(pid,config,db_path=db_path,logger=lambda message:None)
                except Exception as exc:
                    # Model errors are not fatal: keep quote-only local leads.
                    model_failures+=1
                    model_error='model_failed_'+type(exc).__name__[:55]
                    with open_database(db_path) as db:
                        _event(db,run_id,pid,'extract','model_failed',type(exc).__name__[:55])
                        db.commit()
            with open_database(db_path) as db:
                doc=paper_status(db,pid,db_path=db_path)
                if not doc['indexed']:
                    _stage(db,run_id,pid,'extract','failed','PDF 在分析过程中改变')
                    failures+=1;continue
                found=read_findings(db,pid,db_path=db_path) if use_model and not model_error else {'findings':[]}
                structured=[{**row,'source':'model_verified_quote'} for row in found['findings']]
                if structured:model_results+=1
                candidates=structured if structured else _local_excerpts(db,pid)
                count=_candidate_insert(db,run_id,topic,pid,doc['document'],candidates)
                _stage(db,run_id,pid,'review',
                       'ready_for_review' if count else 'no_experiment',
                       (model_error+'; ' if model_error else '')+
                       (f'queued={count}' if count else '未发现可定位的实验指标与数值片段'),count=count)
                prepared+=1;pending+=count
                logger(f'证据准备：第 {index}/{len(ids)} 篇，可审核片段 {count} 条。')
        except Exception as exc:
            failures+=1
            with open_database(db_path) as db:
                _stage(db,run_id,pid,'processing','failed',type(exc).__name__[:90])
            logger(f'证据准备：第 {index}/{len(ids)} 篇处理失败，可稍后续跑。')
        finally:
            if progress:progress(index,len(ids),f'已处理 {index}/{len(ids)} 篇')
    with open_database(db_path) as db:
        states=[r['state'] for r in db.execute('SELECT state FROM evidence_batch_items WHERE run_id=?',(run_id,))]
        failed_count=sum(x in ('failed','no_pdf','no_text') for x in states)
        no_experiment=sum(x=='no_experiment' for x in states)
        final='partial' if failed_count or no_experiment else 'complete'
        if not run['use_model']:
            model_status='not_requested'
        elif not use_model:
            model_status='not_ready'
        elif model_results and model_failures:
            model_status='partial'
        elif model_results:
            model_status='structured_results'
        elif model_failures:
            model_status='failed_fallback'
        else:
            model_status='no_structured_results'
        db.execute('UPDATE evidence_batch_runs SET state=?,model_status=?,updated_at=? WHERE id=?',
                   (final,model_status,now(),run_id))
        _event(db,run_id,'','batch',final,f'prepared={prepared} failures={failed_count} review_candidates={pending} model_status={model_status}')
        db.commit()
    return {'run_id':run_id,'prepared':prepared,'no_pdf':no_pdf,'failed':failed_count,
            'no_experiment':no_experiment,'review_candidates':pending,'state':final,'model_used':use_model,
            'model_status':model_status,'model_attempts':model_attempts,
            'model_results':model_results,'model_failures':model_failures}


def _current(db,row,db_path: Path,cache=None)->bool:
    # Digest a PDF only once per request. A large review queue can hold hundreds
    # of excerpts from the same file; repeated SHA256 scans cause UI stalls.
    if cache is None:cache={}
    pid=row['paper_id']
    if pid not in cache:
        cache[pid]=paper_status(db,pid,db_path=Path(db_path))
    doc=cache[pid]
    current=doc.get('document') or {}
    if not doc['indexed'] or current.get('pdf_sha256')!=row['pdf_sha256']:
        return False
    chunk=db.execute('SELECT page,content FROM fulltext_chunks WHERE id=? AND paper_id=?',
                     (row['chunk_id'],row['paper_id'])).fetchone()
    if not chunk or chunk['page']!=row['page']:return False
    try:_locate(chunk['content'],row['quote'])
    except ValueError:return False
    return True


def list_run(db,topic,run_id,*,db_path:Path):
    init_schema(db);topic=_topic(topic)
    if type(run_id) is not str or not re.fullmatch('[0-9a-f]{32}',run_id):raise ValueError('任务编号无效')
    run=db.execute('SELECT * FROM evidence_batch_runs WHERE id=? AND topic_id=?',(run_id,topic)).fetchone()
    if not run:raise ValueError('当前研究主题不存在该任务')
    items=[dict(row) for row in db.execute('''SELECT i.*,p.title FROM evidence_batch_items i
        JOIN papers p ON p.paper_id=i.paper_id WHERE i.run_id=? ORDER BY i.rowid''',(run_id,))]
    candidates=[];file_cache={}
    for row in db.execute('''SELECT c.*,p.title,p.doi FROM evidence_batch_candidates c
        JOIN papers p ON p.paper_id=c.paper_id
        WHERE c.run_id=? AND c.topic_id=? ORDER BY c.paper_id,c.page,c.id LIMIT 150''',(run_id,topic)):
        item=dict(row);item['effective_status']=item['decision'] if _current(db,item,db_path,file_cache) else 'stale'
        candidates.append(item)
    events=[dict(row) for row in db.execute('''SELECT paper_id,stage,outcome,detail,created_at
        FROM evidence_batch_events WHERE run_id=? ORDER BY id DESC LIMIT 120''',(run_id,))]
    return {'run':dict(run),'items':items,'candidates':candidates,'events':events,
            'counts':{'papers':len(items),'pending':sum(c['effective_status']=='pending' for c in candidates),
                      'approved':sum(c['effective_status']=='approved' for c in candidates),
                      'stale':sum(c['effective_status']=='stale' for c in candidates)}}


def latest_run(db,topic):
    init_schema(db);_topic(topic)
    row=db.execute('SELECT id FROM evidence_batch_runs WHERE topic_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(topic,)).fetchone()
    return row['id'] if row else ''


def _grounded_field(value,quote,label,max_chars):
    if type(value) is not str or len(value)>max_chars or '\x00' in value:
        raise ValueError(label+'格式无效')
    text=value.strip()
    if text and ' '.join(text.split()).casefold() not in ' '.join(quote.split()).casefold():
        raise ValueError(label+'必须能在原文引文中直接找到')
    if label=='实验数值' and text:
        # Do not approve 0.012 by substring-matching 0.0123, or silently
        # discard a sign. Keep the exact printed numeric token.
        normalized=' '.join(quote.split())
        token=r'(?<![\d.,+\-])'+re.escape(text)+r'(?![\d.,])'
        if not re.search(token,normalized):
            raise ValueError('实验数值必须与原文中的完整数字一致，包括符号和精度')
    return text


def review_candidate(db,topic,candidate_id,*,revision,decision,metric,value,dataset,baseline,setting,
                     review_note,expected_sha256,db_path):
    init_schema(db);_topic(topic)
    if type(candidate_id) is not str or not re.fullmatch('[0-9a-f]{32}',candidate_id):raise ValueError('记录编号无效')
    if decision not in ('approved','rejected'):raise ValueError('审核结论无效')
    if type(revision) is not int or revision<1:raise ValueError('审核版本无效')
    if type(review_note) is not str or len(review_note)>900:raise ValueError('审核说明过长')
    if type(expected_sha256) is not str or not re.fullmatch('[0-9a-f]{64}',expected_sha256):raise ValueError('PDF 指纹无效')
    row=db.execute('SELECT * FROM evidence_batch_candidates WHERE id=? AND topic_id=?',(candidate_id,topic)).fetchone()
    if not row:raise ValueError('找不到当前主题的证据候选')
    row=dict(row)
    if row['revision']!=revision:raise ValueError('审核记录已经更改，请刷新后操作')
    if row['pdf_sha256']!=expected_sha256 or not _current(db,row,db_path):
        raise ValueError('PDF 已改变或无法定位引文，请重新解析后审核')
    anchor_id=row['anchor_id']
    if decision=='approved':
        metric=_grounded_field(metric,row['quote'],'评价指标',120)
        value=_grounded_field(value,row['quote'],'实验数值',80)
        dataset=_grounded_field(dataset,row['quote'],'数据集',120)
        baseline=_grounded_field(baseline,row['quote'],'对照方法',120)
        setting=_grounded_field(setting,row['quote'],'实验条件',220)
        if not metric or not value or not NUMBER.search(value):
            raise ValueError('确认实验结果必须填写原文中明确出现的评价指标和数值')
        if not review_note.strip():raise ValueError('请填写已核对 PDF 表格或正文的审核说明')
        if not anchor_id:
            chain=EvidenceChain(db,topic,backend='local',db_path=db_path)
            anchor=chain.add_anchor(row['paper_id'],row['page'],row['quote'],
                                     expected_sha256=row['pdf_sha256'])
            anchor_id=anchor['id']
    else:
        metric=row['metric'];value=row['value'];dataset=row['dataset']
        baseline=row['baseline'];setting=row['setting']
        if anchor_id:
            # Withdrawing a batch approval must not leave a reusable active
            # anchor. Never silently remove one cited by a research claim.
            linked=db.execute('''SELECT 1 FROM research_claim_links
                WHERE topic_id=? AND anchor_id=? LIMIT 1''',(topic,anchor_id)).fetchone()
            if linked:
                raise ValueError('这条原文已被研究结论引用；请先解除引用，再排除该实验证据')
            db.execute('DELETE FROM research_anchors WHERE id=? AND topic_id=?',
                       (anchor_id,topic))
            anchor_id=''
    cursor=db.execute('''UPDATE evidence_batch_candidates SET decision=?,revision=revision+1,
          metric=?,value=?,dataset=?,baseline=?,setting=?,anchor_id=?,review_note=?,reviewed_at=?
          WHERE id=? AND topic_id=? AND revision=?''',
          (decision,metric,value,dataset,baseline,setting,anchor_id,review_note.strip(),now(),
           candidate_id,topic,revision))
    if cursor.rowcount!=1:raise ValueError('审核冲突，请刷新')
    _event(db,row['run_id'],row['paper_id'],'review',decision,'revision='+str(revision+1))
    db.commit()
    return {'id':candidate_id,'decision':decision,'revision':revision+1,'anchor_id':anchor_id}


def comparison_matrix(db,topic,*,db_path:Path,paper_ids=None):
    init_schema(db);_topic(topic)
    allowed=None
    if paper_ids is not None:
        if not isinstance(paper_ids,(list,tuple,set)) or len(paper_ids)>1000:
            raise ValueError('论文筛选范围无效')
        allowed=set(paper_ids)
    rows=[];stale=0;file_cache={}
    for row in db.execute('''SELECT c.*,p.title,p.doi,p.url FROM evidence_batch_candidates c
        JOIN papers p ON p.paper_id=c.paper_id WHERE c.topic_id=? AND c.decision='approved'
        ORDER BY c.metric,c.dataset,c.paper_id,c.page LIMIT 1200''',(topic,)):
        data=dict(row)
        if allowed is not None and data['paper_id'] not in allowed:continue
        if _current(db,data,db_path,file_cache):rows.append(data)
        else:stale+=1
    groups={}
    for row in rows:
        key=(' '.join(row['metric'].casefold().split()),' '.join(row['dataset'].casefold().split()))
        groups.setdefault(key,[]).append(row)
    comparisons=[]
    for (metric,dataset),items in groups.items():
        if len({r['paper_id'] for r in items})<2:continue
        comparisons.append({'metric':items[0]['metric'],'dataset':items[0]['dataset'],
            'comparable':False,'status':'requires_method_comparability_review',
            'note':'已人工确认数值及原文位置；仍需核对数据划分、指标定义、单位、基线和计算预算，不自动计算优劣或差值。',
            'entries':[{'paper_id':r['paper_id'],'title':r['title'],'doi':r['doi'],'url':r['url'],
                'page':r['page'],'quote':r['quote'],'pdf_sha256':r['pdf_sha256'],
                'metric':r['metric'],'value':r['value'],'dataset':r['dataset'],
                'baseline':r['baseline'],'setting':r['setting'],'anchor_id':r['anchor_id']}
                for r in items]})
    return {'topic_id':topic,'approved_valid':len(rows),'stale_excluded':stale,
            'groups':comparisons,'note':'只有人工确认并且 PDF 版本有效的数据才能进入比较矩阵。跨论文可比性仍需专门确认。'}


def comparison_csv(matrix:dict)->bytes:
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['论文标题','论文ID','DOI','评价指标','数据集','原文数值','基线','实验条件','PDF页码','原文引文','PDF SHA256','可比性'])
    for group in matrix['groups']:
        for row in group['entries']:
            values=[row['title'],row['paper_id'],row['doi'],group['metric'],group['dataset'],
                    row['value'],row['baseline'],row['setting'],row['page'],row['quote'],row['pdf_sha256'],'待人工核对']
            writer.writerow(["'"+str(x) if str(x).lstrip().startswith(('=','+','-','@')) else x for x in values])
    return ('\ufeff'+stream.getvalue()).encode('utf-8')

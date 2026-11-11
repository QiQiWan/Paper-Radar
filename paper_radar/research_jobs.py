# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Persisted asynchronous local research inquiry jobs (v1.52).

Request text and selected IDs live in the user's local SQLite database and are
never copied to diagnostics. Explicit model transfer consent remains mandatory.
"""
from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from time import monotonic
from uuid import uuid4

from .core import open_database,TOPIC_ID_RE
from .research_intelligence import _question,_paper_rows,_aligned_terms,run_local
from .research_screening import active_facets
from .research_scope import build_scope

BOOT_ID=uuid4().hex
JOB_ID=re.compile(r'^[a-f0-9]{32}$')
STAGES={'queued','running','completed','partial','failed','interrupted'}


def _now():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def init_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS inquiry_jobs_v152 (
        id TEXT PRIMARY KEY,topic_id TEXT NOT NULL,request_json TEXT NOT NULL,
        request_hash TEXT NOT NULL,state TEXT NOT NULL,stage TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,started_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT '',
        boot_id TEXT NOT NULL DEFAULT '',report_id TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT '',error_message TEXT NOT NULL DEFAULT '',
        attempt INTEGER NOT NULL DEFAULT 0,metrics_json TEXT NOT NULL DEFAULT '{}')''')
    db.execute('CREATE INDEX IF NOT EXISTS ix_inquiry_jobs_topic ON inquiry_jobs_v152(topic_id,created_at DESC)')
    db.commit()


def _validate(request):
    allowed={'question','selection_mode','paper_ids','include_ids','exclude_ids','use_model','confirm_transfer'}
    if type(request) is not dict or set(request)-allowed:
        raise ValueError('研究分析参数包含无效字段')
    q=_question(request.get('question'))
    mode=request.get('selection_mode','recommended')
    for item in ('paper_ids','include_ids','exclude_ids'):
        if item in request and type(request[item]) is not list:
            raise ValueError('论文选择参数类型无效')
    use_model=request.get('use_model',False)
    consent=request.get('confirm_transfer',False)
    if type(use_model) is not bool or type(consent) is not bool:
        raise ValueError('模型选项无效')
    if use_model and not consent:raise ValueError('请先授权本次模型数据发送')
    return {'question':q,'selection_mode':mode,'paper_ids':request.get('paper_ids'),
            'include_ids':request.get('include_ids'), 'exclude_ids':request.get('exclude_ids'),
            'use_model':use_model,'confirm_transfer':consent}


def create_job(db_path:Path,topic:str,request:dict):
    if type(topic) is not str or not TOPIC_ID_RE.fullmatch(topic):
        raise ValueError('研究主题编号无效')
    payload=_validate(request)
    with open_database(db_path) as db:
        papers,more=_paper_rows(db,topic,'local')
        if more:raise ValueError('主题超过 1000 篇，请拆分后分析以确保覆盖完整')
        scope=build_scope(papers,payload['question'],_aligned_terms(payload['question']),
                          active_facets(payload['question']),mode=payload['selection_mode'],
                          paper_ids=payload['paper_ids'],include_ids=payload['include_ids'],
                          exclude_ids=payload['exclude_ids'])
        init_schema(db)
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        digest=sha256((topic+'\n'+raw+'\n'+scope['selection_hash']).encode('utf-8')).hexdigest()
        id=uuid4().hex;at=_now()
        db.execute('''INSERT INTO inquiry_jobs_v152
            (id,topic_id,request_json,request_hash,state,stage,progress,detail,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (id,topic,raw,digest,'queued','queued',0,'等待执行',at,at))
    return {'run_id':id,'state':'queued','selected_papers':scope['selected_count'],
            'topic_total':scope['topic_total'],'selection_hash':scope['selection_hash']}


def _row(db,id,topic=None):
    if type(id) is not str or not JOB_ID.fullmatch(id):
        raise ValueError('研究任务编号无效')
    sql='SELECT * FROM inquiry_jobs_v152 WHERE id=?'
    args=[id]
    if topic is not None:sql+=' AND topic_id=?';args.append(topic)
    row=db.execute(sql,tuple(args)).fetchone()
    if not row:raise ValueError('未找到该主题中的分析任务')
    return row


def read_job(db_path:Path,id:str,topic:str|None=None):
    with open_database(db_path) as db:
        init_schema(db)
        row=_row(db,id,topic)
        if row['state']=='running' and row['boot_id']!=BOOT_ID:
            db.execute('''UPDATE inquiry_jobs_v152 SET state='interrupted',stage='interrupted',
                detail='服务重启，任务已暂停；可点击继续',updated_at=? WHERE id=?''',(_now(),id))
            row=_row(db,id,topic)
        record=dict(row)
    # Expose progress and error codes; never expose stored question/credentials.
    record.pop('request_json',None)
    record.pop('request_hash',None)
    record.pop('boot_id',None)
    record['metrics']=json.loads(record.pop('metrics_json') or '{}')
    return record


def update_job(db_path:Path,id:str,*,state=None,stage=None,progress=None,
               detail=None,report_id=None,error_code=None,error_message=None,metrics=None):
    with open_database(db_path) as db:
        init_schema(db)
        _row(db,id)
        sets=['updated_at=?'];vals=[_now()]
        for name,value in [('state',state),('stage',stage),('progress',progress),('detail',detail),
                           ('report_id',report_id),('error_code',error_code),('error_message',error_message)]:
            if value is not None:
                sets.append(name+'=?');vals.append(value)
        if metrics is not None:
            sets.append('metrics_json=?');vals.append(json.dumps(metrics,ensure_ascii=False))
        if state in ('completed','partial','failed'):
            sets.append('completed_at=?');vals.append(_now())
        vals.append(id)
        db.execute('UPDATE inquiry_jobs_v152 SET '+','.join(sets)+' WHERE id=?',vals)


def resume_job(db_path:Path,id:str,topic:str):
    with open_database(db_path) as db:
        init_schema(db)
        row=_row(db,id,topic)
        if row['state']=='running' and row['boot_id']==BOOT_ID:
            raise ValueError('研究任务正在执行，请查看当前进度')
        if row['state']=='completed':
            raise ValueError('研究任务已完成，请查看结果；重新分析请创建新任务')
        if row['state']=='running' and row['boot_id']!=BOOT_ID:pass
        elif row['state'] not in ('interrupted','failed','partial','queued'):
            raise ValueError('当前任务不能续跑')
        db.execute('''UPDATE inquiry_jobs_v152 SET state='queued',stage='queued',
            detail='等待重新执行',progress=0,updated_at=? WHERE id=?''',(_now(),id))
    return {'run_id':id,'state':'queued'}


def process_job(db_path:Path,id:str,cfg:dict,*,progress=None,diagnostic=None):
    with open_database(db_path) as db:
        init_schema(db)
        row=_row(db,id)
        payload=json.loads(row['request_json'])
        topic=row['topic_id']
        started=_now()
        db.execute('''UPDATE inquiry_jobs_v152 SET state='running',stage='collect',
           progress=2,detail='正在准备论文数据',boot_id=?,started_at=?,updated_at=?,attempt=attempt+1
           WHERE id=?''',(BOOT_ID,started,started,id))
    t0=monotonic();stages={}
    def stage_callback(stage,percent,detail):
        stages[stage]=round(monotonic()-t0,3)
        update_job(db_path,id,stage=stage,progress=percent,detail=detail)
        if progress:progress(percent,detail)
    try:
        saved=run_local(db_path,topic,payload['question'],model_config=cfg,
            use_model=payload['use_model'],confirm_transfer=payload['confirm_transfer'],
            selection_mode=payload['selection_mode'],paper_ids=payload['paper_ids'],
            include_ids=payload['include_ids'],exclude_ids=payload['exclude_ids'],
            report_id=id,progress=stage_callback,diagnostic=diagnostic)
        brief=saved['brief'];model=brief.get('model') or {}
        elapsed=round(monotonic()-t0,3)
        # Approximate budget only; chat API currently returns no trusted usage.
        batch_count=int(model.get('batch_attempted') or 0)
        model_estimate=None
        if payload['use_model'] and batch_count:
            input_tokens=sum(max(1,len(str(c.get('abstract_excerpt') or ''))//3)
                for c in brief.get('metadata_candidates',[]))
            output_tokens=batch_count*550
            input_price=float(cfg.get('llm_input_usd_per_million') or 0)
            output_price=float(cfg.get('llm_output_usd_per_million') or 0)
            model_estimate={'input_tokens_estimated':input_tokens,
                'output_tokens_upper_bound':output_tokens,
                'estimated_cost_usd':round((input_tokens*input_price+output_tokens*output_price)/1e6,6)
                    if input_price>0 and output_price>0 else None,
                'usage_type':'estimated_not_provider_reported'}
        outcome='partial' if str(model.get('status','')).startswith('model_failed') or model.get('status')=='model_partially_completed' else 'completed'
        metrics={'elapsed_seconds':elapsed,'stage_offsets_seconds':stages,
            'topic_total':brief.get('selection',{}).get('topic_total',0),
            'selected_papers':brief.get('selection',{}).get('selected_count',0),
            'abstracts_used':brief.get('selection',{}).get('abstract_available_count',0),
            'model_batches':batch_count,'model_batches_succeeded':model.get('batch_succeeded',0),
            'model_cost_estimate':model_estimate}
        update_job(db_path,id,state=outcome,stage='completed',progress=100,
            detail='研究分析已完成，点击查看结果' if outcome=='completed' else
                   '基础分析已完成，部分模型批次失败；可查看结果和日志',
            report_id=saved['id'],metrics=metrics)
        if progress:progress(100,'研究分析已保存')
        return {'run_id':id,'report_id':saved['id'],'state':outcome,'metrics':metrics}
    except Exception as exc:
        # Do not put research question or model response into logs or UI.
        code='INQUIRY_INVALID' if isinstance(exc,ValueError) else 'INQUIRY_RUNTIME_FAILED'
        update_job(db_path,id,state='failed',stage='failed',detail='研究分析未完成',
            error_code=code,error_message=(str(exc)[:160] if isinstance(exc,ValueError) else
                  '分析运行失败，请下载诊断日志查看对应任务编号'),
            metrics={'elapsed_seconds':round(monotonic()-t0,3),'stage_offsets_seconds':stages})
        raise


def latest_job(db_path:Path,topic:str):
    with open_database(db_path) as db:
        init_schema(db)
        row=db.execute('''SELECT id FROM inquiry_jobs_v152 WHERE topic_id=?
            ORDER BY created_at DESC,id DESC LIMIT 1''',(topic,)).fetchone()
    return read_job(db_path,row['id'],topic) if row else {'state':'none'}

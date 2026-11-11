# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.45: topic-isolated research memory, event delta and periodic brief engine.

No model calls during scheduled runs. Confirmed facts retain verified claim IDs;
user-written hypotheses remain labeled, feedback changes recommendations only,
and historic briefs are immutable evidence snapshots.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import secrets
import sqlite3
from pathlib import Path

from .evidence_chain import EvidenceChain
from .research_synthesis_agent import (
    FINGERPRINT_VERSION, _source_fingerprint, claim_evidence_record, tokens, utc_now)

CADENCES=('daily','weekly','monthly')
KINDS=('note','question','hypothesis','confirmed_claim')
HEX_ID=re.compile(r'^[a-f0-9]{32}$')
MAX_EVENTS=300


def _time(value=None):
    if value is None:return datetime.now(timezone.utc)
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None:
        raise ValueError('请输入带时区的时间')
    return value.astimezone(timezone.utc)


def _window(cadence,now=None):
    t=_time(now)
    if cadence=='daily':end=t.replace(hour=0,minute=0,second=0,microsecond=0);start=end-timedelta(days=1)
    elif cadence=='weekly':
        end=(t-timedelta(days=t.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
        start=end-timedelta(days=7)
    elif cadence=='monthly':
        end=t.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        start=end.replace(year=end.year-1,month=12) if end.month==1 else end.replace(month=end.month-1)
    else:raise ValueError('周期必须为 daily、weekly 或 monthly')
    return start,end


def _claims(chain):
    data=chain.read()
    return data,{c['id']:c for c in data['claims'] if c['effective_status']=='confirmed'}


def confirmed_fingerprint(confirmed: dict, ids: list[str]) -> str:
    """Immutable signature of cited claim content+revision, not just IDs."""
    rows=[claim_evidence_record(confirmed[key]) for key in sorted(ids)]
    return sha256(json.dumps({'version':FINGERPRINT_VERSION,'claims':rows},
                             ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def read_memory(db,topic,chain,*,limit=80):
    if type(limit) is not int or not 1<=limit<=200:raise ValueError('记忆条数无效')
    _,confirmed=_claims(chain)
    items=[]
    for row in db.execute('''SELECT * FROM research_memory_items
            WHERE topic_id=? ORDER BY updated_at DESC,id DESC LIMIT ?''',(topic,limit)):
        item=dict(row)
        references=json.loads(item.pop('claim_ids_json'))
        good=bool(references) and all(x in confirmed for x in references)
        if good:
            good=(item.pop('claim_fingerprint') == confirmed_fingerprint(confirmed,references))
        else:
            item.pop('claim_fingerprint')
        if item['status']=='retired':
            item['evidence_status']='retired'
        elif item['kind']=='confirmed_claim':
            item['evidence_status']='current' if good else 'needs_review'
        else:
            item['evidence_status']='user_note_unverified'
        item['claim_ids']=references
        items.append(item)
    return {'topic_id':topic,'items':items,'confirmed_claims_available':len(confirmed)}


def save_memory(db,topic,chain,*,kind,body,claim_ids=None,expected_revision=0,item_id=''):
    if kind not in KINDS:raise ValueError('无效的记忆类别')
    if type(body) is not str or not 8<=len(body.strip())<=1200 or '\x00' in body:
        raise ValueError('记忆内容须为 8–1200 字')
    if type(expected_revision) is not int or not 0<=expected_revision<1_000_000:
        raise ValueError('记忆版本号无效')
    references=claim_ids if claim_ids is not None else []
    if type(references) is not list or len(references)>10 or any(type(x) is not str or not HEX_ID.fullmatch(x) for x in references):
        raise ValueError('确认结论编号格式无效')
    _,confirmed=_claims(chain)
    if kind=='confirmed_claim':
        if len(references)!=1 or any(x not in confirmed for x in references):
            raise ValueError('确认型记忆必须关联且仅关联一条当前有效的人工确认结论')
        if body.strip()!=confirmed[references[0]]['body'].strip():
            raise ValueError('确认型记忆必须完整保留人工确认结论原文；个人解释请另存工作假设')
    elif references and any(x not in confirmed for x in references):
        raise ValueError('所引用的研究结论已经失效或不存在')
    now=utc_now();refs=json.dumps(list(dict.fromkeys(references)),separators=(',',':'))
    fingerprint=confirmed_fingerprint(confirmed,references) if references else ''
    if expected_revision==0:
        if item_id:raise ValueError('新建记忆不能指定已有 ID')
        item_id=secrets.token_hex(16)
        db.execute('''INSERT INTO research_memory_items
            (id,topic_id,kind,body,claim_ids_json,claim_fingerprint,revision,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,1,'active',?,?)''',(item_id,topic,kind,body.strip(),refs,fingerprint,now,now))
        revision=1;action='create'
    else:
        if type(item_id) is not str or not HEX_ID.fullmatch(item_id):raise ValueError('记忆 ID 无效')
        updated=db.execute('''UPDATE research_memory_items SET kind=?,body=?,claim_ids_json=?,
            claim_fingerprint=?,revision=revision+1,status='active',updated_at=?
            WHERE topic_id=? AND id=? AND revision=? AND status='active' ''',
            (kind,body.strip(),refs,fingerprint,now,topic,item_id,expected_revision))
        if updated.rowcount!=1:raise ValueError('记忆已被其他修改覆盖，请刷新后重试')
        revision=expected_revision+1;action='revise'
    db.execute('''INSERT INTO research_memory_audit
        (topic_id,item_id,revision,action,body,claim_ids_json,event_at)
        VALUES (?,?,?,?,?,?,?)''',(topic,item_id,revision,action,body.strip(),refs,now))
    return {'id':item_id,'revision':revision,'kind':kind,'evidence_status':
            'current' if kind=='confirmed_claim' else 'user_note_unverified'}


def retire_memory(db,topic,item_id,expected_revision):
    if type(item_id) is not str or not HEX_ID.fullmatch(item_id) or type(expected_revision) is not int:
        raise ValueError('记忆编号或版本无效')
    row=db.execute('SELECT body,claim_ids_json FROM research_memory_items WHERE id=? AND topic_id=?',
                   (item_id,topic)).fetchone()
    if not row:raise ValueError('记忆不存在')
    rev=db.execute('''UPDATE research_memory_items SET status='retired',revision=revision+1,updated_at=?
        WHERE id=? AND topic_id=? AND revision=? AND status='active' ''',
        (utc_now(),item_id,topic,expected_revision))
    if rev.rowcount!=1:raise ValueError('记忆已修改或已退休，请刷新')
    db.execute('''INSERT INTO research_memory_audit
        (topic_id,item_id,revision,action,body,claim_ids_json,event_at)
        VALUES (?,?,?,?,?,?,?)''',(topic,item_id,expected_revision+1,'retire',row['body'],row['claim_ids_json'],utc_now()))
    return {'id':item_id,'status':'retired','revision':expected_revision+1}


def feedback(db,topic,paper_id,decision):
    if decision not in ('relevant','irrelevant','clear'):raise ValueError('评价只能选择相关、不相关或清除')
    if type(paper_id) is not str or not 1<=len(paper_id)<=150:raise ValueError('论文编号无效')
    member=db.execute('SELECT 1 FROM topic_papers WHERE topic_id=? AND paper_id=?',
                      (topic,paper_id)).fetchone()
    if not member:raise ValueError('论文不属于当前主题')
    if decision=='clear':
        db.execute('DELETE FROM research_interest_feedback WHERE topic_id=? AND paper_id=?',(topic,paper_id))
    else:
        db.execute('''INSERT INTO research_interest_feedback VALUES (?,?,?,?)
            ON CONFLICT(topic_id,paper_id) DO UPDATE SET decision=excluded.decision,
            updated_at=excluded.updated_at''',(topic,paper_id,decision,utc_now()))
    return {'paper_id':paper_id,'decision':decision}


def interests(db,topic,*,backend='local'):
    """Transparent weak preference signal; may reorder but never delete papers."""
    col='id' if backend=='portal' else 'paper_id'
    rows=db.execute(f'''SELECT f.decision,p.title FROM research_interest_feedback f
        JOIN papers p ON p.{col}=f.paper_id WHERE f.topic_id=? LIMIT 400''',(topic,)).fetchall()
    counts=Counter();positive=negative=0
    for row in rows:
        if row['decision']=='relevant':positive+=1;sign=1
        else:negative+=1;sign=-1
        for term in tokens(row['title']):
            if len(term)>=3 and not term.isnumeric():counts[term]+=sign
    weights={term:max(-4,min(4,count)) for term,count in counts.items() if count}
    return {'positive':positive,'negative':negative,
            'top_interests':[{'term':k,'weight':v} for k,v in sorted(weights.items(),key=lambda x:(-x[1],x[0])) if v>0][:12],
            'weights':weights,'mode':'explainable_lexical_reorder_only'}


def preferences(db,topic):
    row=db.execute('SELECT * FROM research_brief_preferences WHERE topic_id=?',(topic,)).fetchone()
    return dict(row) if row else {'topic_id':topic,'enabled':False,'cadence':'weekly',
                                  'updated_at':'','last_generated':''}


def update_preferences(db,topic,*,enabled,cadence):
    if type(enabled) is not bool or cadence not in CADENCES:raise ValueError('简报开关或周期无效')
    db.execute('''INSERT INTO research_brief_preferences VALUES (?,?,?,?,?)
        ON CONFLICT(topic_id) DO UPDATE SET enabled=excluded.enabled,
        cadence=excluded.cadence,updated_at=excluded.updated_at''',
        (topic,int(enabled),cadence,utc_now(),''))
    return preferences(db,topic)


def _event_rows(db,topic,end,cursor):
    rows=db.execute('''SELECT id,kind,paper_id,title,source_name,observed_at,details_json
        FROM following_events WHERE topic_id=? AND id>? AND observed_at<?
        ORDER BY id ASC LIMIT ?''',(topic,cursor,end.isoformat(),MAX_EVENTS+1)).fetchall()
    return [dict(r) for r in rows[:MAX_EVENTS]],len(rows)>MAX_EVENTS


def _claim_map(chain):
    data,confirmed=_claims(chain)
    return data,{k:{'revision':v['revision'],'body_sha256':sha256(v['body'].encode()).hexdigest()}
                for k,v in confirmed.items()}


def _previous_brief(db,topic,cadence):
    row=db.execute('''SELECT cursor_event_id,snapshot_json FROM research_period_briefs
        WHERE topic_id=? AND cadence=? ORDER BY period_end DESC,id DESC LIMIT 1''',
        (topic,cadence)).fetchone()
    return (int(row['cursor_event_id']),json.loads(row['snapshot_json'])) if row else (0,{})


def _build_brief(db,topic,chain,cadence,start,end,*,model_config=None,
                 use_model=False,confirm_transfer=False,model_callable=None,backend='local',
                 events_override=None):
    cursor,prev=_previous_brief(db,topic,cadence)
    events,truncated=(_event_rows(db,topic,end,cursor) if events_override is None
                      else (events_override,False))
    interests_snapshot=interests(db,topic,backend=backend)
    weights=interests_snapshot.pop('weights')
    def sort_key(event):
        score=sum(weights.get(t,0) for t in tokens(event['title']))
        # chronological ordering remains available via event ID
        return (-score,-event['id'])
    sorted_events=sorted(events,key=sort_key)
    data,current_claims=_claim_map(chain)
    older=prev.get('confirmed_claims') or {}
    changes={'new':sorted(set(current_claims)-set(older)),
             'revised':sorted(x for x in current_claims.keys() & older.keys() if current_claims[x]!=older[x]),
             'no_longer_current':sorted(set(older)-set(current_claims))}
    # Events are metadata signals and cannot by themselves prove research progress.
    flagged=[{'event_id':e['id'],'kind':e['kind'],'paper_id':e['paper_id'],
              'title':e['title'][:260],'source':e['source_name'],
              'observed_at':e['observed_at'],'evidence_level':'metadata_only'}
             for e in sorted_events[:60]]
    pending=[]
    try:
        from .following import status as following_status
        pending=[{'source':c['source_name'],'reason':c['error_code'] or c['status']}
                 for c in following_status(db,topic,limit=1)['checkpoints'] if c['status']!='complete']
    except sqlite3.OperationalError:
        pass
    questions=[]
    if changes['revised'] or changes['no_longer_current']:
        questions.append({'text':'哪些已确认的研究结论在本期发生变化，需要重新核查原文？',
                          'source_claim_ids':changes['revised']+changes['no_longer_current'],
                          'status':'suggested_for_validation'})
    if events:
        questions.append({'text':'新增论文中有哪些实验条件与当前研究结论直接相关，值得阅读全文确认？',
                          'source_event_ids':[e['id'] for e in events[:12]],
                          'status':'suggested_for_validation'})
    if pending:
        questions.append({'text':'尚未补检的来源是否会改变当前研究覆盖情况？',
                          'status':'suggested_for_validation'})
    model={'status':'disabled','sections':[],'questions':[]}
    if use_model:
        if not confirm_transfer or not (model_config or {}).get('llm_data_consent'):
            raise ValueError('周期研究分析调用模型前请先明确授权')
        # Model receives only current verified claims, not full PDF or private notes.
        from .research_synthesis_agent import _call_model, _confirmed
        findings,_=_confirmed(data, (model_config or {}).get('research_goal') or '科研进展 方法 结果',
                              include_unmatched=True)
        try:model=_call_model('本期研究进展：已确认结论及待验证问题',findings,model_config or {},model_callable)
        except (RuntimeError,ValueError,TypeError,TimeoutError,OSError) as exc:
            model={'status':'unavailable_fallback','sections':[],'questions':[],
                   'reason':type(exc).__name__}
    return {'schema_version':1,'topic_id':topic,'cadence':cadence,
            'period_start':start.isoformat(),'period_end':end.isoformat(),
            'generated_at':utc_now(), 'from_event_cursor':cursor,
            'to_event_cursor':max((e['id'] for e in events),default=cursor),
            'event_count':len(events),'events_truncated':truncated,
            'event_kinds':dict(Counter(e['kind'] for e in events)),
            'event_summaries':flagged,'confirmed_claims':current_claims,
            'claim_changes':changes,'pending_sources':pending,
            'interest_profile':interests_snapshot,'research_questions':questions,
            'model':model,'source_fingerprint':_source_fingerprint(data),
            'source_fingerprint_version':FINGERPRINT_VERSION,
            'warnings':['新增与修改事件只表明题录变化，尚不能证明研究突破。',
                        '模型解释、兴趣分数与研究建议都需要研究人员审核。']}


def to_markdown(report,*,status='snapshot'):
    def clean(value):return str(value or '').replace('<','&lt;').replace('\r',' ').replace('\n',' ')
    lines=[f'# {report["cadence"]} 科研跟进简报','',
        f'统计区间：{report["period_start"]} 至 {report["period_end"]}',
        f'生成时间：{report["generated_at"]} · 证据状态：{status}',
        '',f'本期检索事件：{report["event_count"]}；不同事件类型：{report["event_kinds"]}',
        '','## 新增和修改的论文（仅题录信号）','']
    for e in report['event_summaries']:
        lines.append(f'- [{e["event_id"]}] {clean(e["title"])} · {e["kind"]} · {e["source"]}')
    if not report['event_summaries']:lines.append('暂无新增或修改论文。')
    changes=report['claim_changes'];lines+=['','## 已确认研究结论的变化','',
        f'新确认：{len(changes["new"])}；修订：{len(changes["revised"])}；不再有效：{len(changes["no_longer_current"])}。','']
    for kind in ('new','revised','no_longer_current'):
        for claim_id in changes[kind]:lines.append(f'- {kind}: `{claim_id}`')
    lines+=['','## 偏好反馈和待办','',
        f'相关反馈：{report["interest_profile"]["positive"]}；不相关反馈：{report["interest_profile"]["negative"]}。']
    for p in report['pending_sources']:lines.append(f'- 需补检：{clean(p["source"])} · {clean(p["reason"])}')
    lines+=['','## 待验证研究问题','']
    for q in report['research_questions']+report['model']['questions']:
        lines.append('- '+clean(q.get('text')))
    if not report['research_questions'] and not report['model']['questions']:
        lines.append('本期没有足够新证据形成新的待验证问题。')
    lines+=['','## 模型分析候选（未经人工审核）','']
    for s in report['model']['sections']:
        lines.append('- '+clean(s['text'])+'（结论 ID：'+','.join(s['claim_ids'])+'）')
    lines+=['','## 使用限制','']
    lines+=['- '+clean(x) for x in report['warnings']]
    if report['events_truncated']:lines.append('- 本期超过单次 300 条事件上限，余下事件将留待后续补检。')
    return '\n'.join(lines)+'\n'


def generate_brief(db,topic,chain,*,cadence='weekly',now=None,
                   use_model=False,confirm_transfer=False,model_config=None,
                   model_callable=None,backend='local'):
    from .research_brief_reliability import run
    return run(db,topic,chain,cadence=cadence,now=now,model_config=model_config,
               use_model=use_model,confirm_transfer=confirm_transfer,
               model_callable=model_callable,backend=backend)


def read_brief(db,topic,ident,chain,*,cadence=None):
    if type(ident) is not str or not HEX_ID.fullmatch(ident):raise ValueError('简报编号无效')
    row=db.execute('SELECT * FROM research_period_briefs WHERE id=? AND topic_id=?',
                   (ident,topic)).fetchone()
    if row is None:raise ValueError('简报不存在')
    raw=row['snapshot_json'];digest=sha256(raw.encode('utf-8')).hexdigest()
    if digest!=row['source_sha256']:raise RuntimeError('简报文件完整性校验失败')
    report=json.loads(raw)
    data=chain.read();current=_source_fingerprint(data)
    state=('current' if report.get('source_fingerprint_version')==FINGERPRINT_VERSION
           and current==report['source_fingerprint'] else 'needs_review')
    from .research_brief_reliability import revision_diff, _claim_map
    _, confirmed=_claim_map(chain)
    drift=revision_diff(report,confirmed)
    amendments=[]
    try:
        amendments=[json.loads(r['snapshot_json']) for r in db.execute('''
            SELECT snapshot_json FROM research_brief_amendments
            WHERE topic_id=? AND brief_id=? ORDER BY created_at,id''',(topic,ident))]
    except sqlite3.OperationalError:
        pass  # Compatibility with databases that have not yet been migrated.
    summary={'events':sum(len(a.get('events',[])) for a in amendments),
             'revisions':sum(a.get('kind')=='research_evidence_changed' for a in amendments)}
    extra=[]
    if amendments:
        extra=['', '## 已记录的更正与新增事件', '']
        for item in amendments:
            if item['kind']=='late_events':
                for event in item.get('events',[]):
                    extra.append(f'- 晚到事件 [{event["id"]}]: {event["title"]}（{event["observed_at"]}）')
            elif item['kind']=='research_evidence_changed':
                extra.append('- 已确认结论发生修订，需核对本期报告的证据有效性。')
    return {'id':ident,'report':report,'digest':digest,'snapshot_status':state,
            'revision_diff':drift,'amendments':amendments,'amendment_summary':summary,
            'markdown':to_markdown(report,status=state)+'\n'.join(extra)+('\n' if extra else '')}



def list_briefs(db,topic,*,limit=30):
    limit=max(1,min(80,int(limit)))
    rows=db.execute('''SELECT id,cadence,period_start,period_end,created_at,cursor_event_id
        FROM research_period_briefs WHERE topic_id=? ORDER BY period_end DESC,id DESC LIMIT ?''',
        (topic,limit)).fetchall()
    return {'briefs':[dict(row) for row in rows], 'preferences':preferences(db,topic)}


def automatic_due(db,topic,chain,*,now=None,backend='local'):
    setting=preferences(db,topic)
    if not setting['enabled']:
        return {'state':'disabled'}
    cadence=setting['cadence'];start,end=_window(cadence,now)
    previous=db.execute('''SELECT id FROM research_period_briefs WHERE topic_id=? AND cadence=? AND period_end=?''',
                        (topic,cadence,end.isoformat())).fetchone()
    result=generate_brief(db,topic,chain,cadence=cadence,now=now,backend=backend)
    if result.get('state')!='complete':return {'state':result['state'],'result':result}
    if previous and result.get('late_events_recorded',0)==0:
        return {'state':'already_generated','period_end':end.isoformat(),'result':result}
    return {'state':'amended' if previous else 'generated','result':result}


def run_local(db_path,topic,*,cadence='weekly',now=None,
              use_model=False,confirm_transfer=False,model_config=None,model_callable=None):
    from .core import open_database, TOPIC_ID_RE
    from .migrations import migrate
    if type(topic) is not str or not TOPIC_ID_RE.fullmatch(topic):raise ValueError('无效主题')
    with open_database(db_path) as db:
        migrate(db,'research')
        chain=EvidenceChain(db,topic,backend='local',db_path=db_path)
        return generate_brief(db,topic,chain,cadence=cadence,now=now,
                              model_config=model_config,use_model=use_model,
                              confirm_transfer=confirm_transfer,model_callable=model_callable)


def run_portal(portal,uid,topic,*,cadence='weekly',now=None):
    from .migrations import migrate
    portal.available(uid,topic)
    with portal.workspace(uid) as db:
        migrate(db,'portal_workspace')
        return generate_brief(db,topic,
            EvidenceChain(db,topic,backend='portal',portal=portal,uid=uid),
            cadence=cadence,now=now,backend='portal')


def after_search(db_path,topic,*,now=None):
    """Best-effort, no-model, idempotent periodic snapshot after a local cycle."""
    from .core import open_database
    from .migrations import migrate
    with open_database(db_path) as db:
        migrate(db,'research')
        return automatic_due(db,topic,EvidenceChain(db,topic,backend='local',db_path=db_path),now=now)

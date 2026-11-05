# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Run saved research topics one by one without switching the user's UI state."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime,timezone
from .core import TOPIC_FIELDS, read_config, validate_config, run_cycle
from .history import record_run


def topic_snapshots(config: dict) -> list[dict]:
    cfg=validate_config(config)
    snapshots=[]
    for topic in cfg['topics']:
        copy=deepcopy(cfg)
        for field in TOPIC_FIELDS:
            copy[field]=deepcopy(topic[field])
        copy['active_topic_id']=topic['id']
        snapshots.append(copy)
    return snapshots


def run_all_topics(config: dict | None = None, logger=print, progress=None,
                   runner=run_cycle, recorder=record_run) -> dict:
    topics=topic_snapshots(config or read_config())
    results=[]
    for index,cfg in enumerate(topics):
        tid=cfg['active_topic_id']
        name=next((r['name'] for r in cfg['topics'] if r['id']==tid),tid)
        logger(f'[{index+1}/{len(topics)}] 开始研究主题：{name}')
        started=datetime.now(timezone.utc).isoformat()
        if progress:
            progress({'percent':int(index*100/max(1,len(topics))),
                      'stage':'批量检索','detail':f'开始主题 {index+1}/{len(topics)}：{name}'})
        try:
            # No save_config here: the active topic shown in the UI stays unchanged.
            result=runner(cfg,logger=logger,progress=(
                (lambda state,i=index: progress({**state,'percent':min(99,round((i*100+float(state.get('percent',0)))/max(1,len(topics)))),'detail':f'{name} · '+str(state.get('detail',''))}))
                if progress else None))
            agent_state=result.get('agent_run',{}).get('state','success')
            item_state='partial' if agent_state=='partial' else 'success'
            results.append({'topic_id':tid,'name':name,'state':item_state,
                            'retrieved':result['retrieved'],'new':result['new'],
                            'selected':result['selected']})
            try:
                recorder(tid,'search',item_state,result=result,started_at=started)
            except Exception as history_exc:
                logger(f'主题“{name}”已检索成功，但运行历史未能写入：{history_exc}')
        except Exception as exc:
            message=str(exc)[:280]
            results.append({'topic_id':tid,'name':name,'state':'failed','error':message})
            try:
                recorder(tid,'search','failed',error=message,started_at=started)
            except Exception:
                pass
            logger(f'研究主题“{name}”失败，继续下一个主题：{message}')
    success=sum(r['state'] in ('success','partial') for r in results)
    partial=sum(r['state']=='partial' for r in results)
    failure=len(results)-success
    if progress:
        progress({'percent':100,'stage':'批量检索完成',
                  'detail':f'成功 {success} 个主题；失败 {failure} 个主题'})
    if not success:
        raise RuntimeError('全部研究主题检索失败，请检查运行记录与文献来源。')
    return {'topics_succeeded':success,'topics_failed':failure,'topics_partial':partial,'topics':results,
            'retrieved':sum(r.get('retrieved',0) for r in results),
            'new':sum(r.get('new',0) for r in results),
            'selected':sum(r.get('selected',0) for r in results)}

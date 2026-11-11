# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.52: deterministic, inspectable paper selection for research inquiries.

All selection happens on existing topic membership. No user-provided IDs may
join arbitrary papers outside the active topic. Source text is untrusted data.
"""
from __future__ import annotations
from hashlib import sha256
import json
import re
from .research_screening import screen_paper, excerpt_near_focus

MODES=('recommended','all','manual')
MAX_SCOPE_PAPERS=1000
MAX_MANUAL=250
MAX_RECOMMENDED=20


def _ids(raw, label):
    if raw is None:return []
    if not isinstance(raw,list) or len(raw)>MAX_SCOPE_PAPERS or any(type(x) is not str or not x or len(x)>150 for x in raw):
        raise ValueError(label+'必须是有效论文编号列表')
    return list(dict.fromkeys(raw))


def build_scope(papers:list[dict], question:str, terms:set[str], facets:list[str], *,
                mode='recommended', paper_ids=None, include_ids=None, exclude_ids=None,
                recommended_limit=16):
    if mode not in MODES:raise ValueError('请选择智能推荐、全部主题或手动选文')
    if type(recommended_limit) is not int or not 1<=recommended_limit<=MAX_RECOMMENDED:
        raise ValueError('推荐数量超出允许范围')
    source={str(p['paper_id']):p for p in papers}
    ids=_ids(paper_ids,'选定论文');plus=_ids(include_ids,'补充论文');minus=_ids(exclude_ids,'排除论文')
    unknown=set(ids+plus+minus)-set(source)
    if unknown:raise ValueError('选文中包含不属于当前主题的论文，请刷新文献清单')
    if set(plus)&set(minus):raise ValueError('同一篇论文不能同时补充和排除')
    if mode=='manual' and not ids:raise ValueError('请至少选择一篇论文')
    if mode!='manual' and ids:raise ValueError('所选论文仅可用于手动选文模式')
    if mode!='recommended' and (plus or minus):raise ValueError('补充与排除仅可用于智能推荐模式')
    if mode=='manual' and len(ids)>MAX_MANUAL:raise ValueError('单次手动选文最多 250 篇')
    matched=[]
    for paper in papers:
        title=str(paper.get('title') or '')
        abstract=str(paper.get('abstract') or '')
        spec=screen_paper(paper,facets,terms) if facets else None
        lexical=sum((3 if term in title.casefold() else 1) for term in terms
                    if len(term)>=4 and term in (title+' '+abstract[:4000]).casefold())
        if not spec and not facets and lexical:
            spec={'score':float(lexical),'level':'related','facets':[],'labels':[],
                  'reason':'题名或摘要与研究问题匹配'}
        if spec:matched.append((float(spec['score']),paper,spec))
    matched.sort(key=lambda x:(-x[0],str(x[1]['paper_id'])))
    ranked_ids=[str(p['paper_id']) for _,p,_ in matched]
    match_by_id={str(p['paper_id']):(score,spec) for score,p,spec in matched}
    if mode=='recommended':
        chosen=[];covered=set()
        # Greedy coverage: diversify the key evaluation dimensions without
        # hiding highly relevant papers or inventing semantic similarity scores.
        remaining=list(matched)
        while remaining and len(chosen)<recommended_limit:
            remaining.sort(key=lambda rec:(-(rec[0]+1.0*len(set(rec[2]['facets'])-covered)),str(rec[1]['paper_id'])))
            score,paper,spec=remaining.pop(0)
            chosen.append(str(paper['paper_id']))
            covered.update(spec['facets'])
        chosen=[pid for pid in plus if pid not in minus]+[pid for pid in chosen if pid not in minus and pid not in plus]
    elif mode=='manual':chosen=ids
    else:chosen=[str(p['paper_id']) for p in papers]
    if len(chosen)>MAX_SCOPE_PAPERS:raise ValueError('分析范围超过上限，请缩小主题')
    items=[]
    for pid in chosen:
        paper=source[pid]
        score,spec=match_by_id.get(pid,(0.0,{'score':0,'level':'unmatched',
            'facets':[],'labels':[],'reason':'用户选择或主题全量收录'}))
        items.append({'paper_id':pid,'title':str(paper.get('title') or '')[:300],
            'doi':str(paper.get('doi') or '')[:240],
            'published':str(paper.get('published') or '')[:50],
            'matched_terms':round(score,2),
            'status':'metadata_only_not_verified' if score else 'unmatched_topic_reference',
            'abstract_available':bool(str(paper.get('abstract') or '').strip()),
            'abstract_excerpt':excerpt_near_focus(paper.get('abstract') or '',spec['facets'],max_chars=550),
            'relevance_level':spec['level'],'matched_facets':spec['facets'],
            'matched_labels':spec['labels'],'relevance_reason':spec['reason'],
            'selected_by':('user_added' if pid in plus else 'manual' if mode=='manual' else
                           'all_topic' if mode=='all' else 'recommended')})
    serialized=[{'id':pid,'abstract_hash':sha256(str(source[pid].get('abstract') or '').encode('utf-8')).hexdigest()}
                for pid in chosen]
    selection_hash=sha256(json.dumps(serialized,sort_keys=True).encode('utf-8')).hexdigest()
    return {'mode':mode,'items':items,'recommended_ids':ranked_ids[:recommended_limit],
            'selected_ids':chosen,'included_ids':plus,'excluded_ids':minus,
            'selected_count':len(chosen),'matched_count':len(matched),
            'topic_total':len(papers),'selection_hash':selection_hash,
            'unmatched_selected':sum(pid not in match_by_id for pid in chosen),
            'abstract_available_count':sum(item['abstract_available'] for item in items)}


def preview_scope(papers:list[dict], question:str, terms:set[str], facets:list[str], *,limit=16):
    plan=build_scope(papers,question,terms,facets,mode='recommended',recommended_limit=limit)
    # Every topic member can be searched in the manual picker; no silent limit.
    rec=set(plan['recommended_ids'])
    by_id={item['paper_id']:item for item in plan['items']}
    rows=[]
    for p in papers:
        pid=str(p['paper_id'])
        pick=by_id.get(pid)
        rows.append({'paper_id':pid,'title':str(p.get('title') or '')[:300],
            'abstract_available':bool(str(p.get('abstract') or '').strip()),
            'recommended':pid in rec,
            'relevance_reason':pick['relevance_reason'] if pick else '当前未进入推荐清单',
            'matched_terms':pick['matched_terms'] if pick else 0})
    return {'topic_total':len(papers),'matched_count':plan['matched_count'],
            'recommended_ids':plan['selected_ids'],'papers':rows,
            'selection_hash':plan['selection_hash'],'mode':'recommended'}

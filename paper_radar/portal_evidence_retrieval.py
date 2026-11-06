# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Bounded experiment-page retrieval for public PDF analyses.

This is an *evidence candidate selector*, not an experiment extractor. Pages
remain tied to their original PDF page numbers. Neither this module nor the
LLM is allowed to invent rows, join tables across pages, or claim comparable
experimental conditions without a verbatim quote verified by experiments.py.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

MAX_CANDIDATE_PAGES = 12
MAX_PAGE_TEXT = 3400
BATCH_SIZE = 4
CLOUD_CALL_DAILY_LIMIT = 8

TABLE = re.compile(r'\b(?:table|tab\.?|figure|fig\.?)\s*[A-Z]?\d+\b|(?:表|图)\s*\d+',re.I)
RESULTS = re.compile(r'\b(?:experiment(?:s|al)?|results?|benchmark(?:s)?|evaluation|ablation|comparison|'
                     r'baselines?|performance|test\s+set|metrics?|measurements?)\b|'
                     r'(?:实验|结果|性能|对比|消融|评估|基准|测试集)',re.I)
MEASURES = re.compile(r'\b(?:rmse|mae|mse|mean\s+square|relative\s+[lL][12]|'
                      r'accuracy|precision|recall|f1[ -]?score|error|speedup|latency|'
                      r'flops|r2|l2|mape|aue|iou)\b|(?:误差|精度|均方|准确率|召回率|加速比)',re.I)
NUMBER = re.compile(r'(?<!\w)[+-]?\d+(?:[.,]\d+)?(?:\s*%|\s*±\s*\d+(?:\.\d+)?)?')
REFERENCES = re.compile(r'(?im)^\s*(?:references|bibliography|参考文献)\s*$')


def page_score(text: str) -> float:
    """Only lexical cues determine page priority; no model or external calls."""
    if not isinstance(text,str) or not text.strip():return -1000
    hits=TABLE.findall(text);results=RESULTS.findall(text);metrics=MEASURES.findall(text)
    numbers=NUMBER.findall(text)
    score=min(5,len(hits))*7.0 + min(7,len(results))*2.2 + min(8,len(metrics))*1.8
    score+=min(50,len(numbers))*.12
    if re.search(r'(?im)^\s*(?:\d+(?:\.\d+)*[. ]+)?(?:experiments?|results?|evaluation|'
                 r'benchmark(?:s)?|实验结果|实验分析)\s*$',text):
        score+=6
    if REFERENCES.search(text) and not hits:score-=18
    return round(score,3)


def page_excerpt(text: str, *, limit: int=MAX_PAGE_TEXT) -> str:
    """Find a table/results-rich window on a long page without rewriting text."""
    if not isinstance(text,str):return ''
    if len(text)<=limit:return text
    limit=max(512,int(limit))
    # Exact substring retained, allowing downstream contiguous quote matching.
    # Sliding by half a window avoids losing an experiment table near page end.
    starts=list(range(0,len(text)-limit+1,max(256,limit//2)))
    starts.extend([0,len(text)-limit])
    candidates=[]
    for start in sorted(set(starts)):
        chunk=text[start:start+limit]
        score=page_score(chunk)
        # Prefer early context on exact ties; no arbitrary mid-token joins.
        candidates.append((score,-start,chunk))
    return max(candidates)[-1]


def select_candidate_pages(pages:list[dict],*,limit:int=MAX_CANDIDATE_PAGES,
                           excerpt_chars:int=MAX_PAGE_TEXT) -> dict:
    """Rank all parsed PDF pages with three-region coverage and strict caps.

    Returns material only from original PDF text; avoids the former first-eight
    page cutoff. Low-scoring pages may be selected to ensure late appendices
    are not categorically excluded. No claim is made that every table is found.
    """
    limit=max(1,min(MAX_CANDIDATE_PAGES,int(limit)))
    valid=[];seen=set()
    for raw in pages:
        if not isinstance(raw,dict) or type(raw.get('page')) is not int:continue
        num=raw['page'];body=raw.get('text')
        if not 1<=num<=120 or num in seen or not isinstance(body,str) or not body.strip():continue
        seen.add(num)
        snippet=page_excerpt(body,limit=excerpt_chars)
        valid.append({'page':num,'chunk_id':num,'text':snippet,'score':page_score(snippet),
                      'full_page_chars':len(body)})
    if not valid:return {'pages':[],'scanned_pages':len(pages),'candidate_pages':[],
                         'excluded_nonempty_pages':0,'strategy':'ranked-three-region-v1'}
    by_score=sorted(valid,key=lambda row:(-row['score'],row['page']))
    selected=[]
    if len(valid)>limit and limit>=6:
        # Reserve one highest-scoring page from each part of the PDF. This
        # prevents experiments in appendices from being excluded a priori.
        first=min(row['page'] for row in valid);last=max(row['page'] for row in valid)
        width=max(1,(last-first+3)//3)
        for zone in range(3):
            pool=[r for r in by_score if min(2,(r['page']-first)//width)==zone]
            if pool:selected.append(pool[0])
    identifiers={r['page'] for r in selected}
    for row in by_score:
        if len(selected)>=limit:break
        if row['page'] not in identifiers:
            selected.append(row);identifiers.add(row['page'])
    selected=sorted(selected,key=lambda row:row['page'])
    return {'pages':selected,'scanned_pages':len(pages),
            'candidate_pages':[r['page'] for r in selected],
            'excluded_nonempty_pages':len(valid)-len(selected),
            'strategy':'ranked-three-region-v1'}


def make_batches(selected:dict,*,batch_size:int=BATCH_SIZE) -> list[list[dict]]:
    """Bound at most three bounded cloud requests (4 pages each)."""
    size=max(1,min(BATCH_SIZE,int(batch_size)))
    rows=[{'chunk_id':row['chunk_id'],'page':row['page'],'text':row['text']}
          for row in selected['pages']]
    return [rows[i:i+size] for i in range(0,len(rows),size)]

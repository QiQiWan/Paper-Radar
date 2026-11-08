# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Reproducible 1,000-metadata / 16-parsed-PDF evidence-workbench benchmark.

Generated files and IDs are synthetic. The PDF text extraction, SQLite engine,
user session/cookie/CSRF, and HTTP endpoints are real. No network provider calls.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from io import BytesIO
import json
import math
from pathlib import Path
import platform
import statistics
from time import perf_counter
from urllib import request

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
from .v131_workspace_benchmark import workspace_server,response_http,pct,_rss_mb
from paper_radar.portal_research import save_pdf,analyze
from paper_radar.portal_triage import save_note
from paper_radar import __version__

QUOTE='Benchmark on DatasetA with Accuracy 91% versus BaselineX under split1.'


def _fixture_pdf():
    writer=PdfWriter()
    p=writer.add_blank_page(width=595,height=842)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject()
    stream.set_data(('BT /F1 11 Tf 30 750 Td ('+QUOTE+') Tj ET').encode('ascii'))
    p[NameObject('/Contents')]=writer._add_object(stream)
    file=BytesIO();writer.write(file)
    return file.getvalue()


def run(*,papers=1000,pdfs=16,requests=320,concurrency=8):
    if not 20<=papers<=50000 or not 1<=pdfs<=min(25,papers):
        raise ValueError('papers must be 20..50000 and PDFs must be 1..25')
    if requests<20 or not 1<=concurrency<=32:
        raise ValueError('requests >=20 and concurrency 1..32 required')
    total_start=perf_counter();raw=_fixture_pdf()
    all_ops=[];failures=[]
    with workspace_server(papers=papers) as env:
        portal,uid=env['portal'],env['user']['id']
        base,token,csrf=env['base'],env['token'],env['csrf']
        startup_rss=_rss_mb()
        seed_start=perf_counter()
        for i in range(pdfs):
            pid=f'synthetic:v131:{i:06d}'
            save_pdf(portal,uid,'default',pid,raw)
            parsed=analyze(portal,uid,'default',pid,cloud=False)
            if parsed['state'] != 'indexed':
                raise RuntimeError('Synthetic PDF text extraction failed for '+pid)
            save_note(portal,uid,'default',pid,
                f'Synthetic note for paper {i}: read and verify source.', 'cite',0)
        seed_seconds=perf_counter()-seed_start
        anchor_ids=[];claim_ids=[]
        def post(path,body,step):
            res=response_http(base,token,path,method='POST',body=body,csrf=csrf)
            all_ops.append({'step':step,'duration_ms':round(res['duration_ms'],3),'status':res['status']})
            if not res['ok']:failures.append({'step':step,'status':res['status'],'body':str(res.get('body'))[:120]})
            return res
        for i in range(pdfs):
            pid=f'synthetic:v131:{i:06d}'
            r=post('/api/public/trace/anchor',{'topic_id':'default','paper_id':pid,
                'page':1,'quote':QUOTE,'experiment_id':'','expected_sha256':''},'anchor_write')
            if r['ok']:anchor_ids.append(r['body']['id'])
        for i,aid in enumerate(anchor_ids):
            r=post('/api/public/trace/claim',{'topic_id':'default',
                'body':f'Synthetic manual result for paper {i}; anchored to the generated PDF text.',
                'kind':'finding','anchor_ids':[aid],'expected_revision':0,'claim_id':''},'claim_draft')
            if not r['ok']:continue
            claim_ids.append(r['body']['id'])
            post('/api/public/trace/confirm',{'topic_id':'default',
                'claim_id':r['body']['id'],'expected_revision':1,
                'review_note':'Synthetic review of the exact generated PDF source text',
                'decision':'confirmed'},'claim_confirm')
        if failures:raise RuntimeError('Cannot start load on incomplete seed '+str(failures[:4]))
        cases=[('topic_trace','/api/public/trace?topic_id=default'),
            ('paper_trace','/api/public/trace?topic_id=default&paper_id=synthetic%3Av131%3A000001'),
            ('claim_history','/api/public/trace/history?topic_id=default&claim_id='+claim_ids[0])]
        samples=[];breakdown=defaultdict(list)
        def one(i):
            kind,url=cases[i%len(cases)]
            res=response_http(base,token,url)
            data=res.get('body') or {}
            valid=res['ok'] and (data.get('counts',{}).get('confirmed_current')==pdfs
                if kind=='topic_trace' else bool(data.get('claims')) if kind=='paper_trace'
                else len(data.get('history',[]))==2)
            return kind,res['duration_ms'],bool(valid),res['status']
        start=perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures=[pool.submit(one,i) for i in range(requests)]
            for future in as_completed(futures):
                kind,elapsed,valid,status=future.result()
                samples.append(elapsed);breakdown[kind].append(elapsed)
                if not valid:failures.append({'step':kind,'status':status})
        wall_s=perf_counter()-start
        # Export endpoint returns Markdown bytes; an HTTP 200 alone is not enough.
        req=request.Request(base+'/api/public/trace/export?topic_id=default',
                            headers={'Cookie':'PRSession='+token})
        with request.urlopen(req,timeout=12) as resp:
            exported=resp.read().decode('utf-8')
        from paper_radar.portal_triage import _md
        export_valid=exported.count('研究发现')>=pdfs and _md(QUOTE) in exported
        if not export_valid:failures.append({'step':'export','status':200,'error':'missing_quote_or_claim'})
        with portal.workspace(uid) as db:
            anchors=db.execute('SELECT COUNT(*) FROM research_anchors').fetchone()[0]
            claims=db.execute('SELECT COUNT(*) FROM research_claims WHERE decision="confirmed"').fetchone()[0]
            notes=db.execute('SELECT COUNT(*) FROM paper_notes').fetchone()[0]
            audit=db.execute('SELECT COUNT(*) FROM research_claim_audit').fetchone()[0]
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        after_rss=_rss_mb()
        return {'version':__version__,'collected_at_utc':datetime.now(timezone.utc).isoformat(),
            'env':{'os':platform.platform(),'python':platform.python_version(),'rss_mb_start':startup_rss,
                   'rss_mb_end':after_rss},
            'scope':{'papers_metadata_synthetic':papers,'pdfs_synthetic_extracted':pdfs,
                'notes_synthetic':pdfs,'remote_model_calls':0,'real_user_operations':0},
            'preparation_seconds':round(seed_seconds,3),
            'operations':{'count':len(all_ops),'success':len(all_ops)-len([e for e in failures if e['step'] in ('anchor_write','claim_draft','claim_confirm')]),
                'p95_ms':pct([e['duration_ms'] for e in all_ops],95)},
            'reads':{'requests':requests,'completed':requests-len([e for e in failures if e['step'] in ('topic_trace','paper_trace','claim_history')]),
                'error_rate':round(len([e for e in failures if e['step'] in ('topic_trace','paper_trace','claim_history')])/requests,4),
                'p50_ms':pct(samples,50),'p95_ms':pct(samples,95),
                'max_ms':round(max(samples),3),'wall_seconds':round(wall_s,3),
                'by_case':{k:{'count':len(v),'p95_ms':pct(v,95)} for k,v in breakdown.items()}},
            'persistence':{'anchors':anchors,'confirmed_claims':claims,'notes':notes,'audit_events':audit,
                'integrity_check':integrity,'export_valid':export_valid},
            'failures':failures[:100],
            'limitations':'合成元数据与 1 页 PDF；未测真实千篇全文、多用户真人操作、跨平台安装。'}


def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--papers',type=int,default=1000)
    a.add_argument('--pdfs',type=int,default=16)
    a.add_argument('--requests',type=int,default=320)
    a.add_argument('--concurrency',type=int,default=8)
    a.add_argument('--out',type=Path,default=None)
    a.add_argument('--enforce',action='store_true')
    cfg=a.parse_args(argv)
    result=run(papers=cfg.papers,pdfs=cfg.pdfs,requests=cfg.requests,concurrency=cfg.concurrency)
    raw=json.dumps(result,ensure_ascii=False,indent=2)
    if cfg.out:
        cfg.out.parent.mkdir(parents=True,exist_ok=True);cfg.out.write_text(raw+'\n',encoding='utf-8')
    else:print(raw)
    if cfg.enforce and (result['failures'] or result['reads']['p95_ms']>1500 or
                        not result['persistence']['export_valid'] or result['persistence']['integrity_check']!='ok'):
        raise SystemExit(1)
    return result

if __name__=='__main__':main()

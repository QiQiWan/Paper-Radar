# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Reproducible opt-in fixed-version PDF experiment inference.

No network activity unless --execute-cloud is explicitly passed. Executes at
most --max-requests bounded cloud calls, preserves completed paper records.
The output is an evaluation input, not expert-verified scientific evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from paper_radar.benchmark_quality import load_manifest,checked_pdf_hash,norm
from paper_radar.portal_evidence_retrieval import select_candidate_pages,make_batches
from paper_radar.experiments import validate_experiments,_json_payload
from paper_radar.model_governance import describe_failure,estimate
from paper_radar import portal_research
from tools.eval_real_papers import parse_pdf


def main(argv=None):
    cli=argparse.ArgumentParser(description='Evidence-grounded PDF model evaluation (opt-in; may incur charges)')
    cli.add_argument('--benchmark',type=Path,default=ROOT/'benchmarks'/'real_papers_v2')
    cli.add_argument('--output',type=Path,default=ROOT/'reports'/'real_papers_v2'/'model_predictions.json')
    cli.add_argument('--execute-cloud',action='store_true',help='Explicitly authorize sending selected PDF excerpts to the model provider')
    cli.add_argument('--max-papers',type=int,default=3)
    cli.add_argument('--max-requests',type=int,default=3)
    args=cli.parse_args(argv)
    if not 1<=args.max_papers<=33 or not 1<=args.max_requests<=99:
        raise SystemExit('Limits: 1..33 papers, 1..99 requests')
    manifest=load_manifest(args.benchmark)
    model=os.getenv('PAPERRADAR_PUBLIC_LLM_MODEL','').strip()
    base=os.getenv('PAPERRADAR_PUBLIC_LLM_BASE','').strip()
    has_key=bool(os.getenv('PAPERRADAR_PUBLIC_LLM_KEY','').strip())
    provider_fingerprint=hashlib.sha256((model+'|'+base+'|portal-research-experiment-v130').encode()).hexdigest()
    selected=manifest['papers'][:args.max_papers]
    preflight={'available_pdf_count':sum(checked_pdf_hash(args.benchmark,p) is not None for p in selected),
               'papers_selected':len(selected),'request_budget':args.max_requests,
               'rough_cost':estimate({'llm_input_usd_per_million':os.getenv('PAPERRADAR_PUBLIC_LLM_INPUT_USD_PER_MILLION',''),
                                      'llm_output_usd_per_million':os.getenv('PAPERRADAR_PUBLIC_LLM_OUTPUT_USD_PER_MILLION','')},'experiments'),
               'model':model,'destination_host':__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(base).hostname or '',
               'has_key':has_key,'cloud_consent':args.execute_cloud}
    if not args.execute_cloud:
        print(json.dumps({'state':'offline_preview','preflight':preflight,'notice':'未发送论文内容。如需实际模型预测，确认版权和费用后增加 --execute-cloud。'},ensure_ascii=False,indent=2))
        return 0
    if not model or not base or not has_key:
        raise SystemExit('Missing PAPERRADAR_PUBLIC_LLM_KEY, _MODEL or _BASE; no cloud requests were made')
    if args.output.exists():
        state=json.loads(args.output.read_text(encoding='utf-8'))
        if (state.get('model')!=model or state.get('benchmark_id')!=manifest['benchmark_id'] or
            state.get('provider_fingerprint')!=provider_fingerprint):
            raise SystemExit('Existing output belongs to a different benchmark/model; choose a fresh --output')
    else:
        state={'benchmark_id':manifest['benchmark_id'],'model':model,'prompt_version':'portal-research-experiment-v130',
               'provider_fingerprint':provider_fingerprint,
               'run_conditions':{'selected_papers':args.max_papers,'request_budget':args.max_requests},
               'papers':{},'findings':[]}
    def commit():
        args.output.parent.mkdir(parents=True,exist_ok=True)
        tmp=args.output.with_name(args.output.name+'.partial')
        tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(tmp,args.output)
    used=0
    for paper in selected:
        pid=paper['paper_id']
        sha=checked_pdf_hash(args.benchmark,paper)
        if not sha:
            state['papers'][pid]={'status':'pdf_unavailable'}
            commit();continue
        previous=state['papers'].get(pid,{})
        if previous.get('status')=='complete' and previous.get('pdf_sha256')==sha:
            continue
        source=args.benchmark/'pdfs'/(pid+paper['version']+'.pdf')
        parsed=parse_pdf(source)
        if parsed['status']!='parsed':
            state['papers'][pid]={'status':parsed['status'],'pdf_sha256':sha};commit();continue
        batches=make_batches(select_candidate_pages(parsed['pages']))
        previous_state=previous if previous.get('pdf_sha256')==sha else {}
        # Distinct completed request slots persist across reruns.
        completed=dict(previous_state.get('completed_batches',{}))
        for i,batch in enumerate(batches):
            if completed.get(str(i),{}).get('status')=='success':continue
            if used>=args.max_requests:break
            used+=1
            try:
                raw=portal_research._cloud_extraction(SimpleNamespace(model_callable=None),batch)
                support={p['chunk_id']:{'page':p['page'],'content':p['text']} for p in batch}
                verified=validate_experiments(raw,support)
                original=_json_payload(raw) or {}
                raw_rows=original.get('findings',[]) if isinstance(original,dict) else []
                for item in verified:
                    # Only reuse method if the model supplied it in exactly the
                    # same accepted quote and chunk; empty is a valid miss.
                    method=''
                    for raw_row in raw_rows:
                        if not isinstance(raw_row,dict):continue
                        if (raw_row.get('chunk_id')==item['chunk_id'] and
                            norm(raw_row.get('quote'))==norm(item.get('quote')) and
                            norm(raw_row.get('value'))==norm(item.get('value'))):
                            name=str(raw_row.get('method') or '').strip()[:100]
                            if name and norm(name) in norm(item.get('quote')):method=name
                            break
                    item_out={key:item[key] for key in ('dataset','metric','value','quote','page')}
                    item_out.update({'paper_id':pid,'page_index':item['page']-1,'method':method,
                                     'pdf_sha256':sha,'baseline':item['baseline'],'setting':item['setting']})
                    item.setdefault('_benchmark_record',item_out)
                completed[str(i)]={'status':'success','findings':[x['_benchmark_record'] for x in verified]}
            except Exception as exc:
                completed[str(i)]={'status':'failed','error':describe_failure(str(exc))}
            state['papers'][pid]={'status':'partial','pdf_sha256':sha,'completed_batches':completed,
                                  'batch_count':len(batches)}
            commit()
        complete=len(completed)==len(batches) and all(x['status']=='success' for x in completed.values())
        state['papers'][pid]={'status':'complete' if complete else 'partial','pdf_sha256':sha,
                              'completed_batches':completed,'batch_count':len(batches)}
        commit()
    state['findings']=[rec for pid,item in sorted(state['papers'].items())
                       for batch in item.get('completed_batches',{}).values()
                       if batch['status']=='success' for rec in batch['findings']]
    commit()
    print(json.dumps({'output':str(args.output),'model':model,'requests_this_run':used,
                       'complete_papers':sum(x['status']=='complete' for x in state['papers'].values()),
                       'findings':len(state['findings']),'notice':'仅为模型候选，需双人专家复核'},ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())

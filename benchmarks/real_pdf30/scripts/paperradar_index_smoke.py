#!/usr/bin/env python3
"""Offline Paper-Radar pypdf smoke, one isolated subprocess per PDF."""
from pathlib import Path
import argparse,json,os,sqlite3,subprocess,sys,time

CHILD=r'''
import sys,sqlite3,json,time
from pathlib import Path
from paper_radar.fulltext import index_paper
path=Path(sys.argv[1]);pid=sys.argv[2]
conn=sqlite3.connect(path,timeout=12);conn.row_factory=sqlite3.Row
try:
 result=index_paper(conn,pid,db_path=path)
 print(json.dumps({'status':result['status'],'pages':result['pages'],'chunks':result['chunks'],'total_pages':result['total_pages']}))
finally:conn.close()
'''

def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--base',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--timeout',type=int,default=25)
 a=p.parse_args()
 papers=json.loads((a.base/'manifest.json').read_text(encoding='utf-8'))['papers']
 db=a.base/'compatibility_scratch/data/papers.sqlite3'
 con=sqlite3.connect(db);con.row_factory=sqlite3.Row
 indexed={r['paper_id']:dict(r) for r in con.execute('SELECT paper_id,state,indexed_pages,total_pages,chunk_count FROM fulltext_documents')}
 con.close()
 reports=[]
 for paper in papers:
  pid=paper['paper_id'];start=time.monotonic()
  if pid in indexed and indexed[pid]['state'] in ('indexed','partial'):
   src=indexed[pid]
   record={'paper_id':pid,'state':src['state'],'indexed_pages':src['indexed_pages'],
           'pdf_pages':paper['pages'],'chunk_count':src['chunk_count'],'elapsed_seconds':0,'reused_existing':True}
  else:
   try:
    env=os.environ.copy();env['PYTHONPATH']=str(a.repo)
    r=subprocess.run([sys.executable,'-c',CHILD,str(db),pid],cwd=a.repo,env=env,
                     capture_output=True,text=True,timeout=a.timeout)
    if r.returncode!=0:
     record={'paper_id':pid,'state':'failed','exit_code':r.returncode,
             'error':r.stderr[-450:],'pdf_pages':paper['pages']}
    else:
     result=json.loads(r.stdout.strip().splitlines()[-1]);record={'paper_id':pid,'state':result['status'],
      'indexed_pages':result['pages'],'pdf_pages':paper['pages'],'chunk_count':result['chunks']}
   except subprocess.TimeoutExpired:
    record={'paper_id':pid,'state':'timeout','timeout_seconds':a.timeout,'pdf_pages':paper['pages']}
   except Exception as exc:
    record={'paper_id':pid,'state':'failed','error':str(exc)[:350],'pdf_pages':paper['pages']}
   record['elapsed_seconds']=round(time.monotonic()-start,2)
  reports.append(record)
  print(pid,record['state'],record.get('elapsed_seconds'),flush=True)
 result={'version':'1.45.0','mode':'separate_bounded_process_per_pdf',
         'papers':len(papers),'summary':{state:sum(r['state']==state for r in reports)
                          for state in ('indexed','partial','timeout','failed')},'results':reports}
 (a.base/'paper_radar_fulltext_compatibility.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print('SUMMARY',result['summary'])
if __name__=='__main__':main()

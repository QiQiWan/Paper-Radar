#!/usr/bin/env python3
"""Optional local Chromium smoke test using a throwaway SQLite/PDF fixture.
No external downloads, model calls or user data. Python + Playwright required.
"""
from __future__ import annotations
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
import json
import sys
from playwright.sync_api import sync_playwright

from paper_radar import webapp
from paper_radar.core import DEFAULTS,connect,save_papers
from paper_radar.models import Paper
from paper_radar.evidence_batch import process_run


def pdf_file(path, text):
    # PDF generation is kept test-only and does not ship with user data.
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer=PdfWriter()
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    page=writer.add_blank_page(width=612,height=792)
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject()
    text=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
    stream.set_data(('BT /F1 12 Tf 72 700 Td ('+text+') Tj ET').encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


def main(out:Path):
    out.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='paper_radar_v150_qa_') as tmp:
        root=Path(tmp);db_path=root/'data'/'papers.sqlite3'
        cfg={**DEFAULTS,'active_topic_id':'default','llm_data_consent':False,
             'onboarding_completed':True,'keywords':['neural operator']}
        db=connect(db_path)
        docs=[]
        for i in range(2):
            docs.append(Paper(paper_id=f'qa-evidence-{i}',title=f'Neural operator generalization across mesh geometry {i}',
                abstract='Neural operators are evaluated on irregular geometry and mesh resolution.',
                authors='Lab',published='2026-10-01',updated='2026-10-01',category='cs.LG',
                url='https://example.org',source='arxiv',pdf_url=''))
        save_papers(db,docs,topic_id='default')
        download=root/'downloads';download.mkdir()
        for i in range(2):
            path=download/f'qa-{i}.pdf';pdf_file(path,
                f'Experiments. On MeshBench RMSE 0.0123 in split A with baseline 0.050 for neural operator {i}.')
            db.execute("UPDATE papers SET local_file=?,download_status='downloaded' WHERE paper_id=?",
                       (path.name,docs[i].paper_id))
        db.commit();db.close()
        @contextmanager
        def local_db():
            conn=connect(db_path)
            try:
                with conn:yield conn
            finally:conn.close()
        server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler);server.token='qa-v150-csrf'
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        with patch.object(webapp,'DB_FILE',db_path),\
             patch.object(webapp,'LOCK_FILE',root/'worker.lock'),\
             patch.object(webapp,'open_database',local_db),\
             patch.object(webapp,'read_config',lambda:cfg),\
             patch.object(webapp,'config_health',lambda p:{'read_only':False}),\
             patch.object(webapp.runtime,'start',lambda mode,ids:process_run(db_path,ids[0],cfg)):
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True,executable_path='/usr/bin/chromium',
                                                       args=['--no-sandbox','--disable-dev-shm-usage'])
                    page=browser.new_page(viewport={'width':1365,'height':850},device_scale_factor=1)
                    # Chromium blocks every navigation in this isolated environment.
                    # Run the actual interactive component in about:blank, while
                    # its fetch callback reaches the real isolated HTTP server.
                    def relay(path,method='GET',body=None):
                        body_raw=json.dumps(body,ensure_ascii=False).encode('utf-8') if body is not None else None
                        headers={'X-Local-Token':'qa-v150-csrf','Origin':base,
                                 'Content-Type':'application/json'}
                        upstream=Request(base+str(path),data=body_raw,headers=headers,method=method)
                        try:
                            with urlopen(upstream,timeout=20) as response:
                                code=response.status;raw=response.read()
                        except HTTPError as error:
                            code=error.code;raw=error.read()
                        if code>=400:
                            try:detail=json.loads(raw)
                            except Exception:detail={'error':raw.decode('utf-8','replace')}
                            return {'ok':False,'message':detail.get('error',f'HTTP {code}')}
                        try:result=json.loads(raw)
                        except json.JSONDecodeError:result={'raw':raw.decode('utf-8','replace')}
                        return {'ok':True,'result':result}
                    page.expose_function('qaApi',relay)
                    page.set_content('<!doctype html><html lang="zh"><meta charset="utf-8"><body>'
                        '<main style="max-width:1000px;margin:25px auto;padding:18px">'
                        '<h1>研究分析</h1><label for="question">研究问题</label>'
                        '<textarea id="question" style="display:block;width:98%;height:95px;'
                        'padding:12px;margin:12px 0 20px"></textarea><div id="mount"></div>'
                        '</main></body></html>')
                    page.add_style_tag(content=(Path(__file__).parents[1]/'paper_radar/static/evidence_batch.css').read_text())
                    page.add_script_tag(content=(Path(__file__).parents[1]/'paper_radar/static/evidence_batch_workbench.js').read_text())
                    page.evaluate("""() => { window.qaBatch=PREvidenceBatch.mount({
                        root:document.querySelector('#mount'),
                        request:async (path,method='GET',payload=null)=>{
                            const response=await window.qaApi(path,method,payload);
                            if(!response.ok)throw new Error(response.message);
                            return response.result;
                        },
                        getQuestion:()=>document.querySelector('#question').value,
                        getModelState:()=>({ready:false,enabled:false}),
                        onModelSetup:()=>{}
                    }); }""")
                    errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
                    page.wait_for_selector('.batch-workbench',timeout=5000)
                    page.locator('#question').fill('神经算子在不同网格和几何条件下的泛化能力有哪些实验限制？')
                    page.get_by_role('button',name='选择相关论文').click()
                    page.wait_for_selector('.batch-select-paper',timeout=10000)
                    assert page.locator('.batch-select-paper').count()==2
                    page.screenshot(path=str(out/'batch_select_desktop.png'),full_page=False)
                    page.get_by_role('button',name='开始批量准备').click()
                    page.wait_for_selector('.batch-candidate',timeout=15000)
                    page.screenshot(path=str(out/'batch_review_desktop.png'),full_page=False)
                    card=page.locator('.batch-candidate').first
                    card.locator('.batch-input-field').nth(0).locator('input').fill('MeshBench')
                    card.locator('.batch-input-field').nth(1).locator('input').fill('RMSE')
                    card.locator('.batch-input-field').nth(2).locator('input').fill('0.0123')
                    card.locator('.batch-input-field').nth(3).locator('input').fill('baseline')
                    card.locator('.batch-input-field').nth(4).locator('input').fill('split A')
                    card.locator('.batch-input-field').nth(5).locator('input').fill('已核对 PDF 第 1 页实验条件与数值。')
                    card.get_by_role('button',name='核对后确认').click()
                    page.wait_for_function("document.querySelector('.batch-status-head')?.textContent.includes('已确认 1')",timeout=10000)
                    page.screenshot(path=str(out/'batch_approved_desktop.png'),full_page=False)
                    # Approve the second independent paper and inspect the matrix.
                    second=page.locator('.batch-candidate').first
                    for offset,value in enumerate(('MeshBench','RMSE','0.0123','baseline','split A','已核对第二篇 PDF 的指标与数据集。')):
                        second.locator('.batch-input-field').nth(offset).locator('input').fill(value)
                    second.get_by_role('button',name='核对后确认').click()
                    page.wait_for_function("document.querySelector('.batch-status-head')?.textContent.includes('已确认 2')",timeout=10000)
                    page.get_by_role('button',name='查看跨论文对照').click()
                    page.wait_for_selector('.batch-compare-group',timeout=10000)
                    assert page.locator('.batch-compare-entry').count()==2
                    page.screenshot(path=str(out/'batch_comparison_desktop.png'),full_page=False)
                    assert not errors,errors
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(out/'batch_review_mobile.png'),full_page=False)
                    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
                    browser.close()
                (out/'qa_results.json').write_text(json.dumps({'status':'passed','browser':'chromium',
                    'checks':['preview','select','batch_processing','review_approval','two_paper_comparison','desktop_layout','mobile_no_overflow'],
                    'javascript_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
                return 0
            finally:
                server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv)>1 else Path('/mnt/data/paper_radar_v150_qa')))

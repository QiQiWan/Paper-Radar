"""Browser QA for the persistent model error and the diagnostics download action.

Uses the existing isolated real-HTTP bridge from qa_v149_journeys. Browser native
navigation, credentials and external provider connectivity are not tested.
"""
from pathlib import Path
import os
import sys
if not os.environ.get('PAPER_RADAR_USER_HOME'):
    raise SystemExit('Set PAPER_RADAR_USER_HOME to an isolated test workspace before running QA.')
# This QA intentionally probes an unreachable localhost port, never a real provider.
os.environ['PAPER_RADAR_API_KEY']='paper-radar-qa-fake-key'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from http.server import ThreadingHTTPServer
from requests import Session
from playwright.sync_api import sync_playwright, expect
from tools.qa_v149_journeys import install_frontend, serving
from paper_radar.webapp import Handler
from paper_radar.core import DEFAULTS, save_config
from paper_radar.runtime_diagnostics import RuntimeDiagnostics
import json
import os
import shutil

out=Path(os.environ.get('PAPER_RADAR_QA_OUTPUT','/mnt/data/paper_radar_v1491_qa/error_browser'))
out.mkdir(parents=True,exist_ok=True)
cfg={**DEFAULTS,'api_provider':'DeepSeek','api_base_url':'http://127.0.0.1:9',
     'api_model':'example-model','llm_data_consent':True, 'update_check_enabled':False}
save_config(cfg)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
server.daemon_threads=True
server.token='qa-v148-token'
errors=[]
with serving(server) as base:
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox','--disable-dev-shm-usage'])
  page=browser.new_page(viewport={'width':1440,'height':900},accept_downloads=True)
  page.on('pageerror',lambda e:errors.append(str(e)))
  install_frontend(page,base,Session(),'local',[])
  expect(page.locator('#homeTopicName')).not_to_contain_text('正在读取')
  page.locator('.navigation [data-view="dashboard"]').click()
  page.locator('#aiResearchGoal').fill('研究有限元神经算子的跨网格与复杂几何泛化，并比较多种实验条件的误差')
  page.locator('#aiPlanRequest').click()
  expect(page.locator('#runtimeErrorBanner')).to_be_visible(timeout=9000)
  expect(page.locator('#runtimeErrorTitle')).to_have_text('模型连接未完成')
  expect(page.locator('#runtimeErrorMessage')).to_contain_text('模型服务暂时无法连接')
  expect(page.locator('#runtimeErrorMessage')).to_contain_text('错误编号')
  expect(page.locator('#runtimeErrorFix')).to_be_visible()
  page.wait_for_timeout(550)  # allow the user-facing smooth scroll to settle before capture
  page.screenshot(path=str(out/'model_service_error_actionable.png'),full_page=False)
  with page.expect_download(timeout=7000) as result:
   page.locator('#runtimeErrorDownload').click()
  download=result.value
  assert download.suggested_filename.endswith('.zip')
  with page.expect_download(timeout=7000) as result2:
   page.locator('#downloadRuntimeLogsTop').click()
  assert result2.value.suggested_filename.endswith('.zip')
  page.locator('#runtimeErrorClose').click()
  expect(page.locator('#runtimeErrorBanner')).to_be_hidden()
  browser.close()
records=[json.loads(x) for x in RuntimeDiagnostics(Path(os.environ['PAPER_RADAR_USER_HOME'])).recent().splitlines()]
assert any(r['event']=='model_error' for r in records)
assert not errors,errors
(out/'qa_result.json').write_text(json.dumps({'tests':6,'passed':6,'js_errors':errors},ensure_ascii=False,indent=2),'utf-8')
print('v1.49.1 browser error workflow 6/6 passed')

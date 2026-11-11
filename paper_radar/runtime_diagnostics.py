# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Bounded, privacy-conscious local runtime diagnostics.

Never record HTTP bodies, query strings, passwords, keys, cookies, model
prompts, paper titles, IPs or path arguments. Store only error classes and
Python frame *locations* (without source lines or local variables).
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
import json
import re
import secrets
import traceback
import zipfile

MAX_LOG_BYTES = 2_000_000
MAX_EXPORT_BYTES = 1_000_000
_ANALYSIS_COUNTERS = frozenset({'papers','confirmed_matches','candidate_papers','questions','finding_count','model_requests','anchors','matched','pending_sources','abstract_missing','progress_percent','completed_items','total_items','retrieved','new','downloaded','failed','indexed'})
_LOG_FILES = ('runtime.jsonl', 'runtime.jsonl.1', 'runtime.jsonl.2')
_LOCK = Lock()


def _route(value: str) -> str:
    path = urlsplit(str(value or '/')).path
    if path.startswith('/download/'):
        return '/download/<file>'
    if path.startswith('/guide/'):
        return '/guide/<page>'
    if re.fullmatch(r'/api/[A-Za-z0-9/_-]{1,90}', path):
        return path
    if re.fullmatch(r'/app/[A-Za-z0-9_-]{1,32}', path):
        return path
    return '/other'


def _frame_locations(exc: BaseException) -> list[str]:
    frames = traceback.extract_tb(exc.__traceback__)
    return [f'{Path(frame.filename).name}:{frame.lineno}:{frame.name[:50]}'
            for frame in frames[-16:]]


def _safe_tail(path: Path, max_bytes=MAX_EXPORT_BYTES) -> str:
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - max_bytes))
            return stream.read(max_bytes).decode('utf-8', 'replace')
    except OSError:
        return ''


def _scrub_user_log(value: str) -> str:
    """Best-effort redaction for *opted-in* legacy free-text task logs.

    Legacy log lines can contain research subjects; the UI warns the owner to
    inspect the bundle before sending it to another party.
    """
    value = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [REDACTED]', value)
    value = re.sub(r'(?i)(?:api[_ -]?key|token|password|secret)\s*[=:]\s*["\']?\S+', '[CREDENTIAL REDACTED]', value)
    value = re.sub(r'https?://[^\s\]\[<>"\']+', '[URL REDACTED]', value)
    return value


class RuntimeDiagnostics:
    def __init__(self, root: Path):
        self.folder = Path(root) / 'logs'

    def record(self, event: str, *, severity='info', method='', route='', code='',
               request_id='', status=None, duration_ms=None, error: BaseException | None = None,
               task='', detail='', run_id='', stage='', counts=None) -> str:
        request_id = request_id or (secrets.token_hex(6) if error else '')
        row = {
            'time': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'level': severity if severity in {'info', 'warning', 'error'} else 'info',
            'event': str(event)[:48],
        }
        if method in {'GET', 'POST', 'PUT', 'SYSTEM', 'WORKER'}: row['method'] = method
        if route: row['route'] = _route(route)
        if code: row['code'] = str(code)[:64]
        if request_id: row['request_id'] = request_id
        if isinstance(status, int): row['status'] = status
        if duration_ms is not None: row['duration_ms'] = max(0, int(duration_ms))
        if task: row['task'] = str(task)[:48]
        if isinstance(run_id,str) and re.fullmatch(r'[a-z0-9]{8,40}',run_id):row['run_id']=run_id
        if isinstance(stage,str) and re.fullmatch(r'[a-z0-9_]{2,48}',stage):row['stage']=stage
        if isinstance(counts,dict):
            safe={k:max(0,min(10000000,int(v))) for k,v in counts.items()
                  if k in _ANALYSIS_COUNTERS and type(v) in (int,bool) and v >= 0}
            if safe:row['counts']=safe
        # 'detail' is controlled diagnostic text, never an external exception.
        if detail: row['detail'] = str(detail)[:96]
        if error is not None:
            row['exception'] = type(error).__name__
            row['frames'] = _frame_locations(error)
        raw = json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n'
        with _LOCK:
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                log = self.folder / _LOG_FILES[0]
                if log.is_file() and log.stat().st_size + len(raw.encode('utf-8')) > MAX_LOG_BYTES:
                    for i in (2, 1):
                        older = self.folder / _LOG_FILES[i - 1]
                        newer = self.folder / _LOG_FILES[i]
                        if older.exists(): older.replace(newer)
                with log.open('a', encoding='utf-8') as stream:
                    stream.write(raw)
            except OSError:
                # Low disk space or a damaged volume must not take down a task.
                pass
        return request_id

    def recent(self, *, limit=90) -> str:
        tail = _safe_tail(self.folder / _LOG_FILES[0], 90_000)
        return '\n'.join(tail.splitlines()[-max(1, min(180, int(limit))):])

    def summary(self, *, limit=300) -> dict:
        """Bounded error overview; no raw titles, prompts, URLs or exception messages.

        Ignore corrupted/partial JSONL records so diagnostics remain accessible
        after abrupt shutdown and disk pressure.
        """
        from collections import Counter
        limit=max(1,min(500,int(limit)))
        raw='\n'.join(_safe_tail(self.folder / name, 125_000)
                 for name in reversed(_LOG_FILES))
        rows=[]
        for line in raw.splitlines()[-limit:]:
            try:
                item=json.loads(line)
            except (ValueError,TypeError):
                continue
            if not isinstance(item,dict):
                continue
            rows.append(item)
        codes=Counter()
        events=Counter()
        request_failures=0
        slow_requests=0
        failures=[]
        inquiry_runs={}
        for item in rows:
            level=item.get('level')
            event=str(item.get('event') or '')[:48]
            code=str(item.get('code') or '')[:64]
            status=item.get('status')
            events[event]+=1
            if item.get('task')=='research_inquiry' and item.get('run_id'):
                key=item['run_id']
                entry=inquiry_runs.setdefault(key,{'run_id':key,'started_at':item.get('time'),
                    'last_event':'','last_code':'','model_code':'','outcome_code':'','stages':[],'counts':{}})
                entry['last_event']=event
                if code: entry['last_code']=code
                if event in ('inquiry_model_status','inquiry_model_failed'):
                    entry['model_code']=code
                if event=='inquiry_composed':entry['outcome_code']=code
                if item.get('stage'):entry['stages'].append({'stage':item['stage'],
                    'status':code or 'ok','duration_ms':item.get('duration_ms',0)})
                if isinstance(item.get('counts'),dict):entry['counts'].update(item['counts'])
            if isinstance(status,int) and status>=400:
                request_failures+=1
            if item.get('event')=='http_request' and (item.get('duration_ms') or 0)>=5000:
                slow_requests+=1
            if level in ('warning','error') or (isinstance(status,int) and status>=500):
                if code: codes[code]+=1
                # Only explicit allowlisted diagnostic properties, never free-text detail.
                failures.append({k:item[k] for k in
                  ('time','level','event','method','route','code','status','request_id','exception')
                  if k in item})
        return {'sampled_events':len(rows),
                'note':'仅汇总最近最多 500 条结构化诊断，不代表整个运行周期或全部科研任务。',
                'errors_by_code':[{'code':k,'count':v} for k,v in codes.most_common(12)],
                'recent_issues':failures[-15:],
                'requests_http_4xx_5xx':request_failures,
                'slow_requests_over_5s':slow_requests,
                'client_disconnections':events.get('client_disconnected',0),
                'model_calls_failed':events.get('model_error',0)+events.get('ai_plan_unusable',0)+events.get('inquiry_model_failed',0),
                'diagnostic_exports':events.get('diagnostics_exported',0),
                'research_runs':list(inquiry_runs.values())[-8:],
                'model_disabled_runs':sum(1 for r in inquiry_runs.values() if r['model_code']=='MODEL_DISABLED'),
                'model_failure_events':events.get('inquiry_model_failed',0)}

    def evidence_batches(self) -> list[dict]:
        """Export bounded stage counts without topic names, titles, IDs or PDF text."""
        import sqlite3
        path=self.folder.parent/'data'/'papers.sqlite3'
        if not path.is_file():return []
        try:
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'evidence_batch_runs','evidence_batch_items','evidence_batch_events'}<=tables:
                    return []
                runs=db.execute('''SELECT id,state,created_at,updated_at,model_status FROM evidence_batch_runs
                    ORDER BY created_at DESC,id DESC LIMIT 15''').fetchall()
                result=[]
                for rid,state,created,updated,model_status in runs:
                    stage_counts=[{'stage':str(a)[:40],'state':str(b)[:40],'count':c}
                       for a,b,c in db.execute('''SELECT stage,state,COUNT(*) FROM evidence_batch_items
                           WHERE run_id=? GROUP BY stage,state''',(rid,))]
                    events=[{'stage':str(a)[:40],'outcome':str(b)[:40]}
                       for a,b in db.execute('''SELECT stage,outcome FROM evidence_batch_events
                           WHERE run_id=? ORDER BY id DESC LIMIT 80''',(rid,))]
                    result.append({'run_id':rid,'state':state,'created_at':created,
                        'updated_at':updated,'model_status':model_status,
                        'items':stage_counts,'recent_events':events})
                return result
        except (sqlite3.Error,OSError,ValueError):
            return []

    def export(self, *, include_task_logs=False) -> bytes:
        destination = BytesIO()
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            included = []
            for filename in _LOG_FILES:
                tail = _safe_tail(self.folder / filename)
                if tail:
                    archive.writestr(filename, tail)
                    included.append(filename)
            if include_task_logs:
                for filename in ('manual.log', 'scheduled.log', 'web_server.log'):
                    tail = _safe_tail(self.folder / filename, 200_000)
                    if tail:
                        archive.writestr('optional_task_logs/' + filename, _scrub_user_log(tail))
                        included.append('optional_task_logs/' + filename)
            overview=self.summary()
            batches=self.evidence_batches()
            archive.writestr('evidence_batches.json',json.dumps(batches,ensure_ascii=False,indent=2))
            archive.writestr('summary.json', json.dumps(overview,ensure_ascii=False,indent=2))
            archive.writestr('research_runs.json', json.dumps(overview.get('research_runs',[]),ensure_ascii=False,indent=2))
            guide=['# 运行诊断速览','',f'最近采样事件：{overview["sampled_events"]}',
                f'请求失败数：{overview["requests_http_4xx_5xx"]}',
                f'超过 5 秒的请求数：{overview["slow_requests_over_5s"]}',
                f'记录到的研究分析任务数：{len(overview.get("research_runs",[]))}',
                f'最近批量证据任务数：{len(batches)}','',
                '## 研究分析诊断','']
            for item in overview.get('research_runs',[]):
                guide += [f'- 任务 {item["run_id"]}：结果 {item["outcome_code"] or "尚未完成"}，模型 {item["model_code"] or "无记录"}，阶段 {len(item["stages"])}']
            guide += ['', '此报告仅含阶段、耗时与计数；不包含研究问题、论文正文、API Key 或模型提示词。',
                      '如显示 PDF 证据为零，优先检查 PDF 下载、全文解析、证据确认任务。']
            archive.writestr('README_FIRST.md','\n'.join(guide)+'\n')
            archive.writestr('manifest.json', json.dumps({
                'format': 'paper-radar-runtime-diagnostics-v1',
                'created_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'included': included+['summary.json','research_runs.json','evidence_batches.json','README_FIRST.md'], 'task_logs_opted_in': bool(include_task_logs),
                'sensitive_content': 'optional_task_logs may contain research topics; inspect before sharing',
                'excluded': ['settings.json', 'API keys', 'cookies', 'PDF files', 'database', 'HTTP request bodies'],
            }, ensure_ascii=False, indent=2))
            archive.writestr('README.txt',
                'Paper-Radar runtime diagnosis\n'
                'Search runtime.jsonl for the request_id shown in the UI.\n'
                'Entries contain route, status, exception class and stack frame locations, never request bodies.\n'
                'If optional task logs were requested, READ THEM BEFORE SHARING; research topics may be present.\n')
        return destination.getvalue()

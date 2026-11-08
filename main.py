# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Entry point: local browser UI, headless scheduled run, or demo report."""
from __future__ import annotations
import sys
import traceback
from datetime import datetime
from pathlib import Path


def run_once():
    from paper_radar.core import run_cycle, read_config, APP_ROOT, APP_CONFIG
    from paper_radar.config_safety import assert_primary_healthy, initialize_fresh_workspace
    from paper_radar.core import DEFAULTS, validate_config
    # Initialize a genuinely empty scheduled workspace before writing any
    # status or diagnostic files. Never recreate config when data already exists.
    initialize_fresh_workspace(APP_CONFIG, DEFAULTS, validate_config)
    from paper_radar.batch import run_all_topics
    from paper_radar.history import record_run
    from paper_radar.run_status import write_status
    from paper_radar.sources.retrieval_trace import RetrievalTrace, ACTIVE_TRACE
    logfile = APP_ROOT / 'logs' / 'scheduled.log'
    logfile.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone().isoformat(timespec='seconds')
    trace = RetrievalTrace(APP_ROOT,mode='scheduled')
    trace_token = ACTIVE_TRACE.set(trace)
    base_state = {'state':'running', 'mode':'scheduled', 'started_at':started_at,
                  'diagnostic_run_id':trace.run_id}
    write_status(base_state)
    last_progress = {}
    with logfile.open('a',encoding='utf-8') as f:
        def log(text):
            f.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n')
            f.flush()
        try:
            log('开始检索；诊断编号：'+trace.run_id)
            def on_progress(value):
                last_progress.update(value)
                write_status({**base_state, 'progress':value})
            cfg=read_config()
            trace.record('run_plan',source_total=len(cfg.get('sources',[])),
                query_total=len(cfg.get('keywords',[])),
                lookback_days=int(cfg.get('lookback_days',7)),
                max_results=int(cfg.get('max_results',30)),
                timeout_ms=int(cfg.get('source_total_timeout_seconds',210))*1000)
            assert_primary_healthy(APP_CONFIG)
            if cfg.get('schedule_all_topics'):
                result=run_all_topics(cfg,logger=log,progress=on_progress)
            else:
                result=run_cycle(config=cfg,logger=log,progress=on_progress)
                from paper_radar.task_outcome import retrieval_outcome
                report=retrieval_outcome(result.get('source_results',[]),
                    found=result['retrieved'],added=result['new'])
                outcome=report['outcome']
                history_state='failed' if outcome=='failed' else 'partial' if outcome in ('partial','unknown') else 'success'
                try:record_run(cfg['active_topic_id'],'scheduled',history_state,
                    result={**result,'diagnostic_run_id':trace.run_id},started_at=started_at)
                except Exception as exc:log('检索成功，但历史保存失败：'+str(exc)[:100])
            values={'retrieved':result['retrieved'],'new':result['new'], 'selected':result['selected']}
            if cfg.get('schedule_all_topics'):
                values.update(topics_succeeded=result.get('topics_succeeded',0),topics_failed=result.get('topics_failed',0))
            if not cfg.get('schedule_all_topics'):
                values.update(source_results=result.get('source_results',[]),
                    retrieval_summary=report)
                final_state=history_state
            else:
                final_state='partial' if values.get('topics_failed',0)>0 or result.get('topics_partial',0)>0 else 'success'
            write_status({'state':final_state,'mode':'scheduled','started_at':started_at,
                'finished_at':datetime.now().astimezone().isoformat(timespec='seconds'),'result':values,'progress':last_progress})
            log(f"完成：新增 {values['new']} 篇，已更新报告。")
            trace.close(final_state,count=values['retrieved'])
        except Exception as exc:
            log('执行失败：'+traceback.format_exc())
            trace.close('failed')
            try:
                if not read_config().get('schedule_all_topics'):
                    record_run(read_config()['active_topic_id'],'scheduled','failed',
                               result={'diagnostic_run_id':trace.run_id},error=str(exc),started_at=started_at)
            except Exception:
                pass
            write_status({'state':'failed','mode':'scheduled','started_at':started_at,
                'finished_at':datetime.now().astimezone().isoformat(timespec='seconds'),
                'error':str(exc)[:500], 'progress':last_progress})
            raise
        finally:
            ACTIVE_TRACE.reset(trace_token)


if __name__=='__main__':
    if '--once' in sys.argv:
        try:run_once()
        except Exception:sys.exit(1)
    elif '--demo' in sys.argv:
        from paper_radar.core import make_demo
        print(make_demo())
    elif '--cached' in sys.argv:
        from paper_radar.core import report_from_cache
        print(report_from_cache())
    elif '--serve' in sys.argv or '--web' in sys.argv:
        from paper_radar.webapp import run_server
        port = 8765
        if '--port' in sys.argv:
            try:
                port = int(sys.argv[sys.argv.index('--port')+1])
            except (ValueError, IndexError):
                raise SystemExit('端口号无效')
        if not (1024 <= port <= 65535):
            raise SystemExit('端口必须在 1024—65535 之间')
        if '--web' in sys.argv:
            import threading, webbrowser, time
            threading.Thread(target=lambda:(time.sleep(.8),webbrowser.open(f'http://127.0.0.1:{port}')),daemon=True).start()
        run_server(port)
    elif '--launch' in sys.argv:
        from paper_radar.launcher import main
        main()
    else:
        # Plain `python main.py` also runs the safe pre-launch update check.
        from startup import main as startup_main
        raise SystemExit(startup_main())

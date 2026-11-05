# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Entry point: local browser UI, headless scheduled run, or demo report."""
from __future__ import annotations
import sys
import traceback
from datetime import datetime
from pathlib import Path


def run_once():
    from paper_radar.core import run_cycle, read_config, APP_ROOT
    from paper_radar.batch import run_all_topics
    from paper_radar.history import record_run
    from paper_radar.run_status import write_status
    logfile = APP_ROOT / 'logs' / 'scheduled.log'
    logfile.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone().isoformat(timespec='seconds')
    base_state = {'state':'running', 'mode':'scheduled', 'started_at':started_at}
    write_status(base_state)
    last_progress = {}
    with logfile.open('a',encoding='utf-8') as f:
        def log(text):
            f.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n')
            f.flush()
        try:
            log('开始检索')
            def on_progress(value):
                last_progress.update(value)
                write_status({**base_state, 'progress':value})
            cfg=read_config()
            if cfg.get('schedule_all_topics'):
                result=run_all_topics(cfg,logger=log,progress=on_progress)
            else:
                result=run_cycle(config=cfg,logger=log,progress=on_progress)
                try:record_run(cfg['active_topic_id'],'scheduled','success',result=result,started_at=started_at)
                except Exception as exc:log('检索成功，但历史保存失败：'+str(exc)[:100])
            values={'retrieved':result['retrieved'],'new':result['new'], 'selected':result['selected']}
            if cfg.get('schedule_all_topics'):
                values.update(topics_succeeded=result.get('topics_succeeded',0),topics_failed=result.get('topics_failed',0))
            write_status({'state':'partial' if values.get('topics_failed',0)>0 else 'success','mode':'scheduled','started_at':started_at,
                'finished_at':datetime.now().astimezone().isoformat(timespec='seconds'),'result':values,'progress':last_progress})
            log(f"完成：新增 {values['new']} 篇，已更新报告。")
        except Exception as exc:
            log('执行失败：'+traceback.format_exc())
            try:
                if not read_config().get('schedule_all_topics'):
                    record_run(read_config()['active_topic_id'],'scheduled','failed',error=str(exc),started_at=started_at)
            except Exception:
                pass
            write_status({'state':'failed','mode':'scheduled','started_at':started_at,
                'finished_at':datetime.now().astimezone().isoformat(timespec='seconds'),
                'error':str(exc)[:500], 'progress':last_progress})
            raise


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

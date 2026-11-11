# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Unified CLI for public services, review workbench and diagnostics.

The repository root intentionally has only two platform startup scripts.
Use `start_windows.bat --help` or `./start_macos_linux.sh --help`.
"""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
HELP="""Paper-Radar unified launcher
  start_windows.bat                   Local literature workbench
  start_windows.bat --review-init     Create separate A/B/adjudicator/auditor credentials
  start_windows.bat --review          Local PDF blind-review workbench (127.0.0.1:8778)
  start_windows.bat --review-status   Review progress and audit chain
  start_windows.bat --review-export   Export the audit snapshot
  start_windows.bat --review-gold     Export complete adjudicated gold CSV files
  start_windows.bat --eval           RealPDF30 repeatable offline benchmark
  start_windows.bat --smoke          Verify public literature APIs
  start_windows.bat --public [PORT]  Public portal service (requires public origin)
  start_windows.bat --worker         Public research task worker
  start_windows.bat --diagnostics   Export bounded runtime diagnostics without starting UI
The same options work on macOS/Linux via ./start_macos_linux.sh.
"""


def _execute(*args):
    return subprocess.call([sys.executable,*args],cwd=ROOT)


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('--help','-h'):
        print(HELP)
        return 0
    mode=argv.pop(0)
    if mode=='--diagnostics':
        from datetime import datetime
        from .runtime_diagnostics import RuntimeDiagnostics
        from .storage_layout import resolve_data_root
        if argv not in ([],['--include-task-logs']):
            print('Usage: --diagnostics [--include-task-logs]',file=sys.stderr)
            return 2
        target=Path.home()/'Downloads'
        if not target.is_dir():target=Path.cwd()
        target=target/('Paper-Radar-Diagnostics-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
        target.write_bytes(RuntimeDiagnostics(resolve_data_root()).export(include_task_logs=bool(argv)))
        print('运行日志已保存：'+str(target))
        if argv:print('已附带可含研究主题的任务日志，分享前请检查。')
        return 0
    if mode in ('--review-init','--review','--review-status','--review-export','--review-gold'):
        sub={'--review-init':'init','--review':'serve','--review-status':'status','--review-export':'export','--review-gold':'export-gold'}[mode]
        return _execute('-m','paper_radar.review_webapp',sub,*argv)
    if mode=='--smoke':
        return _execute('-m','paper_radar.sources.smoke','--live','--output','retrieval_smoke.json',*argv)
    if mode=='--eval':
        dataset=ROOT/'benchmarks/real_pdf30'
        pdf=dataset/'pdfs'
        out=ROOT/'realpdf30_evaluation'
        commands=[
            [str(dataset/'scripts/download_official_pdfs.py'),'--manifest',str(dataset/'manifest.json'),'--dest',str(pdf)],
            ['-m','benchmarks.real_pdf30.scripts.synthesis_realpilot_v1453','--dataset',str(dataset),
             '--pdf-dir',str(pdf),'--out',str(out/'synthesis_pilot.json')],
            ['-m','tools.eval_realpdf30_models','--mode','check-gold','--dataset',str(dataset),
             '--pdf-dir',str(pdf),'--geometry',str(out/'no_existing_geometry.jsonl'),
             '--output',str(out/'gold_status')]
        ]
        for args in commands:
            result=_execute(*args)
            if result:return result
        return 0
    if mode=='--public':
        port=argv.pop(0) if argv else os.environ.get('PAPERRADAR_PUBLIC_PORT','8899')
        origin=os.environ.get('PAPERRADAR_PUBLIC_ORIGIN','')
        if not origin:
            print('Set PAPERRADAR_PUBLIC_ORIGIN to the HTTPS domain (or localhost test origin)',file=sys.stderr)
            return 2
        data=os.environ.get('PAPERRADAR_PORTAL_DATA',str(ROOT/'storage/portal'))
        return _execute('-m','paper_radar.public_portal','--data-root',data,
                        'serve','--host','127.0.0.1','--port',port,'--origin',origin,*argv)
    if mode=='--worker':
        data=os.environ.get('PAPERRADAR_PORTAL_DATA',str(ROOT/'storage/portal'))
        env=os.environ
        return _execute('-m','paper_radar.portal_worker','--data-root',data,
                        '--lease-seconds',env.get('PAPERRADAR_LEASE_SECONDS','45'),
                        '--heartbeat-seconds',env.get('PAPERRADAR_HEARTBEAT_SECONDS','5'),
                        '--job-timeout',env.get('PAPERRADAR_JOB_TIMEOUT','480'),
                        '--cpu-seconds',env.get('PAPERRADAR_CPU_SECONDS','240'),
                        '--memory-mb',env.get('PAPERRADAR_WORKER_MEMORY_MB','1536'),'run',*argv)
    print('Unknown launcher option: '+mode+'\n\n'+HELP,file=sys.stderr)
    return 2


if __name__=='__main__':
    raise SystemExit(main())

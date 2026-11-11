#!/usr/bin/env python3
"""Export actual topic-library pilot outcomes without network access or API credentials."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0,str(PROJECT))

from paper_radar.core import DB_FILE, APP_CONFIG, open_database, read_config
from paper_radar.pilot_audit import export_zip, overview


def main():
    ap=argparse.ArgumentParser(description='Export real paper-library pilot metrics')
    ap.add_argument('--db',type=Path,default=DB_FILE,help='Existing papers.sqlite3')
    ap.add_argument('--topic',default='',help='Existing topic ID (default: current topic)')
    ap.add_argument('--output',type=Path,default=None,help='Where to save ZIP')
    args=ap.parse_args()
    db_path=args.db.expanduser().resolve()
    if not db_path.is_file():
        ap.error('找不到文献数据库：'+str(db_path)+'；请先运行 Paper-Radar 检索。')
    if args.topic:
        topic=args.topic
    elif args.db.resolve()==DB_FILE.resolve() and APP_CONFIG.is_file():
        topic=read_config(APP_CONFIG).get('active_topic_id','default')
    else:
        topic='default'
    output=args.output or (PROJECT/'reports'/'pilot'/('pilot_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.zip'))
    output=output.expanduser().resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    with open_database(db_path) as conn:
        info=overview(conn,topic,db_path)
        data=export_zip(conn,topic,db_path)
    output.write_bytes(data)
    stats=info['summary']
    print('试点统计导出完成：',output)
    print('当前主题论文总量：',stats['library_total'])
    print('纳入批量试点：',stats['pilot_selected_unique'])
    print('本地 PDF：',stats['downloaded_or_existing_pdf'])
    print('PDF 实测获取率：',stats['pdf_acquisition_rate'])
    print('人工字段准确率：',stats['field_accuracy_on_reviewed_fields'])
    print('人工有效审核耗时（秒）：',stats['mean_active_review_seconds'])
    print('已确认可比的跨论文组：',stats['method_confirmed_comparison_groups'])
    if not stats['pilot_selected_unique']:
        print('目前没有实际批量任务。请在「研究分析」中选择高相关论文并执行，再导出。')
    return 0

if __name__=='__main__':
    raise SystemExit(main())

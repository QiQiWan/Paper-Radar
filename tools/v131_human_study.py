# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Local 1,000-paper research-workspace *human* task recorder.

Runs a real HTTP portal and an isolated seeded account per participant. An
observer operates this terminal while the participant operates the browser.
No timings or success outcomes are invented. Consent, recruitment, OS versions,
accessibility and subjective usability are administered outside the software.

python -m tools.v131_human_study --participants 5 --out human_study.json
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import secrets
from statistics import median
from time import perf_counter

from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar import __version__
from tools.v131_workspace_benchmark import workspace_server,seed_workspace,pct

TASKS=[
 ('login_dashboard','使用测试账号登录，确认工作区展示 1,000 篇论文'),
 ('library','进入论文库，确认首屏 12 条并辨认阅读状态'),
 ('search','使用搜索框定位编号 000124 的论文'),
 ('star','将编号 000124 的论文添加星标'),
 ('reading','将编号 000124 的论文设为「已读完」'),
 ('filter','同时使用星标筛选与搜索，定位 000124'),
 ('restore','复制当前论文库链接，新开浏览器标签并恢复筛选状态'),
 ('pagination','清除筛选后加载下一页，核实论文数量变化'),
 ('report_export','进入报告页并下载可核对的证据 CSV'),
 ('navigation','在任务页和报告页间切换，并用浏览器返回键回到任务页'),
 ('relogin','退出登录，重新登录并确认星标和阅读状态保留'),
 ('following','进入研究跟进，辨认各来源的成功状态与待补检提示'),
 ('follow_settings','关闭和重新启用当前主题的站内新论文提醒，并刷新验证'),
 ('follow_navigation','分享研究跟进链接，在新标签页打开并确认研究主题没有改变'),
]


def aggregate(sessions:list[dict])->dict:
    items=[step for session in sessions for step in session['tasks']]
    total=len(items)
    passed=sum(bool(t['success']) for t in items)
    times=[t['seconds']*1000 for t in items]
    errors=sum(int(t['error_count']) for t in items)
    return {'participants':len(sessions),'tasks':total,'completed':passed,
            'completion_rate':round(passed/total,4) if total else None,
            'task_failure_rate':round((total-passed)/total,4) if total else None,
            'interaction_error_count':errors,
            'interaction_errors_per_task':round(errors/total,3) if total else None,
            'p50_task_ms':pct(times,50),'p95_task_ms':pct(times,95),
            'median_task_ms':round(median(times),2) if times else None}


def _save(path:Path,sessions:list[dict],*,papers:int):
    report={'schema_version':1,'version':__version__,'kind':'observed_human_participants',
            'not_automated':True,'synthetic_paper_metadata':True,'pdf_count':0,
            'papers_per_account':papers,'summary':aggregate(sessions),'sessions':sessions,
            'timestamp_utc':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    path.parent.mkdir(parents=True,exist_ok=True)
    target=path.with_name(path.name+'.tmp')
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    target.replace(path)
    return report


def run(*,participants:int,papers:int,out:Path):
    if not 1<=participants<=20 or papers<1000 or papers>5000:
        raise ValueError('participants must be 1..20 and papers 1000..5000')
    print('仅使用临时合成论文。请先完成受试者知情同意并保证每人独立操作。')
    sessions=[]
    with workspace_server(papers=papers) as env:
        for i in range(1,participants+1):
            participant=f'P{i:02d}'
            username=f'ux_{i:02d}'
            password=secrets.token_urlsafe(18)
            with auth_db(env['portal'].auth_path) as db:
                account=create_account(db,username,password)
            seed_workspace(env['portal'],account['id'],papers)
            print(f'\n受试者 {participant}，URL: {env["base"]}，账号: {username}，临时密码: {password}')
            print('请勿在对外共享的研究数据中复制临时密码。')
            session={'participant':participant,'tasks':[],'status':'in_progress'}
            sessions.append(session)
            _save(out,sessions,papers=papers)
            for slug,description in TASKS:
                print(f'\n任务 {slug}：{description}')
                input('观察员准备完毕，受试者开始前按 Enter 计时：')
                start=perf_counter()
                input('受试者完成、放弃或超时后，立即按 Enter 停止计时：')
                seconds=round(perf_counter()-start,3)
                while True:
                    state=input('任务成功完成？（1 成功 / 0 失败）：').strip()
                    if state in ('0','1'):break
                while True:
                    count=input('记录操作失误次数（非负整数，包括恢复过的错误）：').strip()
                    if count.isdigit():break
                item={'task':slug,'success':state=='1','seconds':seconds,'error_count':int(count)}
                session['tasks'].append(item)
                _save(out,sessions,papers=papers)
            session['status']='completed'
            _save(out,sessions,papers=papers)
            print(f'受试者 {participant} 已完成，请退出当前账号后换下一位。')
    summary=_save(out,sessions,papers=papers)['summary']
    print('研究结果:',json.dumps(summary,ensure_ascii=False))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--participants',type=int,default=5)
    parser.add_argument('--papers',type=int,default=1000)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    run(participants=args.participants,papers=args.papers,out=args.out)

if __name__=='__main__':main()

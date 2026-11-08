"""Guard task terminology and navigation consistency in UI-only releases."""
from __future__ import annotations
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'paper_radar'/'static'
LIVE_FILES=['index.html','portal.html','app.js','portal.js','inquiry_workbench.js','trace_workbench.js']
FORBIDDEN={
    '检索论文并更新报告': '旧检索按钮名称已废弃，应使用“开始检索”',
    'AI 推荐理由（仅基于题名摘要）': '摘要匹配内容不得称作全文证据',
    '更清楚地追踪你的研究前沿': '删除宣传式标题',
    '等待 Worker': '用户任务状态统一为“排队中”',
    '查看不可覆盖的历史修订记录': '统一为“修订记录”',
}
NAV_LABELS=('工作台','论文库','任务中心','研究跟进','研究助手','研究报告','设置')

class IDs(HTMLParser):
    def __init__(self):super().__init__();self.values=set()
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if attrs.get('id'):self.values.add(attrs['id'])

def check() -> dict:
    texts={name:(STATIC/name).read_text('utf-8') for name in LIVE_FILES}
    bad=[]
    for name,content in texts.items():
        for phrase,message in FORBIDDEN.items():
            if phrase in content:bad.append(f'{name}: {message}: {phrase}')
    if '<script defer="" src="/ui_state.js"></script>' not in texts['index.html']:
        bad.append('本地版未引入统一状态组件')
    if '<script defer="" src="/ui_state.js"></script>' not in texts['portal.html']:
        bad.append('公网版未引入统一状态组件')
    for label in NAV_LABELS:
        if not (re.search(r'<span>'+label+r'</span>',texts['index.html']) and
                re.search(r'<span>'+label+r'</span>',texts['portal.html'])):
            bad.append('两端导航不一致: '+label)
    for file in ('index.html','portal.html'):
        ids=IDs();ids.feed(texts[file])
        if not ids.values:bad.append(file+' 未找到控件 ID')
        if file=='index.html' and not {'runSearch','sourceOptions','taskStage','settings-sources'}.issubset(ids.values):
            bad.append('本地关键操作入口缺失')
        if file=='portal.html' and not {'searchNow','runResearch','jobState','cloudAnalysis'}.issubset(ids.values):
            bad.append('公网关键操作入口缺失')
    if bad:raise AssertionError('\n'.join(bad))
    return {'live_files':len(LIVE_FILES),'forbidden_phrases':len(FORBIDDEN),'nav_labels':len(NAV_LABELS),'status_component':True}

if __name__=='__main__':
    import json
    print(json.dumps(check(),ensure_ascii=False))

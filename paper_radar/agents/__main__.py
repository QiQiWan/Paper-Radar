# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Inspect the agent DAG without API credentials: python -m paper_radar.agents."""
import argparse
import json
from pathlib import Path
from .catalog import build_registry, ON_DEMAND
from .trace import read_traces
from .research.synthesis import registry as synthesis_registry


def main(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar Agent 工作流与状态')
    parser.add_argument('command',nargs='?',choices=['list','graph','traces','doctor','research-graph','synthesis-graph'],default='list')
    parser.add_argument('--db',type=Path,help='论文数据库路径；traces 使用其同级 Agent 记录')
    parser.add_argument('--topic',default=None,help='限制查看主题 ID')
    args=parser.parse_args(argv)
    registry=build_registry()
    if args.command=='list':
        print(json.dumps({'pipeline':registry.manifest(),'synthesis_pipeline':synthesis_registry().manifest(),'on_demand':ON_DEMAND},ensure_ascii=False,indent=2))
    elif args.command=='graph':
        print('digraph PaperRadarAgents {')
        for entry in registry.manifest():
            print(f'  {entry["name"]} [label="{entry["title"]}"];')
            for dep in entry['depends_on']:
                print(f'  {dep} -> {entry["name"]};')
        print('}')
    elif args.command=='research-graph':
        from .research.pipeline import research_registry
        print('digraph ResearchAgents {')
        for entry in research_registry().manifest():
            print(f'  {entry["name"]} [label="{entry["title"]}"];')
            for dep in entry['depends_on']:
                print(f'  {dep} -> {entry["name"]};')
        print('}')
    elif args.command=='synthesis-graph':
        print('digraph SynthesisAgents {')
        for entry in synthesis_registry().manifest():
            print(f'  {entry["name"]} [label="{entry["title"]}"];')
            for dep in entry['depends_on']:
                print(f'  {dep} -> {entry["name"]};')
        print('}')
    elif args.command=='doctor':
        from importlib import import_module
        problems=[]
        from .research.pipeline import research_registry
        for agent in registry.plan()+research_registry().plan()+synthesis_registry().plan():
            try:
                module = import_module(agent.spec.owner_module)
                if agent.execute.__module__ != module.__name__:
                    problems.append(agent.spec.name + ': execute入口与注册表不一致')
            except (ImportError, AttributeError) as exc:
                problems.append(agent.spec.name + ': 模块无法导入 (' + type(exc).__name__ + ')')
        print(json.dumps({'state':'ok' if not problems else 'failed',
                          'agent_count':len(registry.plan())+len(research_registry().plan())+len(synthesis_registry().plan()),'problems':problems,
                          'scope':'本地定义检查；不调用网络、模型或数据库'},
                         ensure_ascii=False,indent=2))
        if problems: return 1
    else:
        from ..core import DB_FILE
        db=args.db or DB_FILE
        print(json.dumps(read_traces(db.parent/'agent_traces.sqlite3',topic_id=args.topic),
                         ensure_ascii=False,indent=2))

if __name__=='__main__':
    raise SystemExit(main())

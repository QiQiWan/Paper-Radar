# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Local API invocation accounting. No provider credentials or prompts stored.
Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""
from __future__ import annotations
from pathlib import Path
from threading import Lock
from contextlib import contextmanager
from contextvars import ContextVar
import json, os
_LOCK = Lock()
_ACTIVE_USAGE = ContextVar('paper_radar_active_agent_usage', default=None)

@contextmanager
def capture_agent_usage(agent_name: str):
    """Account confirmed token usage to the currently executing Agent.

    Preserves the historic per-provider accounting file. Never saves prompts,
    completions, keys, or user text in traces.
    """
    usage = {'agent': agent_name, 'requests': 0, 'input_tokens': 0, 'output_tokens': 0}
    token = _ACTIVE_USAGE.set(usage)
    try:
        yield usage
    finally:
        _ACTIVE_USAGE.reset(token)


def _path() -> Path:
    from .core import APP_ROOT
    return APP_ROOT/'data'/'api_usage.json'

def read_usage(path: Path | None = None) -> dict:
    p=path or _path()
    try:
        value=json.loads(p.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

def record_usage(provider:str,prompt_tokens:int=0,completion_tokens:int=0,path:Path|None=None)->None:
    with _LOCK:
        p=path or _path(); p.parent.mkdir(parents=True,exist_ok=True)
        data=read_usage(p)
        entry=data.setdefault(provider,{'requests':0,'input_tokens':0,'output_tokens':0})
        entry['requests']+=1
        entry['input_tokens']+=max(0,int(prompt_tokens or 0))
        entry['output_tokens']+=max(0,int(completion_tokens or 0))
        temp=p.with_name(p.name+f'.{os.getpid()}.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(p)
        ledger = _ACTIVE_USAGE.get()
        if ledger is not None:
            ledger['requests'] += 1
            ledger['input_tokens'] += max(0,int(prompt_tokens or 0))
            ledger['output_tokens'] += max(0,int(completion_tokens or 0))

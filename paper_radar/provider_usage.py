# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Local API invocation accounting. No provider credentials or prompts stored.
Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""
from __future__ import annotations
from pathlib import Path
from threading import Lock
from contextlib import contextmanager
from contextvars import ContextVar
import json, os, secrets
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
    # Production Workers run with a read-only source tree. Keep the anonymous
    # provider-level aggregate inside the configured, writable portal data root.
    portal_root=os.getenv('PAPERRADAR_PORTAL_DATA','').strip()
    if portal_root:
        return Path(portal_root).resolve()/'metrics'/'api_usage.json'
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
        from .portal_resource_locks import exclusive_file_lock
        p=Path(path or _path());p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Serialize read-modify-write across independent Worker processes;
        # a process-local threading.Lock alone silently loses increments.
        with exclusive_file_lock(p.parent/'.api_usage.lock',timeout=10):
            data=read_usage(p)
            entry=data.setdefault(provider,{'requests':0,'input_tokens':0,'output_tokens':0})
            entry['requests']+=1
            entry['input_tokens']+=max(0,int(prompt_tokens or 0))
            entry['output_tokens']+=max(0,int(completion_tokens or 0))
            temp=p.with_name('.'+p.name+'.'+str(os.getpid())+'.'+secrets.token_hex(6)+'.tmp')
            try:
                temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                if os.name!='nt':os.chmod(temp,0o600)
                temp.replace(p)
            finally:
                temp.unlink(missing_ok=True)
        ledger = _ACTIVE_USAGE.get()
        if ledger is not None:
            ledger['requests'] += 1
            ledger['input_tokens'] += max(0,int(prompt_tokens or 0))
            ledger['output_tokens'] += max(0,int(completion_tokens or 0))

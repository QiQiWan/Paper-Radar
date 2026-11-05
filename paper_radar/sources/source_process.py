# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Fixed subprocess entrypoint for an approved registered source."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
import json
import sys


def main():
    try:
        message=json.loads(sys.stdin.buffer.read(32769).decode('utf-8'))
        from .base import _REGISTRY
        from . import available_sources  # imports/initializes the approved registry
        name=message['name']
        if name not in _REGISTRY:
            raise ValueError('未知检索来源')
        config=message['config']
        if not isinstance(config,dict):
            raise ValueError('检索参数格式无效')
        now=datetime.fromisoformat(message['now']) if message.get('now') else None
        papers=_REGISTRY[name]().fetch(config,logger=lambda _: None,now=now)
        raw={'ok':True,'papers':[asdict(p) for p in papers[:300]]}
    except Exception as exc:
        # The child must never echo an API URL (Springer can embed secrets).
        raw={'ok':False,'error':f'{type(exc).__name__}：来源连接失败或授权不可用，请检查网络、访问凭证与来源状态'}
    sys.stdout.write(json.dumps(raw,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())

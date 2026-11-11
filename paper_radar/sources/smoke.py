# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Explicit, bounded end-to-end retrieval check through the real child IPC.

Does not read a user's research topics or persist any paper metadata. All
output is sanitized status and transport events, even when a server fails.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from .supervisor import fetch_isolated, SourceTimeout
from .failures import SourceQueryError
from .retrieval_trace import sanitized_event

DEFAULT_SOURCES = ('arxiv', 'openalex', 'crossref', 'openreview')


def check(sources=DEFAULT_SOURCES, *, timeout=25.):
    statuses = []
    events = []
    for source in sources:
        start = monotonic()
        local_events = []
        config = {
            'keywords': ['machine learning'], 'categories': [],
            'lookback_days': 30, 'max_results': 3,
            'max_queries_per_source': 1,
        }
        try:
            papers = fetch_isolated(source, config, timeout_seconds=timeout,
                                    trace_hook=local_events.append)
            ok, code, count = True, 'OK', len(papers)
        except SourceTimeout:
            ok, code, count = False, 'SOURCE_TIMEOUT', 0
        except SourceQueryError as exc:
            ok, code, count = False, exc.code, 0
        except Exception:
            # Do not serialize raw exception text or accidental URLs.
            ok, code, count = False, 'SOURCE_UNAVAILABLE', 0
        statuses.append({'source': source, 'ok': ok, 'error_code': code,
                         'count': count,
                         'elapsed_ms': round((monotonic()-start)*1000)})
        events.extend(local_events)
    return {'schema': 'paper-radar-retrieval-smoke-v1',
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'sources': statuses, 'events': events}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bounded live literature retrieval diagnostics')
    parser.add_argument('--live', action='store_true', help='Explicitly request public metadata APIs')
    parser.add_argument('--sources', nargs='+', choices=DEFAULT_SOURCES,
                        default=list(DEFAULT_SOURCES))
    parser.add_argument('--timeout', type=float, default=25.,
                        help='Seconds per source child (default 25)')
    parser.add_argument('--output', default='', help='Optional report JSON file')
    args = parser.parse_args(argv)
    if not args.live:
        parser.error('Add --live to approve public API test requests')
    if not 3. <= args.timeout <= 90.:
        parser.error('--timeout must be 3-90 seconds')
    result = check(args.sources, timeout=args.timeout)
    for item in result['sources']:
        label = '连接与解析正常' if item['ok'] else item['error_code']
        print(f"{item['source']}: {label}; {item['count']} 篇; {item['elapsed_ms']} ms")
    if args.output:
        path = Path(args.output)
        if path.parent and not path.parent.is_dir():
            parser.error('Report directory must already exist')
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('诊断已保存：' + str(path))
    return 0 if any(x['ok'] for x in result['sources']) else 1


if __name__ == '__main__':
    raise SystemExit(main())

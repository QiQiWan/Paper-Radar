# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Source API credentials. Local OS keyring or explicit environment variables only.

Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""
from __future__ import annotations
import os

SERVICE = 'Paper-Radar-Publishers'
ENV = {
    ('elsevier', 'key'): 'PAPER_RADAR_ELSEVIER_API_KEY',
    ('elsevier', 'insttoken'): 'PAPER_RADAR_ELSEVIER_INSTTOKEN',
    ('springer', 'key'): 'PAPER_RADAR_SPRINGER_API_KEY',
    ('openalex', 'key'): 'OPENALEX_API_KEY',
}

def get_credential(source: str, field: str = 'key') -> str:
    # The public portal must not silently spend a desktop user's keyring
    # credits on untrusted multi-user metadata searches.
    if (source == 'openalex' and os.environ.get('PAPERRADAR_PUBLIC_METADATA_CHILD') == '1'
            and os.environ.get('PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY') != '1'):
        return ''
    env = ENV.get((source, field))
    if env:
        value = os.environ.get(env, '').strip()
        if value:
            return value
    try:
        import keyring
        return (keyring.get_password(SERVICE, f'{source}:{field}') or '').strip()
    except Exception:
        return ''

def save_credential(source: str, field: str, value: str) -> None:
    if (source, field) not in ENV:
        raise ValueError('不支持的出版商凭据字段')
    if len(value) > 2000:
        raise ValueError('凭据长度超出限制')
    try:
        import keyring
        if value.strip():
            keyring.set_password(SERVICE, f'{source}:{field}', value.strip())
        else:
            try:
                keyring.delete_password(SERVICE, f'{source}:{field}')
            except Exception:
                pass
    except Exception as exc:
        raise RuntimeError('无法访问系统凭据管理器，请检查 keyring 是否正确安装。') from exc

def credential_state() -> dict[str, dict[str, bool]]:
    status = {src: {'key': bool(get_credential(src)),
                    'insttoken': bool(get_credential(src, 'insttoken')) if src == 'elsevier' else False}
              for src in ('elsevier', 'springer', 'openalex')}
    status['sciencedirect'] = dict(status['elsevier'])
    return status

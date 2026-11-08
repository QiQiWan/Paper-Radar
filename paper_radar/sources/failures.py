# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Sanitized, stable source failure codes for local and public research.

Only approved error categories cross the child-process boundary. URLs, tokens,
query strings, and raw exception messages are never forwarded to a browser.
"""
from __future__ import annotations

import errno
import json
import socket
import xml.etree.ElementTree as ET
import ssl
from urllib.error import HTTPError, URLError

MESSAGES = {
    'DNS_ERROR': '域名解析失败，请检查 DNS 或网络连接',
    'PROXY_ERROR': '网络代理连接失败，请检查系统代理设置',
    'TLS_ERROR': '安全连接或证书验证失败，请检查系统时间及证书',
    'NETWORK_ERROR': '无法连接文献服务，请检查网络或代理',
    'SOURCE_TIMEOUT': '文献服务响应超时，可稍后重试或调整等待时间',
    'RATE_LIMIT': '文献服务限制了请求频率，请稍后重试',
    'AUTH_REQUIRED': '来源要求授权或 API Key，请检查权限',
    'BAD_QUERY': '检索条件被来源拒绝，请简化关键词或筛选条件',
    'REMOTE_SERVER_ERROR': '文献服务暂时出现服务器错误',
    'SOURCE_UNAVAILABLE': '文献服务未返回可用结果，请检查该来源状态',
    'SOURCE_CHILD_FAILED': '检索子进程异常退出，请查看诊断中的退出码及运行环境',
    'SOURCE_CHILD_ENCODING': '检索子进程输出编码不兼容，请升级并重启程序',
    'SOURCE_CHILD_EMPTY': '检索子进程没有返回任何结果数据，请检查启动方式和程序安装',
    'SOURCE_CHILD_PROTOCOL': '来源子进程未返回有效结果，请检查 Python 环境及程序安装',
    'SOURCE_CHILD_START': '无法启动来源子进程，请检查 Python 环境或安全软件',
    'SOURCE_RESPONSE_INVALID': '连接后无法解析来源返回的数据，请检查接口状态或版本',
    'SOURCE_PARTIAL': '主检索成功，部分版本检查未完成，需在下次检索时补查',
    'SOURCE_BUDGET_EXHAUSTED': '该来源本次时间预算已用完，其他来源仍会继续',
}


def code_for_exception(exc: BaseException) -> str:
    """Classify nested urllib exceptions without exposing transport details."""
    seen: set[int] = set()
    current: BaseException | None = exc
    chain = []
    while isinstance(current, BaseException) and id(current) not in seen and len(chain) < 5:
        chain.append(current)
        seen.add(id(current))
        reason = current.reason if isinstance(current, URLError) else None
        current = (reason if isinstance(reason, BaseException) else
                   current.__cause__ or current.__context__)
    for item in chain:
        if isinstance(item, HTTPError):
            if item.code == 429: return 'RATE_LIMIT'
            if item.code in (401, 403, 409): return 'AUTH_REQUIRED'
            if 400 <= item.code < 500: return 'BAD_QUERY'
            if 500 <= item.code <= 599: return 'REMOTE_SERVER_ERROR'
    for item in chain:
        if isinstance(item, (json.JSONDecodeError, ET.ParseError)): return 'SOURCE_RESPONSE_INVALID'
    for item in chain:
        if isinstance(item, (ssl.SSLError, ssl.CertificateError)): return 'TLS_ERROR'
        if isinstance(item, socket.gaierror): return 'DNS_ERROR'
        if isinstance(item, (socket.timeout, TimeoutError)): return 'SOURCE_TIMEOUT'
        if isinstance(item, OSError):
            if item.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH,
                              errno.ECONNREFUSED, errno.ECONNRESET):
                return 'NETWORK_ERROR'
    message = str(exc).lower()
    if '返回数据格式' in message or '响应格式' in message: return 'SOURCE_RESPONSE_INVALID'
    if 'proxy' in message or '代理' in message: return 'PROXY_ERROR'
    if 'name resolution' in message or 'getaddrinfo' in message or 'dns' in message: return 'DNS_ERROR'
    if 'certificate' in message or 'ssl' in message or '证书' in message: return 'TLS_ERROR'
    if 'timed out' in message or 'timeout' in message or '超时' in message: return 'SOURCE_TIMEOUT'
    if 'rate limit' in message or '429' in message or '限流' in message: return 'RATE_LIMIT'
    if 'api key' in message or '授权' in message or '权限' in message: return 'AUTH_REQUIRED'
    if '拒绝查询' in message or '参数' in message: return 'BAD_QUERY'
    return 'SOURCE_UNAVAILABLE'


class SourceQueryError(RuntimeError):
    def __init__(self, code: str):
        self.code = code if code in MESSAGES else 'SOURCE_UNAVAILABLE'
        super().__init__(MESSAGES[self.code])

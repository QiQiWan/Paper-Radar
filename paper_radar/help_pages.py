# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Read-only allowlisted documentation pages shared by local and public UIs.

No filesystem path or template is accepted from an HTTP request. Markdown is
HTML-escaped before minimal formatting; a malicious document cannot inject JS.
"""
from __future__ import annotations
from html import escape, unescape
from urllib.parse import urlsplit
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
GUIDES = {
    'quickstart': ('快速入门', 'docs/QUICKSTART_V1.28.0.md'),
    'usage': ('完整操作手册', 'docs/USAGE.md'),
    'desktop': ('桌面运行与进程管理', 'docs/RUNTIME_AND_UPDATES_V1.28.0.md'),
    'zotero': ('Zotero 使用教程', 'docs/ZOTERO_QUICKSTART.md'),
    'sources': ('文献来源与权限', 'docs/SOURCES_AND_ACCESS.md'),
    'model': ('模型接口配置', 'docs/API_PROVIDERS.md'),
    'portal': ('公网工作台使用手册', 'docs/PORTAL_GUIDE_V1.21.0.md'),
}


def inline(text: str) -> str:
    # Text first: only our own known HTML tags may be added. Markdown links
    # cannot introduce script/data/file URLs or arbitrary local filesystem
    # paths. Existing help pages may link to other allowlisted manuals.
    value = escape(text)
    def link(match):
        label=match.group(1)
        url=unescape(match.group(2))
        if url in ('UPDATING.md','docs/UPDATING.md'):
            url='/guide/desktop'
        else:
            basename=url.replace('docs/','',1).removeprefix('./')
            by_file={Path(relative).name:slug for slug,(_,relative) in GUIDES.items()}
            if basename in by_file:
                url='/guide/'+by_file[basename]
        parsed=urlsplit(url)
        if url.startswith('/guide/') and url.removeprefix('/guide/') in GUIDES:
            return '<a href="'+escape(url,quote=True)+'">'+label+'</a>'
        if parsed.scheme=='https' and parsed.netloc and not parsed.username and not parsed.password:
            return '<a href="'+escape(url,quote=True)+'" target="_blank" rel="noopener noreferrer">'+label+'</a>'
        return label
    value = re.sub(r'\[([^\[\]]{1,160})\]\(([^()\s]{1,600})\)',link,value)
    value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
    value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
    return value


def markdown_html(text: str) -> str:
    parts: list[str] = []
    code: list[str] = []
    in_code = False
    list_kind = ''
    def close_list():
        nonlocal list_kind
        if list_kind:
            parts.append(f'</{list_kind}>')
            list_kind = ''
    for raw in text.replace('\r\n', '\n').split('\n'):
        line = raw.strip()
        if line.startswith('```'):
            close_list()
            if in_code:
                parts.append('<pre><code>' + '\n'.join(code) + '</code></pre>')
                code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(escape(raw))
            continue
        if not line:
            close_list()
            continue
        heading = re.match(r'^(#{1,5})\s+(.+)$', line)
        if heading:
            close_list()
            depth = len(heading.group(1))
            parts.append(f'<h{depth}>{inline(heading.group(2))}</h{depth}>')
            continue
        if line.startswith('!['):
            # Images in archived docs are not served through this public route.
            continue
        if re.fullmatch(r'-{3,}', line):
            close_list(); parts.append('<hr>'); continue
        item = re.match(r'^[-*]\s+(.+)', line)
        number = re.match(r'^\d+[.)]\s+(.+)', line)
        if item or number:
            kind = 'ul' if item else 'ol'
            if kind != list_kind:
                close_list();parts.append(f'<{kind}>');list_kind=kind
            parts.append('<li>' + inline((item or number).group(1)) + '</li>')
            continue
        close_list()
        parts.append('<p>' + inline(line) + '</p>')
    close_list()
    if code:
        parts.append('<pre><code>'+'\n'.join(code)+'</code></pre>')
    return '\n'.join(parts)


def render_guide(slug: str, *, public: bool = False) -> bytes | None:
    entry = GUIDES.get(slug)
    if entry is None:
        return None
    title, relative = entry
    path = ROOT / relative
    try:
        markdown = path.read_text(encoding='utf-8')
    except OSError:
        return None
    nav = ''.join(f'<a {"aria-current=\"page\"" if key == slug else ""} '
                  f'href="/guide/{key}">{escape(label)}</a>'
                  for key, (label, _) in GUIDES.items())
    back = '/'
    body = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Paper-Radar</title><link rel="stylesheet" href="/guide.css"></head>
<body><header class="guide-header"><a class="back-link" href="{back}">← 返回研究工作台</a>
<strong>Paper-Radar · 使用帮助</strong></header>
<div class="guide-shell"><aside class="guide-nav"><h2>帮助中心</h2>{nav}</aside>
<main class="guide-content"><div class="guide-eyebrow">使用教程 / {escape(title)}</div>
{markdown_html(markdown)}</main></div></body></html>'''
    return body.encode('utf-8')

# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Safe, dependency-free help centre rendering with allowlisted documents and figures.

User-supplied paths are never used for filesystem access. Text is escaped before
adding a small, known set of HTML elements. Only local allowlisted diagrams,
help pages, app routes and HTTPS external hyperlinks are permitted.
"""
from __future__ import annotations

from html import escape, unescape
from pathlib import Path
from urllib.parse import urlsplit
import re

ROOT = Path(__file__).resolve().parent.parent
# A single consolidated manual drives all contextual help pages.
_MANUAL = 'docs/USER_GUIDE.md'
GUIDES = {
    'quickstart': ('快速入门', _MANUAL),
    'usage': ('页面与操作', _MANUAL),
    'following': ('研究跟进', _MANUAL),
    'inquiry': ('研究助手', _MANUAL),
    'sources': ('文献来源', _MANUAL),
    'model': ('AI 模型', _MANUAL),
    'ai': ('AI 模型', _MANUAL),
    'zotero': ('Zotero', _MANUAL),
    'reading': ('阅读与证据', _MANUAL),
    'triage': ('阅读与证据', _MANUAL),
    'trace': ('阅读与证据', _MANUAL),
    'desktop': ('运行与数据', _MANUAL),
    'windows-recovery': ('运行与数据', _MANUAL),  # old bookmarks share the single help section
    'portal': ('公网工作台', _MANUAL),
}
_SECTIONS = {
    'ai':'model', 'triage':'reading', 'trace':'reading',
    'windows-recovery':'desktop',
}
_NAV_SLUGS = ('quickstart','usage','following','inquiry','sources','ai','zotero','reading','desktop','portal')
GUIDE_ASSETS = frozenset({
    'workflow.svg', 'ai-paths.svg', 'sources.svg', 'evidence.svg',
    'runtime.svg', 'navigation.svg', 'zotero.svg', 'portal.svg', 'trace.svg',
})


def _section(text: str, slug: str) -> str | None:
    """Extract a contextual section from a shared manual without dynamic paths."""
    sections = re.split(r'(?m)^<!-- GUIDE:([a-z-]+) -->\s*$', text)
    parts = {sections[i]: sections[i + 1].strip()
             for i in range(1, len(sections) - 1, 2)}
    return parts.get(_SECTIONS.get(slug, slug))

_LOCAL_PAGES = {'dashboard', 'library', 'tasks', 'following', 'inquiry', 'reports', 'settings', 'downloads', 'zotero'}
_PUBLIC_PAGE_MAP = {'dashboard': 'workspace', 'library': 'library', 'tasks': 'tasks',
                    'reports': 'reports', 'following':'following', 'inquiry':'inquiry', 'settings': 'settings',
                    'downloads': 'library', 'zotero': 'settings'}


def guide_asset(name: str) -> Path | None:
    """Return a whitelisted SVG, never an arbitrary path or uploaded image."""
    if name not in GUIDE_ASSETS:
        return None
    path = ROOT / 'docs' / 'assets' / name
    return path if path.is_file() else None


def _link(label: str, url: str, *, public: bool) -> str:
    # The label is already escaped when this is called.
    target = unescape(url)
    basename = target.removeprefix('./').removeprefix('docs/')
    if basename in ('UPDATING.md', 'RUNTIME_AND_UPDATES_V1.28.0.md'):
        target = '/guide/desktop'
    else:
        by_file = {Path(relative).name: slug for slug, (_, relative) in GUIDES.items() if slug in _NAV_SLUGS}
        if basename in by_file:
            target = '/guide/' + by_file[basename]
    if target.startswith('app:'):
        page = target[4:]
        if page in _LOCAL_PAGES:
            target = '/' + _PUBLIC_PAGE_MAP[page] if public else '/app/' + page
    if target.startswith('/guide/') and target.removeprefix('/guide/') in GUIDES:
        return f'<a href="{escape(target, quote=True)}">{label}</a>'
    if target in ({'/' + page for page in _PUBLIC_PAGE_MAP.values()} if public
                  else {'/app/' + page for page in _LOCAL_PAGES}):
        return f'<a href="{escape(target, quote=True)}">{label}</a>'
    parsed = urlsplit(target)
    if (parsed.scheme == 'https' and parsed.hostname and parsed.netloc and
            not parsed.username and not parsed.password):
        return (f'<a href="{escape(target, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{label}</a>')
    return label


def inline(text: str, *, public: bool = False) -> str:
    value = escape(text)
    value = re.sub(r'\[([^\[\]]{1,160})\]\(([^()\s]{1,600})\)',
                   lambda m: _link(m.group(1), m.group(2), public=public), value)
    value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
    value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
    return value


def _cells(line: str) -> list[str]:
    # Markdown permits optional outer pipes. Escaped pipes stay inside cells.
    value = line.strip().strip('|')
    return [part.replace('\\|', '|').strip() for part in re.split(r'(?<!\\)\|', value)]


def _divider(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells)


def markdown_html(text: str, *, public: bool = False) -> str:
    parts: list[str] = []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    in_code = False
    code: list[str] = []
    list_kind = ''

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            parts.append(f'</{list_kind}>')
            list_kind = ''

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if line.startswith('```'):
            close_list()
            if in_code:
                parts.append('<pre><code>' + '\n'.join(code) + '</code></pre>')
                code.clear()
            in_code = not in_code
            i += 1
            continue
        if in_code:
            code.append(escape(raw))
            i += 1
            continue
        if not line:
            close_list()
            i += 1
            continue
        # Parse tables as a block. The separator row is never rendered as text.
        if ('|' in line and i + 1 < len(lines) and
                (head := _cells(line)) and len(head) >= 2 and
                (align := _cells(lines[i+1])) and len(head) == len(align) and _divider(align)):
            close_list()
            parts.append('<div class="guide-table-wrap" role="region" tabindex="0" aria-label="内容表格，窄屏可横向滚动"><table><thead><tr>' +
                         ''.join('<th scope="col">' + inline(x, public=public) + '</th>' for x in head) +
                         '</tr></thead><tbody>')
            i += 2
            while i < len(lines) and '|' in lines[i]:
                row = _cells(lines[i])
                if not any(row):
                    break
                row = (row + [''] * len(head))[:len(head)]
                parts.append('<tr>' + ''.join('<td>' + inline(x, public=public) + '</td>' for x in row) + '</tr>')
                i += 1
            parts.append('</tbody></table></div><p class="guide-swipe-hint">左右滑动可查看完整表格</p>')
            continue
        image = re.fullmatch(r'!\[([^\]]{0,160})\]\((?:\./)?(?:docs/)?assets/([a-z0-9-]+\.svg)\)', line)
        if image:
            close_list()
            name = image.group(2)
            if guide_asset(name):
                alt = escape(image.group(1), quote=True)
                parts.append(f'<figure class="guide-figure"><img src="/guide/assets/{name}" '
                             f'alt="{alt}" loading="lazy"><figcaption>{escape(image.group(1))}</figcaption></figure><p class="guide-swipe-hint">左右滑动可查看完整流程图</p>')
            i += 1
            continue
        heading = re.match(r'^(#{1,5})\s+(.+)$', line)
        if heading:
            close_list()
            depth = len(heading.group(1))
            label = heading.group(2)
            anchor = re.match(r'^\{#([a-z0-9-]{1,40})\}\s+(.+)$', label)
            ident = f' id="{anchor.group(1)}"' if anchor else ''
            parts.append(f'<h{depth}{ident}>{inline(anchor.group(2) if anchor else label, public=public)}</h{depth}>')
            i += 1
            continue
        if re.fullmatch(r'-{3,}', line):
            close_list()
            parts.append('<hr>')
            i += 1
            continue
        item = re.match(r'^[-*]\s+(.+)', line)
        number = re.match(r'^\d+[.)]\s+(.+)', line)
        if item or number:
            kind = 'ul' if item else 'ol'
            if list_kind != kind:
                close_list()
                parts.append(f'<{kind}>')
                list_kind = kind
            parts.append('<li>' + inline((item or number).group(1), public=public) + '</li>')
            i += 1
            continue
        close_list()
        if line.startswith('> '):
            parts.append('<blockquote>' + inline(line[2:], public=public) + '</blockquote>')
        else:
            parts.append('<p>' + inline(line, public=public) + '</p>')
        i += 1
    close_list()
    if code:
        parts.append('<pre><code>' + '\n'.join(code) + '</code></pre>')
    return '\n'.join(parts)


def render_guide(slug: str, *, public: bool = False) -> bytes | None:
    entry = GUIDES.get(slug)
    if not entry:
        return None
    title, relative = entry
    try:
        markdown = (ROOT / relative).read_text(encoding='utf-8')
    except OSError:
        return None
    markdown = _section(markdown, slug)
    if markdown is None:
        return None
    nav_items = []
    for key, (label, _) in GUIDES.items():
        if key not in _NAV_SLUGS:
            continue
        current = ' aria-current="page"' if key == slug else ''
        nav_items.append(f'<a{current} href="/guide/{key}">{escape(label)}</a>')
    nav = ''.join(nav_items)
    back = '/workspace' if public else '/app/dashboard'
    body = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Paper-Radar</title><link rel="stylesheet" href="/guide.css"></head>
<body><header class="guide-header"><a class="back-link" href="{back}">← 返回研究工作台</a>
<strong>Paper-Radar · 使用帮助</strong></header>
<div class="guide-shell"><aside class="guide-nav"><h2>帮助中心</h2>{nav}</aside>
<details class="guide-mobile-nav"><summary>教程目录</summary><nav aria-label="教程目录">{nav}</nav></details>
<main class="guide-content"><div class="guide-eyebrow">使用教程 / {escape(title)}</div>
{markdown_html(markdown, public=public)}</main></div></body></html>'''
    return body.encode('utf-8')

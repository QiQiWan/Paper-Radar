# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Bounded, background-only GitHub Release checks.

No auto-installation, no personal settings or API keys are transmitted.
Public GitHub Release metadata is the single source of truth: a tag pushed
without a published Release cannot trigger a software update notification.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
from threading import Lock, Thread
from urllib import error, request
import json
import re

from . import __version__
from .core import APP_ROOT, read_config

REPO = 'QiQiWan/Paper-Radar'
API_URL = 'https://api.github.com/repos/' + REPO + '/releases/latest'
RELEASES_URL = 'https://github.com/' + REPO + '/releases'
STATE_FILE = APP_ROOT / 'data' / 'update_check.json'
# Only version-number releases (stable releases use the GitHub 'latest' endpoint).
_VERSION = re.compile(r'^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$', re.I)


def version_tuple(tag: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(str(tag or '').strip())
    return tuple(map(int, match.groups())) if match else None


def fetch_latest(opener=None) -> dict:
    """Return verified public release metadata. Network is only used here.

    Caller must invoke this from a worker thread, never HTTP request/GUI thread.
    """
    opener = opener or request.urlopen
    req = request.Request(API_URL, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'Paper-Radar/' + __version__ + ' (desktop update checker)',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    try:
        with opener(req, timeout=8) as response:
            raw = response.read(128 * 1024 + 1)
    except error.HTTPError as exc:
        if exc.code == 404:
            return {'state': 'unpublished', 'message': '仓库暂无可访问的正式 Release，或仓库目前无法公开访问。'}
        if exc.code in (403, 429):
            raise RuntimeError('GitHub API 暂时限流或无法访问，请稍后手动检查。') from None
        raise RuntimeError(f'GitHub Release 查询失败（HTTP {exc.code}）。') from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError('网络无法连接 GitHub，已保留上次成功的检查结果。') from None
    if len(raw) > 128 * 1024:
        raise RuntimeError('Release 信息超出大小限制。')
    try:
        info = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeError('GitHub 返回的更新信息无法解析。') from None
    if not isinstance(info, dict) or info.get('draft') or info.get('prerelease'):
        raise RuntimeError('GitHub Release 信息格式无效。')
    tag = str(info.get('tag_name') or '').strip()
    remote = version_tuple(tag)
    if remote is None:
        raise RuntimeError('最新 Release 的版本号不符合 vX.Y.Z 格式，已忽略。')
    release_url = str(info.get('html_url') or '')
    if not re.fullmatch(r'https://github\.com/QiQiWan/Paper-Radar/releases/tag/[A-Za-z0-9._-]+', release_url):
        raise RuntimeError('GitHub Release 链接未通过安全校验。')
    local = version_tuple(__version__)
    if local is None:
        raise RuntimeError('当前程序版本格式无效。')
    return {'state': 'available' if remote > local else 'up_to_date',
            'latest_version': '.'.join(map(str, remote)), 'tag_name': tag,
            'release_url': release_url, 'release_notes': str(info.get('body') or '')[:1400],
            'published_at': str(info.get('published_at') or '')[:40],
            'message': '发现可用的新版本。' if remote > local else '当前已是最新公开正式版本。'}


class UpdateChecker:
    """Thread-safe persisted cooldown; web handlers only read cached values."""
    def __init__(self, path: Path = STATE_FILE, fetcher=None, now=None):
        self.path = Path(path)
        self.fetcher = fetcher or fetch_latest
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = Lock()
        self._checking = False
        self._snapshot = self._load()

    def _load(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _persist(self, data: dict):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + '.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.path)
        except OSError:
            # A read-only installation still serves the result in memory.
            pass

    def status(self, config: dict | None = None) -> dict:
        cfg = config or read_config()
        with self._lock:
            copy = dict(self._snapshot)
            busy = self._checking
        copy.setdefault('state', 'never_checked')
        # An installed update can preserve the old state file. Re-evaluate the
        # verified remote version against THIS executable on every read;
        # otherwise a newly updated installation can show a stale alert.
        if copy.get('state') in ('available', 'up_to_date'):
            remote_version = version_tuple(copy.get('latest_version', ''))
            current_version = version_tuple(__version__)
            if remote_version and current_version:
                copy['state'] = 'available' if remote_version > current_version else 'up_to_date'
        # Treat the local on-disk status file as untrusted: avoid exposing a
        # non-official link if a stale/corrupt cache has been edited.
        link = str(copy.get('release_url') or '')
        if link and not re.fullmatch(r'https://github\.com/QiQiWan/Paper-Radar/releases/tag/[A-Za-z0-9._-]+', link):
            copy.pop('release_url', None)
        from .startup_update import read_json
        root_status = read_json(APP_ROOT / '.paper_radar_updates' / 'status.json')
        # Never expose arbitrary local status fields to the browser.
        copy['startup_update'] = {key:root_status.get(key) for key in
            ('state','message','at','installed_version','previous_version','attempted_version')
            if key in root_status}
        copy.update(current_version=__version__, enabled=bool(cfg.get('update_check_enabled', True)),
                    checking=busy, releases_url=RELEASES_URL)
        copy['dismissed'] = (copy.get('tag_name') and
                             copy.get('tag_name') == cfg.get('dismissed_update_tag')) or False
        if not copy['enabled']:
            copy['state'] = 'disabled'
        return copy

    def schedule(self, config: dict | None = None, *, force: bool = False) -> bool:
        # A frozen application is updated by its signed native installer, never
        # by the source ZIP updater (which cannot safely replace .exe/.app).
        if getattr(sys, 'frozen', False):
            return False
        cfg = config or read_config()
        if not cfg.get('update_check_enabled', True) and not force:
            return False
        interval = max(6, min(int(cfg.get('update_check_interval_hours', 24)), 168))
        with self._lock:
            if self._checking:
                return False
            if not force:
                checked = self._snapshot.get('checked_at')
                if checked:
                    try:
                        when = datetime.fromisoformat(checked)
                        if when.tzinfo and self.now() - when < timedelta(hours=interval):
                            return False
                    except (TypeError, ValueError):
                        pass
            self._checking = True
        Thread(target=self._worker, daemon=True).start()
        return True

    def _worker(self):
        try:
            result = self.fetcher()
            if not isinstance(result, dict) or result.get('state') not in {'available', 'up_to_date', 'unpublished'}:
                raise RuntimeError('更新来源返回了不受支持的响应。')
            snapshot = dict(result)
        except Exception as exc:
            with self._lock:
                snapshot = dict(self._snapshot)
            # Errors must never replace a previously verified available release.
            snapshot['message'] = str(exc)[:180]
            if snapshot.get('state') not in {'available', 'up_to_date'}:
                snapshot['state'] = 'error'
        snapshot['checked_at'] = self.now().astimezone(timezone.utc).isoformat(timespec='seconds')
        self._persist(snapshot)
        with self._lock:
            self._snapshot = snapshot
            self._checking = False


checker = UpdateChecker()

# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Safe, stdlib-only, pre-launch GitHub Release updater.

The updater runs *before* optional application dependencies are installed.
It never executes downloaded binaries/scripts during validation; it verifies
GitHub release metadata, archive SHA-256, exact project version, ZIP members,
Python syntax and a boot protocol first. It only switches managed code files.
User settings, databases, PDFs, reports, logs and virtual environments are
strictly outside the managed set.

A write-ahead journal makes incomplete file moves recoverable on next launch.
Successful updates keep a bounded rollback snapshot. The application is then
started by the stable bootstrap supervisor (startup.py), which rolls back if
its dependency check or startup health check fails.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import shutil
import socket
import stat
import tempfile
import time
from urllib import error, parse, request
import zipfile

from . import __version__

REPO = 'QiQiWan/Paper-Radar'
API_URL = 'https://api.github.com/repos/' + REPO + '/releases/latest'
MAX_ARCHIVE = 80 * 1024 * 1024
MAX_UNPACKED = 180 * 1024 * 1024
MAX_DB_SNAPSHOT = 512 * 1024 * 1024
MAX_MEMBERS = 3500
NETWORK_TIMEOUT = 3.5
BOOT_PROTOCOL = 1
_VERSION = re.compile(r'^[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
HASH = re.compile(r'^sha256:([0-9a-fA-F]{64})$')
# Never replace a running Windows batch file or POSIX shell file. These stable
# entrypoints only create a venv and dispatch to startup.py.
MANAGED_ITEMS = (
    'paper_radar', 'main.py', 'startup.py', 'requirements.txt',
    'deploy', 'start_public_windows.bat', 'start_public_macos_linux.sh',
    'start_public_worker_windows.bat', 'start_public_worker_macos_linux.sh',
    'pyproject.toml', 'CHECK_RETRIEVAL_WINDOWS.bat', 'docs', 'tests', 'tools', '.github', 'benchmarks',
    'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
    'NOTICE.md', 'LICENSE', '.editorconfig', '.env.example',
    '.gitattributes', '.gitignore',
)
PROTECTED_NAMES = {'.venv', 'venv', 'env', 'settings.json', '.env',
                   'data', 'logs', 'downloads', 'reports', 'storage', '.git',
                   'start_windows.bat', 'start_macos_linux.sh', '.paper_radar_updates', '.paper_radar_recovery', '.paper_radar_home'}
ALLOWED_HOSTS = {'github.com', 'release-assets.githubusercontent.com',
                 'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}


def parsed_version(value: str):
    match = _VERSION.fullmatch(str(value or '').strip())
    return tuple(map(int, match.groups())) if match else None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_https_host(url: str, hosts: set[str]) -> bool:
    try:
        parsed = parse.urlsplit(url)
        return parsed.scheme == 'https' and parsed.hostname in hosts and not parsed.username and not parsed.password
    except ValueError:
        return False


def _response_bytes(url: str, max_bytes: int, timeout: float, opener):
    req = request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'Paper-Radar/' + __version__ + ' (startup updater)',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    with opener(req, timeout=timeout) as response:
        final = getattr(response, 'url', url) or url
        if not _is_https_host(str(final), {'api.github.com'}):
            raise UpdateError('Release 元数据请求重定向到不受信任的地址。')
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise UpdateError('Release 元数据超出限制。')
        return raw


class UpdateError(Exception):
    """Expected download/validation failure; continue with installed build."""


class RecoveryError(UpdateError):
    """Existing files may be inconsistent: fail closed; require manual repair."""


def release_asset(info: dict, current: str = __version__) -> dict | None:
    if not isinstance(info, dict) or info.get('draft') or info.get('prerelease'):
        raise UpdateError('Release 不是正式发布版本。')
    tag = str(info.get('tag_name') or '')
    remote, local = parsed_version(tag), parsed_version(current)
    if remote is None or local is None:
        raise UpdateError('发布版本号不是 X.Y.Z 格式。')
    if remote <= local:
        return None
    version = '.'.join(map(str, remote))
    page = 'https://github.com/' + REPO + '/releases/tag/' + tag
    if str(info.get('html_url') or '') != page:
        raise UpdateError('GitHub Release 地址与固定项目不匹配。')
    expected = f'Paper-Radar_v{version}_Source.zip'
    for asset in info.get('assets') or []:
        if not isinstance(asset, dict) or asset.get('name') != expected or asset.get('state') != 'uploaded':
            continue
        asset_url = str(asset.get('browser_download_url') or '')
        required = f'https://github.com/{REPO}/releases/download/{tag}/{expected}'
        if asset_url != required:
            raise UpdateError('Release 附件地址未通过固定项目校验。')
        match = HASH.fullmatch(str(asset.get('digest') or ''))
        if match is None:
            raise UpdateError('发布附件缺少 GitHub SHA-256 摘要，已拒绝自动安装。')
        size = asset.get('size')
        if type(size) is not int or not 1024 <= size <= MAX_ARCHIVE:
            raise UpdateError('发布附件大小无效或超出限制。')
        base = f'https://github.com/{REPO}/releases/download/{tag}/'
        ancillary = {}
        for suffix,label in (('release.json','manifest'),('release.sig','signature')):
            filename=f'Paper-Radar_v{version}_{suffix}'
            matches=[entry for entry in info.get('assets') or []
                     if isinstance(entry,dict) and entry.get('name')==filename
                     and entry.get('state')=='uploaded']
            if matches:
                exact=base+filename
                if len(matches)!=1 or matches[0].get('browser_download_url')!=exact:
                    raise UpdateError('Release 签名附件地址无效。')
                ancillary[label+'_url']=exact
        if local >= (1, 16, 0) and ('manifest_url' not in ancillary or 'signature_url' not in ancillary):
            # The public Release is valid for manual installation but not for
            # trusted source auto-upgrade; keep running the installed version.
            return None
        return {'version':version, 'tag':tag, 'url':asset_url,
                'sha256':match.group(1).lower(), 'size':size,
                'release_url':page, **ancillary}
    raise UpdateError('最新正式 Release 未附带约定名称的源码 ZIP；启动本地版本。')


def _safe_member(name: str, root_name: str) -> str:
    if '\\' in name or '\x00' in name or name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise UpdateError('更新 ZIP 包含不安全路径。')
    raw_parts = name.rstrip('/').split('/')
    parts = PurePosixPath(name).parts
    if not parts or any(p in ('..', '.', '') for p in raw_parts):
        raise UpdateError('更新 ZIP 包含相对路径穿越。')
    if len(parts) == 1 and name == root_name + '/':
        return ''
    if parts[0] != root_name or len(parts) < 2:
        raise UpdateError('更新 ZIP 须统一放在 Paper-Radar/ 根目录。')
    relative = '/'.join(parts[1:])
    if ':' in relative or len(relative) > 300:
        raise UpdateError('更新 ZIP 存在异常文件路径。')
    base = parts[1]
    # Stable launchers ship in the source release but must not be replaced
    # while their shell process is executing.
    if base in {'start_windows.bat', 'start_macos_linux.sh'}:
        return '_skip/' + relative
    if base in PROTECTED_NAMES:
        raise UpdateError('更新 ZIP 尝试写入用户数据或受保护的启动文件。')
    if base not in MANAGED_ITEMS:
        raise UpdateError('更新 ZIP 包含不在程序白名单内的根目录文件。')
    if any(p in ('..', '.', '__pycache__') for p in parts[1:]):
        raise UpdateError('ZIP 含不支持的目录。')
    return relative


def unpack_verified(archive: Path, stage: Path, version: str):
    """Validate all members before any extraction; refuse links and zip bombs."""
    stage.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            items = zf.infolist()
            if len(items) > MAX_MEMBERS:
                raise UpdateError('ZIP 文件条目数量异常。')
            known = set()
            total = 0
            writes = []
            for info in items:
                rel = _safe_member(info.filename, 'Paper-Radar')
                if not rel or rel.startswith('_skip/'):
                    continue
                key = rel.casefold().rstrip('/')
                if key in known:
                    raise UpdateError('ZIP 包含重复或大小写冲突的文件名。')
                known.add(key)
                mode = (info.external_attr >> 16) & 0xffff
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise UpdateError('ZIP 中存在不允许的符号链接或设备文件。')
                total += info.file_size
                if total > MAX_UNPACKED or info.file_size > MAX_UNPACKED:
                    raise UpdateError('ZIP 解压体积超出安全限制。')
                if info.compress_size and info.file_size > 1000 * info.compress_size and info.file_size > (1<<20):
                    raise UpdateError('ZIP 压缩率异常。')
                writes.append((info, rel))
            for required in ('main.py', 'startup.py', 'pyproject.toml',
                             'requirements.txt', 'paper_radar/__init__.py',
                             'paper_radar/startup_update.py'):
                if required not in known:
                    raise UpdateError(f'发布 ZIP 缺少启动必需文件 {required}。')
            for info, rel in writes:
                target = stage.joinpath(*PurePosixPath(rel).parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest, length=512 * 1024)
    except (zipfile.BadZipFile, EOFError, OSError) as exc:
        raise UpdateError('更新 ZIP 损坏或解压失败。') from exc
    source = (stage / 'paper_radar' / '__init__.py').read_text(encoding='utf-8')
    match = re.search(r'^__version__\s*=\s*[\'\"]([^\'\"]+)[\'\"]', source, re.M)
    if match is None or match.group(1) != version:
        raise UpdateError('ZIP 内部软件版本与 Release 标签不一致。')
    if not re.search(r'^__startup_protocol__\s*=\s*1\s*$', source, re.M):
        raise UpdateError('更新包未声明兼容的启动协议；需人工升级。')
    project = (stage / 'pyproject.toml').read_text(encoding='utf-8')
    if not re.search(r'^version\s*=\s*[\'\"]' + re.escape(version) + r'[\'\"]\s*$', project, re.M):
        raise UpdateError('项目元数据版本号与 Release 不一致。')
    for py in stage.rglob('*.py'):
        try:
            ast.parse(py.read_text(encoding='utf-8'), filename=str(py))
        except (SyntaxError, UnicodeError) as exc:
            raise UpdateError('ZIP 中包含无法解析的 Python 源码。') from exc


class StartupUpdater:
    def __init__(self, root: Path, *, opener=None, timeout: float = NETWORK_TIMEOUT,
                 repo: str = REPO):
        self.root = Path(root).resolve()
        self.opener = opener or request.urlopen
        self.timeout = timeout
        # Keep staging/backups on the installation filesystem even when users
        # have moved data/ to another disk. Never include this in source ZIPs.
        self.base = self.root / '.paper_radar_updates'
        self.status_path = self.base / 'status.json'
        self.journal_path = self.base / 'active.json'
        self.lock_path = self.base / 'updater.lock'
        self.failed_path = self.base / 'failed_release.json'
        self.repo = repo  # fixed for tests/docs; never used to construct URLs

    def status(self, state: str, message: str, **kwargs):
        value = {'state':state, 'message':str(message)[:240], 'at':now_iso(),
                 'installed_version':self.local_version(), **kwargs}
        try:
            atomic_json(self.status_path, value)
        except OSError:
            # Read-only installations must still launch their existing version.
            pass
        return value

    def public_service_running(self) -> bool:
        """Refuse hot replacement while a public server may use this source tree.

        v1.22.1 moved its runtime marker into the writable portal-data root to
        permit read-only systemd installations. Retain the old marker guard,
        check standard/configured data roots, and detect Linux services whose
        data root was configured in a *different* environment. A stale marker
        conservatively blocks updates until an operator clears it.
        """
        if (self.base/'public_service.active').exists():
            return True
        paths = [self.root/'storage'/'portal', self.root/'data'/'portal']
        configured = os.environ.get('PAPERRADAR_PORTAL_DATA','').strip()
        if configured:
            paths.insert(0,Path(configured).expanduser())
        for data_root in paths:
            if (data_root/'runtime'/'public_service.active').exists():
                return True
        # systemd may set PAPERRADAR_PORTAL_DATA for the service only, not for
        # the local desktop updater. Checking /proc protects Linux installations
        # sharing a code tree across user accounts without relying on a source
        # directory writable by the public service.
        proc=Path('/proc')
        if os.name=='posix' and proc.is_dir():
            try:
                processes=proc.iterdir()
                for process in processes:
                    if not process.name.isdecimal():
                        continue
                    try:
                        argv=(process/'cmdline').read_bytes().split(b'\0')
                        if b'paper_radar.public_portal' not in argv:
                            continue
                        cwd=(process/'cwd').resolve(strict=True)
                        if cwd==self.root or self.root in cwd.parents:
                            return True
                    except (OSError,RuntimeError):
                        # /proc entries are transient and may be inaccessible.
                        continue
            except OSError:
                pass
        return False

    def local_version(self):
        try:
            text = (self.root / 'paper_radar' / '__init__.py').read_text(encoding='utf-8')
            match = re.search(r'^__version__\s*=\s*[\'\"]([^\'\"]+)[\'\"]', text, re.M)
            return match.group(1) if match else __version__
        except OSError:
            return __version__

    @contextmanager
    def lock(self):
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != 'nt':
            os.chmod(self.base,0o700)
        acquired = False
        try:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'w') as fp:
                    fp.write(str(os.getpid()))
                acquired = True
            except FileExistsError:
                try:
                    if time.time() - self.lock_path.stat().st_mtime > 600:
                        self.lock_path.unlink(missing_ok=True)
                        fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        os.close(fd)
                        acquired = True
                except (OSError, FileExistsError):
                    pass
                if not acquired:
                    raise UpdateError('已有更新检查在运行，已跳过本次启动更新。')
            yield
        finally:
            if acquired:
                self.lock_path.unlink(missing_ok=True)

    def is_web_running(self) -> bool:
        # Do not overwrite code under a live HTTP server. Check application ID,
        # never rely on an unrelated process merely occupying a local port.
        from urllib.request import urlopen
        for port in range(8765, 8777):
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=.22) as response:
                    if json.loads(response.read(1024)).get('app') == 'paper-radar':
                        return True
            except (OSError, TimeoutError, ValueError, error.URLError):
                pass
        return False

    def recover_if_interrupted(self) -> bool:
        """Roll back only files listed in the interrupted transaction journal."""
        journal = read_json(self.journal_path)
        if not journal:
            return False
        if journal.get('phase') == 'committed':
            # Power loss after committed code swap but before recording startup
            # validation must still preserve the rollback snapshot.
            old_status = read_json(self.status_path)
            if old_status.get('state') != 'updated' or old_status.get('transaction_id') != journal.get('id'):
                self.status('updated', '新版本已安装，正在补做启动验证。',
                    transaction_id=journal.get('id'),
                    previous_version=journal.get('previous_version'),
                    release_url=journal.get('release_url'))
            self.journal_path.unlink(missing_ok=True)
            return False
        transaction = str(journal.get('id') or '')
        if not re.fullmatch(r'\d{8}T\d{6}-[0-9a-f]{8}', transaction):
            raise RecoveryError('更新事务文件损坏，需要手动检查 .paper_radar_updates。')
        stage = self.base / 'staging' / transaction / 'source'
        backup = self.base / 'backups' / transaction / 'previous'
        entries = journal.get('entries') or []
        if not isinstance(entries,list) or any(item not in MANAGED_ITEMS for item in entries):
            raise RecoveryError('更新事务记录中的文件不在白名单内。')
        try:
            for name in reversed(entries):
                target, old, staged = self.root / name, backup / name, stage / name
                if old.exists():
                    self._remove(target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old, target)
                elif name in journal.get('new_items', []) and (journal.get('phase') == 'rolling_back' or not staged.exists()):
                    self._remove(target)
        except OSError as exc:
            raise RecoveryError('程序回滚未能完成，请从备份恢复后再启动。') from exc
        if journal.get('phase') == 'rolling_back' and journal.get('restore_user_data'):
            self._restore_user_state(transaction)
        self.journal_path.unlink(missing_ok=True)
        self.status('rolled_back', '检测到上次更新中断，已恢复之前的程序文件。',
                    attempted_version=journal.get('version'))
        return True

    @staticmethod
    def _remove(p: Path):
        if p.is_symlink():
            raise UpdateError('检测到符号链接，已停止自动文件操作。')
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    def check_release(self):
        try:
            raw = _response_bytes(API_URL, 128*1024, self.timeout, self.opener)
        except error.HTTPError as exc:
            if exc.code == 404:
                return {'unpublished':True}
            raise UpdateError(f'GitHub Release 查询失败（HTTP {exc.code}）。') from None
        except (OSError, TimeoutError, error.URLError) as exc:
            raise UpdateError('当前网络无法访问 GitHub，已使用本地版本。') from exc
        try:
            info = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise UpdateError('Release 元数据不是有效 JSON。') from exc
        return release_asset(info, self.local_version())

    def _download(self, asset: dict, target: Path):
        req = request.Request(asset['url'],headers={'User-Agent': 'Paper-Radar/'+__version__})
        hasher = hashlib.sha256()
        total = 0
        try:
            with self.opener(req, timeout=max(12, self.timeout)) as response, target.open('wb') as out:
                final = str(getattr(response,'url',asset['url']) or asset['url'])
                if not _is_https_host(final, ALLOWED_HOSTS):
                    raise UpdateError('ZIP 下载跳转到了未经允许的域名。')
                while True:
                    chunk = response.read(512*1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE or total > asset['size']:
                        raise UpdateError('实际下载体积超过发布记录。')
                    hasher.update(chunk)
                    out.write(chunk)
        except (OSError, TimeoutError, error.URLError) as exc:
            raise UpdateError('更新包下载失败，已使用本地版本。') from exc
        if total != asset['size'] or hasher.hexdigest() != asset['sha256']:
            raise UpdateError('更新 ZIP 与 GitHub 提供的 SHA-256/大小不一致，拒绝安装。')

    def _check_signature(self, asset: dict) -> None:
        """Installed 1.16+ clients require an independent signed manifest."""
        trust=self.root/'paper_radar'/'release_public_keys.json'
        current=parsed_version(self.local_version()) or (0,0,0)
        if not trust.exists() and current<(1,16,0):
            # Historical 1.15 test installations have no pinned key. Clients
            # installing 1.16 MUST use a manual bootstrap; see release guide.
            return
        if not trust.is_file():
            raise UpdateError('本地发布公钥文件缺失，已拒绝自动安装。')
        if not asset.get('manifest_url') or not asset.get('signature_url'):
            raise UpdateError('Release 缺少独立签名文件，已拒绝自动安装。')
        def ancillary(url: str, limit: int) -> bytes:
            if not _is_https_host(url, {'github.com'}):
                raise UpdateError('签名附件地址不可信。')
            req=request.Request(url,headers={'User-Agent':'Paper-Radar/'+__version__})
            try:
                with self.opener(req,timeout=max(12,self.timeout)) as response:
                    final=str(getattr(response,'url',url) or url)
                    if not _is_https_host(final,ALLOWED_HOSTS):
                        raise UpdateError('签名文件跳转到未授权域名。')
                    raw=response.read(limit+1)
                    if len(raw)>limit:raise UpdateError('签名附件超出大小限制。')
                    return raw
            except (OSError,TimeoutError,error.URLError) as exc:
                raise UpdateError('签名元数据下载失败，已拒绝自动安装。') from exc
        manifest=ancillary(asset['manifest_url'],12000)
        signature=ancillary(asset['signature_url'],1000)
        from .release_signatures import verify_manifest, SignatureError
        try:
            verify_manifest(manifest,signature,trust,version=asset['version'],
                sha256=asset['sha256'],archive_size=asset['size'],
                name=f'Paper-Radar_v{asset["version"]}_Source.zip')
        except SignatureError as exc:
            raise UpdateError('独立发布签名校验失败：'+str(exc)) from exc

    def _snapshot_user_state(self, backup_root: Path):
        """Copy settings and online SQLite backups before allowing new code to run.

        PDF/report directories are never copied or replaced by code updates.
        Large DBs require manual upgrading, because unbounded backup can fill
        the disk and invalidate our rollback guarantee.
        """
        state = backup_root/'user_state'
        data_dir = self.root/'data'
        databases = sorted(p for p in data_dir.glob('*.sqlite3') if p.is_file()) if data_dir.is_dir() else []
        if any(p.is_symlink() for p in databases):
            raise UpdateError('数据文件含符号链接，需要人工备份并升级。')
        size = sum(p.stat().st_size for p in databases)
        if size > MAX_DB_SNAPSHOT:
            raise UpdateError('用户数据库较大，无法保证自动备份；请手动备份升级。')
        free = shutil.disk_usage(self.root).free
        if free < max(64*1024*1024, int(size*1.3)+16*1024*1024):
            raise UpdateError('磁盘剩余空间不足以安全备份数据库并更新。')
        state.mkdir(parents=True, exist_ok=True)
        names = []
        config = self.root/'settings.json'
        if config.is_file():
            if config.is_symlink():
                raise UpdateError('settings.json 是符号链接，需要人工升级。')
            shutil.copy2(config,state/'settings.json')
            names.append('settings.json')
        for source in databases:
            target = state/'data'/source.name
            target.parent.mkdir(parents=True,exist_ok=True)
            try:
                with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=3) as old_db, \
                     sqlite3.connect(str(target),timeout=3) as new_db:
                    old_db.backup(new_db)
            except (sqlite3.DatabaseError,OSError) as exc:
                raise UpdateError(f'无法备份数据库 {source.name}，已取消自动更新。') from exc
            names.append('data/'+source.name)
        atomic_json(state/'manifest.json',{'files':names,'at':now_iso()})

    def _restore_user_state(self, tx: str):
        """Restore pre-launch snapshots only when newly installed code failed
        before the workbench became usable. Never restore during normal update.
        """
        source = self.base/'backups'/tx/'user_state'
        manifest = read_json(source/'manifest.json')
        if not manifest:
            return
        names = manifest.get('files',[])
        if not isinstance(names,list) or any(name!='settings.json' and not re.fullmatch(r'data/[A-Za-z0-9_.-]+\.sqlite3',name) for name in names):
            raise RecoveryError('用户数据库备份清单损坏；已停止自动恢复。')
        if self.is_web_running():
            raise RecoveryError('本地工作台仍在运行，无法安全恢复旧数据库。')
        for name in names:
            original = source/name
            dest = self.root/name
            if not original.is_file() or dest.is_symlink():
                raise RecoveryError('备份缺失或用户数据目标是符号链接，无法恢复。')
            dest.parent.mkdir(parents=True,exist_ok=True)
            tmp = dest.with_name(dest.name+'.update_restore.tmp')
            shutil.copy2(original,tmp)
            if name.startswith('data/'):
                for suffix in ('-wal','-shm'):
                    Path(str(dest)+suffix).unlink(missing_ok=True)
            os.replace(tmp,dest)

    def _install(self, asset: dict) -> dict:
        # Stage is in data/ on the same partition as managed code, allowing
        # single-entry os.replace and journal-assisted rollback.
        transaction = time.strftime('%Y%m%dT%H%M%S')+'-'+os.urandom(4).hex()
        staging = self.base / 'staging' / transaction
        source = staging / 'source'
        backup = self.base / 'backups' / transaction / 'previous'
        staging.mkdir(parents=True, exist_ok=False)
        try:
            self._download(asset, staging/'release.zip')
            self._check_signature(asset)
            unpack_verified(staging/'release.zip', source, asset['version'])
            entries = [name for name in MANAGED_ITEMS if (source/name).exists()]
            # User database/settings snapshot must succeed before any code move.
            self._snapshot_user_state(self.base/'backups'/transaction)
            for name in entries:
                old = self.root / name
                if old.is_symlink():
                    raise UpdateError(f'本地 {name} 为符号链接，需要手动更新。')
            journal = {'id':transaction, 'version':asset['version'],
                       'previous_version':self.local_version(),
                       'release_url':asset['release_url'],
                       'entries':entries,
                       'new_items':[name for name in entries if not (self.root/name).exists()],
                       'phase':'installing', 'at':now_iso()}
            atomic_json(self.journal_path,journal)
            backup.mkdir(parents=True,exist_ok=True)
            for name in entries:
                old = self.root / name
                moved = source / name
                if old.exists():
                    (backup/name).parent.mkdir(parents=True,exist_ok=True)
                    os.replace(old, backup/name)
                os.replace(moved, old)
            journal['phase']='committed'
            atomic_json(self.journal_path,journal)
            status = self.status('updated',f'已更新到 {asset["version"]}，正在启动新版本。',
                                 previous_version=journal['previous_version'],
                                 release_url=asset['release_url'],
                                 transaction_id=transaction)
            self.journal_path.unlink(missing_ok=True)
            return status
        except Exception:
            if self.journal_path.exists():
                try:
                    self.recover_if_interrupted()
                except Exception as exc:
                    raise RecoveryError('程序更新中断且自动回滚失败，请从备份恢复。') from exc
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def mark_failed_and_rollback(self, *, failed_version: str, reason: str,
                                 restore_user_data: bool = True):
        """Restore the immediately previous program snapshot after launch failure."""
        data = read_json(self.status_path)
        tx = data.get('transaction_id') if data.get('state') in ('updated','success') else None
        if not tx or not re.fullmatch(r'\d{8}T\d{6}-[0-9a-f]{8}', tx):
            return False
        if data.get('installed_version') != failed_version:
            return False
        backup = self.base / 'backups' / tx / 'previous'
        if self.is_web_running():
            return False
        if not backup.is_dir():
            return False
        # Write the recovery journal before removing any installed files.
        restored_items = [name for name in MANAGED_ITEMS if (backup/name).exists()]
        added = [name for name in MANAGED_ITEMS if name not in restored_items and (self.root/name).exists()]
        journal = {'id':tx, 'version':failed_version, 'entries':restored_items+added,
                   'new_items':added, 'phase':'rolling_back',
                   'restore_user_data':bool(restore_user_data)}
        atomic_json(self.journal_path,journal)
        for name in reversed(restored_items):
            self._remove(self.root/name)
            os.replace(backup/name, self.root/name)
        for name in added:
            self._remove(self.root/name)
        if restore_user_data:
            self._restore_user_state(tx)
        atomic_json(self.failed_path, {'version':failed_version,'reason':reason[:200],'at':now_iso()})
        self.journal_path.unlink(missing_ok=True)
        self.status('rolled_back', '升级后的启动检查失败，已恢复此前版本。',
                    attempted_version=failed_version, error=reason[:200])
        return True

    def _prune_backups(self, keep: int = 2):
        location = self.base/'backups'
        if not location.is_dir():
            return
        backups = sorted((p for p in location.iterdir() if p.is_dir()), reverse=True)
        for entry in backups[keep:]:
            shutil.rmtree(entry,ignore_errors=True)

    def run(self, *, enabled: bool = True, force: bool = False) -> dict:
        try:
            with self.lock():
                self.recover_if_interrupted()
                # A power loss may happen after code was committed but before
                # startup.py confirmed new dependencies and Web health. Retain
                # rollback provenance across restarts and revalidate first.
                previous = read_json(self.status_path)
                if (previous.get('state') == 'updated' and
                    previous.get('installed_version') == self.local_version()):
                    tx = str(previous.get('transaction_id') or '')
                    if not re.fullmatch(r'\d{8}T\d{6}-[0-9a-f]{8}', tx) or not (self.base/'backups'/tx/'previous').is_dir():
                        return self.status('recovery_failed', '新版启动验证尚未完成，且旧版回滚快照缺失，已暂停启动。')
                    return self.status('updated', '上次更新尚未完成启动验证，正在继续启动新版本。',
                         transaction_id=tx, previous_version=previous.get('previous_version'),
                         release_url=previous.get('release_url'))
                if not enabled:
                    return self.status('disabled', '已在配置中关闭启动自动更新。')
                if self.public_service_running():
                    return self.status('running', '公网服务仍在运行或锁未清理，已禁止覆盖程序文件。')
                if (self.root/'data'/'running.lock').exists():
                    return self.status('running', '检测到后台检索任务正在运行，跳过本次更新。')
                if self.is_web_running():
                    return self.status('running', '检测到工作台正在运行，跳过热更新并打开已有页面。')
                asset = self.check_release()
                if asset is not None and asset.get('unpublished'):
                    return self.status('unpublished', '仓库尚无公开正式 Release，启动本地程序。')
                if asset is None:
                    return self.status('up_to_date', '无更高的公开正式版本，启动本地程序。')
                failed = read_json(self.failed_path)
                if failed.get('version') == asset['version'] and not force:
                    return self.status('skipped_failed', '该版本上次升级失败，暂不重复尝试，启动本地版本。',
                                       attempted_version=asset['version'])
                result = self._install(asset)
                self._prune_backups()
                return result
        except RecoveryError as exc:
            return self.status('recovery_failed', str(exc), error_type=type(exc).__name__)
        except (UpdateError, OSError, ValueError, zipfile.BadZipFile) as exc:
            return self.status('error', str(exc), error_type=type(exc).__name__)


def read_startup_settings(root: Path) -> dict:
    """Read only update-specific booleans, no importing the application config."""
    from .storage_layout import resolve_data_root
    data_root = resolve_data_root(Path(root), create=False)
    primary = data_root/'settings.json'
    source = read_json(primary)
    # When config is missing but a backup remains, treat this as potential
    # OneDrive synchronization rather than a newly installed workspace.
    degraded = not source and os.path.lexists(os.fspath(primary))
    missing_with_backup = not os.path.lexists(os.fspath(primary)) and (data_root/'settings.json.bak').exists()
    if degraded or missing_with_backup:
        # Preserve the user's foreground/background preference but NEVER use
        # an unverified fallback to auto-install code or contact update hosts.
        source = read_json(data_root/'settings.json.bak')
    return {'update_check_enabled':not (degraded or missing_with_backup) and source.get('update_check_enabled',True) is not False,
            'auto_update_on_startup':not (degraded or missing_with_backup) and source.get('auto_update_on_startup',False) is True,
            'service_run_mode':source.get('service_run_mode') if source.get('service_run_mode') in ('foreground','background') else 'foreground'}


def cli(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='Paper-Radar 启动更新与恢复（无需外部依赖）')
    parser.add_argument('--check-only',action='store_true',help='只查看版本，不安装')
    parser.add_argument('--no-update',action='store_true',help='跳过本次联网检查')
    parser.add_argument('--force',action='store_true',help='忽略上次失败版本的抑制记录')
    parser.add_argument('--status',action='store_true',help='查看上次启动更新记录')
    parser.add_argument('--rollback-last',action='store_true',help='手动恢复上个程序版本（保留当前用户资料）')
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parent.parent
    u=StartupUpdater(root)
    if args.status:
        print(json.dumps(read_json(u.status_path),ensure_ascii=False,indent=2))
        return 0
    if args.rollback_last:
        if u.is_web_running():
            print('请先关闭本地工作台，再执行手动回滚。')
            return 2
        try:
            with u.lock():
                status = read_json(u.status_path)
                ok = u.mark_failed_and_rollback(failed_version=u.local_version(),
                         reason='用户手动回滚', restore_user_data=False)
            print('已恢复旧程序，当前数据库和文献未回退。' if ok else '没有可恢复的旧版本备份。')
            return 0 if ok else 2
        except (OSError, UpdateError) as exc:
            print('手动回滚失败：'+str(exc))
            return 2
    settings=read_startup_settings(root)
    enabled=not args.no_update and settings['update_check_enabled'] and settings['auto_update_on_startup']
    if args.check_only:
        try:
            asset=u.check_release() if enabled else None
            print(json.dumps({'state':'available' if asset else 'none', 'release':asset},ensure_ascii=False,indent=2))
        except UpdateError as exc:
            print(f'更新检查：{exc}')
        return 0
    outcome=u.run(enabled=enabled,force=args.force)
    print(outcome['message'])
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())

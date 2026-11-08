# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Safe settings.json diagnosis and explicit recovery, useful for OneDrive/Windows.

Does not print setting values or keys. It never deletes unreadable data.
Run: python tools/windows_config_doctor.py --check
     python tools/windows_config_doctor.py --repair
     python tools/windows_config_doctor.py --repair --use-defaults
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from paper_radar.config_safety import backup_path, _read_with_retry  # noqa: E402
from paper_radar.storage_layout import resolve_data_root  # noqa: E402


def inspect(path: Path) -> dict:
    info = {'exists': os.path.lexists(os.fspath(path)), 'readable': False,
            'kind': 'missing', 'error': ''}
    if not info['exists']:
        return info
    try:
        info['kind'] = 'symlink' if path.is_symlink() else 'directory' if path.is_dir() else 'file'
        if info['kind'] != 'file':
            info['error'] = '配置路径不是普通文件'
            return info
        info['bytes'] = path.stat().st_size
        content = _read_with_retry(path)
        # Validate known fields without printing any setting value.  The
        # doctor runs in the already provisioned Python environment.
        from paper_radar.core import DEFAULTS, validate_config
        validate_config({**DEFAULTS, **{key: value for key,value in content.items() if key in DEFAULTS}})
        info['readable'] = True
        if not info['readable']:
            info['error'] = '顶层不是 JSON 对象'
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        info['error'] = f'{type(exc).__name__}: {str(exc)[:180]}'
    return info


def restore(root: Path, *, use_defaults: bool = False) -> int:
    primary = root/'settings.json'
    backup = backup_path(primary)
    primary_state = inspect(primary)
    backup_state = inspect(backup)
    if primary_state['readable']:
        print('主配置文件可正常读取，无需恢复。')
        return 0
    if not primary_state['exists'] and not backup_state['exists'] and not use_defaults:
        if (root/'data'/'papers.sqlite3').exists():
            print('检测到现有论文数据库，但配置文件不存在；需从备份恢复或明确同意重建默认配置。')
            return 2
        print('主配置和备份均不存在：这是允许的首次使用状态。')
        return 0
    if not backup_state['readable'] and not use_defaults:
        print('拒绝自动恢复：没有有效 settings.json.bak。旧文件已原样保留。')
        print('可先将 OneDrive 中的 settings.json 设置为“始终保留在此设备上”，等待下载完毕。')
        print('若无法修复，可明确使用 --repair --use-defaults（原文件将归档保留，原有研究数据库不清空）。')
        return 2
    if primary.is_symlink():
        print('拒绝恢复符号链接配置文件。请人工检查文件指向并恢复。')
        return 2
    try:
        if backup_state['readable']:
            content = _read_with_retry(backup)
        else:
            from paper_radar.core import DEFAULTS, validate_config
            content = validate_config(dict(DEFAULTS))
        text = json.dumps(content, ensure_ascii=False, indent=2)
        target_dir = root/'.paper_radar_recovery'
        target_dir.mkdir(parents=True, exist_ok=True)
        stage = root/'settings.json.recovery.tmp'
        stage.write_text(text, encoding='utf-8')
        # Verify the staged settings before touching the damaged source.
        _read_with_retry(stage)
        suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)
        archived = target_dir / ('settings.' + suffix + '.json.unreadable')
        moved = False
        if primary_state['exists']:
            os.replace(primary, archived)  # may work even for a cloud placeholder
            moved = True
        try:
            os.replace(stage, primary)
        except OSError:
            if moved:
                os.replace(archived, primary)  # best-effort original restoration
            raise
        if not backup_state['readable']:
            # The explicit defaults recovery needs a usable backup next time.
            if backup_state['exists']:
                old_backup_archive = target_dir/('settings-bak.' + suffix + '.json.unreadable')
                try: os.replace(backup, old_backup_archive)
                except OSError: pass  # do not risk already restored primary
            try:
                new_backup_tmp = root/'settings.json.bak.recovery.tmp'
                new_backup_tmp.write_text(text, encoding='utf-8')
                os.replace(new_backup_tmp, backup)
            except OSError:
                print('注意：主配置已修复，但备份未能重建；请检查 OneDrive 文件权限。')
        # Backup is preserved even when generating defaults; the old settings
        # are retained in .paper_radar_recovery for manual investigation.
        print('恢复成功。', '原 settings.json 已单独保留在：'+str(archived) if moved else '之前无主配置文件。')
        print('采用来源：', '有效 .bak 备份' if backup_state['readable'] else '用户明确选择的默认配置')
        print('请关闭之前的 Paper-Radar 窗口后重新运行 start_windows.bat。')
        return 0
    except (OSError, ValueError, UnicodeError) as exc:
        print(f'恢复失败：{type(exc).__name__}：{exc}')
        print('旧文件不会被主动删除。请在 OneDrive 完全同步后重试，或将完整项目迁至非同步目录。')
        return 3


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Paper-Radar Windows 配置安全检查与恢复')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='只诊断，不修改任何配置文件（默认）')
    mode.add_argument('--repair', action='store_true', help='从有效备份恢复，先隔离旧文件')
    parser.add_argument('--use-defaults', action='store_true', help='当备份不可用时，明确允许用默认配置恢复')
    args=parser.parse_args(argv)
    if args.use_defaults and not args.repair:
        parser.error('--use-defaults 只能与 --repair 一起使用')
    root=resolve_data_root(args.root.resolve(),create=False)
    primary=root/'settings.json'
    print('项目目录：', root)
    print('是否位于 OneDrive：', '是（请检查文件同步状态）' if 'onedrive' in str(root).lower() else '未检测到')
    current=inspect(primary)
    backup=inspect(backup_path(primary))
    for label, state in (('主设置 settings.json',current),('安全备份 settings.json.bak',backup)):
        print(label+':', '正常' if state['readable'] else ('不存在' if not state['exists'] else '不可用'))
        print('  文件类型：',state['kind'], ' 文件字节：',state.get('bytes','-'))
        if state['error']:print('  错误：',state['error'])
    if args.repair:
        return restore(root, use_defaults=args.use_defaults)
    if not current['exists'] and backup['exists']:
        print('主配置不存在但备份仍存在，建议执行 --repair 还原。')
        return 2
    if not current['exists'] and (root/'data'/'papers.sqlite3').exists():
        print('已有论文库却没有 settings.json，不能当作新安装；请先尝试恢复配置。')
        return 2
    return 0 if current['readable'] or (not current['exists'] and not backup['exists']) else 2


if __name__ == '__main__':
    raise SystemExit(main())

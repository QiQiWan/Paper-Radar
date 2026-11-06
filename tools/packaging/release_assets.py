# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Checksum and independently verify the six assets of a multi-OS Release."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re
from zipfile import ZipFile, BadZipFile


def asset_names(version: str) -> list[str]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Invalid version")
    return [
        "Paper-Radar_Windows_x64.zip",
        "Paper-Radar_Ubuntu_portable_amd64.zip",
        f"Paper-Radar_{version}_amd64.deb",
        "Paper-Radar_macOS_arm64.zip",
        "Paper-Radar_macOS_intel.zip",
        f"Paper-Radar_v{version}_Source.zip",
    ]


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def checksum_file(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Missing or empty artifact: {path}")
    name = path.with_name(path.name + ".sha256")
    name.write_text(f"{sha256(path)}  {path.name}\n", encoding="ascii")
    return name


def verify_zip(path: Path) -> None:
    try:
        with ZipFile(path) as z:
            infos = z.infolist()
            if not infos:
                raise ValueError(f"Empty ZIP: {path.name}")
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                parts = Path(normalized).parts
                if not parts or normalized.startswith("/") or ".." in parts or ":" in parts[0]:
                    raise ValueError(f"Unsafe ZIP path: {info.filename}")
            if z.testzip() is not None:
                raise ValueError(f"Corrupted ZIP: {path.name}")
    except BadZipFile as exc:
        raise ValueError(f"Invalid ZIP: {path.name}") from exc


def verify(directory: Path, version: str) -> list[Path]:
    verified = []
    for filename in asset_names(version):
        path = directory / filename
        checksum_path = directory / (filename + ".sha256")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing/empty release asset {path}")
        if not checksum_path.is_file():
            raise ValueError(f"Missing checksum {checksum_path}")
        expected = f"{sha256(path)}  {filename}\n"
        if checksum_path.read_text(encoding="ascii") != expected:
            raise ValueError(f"Checksum mismatch: {filename}")
        if filename.endswith(".zip"):
            verify_zip(path)
        elif filename.endswith(".deb") and path.open("rb").read(8) != b"!<arch>\n":
            raise ValueError(f"Invalid Debian archive: {filename}")
        verified.extend([path, checksum_path])
    return verified


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd_hash = sub.add_parser("hash")
    cmd_hash.add_argument("files", nargs="+", type=Path)
    cmd_verify = sub.add_parser("verify")
    cmd_verify.add_argument("--release-dir", required=True, type=Path)
    cmd_verify.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.cmd == "hash":
        for path in args.files:
            print(checksum_file(path))
    else:
        items = verify(args.release_dir, args.version)
        print(f"Verified {len(items) // 2} release assets and checksums")


if __name__ == "__main__":
    main()

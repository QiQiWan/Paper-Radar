# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Validate that the two canonical Paper-Radar versions agree before CI releases."""
from __future__ import annotations
import argparse
import ast
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def read_version(root: Path = ROOT) -> str:
    module = ast.parse((root / "paper_radar" / "__init__.py").read_text(encoding="utf-8"))
    python_version = None
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                python_version = node.value.value
    project_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if python_version != project_version:
        raise ValueError(f"Version mismatch: paper_radar={python_version!r}, pyproject.toml={project_version!r}")
    if not isinstance(python_version, str) or not SEMVER.fullmatch(python_version):
        raise ValueError("Release version must be numeric MAJOR.MINOR.PATCH, e.g. 1.27.0")
    agents_ast = ast.parse((root / "paper_radar" / "agents" / "catalog.py").read_text(encoding="utf-8"))
    agent_version = None
    for node in agents_ast.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets):
            if isinstance(node.value, ast.Constant):
                agent_version = node.value.value
    if agent_version != python_version:
        raise ValueError(f"Agent catalog version mismatch: {agent_version!r}")
    index = (root / "paper_radar" / "static" / "index.html").read_text(encoding="utf-8")
    portal = (root / "paper_radar" / "static" / "portal.html").read_text(encoding="utf-8")
    if f"Paper-Radar v{python_version}" not in index or f'id="portalVersion">v{python_version}</span>' not in portal:
        raise ValueError("Displayed application version is not aligned with project version")
    return python_version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    version = read_version()
    print(f"Paper-Radar v{version}")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"version={version}\ntag=v{version}\n")


if __name__ == "__main__":
    main()

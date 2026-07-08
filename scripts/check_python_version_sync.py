#!/usr/bin/env python3
"""Fail if any Dockerfile's `FROM python:X.Y` base diverges from pyproject's floor.

check-ast / ruff run on the *local* interpreter (3.14), so they can't notice a
base-image regression — a Dockerfile silently dropped back to `python:3.12-slim`
would still lint clean while the 3.14-only code (PEP 758 `except A, B:`) fails at
runtime. This guard locks the two together: the `requires-python` floor and every
`FROM python:` pin must agree on the same major.minor.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROM_PYTHON = re.compile(r"^FROM\s+python:(\d+)\.(\d+)", re.MULTILINE)
FLOOR = re.compile(r">=\s*(\d+)\.(\d+)")


def floor_version() -> tuple[int, int]:
    """The (major, minor) lower bound declared by `requires-python`."""
    spec = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    match = FLOOR.search(spec)
    if not match:
        sys.exit(f"requires-python {spec!r} has no `>=X.Y` lower bound to anchor on")
    return int(match[1]), int(match[2])


def dockerfile_versions() -> list[tuple[Path, tuple[int, int]]]:
    """Every `FROM python:X.Y` pin across the tree, as (path, (major, minor))."""
    found = []
    for path in ROOT.rglob("Dockerfile"):
        if ".venv" in path.parts or "build" in path.parts or "node_modules" in path.parts:
            continue
        for major, minor in FROM_PYTHON.findall(path.read_text()):
            found.append((path.relative_to(ROOT), (int(major), int(minor))))
    return found


def main() -> int:
    floor = floor_version()
    drifted = [(p, v) for p, v in dockerfile_versions() if v != floor]
    if drifted:
        want = f"{floor[0]}.{floor[1]}"
        print(f"Python version drift — requires-python floor is {want}, but:")
        for path, (major, minor) in drifted:
            print(f"  {path}: FROM python:{major}.{minor}")
        print("Align the Dockerfile base and the pyproject floor on the same major.minor.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

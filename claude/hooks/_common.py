"""Shared helpers for the org hook scripts (claude/hooks/*.py).

Each hook is a standalone CLI that runs identically as a local git/Claude Code hook and
as a job in the org guardrails workflow (ADR-002 R8). Standard library only.
"""
from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):  # orgfile.py sits next to the hooks when distributed, one level up in the source repo
    if (_p / "orgfile.py").is_file() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import orgfile  # noqa: E402

IN_ACTIONS = bool(os.environ.get("GITHUB_ACTIONS"))


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def changed_files(root: Path, rng: str | None) -> list[str]:
    """Files changed in a commit range (base..head), or the full tracked tree when rng is None."""
    if rng:
        out = git(root, "diff", "--name-only", "--diff-filter=ACMR", rng)
        return [line for line in out.splitlines() if line.strip()]
    try:
        out = git(root, "ls-files")
        return [line for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        # Not a git checkout (e.g. an exported tree): walk the filesystem instead.
        skip = {".git", "node_modules", ".direnv", "result"}
        return sorted(
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and not any(part in skip for part in p.relative_to(root).parts)
        )


def commits(root: Path, rng: str) -> list[tuple[str, str, str]]:
    """(sha, author, subject) for each non-merge commit in the range."""
    out = git(root, "log", "--no-merges", "--format=%H%x1f%an <%ae>%x1f%s", rng)
    result = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, author, subject = line.split("\x1f", 2)
        result.append((sha, author, subject))
    return result


def trailers(root: Path, sha: str) -> dict[str, list[str]]:
    body = git(root, "show", "-s", "--format=%B", sha)
    found: dict[str, list[str]] = {}
    for line in body.splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            key = key.strip()
            if key and key[0].isupper() and " " not in key:
                found.setdefault(key, []).append(val.strip())
    return found


def matches_any(path: str, globs: list[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
        # "dir/**" should also match files directly under dir/
        if g.endswith("/**") and path.startswith(g[:-2]):
            return True
    return False


def annotate(level: str, msg: str, file: str | None = None, line: int | None = None) -> None:
    """GitHub-style annotation in Actions, plain line elsewhere."""
    if IN_ACTIONS:
        loc = ""
        if file:
            loc = f" file={file}" + (f",line={line}" if line else "")
        print(f"::{level}{loc}::{msg}")
    else:
        prefix = {"error": "ERROR", "warning": "WARN", "notice": "note"}.get(level, level)
        where = f" [{file}{':' + str(line) if line else ''}]" if file else ""
        print(f"{prefix}{where}: {msg}")


def load_cfg(root: Path):
    return orgfile.load(root)


def parse_common(argv: list[str], description: str):
    import argparse

    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--root", type=Path, default=Path("."), help="repository root")
    ap.add_argument("--range", dest="rng", default=None, help="commit range base..head (default: whole tree / all)")
    ap.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    return ap

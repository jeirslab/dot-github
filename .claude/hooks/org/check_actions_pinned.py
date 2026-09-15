#!/usr/bin/env python3
"""Supply chain, part 1 (ideas.md 18): every `uses:` in the workflows is pinned.

Allowed refs: a 40-hex commit SHA, or a version tag (`v1`, `v1.2.3`). Rejected: branch
names (`@main`, `@master`, `@develop`) and unpinned local paths are fine (`./`).

  check_actions_pinned.py [--root .]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import annotate, parse_common  # noqa: E402

USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^\s'\"#]+)")
OK_REF = re.compile(r"^(?:[0-9a-f]{40}|v\d+(?:\.\d+){0,2}|\d+(?:\.\d+){1,2})$")


def main(argv: list[str] | None = None) -> int:
    ap = parse_common(argv or sys.argv[1:], __doc__)
    args = ap.parse_args(argv)
    root: Path = args.root.resolve()
    bad = 0
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        for i, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            m = USES.match(line)
            if not m:
                continue
            ref = m.group(1)
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            if "@" not in ref:
                annotate("error", f"unpinned action {ref!r}: add @<tag> or @<sha>", str(wf.relative_to(root)), i)
                bad += 1
                continue
            tag = ref.rsplit("@", 1)[1]
            if not OK_REF.match(tag):
                annotate("error", f"action {ref!r} pinned to a branch; pin to a version tag or commit SHA", str(wf.relative_to(root)), i)
                bad += 1
    print("actions: all pinned" if not bad else f"actions: {bad} unpinned")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

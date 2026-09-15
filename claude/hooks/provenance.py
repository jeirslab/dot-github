#!/usr/bin/env python3
"""Provenance: who (or what) authored the commits in a range (ADR-002 R4).

Classifies each commit as human, claude-code (a `Claude-Session:` trailer or a Claude
co-author), lovable (bot login), alert-agent, or bot (dependabot/renovate/org bots),
and prints the PR labels to apply: `author:<class>` for each class present plus
`author:mixed` when humans and agents both contributed.

  provenance.py --range base..head [--root .] [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import commits, parse_common, trailers  # noqa: E402

BOT_MARKERS = {
    "lovable": ("lovable", "loveable"),
    "alert-agent": ("alert-agent", "alert-response"),
    "bot": ("dependabot", "renovate", "org-config-sync", "release-bot", "rollback-bot", "github-actions"),
}


def classify(author: str, tr: dict[str, list[str]]) -> str:
    a = author.lower()
    if "Claude-Session" in tr or any("claude" in v.lower() for v in tr.get("Co-Authored-By", [])):
        return "claude-code"
    for cls, marks in BOT_MARKERS.items():
        if any(m in a for m in marks):
            return cls
    return "human"


def main(argv: list[str] | None = None) -> int:
    ap = parse_common(argv or sys.argv[1:], __doc__)
    args = ap.parse_args(argv)
    if not args.rng:
        print("provenance: --range base..head is required")
        return 2
    root: Path = args.root.resolve()
    classes: dict[str, int] = {}
    sessions: list[str] = []
    for sha, author, _subject in commits(root, args.rng):
        tr = trailers(root, sha)
        cls = classify(author, tr)
        classes[cls] = classes.get(cls, 0) + 1
        sessions += tr.get("Claude-Session", [])
    labels = [f"author:{c}" for c in sorted(classes)]
    agent_classes = {c for c in classes if c != "human"}
    if "human" in classes and agent_classes:
        labels.append("author:mixed")
    agent_authored = bool(agent_classes)
    result = {"classes": classes, "labels": labels, "agent_authored": agent_authored, "sessions": sorted(set(sessions))}
    if args.json:
        print(json.dumps(result))
    else:
        print("provenance: " + ", ".join(f"{k}={v}" for k, v in sorted(classes.items())) + f" → labels {labels}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

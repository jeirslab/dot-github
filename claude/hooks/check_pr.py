#!/usr/bin/env python3
"""PR hygiene (ideas.md 21) and ADR-required (ideas.md 1) checks for one pull request.

  check_pr.py --range base..head --body-file BODY.md [--labels a,b] [--title T] [--root .]

Checks:
  * size ceiling (org.toml pr.max_files / pr.max_lines) → label `needs-split` suggested
  * exactly one ticket key across the commits and title (else `needs-split`)
  * a "Verification" section in the body when pr.require_verification_section
  * a change under adr.required_paths requires a change under docs/adr/ or the waiver label
  * generated files (org.toml generated.files) changed only together with their inputs
Findings are errors; suggestions are notices. Exit 1 on errors.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import annotate, changed_files, commits, git, load_cfg, matches_any, parse_common  # noqa: E402

TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")


def verification_has_substance(section: str) -> bool:
    """True when the text after the Verification heading (up to the next heading) carries
    at least one line that looks like evidence: a command/URL in backticks, a table row
    with a verdict, or a sentence of 4+ words that is not an unchecked box or placeholder."""
    section = re.split(r"(?m)^#+\s|^\*\*[^*]+\*\*\s*$", section, maxsplit=1)[0]
    section = re.sub(r"<!--.*?-->", "", section, flags=re.S)
    for line in section.splitlines():
        t = line.strip()
        if not t or t.startswith("- [ ]") or t.lower() in ("n/a", "tbd", "todo", "none", "-"):
            continue
        if t.startswith("|") and set(t) <= set("|-: "):
            continue
        if "`" in t or "://" in t:
            return True
        if t.startswith("|") and any(v in t.lower() for v in ("reproduced", "pass", "fail", "ok", "verified")):
            return True
        if len(re.findall(r"[A-Za-z]{2,}", t)) >= 4:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = parse_common(argv or sys.argv[1:], __doc__)
    ap.add_argument("--body-file", type=Path, default=None)
    ap.add_argument("--title", default="")
    ap.add_argument("--labels", default="", help="comma-separated labels on the PR")
    args = ap.parse_args(argv)
    if not args.rng:
        print("check_pr: --range base..head is required")
        return 2
    root: Path = args.root.resolve()
    cfg = load_cfg(root)
    labels = {l.strip() for l in args.labels.split(",") if l.strip()}
    body = args.body_file.read_text(encoding="utf-8") if args.body_file and args.body_file.is_file() else ""
    errors = 0
    suggest: set[str] = set()

    files = changed_files(root, args.rng)
    stat = git(root, "diff", "--shortstat", args.rng).strip()
    m = re.search(r"(\d+) insertion", stat); ins = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) deletion", stat); dele = int(m.group(1)) if m else 0
    lines = ins + dele

    # size
    if len(files) > cfg["pr"]["max_files"] or lines > cfg["pr"]["max_lines"]:
        annotate("warning", f"large PR: {len(files)} files, {lines} changed lines (limits {cfg['pr']['max_files']}/{cfg['pr']['max_lines']}) — split it or justify in the body")
        suggest.add("needs-split")

    # one ticket
    keys: set[str] = set(TICKET.findall(args.title))
    for _sha, _a, subject in commits(root, args.rng):
        keys |= set(TICKET.findall(subject.split(":", 1)[0]))
    if len(keys) > 1:
        annotate("warning", f"PR spans {len(keys)} tickets ({', '.join(sorted(keys))}); one ticket per PR keeps releases and rollbacks legible")
        suggest.add("needs-split")
    if not keys:
        annotate("error", "no ticket key in the PR title or commit prefixes")
        errors += 1

    # verification section
    if cfg["pr"]["require_verification_section"]:
        m = re.search(r"(?im)^(#+\s*verification[^\n]*|\*\*verification[^\n]*)$", body)
        if not m:
            annotate("error", "PR body needs a 'Verification' section saying what was actually run or checked")
            errors += 1
        elif not verification_has_substance(body[m.end():]):
            annotate("error", "the 'Verification' section is empty or template-only: list what was run (commands, URLs, screenshots) and the result — the org-verifier subagent produces this")
            errors += 1

    # ADR required
    trig = [f for f in files if matches_any(f, cfg["adr"]["required_paths"])]
    adr_changed = any(f.startswith("docs/adr/") for f in files)
    if trig and not adr_changed and cfg["labels"]["adr_waiver"] not in labels:
        annotate("error", f"changes under ADR-required paths ({', '.join(trig[:5])}{'…' if len(trig) > 5 else ''}) but no docs/adr/ change and no '{cfg['labels']['adr_waiver']}' label")
        errors += 1
        suggest.add("needs-adr")

    # generated files
    for g in cfg["generated"]["files"]:
        if g["path"] in files and not any(i in files for i in g["inputs"]):
            annotate("error", f"generated file {g['path']} changed without any of its inputs ({', '.join(g['inputs'])}); regenerate from the inputs, never hand-edit", g["path"])
            errors += 1

    out = {"errors": errors, "files": len(files), "lines": lines, "tickets": sorted(keys), "suggest_labels": sorted(suggest)}
    if args.json:
        print(json.dumps(out))
    else:
        print(f"pr: {'ok' if not errors else str(errors) + ' error(s)'}; {len(files)} files, {lines} lines, tickets {sorted(keys)}, suggest {sorted(suggest)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

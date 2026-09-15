#!/usr/bin/env python3
"""jira — the org's token-cheap Jira interface (ADR-008, INFRA-278; port of the docs repo's
`docs jira`, INFRA-219).

Reads come from a local snapshot; writes are batched and validated before any network call:

  jira sync [--projects A,B] [--open-only] [--since 7d] [--search kw...] [--raw]   refresh the snapshot
  jira show INFRA-12 [...]          one ticket from the snapshot (summary, status, assignee, comments)
  jira find "keyword"               keys + summaries matching, from the snapshot
  jira apply plan.yaml [--dry-run]  create / transition / assign / comment / summary / delete, in one run
  jira standup [--weeks 1] [--mine|--team] [--dry-run]     AI-written activity summary via `claude -p`
  jira sweep --repos-file claude/repos.txt [--all --org X]  branch ↔ ticket report (see branch_sweep.py)
  jira config                       the effective configuration (base URL, snapshot path, scopes)

Configuration: org.toml [jira] + JIRA_BASE_URL; credentials only from JIRA_EMAIL / JIRA_API_TOKEN.
Standard library only; PyYAML (optional) lets plans be YAML instead of JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jira_config import Config  # noqa: E402


def jql_date(value: str, end_of_day: bool = False) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f'"{value} 23:59"' if end_of_day else f'"{value}"'
    m = re.fullmatch(r"-?(\d+[mhdw])", value)
    if m:
        return f"-{m.group(1)}"
    raise SystemExit(f"jira: {value!r} — use YYYY-MM-DD or a relative duration like 7d / 2w")


def sync_jql(cfg: Config, projects: str | None, open_only: bool, since: str | None, until: str | None,
             date_field: str | None) -> str | None:
    keys = [p.strip().upper() for p in projects.split(",") if p.strip()] if projects else cfg.sync.get("projects", [])
    excluded = cfg.sync.get("exclude_statuses", [])
    since = since or cfg.sync.get("since") or None
    until = until or cfg.sync.get("until") or None
    field = date_field or cfg.sync.get("date_field") or "updated"
    clauses = []
    if keys:
        clauses.append(f"project IN ({', '.join(keys)})")
    if open_only:
        clauses.append("statusCategory != Done")
    elif excluded:
        clauses.append("status NOT IN (" + ", ".join(f'"{s}"' for s in excluded) + ")")
    if since:
        clauses.append(f"{field} >= {jql_date(since)}")
    if until:
        clauses.append(f"{field} <= {jql_date(until, True)}")
    return (" AND ".join(clauses) + " ORDER BY created ASC") if clauses else None


def cmd_sync(a) -> int:
    from jira_client import JiraError
    from jira_export import export
    cfg = Config(a.root)
    cfg.credentials()
    query = a.jql or sync_jql(cfg, a.projects, a.open_only, a.since, a.until, a.date_field)
    try:
        for fmt in ["simple"] + (["raw"] if a.raw else []):
            export(cfg, fmt, list(a.search) if a.search else None, query)
    except JiraError as e:
        print(f"jira: {e}", file=sys.stderr); return 1
    print(f"Snapshot: {cfg.snapshot_path}")
    return 0


def _catalog(cfg: Config):
    from jira_models import Catalog
    if not cfg.snapshot_path.is_file():
        raise SystemExit(f"jira: no snapshot at {cfg.snapshot_path} — run `jira sync` first")
    return Catalog.load(cfg.snapshot_path)


def cmd_show(a) -> int:
    cfg = Config(a.root); cat = _catalog(cfg)
    rc = 0
    for key in a.keys:
        n = cat.issue(key.upper())
        if n is None:
            print(f"{key}: not in snapshot (sync, or check the key)"); rc = 1; continue
        if a.json:
            print(json.dumps(n, indent=2, ensure_ascii=False)); continue
        who = cat.person(n.get("assignee"))
        print(f"{n['key']}  [{n.get('type')}]  {n.get('status')}  ← {who['displayName'] if who else 'unassigned'}")
        print(f"  {n.get('summary')}")
        if n.get("parentKey"):
            print(f"  parent: {n['parentKey']}")
        for c in (n.get("comments") or [])[-a.comments:] if a.comments else []:
            au = cat.person(c.get("author"))
            print(f"  · {c.get('created')} {au['displayName'] if au else '?'}: {c.get('body', '').strip()[:300].replace(chr(10), ' ')}")
        if n.get("children"):
            print(f"  children: {', '.join(c['key'] + ' (' + (c.get('status') or '?') + ')' for c in n['children'])}")
    return rc


def cmd_find(a) -> int:
    cfg = Config(a.root); cat = _catalog(cfg)
    hits = cat.find(a.term)
    if a.open:
        hits = [n for n in hits if (n.get("status") or "").lower() not in ("done", "closed", "abandon", "abandoned")]
    for n in sorted(hits, key=lambda n: n["key"]):
        who = cat.person(n.get("assignee"))
        print(f"{n['key']:<16} {n.get('status') or '?':<12} {(who or {}).get('displayName') or '-':<16} {n.get('summary')}")
    print(f"{len(hits)} match(es) in {cfg.snapshot_path.name} (exported {cat.summary.get('exported_at', '?')[:16]})", file=sys.stderr)
    return 0


def cmd_apply(a) -> int:
    import jira_apply
    from jira_client import JiraHttp, JiraWriter
    cfg = Config(a.root); cat = _catalog(cfg)
    try:
        plan = jira_apply.load_plan(a.plan)
    except Exception as e:
        print(f"jira: bad plan: {e}", file=sys.stderr); return 2
    errors = jira_apply.validate(plan, cat)
    for e in errors:
        print(f"INVALID  {e}", file=sys.stderr)
    if errors:
        return 1
    print(f"Plan valid: {len(plan)} op(s).")
    if a.dry_run:
        return 0
    email, token = cfg.credentials()
    writer = JiraWriter(JiraHttp(cfg.base_url, email, token), cat)
    results = jira_apply.apply(plan, writer, default_author=email)
    for r in results:
        print(f"{'ok  ' if r['ok'] else 'FAIL'}  {r['op']:<10} {r['key']:<14} {r['detail']}")
    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"Stopped at first failure; {len(plan) - len(results)} op(s) not attempted. "
              "The snapshot reflects the ops that succeeded.", file=sys.stderr)
        return 1
    return 0


def cmd_standup(a) -> int:
    import jira_standup
    cfg = Config(a.root)
    since, until = jira_standup.window(a.weeks, a.since, a.until)
    parts = [a.context] if a.context else []
    if a.context_file:
        parts.append(Path(a.context_file).read_text().strip())
    ctx = jira_standup.build_context(cfg, since, until, a.mine, a.team, a.projects, "\n\n".join(parts) or None)
    if a.dry_run:
        print(json.dumps(ctx, indent=2, ensure_ascii=False)); return 0
    text = jira_standup.summarize(cfg, ctx, a.model, a.instructions)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n"); print(f"Written: {a.out}", file=sys.stderr)
    return 0


def cmd_sweep(argv: list[str], root: str | None) -> int:
    import branch_sweep
    return branch_sweep.main(argv + (["--root", root] if root else []))


def cmd_config(a) -> int:
    print(json.dumps(Config(a.root).to_dict(), indent=2)); return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jira", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="directory whose org.toml [jira] to read (default: walk up from cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="refresh the snapshot from the live API")
    s.add_argument("--raw", action="store_true"); s.add_argument("--search", nargs="+", default=None)
    s.add_argument("--projects", default=None); s.add_argument("--open-only", action="store_true")
    s.add_argument("--since", default=None); s.add_argument("--until", default=None)
    s.add_argument("--date-field", choices=["updated", "created", "resolved"], default=None)
    s.add_argument("--jql", default=None); s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("show", help="print ticket(s) from the snapshot")
    s.add_argument("keys", nargs="+"); s.add_argument("--json", action="store_true")
    s.add_argument("--comments", type=int, default=3, help="last N comments (0 = none)"); s.set_defaults(fn=cmd_show)

    s = sub.add_parser("find", help="search keys and summaries in the snapshot")
    s.add_argument("term"); s.add_argument("--open", action="store_true"); s.set_defaults(fn=cmd_find)

    s = sub.add_parser("apply", help="apply a YAML/JSON change-set")
    s.add_argument("plan"); s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("standup", help="AI activity summary for a window")
    s.add_argument("--weeks", type=int, default=1); s.add_argument("--since"); s.add_argument("--until")
    s.add_argument("--mine", action="store_true"); s.add_argument("--team", action="store_true")
    s.add_argument("--projects"); s.add_argument("--context"); s.add_argument("--context-file")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--out"); s.add_argument("--model")
    s.add_argument("--instructions"); s.set_defaults(fn=cmd_standup)

    sub.add_parser("sweep", help="branch ↔ ticket report (all following args go to branch_sweep.py --help)")

    s = sub.add_parser("config", help="effective configuration"); s.set_defaults(fn=cmd_config)

    args = list(sys.argv[1:] if argv is None else argv)
    if "sweep" in args:  # everything after `sweep` belongs to branch_sweep's own parser
        i = args.index("sweep")
        pre = ap.parse_args(args[:i] + ["config"])
        return cmd_sweep(args[i + 1:], pre.root)
    a = ap.parse_args(args)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

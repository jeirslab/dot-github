#!/usr/bin/env python3
"""Branch protection as code (ideas.md 15, ADR-003 R5/R9): apply the rulesets in
claude/rulesets/*.json to repositories, creating or updating by name. Diffs before writing
so an unchanged ruleset is a no-op; --dry-run prints what would change.

Requires a token with repository administration (ORG_ADMIN_TOKEN). Standard library only.

  apply_rulesets.py --repos owner/a owner/b [--rulesets-dir claude/rulesets] [--dry-run]
  apply_rulesets.py --repos-file claude/repos.txt ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


def _req(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json", "User-Agent": "org-rulesets",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        return e.code, (json.loads(e.read().decode() or "null"))


def _normalise(rs: dict) -> dict:
    """Only the fields we manage, so a diff against the API's fuller object is meaningful."""
    return {
        "name": rs["name"], "target": rs.get("target", "branch"), "enforcement": rs["enforcement"],
        "conditions": rs.get("conditions", {}), "bypass_actors": rs.get("bypass_actors", []),
        "rules": sorted(rs.get("rules", []), key=lambda r: r["type"]),
    }


def read_repos_file(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and "/" in line:
            out.append(line)
    return out


def apply(repo: str, rulesets: list[dict], token: str, dry_run: bool) -> list[str]:
    log = []
    status, existing = _req("GET", f"{API}/repos/{repo}/rulesets", token)
    if status != 200 or not isinstance(existing, list):
        return [f"{repo}: cannot list rulesets (HTTP {status}): {existing}"]
    by_name = {r["name"]: r["id"] for r in existing}
    for rs in rulesets:
        want = _normalise(rs)
        if rs["name"] in by_name:
            rid = by_name[rs["name"]]
            st, cur = _req("GET", f"{API}/repos/{repo}/rulesets/{rid}", token)
            if st == 200 and isinstance(cur, dict) and _normalise(cur) == want:
                log.append(f"{repo}: '{rs['name']}' unchanged")
                continue
            if dry_run:
                log.append(f"{repo}: would UPDATE '{rs['name']}' (id {rid})")
                continue
            st, resp = _req("PUT", f"{API}/repos/{repo}/rulesets/{rid}", token, want)
            log.append(f"{repo}: updated '{rs['name']}'" if st == 200 else f"{repo}: UPDATE FAILED '{rs['name']}' HTTP {st}: {resp}")
        else:
            if dry_run:
                log.append(f"{repo}: would CREATE '{rs['name']}'")
                continue
            st, resp = _req("POST", f"{API}/repos/{repo}/rulesets", token, want)
            log.append(f"{repo}: created '{rs['name']}'" if st == 201 else f"{repo}: CREATE FAILED '{rs['name']}' HTTP {st}: {resp}")
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos", nargs="*", default=[])
    ap.add_argument("--repos-file", type=Path)
    ap.add_argument("--rulesets-dir", type=Path, default=Path(__file__).resolve().parent / "rulesets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    token = os.environ.get("ORG_ADMIN_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token and not args.dry_run:
        print("apply_rulesets: ORG_ADMIN_TOKEN (or GH_TOKEN) is required")
        return 2
    repos = list(args.repos)
    if args.repos_file:
        repos += read_repos_file(args.repos_file)
    if not repos:
        print("apply_rulesets: no repositories given")
        return 2
    rulesets = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.rulesets_dir.glob("*.json"))]
    failed = 0
    for repo in repos:
        for line in (apply(repo, rulesets, token, args.dry_run) if token else [f"{repo}: would apply {len(rulesets)} ruleset(s) (no token, dry run)"]):
            print(line)
            if "FAILED" in line or "cannot" in line:
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

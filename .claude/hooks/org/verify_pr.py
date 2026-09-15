#!/usr/bin/env python3
"""Software substitute for branch rulesets and environment reviewers (ADR-007).

Given a commit on a deploy branch, verify that it landed through a pull request that:
  1. was merged (not a direct push);
  2. had at least one APPROVED review from someone other than the author
     (`--min-approvals`, default 1);
  3. had the required org check runs succeed on its head SHA (`--required-checks`,
     comma-separated check-run names; "gate / gate" etc.);
  4. if the PR touched a path listed in org.toml [codeowners], had an approval from one of
     the listed owners (users directly; teams resolved via the API when the token allows,
     otherwise a warning).

Exit 0 when everything holds; exit 1 with reasons otherwise; exit 3 when the commit has no
merged PR at all (the branch guard uses this to open a revert). Standard library only.

  verify_pr.py --repo owner/name --sha <sha> [--root .] [--required-checks "a,b"] [--min-approvals 1]
  Token from GH_TOKEN / GITHUB_TOKEN. --dump prints the collected facts as JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import annotate, load_cfg, matches_any  # noqa: E402

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


class GitHub:
    """Tiny REST client; replaceable in tests with any object exposing get(path)."""

    def __init__(self, token: str):
        self.token = token

    def get(self, path: str):
        req = urllib.request.Request(f"{API}{path}", headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "org-verify-pr",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, None


def merged_pr_for(gh, repo: str, sha: str) -> dict | None:
    st, prs = gh.get(f"/repos/{repo}/commits/{sha}/pulls")
    if st != 200 or not isinstance(prs, list):
        return None
    merged = [p for p in prs if p.get("merged_at") and (p.get("merge_commit_sha") == sha or True)]
    # prefer the PR whose merge commit is exactly this sha
    exact = [p for p in merged if p.get("merge_commit_sha") == sha]
    return (exact or merged or [None])[0]


def approvals(gh, repo: str, number: int, author: str) -> list[str]:
    st, reviews = gh.get(f"/repos/{repo}/pulls/{number}/reviews?per_page=100")
    if st != 200 or not isinstance(reviews, list):
        return []
    latest: dict[str, str] = {}
    for r in reviews:  # chronological; the last state per reviewer wins
        u = (r.get("user") or {}).get("login", "")
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[u] = r["state"]
    return sorted(u for u, s in latest.items() if s == "APPROVED" and u and u != author)


def check_runs(gh, repo: str, sha: str) -> dict[str, str]:
    st, data = gh.get(f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")
    if st != 200 or not isinstance(data, dict):
        return {}
    return {c["name"]: (c.get("conclusion") or c.get("status") or "unknown") for c in data.get("check_runs", [])}


def changed_files(gh, repo: str, number: int) -> list[str]:
    st, files = gh.get(f"/repos/{repo}/pulls/{number}/files?per_page=100")
    return [f["filename"] for f in files] if st == 200 and isinstance(files, list) else []


def team_members(gh, org: str, slug: str) -> list[str] | None:
    st, members = gh.get(f"/orgs/{org}/teams/{slug}/members?per_page=100")
    if st != 200 or not isinstance(members, list):
        return None
    return [m["login"] for m in members]


def verify(gh, repo: str, sha: str, cfg: dict, required_checks: list[str], min_approvals: int) -> tuple[int, dict]:
    facts: dict = {"repo": repo, "sha": sha}
    pr = merged_pr_for(gh, repo, sha)
    if not pr:
        facts["pr"] = None
        annotate("error", f"{sha[:7]} on {repo} is not the merge of a pull request — direct push")
        return 3, facts
    number, author = pr["number"], (pr.get("user") or {}).get("login", "")
    head = (pr.get("head") or {}).get("sha", "")
    facts.update({"pr": number, "author": author, "head": head, "title": pr.get("title")})
    problems = 0

    appr = approvals(gh, repo, number, author)
    facts["approvals"] = appr
    if len(appr) < min_approvals:
        annotate("error", f"PR #{number}: {len(appr)} approving review(s) from someone other than the author; {min_approvals} required")
        problems += 1

    runs = check_runs(gh, repo, head) if head else {}
    facts["check_runs"] = runs
    for name in required_checks:
        concl = runs.get(name)
        if concl is None:
            annotate("error", f"PR #{number}: required check '{name}' never ran on {head[:7]}")
            problems += 1
        elif concl != "success":
            annotate("error", f"PR #{number}: required check '{name}' concluded '{concl}' on {head[:7]}")
            problems += 1

    owners_cfg: dict = cfg.get("codeowners", {})
    if owners_cfg:
        files = changed_files(gh, repo, number)
        facts["files"] = len(files)
        needed: dict[str, list[str]] = {}
        for glob, owners in owners_cfg.items():
            if any(matches_any(f, [glob]) for f in files):
                needed[glob] = owners
        facts["codeowner_paths"] = needed
        if needed:
            org = repo.split("/", 1)[0]
            for glob, owners in needed.items():
                allowed: set[str] = set()
                unresolved = []
                for o in owners:
                    o = o.lstrip("@")
                    if "/" in o:
                        members = team_members(gh, o.split("/", 1)[0], o.split("/", 1)[1])
                        if members is None:
                            unresolved.append(o)
                        else:
                            allowed |= set(members)
                    else:
                        allowed.add(o)
                ok = bool(allowed & set(appr))
                if ok:
                    continue
                if unresolved and not allowed:
                    annotate("warning", f"PR #{number}: touched {glob} but team(s) {unresolved} could not be resolved (token lacks read:org?) — code-owner approval not verified")
                else:
                    annotate("error", f"PR #{number}: touched {glob} without an approval from a listed owner ({', '.join(owners)}); approvers were {appr or 'none'}")
                    problems += 1
    return (1 if problems else 0), facts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--required-checks", default="")
    ap.add_argument("--min-approvals", type=int, default=1)
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        print("verify_pr: GH_TOKEN/GITHUB_TOKEN required")
        return 2
    cfg = load_cfg(args.root.resolve())
    required = [c.strip() for c in args.required_checks.split(",") if c.strip()]
    rc, facts = verify(GitHub(token), args.repo, args.sha, cfg, required, args.min_approvals)
    if args.dump:
        print(json.dumps(facts, indent=2))
    print({0: "verify_pr: ok", 1: "verify_pr: FAILED", 3: "verify_pr: DIRECT PUSH"}[rc])
    return rc


if __name__ == "__main__":
    sys.exit(main())

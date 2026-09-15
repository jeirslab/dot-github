#!/usr/bin/env python3
"""Resolve the repositories an org-wide workflow acts on (ADR-009, INFRA-283).

Normal mode: the allowlist (`claude/repos.txt`) or an explicit override, at each
repository's default/integration branch (empty ref).

Feature-test mode (`ORG_FEAT_TEST=true`): exactly one target — this repository at its
feature-test branch (`ORG_FEAT_TEST_BRANCH`, default `feature-test`) — whatever the
trigger, the override, or the branch the workflow was dispatched from. This is how a
workflow change is exercised on the org's own repository before it goes org-wide.

  targets.py --owner ORG [--repos-file claude/repos.txt] [--repos "a/b c/d"] [--github-output]
  → JSON {"feat_test": bool, "ref": "" | "<branch>", "repos": [...], "self": "ORG/.github"}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def read_repos_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [l.split("#")[0].strip() for l in path.read_text().splitlines() if l.split("#")[0].strip()]


def resolve(owner: str, repos_file: Path | None, override: str | None, env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    self_repo = env.get("GITHUB_REPOSITORY") or f"{owner}/.github"
    feat = str(env.get("ORG_FEAT_TEST", "")).lower() == "true"
    branch = env.get("ORG_FEAT_TEST_BRANCH") or "feature-test"
    if feat:
        return {"feat_test": True, "ref": branch, "repos": [self_repo], "self": self_repo,
                "note": f"feature-test mode: every org-wide action is confined to {self_repo}@{branch}"}
    repos = [r for r in (override or "").replace(",", " ").split() if r] if override and override.strip() else []
    if not repos and repos_file:
        repos = read_repos_file(repos_file)
    repos = [r for r in dict.fromkeys(repos) if REPO_RE.match(r)]
    # Same-org confinement (ADR-014): act only on repos owned by the org that owns .github,
    # no matter how broadly the provided token is scoped. A cross-org entry — in the allowlist
    # or in the dispatch override — is dropped, not acted on.
    kept = [r for r in repos if r.split("/", 1)[0] == owner]
    foreign = [r for r in repos if r.split("/", 1)[0] != owner]
    note = f"ignored {len(foreign)} target(s) outside org '{owner}': {' '.join(foreign)}" if foreign else ""
    return {"feat_test": False, "ref": "", "repos": kept, "self": self_repo, "note": note}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repos-file", type=Path, default=None)
    ap.add_argument("--repos", default="", help="explicit space/comma-separated override (ignored in feature-test mode)")
    ap.add_argument("--github-output", action="store_true", help="also append repos/ref/feat_test to $GITHUB_OUTPUT")
    a = ap.parse_args(argv)
    r = resolve(a.owner, a.repos_file, a.repos)
    print(json.dumps(r))
    if r["note"]:
        print(f"::notice::{r['note']}", file=sys.stderr)
    if a.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        lines = [f"repos={json.dumps(r['repos'])}", f"ref={r['ref']}", f"feat_test={str(r['feat_test']).lower()}", f"self={r['self']}"]
        if out:
            with open(out, "a") as f:
                f.write("\n".join(lines) + "\n")
        else:
            print("\n".join(lines), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

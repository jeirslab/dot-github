#!/usr/bin/env python3
"""Set the org-sync credential on demand (ADR-014/ADR-015).

A workflow cannot set its own bootstrap secret (GITHUB_TOKEN cannot manage secrets, and
Actions never exposes the triggering user's credentials), so the root credential is always
provisioned locally, deliberately, by someone with admin. This wraps that one action.

  set-sync-token                      # ORG_SYNC_TOKEN on <owner>/.github, token from `gh auth token`
  set-sync-token --token ghp_xxx      # explicit token (else $PAT, else `gh auth token`)
  set-sync-token --org                # set it as an ORG-level Actions secret instead of repo-level
  set-sync-token --repo owner/name --name ORG_SYNC_TOKEN

The owner defaults to the owner of the current repo's `origin` remote. Uses the local `gh`
CLI (your admin auth); nothing here reaches other orgs. Stdlib only.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def resolve_token(explicit: str | None, env: dict) -> str:
    """--token, else $PAT, else the local gh session token. Never printed."""
    if explicit:
        return explicit.strip()
    if env.get("PAT"):
        return env["PAT"].strip()
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit("set-sync-token: no token (pass --token, set $PAT, or `gh auth login`)")
    return out.stdout.strip()


def detect_owner(env: dict) -> str:
    r = subprocess.run(["gh", "repo", "view", "--json", "owner", "-q", ".owner.login"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    raise SystemExit("set-sync-token: could not detect the owner; pass --repo owner/name or --org-name")


def gh_command(name: str, *, org: str | None, repo: str | None, visibility: str) -> list[str]:
    """The `gh secret set` invocation (token supplied on stdin, never on the command line)."""
    if org:
        return ["gh", "secret", "set", name, "--org", org, "--visibility", visibility]
    return ["gh", "secret", "set", name, "--repo", repo]


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    import os
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", help="the PAT/App token to store (default: $PAT, else `gh auth token`)")
    ap.add_argument("--name", default="ORG_SYNC_TOKEN", help="secret name (default: ORG_SYNC_TOKEN)")
    ap.add_argument("--repo", help="owner/name for a repo-level secret (default: <owner>/.github)")
    ap.add_argument("--org", dest="org_name", nargs="?", const="", help="set an ORG-level secret; optional org login (default: detected owner)")
    ap.add_argument("--visibility", default="all", choices=("all", "private", "selected"), help="org secret visibility (default: all)")
    ap.add_argument("--dry-run", action="store_true", help="print the command (without the token) and exit")
    a = ap.parse_args(argv)

    owner = None
    if a.org_name is not None:
        owner = a.org_name or detect_owner(env)
        cmd = gh_command(a.name, org=owner, repo=None, visibility=a.visibility)
        where = f"org '{owner}' (visibility: {a.visibility})"
    else:
        repo = a.repo or f"{detect_owner(env)}/.github"
        cmd = gh_command(a.name, org=None, repo=repo, visibility=a.visibility)
        where = f"repo '{repo}'"

    if a.dry_run:
        print(f"would set {a.name} on {where} via: {' '.join(cmd)} (token on stdin)")
        return 0

    token = resolve_token(a.token, env)
    r = subprocess.run(cmd, input=token, text=True)
    if r.returncode != 0:
        raise SystemExit(f"set-sync-token: `gh secret set` failed ({r.returncode})")
    print(f"set {a.name} on {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

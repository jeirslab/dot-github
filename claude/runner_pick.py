#!/usr/bin/env python3
"""Pick the runner label for a trusted org-level job (ADR-011, INFRA-290).

  ORG_RUNNER unset / ''   → hosted ("ubuntu-latest")
  ORG_RUNNER = auto       → the fleet label when the org (or repo) has an ONLINE runner
                            carrying it, else hosted with a warning; needs a token that can
                            list runners (ORG_ADMIN_TOKEN / ORG_READ_TOKEN), else hosted
  ORG_RUNNER = <label>    → that label, unconditionally

Prints the label; with --github-output also appends `label=` to $GITHUB_OUTPUT. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HOSTED = "ubuntu-latest"


def list_runners(owner: str, repo: str | None, token: str, api: str = "https://api.github.com") -> list[dict]:
    path = f"/repos/{owner}/{repo}/actions/runners" if repo else f"/orgs/{owner}/actions/runners"
    req = urllib.request.Request(f"{api}{path}?per_page=100", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("runners", [])


def online_with_label(runners: list[dict], label: str) -> list[str]:
    return [r["name"] for r in runners
            if r.get("status") == "online" and any(l.get("name") == label for l in r.get("labels", []))]


def pick(mode: str, fleet_label: str, owner: str, repo: str | None, token: str | None, fetch=list_runners) -> tuple[str, str]:
    """→ (label, reason)."""
    mode = (mode or "").strip()
    if not mode:
        return HOSTED, "ORG_RUNNER unset"
    if mode != "auto":
        return mode, "ORG_RUNNER forces the label"
    if not token:
        return HOSTED, "ORG_RUNNER=auto but no token can list runners (set ORG_ADMIN_TOKEN or ORG_READ_TOKEN)"
    try:
        runners = fetch(owner, repo, token)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return HOSTED, f"ORG_RUNNER=auto but listing runners failed ({e}); hosted fallback"
    names = online_with_label(runners, fleet_label)
    if names:
        return fleet_label, f"online: {', '.join(names)}"
    return HOSTED, f"ORG_RUNNER=auto but no online runner carries '{fleet_label}'; hosted fallback"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    ap.add_argument("--repo", default=None, help="list repository runners instead of organization runners")
    ap.add_argument("--fleet-label", default=os.environ.get("ORG_FLEET_LABEL") or "org-fleet")
    ap.add_argument("--github-output", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get("ORG_ADMIN_TOKEN") or os.environ.get("ORG_READ_TOKEN") or os.environ.get("GH_TOKEN")
    label, reason = pick(os.environ.get("ORG_RUNNER", ""), a.fleet_label, a.owner, a.repo, token)
    print(label)
    print(f"::notice::runner: {label} ({reason})" if label != HOSTED or "unset" in reason else f"::warning::runner: {label} ({reason})", file=sys.stderr)
    if a.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"label={label}\nreason={reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""org.toml — the per-repository declaration the org workflows read (ADR-003).

A repository says *what* to run and *where* to deploy; the org workflows say *when* and
*how*. Nothing in the workflows knows a repository's stack. Standard library only
(`tomllib`, Python 3.11+).

Usage (from a repository root, or with an explicit root):
  orgfile.py [root] --json                 # the effective declaration, defaults merged
  orgfile.py [root] gate.full              # one value, for shell: $(orgfile.py . gate.full)
  orgfile.py [root] --validate             # exit 1 with reasons if the file is unusable
  orgfile.py --example                     # print a starter org.toml
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

FILENAME = "org.toml"

DEFAULTS: dict[str, Any] = {
    "branches": {"integration": "staging", "deploy": ["main", "master"]},
    "gate": {
        "fast": "",            # command for push/PR into the integration branch
        "full": "",            # command for a PR into a deploy branch; defaults to fast
        "uses_nix": True,      # install nix on the runner before running gates
        "timeout_minutes": 30,
    },
    "deploy": {},              # deploy.<env> tables: command, runner, environment, health, health_timeout_minutes
    "artifacts": {"oci": {"attr": "", "registry": "ghcr.io", "name": ""}},
    "adr": {"required_paths": [".github/workflows/**", "migrations/**", "contracts/**", "nix/modules/**", "nix/hosts/**"]},
    "secrets": {"sops_paths": ["nix/secrets/**", "**/*.sops.*"], "allow_paths": []},
    "generated": {"files": [{"path": "flake.lock", "inputs": ["flake.nix"]}]},
    "pr": {"max_files": 60, "max_lines": 2000, "require_verification_section": True},
    "codeowners": {},          # "<glob>" = ["@org/team", "@user"]
    "labels": {"adr_waiver": "adr:not-needed", "auto_merge": "auto-merge"},
    "verify": {"ui": False, "command": ""},   # ui=true syncs the Playwright MCP in; command = post-deploy verification (optional)
    "jira": {                  # org Jira CLI (ADR-008): claude/jira/ reads this table; credentials never live here
        "base_url": "",        # or JIRA_BASE_URL in the environment
        "snapshot": "",        # default: $XDG_CACHE_HOME/org-jira/<site>/summary.json
        "sync": {"projects": [], "exclude_statuses": [], "since": "", "until": "", "date_field": "updated"},
        "standup": {"projects": [], "shared_projects": [], "instructions": "", "adr_dirs": ["docs/adr"]},
        "sweep": {"projects": [], "ignore_branches": ["dependabot/*", "renovate/*", "chore/*", "rollback/*", "revert/*", "sync/*"], "stale_days": 30},
    },
}

EXAMPLE = '''# org.toml — what the org workflows run for this repository (ADR-003).
# Every key is optional; defaults are in claude/orgfile.py of the org .github repo.

[branches]
integration = "staging"          # direct pushes allowed; fast gate runs here
deploy = ["main", "master"]      # PR-only; full gate + approval; a merge is a release

[gate]
fast = "nix flake check"         # push / PR into the integration branch
full = "nix flake check"         # PR into a deploy branch (defaults to fast)
uses_nix = true
timeout_minutes = 30

# [deploy.staging]               # runs after a push/merge to the integration branch
# command = "just deploy staging"
# runner = ["self-hosted", "tailnet"]
# environment = "staging"        # GitHub Environment: secrets + optional approvers
# health = "https://staging.example.internal/healthz"   # URL (2xx) or a command
# health_timeout_minutes = 10

# [deploy.production]            # runs after a PR merges into a deploy branch
# command = "just deploy production"
# runner = ["self-hosted", "tailnet"]
# environment = "production"     # required reviewers here = the named approver set (R9)
# health = "https://app.example/healthz"
# health_timeout_minutes = 15

# [artifacts.oci]
# attr = "packages.x86_64-linux.oci-image"   # nix attribute producing an OCI tarball
# registry = "ghcr.io"

[adr]
required_paths = [".github/workflows/**", "migrations/**", "contracts/**"]

[secrets]
sops_paths = ["nix/secrets/**", "**/*.sops.*"]
allow_paths = []                 # documented test vectors etc.

[generated]
files = [{ path = "flake.lock", inputs = ["flake.nix"] }]

[pr]
max_files = 60
max_lines = 2000
require_verification_section = true

[workflows]                      # which org workflows this repo runs (ADR-004); the org sync
# generates .github/workflows/org-<feature>.yml callers for each and prunes deselected ones.
enabled = ["gate", "guardrails"]  # options: gate, guardrails, release, deploy, oci, branch-guard

[codeowners]                     # sensitive paths → required reviewers (R9)
# "nix/hosts/pve/btc-*" = ["@ORG/bitcoin-ops"]
# "nix/secrets/**" = ["@ORG/security"]

[verify]
ui = false                       # true → the Playwright MCP is synced into .mcp.json for the org-verifier subagent
# command = "just verify-staging" # optional: what the verifier runs against a deployed environment

[jira]                           # org Jira CLI (ADR-008); credentials only from JIRA_EMAIL / JIRA_API_TOKEN
base_url = "https://ORG.atlassian.net"
[jira.sync]
projects = ["PROJ"]              # snapshot scope; also the keys recognised in branch names
[jira.standup]
projects = ["PROJ"]              # included wholesale; shared_projects = filtered to the caller
'''


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(root: Path | str = ".") -> dict[str, Any]:
    root = Path(root)
    path = root / FILENAME
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"{path}: {e}")
    cfg = _merge(DEFAULTS, data)
    if not cfg["gate"]["full"]:
        cfg["gate"]["full"] = cfg["gate"]["fast"]
    cfg["_present"] = path.is_file()
    return cfg


def validate(cfg: dict[str, Any]) -> list[str]:
    problems = []
    if not isinstance(cfg["branches"]["deploy"], list) or not cfg["branches"]["deploy"]:
        problems.append("branches.deploy must be a non-empty list")
    if cfg["branches"]["integration"] in cfg["branches"]["deploy"]:
        problems.append("branches.integration must not also be a deploy branch")
    for env, d in cfg["deploy"].items():
        if not isinstance(d, dict) or not d.get("command"):
            problems.append(f"deploy.{env}.command is required")
        if d.get("runner") is not None and not isinstance(d["runner"], list):
            problems.append(f"deploy.{env}.runner must be a list of runner labels")
    for g in cfg["generated"]["files"]:
        if not g.get("path") or not g.get("inputs"):
            problems.append("generated.files entries need path and inputs")
    for glob, owners in cfg["codeowners"].items():
        if not isinstance(owners, list) or not owners:
            problems.append(f"codeowners[{glob!r}] must be a non-empty list")
    j = cfg["jira"]
    if j.get("base_url") and not str(j["base_url"]).startswith(("http://", "https://")):
        problems.append("jira.base_url must start with http(s)://")
    for section in ("sync", "standup", "sweep"):
        if not isinstance(j[section].get("projects", []), list):
            problems.append(f"jira.{section}.projects must be a list of project keys")
    return problems


def get(cfg: dict[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return "" if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--example" in args:
        sys.stdout.write(EXAMPLE)
        return 0
    root = "."
    if args and not args[0].startswith("--"):
        root = args.pop(0)
    cfg = load(root)
    if "--validate" in args:
        problems = validate(cfg)
        for p in problems:
            print(f"org.toml: {p}")
        print("org.toml: ok" if not problems else f"org.toml: {len(problems)} problem(s)")
        return 1 if problems else 0
    if "--json" in args or not args:
        print(json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, indent=2))
        return 0
    print(_fmt(get(cfg, args[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

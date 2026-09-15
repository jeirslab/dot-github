"""Configuration for the org Jira CLI (ADR-008, INFRA-278).

Generic code, specific config. Nothing here names an organization. Resolution order for
every setting: environment variable → ``[jira]`` table of the nearest ``org.toml`` (walking
up from the current directory) → built-in default.

  JIRA_BASE_URL       https://<site>.atlassian.net            ([jira].base_url)
  JIRA_EMAIL          basic-auth username                     (never in a file)
  JIRA_API_TOKEN      API token                               (never in a file)
  ORG_JIRA_SNAPSHOT   snapshot path                           ([jira].snapshot)

The snapshot defaults to a per-site cache outside any repository
(``$XDG_CACHE_HOME/org-jira/<site>/summary.json``) so one sync serves every checkout on
the machine and nothing lands in a working tree by accident. A repository that wants a
committed snapshot sets ``[jira].snapshot`` to a path inside it.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ORG_FILE = "org.toml"

DEFAULTS: dict[str, Any] = {
    "base_url": "",
    "snapshot": "",
    "sync": {"projects": [], "exclude_statuses": [], "since": "", "until": "", "date_field": "updated"},
    "standup": {"projects": [], "shared_projects": [], "instructions": "", "adr_dirs": ["docs/adr"]},
    "sweep": {"projects": [],   # Jira project keys to recognise in branch names (default: sync.projects, then the snapshot's)
              "ignore_branches": ["dependabot/*", "renovate/*", "chore/*", "rollback/*", "revert/*", "sync/*"],
              "stale_days": 30},
}


def find_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ORG_FILE).is_file():
            return candidate
    return None


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Config:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).resolve() if root else find_root()
        data: dict = {}
        if self.root and (self.root / ORG_FILE).is_file():
            with open(self.root / ORG_FILE, "rb") as f:
                data = tomllib.load(f).get("jira", {}) or {}
        self.jira: dict = _merge(DEFAULTS, data)
        self.sync: dict = self.jira["sync"]
        self.standup: dict = self.jira["standup"]
        self.sweep: dict = self.jira["sweep"]

    # ---- connection -----------------------------------------------------------------
    @property
    def base_url(self) -> str:
        url = os.environ.get("JIRA_BASE_URL") or self.jira.get("base_url") or ""
        if not url:
            sys.exit("jira: set JIRA_BASE_URL or [jira].base_url in org.toml")
        if not url.startswith(("http://", "https://")):
            sys.exit(f"jira: base URL must start with http(s)://, got {url!r}")
        return url.rstrip("/")

    @property
    def base_url_or_none(self) -> str | None:
        return (os.environ.get("JIRA_BASE_URL") or self.jira.get("base_url") or "").rstrip("/") or None

    @property
    def site(self) -> str:
        return urlparse(self.base_url_or_none or "").netloc or "default"

    def credentials(self) -> tuple[str, str]:
        email = os.environ.get("JIRA_EMAIL")
        token = os.environ.get("JIRA_API_TOKEN")
        if not email or not token:
            sys.exit("jira: JIRA_EMAIL and JIRA_API_TOKEN must be set in the environment "
                     "(Infisical locally, repository secrets in Actions; never a file in the tree)")
        return email, token

    def has_credentials(self) -> bool:
        return bool(os.environ.get("JIRA_EMAIL") and os.environ.get("JIRA_API_TOKEN"))

    # ---- snapshot -------------------------------------------------------------------
    @property
    def snapshot_path(self) -> Path:
        env = os.environ.get("ORG_JIRA_SNAPSHOT")
        if env:
            return Path(env).expanduser()
        declared = self.jira.get("snapshot")
        if declared:
            base = self.root or Path.cwd()
            return (base / declared).resolve()
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        return cache / "org-jira" / self.site / "summary.json"

    def raw_snapshot_path(self) -> Path:
        p = self.snapshot_path
        return p.with_name("platform-state.json")

    def to_dict(self) -> dict:
        return {"root": str(self.root) if self.root else None,
                "base_url": self.base_url_or_none,
                "snapshot": str(self.snapshot_path), "credentials": self.has_credentials(),
                "sync": self.sync, "standup": self.standup, "sweep": self.sweep}

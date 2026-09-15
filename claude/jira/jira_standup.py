"""AI-assisted activity summary from Jira for a reporting window (port of INFRA-207/219).

Fetches issues updated in the window, buckets them (completed / new / in-flight / other),
gathers ADRs touched in the window as direction context, and pipes the JSON through
``claude -p`` guided by an instruction file. Scope comes from org.toml ``[jira.standup]``:
``projects`` are included wholesale, ``shared_projects`` are filtered to the caller's own
tickets unless ``--team``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from jira_client import JiraHttp
from jira_config import Config

FIELDS = ["summary", "status", "issuetype", "assignee", "created", "updated", "resolutiondate", "project", "labels", "parent"]
DEFAULT_INSTRUCTIONS = Path(__file__).with_name("standup-instructions.md")


def slim(issue: dict) -> dict:
    f = issue["fields"]
    return {"key": issue["key"], "project": f["project"]["key"], "type": f["issuetype"]["name"], "summary": f["summary"],
            "status": f["status"]["name"], "status_category": f["status"]["statusCategory"]["key"],
            "assignee": (f.get("assignee") or {}).get("displayName"), "created": f["created"][:10],
            "updated": f["updated"][:10], "resolved": (f.get("resolutiondate") or "")[:10] or None,
            "labels": f.get("labels") or [], "parent": (f.get("parent") or {}).get("key")}


def bucket(issues: list[dict], since: str, until: str) -> dict:
    completed, new, in_flight, other = [], [], [], []
    for it in issues:
        it["new_this_window"] = since <= it["created"] <= until
        if it["status_category"] == "done" and since <= (it["resolved"] or it["updated"]) <= until:
            completed.append(it)
        elif it["new_this_window"]:
            new.append(it)
        elif it["status_category"] == "indeterminate":
            in_flight.append(it)
        else:
            other.append(it)
    return {"completed": completed, "new": new, "in_flight": in_flight, "other_activity": other}


def recent_adrs(dirs: list[str], root: Path | None, since: str, until: str) -> list[dict]:
    if root is None:
        return []
    adrs: list[dict] = []
    for d in dirs:
        try:
            out = subprocess.run(["git", "-C", str(root), "log", f"--since={since}", f"--until={until} 23:59",
                                  "--name-only", "--pretty=format:", "--", d], capture_output=True, text=True, timeout=15)
            paths = sorted({p for p in out.stdout.splitlines() if p.strip().endswith(".md")})
        except Exception:
            continue
        for rel in paths[:8]:
            try:
                adrs.append({"file": rel, "excerpt": (root / rel).read_text()[:2500]})
            except OSError:
                continue
    return adrs[:8]


def jql_for(cfg: Config, since: str, until: str, mine: bool, team: bool, projects: str | None) -> str:
    own, shared = cfg.standup.get("projects", []), cfg.standup.get("shared_projects", [])
    if projects:
        keys = [p.strip().upper() for p in projects.split(",") if p.strip()]
        scope = f"project in ({', '.join(keys)})"
    elif team or not shared or not own:
        keys = [*own, *shared]
        scope = f"project in ({', '.join(keys)})"
    else:
        scope = f"(project in ({', '.join(own)}) OR (project in ({', '.join(shared)}) AND assignee = currentUser()))"
        keys = [*own, *shared]
    if not keys:
        sys.exit("jira standup: no projects configured — set [jira.standup].projects in org.toml or pass --projects")
    jql = f'{scope} AND updated >= "{since}" AND updated <= "{until} 23:59"'
    if mine:
        jql += " AND assignee = currentUser()"
    return jql + " ORDER BY project, updated DESC"


def build_context(cfg: Config, since: str, until: str, mine: bool, team: bool, projects: str | None,
                  extra: str | None) -> dict:
    jql = jql_for(cfg, since, until, mine, team, projects)
    email, token = cfg.credentials()
    print(f"JQL: {jql}", file=sys.stderr)
    raw = JiraHttp(cfg.base_url, email, token).search(jql, FIELDS)
    print(f"Fetched {len(raw)} issues.", file=sys.stderr)
    return {"window": {"since": since, "until": until}, "scope": "org" if team else "mine", "extra_context": extra,
            "recent_adrs": recent_adrs(cfg.standup.get("adr_dirs", []), cfg.root, since, until),
            **bucket([slim(i) for i in raw], since, until)}


def summarize(cfg: Config, context: dict, model: str | None, instructions: str | None) -> str:
    path = Path(instructions) if instructions else (
        (cfg.root / cfg.standup["instructions"]) if cfg.standup.get("instructions") and cfg.root else DEFAULT_INSTRUCTIONS)
    prompt = f"{path.read_text()}\n\n---\n\nJira activity data (JSON):\n\n```json\n{json.dumps(context, indent=2, ensure_ascii=False)}\n```\n"
    claude = shutil.which("claude")
    if not claude:
        sys.exit("jira standup: `claude` CLI not found on PATH (use --dry-run to get the JSON context instead)")
    cmd = [claude, "-p"] + (["--model", model] if model else [])
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        sys.exit(f"jira standup: claude exited {r.returncode}: {r.stderr.strip()[:500]}")
    return r.stdout.strip()


def window(weeks: int, since: str | None, until: str | None) -> tuple[str, str]:
    until_date = until or date.today().isoformat()
    return since or (date.fromisoformat(until_date) - timedelta(weeks=weeks)).isoformat(), until_date

#!/usr/bin/env python3
"""Branch ↔ ticket sweep (ADR-008, INFRA-278): what work is pending merge, per person.

For every repository it lists the branches, extracts Jira keys from branch names
(``INFRA-237``, ``feature/LEGACY-234-thing``, ``claude/infra-12-x`` — case-insensitive),
and reports each ticket branch with: ahead/behind the integration branch, last commit
age and author, open PR, and the ticket's summary / status / assignee from Jira. Ticket
data comes from the live API when credentials exist (``--offline`` forces the snapshot).

Flags per branch:
  merged        nothing ahead of the base → delete candidate
  ticket-done   ticket is Done but the branch still carries unmerged commits
  stale         no commit for `stale_days` and no open PR
  in-progress-merged  branch merged but the ticket still says In Progress → close it (skill org-jira)
  no-ticket     key not found in Jira (typo, deleted, or a project the credential can't see)
  unassigned    ticket has no assignee

GitHub access: GH_TOKEN / GITHUB_TOKEN (needs read on every repository swept; the default
Actions token only reads its own repository). Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime
import fnmatch
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from jira_config import Config
from jira_models import Catalog

KEY_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,9})-(\d+)(?![0-9])")
API = "https://api.github.com"


# ---- GitHub -----------------------------------------------------------------------------------

class GitHub:
    def __init__(self, token: str | None, api: str = API) -> None:
        self.token, self.api = token, api.rstrip("/")

    def get(self, path: str) -> tuple[int, object]:
        req = urllib.request.Request(self.api + path, headers={"Accept": "application/vnd.github+json",
                                                               "X-GitHub-Api-Version": "2022-11-28",
                                                               **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            return e.code, None

    def paged(self, path: str, per_page: int = 100) -> list:
        out: list = []
        page = 1
        sep = "&" if "?" in path else "?"
        while True:
            code, data = self.get(f"{path}{sep}per_page={per_page}&page={page}")
            if code != 200 or not isinstance(data, list):
                if page == 1 and code != 200:
                    raise RuntimeError(f"GET {path} -> HTTP {code}")
                return out
            out.extend(data)
            if len(data) < per_page:
                return out
            page += 1

    def org_repos(self, org: str) -> list[str]:
        try:
            repos = self.paged(f"/orgs/{org}/repos?type=all&sort=full_name")
        except RuntimeError:
            repos = self.paged(f"/users/{org}/repos?sort=full_name")
        return [r["full_name"] for r in repos if not r.get("archived")]

    def repo(self, full: str) -> dict:
        code, data = self.get(f"/repos/{full}")
        if code != 200:
            raise RuntimeError(f"cannot read {full} (HTTP {code}); does the token have access?")
        return data  # type: ignore[return-value]

    def org_toml(self, full: str, ref: str) -> dict:
        code, data = self.get(f"/repos/{full}/contents/org.toml?ref={urllib.parse.quote(ref)}")
        if code != 200 or not isinstance(data, dict) or data.get("encoding") != "base64":
            return {}
        try:
            return tomllib.loads(base64.b64decode(data["content"]).decode())
        except Exception:
            return {}

    def branches(self, full: str) -> list[dict]:
        return self.paged(f"/repos/{full}/branches")

    def compare(self, full: str, base: str, head: str) -> dict | None:
        code, data = self.get(f"/repos/{full}/compare/{urllib.parse.quote(base, safe='')}...{urllib.parse.quote(head, safe='')}")
        return data if code == 200 and isinstance(data, dict) else None

    def commit(self, full: str, sha: str) -> dict | None:
        code, data = self.get(f"/repos/{full}/commits/{sha}")
        return data if code == 200 and isinstance(data, dict) else None

    def open_prs(self, full: str, branch: str) -> list[dict]:
        owner = full.split("/")[0]
        code, data = self.get(f"/repos/{full}/pulls?state=open&head={urllib.parse.quote(owner + ':' + branch, safe='')}")
        return data if code == 200 and isinstance(data, list) else []


# ---- Jira -------------------------------------------------------------------------------------

def ticket_info_live(cfg: Config, keys: list[str]) -> dict[str, dict]:
    from jira_client import JiraError, JiraHttp
    email, token = cfg.credentials()
    http = JiraHttp(cfg.base_url, email, token)
    info: dict[str, dict] = {}
    for i in range(0, len(keys), 50):
        chunk = keys[i:i + 50]
        try:
            issues = http.search(f"key in ({', '.join(chunk)})", ["summary", "status", "assignee", "updated", "issuetype"])
        except JiraError as e:
            if e.status == 400:  # one bad key poisons the whole query; retry one by one
                issues = []
                for k in chunk:
                    try:
                        issues += http.search(f"key = {k}", ["summary", "status", "assignee", "updated", "issuetype"])
                    except JiraError:
                        pass
            else:
                raise
        for it in issues:
            f = it["fields"]
            info[it["key"]] = {"summary": f.get("summary"), "status": (f.get("status") or {}).get("name"),
                               "status_category": ((f.get("status") or {}).get("statusCategory") or {}).get("key"),
                               "assignee": (f.get("assignee") or {}).get("displayName"),
                               "type": (f.get("issuetype") or {}).get("name"), "updated": (f.get("updated") or "")[:10]}
    return info


ACTIVE_WORDS = ("in progress", "in review", "review", "in development", "testing")
DONE_WORDS = ("done", "closed", "resolved", "abandon", "abandoned", "cancelled", "canceled", "won't do", "wont do")


def ticket_info_snapshot(catalog: Catalog, keys: list[str]) -> dict[str, dict]:
    info: dict[str, dict] = {}
    for k in keys:
        n = catalog.issue(k)
        if n is None:
            continue
        member = catalog.person(n.get("assignee"))
        status = n.get("status") or ""
        cat = "done" if status.lower() in DONE_WORDS else ("indeterminate" if status.lower() in ACTIVE_WORDS else "new")
        info[k] = {"summary": n.get("summary"), "status": status, "status_category": cat,
                   "assignee": member.get("displayName") if member else None,
                   "type": n.get("type"), "updated": (n.get("updated") or "")[:10]}
    return info


# ---- sweep ------------------------------------------------------------------------------------

def keys_in(branch: str, projects: list[str] | None) -> list[str]:
    """Jira keys in a branch name. With a known project list, matches are filtered to it
    (any case). Without one, only an upper-case prefix counts, so ``release-2026-09`` or
    ``v1-2`` are not mistaken for tickets while ``INFRA-12`` still is."""
    known = {p.upper() for p in projects} if projects else None
    found = []
    for m in KEY_RE.finditer(branch):
        prefix, num = m.group(1), m.group(2)
        if known is not None:
            if prefix.upper() not in known:
                continue
        elif not prefix.isupper():
            continue
        key = f"{prefix.upper()}-{num}"
        if key not in found:
            found.append(key)
    return found


def age_days(iso: str | None, now: datetime.datetime) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (now - dt).days)


def sweep_repo(gh: GitHub, full: str, ignore: list[str], now: datetime.datetime,
               projects: list[str] | None = None) -> tuple[list[dict], list[dict], dict]:
    meta = gh.repo(full)
    default = meta.get("default_branch") or "main"
    decl = gh.org_toml(full, default).get("branches", {})
    deploy = decl.get("deploy") or ["main", "master"]
    integration = decl.get("integration") or "staging"
    names = {b["name"]: b for b in gh.branches(full)}
    base = integration if integration in names else default
    long_lived = {default, integration, *deploy}
    linked, unlinked = [], []
    for name, b in sorted(names.items()):
        if name in long_lived or any(fnmatch.fnmatch(name, pat) for pat in ignore):
            continue
        keys = keys_in(name, projects)
        commit = gh.commit(full, b["commit"]["sha"]) or {}
        cdate = ((commit.get("commit") or {}).get("committer") or {}).get("date")
        entry = {"repo": full, "branch": name, "base": base, "tickets": keys, "sha": b["commit"]["sha"][:7],
                 "last_commit": cdate, "age_days": age_days(cdate, now),
                 "author": (commit.get("author") or {}).get("login") or ((commit.get("commit") or {}).get("author") or {}).get("name")}
        if not keys:
            unlinked.append(entry); continue
        cmp = gh.compare(full, base, name) or {}
        entry.update({"ahead": cmp.get("ahead_by"), "behind": cmp.get("behind_by")})
        prs = gh.open_prs(full, name)
        entry["pr"] = {"number": prs[0]["number"], "url": prs[0]["html_url"], "draft": prs[0].get("draft", False),
                       "base": prs[0]["base"]["ref"]} if prs else None
        linked.append(entry)
    return linked, unlinked, {"repo": full, "default_branch": default, "base": base, "branches": len(names)}


def annotate(entries: list[dict], info: dict[str, dict], stale_days: int, have_info: bool = True) -> None:
    for e in entries:
        e["ticket_info"] = {k: info.get(k) for k in e["tickets"]}
        flags = []
        if e.get("ahead") == 0:
            flags.append("merged")
        for k in e["tickets"]:
            t = info.get(k)
            if t is None:
                if have_info:
                    flags.append("no-ticket")
            else:
                if t.get("status_category") == "done" and (e.get("ahead") or 0) > 0:
                    flags.append("ticket-done")
                if t.get("status_category") == "indeterminate" and e.get("ahead") == 0:
                    flags.append("in-progress-merged")
                if not t.get("assignee"):
                    flags.append("unassigned")
        if (e.get("age_days") or 0) >= stale_days and not e.get("pr") and e.get("ahead") != 0:
            flags.append("stale")
        e["flags"] = sorted(set(flags))
        owners = [info[k]["assignee"] for k in e["tickets"] if info.get(k) and info[k].get("assignee")]
        e["owner"] = owners[0] if owners else (e.get("author") or "unknown")


def run(gh: GitHub, cfg: Config, repos: list[str], offline: bool, stale_days: int | None = None,
        ignore: list[str] | None = None, ticket_lookup=None) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    ignore = ignore if ignore is not None else cfg.sweep.get("ignore_branches", [])
    stale = stale_days if stale_days is not None else int(cfg.sweep.get("stale_days", 30))
    projects = list(cfg.sweep.get("projects") or cfg.sync.get("projects") or [])
    snapshot = Catalog.load(cfg.snapshot_path) if cfg.snapshot_path.is_file() else None
    if not projects and snapshot is not None:
        projects = snapshot.project_keys()
    linked, unlinked, repo_rows, errors = [], [], [], []
    for full in repos:
        try:
            l, u, row = sweep_repo(gh, full, ignore, now, projects or None)
            linked += l; unlinked += u; repo_rows.append(row)
        except RuntimeError as e:
            errors.append(str(e))
    keys = sorted({k for e in linked for k in e["tickets"]})
    source = "none"
    info: dict[str, dict] = {}
    if keys:
        if ticket_lookup is not None:
            info, source = ticket_lookup(keys), "injected"
        elif not offline and cfg.has_credentials():
            info, source = ticket_info_live(cfg, keys), "live"
        elif snapshot is not None:
            info, source = ticket_info_snapshot(snapshot, keys), f"snapshot {cfg.snapshot_path}"
        else:
            errors.append("no Jira credentials and no snapshot; ticket columns are empty")
    annotate(linked, info, stale, have_info=source != "none")
    by_owner = collections.Counter(e["owner"] for e in linked)
    return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ticket_source": source, "stale_days": stale,
            "projects": projects,
            "repos": repo_rows, "branches": sorted(linked, key=lambda e: (e["owner"], e["repo"], e["branch"])),
            "unlinked": sorted(unlinked, key=lambda e: (e["repo"], e["branch"])),
            "stats": {"repos": len(repo_rows), "ticket_branches": len(linked), "unlinked_branches": len(unlinked),
                      "tickets": len(keys), "by_owner": dict(by_owner),
                      "flags": dict(collections.Counter(f for e in linked for f in e["flags"]))},
            "errors": errors}


def markdown(report: dict, base_url: str | None = None) -> str:
    s = report["stats"]
    out = [f"# Branch ↔ ticket sweep", "",
           f"Generated {report['generated_at']} · {s['repos']} repositories · {s['ticket_branches']} ticket branches "
           f"({s['tickets']} tickets) · {s['unlinked_branches']} branches without a ticket key · ticket data: {report['ticket_source']}", ""]
    if report["errors"]:
        out += ["> " + e for e in report["errors"]] + [""]
    for owner in sorted({e["owner"] for e in report["branches"]}, key=str.lower):
        rows = [e for e in report["branches"] if e["owner"] == owner]
        out += [f"## {owner} ({len(rows)})", "", "| Repository | Branch | Ticket | Status | Ahead / behind `base` | Last commit | PR | Flags |", "|---|---|---|---|---|---|---|---|"]
        for e in rows:
            tickets = []
            for k in e["tickets"]:
                t = e["ticket_info"].get(k)
                label = f"[{k}]({base_url}/browse/{k})" if base_url else f"`{k}`"
                tickets.append(f"{label} {t['summary'][:50]}" if t and t.get("summary") else label)
            status = ", ".join((e["ticket_info"].get(k) or {}).get("status") or "?" for k in e["tickets"])
            pr = f"[#{e['pr']['number']}]({e['pr']['url']}){' (draft)' if e['pr']['draft'] else ''}" if e.get("pr") else ""
            last = f"{e['age_days']}d ago" if e.get("age_days") is not None else "?"
            ab = f"+{e.get('ahead', '?')} / -{e.get('behind', '?')} `{e['base']}`"
            out.append(f"| `{e['repo'].split('/')[-1]}` | `{e['branch']}` | {'<br>'.join(tickets)} | {status} | {ab} | {last} ({e.get('author') or '?'}) | {pr} | {', '.join(e['flags'])} |")
        out.append("")
    if report["unlinked"]:
        out += [f"## Branches without a ticket key ({len(report['unlinked'])})", ""]
        for e in report["unlinked"][:40]:
            out.append(f"- `{e['repo'].split('/')[-1]}` `{e['branch']}` — {e['age_days'] if e.get('age_days') is not None else '?'}d ({e.get('author') or '?'})")
        if len(report["unlinked"]) > 40:
            out.append(f"- … {len(report['unlinked']) - 40} more")
        out.append("")
    if s["flags"]:
        out += ["## Flags", ""] + [f"- **{k}**: {v}" for k, v in sorted(s["flags"].items())] + [""]
    return "\n".join(out)


def read_repos_file(path: Path) -> list[str]:
    return [l.split("#")[0].strip() for l in path.read_text().splitlines() if l.split("#")[0].strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--org", help="GitHub organization (needed for --all; otherwise derived from --repos)")
    ap.add_argument("--repos", help="comma-separated owner/name list")
    ap.add_argument("--repos-file", type=Path, help="file with one owner/name per line (# comments)")
    ap.add_argument("--all", action="store_true", help="every non-archived repository of --org the token can see")
    ap.add_argument("--offline", action="store_true", help="ticket data from the snapshot only (no Jira calls)")
    ap.add_argument("--stale-days", type=int, default=None)
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--out", type=Path, help="write the report here as well")
    ap.add_argument("--json-out", type=Path, help="also write the JSON report here")
    ap.add_argument("--root", default=None, help="directory whose org.toml [jira] to read")
    a = ap.parse_args(argv)
    cfg = Config(a.root)
    gh = GitHub(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    repos: list[str] = []
    if a.repos:
        repos += [r.strip() for r in a.repos.split(",") if r.strip()]
    if a.repos_file:
        repos += read_repos_file(a.repos_file)
    if a.all:
        if not a.org:
            ap.error("--all needs --org")
        repos += gh.org_repos(a.org)
    repos = list(dict.fromkeys(repos))
    if not repos:
        ap.error("nothing to sweep: pass --repos, --repos-file, or --all --org")
    report = run(gh, cfg, repos, a.offline, a.stale_days)
    base = os.environ.get("JIRA_BASE_URL") or cfg.jira.get("base_url") or None
    text = json.dumps(report, indent=2) if a.format == "json" else markdown(report, base)
    print(text)
    if a.out:
        a.out.write_text(text + ("\n" if not text.endswith("\n") else ""))
    if a.json_out:
        a.json_out.write_text(json.dumps(report, indent=2) + "\n")
    return 1 if report["errors"] and not report["repos"] else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Capture Jira state into a JSON snapshot (port of the docs repo exporter, INFRA-219 → INFRA-278).

  raw     the REST responses verbatim (platform-state.json next to the snapshot)
  simple  a trimmed tree per project: epic → story → subtask, key/type/summary/status/
          parentKey/assignee (+ flattened comments), people in a top-level ``team`` table.
          This is the snapshot every other command reads.

``--search KEYWORD...`` keeps matching issues plus their ancestors and subtree so the tree
stays readable. Credentials and base URL come from jira_config (environment / org.toml).
Uses POST /rest/api/3/search/jql with nextPageToken pagination.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import html
import json
import sys
from pathlib import Path

from jira_client import JiraError, JiraHttp
from jira_config import Config
from jira_models import short_id

PAGE_SIZE = 100


def fetch_projects(http: JiraHttp) -> list[dict]:
    projects: list[dict] = []
    start = 0
    while True:
        page = http.get(f"/rest/api/3/project/search?startAt={start}&maxResults={PAGE_SIZE}") or {}
        values = page.get("values", [])
        projects.extend(values)
        if page.get("isLast", True) or not values:
            return projects
        start += len(values)


def fetch_issues(http: JiraHttp, jql: str) -> list[dict]:
    issues: list[dict] = []
    token: str | None = None
    while True:
        body: dict = {"jql": jql, "fields": ["*all"], "maxResults": PAGE_SIZE}
        if token:
            body["nextPageToken"] = token
        page = http.post("/rest/api/3/search/jql", body) or {}
        issues.extend(page.get("issues", []))
        print(f"  fetched {len(issues)} issues...", file=sys.stderr)
        token = page.get("nextPageToken")
        if page.get("isLast") or not token:
            return issues


# ---- simplified transform ---------------------------------------------------------------

def key_sort(key: str) -> tuple:
    proj, _, num = (key or "").partition("-")
    try:
        return (proj, int(num))
    except ValueError:
        return (proj, 0)


def parse_dt(ts: str | None) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts)
    except ValueError:
        if len(ts) >= 5 and ts[-5] in "+-" and ts[-3] != ":":
            try:
                return datetime.datetime.fromisoformat(ts[:-2] + ":" + ts[-2:])
            except ValueError:
                return None
    return None


def utc_str(ts: str | None) -> str | None:
    dt = parse_dt(ts)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def flatten_adf(node) -> str:
    """Best-effort ADF → text: paragraph/heading/list breaks kept, mentions as @name."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten_adf(n) for n in node)
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return "@" + node.get("attrs", {}).get("text", "").lstrip("@")
    rendered = flatten_adf(node.get("content", []))
    return rendered + "\n" if kind in ("paragraph", "heading", "listItem", "blockquote", "codeBlock") else rendered


def build_team(issues: list[dict]) -> list[dict]:
    """People seen in any role; per-project stats count assignments only. Unassigned last."""
    people: dict[str, dict] = collections.OrderedDict()

    def note(user) -> None:
        if user and user.get("accountId"):
            entry = people.setdefault(user["accountId"], {"displayName": None, "email": None})
            entry["displayName"] = user.get("displayName") or entry["displayName"]
            entry["email"] = user.get("emailAddress") or entry["email"]

    def proj_stat() -> dict:
        return {"total": 0, "byStatus": collections.Counter(), "last_dt": None, "last_key": None}

    stats: dict = collections.defaultdict(lambda: collections.defaultdict(proj_stat))
    for issue in issues:
        f = issue.get("fields", {})
        note(f.get("assignee")); note(f.get("reporter"))
        for c in f.get("comment", {}).get("comments", []):
            note(c.get("author"))
        aid = f["assignee"]["accountId"] if f.get("assignee") else None
        b = stats[aid][issue["key"].split("-")[0]]
        b["total"] += 1
        b["byStatus"][f.get("status", {}).get("name")] += 1
        dt = parse_dt(f.get("updated"))
        if dt is not None:
            dt = dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
            if b["last_dt"] is None or dt > b["last_dt"]:
                b["last_dt"], b["last_key"] = dt, issue["key"]

    def block(by_proj: dict) -> dict:
        out = collections.OrderedDict()
        for proj in sorted(by_proj):
            s = by_proj[proj]
            out[proj] = {"total": s["total"], "byStatus": dict(s["byStatus"]),
                         "lastUpdated": s["last_dt"].astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if s["last_dt"] else None,
                         "lastUpdatedKey": s["last_key"]}
        return out

    team = [{"accountId": aid, "shortId": short_id(aid), "displayName": info["displayName"], "email": info["email"],
             "stats": block(stats.get(aid, {}))} for aid, info in people.items()]
    if None in stats:
        team.append({"accountId": None, "shortId": None, "displayName": "Unassigned", "email": None, "stats": block(stats[None])})
    return team


def issue_haystack(issue: dict) -> str:
    f = issue.get("fields", {})
    parts = [issue.get("key", ""), f.get("summary") or "", *(f.get("labels") or [])]
    parts += [flatten_adf(c.get("body")) for c in f.get("comment", {}).get("comments", [])]
    return html.unescape(" ".join(parts)).lower()


def filter_by_search(issues: list[dict], terms: list[str]) -> list[dict]:
    parent_of: dict[str, str] = {}
    children: dict[str, list[str]] = collections.defaultdict(list)
    by_key = {i["key"]: i for i in issues}
    for issue in issues:
        p = issue["fields"].get("parent")
        if p and p.get("key"):
            parent_of[issue["key"]] = p["key"]; children[p["key"]].append(issue["key"])
    keep: set[str] = set()
    for key in [i["key"] for i in issues if any(t in issue_haystack(i) for t in terms)]:
        cur: str | None = key
        while cur and cur in by_key and cur not in keep:
            keep.add(cur); cur = parent_of.get(cur)
        stack = list(children.get(key, []))
        while stack:
            child = stack.pop()
            if child not in keep:
                keep.add(child); stack.extend(children.get(child, []))
    return [i for i in issues if i["key"] in keep]


def simple_node(issue: dict, by_key: dict, children: dict) -> dict:
    f = issue["fields"]; parent = f.get("parent")
    node: dict = {"key": issue["key"], "type": f["issuetype"]["name"], "summary": html.unescape(f.get("summary") or ""),
                  "status": f.get("status", {}).get("name"), "updated": utc_str(f.get("updated")),
                  "parentKey": parent.get("key") if parent else None}
    if f.get("assignee"):
        node["assignee"] = short_id(f["assignee"]["accountId"])
    comments = []
    for c in f.get("comment", {}).get("comments", []):
        created = utc_str(c.get("created"))
        comments.append({"author": short_id((c.get("author") or {}).get("accountId")),
                         "created": created[:10] if created else None,
                         "body": html.unescape(flatten_adf(c.get("body")).strip())})
    if comments:
        node["comments"] = comments
    kids = [simple_node(by_key[k], by_key, children) for k in sorted(children.get(issue["key"], []), key=key_sort) if k in by_key]
    if kids:
        node["children"] = kids
    return node


def simplify_snapshot(snapshot: dict, search_terms: list[str] | None = None) -> dict:
    issues = snapshot["issues"]
    if search_terms:
        issues = filter_by_search(issues, [t.lower() for t in search_terms])
    by_key = {i["key"]: i for i in issues}
    children: dict[str, list[str]] = collections.defaultdict(list)
    for issue in issues:
        p = issue["fields"].get("parent")
        if p and p.get("key"):
            children[p["key"]].append(issue["key"])
    names = {p["key"]: p.get("name", p["key"]) for p in snapshot.get("projects", [])}
    buckets: dict[str, dict] = collections.OrderedDict()
    for issue in issues:
        proj = issue["key"].split("-")[0]
        buckets.setdefault(proj, {"epics": [], "issues": []})
        p = issue["fields"].get("parent")
        if p and p.get("key") in by_key:
            continue
        node = simple_node(issue, by_key, children)
        buckets[proj]["epics" if issue["fields"]["issuetype"]["name"].lower() == "epic" else "issues"].append(node)
    projects = []
    for proj in sorted(buckets):
        entry = {"key": proj, "name": names.get(proj, proj),
                 "epics": sorted(buckets[proj]["epics"], key=lambda n: key_sort(n["key"]))}
        if buckets[proj]["issues"]:
            entry["issues"] = sorted(buckets[proj]["issues"], key=lambda n: key_sort(n["key"]))
        projects.append(entry)
    out: dict = {"exported_at": snapshot["exported_at"], "base_url": snapshot.get("base_url")}
    if search_terms:
        out["search"] = list(search_terms)
    out.update({"issue_count": len(issues), "team": build_team(issues), "projects": projects})
    return out


# ---- entry ----------------------------------------------------------------------------------

def export(cfg: Config, fmt: str = "simple", search: list[str] | None = None, jql: str | None = None,
           output: Path | None = None) -> Path:
    email, token = cfg.credentials()
    http = JiraHttp(cfg.base_url, email, token)
    print(f"Exporting Jira state from {cfg.base_url}", file=sys.stderr)
    projects = fetch_projects(http)
    print(f"  {len(projects)} projects", file=sys.stderr)
    if jql is None:  # Jira Cloud rejects unbounded JQL on /search/jql
        keys = [p["key"] for p in projects if p.get("key")]
        if not keys:
            raise JiraError("no visible projects to export — check the credential's permissions")
        jql = f"project IN ({', '.join(keys)}) ORDER BY created ASC"
    issues = fetch_issues(http, jql)
    snapshot = {"exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "base_url": cfg.base_url,
                "jql": jql, "project_count": len(projects), "issue_count": len(issues), "projects": projects, "issues": issues}
    if fmt == "simple":
        payload: dict = simplify_snapshot(snapshot, search)
        print(f"  simplified -> {payload['issue_count']} issues, {len(payload['team'])} people", file=sys.stderr)
        out = output or cfg.snapshot_path
    else:
        payload = snapshot
        if search:
            kept = filter_by_search(snapshot["issues"], [t.lower() for t in search])
            payload = {**snapshot, "search": search, "issues": kept, "issue_count": len(kept)}
        out = output or cfg.raw_snapshot_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out}", file=sys.stderr)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dump Jira state to JSON.")
    ap.add_argument("--format", choices=["raw", "simple"], default="simple")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--search", nargs="+", metavar="KEYWORD", default=None)
    ap.add_argument("--jql", default=None)
    ap.add_argument("--root", default=None, help="directory whose org.toml [jira] to read (default: walk up from cwd)")
    a = ap.parse_args(argv)
    try:
        export(Config(a.root), a.format, a.search, a.jql, a.output)
    except JiraError as e:
        print(f"jira-export: {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

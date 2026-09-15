"""Indexed view over the simplified Jira snapshot (summary.json). Standard library only.

The snapshot is a plain JSON tree written by ``jira_export.py --format simple``; this
module wraps it with O(1) lookups (issue by key, person by id/name) and the local patches
that mirror a write that already succeeded in Jira. Jira is the source of truth; the
snapshot is a cache, never merged back.

Shape (camelCase, as the exporter writes it):

  {"exported_at", "base_url", "issue_count",
   "team": [{"accountId", "shortId", "displayName", "email", "stats": {...}}],
   "projects": [{"key", "name", "epics": [node], "issues": [node]}]}
  node = {"key", "type", "summary", "status", "updated", "parentKey", "assignee"(shortId),
          "comments": [{"author", "created", "body"}], "children": [node]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

Node = dict[str, Any]
Member = dict[str, Any]


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.get("children", []) or []:
        yield from walk(child)


def short_id(account_id: str | None) -> str | None:
    return account_id.split("-")[0] if account_id else None


class Catalog:
    def __init__(self, summary: dict, path: Path | None = None) -> None:
        self.summary = summary
        self.path = path
        self._reindex()

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        path = Path(path)
        return cls(json.loads(path.read_text(encoding="utf-8")), path=path)

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path or self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ---- indexes ----------------------------------------------------------------------
    def _reindex(self) -> None:
        self._issues: dict[str, Node] = {}
        self._parent_of: dict[str, str | None] = {}
        for project in self.summary.get("projects", []):
            for root in [*project.get("epics", []), *project.get("issues", [])]:
                for node in walk(root):
                    self._issues[node["key"]] = node
                    self._parent_of[node["key"]] = node.get("parentKey")
        self._projects: dict[str, dict] = {p["key"]: p for p in self.summary.get("projects", [])}
        self._people: dict[str, Member] = {}
        for m in self.summary.get("team", []):
            for handle in (m.get("accountId"), m.get("shortId"), m.get("email")):
                if handle:
                    self._people[handle.lower()] = m
            if m.get("displayName"):
                self._people[m["displayName"].lower()] = m

    # ---- reads ------------------------------------------------------------------------
    @property
    def team(self) -> list[Member]:
        return self.summary.get("team", [])

    def project_keys(self) -> list[str]:
        return list(self._projects)

    def issues(self) -> Iterator[Node]:
        return iter(self._issues.values())

    def issue(self, key: str) -> Node | None:
        return self._issues.get(key)

    def project_of(self, key: str) -> dict | None:
        return self._projects.get(key.split("-")[0])

    def resolve_person(self, query: str | None) -> Member | None:
        """accountId / shortId / email / display name (or unique substring) → member."""
        if not query:
            return None
        hit = self._people.get(query.lower())
        if hit:
            return hit
        matches = [m for m in self.team if m.get("displayName") and query.lower() in m["displayName"].lower()]
        return matches[0] if len(matches) == 1 else None

    def person(self, short: str | None) -> Member | None:
        return self.resolve_person(short)

    def assignee_of(self, key: str) -> Member | None:
        node = self.issue(key)
        return self.resolve_person(node.get("assignee")) if node and node.get("assignee") else None

    def find(self, term: str) -> list[Node]:
        t = term.lower()
        return [n for n in self._issues.values()
                if t in n["key"].lower() or t in (n.get("summary") or "").lower()]

    # ---- local patches (only after the Jira write succeeded) --------------------------
    def _require(self, key: str) -> Node:
        node = self.issue(key)
        if node is None:
            raise KeyError(f"{key} not in snapshot — run `jira sync` first")
        return node

    def apply_assignment(self, key: str, member: Member | None) -> Node:
        node = self._require(key)
        node["assignee"] = member.get("shortId") if member else None
        return node

    def apply_comment(self, key: str, author: Member | None, body: str, created: str) -> dict:
        node = self._require(key)
        comment = {"author": author.get("shortId") if author else None, "created": created, "body": body}
        node.setdefault("comments", []).append(comment)
        return comment

    def apply_status(self, key: str, status: str, updated: str | None = None) -> Node:
        node = self._require(key)
        node["status"] = status
        if updated:
            node["updated"] = updated
        return node

    def apply_summary(self, key: str, summary: str, updated: str | None = None) -> Node:
        node = self._require(key)
        node["summary"] = summary
        if updated:
            node["updated"] = updated
        return node

    def add_issue(self, node: Node) -> Node:
        project = self.project_of(node["key"])
        if project is None:
            raise KeyError(f"no project for {node['key']} in snapshot — run `jira sync` first")
        parent = self.issue(node["parentKey"]) if node.get("parentKey") else None
        if parent is not None:
            parent.setdefault("children", []).append(node)
        elif (node.get("type") or "").lower() == "epic":
            project.setdefault("epics", []).append(node)
        else:
            project.setdefault("issues", []).append(node)
        self._issues[node["key"]] = node
        self.summary["issue_count"] = self.summary.get("issue_count", 0) + 1
        return node

    def remove_issue(self, key: str) -> Node:
        node = self._require(key)
        parent = self.issue(node["parentKey"]) if node.get("parentKey") else None
        if parent is not None:
            parent["children"] = [c for c in parent.get("children", []) if c["key"] != key]
        else:
            project = self.project_of(key)
            if project is not None:
                project["epics"] = [c for c in project.get("epics", []) if c["key"] != key]
                project["issues"] = [c for c in project.get("issues", []) if c["key"] != key]
        for d in walk(node):
            self._issues.pop(d["key"], None)
            self.summary["issue_count"] = self.summary.get("issue_count", 1) - 1
        return node

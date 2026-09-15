"""Jira REST transport + write client. Standard library only.

Every mutation: validate locally against the Catalog → write to Jira → only on success
patch the snapshot. Raises JiraError (never exits) so callers decide how to report.
"""
from __future__ import annotations

import base64
import datetime
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from jira_models import Catalog, Member, Node, short_id


class JiraError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def adf(text: str) -> dict:
    """Plain text → minimal Atlassian Document Format (one paragraph per line)."""
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": ([{"type": "text", "text": line}] if line else [])}
                        for line in text.split("\n")]}


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # optional: NixOS python outside a shell has no system CA path
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class JiraHttp:
    def __init__(self, base_url: str, email: str, token: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        self._ssl = ssl_context()
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, context=self._ssl, timeout=self.timeout) as resp:
                payload = resp.read().decode()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise JiraError(f"{method} {path} -> HTTP {exc.code}: {detail[:500]}", exc.code) from None
        except urllib.error.URLError as exc:
            raise JiraError(f"{method} {path} -> {exc.reason}") from None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: dict) -> Any:
        return self.request("POST", path, body)

    def put(self, path: str, body: dict) -> Any:
        return self.request("PUT", path, body)

    def delete(self, path: str) -> None:
        self.request("DELETE", path)

    def search(self, jql: str, fields: list[str], page_size: int = 100) -> list[dict]:
        """POST /search/jql with nextPageToken pagination (the only surviving search endpoint)."""
        issues: list[dict] = []
        token: str | None = None
        while True:
            body: dict = {"jql": jql, "fields": fields, "maxResults": page_size}
            if token:
                body["nextPageToken"] = token
            page = self.post("/rest/api/3/search/jql", body) or {}
            issues.extend(page.get("issues", []))
            token = page.get("nextPageToken")
            if page.get("isLast") or not token:
                return issues

    def myself(self) -> dict:
        return self.get("/rest/api/3/myself") or {}


class JiraWriter:
    """Mutations: validate → write → patch the snapshot (autosaved)."""

    def __init__(self, http: JiraHttp, catalog: Catalog, autosave: bool = True) -> None:
        self.http, self.catalog, self.autosave = http, catalog, autosave

    def _persist(self) -> None:
        if self.autosave and self.catalog.path is not None:
            self.catalog.save()

    def _require_person(self, query: str) -> Member:
        member = self.catalog.resolve_person(query)
        if member is None:
            raise JiraError(f"could not resolve a single person from {query!r}")
        if not member.get("accountId"):
            raise JiraError(f"{query!r} is not an assignable account")
        return member

    def _require_issue(self, key: str) -> Node:
        node = self.catalog.issue(key)
        if node is None:
            raise JiraError(f"{key} not in snapshot — run `jira sync` first")
        return node

    def add_comment(self, key: str, body: str, author: str | None = None) -> dict:
        self._require_issue(key)
        member = self.catalog.resolve_person(author) if author else None
        self.http.post(f"/rest/api/3/issue/{key}/comment", {"body": adf(body)})
        comment = self.catalog.apply_comment(key, member, body, now_utc()[:10])
        self._persist()
        return comment

    def assign_issue(self, key: str, assignee: str | None) -> Node:
        self._require_issue(key)
        member = self._require_person(assignee) if assignee else None
        self.http.put(f"/rest/api/3/issue/{key}/assignee", {"accountId": member["accountId"] if member else None})
        node = self.catalog.apply_assignment(key, member)
        node["updated"] = now_utc()
        self._persist()
        return node

    def update_summary(self, key: str, summary: str) -> Node:
        self._require_issue(key)
        self.http.put(f"/rest/api/3/issue/{key}", {"fields": {"summary": summary}})
        node = self.catalog.apply_summary(key, summary, now_utc())
        self._persist()
        return node

    def transition_issue(self, key: str, to_status: str) -> Node:
        self._require_issue(key)
        data = self.http.get(f"/rest/api/3/issue/{key}/transitions") or {}
        target = to_status.strip().lower()
        match = next((t for t in data.get("transitions", [])
                      if t.get("to", {}).get("name", "").lower() == target or t.get("name", "").lower() == target), None)
        if match is None:
            available = ", ".join(t["to"]["name"] for t in data.get("transitions", []))
            raise JiraError(f"no transition to {to_status!r} from {key}; available: {available}")
        self.http.post(f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": match["id"]}})
        node = self.catalog.apply_status(key, match["to"]["name"], now_utc())
        self._persist()
        return node

    def create_issue(self, project: str, issue_type: str, summary: str, description: str | None = None,
                     parent_key: str | None = None, assignee: str | None = None) -> Node:
        member = self._require_person(assignee) if assignee else None
        fields: dict = {"project": {"key": project}, "issuetype": {"name": issue_type}, "summary": summary}
        if description:
            fields["description"] = adf(description)
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if member:
            fields["assignee"] = {"accountId": member["accountId"]}
        created = self.http.post("/rest/api/3/issue", {"fields": fields}) or {}
        key = created.get("key")
        if not key:
            raise JiraError(f"create returned no key: {created}")
        fresh = self.http.get(f"/rest/api/3/issue/{key}?fields=status,updated,issuetype") or {}
        ff = fresh.get("fields", {})
        node: Node = {"key": key, "type": ff.get("issuetype", {}).get("name", issue_type), "summary": summary,
                      "status": ff.get("status", {}).get("name"), "updated": now_utc(), "parentKey": parent_key}
        if member:
            node["assignee"] = member.get("shortId") or short_id(member.get("accountId"))
        self.catalog.add_issue(node)
        self._persist()
        return node

    def delete_issue(self, key: str, delete_subtasks: bool = False) -> Node:
        self._require_issue(key)
        self.http.delete(f"/rest/api/3/issue/{key}" + ("?deleteSubtasks=true" if delete_subtasks else ""))
        node = self.catalog.remove_issue(key)
        self._persist()
        return node

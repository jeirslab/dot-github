"""Batch plan applier: one change-set file, validated whole against the snapshot before
any network I/O, then applied op by op (INFRA-219 design, ported in INFRA-278).

Plan: a list of single-key mappings, YAML (needs PyYAML, which the org flake provides)
or JSON (standard library). ``ref:`` names a created issue; ``$name`` references it later.

    - create: {project: INFRA, type: Epic, summary: "Wallet analytics", assignee: alice, ref: e1}
    - create: {project: INFRA, type: Task, parent: $e1, summary: "Edge tables"}
    - transition: {key: INFRA-201, to: Done}
    - comment: {key: $e1, body: "planned via jira apply"}
    - assign: {key: INFRA-656, to: alice}
    - summary: {key: INFRA-173, text: "new title"}
    - delete: {key: INFRA-999, subtasks: true}
"""
from __future__ import annotations

import json
from pathlib import Path

from jira_client import JiraError, JiraWriter
from jira_models import Catalog

OPS = ("create", "transition", "assign", "comment", "summary", "delete")


def load_plan(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith(".json"):
        plan = json.loads(text)
    else:
        try:
            import yaml
        except ImportError:
            try:
                plan = json.loads(text)
            except json.JSONDecodeError:
                raise ValueError("PyYAML is not installed; write the plan as JSON or run through the org flake") from None
        else:
            plan = yaml.safe_load(text)
    if not isinstance(plan, list):
        raise ValueError("plan must be a list of operations")
    for i, item in enumerate(plan):
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError(f"op {i}: each entry must be a single-key mapping")
        op = next(iter(item))
        if op not in OPS:
            raise ValueError(f"op {i}: unknown operation {op!r} (expected one of {', '.join(OPS)})")
        if not isinstance(item[op], dict):
            raise ValueError(f"op {i} ({op}): body must be a mapping")
    return plan


def validate(plan: list[dict], catalog: Catalog) -> list[str]:
    """Whole-plan validation against the snapshot; no network. Empty list = applyable."""
    errors: list[str] = []
    refs: set[str] = set()
    projects = set(catalog.project_keys())

    def check_key(i: int, op: str, key: str | None, field: str = "key") -> None:
        if not key:
            errors.append(f"op {i} ({op}): missing {field}")
        elif key.startswith("$"):
            if key[1:] not in refs:
                errors.append(f"op {i} ({op}): {key} not defined by an earlier create's ref")
        elif catalog.issue(key) is None:
            errors.append(f"op {i} ({op}): {key} not in snapshot — run `jira sync` first")

    def check_person(i: int, op: str, query: str | None) -> None:
        if query and catalog.resolve_person(query) is None:
            errors.append(f"op {i} ({op}): cannot resolve person {query!r}")

    for i, item in enumerate(plan):
        op = next(iter(item)); body = item[op]
        if op == "create":
            for req in ("project", "type", "summary"):
                if not body.get(req):
                    errors.append(f"op {i} (create): missing {req}")
            if body.get("project") and body["project"] not in projects:
                errors.append(f"op {i} (create): project {body['project']!r} not in snapshot")
            if body.get("parent"):
                check_key(i, "create", body["parent"], "parent")
            check_person(i, "create", body.get("assignee"))
            if body.get("ref"):
                refs.add(body["ref"])
        elif op == "transition":
            check_key(i, op, body.get("key"))
            if not body.get("to"):
                errors.append(f"op {i} (transition): missing 'to' status")
        elif op == "assign":
            check_key(i, op, body.get("key"))
            check_person(i, op, body.get("to"))
        elif op == "comment":
            check_key(i, op, body.get("key"))
            if not body.get("body"):
                errors.append(f"op {i} (comment): missing body")
            check_person(i, op, body.get("author"))
        elif op == "summary":
            check_key(i, op, body.get("key"))
            if not body.get("text"):
                errors.append(f"op {i} (summary): missing text")
        elif op == "delete":
            check_key(i, op, body.get("key"))
    return errors


def apply(plan: list[dict], writer: JiraWriter, default_author: str | None = None) -> list[dict]:
    """Apply sequentially; stop at the first failure. The snapshot stays truthful because
    the writer patches it after each success — rerun a corrected plan for the rest."""
    results: list[dict] = []
    refs: dict[str, str] = {}

    def resolve(key: str) -> str:
        return refs[key[1:]] if key.startswith("$") else key

    for item in plan:
        op = next(iter(item)); body = item[op]
        try:
            if op == "create":
                node = writer.create_issue(body["project"], body["type"], body["summary"], body.get("description"),
                                           resolve(body["parent"]) if body.get("parent") else None, body.get("assignee"))
                if body.get("ref"):
                    refs[body["ref"]] = node["key"]
                results.append({"op": op, "key": node["key"], "ok": True, "detail": body["summary"]})
            elif op == "transition":
                node = writer.transition_issue(resolve(body["key"]), body["to"])
                results.append({"op": op, "key": node["key"], "ok": True, "detail": f"-> {node['status']}"})
            elif op == "assign":
                node = writer.assign_issue(resolve(body["key"]), body.get("to"))
                results.append({"op": op, "key": node["key"], "ok": True, "detail": node.get("assignee") or "unassigned"})
            elif op == "comment":
                key = resolve(body["key"])
                writer.add_comment(key, body["body"], body.get("author") or default_author)
                results.append({"op": op, "key": key, "ok": True, "detail": body["body"][:60]})
            elif op == "summary":
                node = writer.update_summary(resolve(body["key"]), body["text"])
                results.append({"op": op, "key": node["key"], "ok": True, "detail": body["text"][:60]})
            elif op == "delete":
                key = resolve(body["key"])
                writer.delete_issue(key, bool(body.get("subtasks")))
                results.append({"op": op, "key": key, "ok": True, "detail": "deleted"})
        except (JiraError, KeyError) as exc:
            results.append({"op": op, "key": str(body.get("key", "?")), "ok": False, "detail": str(exc)})
            break
    return results

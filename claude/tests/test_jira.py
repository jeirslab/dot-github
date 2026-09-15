"""Org Jira CLI (claude/jira): catalog, plan validation/apply, exporter transform, config,
branch sweep — all against fixtures and fakes, no network."""
from __future__ import annotations

import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "jira"))
import branch_sweep  # noqa: E402
import jira_apply  # noqa: E402
import jira_cli  # noqa: E402
import jira_export  # noqa: E402
from jira_client import JiraError, JiraWriter  # noqa: E402
from jira_config import Config  # noqa: E402
from jira_models import Catalog  # noqa: E402

SNAPSHOT = {
    "exported_at": "2026-09-09T00:00:00+00:00", "base_url": "https://acme.atlassian.net", "issue_count": 4,
    "team": [
        {"accountId": "712020:aaaa-1", "shortId": "712020:aaaa", "displayName": "Alice Example", "email": "alice@acme.test", "stats": {}},
        {"accountId": "712020:bbbb-2", "shortId": "712020:bbbb", "displayName": "Bob Builder", "email": None, "stats": {}},
        {"accountId": None, "shortId": None, "displayName": "Unassigned", "email": None, "stats": {}},
    ],
    "projects": [
        {"key": "INFRA", "name": "Infra", "epics": [
            {"key": "INFRA-1", "type": "Epic", "summary": "Platform", "status": "In Progress", "updated": "2026-09-01T00:00:00Z",
             "parentKey": None, "assignee": "712020:aaaa", "children": [
                 {"key": "INFRA-2", "type": "Task", "summary": "Do the thing", "status": "In Progress", "updated": "2026-09-02T00:00:00Z",
                  "parentKey": "INFRA-1", "assignee": "712020:bbbb", "comments": [{"author": "712020:aaaa", "created": "2026-09-02", "body": "started"}]}]}],
         "issues": [{"key": "INFRA-3", "type": "Task", "summary": "Finished thing", "status": "Done", "updated": "2026-08-01T00:00:00Z", "parentKey": None}]},
        {"key": "APP", "name": "App", "epics": [], "issues": [{"key": "APP-7", "type": "Bug", "summary": "Crash on start", "status": "To Do", "updated": None, "parentKey": None}]},
    ],
}


class FakeHttp:
    """Records writes; answers the few GETs the writer needs."""
    def __init__(self):
        self.calls = []
        self.next_key = 100

    def get(self, path):
        self.calls.append(("GET", path, None))
        if path.endswith("/transitions"):
            return {"transitions": [{"id": "41", "name": "Done", "to": {"name": "Done"}}, {"id": "2", "name": "In Progress", "to": {"name": "In Progress"}}]}
        if "?fields=status" in path:
            return {"fields": {"status": {"name": "To Do"}, "issuetype": {"name": "Task"}}}
        return {}

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if path == "/rest/api/3/issue":
            self.next_key += 1
            return {"key": f"{body['fields']['project']['key']}-{self.next_key}"}
        return {}

    def put(self, path, body):
        self.calls.append(("PUT", path, body)); return {}

    def delete(self, path):
        self.calls.append(("DELETE", path, None))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "summary.json"
        self.path.write_text(json.dumps(SNAPSHOT))
        self.cat = Catalog.load(self.path)

    def test_lookups(self):
        self.assertEqual(self.cat.issue("INFRA-2")["summary"], "Do the thing")
        self.assertEqual(self.cat.project_of("APP-7")["name"], "App")
        self.assertEqual(self.cat.resolve_person("alice")["accountId"], "712020:aaaa-1")
        self.assertEqual(self.cat.resolve_person("712020:bbbb")["displayName"], "Bob Builder")
        self.assertEqual(self.cat.resolve_person("alice@acme.test")["shortId"], "712020:aaaa")
        self.assertIsNone(self.cat.resolve_person("e"))  # ambiguous substring → no guess
        self.assertEqual(self.cat.assignee_of("INFRA-2")["displayName"], "Bob Builder")
        self.assertEqual([n["key"] for n in self.cat.find("thing")], ["INFRA-2", "INFRA-3"])
        self.assertEqual(sorted(self.cat.project_keys()), ["APP", "INFRA"])

    def test_patches_and_save_roundtrip(self):
        alice = self.cat.resolve_person("alice")
        self.cat.apply_status("INFRA-2", "Done", "2026-09-09T00:00:00Z")
        self.cat.apply_assignment("INFRA-3", alice)
        self.cat.add_issue({"key": "INFRA-9", "type": "Task", "summary": "child", "status": "To Do", "parentKey": "INFRA-1"})
        self.cat.add_issue({"key": "APP-8", "type": "Epic", "summary": "top", "status": "To Do", "parentKey": None})
        self.cat.save()
        again = Catalog.load(self.path)
        self.assertEqual(again.issue("INFRA-2")["status"], "Done")
        self.assertEqual(again.issue("INFRA-3")["assignee"], "712020:aaaa")
        self.assertEqual(again.issue("INFRA-9")["parentKey"], "INFRA-1")
        self.assertEqual([c["key"] for c in again.issue("INFRA-1")["children"]], ["INFRA-2", "INFRA-9"])
        self.assertEqual(again.project_of("APP-8")["epics"][0]["key"], "APP-8")
        self.assertEqual(again.summary["issue_count"], 6)
        again.remove_issue("INFRA-1")  # removes the epic and its subtree
        self.assertIsNone(again.issue("INFRA-9")); self.assertEqual(again.summary["issue_count"], 3)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "summary.json"; self.path.write_text(json.dumps(SNAPSHOT))
        self.cat = Catalog.load(self.path)

    def plan(self, ops, name="plan.json"):
        p = Path(self.tmp.name) / name; p.write_text(json.dumps(ops)); return p

    def test_validate_catches_everything_before_network(self):
        plan = jira_apply.load_plan(self.plan([
            {"create": {"project": "NOPE", "type": "Task", "summary": "x"}},
            {"transition": {"key": "INFRA-404", "to": "Done"}},
            {"assign": {"key": "INFRA-2", "to": "nobody-here"}},
            {"comment": {"key": "$missing", "body": "hi"}},
            {"summary": {"key": "INFRA-2"}},
        ]))
        errors = jira_apply.validate(plan, self.cat)
        self.assertEqual(len(errors), 5, errors)
        self.assertTrue(any("not in snapshot" in e for e in errors))
        self.assertTrue(any("$missing" in e for e in errors))

    def test_bad_plan_shapes(self):
        with self.assertRaises(ValueError):
            jira_apply.load_plan(self.plan({"create": {}}))
        with self.assertRaises(ValueError):
            jira_apply.load_plan(self.plan([{"explode": {"key": "INFRA-2"}}]))

    def test_apply_happy_path_refs_and_snapshot_patch(self):
        http = FakeHttp()
        writer = JiraWriter(http, self.cat)  # type: ignore[arg-type]
        plan = jira_apply.load_plan(self.plan([
            {"create": {"project": "INFRA", "type": "Epic", "summary": "New epic", "assignee": "alice", "ref": "e1"}},
            {"create": {"project": "INFRA", "type": "Task", "parent": "$e1", "summary": "Child task"}},
            {"transition": {"key": "$e1", "to": "In Progress"}},
            {"comment": {"key": "INFRA-2", "body": "batch says hi"}},
            {"assign": {"key": "INFRA-3", "to": "bob"}},
            {"summary": {"key": "APP-7", "text": "Crash on start (fixed)"}},
            {"delete": {"key": "INFRA-3"}},
        ]))
        self.assertEqual(jira_apply.validate(plan, self.cat), [])
        results = jira_apply.apply(plan, writer, default_author="alice@acme.test")
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(results[0]["key"], "INFRA-101"); self.assertEqual(results[1]["key"], "INFRA-102")
        again = Catalog.load(self.path)
        self.assertEqual(again.issue("INFRA-102")["parentKey"], "INFRA-101")
        self.assertEqual(again.issue("INFRA-101")["status"], "In Progress")
        self.assertEqual(again.issue("INFRA-2")["comments"][-1]["body"], "batch says hi")
        self.assertEqual(again.issue("INFRA-2")["comments"][-1]["author"], "712020:aaaa")
        self.assertIsNone(again.issue("INFRA-3"))
        self.assertEqual(again.issue("APP-7")["summary"], "Crash on start (fixed)")
        posted = [c for c in http.calls if c[0] == "POST" and c[1].endswith("/transitions")]
        self.assertEqual(posted[0][2], {"transition": {"id": "2"}})

    def test_apply_stops_at_first_failure_and_keeps_snapshot_truthful(self):
        http = FakeHttp()
        http.get = lambda path: {"transitions": []} if path.endswith("/transitions") else {}
        writer = JiraWriter(http, self.cat)  # type: ignore[arg-type]
        plan = jira_apply.load_plan(self.plan([
            {"summary": {"key": "INFRA-2", "text": "renamed"}},
            {"transition": {"key": "INFRA-2", "to": "Nowhere"}},
            {"summary": {"key": "APP-7", "text": "never reached"}},
        ]))
        results = jira_apply.apply(plan, writer)
        self.assertEqual([r["ok"] for r in results], [True, False])
        self.assertIn("no transition", results[1]["detail"])
        again = Catalog.load(self.path)
        self.assertEqual(again.issue("INFRA-2")["summary"], "renamed")
        self.assertEqual(again.issue("APP-7")["summary"], "Crash on start")

    def test_yaml_plan_when_pyyaml_available(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")
        p = Path(self.tmp.name) / "plan.yaml"
        p.write_text("- transition: {key: INFRA-2, to: Done}\n- comment:\n    key: INFRA-2\n    body: ok\n")
        plan = jira_apply.load_plan(p)
        self.assertEqual(jira_apply.validate(plan, self.cat), [])


class ExportTransformTests(unittest.TestCase):
    def raw(self):
        def issue(key, typ, summary, parent=None, assignee=None, status="To Do", labels=(), comments=()):
            return {"key": key, "fields": {"issuetype": {"name": typ}, "summary": summary, "status": {"name": status},
                                           "updated": "2026-09-01T10:00:00.000+0900", "parent": {"key": parent} if parent else None,
                                           "assignee": {"accountId": assignee, "displayName": assignee.split(":")[0].title()} if assignee else None,
                                           "reporter": {"accountId": "rep:1-x", "displayName": "Rep"}, "labels": list(labels),
                                           "comment": {"comments": [{"author": {"accountId": "rep:1-x"}, "created": "2026-09-02T00:00:00.000+0000",
                                                                     "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": c}]}]}} for c in comments]}}}
        return {"exported_at": "2026-09-09T00:00:00+00:00", "base_url": "https://acme.atlassian.net",
                "projects": [{"key": "INFRA", "name": "Infra"}],
                "issues": [issue("INFRA-1", "Epic", "Platform &amp; more", assignee="alice:1-x"),
                           issue("INFRA-2", "Task", "Child", parent="INFRA-1", assignee="alice:1-x", comments=("hello",)),
                           issue("INFRA-3", "Task", "Loose", labels=("rabbitmq",)),
                           issue("INFRA-10", "Subtask", "Grandchild", parent="INFRA-2")]}

    def test_simplify_nests_and_builds_team(self):
        out = jira_export.simplify_snapshot(self.raw())
        proj = out["projects"][0]
        self.assertEqual(proj["epics"][0]["key"], "INFRA-1")
        self.assertEqual(proj["epics"][0]["summary"], "Platform & more")
        self.assertEqual(proj["epics"][0]["children"][0]["children"][0]["key"], "INFRA-10")
        self.assertEqual(proj["issues"][0]["key"], "INFRA-3")
        self.assertEqual(proj["epics"][0]["updated"], "2026-09-01T01:00:00Z")  # +0900 normalised to UTC
        self.assertEqual(proj["epics"][0]["children"][0]["comments"][0]["body"], "hello")
        names = [m["displayName"] for m in out["team"]]
        self.assertEqual(names[-1], "Unassigned"); self.assertIn("Alice", names)
        alice = next(m for m in out["team"] if m["displayName"] == "Alice")
        self.assertEqual(alice["stats"]["INFRA"]["total"], 2)

    def test_search_keeps_ancestors_and_subtree(self):
        out = jira_export.simplify_snapshot(self.raw(), ["grandchild"])
        self.assertEqual(out["issue_count"], 3)  # INFRA-10 + ancestors INFRA-2, INFRA-1
        out = jira_export.simplify_snapshot(self.raw(), ["rabbitmq"])
        self.assertEqual([n["key"] for n in out["projects"][0]["issues"]], ["INFRA-3"])


class ConfigTests(unittest.TestCase):
    def test_env_overrides_org_toml_and_snapshot_defaults_to_cache(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "org.toml").write_text('[jira]\nbase_url = "https://acme.atlassian.net/"\n[jira.sync]\nprojects = ["INFRA"]\n')
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": d}, clear=False):
                os.environ.pop("JIRA_BASE_URL", None); os.environ.pop("ORG_JIRA_SNAPSHOT", None)
                cfg = Config(d)
                self.assertEqual(cfg.base_url, "https://acme.atlassian.net")
                self.assertEqual(cfg.snapshot_path, Path(d) / "org-jira" / "acme.atlassian.net" / "summary.json")
                self.assertEqual(cfg.sync["projects"], ["INFRA"])
                self.assertEqual(jira_cli.sync_jql(cfg, None, True, "7d", None, None), "project IN (INFRA) AND statusCategory != Done AND updated >= -7d ORDER BY created ASC")
            with mock.patch.dict(os.environ, {"JIRA_BASE_URL": "https://other.example", "ORG_JIRA_SNAPSHOT": "/tmp/x.json"}):
                cfg = Config(d)
                self.assertEqual(cfg.base_url, "https://other.example"); self.assertEqual(cfg.snapshot_path, Path("/tmp/x.json"))

    def test_missing_base_url_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JIRA_BASE_URL", None)
            cfg = Config(d)
            self.assertIsNone(cfg.base_url_or_none)
            with self.assertRaises(SystemExit):
                _ = cfg.base_url


class FakeGitHub(branch_sweep.GitHub):
    def __init__(self, world: dict):
        super().__init__(token=None); self.world = world

    def repo(self, full):
        return {"default_branch": self.world[full]["default"]}

    def org_toml(self, full, ref):
        return self.world[full].get("org_toml", {})

    def branches(self, full):
        return [{"name": n, "commit": {"sha": b["sha"]}} for n, b in self.world[full]["branches"].items()]

    def compare(self, full, base, head):
        b = self.world[full]["branches"][head]; return {"ahead_by": b["ahead"], "behind_by": b.get("behind", 0)}

    def commit(self, full, sha):
        b = next(b for b in self.world[full]["branches"].values() if b["sha"] == sha)
        return {"commit": {"committer": {"date": b["date"]}, "author": {"name": b.get("author", "someone")}}, "author": {"login": b.get("author")}}

    def open_prs(self, full, branch):
        b = self.world[full]["branches"][branch]
        return [{"number": 5, "html_url": f"https://gh/{full}/pull/5", "draft": False, "base": {"ref": "staging"}}] if b.get("pr") else []


class SweepTests(unittest.TestCase):
    def test_key_extraction(self):
        self.assertEqual(branch_sweep.keys_in("INFRA-237-thing", None), ["INFRA-237"])
        self.assertEqual(branch_sweep.keys_in("feature/legacy-234_x", None), [])            # lower-case needs a project list
        self.assertEqual(branch_sweep.keys_in("feature/legacy-234_x", ["LEGACY"]), ["LEGACY-234"])
        self.assertEqual(branch_sweep.keys_in("claude/infra-12-and-INFRA-13", ["INFRA"]), ["INFRA-12", "INFRA-13"])
        self.assertEqual(branch_sweep.keys_in("release-2026-09", None), [])   # a date is not a key
        self.assertEqual(branch_sweep.keys_in("v1-2", None), [])
        self.assertEqual(branch_sweep.keys_in("chore/UTF8-3", ["INFRA"]), [])   # project filter

    def test_report_and_flags(self):
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        world = {"acme/app": {"default": "main", "org_toml": {"branches": {"integration": "staging", "deploy": ["main"]}}, "branches": {
            "main": {"sha": "m", "date": fresh, "ahead": 0}, "staging": {"sha": "s", "date": fresh, "ahead": 0},
            "INFRA-2-work": {"sha": "a", "date": fresh, "ahead": 3, "behind": 1, "pr": True, "author": "bob"},
            "infra-3-old": {"sha": "b", "date": old, "ahead": 2, "author": "carol"},
            "APP-7-merged": {"sha": "c", "date": fresh, "ahead": 0, "author": "dan"},
            "INFRA-404-typo": {"sha": "d", "date": fresh, "ahead": 1, "author": "erin"},
            "INFRA-2-done-but-open": {"sha": "g", "date": fresh, "ahead": 0, "author": "bob"},
            "dependabot/npm/x": {"sha": "e", "date": fresh, "ahead": 1},
            "spike-no-key": {"sha": "f", "date": old, "ahead": 1, "author": "frank"},
        }}}
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "org.toml").write_text('[jira]\nbase_url = "https://acme.atlassian.net"\n')
            snap = Path(d) / "snap.json"; snap.write_text(json.dumps(SNAPSHOT))
            with mock.patch.dict(os.environ, {"ORG_JIRA_SNAPSHOT": str(snap)}):
                os.environ.pop("JIRA_EMAIL", None); os.environ.pop("JIRA_API_TOKEN", None)
                cfg = Config(d)
                report = branch_sweep.run(FakeGitHub(world), cfg, ["acme/app"], offline=True)
        by = {e["branch"]: e for e in report["branches"]}
        self.assertEqual(set(by), {"INFRA-2-work", "infra-3-old", "APP-7-merged", "INFRA-404-typo", "INFRA-2-done-but-open"})
        self.assertEqual(by["INFRA-2-done-but-open"]["flags"], ["in-progress-merged", "merged"])
        self.assertEqual([e["branch"] for e in report["unlinked"]], ["spike-no-key"])   # dependabot ignored
        self.assertEqual(by["INFRA-2-work"]["owner"], "Bob Builder"); self.assertEqual(by["INFRA-2-work"]["flags"], [])
        self.assertEqual(by["INFRA-2-work"]["pr"]["number"], 5)
        self.assertEqual(by["infra-3-old"]["flags"], ["stale", "ticket-done", "unassigned"])
        self.assertEqual(by["infra-3-old"]["owner"], "carol")   # unassigned ticket → last committer
        self.assertEqual(by["APP-7-merged"]["flags"], ["merged", "unassigned"])
        self.assertEqual(by["INFRA-404-typo"]["flags"], ["no-ticket"])
        self.assertTrue(report["ticket_source"].startswith("snapshot"))
        self.assertEqual(report["stats"]["flags"]["stale"], 1)
        md = branch_sweep.markdown(report, "https://acme.atlassian.net")
        self.assertIn("## Bob Builder (2)", md)
        self.assertIn("[INFRA-2](https://acme.atlassian.net/browse/INFRA-2) Do the thing", md)
        self.assertIn("[#5](https://gh/acme/app/pull/5)", md)
        self.assertIn("`spike-no-key`", md)

    def test_cli_wiring(self):
        world = {"acme/app": {"default": "main", "branches": {"main": {"sha": "m", "date": "2026-09-01T00:00:00Z", "ahead": 0}}}}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(branch_sweep, "GitHub", lambda token: FakeGitHub(world)):
            (Path(d) / "org.toml").write_text('[jira]\nbase_url = "https://acme.atlassian.net"\n')
            buf = io.StringIO()
            with redirect_stdout(buf), mock.patch.dict(os.environ, {"ORG_JIRA_SNAPSHOT": str(Path(d) / "none.json")}):
                rc = jira_cli.main(["--root", d, "sweep", "--repos", "acme/app", "--format", "json", "--offline"])
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["stats"]["repos"], 1); self.assertEqual(data["branches"], [])


class CliSnapshotCommandsTests(unittest.TestCase):
    def test_show_and_find(self):
        with tempfile.TemporaryDirectory() as d:
            snap = Path(d) / "s.json"; snap.write_text(json.dumps(SNAPSHOT))
            (Path(d) / "org.toml").write_text('[jira]\nbase_url = "https://acme.atlassian.net"\n')
            with mock.patch.dict(os.environ, {"ORG_JIRA_SNAPSHOT": str(snap)}):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = jira_cli.main(["--root", d, "show", "infra-2", "INFRA-999"])
                self.assertEqual(rc, 1)
                self.assertIn("INFRA-2  [Task]  In Progress  ← Bob Builder", buf.getvalue())
                self.assertIn("Alice Example: started", buf.getvalue())
                self.assertIn("INFRA-999: not in snapshot", buf.getvalue())
                buf = io.StringIO()
                with redirect_stdout(buf):
                    jira_cli.main(["--root", d, "find", "thing", "--open"])
                self.assertIn("INFRA-2", buf.getvalue()); self.assertNotIn("INFRA-3", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

"""verify_pr.py against a fake GitHub API (no network)."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "hooks"))
import verify_pr  # noqa: E402

REQ = ["gate / gate", "guardrails / secrets"]


class FakeGH:
    def __init__(self, routes: dict):
        self.routes = routes

    def get(self, path: str):
        for prefix, val in self.routes.items():
            if path.startswith(prefix):
                return 200, val
        return 404, None


def cfg(codeowners=None):
    return {"codeowners": codeowners or {}}


def run_verify(gh, c, sha="abc", required=REQ, min_appr=1):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc, facts = verify_pr.verify(gh, "acme/app", sha, c, required, min_appr)
    return rc, facts, buf.getvalue()


PR = {"number": 7, "user": {"login": "alice"}, "merged_at": "2026-09-09T00:00:00Z", "merge_commit_sha": "abc", "head": {"sha": "h1"}, "title": "INFRA-1: x"}


class VerifyPrTests(unittest.TestCase):
    def test_direct_push_is_rc3(self):
        gh = FakeGH({"/repos/acme/app/commits/abc/pulls": []})
        rc, facts, _ = run_verify(gh, cfg())
        self.assertEqual(rc, 3)
        self.assertIsNone(facts["pr"])

    def test_happy_path(self):
        gh = FakeGH({
            "/repos/acme/app/commits/abc/pulls": [PR],
            "/repos/acme/app/pulls/7/reviews": [{"user": {"login": "bob"}, "state": "APPROVED"}],
            "/repos/acme/app/commits/h1/check-runs": {"check_runs": [{"name": "gate / gate", "conclusion": "success"}, {"name": "guardrails / secrets", "conclusion": "success"}]},
            "/repos/acme/app/pulls/7/files": [{"filename": "src/a.py"}],
        })
        rc, facts, _ = run_verify(gh, cfg({"nix/secrets/**": ["@acme/security"]}))
        self.assertEqual(rc, 0)
        self.assertEqual(facts["approvals"], ["bob"])

    def test_self_approval_and_missing_check_fail(self):
        gh = FakeGH({
            "/repos/acme/app/commits/abc/pulls": [PR],
            "/repos/acme/app/pulls/7/reviews": [{"user": {"login": "alice"}, "state": "APPROVED"}],  # author approving herself
            "/repos/acme/app/commits/h1/check-runs": {"check_runs": [{"name": "gate / gate", "conclusion": "failure"}]},
            "/repos/acme/app/pulls/7/files": [],
        })
        rc, facts, out = run_verify(gh, cfg())
        self.assertEqual(rc, 1)
        self.assertEqual(facts["approvals"], [])
        self.assertIn("never ran", out)
        self.assertIn("concluded 'failure'", out)

    def test_changes_requested_after_approval_cancels_it(self):
        gh = FakeGH({
            "/repos/acme/app/commits/abc/pulls": [PR],
            "/repos/acme/app/pulls/7/reviews": [{"user": {"login": "bob"}, "state": "APPROVED"}, {"user": {"login": "bob"}, "state": "CHANGES_REQUESTED"}],
            "/repos/acme/app/commits/h1/check-runs": {"check_runs": [{"name": "gate / gate", "conclusion": "success"}, {"name": "guardrails / secrets", "conclusion": "success"}]},
            "/repos/acme/app/pulls/7/files": [],
        })
        rc, facts, _ = run_verify(gh, cfg())
        self.assertEqual(rc, 1)

    def test_codeowner_path_requires_owner_approval(self):
        routes = {
            "/repos/acme/app/commits/abc/pulls": [PR],
            "/repos/acme/app/pulls/7/reviews": [{"user": {"login": "bob"}, "state": "APPROVED"}],
            "/repos/acme/app/commits/h1/check-runs": {"check_runs": [{"name": "gate / gate", "conclusion": "success"}, {"name": "guardrails / secrets", "conclusion": "success"}]},
            "/repos/acme/app/pulls/7/files": [{"filename": "nix/secrets/db.yaml"}],
            "/orgs/acme/teams/security/members": [{"login": "carol"}],
        }
        rc, _, out = run_verify(FakeGH(routes), cfg({"nix/secrets/**": ["@acme/security"]}))
        self.assertEqual(rc, 1)
        self.assertIn("without an approval from a listed owner", out)
        routes["/repos/acme/app/pulls/7/reviews"] = [{"user": {"login": "carol"}, "state": "APPROVED"}]
        rc, _, _ = run_verify(FakeGH(routes), cfg({"nix/secrets/**": ["@acme/security"]}))
        self.assertEqual(rc, 0)
        # unresolvable team → warning, not failure
        del routes["/orgs/acme/teams/security/members"]
        routes["/repos/acme/app/pulls/7/reviews"] = [{"user": {"login": "bob"}, "state": "APPROVED"}]
        rc, _, out = run_verify(FakeGH(routes), cfg({"nix/secrets/**": ["@acme/security"]}))
        self.assertEqual(rc, 0)
        self.assertIn("could not be resolved", out)


if __name__ == "__main__":
    unittest.main()

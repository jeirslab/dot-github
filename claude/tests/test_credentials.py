"""Unit tests for the credential-provisioning tooling (set_sync_token, app_bootstrap).
Pure functions only — the live gh calls and the browser/manifest flow are exercised by hand."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import set_sync_token  # noqa: E402
import app_bootstrap   # noqa: E402


class SetSyncTokenTests(unittest.TestCase):
    def test_resolve_token_precedence(self):
        self.assertEqual(set_sync_token.resolve_token("ghp_explicit", {"PAT": "env"}), "ghp_explicit")
        self.assertEqual(set_sync_token.resolve_token(None, {"PAT": "  env_pat  "}), "env_pat")

    def test_gh_command_repo_vs_org(self):
        self.assertEqual(
            set_sync_token.gh_command("ORG_SYNC_TOKEN", org=None, repo="acme/.github", visibility="all"),
            ["gh", "secret", "set", "ORG_SYNC_TOKEN", "--repo", "acme/.github"])
        self.assertEqual(
            set_sync_token.gh_command("ORG_SYNC_TOKEN", org="acme", repo=None, visibility="all"),
            ["gh", "secret", "set", "ORG_SYNC_TOKEN", "--org", "acme", "--visibility", "all"])


class AppBootstrapTests(unittest.TestCase):
    def test_manifest_has_the_fleet_permissions(self):
        m = app_bootstrap.build_manifest("acme-org-sync", "http://localhost:9/callback")
        self.assertEqual(m["default_permissions"],
                         {"contents": "write", "pull_requests": "write", "workflows": "write",
                          "issues": "write", "metadata": "read"})
        self.assertFalse(m["public"])
        self.assertEqual(m["redirect_url"], "http://localhost:9/callback")

    def test_new_app_url_org_vs_user(self):
        self.assertEqual(app_bootstrap.new_app_url("acme", "Organization"),
                         "https://github.com/organizations/acme/settings/apps/new")
        self.assertEqual(app_bootstrap.new_app_url("acme", "org"),
                         "https://github.com/organizations/acme/settings/apps/new")
        self.assertEqual(app_bootstrap.new_app_url("alice", "User"),
                         "https://github.com/settings/apps/new")

    def test_parse_conversion(self):
        app_id, slug, pem = app_bootstrap.parse_conversion(
            {"id": 12345, "slug": "acme-org-sync", "pem": "-----BEGIN RSA PRIVATE KEY-----\n..."})
        self.assertEqual(app_id, "12345")
        self.assertEqual(slug, "acme-org-sync")
        self.assertTrue(pem.startswith("-----BEGIN"))


if __name__ == "__main__":
    unittest.main()

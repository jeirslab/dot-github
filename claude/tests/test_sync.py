"""Unit tests for claude/sync.py. Run: python3 -m unittest discover -s claude/tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync  # noqa: E402

SOURCE = Path(__file__).resolve().parents[1]  # the real claude/ directory


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- CLAUDE.md block

    def test_creates_claude_md_when_absent(self):
        r = sync.apply(SOURCE, self.target, "v9.9.9", dry_run=False)
        text = (self.target / "CLAUDE.md").read_text()
        self.assertTrue(text.startswith(sync.BEGIN))
        self.assertIn("Version: v9.9.9", text)
        self.assertIn(sync.END, text)
        self.assertNotIn(sync.VERSION_PLACEHOLDER, text)
        self.assertEqual(r.changes[0].action, "created")

    def test_inserts_block_at_top_and_keeps_repo_content(self):
        repo_rules = "# My repo\n\n- use the fleet CLI\n"
        (self.target / "CLAUDE.md").write_text(repo_rules)
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        text = (self.target / "CLAUDE.md").read_text()
        self.assertTrue(text.startswith(sync.BEGIN))
        self.assertTrue(text.endswith(repo_rules))
        _, block, after = sync.split_block(text)
        self.assertIsNotNone(block)
        self.assertEqual(after.lstrip("\n"), repo_rules)

    def test_prefers_dot_claude_when_present(self):
        (self.target / ".claude").mkdir()
        (self.target / ".claude" / "CLAUDE.md").write_text("# repo\n")
        r = sync.apply(SOURCE, self.target, "v1", dry_run=False)
        self.assertEqual(r.changes[0].path, ".claude/CLAUDE.md")
        self.assertFalse((self.target / "CLAUDE.md").exists())

    def test_idempotent_and_version_stable_when_content_unchanged(self):
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        first = (self.target / "CLAUDE.md").read_text()
        r = sync.apply(SOURCE, self.target, "v2", dry_run=False)
        self.assertEqual(r.changes[0].action, "unchanged")
        self.assertEqual((self.target / "CLAUDE.md").read_text(), first)
        self.assertFalse(r.changed)

    def test_replaces_block_when_template_changes(self):
        (self.target / "CLAUDE.md").write_text("# repo\n\nkeep me\n")
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        # Simulate a drifted/edited block in the repo.
        text = (self.target / "CLAUDE.md").read_text()
        drifted = text.replace("## Non-negotiables", "## Non-negotiables (edited in repo)")
        (self.target / "CLAUDE.md").write_text(drifted)
        r = sync.apply(SOURCE, self.target, "v2", dry_run=False)
        self.assertEqual(r.changes[0].action, "updated")
        final = (self.target / "CLAUDE.md").read_text()
        self.assertNotIn("(edited in repo)", final)
        self.assertIn("Version: v2", final)
        self.assertTrue(final.endswith("keep me\n"))

    def test_content_above_block_is_preserved(self):
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        text = (self.target / "CLAUDE.md").read_text()
        (self.target / "CLAUDE.md").write_text("<!-- repo header -->\n" + text.replace("## On demand", "## On demand (old)"))
        r = sync.apply(SOURCE, self.target, "v2", dry_run=False)
        final = (self.target / "CLAUDE.md").read_text()
        self.assertTrue(final.startswith("<!-- repo header -->\n" + sync.BEGIN))
        self.assertIn("preserved", r.changes[0].detail)

    def test_dry_run_writes_nothing(self):
        r = sync.apply(SOURCE, self.target, "v1", dry_run=True)
        self.assertTrue(r.changed)
        self.assertFalse((self.target / "CLAUDE.md").exists())
        self.assertFalse((self.target / ".claude" / "settings.json").exists())

    def test_check_reports_out_of_sync_then_in_sync(self):
        self.assertTrue(sync.check(SOURCE, self.target).changed)
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        self.assertFalse(sync.check(SOURCE, self.target).changed)

    # ---- settings.json

    def test_settings_merge_unions_lists_and_keeps_repo_keys(self):
        (self.target / ".claude").mkdir()
        (self.target / ".claude" / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(nix *)", "Skill(dataviz)"], "deny": ["Read(secret)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]},
        }))
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        merged = json.loads((self.target / ".claude" / "settings.json").read_text())
        org = json.loads((SOURCE / "settings.json").read_text())
        allow = merged["permissions"]["allow"]
        # org entries first, repo-only entries kept, no duplicates
        self.assertEqual(allow[: len(org["permissions"]["allow"])], org["permissions"]["allow"])
        self.assertIn("Bash(nix *)", allow)
        self.assertEqual(allow.count("Skill(dataviz)"), 1)
        self.assertIn("Read(secret)", merged["permissions"]["deny"])
        self.assertIn("hooks", merged)

    def test_settings_merge_is_idempotent(self):
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        r = sync.apply(SOURCE, self.target, "v1", dry_run=False)
        self.assertEqual(r.changes[1].action, "unchanged")

    # ---- .mcp.json

    def test_mcp_merge_by_server_name(self):
        (self.target / ".mcp.json").write_text(json.dumps({
            "mcpServers": {
                "grafana": {"command": "./scripts/mcp/grafana.sh"},
                "nixos": {"command": "old", "args": []},
            }
        }))
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        merged = json.loads((self.target / ".mcp.json").read_text())
        org = json.loads((SOURCE / "mcp.json").read_text())
        self.assertEqual(merged["mcpServers"]["nixos"], org["mcpServers"]["nixos"])
        self.assertEqual(merged["mcpServers"]["grafana"]["command"], "./scripts/mcp/grafana.sh")

    def test_conditional_mcp_server_follows_org_toml(self):
        # no org.toml → the conditional server (playwright, x-org-when = verify.ui) stays out
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        merged = json.loads((self.target / ".mcp.json").read_text())
        self.assertIn("nixos", merged["mcpServers"]); self.assertNotIn("playwright", merged["mcpServers"])
        # opt in → merged, and the condition key itself is not written
        (self.target / "org.toml").write_text("[verify]\nui = true\n")
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        merged = json.loads((self.target / ".mcp.json").read_text())
        self.assertIn("playwright", merged["mcpServers"]); self.assertNotIn(sync.CONDITION_KEY, merged["mcpServers"]["playwright"])
        # opt out again → removed
        (self.target / "org.toml").write_text("[verify]\nui = false\n")
        sync.apply(SOURCE, self.target, "v1", dry_run=False)
        self.assertNotIn("playwright", json.loads((self.target / ".mcp.json").read_text())["mcpServers"])

    def test_invalid_repo_json_is_refused(self):
        (self.target / ".mcp.json").write_text("{ not json")
        with self.assertRaises(SystemExit):
            sync.apply(SOURCE, self.target, "v1", dry_run=False)

    # ---- skills and agents

    def test_org_skills_and_agents_are_distributed_and_rendered(self):
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        skills = sorted(p.name for p in (self.target / ".claude" / "skills").iterdir())
        agents = sorted(p.name for p in (self.target / ".claude" / "agents").iterdir())
        self.assertTrue(all(n.startswith("org-") for n in skills), skills)
        self.assertTrue(all(n.startswith("org-") and n.endswith(".md") for n in agents), agents)
        self.assertIn("org-jira", skills)
        self.assertIn("org-reviewer.md", agents)
        dev_env = (self.target / ".claude" / "skills" / "org-dev-env" / "SKILL.md").read_text()
        self.assertNotIn(sync.ORG_PLACEHOLDER, dev_env)
        self.assertIn("github:ExampleOrg/.github", dev_env)
        # Every shipped skill/agent has frontmatter with a matching name.
        for name in skills:
            text = (self.target / ".claude" / "skills" / name / "SKILL.md").read_text()
            self.assertTrue(text.startswith("---\n"), name)
            self.assertIn(f"name: {name}\n", text)
        for fname in agents:
            text = (self.target / ".claude" / "agents" / fname).read_text()
            self.assertTrue(text.startswith("---\n"), fname)
            self.assertIn(f"name: {fname[:-3]}\n", text)

    def test_repo_owned_skills_and_agents_are_untouched(self):
        mine = self.target / ".claude" / "skills" / "my-deploy"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("---\nname: my-deploy\n---\nmine\n")
        (self.target / ".claude" / "agents").mkdir()
        (self.target / ".claude" / "agents" / "my-agent.md").write_text("---\nname: my-agent\n---\nmine\n")
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertEqual((mine / "SKILL.md").read_text(), "---\nname: my-deploy\n---\nmine\n")
        self.assertEqual((self.target / ".claude" / "agents" / "my-agent.md").read_text(), "---\nname: my-agent\n---\nmine\n")

    def test_stale_file_inside_org_skill_is_removed_and_sync_is_idempotent(self):
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        stale = self.target / ".claude" / "skills" / "org-jira" / "old.md"
        stale.write_text("obsolete")
        r = sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertFalse(stale.exists())
        self.assertTrue(any("removed" in c.detail for c in r.changes))
        r2 = sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertFalse(r2.changed)

    def test_hooks_distributed_and_codeowners_block(self):
        (self.target / "org.toml").write_text('[codeowners]\n"nix/secrets/**" = ["@acme/security"]\n')
        r = sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        hooks = sorted(p.name for p in (self.target / ".claude" / "hooks" / "org").glob("*.py"))
        self.assertIn("check_secrets.py", hooks)
        self.assertIn("orgfile.py", hooks)
        self.assertIn("_common.py", hooks)
        co = (self.target / ".github" / "CODEOWNERS").read_text()
        self.assertIn("nix/secrets/** @acme/security", co)
        r2 = sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertFalse(r2.changed)
        # the distributed hooks run from their new home
        import subprocess, sys as _sys
        out = subprocess.run([_sys.executable, str(self.target / ".claude/hooks/org/check_secrets.py"), "--root", str(self.target)], capture_output=True, text=True)
        self.assertIn("secrets:", out.stdout + out.stderr)

    def test_git_hooks_distributed_executable(self):
        import os
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        for name in ("pre-commit", "commit-msg", "pre-push"):
            p = self.target / ".claude" / "hooks" / "org" / "git" / name
            self.assertTrue(p.is_file(), name)
            self.assertTrue(os.access(p, os.X_OK), f"{name} not executable")

    # ---- selectable workflows ([workflows].enabled → .github/workflows/org-*.yml)

    def _wf(self, name):
        return self.target / ".github" / "workflows" / name

    def test_workflows_generated_from_enabled_and_rendered(self):
        (self.target / "org.toml").write_text('[workflows]\nenabled = ["gate", "release"]\n')
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertTrue(self._wf("org-gate.yml").is_file())
        self.assertTrue(self._wf("org-release.yml").is_file())
        self.assertFalse(self._wf("org-guardrails.yml").exists())   # not selected
        gate = self._wf("org-gate.yml").read_text()
        self.assertIn(sync.WORKFLOW_MARK, gate)
        self.assertIn("uses: ExampleOrg/.github/.github/workflows/org-gate.yml@v2", gate)
        self.assertNotIn(sync.ORG_PLACEHOLDER, gate)

    def test_no_workflows_when_unset_and_idempotent(self):
        (self.target / "org.toml").write_text('[gate]\nfast = "true"\n')   # no [workflows]
        r = sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertFalse(any(c.path.startswith(".github/workflows/org-") for c in r.changes))
        (self.target / "org.toml").write_text('[workflows]\nenabled = ["gate"]\n')
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        r2 = sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")   # re-run
        self.assertTrue(any(c.path == ".github/workflows/org-gate.yml" and c.action == "unchanged" for c in r2.changes))

    def test_deselected_managed_workflow_is_pruned(self):
        (self.target / "org.toml").write_text('[workflows]\nenabled = ["gate", "release"]\n')
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        (self.target / "org.toml").write_text('[workflows]\nenabled = ["gate"]\n')
        r = sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertFalse(self._wf("org-release.yml").exists())
        self.assertTrue(any("removed" in c.detail for c in r.changes))
        self.assertTrue(self._wf("org-gate.yml").is_file())

    def test_unmanaged_workflow_with_same_name_is_not_clobbered(self):
        # A reusable *definition* (or a repo's own file) without the managed marker is left alone.
        wf = self._wf("org-gate.yml")
        wf.parent.mkdir(parents=True)
        wf.write_text("name: my own gate\non:\n  workflow_call:\n")
        (self.target / "org.toml").write_text('[workflows]\nenabled = ["gate"]\n')
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertEqual(wf.read_text(), "name: my own gate\non:\n  workflow_call:\n")

    # ---- CLI

    def test_org_placeholder_is_rendered_and_detected(self):
        # The lean block carries no org reference itself; the placeholder lives in the
        # skills that ship with it (org-dev-env). render_block must still substitute it.
        synthetic = sync.BEGIN + "\nVersion: " + sync.VERSION_PLACEHOLDER + "\nsee github:" + sync.ORG_PLACEHOLDER + "/.github\n" + sync.END + "\n"
        out = sync.render_block(synthetic, "v1", "ExampleOrg")
        self.assertNotIn(sync.ORG_PLACEHOLDER, out)
        self.assertIn("github:ExampleOrg/.github", out)
        self.assertIn(sync.ORG_PLACEHOLDER, (SOURCE / "skills" / "org-dev-env" / "SKILL.md").read_text())
        # No git remote in a temp dir → visible placeholder rather than a wrong guess.
        self.assertEqual(sync.detect_org(self.target), "<org>")
        sync.apply(SOURCE, self.target, "v1", dry_run=False, org="ExampleOrg")
        self.assertIn("ExampleOrg", (self.target / ".claude" / "skills" / "org-dev-env" / "SKILL.md").read_text())

    def test_cli_json_output(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sync.main(["apply", "--source", str(SOURCE), "--target", str(self.target),
                            "--version", "v1", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertTrue(data["changed"])
        paths = {c["path"] for c in data["changes"]}
        self.assertTrue({"CLAUDE.md", ".claude/settings.json", ".mcp.json"} <= paths)
        self.assertTrue(any(p.startswith(".claude/skills/org-") for p in paths))
        self.assertTrue(any(p.startswith(".claude/agents/org-") for p in paths))


if __name__ == "__main__":
    unittest.main()

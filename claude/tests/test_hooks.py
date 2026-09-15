"""Tests for claude/orgfile.py and claude/hooks/*.py against a throwaway git repo."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE))
sys.path.insert(0, str(CLAUDE / "hooks"))
import orgfile  # noqa: E402
import check_actions_pinned  # noqa: E402
import check_commits  # noqa: E402
import check_pr  # noqa: E402
import check_secrets  # noqa: E402
import gen_codeowners  # noqa: E402
import new_deps  # noqa: E402
import provenance  # noqa: E402


def sh(cwd: Path, *cmd: str) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout


def run(mod, argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main(argv)
    return rc, buf.getvalue()


class Repo:
    def __init__(self, root: Path):
        self.root = root
        sh(root, "git", "init", "-q", "-b", "main")
        sh(root, "git", "config", "user.name", "Dev Person")
        sh(root, "git", "config", "user.email", "dev@example.test")

    def write(self, rel: str, text: str):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg: str, author: str | None = None, trailer: str = ""):
        sh(self.root, "git", "add", "-A")
        full = msg + ("\n\n" + trailer if trailer else "")
        cmd = ["git", "commit", "-q", "-m", full]
        if author:
            cmd += ["--author", author]
        sh(self.root, *cmd)
        return sh(self.root, "git", "rev-parse", "HEAD").strip()


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self.tmp.name))
        self.repo.write("README.md", "# t\n")
        self.repo.write(".gitignore", ".env\n")
        self.base = self.repo.commit("INFRA-1: init")

    def tearDown(self):
        self.tmp.cleanup()

    # ---- orgfile

    def test_orgfile_defaults_and_overrides(self):
        cfg = orgfile.load(self.repo.root)
        self.assertFalse(cfg["_present"])
        self.assertEqual(cfg["branches"]["integration"], "staging")
        self.repo.write("org.toml", '[gate]\nfast = "just test"\n[deploy.staging]\ncommand = "just deploy"\n')
        cfg = orgfile.load(self.repo.root)
        self.assertTrue(cfg["_present"])
        self.assertEqual(cfg["gate"]["full"], "just test")  # defaults to fast
        self.assertEqual(orgfile.validate(cfg), [])
        self.repo.write("org.toml", '[branches]\nintegration = "main"\n[deploy.prod]\nrunner = "x"\n')
        problems = orgfile.validate(orgfile.load(self.repo.root))
        self.assertTrue(any("integration" in p for p in problems))
        self.assertTrue(any("deploy.prod.command" in p for p in problems))
        rc, out = run(orgfile, [str(self.repo.root), "branches.integration"])
        self.assertEqual((rc, out.strip()), (0, "main"))

    def test_orgfile_example_parses(self):
        import tomllib
        tomllib.loads(orgfile.EXAMPLE)

    # ---- secrets

    def test_secrets_clean_then_hits(self):
        rc, _ = run(check_secrets, ["--root", str(self.repo.root)])
        self.assertEqual(rc, 0)
        self.repo.write("config.py", 'TOKEN = "ghp_' + "A" * 40 + '"\n')
        self.repo.write("nix/secrets/db.yaml", "password: plaintext\n")
        self.repo.write(".env", "X=1\n")
        sh(self.repo.root, "git", "add", "-f", ".env")
        head = self.repo.commit("INFRA-2: oops")
        rc, out = run(check_secrets, ["--root", str(self.repo.root), "--range", f"{self.base}..{head}", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out.strip().splitlines()[-1])
        self.assertGreaterEqual(data["hits"], 3)

    def test_secrets_sops_encrypted_passes_and_allow_marker(self):
        self.repo.write("nix/secrets/ok.yaml", "db: ENC[AES256_GCM,data:abc]\nsops:\n  mac: ENC[...]\n")
        self.repo.write("docs/vectors.md", 'xprv' + "9" * 110 + "  # org-secrets: allow\n")
        head = self.repo.commit("INFRA-3: sops")
        rc, _ = run(check_secrets, ["--root", str(self.repo.root), "--range", f"{self.base}..{head}"])
        self.assertEqual(rc, 0)

    # ---- commits / provenance

    def test_commit_prefix_and_provenance(self):
        self.repo.write("a.txt", "1\n")
        good = self.repo.commit("INFRA-5: add a", trailer="Claude-Session: https://example/session_x")
        self.repo.write("b.txt", "2\n")
        bad = self.repo.commit("add b without prefix")
        self.repo.write("c.txt", "3\n")
        bot = self.repo.commit("bump deps", author="dependabot[bot] <bot@example.test>")
        rng = f"{self.base}..{bot}"
        rc, out = run(check_commits, ["--root", str(self.repo.root), "--range", rng, "--json"])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["bad"], 1)
        rc, out = run(provenance, ["--root", str(self.repo.root), "--range", rng, "--json"])
        data = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(rc, 0)
        self.assertIn("author:claude-code", data["labels"])
        self.assertIn("author:human", data["labels"])
        self.assertIn("author:mixed", data["labels"])
        self.assertIn("author:bot", data["labels"])
        self.assertEqual(data["sessions"], ["https://example/session_x"])

    # ---- PR hygiene + ADR-required + generated

    def test_pr_checks(self):
        self.repo.write("org.toml", '[adr]\nrequired_paths = ["migrations/**"]\n[generated]\nfiles = [{ path = "flake.lock", inputs = ["flake.nix"] }]\n')
        self.repo.write("migrations/001.sql", "create table t;\n")
        self.repo.write("flake.lock", "{}\n")
        head = self.repo.commit("INFRA-7: migration")
        body = Path(self.tmp.name) / "body.md"
        body.write_text("## Summary\nstuff\n")
        rc, out = run(check_pr, ["--root", str(self.repo.root), "--range", f"{self.base}..{head}", "--body-file", str(body), "--title", "INFRA-7: migration", "--json"])
        data = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(rc, 1)
        self.assertEqual(data["errors"], 3)  # no verification, ADR required, generated w/o input
        self.assertIn("needs-adr", data["suggest_labels"])
        body.write_text("## Verification\n- `just ci` → green\n")
        self.repo.write("docs/adr/adr-001-x.md", "# ADR\n")
        self.repo.write("flake.nix", "{}\n")
        head = self.repo.commit("INFRA-7: adr + inputs")
        rc, out = run(check_pr, ["--root", str(self.repo.root), "--range", f"{self.base}..{head}", "--body-file", str(body), "--title", "INFRA-7: migration", "--json"])
        self.assertEqual(rc, 0)
        # waiver label also satisfies the ADR requirement
        rc, _ = run(check_pr, ["--root", str(self.repo.root), "--range", f"{self.base}..{head}", "--body-file", str(body), "--labels", "adr:not-needed", "--json"])
        self.assertEqual(rc, 0)

    # ---- actions pinned

    def test_actions_pinned(self):
        self.repo.write(".github/workflows/a.yml", "jobs:\n  x:\n    steps:\n      - uses: actions/checkout@v4\n      - uses: some/thing@main\n      - uses: ./.github/actions/local\n      - uses: other/tool@0123456789abcdef0123456789abcdef01234567\n")
        rc, out = run(check_actions_pinned, ["--root", str(self.repo.root)])
        self.assertEqual(rc, 1)
        self.assertIn("1 unpinned", out)

    # ---- new deps (offline)

    def test_new_deps_offline_lists_additions(self):
        self.repo.write("package.json", '{"dependencies": {"left-pad": "1.0.0"}}\n')
        self.repo.write("pyproject.toml", '[project]\ndependencies = ["requests>=2"]\n')
        base = self.repo.commit("INFRA-8: manifests")
        self.repo.write("package.json", '{"dependencies": {"left-pad": "1.0.0", "totally-made-up-pkg-xyz": "1.0.0"}}\n')
        self.repo.write("pyproject.toml", '[project]\ndependencies = ["requests>=2", "another-fake-pkg-xyz"]\n')
        head = self.repo.commit("INFRA-8: add deps")
        rc, out = run(new_deps, ["--root", str(self.repo.root), "--range", f"{base}..{head}", "--offline", "--json"])
        data = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(rc, 0)
        self.assertEqual({d["name"] for d in data["added"]}, {"totally-made-up-pkg-xyz", "another-fake-pkg-xyz"})

    # ---- git hooks (bash)

    def test_commit_msg_hook(self):
        hook = CLAUDE / "hooks" / "git" / "commit-msg"
        msg = Path(self.tmp.name) / "MSG"
        for subject, ok in (("INFRA-9: fine", True), ("no prefix here", False), ("Merge branch 'x'", True), ("Rollback main to v1.0.0", True)):
            msg.write_text(subject + "\n")
            rc = subprocess.run(["bash", str(hook), str(msg)], capture_output=True, text=True).returncode
            self.assertEqual(rc == 0, ok, subject)

    def test_pre_push_refuses_deploy_branch_and_allows_staging(self):
        hook = CLAUDE / "hooks" / "git" / "pre-push"
        self.repo.write("org.toml", '[gate]\nfast = "true"\n')
        self.repo.commit("INFRA-10: cfg")
        # stdin lines: local_ref local_sha remote_ref remote_sha
        line = "refs/heads/x 1111111111111111111111111111111111111111 refs/heads/main 2222222222222222222222222222222222222222\n"
        r = subprocess.run(["bash", str(hook), "origin", "url"], input=line, cwd=self.repo.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1); self.assertIn("refusing direct push", r.stdout)
        line = "refs/heads/x 1111111111111111111111111111111111111111 refs/heads/staging 2222222222222222222222222222222222222222\n"
        r = subprocess.run(["bash", str(hook), "origin", "url"], input=line, cwd=self.repo.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("running the fast gate", r.stdout)
        r = subprocess.run(["bash", str(hook), "origin", "url"], input="refs/heads/x 1 refs/heads/main 2\n", cwd=self.repo.root, capture_output=True, text=True, env={**os.environ, "ORG_HOOKS_SKIP": "1"})
        self.assertEqual(r.returncode, 0)

    def test_pre_commit_hook_blocks_staged_secret(self):
        hook = CLAUDE / "hooks" / "git" / "pre-commit"
        self.repo.write("cfg.py", 'KEY = "ghp_' + "B" * 40 + '"\n')
        sh(self.repo.root, "git", "add", "cfg.py")
        r = subprocess.run(["bash", str(hook)], cwd=self.repo.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1); self.assertIn("refusing to commit", r.stdout)
        self.repo.write("cfg.py", "KEY = None\n")
        sh(self.repo.root, "git", "add", "cfg.py")
        r = subprocess.run(["bash", str(hook)], cwd=self.repo.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # ---- CODEOWNERS

    def test_codeowners_block_is_managed(self):
        self.repo.write("org.toml", '[codeowners]\n"nix/secrets/**" = ["@acme/security"]\n')
        self.repo.write(".github/CODEOWNERS", "* @acme/everyone\n")
        self.assertEqual(gen_codeowners.apply(self.repo.root), "updated")
        text = (self.repo.root / ".github" / "CODEOWNERS").read_text()
        self.assertTrue(text.startswith(gen_codeowners.BEGIN))
        self.assertIn("nix/secrets/** @acme/security", text)
        self.assertTrue(text.endswith("* @acme/everyone\n"))
        self.assertEqual(gen_codeowners.apply(self.repo.root), "unchanged")


    def test_password_rule_ignores_template_references(self):
        import check_secrets
        rx = dict(check_secrets.PATTERNS)["password assignment"]
        self.assertIsNotNone(rx.search('token = "abcdefghijklmnopqrstu"'))
        self.assertIsNotNone(rx.search("password: 'hunter2hunter2hunter2'"))
        for ok in ('token = "\\${var.github_token}"', 'token = "${var.github_token}"',
                   'password: "{{ secrets.DB_PASSWORD }}"', 'api_key = "$API_KEY_FROM_ENV"'):
            self.assertIsNone(rx.search(ok), ok)

    def test_verification_section_needs_substance(self):
        cp = check_pr
        self.assertFalse(cp.verification_has_substance("\n- [ ] tests\n- [ ] manual\n<!-- fill me -->\nn/a\n"))
        self.assertFalse(cp.verification_has_substance("\n| Claim | How | Result |\n|---|---|---|\n"))
        self.assertTrue(cp.verification_has_substance("\n| gate | `nix flake check` | reproduced |\n"))
        self.assertTrue(cp.verification_has_substance("\nDeployed to staging and exercised the login flow by hand.\n"))
        self.assertTrue(cp.verification_has_substance("\ncurl https://staging.example/healthz -> 200\n## Next\nnothing\n"))
        self.assertFalse(cp.verification_has_substance("\n## Next\nDeployed to staging and exercised the login flow by hand.\n"))  # after the next heading

    def test_commit_msg_hook_warns_on_ticket_status(self):
        hook = CLAUDE / "hooks" / "git" / "commit-msg"
        snap = Path(self.tmp.name) / "summary.json"
        snap.write_text(json.dumps({"projects": [{"key": "INFRA", "epics": [], "issues": [
            {"key": "INFRA-1", "status": "In Progress"}, {"key": "INFRA-2", "status": "Done"}]}]}))
        msg = Path(self.tmp.name) / "MSG"
        env = {**os.environ, "ORG_JIRA_SNAPSHOT": str(snap)}
        for subject, expect in (("INFRA-1: fine", ""), ("INFRA-2: late", "warning: INFRA-2 is 'Done'"), ("INFRA-3: unknown", "not in the local Jira snapshot")):
            msg.write_text(subject + "\n")
            r = subprocess.run(["bash", str(hook), str(msg)], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # never blocks
            self.assertIn(expect, r.stdout)


if __name__ == "__main__":
    unittest.main()


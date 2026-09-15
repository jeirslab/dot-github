"""Runner selection (runner_pick.py) and bootstrap (runner_bootstrap.py) against fakes."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE))
import runner_bootstrap as rb  # noqa: E402
import runner_pick as rp  # noqa: E402

RUNNERS = [
    {"name": "gh-runner-1", "status": "online", "busy": False, "labels": [{"name": "self-hosted"}, {"name": "org-fleet"}]},
    {"name": "gh-runner-2", "status": "offline", "busy": False, "labels": [{"name": "org-fleet"}]},
    {"name": "mac-1", "status": "online", "busy": False, "labels": [{"name": "macos"}]},
]


class PickTests(unittest.TestCase):
    def test_modes(self):
        fetch = lambda owner, repo, token: RUNNERS
        self.assertEqual(rp.pick("", "org-fleet", "acme", None, "t", fetch)[0], "ubuntu-latest")
        self.assertEqual(rp.pick("forced-label", "org-fleet", "acme", None, None, fetch)[0], "forced-label")
        self.assertEqual(rp.pick("auto", "org-fleet", "acme", None, "t", fetch), ("org-fleet", "online: gh-runner-1"))
        self.assertEqual(rp.pick("auto", "org-fleet", "acme", None, None, fetch)[0], "ubuntu-latest")   # no token → hosted
        self.assertEqual(rp.pick("auto", "org-fleet", "acme", None, "t", lambda *a: [])[0], "ubuntu-latest")  # none online
        def boom(*a): raise OSError("down")
        label, reason = rp.pick("auto", "org-fleet", "acme", None, "t", boom)
        self.assertEqual(label, "ubuntu-latest"); self.assertIn("failed", reason)

    def test_cli_writes_github_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            with mock.patch.dict(os.environ, {"ORG_RUNNER": "org-fleet", "GITHUB_OUTPUT": str(out)}, clear=False):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rp.main(["--owner", "acme", "--github-output"])
            self.assertEqual(buf.getvalue().strip(), "org-fleet")
            self.assertIn("label=org-fleet", out.read_text())


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dep = Path(self.tmp.name) / "deployments"
        (self.dep / "nix" / "hosts" / "pve").mkdir(parents=True)
        (self.dep / "nix" / "hosts" / "pve" / "a.nix").write_text('x = { vm_id = 126; internal_ip = "10.40.0.126"; };\n')
        (self.dep / "nix" / "hosts" / "pve" / "b.nix").write_text('y = { vm_id = 128; internal_ip = "10.40.0.127"; };\n')

    def test_pick_free_pair_skips_ids_and_ips_in_use(self):
        self.assertEqual(rb.pick_free(self.dep / "nix" / "hosts", "10.40.0", 126, 199), (129, "10.40.0.129"))

    def test_host_step_writes_host_and_vendored_module(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rb.main(["--deployments", str(self.dep), "--owner", "acme", "--cluster", "pve-c", "--node", "pve-n", "--only", "host"])
        host = (self.dep / "nix" / "hosts" / "pve" / "gh-runner.nix").read_text()
        self.assertIn("vm_id = 129;", host); self.assertIn('internal_ip = "10.40.0.129"', host)
        self.assertIn('url = "https://github.com/acme"', host); self.assertIn("labels = [ \"org-fleet\" ]", host)
        self.assertIn("imports = [ ../../modules/org-runner ];", host)
        mod = (self.dep / "nix" / "modules" / "org-runner" / "default.nix").read_text()
        self.assertIn("options.org.runner", mod); self.assertTrue(mod.startswith("# VENDORED"))
        # idempotent
        buf = io.StringIO()
        with redirect_stdout(buf):
            rb.main(["--deployments", str(self.dep), "--owner", "acme", "--cluster", "pve-c", "--node", "pve-n", "--only", "host"])
        self.assertEqual(buf.getvalue().count("unchanged"), 2)

    def test_dry_run_prints_every_command_and_touches_nothing(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_RUNNER_PAT": "x"}), redirect_stdout(buf):
            rb.main(["--deployments", str(self.dep), "--owner", "acme", "--cluster", "c", "--node", "n", "--dry-run"])
        out = buf.getvalue()
        for s in ("fleet devtools secrets keys add integrations/github/runner_token <GITHUB_RUNNER_PAT>",
                  "fleet deploy tf apply platform.core --yes", "fleet deploy nixos apply host gh-runner",
                  "fleet deploy nixos apply host netcore", "set variable ORG_RUNNER=auto", "dispatch org-runner-smoke.yml"):
            self.assertIn(s, out)
        self.assertFalse((self.dep / "nix" / "hosts" / "pve" / "gh-runner.nix").exists())

    def test_api_steps_against_a_fake(self):
        class FakeGH(rb.GitHub):
            def __init__(self): self.calls = []; self.polls = 0
            def runners(self, owner):
                self.polls += 1
                return RUNNERS if self.polls >= 2 else []
            def set_variable(self, repo, name, value): self.calls.append(("var", repo, name, value)); return "created"
            def dispatch(self, repo, workflow, ref): self.calls.append(("dispatch", repo, workflow, ref))
            def latest_run(self, repo, workflow, since): return {"status": "completed", "conclusion": "success", "html_url": "https://gh/run/1"}
        gh = FakeGH()
        a = mock.Mock(owner="acme", count=1, labels=["org-fleet"], timeout=5, poll=0, ref="main", name="gh-runner")
        with redirect_stdout(io.StringIO()):
            rb.step_wait(a, False, gh); rb.step_select(a, False, gh); rb.step_smoke(a, False, gh)
        self.assertEqual(gh.polls, 2)
        self.assertEqual(gh.calls, [("var", "acme/.github", "ORG_RUNNER", "auto"), ("dispatch", "acme/.github", "org-runner-smoke.yml", "main")])


if __name__ == "__main__":
    unittest.main()

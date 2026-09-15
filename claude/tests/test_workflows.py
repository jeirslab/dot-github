"""Static policy tests for the org workflows (ADR-009). These run offline on every PR and in
`_workflow-tests.yml`; they catch the mistakes that only show up in Actions otherwise."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
TEMPLATES = ROOT / "workflow-templates"

# Reusable workflows that _workflow-tests.yml cannot exercise, each with the reason.
NOT_EXERCISED = {
    "org-oci.yml": "needs artifacts.oci.attr in org.toml and a registry login",
    "sync-release.yml": "only a PR merged into a deploy branch is a release; exercised by the first real merge",
    "org-branch-guard.yml": "would open an issue + revert PR on every feature-test push; its script is exercised via verify_pr",
    "org-adr-consolidate.yml": "runs in the docs repository; dispatchable there",
    "promote-pr.yml": "deprecated (ADR-002)",
    "version-and-package-source.yml": "legacy, v1 callers only",
    "create-branches.yml": "one-shot seeding; exercised by bootstrap",
    "_compute-version.yml": "internal helper of _version-workflows-repo.yml (main-only tagging)",
}
AUTOMATIC = ("push", "schedule", "pull_request", "pull_request_target")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def triggers(doc: dict) -> dict:
    on = doc.get(True, doc.get("on"))
    if isinstance(on, list):
        return {k: {} for k in on}
    if isinstance(on, str):
        return {on: {}}
    return on or {}


class WorkflowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.files = sorted(WF.glob("*.yml"))
        self.docs = {f.name: load(f) for f in self.files}

    def test_every_workflow_parses_and_has_a_name(self):
        for name, doc in self.docs.items():
            self.assertIn("name", doc, name)
            self.assertTrue(doc.get("jobs"), f"{name}: no jobs")

    def test_public_workflows_carry_doc_comments(self):
        for f in self.files:
            if f.name.startswith("_"):
                continue
            self.assertIn("# @doc:", f.read_text(), f"{f.name}: missing '# @doc:' (WORKFLOWS.md is generated from it)")

    def test_automatic_jobs_are_guarded(self):
        """A job that starts on push/schedule/PR must carry the kill switch AND the feature-test
        clause; jobs that `needs` a guarded job inherit it. Reusable/dispatch-only workflows are
        guarded by their callers."""
        for name, doc in self.docs.items():
            t = triggers(doc)
            if not any(k in t for k in AUTOMATIC):
                continue
            for job_id, job in doc["jobs"].items():
                if job.get("needs"):
                    continue
                cond = str(job.get("if", ""))
                self.assertIn("vars.ORG_WORKFLOWS_ENABLED", cond, f"{name}: job '{job_id}' starts automatically without the kill switch")
                self.assertIn("vars.ORG_FEAT_TEST", cond, f"{name}: job '{job_id}' lacks the feature-test clause (ADR-009)")

    def test_feature_test_branch_is_a_trigger_where_main_is(self):
        """Org-wide workflows that run on push to main also run on push to the feature-test
        branch, so a change is exercised there first. Tagging stays main-only by design."""
        exempt = {"_version-workflows-repo.yml"}
        for name, doc in self.docs.items():
            push = triggers(doc).get("push") or {}
            branches = push.get("branches") if isinstance(push, dict) else None
            if not branches or name in exempt:
                continue
            if "main" in branches:
                self.assertIn("feature-test", branches, f"{name}: push trigger has main but not feature-test")

    def test_org_wide_workflows_resolve_targets_through_targets_py(self):
        """No workflow enumerates repos.txt on its own; targets.py is the single place that
        knows about feature-test confinement."""
        for name, doc in self.docs.items():
            if name == "_workflow-tests.yml":
                continue
            runs = "\n".join(str(step.get("run", "")) for job in doc["jobs"].values() for step in job.get("steps", []))
            if "repos.txt" in runs:
                self.assertIn("targets.py", runs, f"{name}: a run step reads repos.txt without claude/targets.py")

    def test_local_reusable_calls_exist(self):
        for name, doc in self.docs.items():
            for job_id, job in doc["jobs"].items():
                uses = job.get("uses", "")
                if uses.startswith("./"):
                    self.assertTrue((ROOT / uses[2:]).is_file(), f"{name}: job '{job_id}' uses missing {uses}")

    def test_templates_pin_v2_and_point_at_existing_workflows(self):
        for f in sorted(TEMPLATES.glob("*.yml")):
            for m in re.finditer(r"uses:\s*<org>/\.github/\.github/workflows/([\w.-]+)@(\S+)", f.read_text()):
                self.assertTrue((WF / m.group(1)).is_file(), f"{f.name}: template calls missing {m.group(1)}")
                self.assertEqual(m.group(2), "v2", f"{f.name}: template must pin @v2, got @{m.group(2)}")
                self.assertIn("workflow_call", triggers(self.docs[m.group(1)]), f"{m.group(1)} is called by a template but has no workflow_call")

    def test_every_reusable_workflow_is_exercised_or_excused(self):
        tests = self.docs["_workflow-tests.yml"]
        called = {j["uses"].split("/")[-1] for j in tests["jobs"].values() if str(j.get("uses", "")).startswith("./")}
        for name, doc in self.docs.items():
            if "workflow_call" not in triggers(doc):
                continue
            self.assertTrue(name in called or name in NOT_EXERCISED,
                            f"{name} is reusable but neither called by _workflow-tests.yml nor listed in NOT_EXERCISED with a reason")
        for name in NOT_EXERCISED:
            self.assertIn(name, self.docs, f"NOT_EXERCISED names a workflow that no longer exists: {name}")

    RUNNER_VAR = "${{ needs.runner.outputs.label }}"
    PICKER = "./.github/workflows/_runner.yml"
    TRUSTED = {"sync-agent-config.yml", "org-scorecard.yml", "org-rulesets.yml", "org-branch-sweep.yml",
               "org-bootstrap.yml", "org-adr-consolidate.yml", "org-platform-github.yml"}
    GATES = {"_claude-config-ci.yml", "org-gate.yml", "org-guardrails.yml", "_workflow-tests.yml"}

    def test_runner_selection_policy(self):
        """ADR-011: trusted org-level jobs select the runner by variable (fleet runner when set,
        hosted otherwise); PR gates and the workflow test suite never do — they run untrusted
        branches and stay hosted."""
        for name in self.TRUSTED:
            jobs = self.docs[name]["jobs"]
            self.assertEqual(jobs.get("runner", {}).get("uses"), self.PICKER, f"{name}: needs a 'runner' job calling the picker")
            for job_id, job in jobs.items():
                if "uses" in job:
                    continue
                self.assertEqual(job.get("runs-on"), self.RUNNER_VAR, f"{name}: job '{job_id}' must run on the picked label")
                needs = job.get("needs", [])
                self.assertIn("runner", needs if isinstance(needs, list) else [needs], f"{name}: job '{job_id}' must need the picker")
        for name in self.GATES:
            for job_id, job in self.docs[name]["jobs"].items():
                self.assertNotIn("needs.runner", str(job.get("runs-on", "")), f"{name}: job '{job_id}' is a gate and must stay on hosted runners")

    def test_hosted_only_setup_steps_are_conditional(self):
        """Steps that install Nix must be skipped on the fleet runner (it has Nix natively)."""
        for name, doc in self.docs.items():
            for job_id, job in doc["jobs"].items():
                if job.get("runs-on") != self.RUNNER_VAR:
                    continue
                for step in job.get("steps", []):
                    if "nix-installer-action" in str(step.get("uses", "")):
                        self.assertIn("github-hosted", str(step.get("if", "")), f"{name}: job '{job_id}' installs Nix unconditionally on a variable runner")

    def test_targets_confinement(self):
        import sys
        sys.path.insert(0, str(ROOT / "claude"))
        import targets
        env = {"ORG_FEAT_TEST": "true", "GITHUB_REPOSITORY": "acme/.github"}
        r = targets.resolve("acme", ROOT / "claude" / "repos.txt", "acme/app acme/other", env)
        self.assertEqual(r["repos"], ["acme/.github"]); self.assertEqual(r["ref"], "feature-test"); self.assertTrue(r["feat_test"])
        # same-org override kept
        r = targets.resolve("acme", None, "acme/app, acme/other", {"ORG_FEAT_TEST": "false"})
        self.assertEqual(r["repos"], ["acme/app", "acme/other"]); self.assertEqual(r["ref"], "")
        # same-org confinement: cross-org targets are dropped (a broad token cannot escape the org)
        r = targets.resolve("acme", None, "acme/app foreignorg/x other/y", {})
        self.assertEqual(r["repos"], ["acme/app"])
        self.assertIn("foreignorg/x", r["note"]); self.assertIn("other/y", r["note"])
        # an allowlist owned by a different org resolves to nothing for this org
        r = targets.resolve("acme", ROOT / "claude" / "repos.txt", "", {})   # repos.txt is jeirslab/*
        self.assertEqual(r["repos"], []); self.assertTrue(r["note"])
        # a USER-owned .github confines to that user's repos (owner may be a user, not an org)
        r = targets.resolve("alice", None, "alice/app orgx/y bob/z", {})
        self.assertEqual(r["repos"], ["alice/app"]); self.assertIn("orgx/y", r["note"])


if __name__ == "__main__":
    unittest.main()

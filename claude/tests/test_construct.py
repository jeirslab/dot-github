"""Unit tests for the constructor's safety-critical logic (claude/construct.py): source-org
detection, the leak verification that gates a public build, and the create-trigger transform.
These are subprocess-free and fast; the full build is exercised by dogfooding."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "claude"))
import construct  # noqa: E402


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class DetectSourceOrgTests(unittest.TestCase):
    def test_reads_owner_from_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)
            write(src / "nix/github/manifest.nix", '{\n  github = {\n    owner = "acme";\n  };\n}\n')
            self.assertEqual(construct.detect_source_org(src), "acme")


class VerifyTests(unittest.TestCase):
    def _target(self, d: str) -> Path:
        return Path(d)

    def test_clean_same_org_passes(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._target(d)
            write(t / "docs/adr/adr-001.md", "jeirslab designed this")   # source org, same-org build: allowed
            construct.verify(t, "jeirslab", "template", "jeirslab")       # must not raise

    def test_org_name_in_mechanism_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._target(d)
            write(t / "claude/sync.py", "# built for acme\n")             # org name in a mechanism file
            with self.assertRaises(SystemExit):
                construct.verify(t, "acme", "external", "jeirslab")

    def test_source_org_leak_cross_org_fails(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._target(d)
            write(t / "README.md", "forked from jeirslab\n")              # source org leaked into another org's build
            with self.assertRaises(SystemExit):
                construct.verify(t, "acme", "external", "jeirslab")

    def test_source_org_allowed_in_same_org_build(self):
        with tempfile.TemporaryDirectory() as d:
            t = self._target(d)
            write(t / "README.md", "jeirslab engine\n")
            construct.verify(t, "jeirslab", "self", "jeirslab")           # org == source org: not a leak


class CreateCleanupTransformTests(unittest.TestCase):
    ENGINE_WF = (
        "name: Template Cleanup\n"
        "# @doc: cleanup\n\n"
        "on:\n  workflow_dispatch:\n\n"
        "permissions:\n  contents: write\n\n"
        "jobs:\n  cleanup:\n"
        "    if: ${{ !github.event.repository.is_template }}\n"
        "    runs-on: ubuntu-latest\n"
    )

    def test_adds_create_trigger_and_tightens_guard(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            write(t / construct.CLEANUP_WF, self.ENGINE_WF)
            construct.enable_create_cleanup(t)
            out = (t / construct.CLEANUP_WF).read_text(encoding="utf-8")
            self.assertIn("on:\n  create:\n  workflow_dispatch:\n", out)
            self.assertIn("github.event_name != 'create' || github.event.ref_type == 'branch'", out)

    def test_rejects_unexpected_format(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            write(t / construct.CLEANUP_WF, "on:\n  push:\n")             # not the engine's dispatch-only shape
            with self.assertRaises(SystemExit):
                construct.enable_create_cleanup(t)


if __name__ == "__main__":
    unittest.main()

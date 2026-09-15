#!/usr/bin/env python3
"""
Generate WORKFLOWS.md with a table of public workflows.
Extracts doc-strings from workflows and links to repo files.

Usage:
  python .github/scripts/generate-workflow-docs.py
"""

import re
import sys
from pathlib import Path

WORKFLOWS_DIR = Path(".github/workflows")
OUTPUT_FILE = Path("WORKFLOWS.md")


def _repo_slug() -> str:
    """owner/repo from GITHUB_REPOSITORY (Actions) or the origin remote (local); never hard-coded."""
    import os, subprocess
    slug = os.environ.get("GITHUB_REPOSITORY")
    if slug:
        return slug
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True).stdout.strip()
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "<org>/.github"


BLOB_URL = f"https://github.com/{_repo_slug()}/blob/main/.github/workflows"
VERSION_PLACEHOLDER = "{{WORKFLOW_VERSION}}"


def extract_workflow_info(filepath: Path) -> dict:
    """Extract name and doc-string from workflow YAML."""
    try:
        content = filepath.read_text(encoding="utf-8")

        # Extract name
        name_match = re.search(r'^name:\s*(.+)$', content, re.MULTILINE)
        name = name_match.group(1).strip() if name_match else "Unknown"

        # Extract doc-string (lines starting with '# @doc:')
        doc_lines = re.findall(r'#\s*@doc:\s*(.+)$', content, re.MULTILINE)
        doc = " ".join(doc_lines).strip() if doc_lines else "(No description)"

        return {
            "filename": filepath.name,
            "name": name,
            "doc": doc,
        }
    except Exception as e:
        print(f"⚠️  Could not parse {filepath}: {e}")
        return {
            "filename": filepath.name,
            "name": "Unknown",
            "doc": "(Parse error)",
        }


def is_public(filename: str) -> bool:
    """Public workflows don't start with underscore."""
    return not filename.startswith("_")


def generate_docs() -> None:
    """Generate WORKFLOWS.md."""

    if not WORKFLOWS_DIR.exists():
        print(f"❌ Error: {WORKFLOWS_DIR} not found")
        sys.exit(1)

    # Discover workflows
    workflows = []
    for filepath in sorted(WORKFLOWS_DIR.glob("*.yml")):
        if not is_public(filepath.name):
            continue
        workflows.append(extract_workflow_info(filepath))

    if not workflows:
        print(f"⚠️  No public workflows found in {WORKFLOWS_DIR}")
        return

    print(f"📋 Found {len(workflows)} public workflows")

    # Generate markdown table
    lines = [
        "# Workflows",
        "",
        "| Workflow | Description |",
        "|----------|-------------|",
    ]

    for wf in workflows:
        wf_link = f"[{wf['filename']}]({BLOB_URL}/{wf['filename']})"
        doc_short = wf["doc"]
        lines.append(f"| {wf_link} | {doc_short} |")

    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Generated {OUTPUT_FILE}")
    print(f"   Documented {len(workflows)} workflows")


if __name__ == "__main__":
    try:
        generate_docs()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

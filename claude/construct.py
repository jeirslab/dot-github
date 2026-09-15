#!/usr/bin/env python3
"""Construct a fresh `.github` for an organization from this engine (the sanctioned build).

This repository is the public engine/template source. An organization's operational
`.github` is a *generated instance* of it — never a hand-forked copy. The same builder
serves two audiences:

  --profile self       Bake THIS org's real data (repos.txt, repos.toml, manifest.nix,
                        org.toml kept as-is) and drop the engine's own ADR history. Used to
                        (re)build the org's operational `.github` from the engine.

  --profile external   Blank the org-specific data to worked-example starters and render
                        every reference to the target org. Used to seed another org's
                        `.github`, and to refresh the public "Use this template" repo.

Both profiles:
  - strip the engine's docs/adr via the sanctioned .github/scripts/template-init.sh (leaving
    the empty skeleton org-bootstrap seeds), which also writes the template marker so the
    create-triggered cleanup workflow is a no-op in the instance;
  - re-render the installed .claude/ (skills, subagents, hooks, settings, MCP, CODEOWNERS)
    and the CLAUDE.md baseline block for the target org via claude/sync.py;
  - regenerate WORKFLOWS.md with the target org's blob URLs.

The engine is company-agnostic by construction (mechanism files never name the org; CI
enforces it), so this builder only rewrites DATA and regenerates GENERATED files. `external`
additionally proves the result: zero occurrences of the source org anywhere in the output.

Usage:
  construct.py --org <name> --profile {self,external} --target <dir> [--source <engine dir>]
               [--version <stamp>] [--force]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "result"}
TEXT_SUFFIXES = {".md", ".txt", ".toml", ".nix", ".py", ".sh", ".json", ".yml", ".yaml", ""}


def export_tree(source: Path, target: Path, ref: str) -> None:
    """Materialize the engine at `ref` into `target`. Uses `git archive`, so ONLY committed
    files are exported — untracked and gitignored files (local settings.local.json, .env,
    secrets, nested worktrees) can never leak into a generated repo. Falls back to a filtered
    copytree only when the source is not a git repository."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    is_git = subprocess.run(["git", "-C", str(source), "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True).returncode == 0
    if is_git:
        archive = subprocess.run(["git", "-C", str(source), "archive", ref],
                                 capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)
        return
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=lambda d, names: {n for n in names if n in EXCLUDE_DIRS})


def detect_source_org(source: Path) -> str:
    """The engine's own org, read from data — never hardcoded here (so the builder itself
    names no company and can be dropped into any org unchanged)."""
    m = source / "nix" / "github" / "manifest.nix"
    if m.is_file():
        mo = re.search(r'owner\s*=\s*"([^"]+)"', m.read_text(encoding="utf-8"))
        if mo:
            return mo.group(1)
    try:
        url = subprocess.run(["git", "-C", str(source), "remote", "get-url", "origin"],
                             capture_output=True, text=True, check=True).stdout.strip()
        mo = re.search(r"[:/]([^/:]+)/[^/]+?(?:\.git)?$", url)
        if mo:
            return mo.group(1)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    raise SystemExit("construct: cannot determine the source org (no manifest owner, no origin remote)")


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    e = dict(os.environ)
    if env:
        e.update(env)
    subprocess.run(cmd, cwd=str(cwd), env=e, check=True)


def strip_adr(target: Path) -> None:
    script = target / ".github" / "scripts" / "template-init.sh"
    if not script.is_file():
        raise SystemExit(f"missing {script} — cannot strip ADRs")
    run(["bash", str(script), "--force"], cwd=target)


CLEANUP_WF = ".github/workflows/template-cleanup.yml"


def enable_create_cleanup(target: Path) -> None:
    """Only in the public template source: the cleanup also runs automatically on `create`
    (branch creation). This is safe ONLY here — the template repo is is_template=true so the
    guard skips it, while a repo generated from it (is_template=false) runs it. The engine
    ships a workflow_dispatch-only cleanup (a create trigger would be destructive in any
    is_template=false repo, including the engine); this adds the create trigger for the
    template repo's button path."""
    p = target / CLEANUP_WF
    text = p.read_text(encoding="utf-8")
    trig_old, trig_new = "on:\n  workflow_dispatch:\n", "on:\n  create:\n  workflow_dispatch:\n"
    if_old = "if: ${{ !github.event.repository.is_template }}"
    if_new = ("if: ${{ !github.event.repository.is_template "
              "&& (github.event_name != 'create' || github.event.ref_type == 'branch') }}")
    if trig_old not in text or if_old not in text:
        raise SystemExit(f"{CLEANUP_WF}: unexpected format; cannot enable the create trigger")
    p.write_text(text.replace(trig_old, trig_new).replace(if_old, if_new), encoding="utf-8")


def replace_in_text_files(target: Path, subs: list[tuple[str, str]], excludes: list[str]) -> None:
    """Substitute in DATA/DOC text files only; never in mechanism/tooling files (`excludes`),
    which must stay company-agnostic and would be corrupted by a blind rewrite."""
    excluded = {(target / e).resolve() for e in excludes}
    for p in target.rglob("*"):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        rp = p.resolve()
        if any(rp == e or e in rp.parents for e in excluded):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = text
        for old, rep in subs:
            new = new.replace(old, rep)
        if new != text:
            p.write_text(new, encoding="utf-8")


def write_starters(target: Path, org: str) -> None:
    """external profile: replace org-specific data with worked-example starters."""
    (target / "claude" / "repos.txt").write_text(
        f"# {org} fleet allowlist. One \"owner/repo\" per line; \"#\" comments.\n"
        f"# Supersede target: claude/repos.toml (ADR-012).\n"
        f"{org}/.github        # the org engine itself (dogfood)\n",
        encoding="utf-8",
    )
    (target / "claude" / "repos.toml").write_text(
        f"# repos.toml — the {org} org fleet registry (ADR-012).\n"
        f"# One [[repo]] per managed repository; the .github engine dogfoods itself.\n\n"
        f"[[repo]]\n"
        f'slug = "{org}/.github"\n'
        f'description = "{org} org engine: reusable workflows, agent baseline, dev-env flake, ADRs"\n',
        encoding="utf-8",
    )
    # org.toml → the neutral starter the tooling ships. This builds a `.github` ENGINE repo,
    # which hosts the reusable org-*.yml definitions and consumes no callers itself, so its
    # [workflows].enabled is empty (the example default is for an org's *other* repos).
    example = subprocess.run(
        [sys.executable, str(target / "claude" / "orgfile.py"), "--example"],
        capture_output=True, text=True, check=True,
    ).stdout
    example = example.replace(
        'enabled = ["gate", "guardrails"]  # options: gate, guardrails, release, deploy, oci, branch-guard',
        'enabled = []  # a .github engine hosts the reusable workflows; consumer repos set their own',
    )
    (target / "org.toml").write_text(example, encoding="utf-8")
    # Drop engine-internal docs that only make sense in the template's own repo.
    for doc in ("docs/SHAKEDOWN.md",):
        p = target / doc
        if p.exists():
            p.unlink()
    # Trim the target CLAUDE.md's repo-specific handoff tail (below the baseline block).
    claude_md = target / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8")
        text = re.split(r"\n## Current state \(handoff", text, maxsplit=1)[0].rstrip() + "\n"
        claude_md.write_text(text, encoding="utf-8")


def set_manifest_owner(target: Path, source_org: str, org: str) -> None:
    m = target / "nix" / "github" / "manifest.nix"
    if m.exists():
        m.write_text(m.read_text(encoding="utf-8").replace(source_org, org), encoding="utf-8")


def rerender_agent_config(target: Path, org: str, version: str) -> None:
    run([sys.executable, str(target / "claude" / "sync.py"), "apply",
         "--source", str(target / "claude"), "--target", str(target),
         "--org", org, "--version", version], cwd=target)


def regen_workflow_docs(target: Path, repo_slug: str) -> None:
    run([sys.executable, str(target / ".github" / "scripts" / "generate-workflow-docs.py")],
        cwd=target, env={"GITHUB_REPOSITORY": repo_slug})


# ------------------------------------------------------------------ verification

# The mechanism files CI forbids the org name from (kept in step with _claude-config-ci.yml).
MECHANISM = [
    "claude/sync.py", "claude/org-check.sh", "claude/jira", "claude/targets.py",
    "claude/runner_pick.py", "claude/runner_bootstrap.py", "claude/construct.py",
    "flake.nix", "nix/github/options.nix", "nix/github/emit.nix",
    "nix/runner/module.nix", "nix/runner/host.nix", ".github/workflows", ".github/scripts",
]


def _grep_token(target: Path, token: str, paths: list[str]) -> list[str]:
    hits: list[str] = []
    for rel in paths:
        base = target / rel
        files = [base] if base.is_file() else (base.rglob("*") if base.is_dir() else [])
        for p in files:
            if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if token.lower() in line.lower():
                        hits.append(f"{p.relative_to(target)}:{i}: {line.strip()[:100]}")
            except (UnicodeDecodeError, OSError):
                pass
    return hits


def verify(target: Path, org: str, profile: str, source_org: str) -> None:
    problems: list[str] = []
    # 1. The org name must never appear in a mechanism file (same invariant CI checks).
    mech = _grep_token(target, org, MECHANISM)
    if mech:
        problems.append(f"org name '{org}' in mechanism file(s):\n  " + "\n  ".join(mech))
    # 2. When building for a DIFFERENT org, the source org must be fully gone from the tree.
    #    (self/template builds are the source org's own, so its name legitimately remains.)
    if org != source_org:
        leaks = _grep_token(target, source_org, ["."])
        if leaks:
            problems.append(f"leaked source org '{source_org}' into a build for '{org}':\n  " + "\n  ".join(leaks[:20]))
    if problems:
        raise SystemExit("construct: verification FAILED\n" + "\n".join(problems))
    print(f"construct: verification OK ({profile}, org={org})")


def construct(source: Path, target: Path, org: str, profile: str, version: str, ref: str) -> None:
    source_org = detect_source_org(source)
    export_tree(source, target, ref)
    # `template` builds the public engine/template source itself: it keeps the design ADRs
    # (they are the rationale) and writes no marker, so a repo generated FROM it still cleans.
    if profile != "template":
        strip_adr(target)
    if profile in ("external", "template"):
        write_starters(target, org)
    if profile == "template":
        enable_create_cleanup(target)
    set_manifest_owner(target, source_org, org)
    if org != source_org:
        # Any remaining data/doc references to the source org (descriptions, README prose)
        # become the target org. Mechanism files are excluded, so tooling stays intact.
        replace_in_text_files(target, [(source_org, org)], excludes=MECHANISM)
    rerender_agent_config(target, org, version)
    repo_slug = f"{org}/dot-github" if profile == "template" else f"{org}/.github"
    regen_workflow_docs(target, repo_slug)
    verify(target, org, profile, source_org)
    print(f"construct: built {profile} for '{org}' at {target} (source org: {source_org})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", required=True, help="target organization login")
    ap.add_argument("--profile", choices=("self", "external", "template"), required=True,
                    help="self: bake this org's real data; external: worked-example starters for another org; "
                         "template: the public engine/template source (starters, keeps ADRs, no marker)")
    ap.add_argument("--target", type=Path, required=True, help="output directory (created/overwritten)")
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1],
                    help="engine checkout to build from (default: this repo)")
    ap.add_argument("--version", default="constructed", help="baseline version stamp for the sync render")
    ap.add_argument("--ref", default="HEAD", help="git ref of the engine to build from (default: HEAD)")
    ap.add_argument("--force", action="store_true", help="overwrite a non-empty --target")
    args = ap.parse_args(argv)

    target = args.target.resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        raise SystemExit(f"{target} is not empty; pass --force to overwrite")
    construct(args.source.resolve(), target, args.org, args.profile, args.version, args.ref)
    return 0


if __name__ == "__main__":
    sys.exit(main())

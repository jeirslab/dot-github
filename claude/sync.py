#!/usr/bin/env python3
"""Sync the organization agent baseline into a consuming repository.

Stdlib only, so it runs identically in a GitHub Actions job, on a laptop, or on any
other forge (deployments ADR-068: the workflow is a thin shell; this is the logic).

What it does to a target checkout (INFRA-272, ADR-001, ADR-004):

  CLAUDE.md        The org baseline (claude/CLAUDE.md) is a block delimited by
                   <!-- BEGIN ORG BASELINE --> / <!-- END ORG BASELINE -->.
                   It is inserted at the top of the repo's CLAUDE.md (or .claude/CLAUDE.md
                   if that is what the repo uses; CLAUDE.md at the root is created when
                   neither exists). If the markers are already present the content between
                   them is replaced wholesale. Everything outside the markers is untouched.
                   The version stamp only changes when the block content changes, so an
                   unchanged template never produces a diff.

  .claude/settings.json   Merged with claude/settings.json: lists under "permissions" are
                   unioned (org entries first), nested dicts are merged recursively with the
                   org value winning on conflicts, keys the baseline does not define are
                   preserved.

  .mcp.json        "mcpServers" merged by server name: org-defined servers are written
                   verbatim, repo-defined servers with other names are preserved.

  .claude/skills/  and  .claude/agents/   Org skills (claude/skills/<name>/, ADR-006) and org
                   subagents (claude/agents/<name>.md) are copied in, rendered like the block
                   ({{ORG_GITHUB}}). Only entries whose name starts with "org-" are touched:
                   they are replaced wholesale; repo-owned skills/agents are never modified.

Usage:
  sync.py apply --source <dir with CLAUDE.md/settings.json/mcp.json> --target <repo checkout>
                --version <stamp> [--org <github-org>] [--dry-run] [--json]
  sync.py check --source ... --target ...        # exit 1 if the target is out of sync
  sync.py render --source ... --version <stamp>  # print the rendered block
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

BEGIN = "<!-- BEGIN ORG BASELINE -->"
END = "<!-- END ORG BASELINE -->"
VERSION_PLACEHOLDER = "{{ORG_BASELINE_VERSION}}"
# The GitHub organization name is rendered in, never written into the template, so a
# company/org rename is a change of one argument (--org) rather than a search-and-replace.
ORG_PLACEHOLDER = "{{ORG_GITHUB}}"
VERSION_RE = re.compile(r"Version: (\S+)")

CLAUDE_MD_CANDIDATES = (".claude/CLAUDE.md", "CLAUDE.md")
SETTINGS_PATH = ".claude/settings.json"
MCP_PATH = ".mcp.json"
SKILLS_DIR = ".claude/skills"   # org skills:    claude/skills/<name>/  → .claude/skills/<name>/
AGENTS_DIR = ".claude/agents"   # org subagents: claude/agents/<name>.md → .claude/agents/<name>.md
HOOKS_DIR = ".claude/hooks/org" # org hook scripts: claude/hooks/*.py (+ orgfile.py) → .claude/hooks/org/ — local guardrails
                                #   claude/hooks/git/* → .claude/hooks/org/git/ — git hooks activated via core.hooksPath (ADR-007)
WORKFLOWS_SRC = "workflow-callers"      # claude/workflow-callers/org-<feature>.yml — thin caller templates
WORKFLOWS_OUT = ".github/workflows"     # generated org-<feature>.yml live alongside the repo's own workflows
WORKFLOW_MARK = "# @org-managed: workflows"  # only files carrying this line are generated over / pruned
ORG_PREFIX = "org-"             # only entries with this prefix are org-owned; others are the repo's


@dataclass
class Change:
    path: str
    action: str  # created | updated | unchanged
    detail: str = ""


@dataclass
class Result:
    changes: list[Change] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(c.action != "unchanged" for c in self.changes)

    def to_json(self) -> str:
        return json.dumps(
            {
                "changed": self.changed,
                "changes": [c.__dict__ for c in self.changes],
            },
            indent=2,
        )


# --------------------------------------------------------------------------- block


def load_template(source: Path) -> str:
    text = (source / "CLAUDE.md").read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{source / 'CLAUDE.md'} must contain the BEGIN/END markers")
    if VERSION_PLACEHOLDER not in text:
        raise SystemExit(f"{source / 'CLAUDE.md'} must contain {VERSION_PLACEHOLDER}")
    return text.strip("\n") + "\n"


def render_block(template: str, version: str, org: str) -> str:
    return template.replace(VERSION_PLACEHOLDER, version).replace(ORG_PLACEHOLDER, org)


def detect_org(target: Path) -> str:
    """Owner of the target's `origin` remote, or a visible placeholder if unknown."""
    import subprocess
    try:
        url = subprocess.run(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "<org>"
    m = re.search(r"[:/]([^/:]+)/[^/]+?(?:\.git)?$", url)
    return m.group(1) if m else "<org>"


def split_block(text: str) -> tuple[str, str | None, str]:
    """Return (before, block, after). block is None when the markers are absent."""
    start = text.find(BEGIN)
    if start == -1:
        return text, None, ""
    end = text.find(END, start)
    if end == -1:
        raise SystemExit("found BEGIN marker without an END marker; refusing to guess")
    end += len(END)
    # Swallow the newline that terminates the END line so re-insertion is exact.
    if text[end : end + 1] == "\n":
        end += 1
    return text[:start], text[start:end], text[end:]


def _body_without_version(block: str) -> str:
    return VERSION_RE.sub("Version: <ignored>", block).strip("\n")


def block_version(block: str) -> str | None:
    m = VERSION_RE.search(block)
    return m.group(1) if m else None


def find_claude_md(target: Path) -> Path:
    for candidate in CLAUDE_MD_CANDIDATES:
        p = target / candidate
        if p.is_file():
            return p
    return target / "CLAUDE.md"


def sync_claude_md(template: str, version: str, org: str, target: Path, dry_run: bool) -> Change:
    path = find_claude_md(target)
    rel = str(path.relative_to(target))
    rendered = render_block(template, version, org)

    if not path.exists():
        new_text = rendered + "\n"
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_text, encoding="utf-8")
        return Change(rel, "created", f"block inserted, version {version}")

    existing = path.read_text(encoding="utf-8")
    before, block, after = split_block(existing)

    if block is None:
        new_text = rendered + "\n" + existing.lstrip("\n")
        if not dry_run:
            path.write_text(new_text, encoding="utf-8")
        return Change(rel, "updated", f"block inserted at top, version {version}")

    if _body_without_version(block) == _body_without_version(rendered):
        return Change(rel, "unchanged", f"block current (version {block_version(block)})")

    if before.strip():
        # Someone put content above the block. Keep it; the block is still ours.
        note = "; content above the block was preserved"
    else:
        note = ""
    new_text = before + rendered + after
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return Change(
        rel,
        "updated",
        f"block replaced ({block_version(block)} -> {version}){note}",
    )


# --------------------------------------------------------------------------- json merges


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path}: not valid JSON ({e}); fix it before syncing")
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object at top level")
    return data


def _union(org: list, repo: list) -> list:
    seen: list = []
    for item in list(org) + list(repo):
        if item not in seen:
            seen.append(item)
    return seen


def merge_settings(org: dict, repo: dict) -> dict:
    """Org wins on scalars, dicts recurse, lists under any key are unioned org-first."""
    out = dict(repo)
    for key, org_val in org.items():
        repo_val = repo.get(key)
        if isinstance(org_val, dict) and isinstance(repo_val, dict):
            out[key] = merge_settings(org_val, repo_val)
        elif isinstance(org_val, list) and isinstance(repo_val, list):
            out[key] = _union(org_val, repo_val)
        else:
            out[key] = org_val
    return out


CONDITION_KEY = "x-org-when"   # an org MCP server with this key is merged only when the target's org.toml has that dotted key truthy


def merge_mcp(org: dict, repo: dict, target_cfg: dict | None = None) -> dict:
    out = dict(repo)
    servers = dict(repo.get("mcpServers") or {})
    for name, server in (org.get("mcpServers") or {}).items():
        server = dict(server)
        cond = server.pop(CONDITION_KEY, None)
        if cond and not _cfg_get(target_cfg or {}, cond):
            servers.pop(name, None)   # opted out: remove the org copy if a previous sync wrote it
            continue
        servers[name] = server
    out["mcpServers"] = servers
    for key, val in org.items():
        if key != "mcpServers":
            out[key] = val
    return out


def _cfg_get(cfg: dict, dotted: str):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _target_cfg(target: Path) -> dict:
    """The target's org.toml (raw), for conditional org entries. Missing/invalid → {}."""
    import tomllib
    path = target / "org.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}


def _write_json(path: Path, data: dict, dry_run: bool) -> None:
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sync_json(
    source_file: Path, target_file: Path, merge, target_root: Path, dry_run: bool
) -> Change:
    rel = str(target_file.relative_to(target_root))
    if not source_file.exists():
        return Change(rel, "unchanged", "no baseline file")
    org = _load_json(source_file)
    existed = target_file.exists()
    repo = _load_json(target_file)
    merged = merge(org, repo)
    if existed and merged == repo:
        return Change(rel, "unchanged", "already contains the baseline")
    _write_json(target_file, merged, dry_run)
    return Change(rel, "updated" if existed else "created", "baseline merged")


# --------------------------------------------------------------------------- skills / agents


def _render_text(text: str, org: str) -> str:
    return text.replace(ORG_PLACEHOLDER, org)


def _sync_file(src: Path, dst: Path, org: str, target_root: Path, dry_run: bool) -> Change:
    rel = str(dst.relative_to(target_root))
    content = _render_text(src.read_text(encoding="utf-8"), org)
    if dst.exists() and dst.read_text(encoding="utf-8") == content:
        return Change(rel, "unchanged", "current")
    action = "updated" if dst.exists() else "created"
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
    return Change(rel, action, "org-owned; replaced wholesale")


def sync_skills(source: Path, target: Path, org: str, dry_run: bool) -> list[Change]:
    """claude/skills/<org-name>/** → .claude/skills/<org-name>/**"""
    changes: list[Change] = []
    src_root = source / "skills"
    if not src_root.is_dir():
        return changes
    for skill_dir in sorted(p for p in src_root.iterdir() if p.is_dir()):
        if not skill_dir.name.startswith(ORG_PREFIX):
            continue
        if not (skill_dir / "SKILL.md").is_file():
            raise SystemExit(f"{skill_dir}: an org skill needs a SKILL.md")
        dst_dir = target / SKILLS_DIR / skill_dir.name
        expected: set[Path] = set()
        for f in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
            dst = dst_dir / f.relative_to(skill_dir)
            expected.add(dst)
            changes.append(_sync_file(f, dst, org, target, dry_run))
        # Files that used to be part of the org skill but no longer are.
        if dst_dir.is_dir():
            for stale in sorted(p for p in dst_dir.rglob("*") if p.is_file() and p not in expected):
                rel = str(stale.relative_to(target))
                if not dry_run:
                    stale.unlink()
                changes.append(Change(rel, "updated", "removed: no longer in the org skill"))
    return changes


def sync_agents(source: Path, target: Path, org: str, dry_run: bool) -> list[Change]:
    """claude/agents/<org-name>.md → .claude/agents/<org-name>.md"""
    changes: list[Change] = []
    src_root = source / "agents"
    if not src_root.is_dir():
        return changes
    for f in sorted(src_root.glob("*.md")):
        if not f.name.startswith(ORG_PREFIX):
            continue
        changes.append(_sync_file(f, target / AGENTS_DIR / f.name, org, target, dry_run))
    return changes


def sync_hooks(source: Path, target: Path, org: str, dry_run: bool) -> list[Change]:
    """claude/hooks/*.py + claude/orgfile.py → .claude/hooks/org/  (the whole directory is org-owned)."""
    changes: list[Change] = []
    src_root = source / "hooks"
    if not src_root.is_dir():
        return changes
    dst_dir = target / HOOKS_DIR
    expected: set[Path] = set()
    files = sorted(src_root.glob("*.py")) + [source / "orgfile.py"]
    for f in files:
        if not f.is_file():
            continue
        dst = dst_dir / f.name
        expected.add(dst)
        changes.append(_sync_file(f, dst, org, target, dry_run))
    # git hooks (pre-commit, commit-msg, pre-push): no extension, must be executable
    git_src = src_root / "git"
    if git_src.is_dir():
        for f in sorted(p for p in git_src.iterdir() if p.is_file()):
            dst = dst_dir / "git" / f.name
            expected.add(dst)
            ch = _sync_file(f, dst, org, target, dry_run)
            if not dry_run and dst.exists():
                dst.chmod(dst.stat().st_mode | 0o111)
            changes.append(ch)
    if dst_dir.is_dir():
        for stale in sorted(p for p in list(dst_dir.glob("*.py")) + list((dst_dir / "git").glob("*")) if p.is_file() and p not in expected):
            rel = str(stale.relative_to(target))
            if not dry_run:
                stale.unlink()
            changes.append(Change(rel, "updated", "removed: no longer an org hook"))
    return changes


def _enabled_workflows(target_cfg: dict) -> set[str]:
    wf = target_cfg.get("workflows") if isinstance(target_cfg, dict) else None
    enabled = (wf or {}).get("enabled")
    return set(enabled) if isinstance(enabled, list) else set()


def sync_workflows(source: Path, target: Path, org: str, target_cfg: dict, dry_run: bool) -> list[Change]:
    """Generate .github/workflows/org-<feature>.yml callers for the features named in the
    target's org.toml [workflows].enabled, and prune deselected ones. GitHub runs workflows
    only from a flat .github/workflows/, so the org- filename prefix IS the managed namespace.
    Only files carrying WORKFLOW_MARK are ever written-over or removed — a repo's own workflows,
    and the reusable org-*.yml *definitions* in the engine itself, are never touched."""
    changes: list[Change] = []
    src_root = source / WORKFLOWS_SRC
    if not src_root.is_dir():
        return changes
    enabled = _enabled_workflows(target_cfg)
    out_dir = target / WORKFLOWS_OUT
    for f in sorted(src_root.glob("org-*.yml")):
        feature = f.stem[len(ORG_PREFIX):]
        dst = out_dir / f.name
        rel = str(dst.relative_to(target))
        existing = dst.read_text(encoding="utf-8") if dst.exists() else None
        if feature in enabled:
            content = _render_text(f.read_text(encoding="utf-8"), org)
            if existing is not None and WORKFLOW_MARK not in existing:
                changes.append(Change(rel, "unchanged", "skipped: a non-managed workflow already owns this name"))
                continue
            if existing == content:
                changes.append(Change(rel, "unchanged", "current"))
                continue
            if not dry_run:
                out_dir.mkdir(parents=True, exist_ok=True)
                dst.write_text(content, encoding="utf-8")
            changes.append(Change(rel, "updated" if existing is not None else "created", "org-managed workflow caller"))
        elif existing is not None and WORKFLOW_MARK in existing:
            if not dry_run:
                dst.unlink()
            changes.append(Change(rel, "updated", "removed: not in org.toml [workflows].enabled"))
    return changes


def sync_codeowners(target: Path, dry_run: bool) -> Change:
    """Managed block in .github/CODEOWNERS from the TARGET's org.toml [codeowners] (ADR-003 R9)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_codeowners", Path(__file__).resolve().parent / "hooks" / "gen_codeowners.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)  # type: ignore[union-attr]
    action = mod.apply(target, dry_run)
    return Change(".github/CODEOWNERS", action, "org block from org.toml [codeowners]")


# --------------------------------------------------------------------------- commands


def apply(source: Path, target: Path, version: str, dry_run: bool, org: str | None = None) -> Result:
    template = load_template(source)
    org = org or detect_org(target)
    result = Result()
    result.changes.append(sync_claude_md(template, version, org, target, dry_run))
    result.changes.append(
        sync_json(source / "settings.json", target / SETTINGS_PATH, merge_settings, target, dry_run)
    )
    tcfg = _target_cfg(target)
    result.changes.append(
        sync_json(source / "mcp.json", target / MCP_PATH, lambda o, r: merge_mcp(o, r, tcfg), target, dry_run)
    )
    result.changes.extend(sync_skills(source, target, org, dry_run))
    result.changes.extend(sync_agents(source, target, org, dry_run))
    result.changes.extend(sync_hooks(source, target, org, dry_run))
    result.changes.extend(sync_workflows(source, target, org, tcfg, dry_run))
    result.changes.append(sync_codeowners(target, dry_run))
    return result


def check(source: Path, target: Path, org: str | None = None) -> Result:
    return apply(source, target, version="<check>", dry_run=True, org=org)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, need_target=True, need_version=True):
        p.add_argument("--source", type=Path, default=Path(__file__).resolve().parent,
                       help="directory holding CLAUDE.md, settings.json, mcp.json (default: this script's dir)")
        if need_target:
            p.add_argument("--target", type=Path, required=True, help="root of the consuming repository checkout")
        if need_version:
            p.add_argument("--version", required=True, help="version stamp, e.g. a tag or short SHA")
        p.add_argument("--org", help="GitHub organization rendered into {{ORG_GITHUB}} (default: owner of the target's origin remote)")
        p.add_argument("--json", action="store_true", help="print a JSON summary")

    p_apply = sub.add_parser("apply", help="insert/refresh the baseline in --target")
    common(p_apply)
    p_apply.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")

    p_check = sub.add_parser("check", help="exit 1 if --target is out of sync with --source")
    common(p_check, need_version=False)

    p_render = sub.add_parser("render", help="print the rendered block")
    common(p_render, need_target=False)

    p_sum = sub.add_parser("summarize", help="print a --json result file as a table")
    p_sum.add_argument("result", type=Path)

    args = ap.parse_args(argv)

    if args.cmd == "summarize":
        data = json.loads(args.result.read_text(encoding="utf-8"))
        for c in data["changes"]:
            print(f"{c['action']:9} {c['path']}  {c['detail']}")
        return 0

    if args.cmd == "render":
        sys.stdout.write(render_block(load_template(args.source), args.version, args.org or "<org>"))
        return 0

    if args.cmd == "check":
        result = check(args.source, args.target, args.org)
        _report(result, args.json)
        return 1 if result.changed else 0

    result = apply(args.source, args.target, args.version, args.dry_run, args.org)
    _report(result, args.json)
    return 0


def _report(result: Result, as_json: bool) -> None:
    if as_json:
        print(result.to_json())
        return
    for c in result.changes:
        print(f"{c.action:9} {c.path}  {c.detail}")
    print("changed" if result.changed else "in sync")


if __name__ == "__main__":
    sys.exit(main())

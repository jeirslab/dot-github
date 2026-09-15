# Organizational agent configuration

This directory is the **single source of truth for how coding agents behave across
the organization's repositories** (ADR-001). It is the harness: rule files, settings, hooks, and
skills. It is versioned and reviewed like any other code in this repository.

```
claude/
├── CLAUDE.md        # org baseline rule file — a managed block inserted at the top of every repo's CLAUDE.md
├── settings.json    # org baseline Claude Code settings (permissions, hooks) → .claude/settings.json
├── mcp.json         # org baseline MCP servers → merged into the repo's .mcp.json by key
├── repos.txt        # allowlist of repositories that receive the baseline (opt-in)
├── sync.py          # the sync logic (stdlib Python; runs locally and in the workflow)
├── tests/           # unit tests for sync.py — run on every PR touching claude/
├── hooks/           # guardrail scripts (secrets, commits, provenance, PR hygiene, deps, CODEOWNERS) → .claude/hooks/org/
│   └── git/         # client git hooks (pre-commit, commit-msg, pre-push; ADR-007) → .claude/hooks/org/git/, activated by the dev shell
├── orgfile.py       # org.toml loader (ADR-003); ships with the hooks
├── jira/            # org Jira CLI (ADR-008): sync / show / find / apply / standup / sweep — invoked via the flake, not distributed
├── rulesets/        # branch rulesets as JSON, applied by apply_rulesets.py / org-rulesets.yml
├── skills/          # org skills (org-*/SKILL.md) → .claude/skills/  — procedures, loaded on demand
└── agents/          # org subagents (org-*.md)    → .claude/agents/  — bounded jobs with scoped tools (reviewer, verifier, conformance, release notes, branch sweep)
```

## Lean by design (ADR-006)

The block is **hard rules only** (~40 lines). Everything procedural lives in skills that
load when the task matches, and bounded jobs run in subagents with their own tool scope:

| Org skill | Loads when… |
|---|---|
| `org-jira` | a task starts, pauses or finishes and a ticket must be found/created/moved |
| `org-adr` | a decision needs recording or superseding |
| `org-dev-env` | entering a repo, missing tools, secrets via Infisical, `org-check` |
| `org-branching` | branching, PRs into `main`, releases, the kill switch |

| Org subagent | Job |
|---|---|
| `org-reviewer` | read-only first-pass review against the org rubric |
| `org-conformance` | report what a repo is missing and the smallest fix |
| `org-release-notes` | draft the release body for a merge into `main` |

Org entries carry the `org-` prefix and are replaced wholesale by the sync; a repository's
own skills and agents (any other name) are never touched. To route a subagent to a cheaper
model (ADR-002 R7), set `model:` in its frontmatter in this repo; the sync ships it.

## Git hooks: the branch model on the client (ADR-007)

`hooks/git/` holds three plain-bash git hooks. The org dev shell (`flake.nix`, ADR-005) sets
`core.hooksPath` to `.claude/hooks/org/git` on first entry; outside the shell,
`nix run github:<org>/.github#install-hooks` does the same. `pre-commit` scans staged files
for secrets, `commit-msg` requires the ticket prefix, `pre-push` refuses pushes to deploy
branches and runs `gate.fast` before a push to the integration branch. `ORG_HOOKS_SKIP=1`
bypasses them for one command. They are the convenience layer; the server-side
`org-branch-guard.yml` and `hooks/verify_pr.py` (run by the release and production-deploy
workflows) are the layer that cannot be bypassed.

## Verification is a subagent, not a sentence (INFRA-282)

`agents/org-verifier.md` reruns the repository gate, exercises the change from the terminal
and, where the repo opts in (`org.toml [verify] ui = true`), drives the screen through the
Playwright MCP (`mcp.json` carries the server with `"x-org-when": "verify.ui"`; the sync
merges it only into repos whose `org.toml` says so). Its report is the PR's "Verification
performed" section — which the `pr` guardrail now rejects when it is template-only — and
the evidence the `org-jira` skill requires before Done. The org dev shell adds browsers
with `mkDevShell { ui = true; }`.

## Jira without the token tax (ADR-008)

`jira/` is the snapshot-first Jira CLI (ported from the docs repository's `docs jira`,
INFRA-219): `jira sync` writes a trimmed snapshot of every visible project to
`$XDG_CACHE_HOME/org-jira/<site>/summary.json`, `jira show` / `jira find` read it with no
API calls, `jira apply plan.yaml` validates a whole change-set locally and applies it in one
run, `jira sweep` correlates branches with tickets across repositories. Standard library;
`nix run github:<org>/.github#jira` adds PyYAML and certifi. Config is `org.toml [jira]`;
credentials are `JIRA_EMAIL` / `JIRA_API_TOKEN` only. Tests: `tests/test_jira.py`.

## Hooks: the same checks locally and in CI (ADR-003)

`hooks/*.py` are standalone, stdlib-only CLIs. The guardrails workflow runs them on every PR;
the sync copies them to `.claude/hooks/org/` so a developer or agent runs the identical check
before pushing. To wire one into Claude Code automatically, add to the repository's
`.claude/settings.json` (not enabled by default — a broken hook blocks every command):

```json
{ "hooks": { "PreToolUse": [ { "matcher": "Bash",
  "hooks": [ { "type": "command", "command": "python3 .claude/hooks/org/check_secrets.py --root . --range HEAD~1..HEAD || true" } ] } ] } }
```

## Two layers

Every repository ends up with **org baseline + repo overlay**:

| Layer | Lives in | Contains |
|-------|----------|----------|
| Org baseline | this directory | Rules true in every repository regardless of stack: Jira workflow, commit prefix, staging discipline, secrets rules, ADR practice, security posture. |
| Repo overlay | the consuming repository's own `CLAUDE.md` / `.claude/` | Anything that names a repo-specific tool, path, or service (the `fleet` CLI, Terranix, a package layout). |

The baseline is never edited inside a consuming repository. Change it here, in a PR, and
it flows out.

## How the baseline lands in a repository

**`CLAUDE.md` — a managed block, like an `authorized_keys` section.** The org baseline is
wrapped in marker comments and inserted at the *top* of the consuming repository's
`CLAUDE.md` (or `.claude/CLAUDE.md`, whichever the repo uses; created if neither exists):

```
<!-- BEGIN ORG BASELINE -->
... org rules, verbatim from claude/CLAUDE.md, version stamped ...
<!-- END ORG BASELINE -->

# <repo name>
... the repository's own rules, untouched ...
```

Rules for the block:
- Everything between the markers is owned upstream. The sync replaces the block wholesale;
  edits made inside it in a consuming repo are lost on the next sync (and flagged as drift).
- Everything outside the markers is the repository's and is never touched.
- `{{ORG_GITHUB}}` is replaced with the GitHub organization name (`--org`, default: the
  owner of the target's `origin` remote), so the template never carries the company name
  and an org rename is a one-argument change.
- `{{ORG_BASELINE_VERSION}}` is replaced with the tag or commit of this repo that the
  block came from, so drift and staleness are detectable by reading one line.
- If the markers are missing, the block is inserted at line 1. If they are present, the
  content between them is replaced in place.

**`settings.json` → `.claude/settings.json`.** Copied as the org baseline. A repo that needs
more keeps its additions in `.claude/settings.local.json` or in keys the baseline does not
set; the sync only rewrites keys it owns.

**`mcp.json` → `.mcp.json`.** Merged by server name: org-defined servers are written
verbatim, repo-defined servers with other names are preserved. Only servers that work in
every repository belong here (today: `nixos`, which needs nothing but `nix`). Fleet-specific
servers such as the fleet CLI server, `grafana`, `tempo` and `opentelemetry` point at operator
machines and stay in `deployments/.mcp.json`. Jira/Confluence arrive through the claude.ai
Atlassian connector, not through `.mcp.json`.

**Sync mechanics (ADR-004, INFRA-272).** `claude/sync.py` does all of the above and runs
anywhere:

```bash
# preview what a repo would get
python3 claude/sync.py apply --source claude --target ../some-repo --version "$(git describe --tags --always)" --dry-run
# apply it
python3 claude/sync.py apply --source claude --target ../some-repo --version v1.0.8
# is a repo in sync? (exit 1 if not)
python3 claude/sync.py check --source claude --target ../some-repo
```

`sync-agent-config.yml` runs the same script for every repository in `repos.txt` on push
to `main` touching `claude/`, weekly (drift), and on dispatch, and opens one PR per repo
on the branch `chore/org-agent-config` (base `staging` if it exists, else the default
branch). Nothing changed → no PR. It needs the `ORG_SYNC_TOKEN` secret (fine-grained PAT
or GitHub App token, `contents` + `pull-requests` write on the target repos). Try it first
with `workflow_dispatch` and `dry_run: true`.

To add a repository: add it to `repos.txt` in a PR.

Nothing here requires GitHub to be interpreted. The files are plain Claude Code
configuration, shell/Python scripts, and Markdown, so the same copy works on any forge.

## Changing it

- Add a rule every time an agent does something it should not do again. Keep rules short
  and testable; if a rule can be a hook, make it a hook (see `hooks/`).
- Repo-specific rules do not go here. If you are naming a tool only one repository has,
  it belongs in that repository's overlay.
- Changes ride the repository's tag stream. Consumers may pin a tag.

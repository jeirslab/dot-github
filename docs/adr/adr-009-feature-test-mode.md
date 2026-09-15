# ADR-009: Feature-test mode — org workflows are exercised on this repository before they go org-wide

- **Date:** 2026-09-10
- **Status:** Proposed

---

## Context

Everything in `.github/workflows/` acts on other repositories: the reusable workflows are
called from every consumer at `@v2`, and the org-wide ones (agent-config sync, scorecard,
rulesets, ADR consolidation, branch sweep, bootstrap) enumerate `claude/repos.txt` and
open PRs, apply settings or write reports there. A workflow change is therefore live for
the whole organization the moment it reaches `main` with the kill switch on, and there
was no way to run it anywhere first. The unit tests cover the scripts the workflows call
(ADR-002 R12), not the YAML, the triggers, the guards, the permissions or the target
resolution, which is where workflow bugs actually live.

The operator asked for a feature-test branch and a variable that confines the org-wide
workflows to it, plus a test suite for the workflows themselves.

## Decision

### One branch, one variable, one resolver

- `feature-test` is a long-lived branch of this repository. Workflow changes are pushed
  there first (direct pushes allowed, like `staging`), then PR'd into `main`.
- The repository variable `ORG_FEAT_TEST` switches feature-test mode on. Optional
  `ORG_FEAT_TEST_BRANCH` renames the branch (default `feature-test`).
- `claude/targets.py` is the only code that resolves which repositories an org-wide
  workflow acts on. In feature-test mode it returns exactly one target, this repository
  at the feature-test branch, whatever the trigger, the dispatch inputs or the branch the
  run started from. Every org-wide workflow calls it and checks targets out at the
  returned ref; PRs it opens are based on that ref.

### The guard

Every automatically triggered job carries one expression (the static tests enforce it):

```
github.event_name == 'workflow_dispatch'
|| (vars.ORG_FEAT_TEST == 'true' && (github.ref_name == <branch> || github.base_ref == <branch>))
|| (vars.ORG_FEAT_TEST != 'true' && vars.ORG_WORKFLOWS_ENABLED == 'true')
```

So: manual dispatch always runs (targets still confined); with feature-test on, only
feature-test pushes and PRs into it run, and `main` is inert; with it off, the kill switch
(ADR-003) decides. Tagging (`_version-workflows-repo.yml`) stays main-only.

### The test suite

1. **Static policy tests** (`claude/tests/test_workflows.py`, on every PR, offline): every
   workflow parses and is named; public ones carry `# @doc:`; every automatic job is
   guarded; every workflow that runs on `main` also runs on `feature-test`; nothing reads
   `repos.txt` except through `targets.py`; local `uses:` paths exist; starter templates
   pin `@v2` and point at reusable workflows that exist; every reusable workflow is either
   exercised by the test workflow or listed as not-exercised with a reason; `targets.py`
   confines correctly.
2. **The exercise workflow** (`_workflow-tests.yml`, on push to `feature-test`): runs the
   policy tests and `actionlint`, then *calls* the reusable workflows against this
   repository: `org-gate` (this repo's `gate.fast`), `org-guardrails`, `org-deploy` with a
   declared no-op `[deploy.test]` target (which also answers whether GitHub Environments
   exist on the plan), plus the sync check, the scorecard checks, the branch sweep and
   `verify_pr` against the pushed commit. A summary job fails if any leg failed.
3. **Everything else already existing**: the script unit tests, the sync dry-run, and
   the org-wide workflows themselves, which in feature-test mode run for real against
   this repository (a sync PR into `feature-test`, a scorecard row for `.github`, the
   sweep issue) and nowhere else.

The loop: branch from `main` → push to `feature-test` (or PR into it) → the exercise
workflow and the confined org-wide workflows run → fix until green → PR `feature-test →
main` → tag `v2` → consumers pick it up. Documented in `docs/workflow-testing.md`.

## Consequences

### Positive

- A workflow change can be run end to end in Actions without touching any other
  repository, with the same YAML that ships, not a mock of it.
- Static tests catch the class of bug that produced most of this repo's earlier fixes
  (a job without the guard, a template pointing at a renamed workflow, a missing doc
  line) before a runner is involved.
- The kill switch and feature-test mode compose: the org can keep `ORG_WORKFLOWS_ENABLED`
  off and still exercise everything on `feature-test`.

### Negative

- Two variables now govern whether anything runs; the README table spells out the four
  combinations. Forgetting to turn `ORG_FEAT_TEST` off leaves `main` inert, which is
  loud (nothing happens) rather than dangerous.
- Reusable workflows called from consumers at `@v2` are *not* affected by the mode; a
  consumer's own variables decide. That is the point, but it means feature-test proves
  the workflows on this repository's shape only. Repos with a different gate or deploy
  shape still need their first real run.
- `org-branch-guard`, `org-oci` and `sync-release` are not exercised by the test
  workflow (a revert PR on every push, a registry login, a real release); their scripts
  are unit-tested and `verify_pr` runs against each pushed commit.
- Nothing here has run in Actions yet either. The first push to `feature-test` with the
  variable on is the test of the test.

## Alternatives Considered

### `act` (run Actions locally in Docker)

**Rejected because:** it does not implement reusable workflows, `vars`, environments or
the permissions model faithfully, which is exactly what needs testing.

### A separate sandbox organization

**Rejected because:** a second org doubles tokens, secrets and repos to keep in sync, and
still would not test the consumers' shape any better than this repository does.

### Per-workflow `dry_run` inputs only

**Rejected because:** dry-run flags test the script, not the trigger, the guard, the
checkout ref or the PR base, and they rot.

## Implementation Notes

- Shipped. `claude/targets.py`, `_workflow-tests.yml`,
  `claude/tests/test_workflows.py`, the guard and `targets.py` wiring in every org-wide
  workflow, `[deploy.test]` in this repo's `org.toml`, `docs/workflow-testing.md`.
- To start: create the `feature-test` branch (done from the same commit), set
  `ORG_FEAT_TEST=true`, push a trivial change to `feature-test`, watch
  `_workflow-tests.yml` and the confined org-wide runs.

# `.github` — organization baseline repository

The organization-level home for two things:

1. **Reusable GitHub Actions building blocks** — workflows invoked with `workflow_call`,
   composite actions, and (when we have ones worth offering) starter templates.
2. **Organizational agent configuration** — the shared harness every repository in the organization's
   coding agents run under: rule file, settings, hooks, skills. See [`claude/`](claude/)
   and [ADR-001](docs/adr/adr-001-org-agent-configuration-home.md).

Decisions that shape either live in [`docs/adr/`](docs/adr/).

## Company-agnostic by design

The organization will be renamed at some point, so nothing in the *mechanism* carries the
company name: variables (`ORG_WORKFLOWS_ENABLED`, `ORG_SHELL_QUIET`), block markers
(`<!-- BEGIN/END ORG BASELINE -->`), placeholders (`{{ORG_BASELINE_VERSION}}`,
`{{ORG_GITHUB}}`), the bot identity (`org-config-sync`), flake names, and every URL built
in a workflow (`${{ github.repository }}`, `${{ github.repository_owner }}`). The org name
exists only as **data**: entries in `claude/repos.txt`, Jira project keys inside the
baseline `CLAUDE.md`, and the `<org>` you write in your own `uses:`/`github:` references.
CI fails if the current organization name (from `github.repository_owner`) appears in a
mechanism file, so the check follows a rename automatically.

## Adopting this template (another organization)

This repository is a GitHub **template**: `main` *is* the template. Another organization
starts its own `.github` from it with **Use this template** (which copies `main` — no
history, no variables, no secrets). Because the mechanism is company-agnostic, adoption is
data entry, not code surgery:

1. **Use this template → create your `<org>/.github`.** A generated repo carries none of
   your predecessor's variables or secrets, so **every workflow is inert** on arrival
   (`ORG_WORKFLOWS_ENABLED` unset ⇒ the kill switch is off). Nothing runs until you opt in.
2. **Remove the template's own history.** `docs/adr/` holds *this* project's design
   records — useful to the template's maintainers, not to you. Either dispatch the
   **Template Cleanup** workflow from your new repo's Actions tab, or run
   [`.github/scripts/template-init.sh`](.github/scripts/template-init.sh) locally; both
   strip the numbered ADRs and leave an empty `docs/adr/` skeleton, and both are idempotent
   (a `.github/.template-initialized` marker makes reruns no-ops). *(The tailored,
   sanctioned path — `claude/construct.py` — does this for you; see below. The button gives
   a raw copy, so this cleanup is the manual equivalent.)*
3. **Set your variables and secrets** (nothing before this has any effect):
   - variables: `ORG_WORKFLOWS_ENABLED` (leave `false` until ready), `ORG_FEAT_TEST` /
     `ORG_FEAT_TEST_BRANCH`, `ORG_RELEASE_NOTES_MODEL`, `ANTHROPIC_BASE_URL` (if proxied),
     `ORG_RUNNER` (if using a fleet runner), `ORG_TF_STATE_BUCKET` (if using the platform stack).
   - secrets: `ANTHROPIC_API_KEY`, `ORG_SYNC_TOKEN` (fine-grained PAT or App token with
     Contents + Pull requests + Workflows + Issues write on your fleet), `ORG_ADMIN_TOKEN`
     (only for rulesets / the platform stack).
4. **Enter your data** in `claude/repos.txt`, `claude/repos.toml`, `nix/github/manifest.nix`
   and `org.toml` (owner, fleet allowlist, teams, Jira `[jira]` table). The `<org>` in any
   `uses:`/`github:` reference is yours.
5. **Rename the repo to `.github`** under your org so GitHub applies it as the org profile
   /default-config repository.
6. **Turn it on** only after a green run on your feature-test branch: flip
   `ORG_WORKFLOWS_ENABLED` to `true` (see Workflows below). You may then delete
   `template-cleanup.yml` and `template-init.sh` — they are one-shot.

## Layout

```
.github/
├── workflows/           # reusable + repo-internal workflows (see WORKFLOWS.md)
├── actions/slack/       # Slack notification composite
└── scripts/             # generate-workflow-docs.py → WORKFLOWS.md
claude/                  # org agent config, sync, org-check, hooks/, rulesets/, orgfile.py (ADR-001/003/004/005/006)
org.toml                 # this repo's own pipeline declaration (ADR-003)
workflow-templates/      # starters for consuming repos: org-pipeline.yml, org-release.yml
flake.nix                # org baseline dev-environment flake: lib.mkDevShell, org-check, sync-agent-config (ADR-005)
docs/adr/                # organization-level ADRs
WORKFLOWS.md             # generated table of public workflows — do not hand-edit
```

## Workflows

**Kill switch.** Every automatically triggered job (push, pull_request, schedule) in this
repository is gated on the variable `ORG_WORKFLOWS_ENABLED == 'true'` (Settings →
Secrets and variables → Actions → Variables, repo or org level). Until it is set, merging
to `main` tags nothing, releases nothing, syncs nothing and runs no CI; only
`workflow_dispatch` runs. Reusable workflows called from another repository read *that*
repository's variables, so a caller must set it too. Flip it once the flake is verified
(INFRA-273) and `ORG_SYNC_TOKEN` exists (INFRA-272).

**Feature-test mode** ([ADR-009](docs/adr/adr-009-feature-test-mode.md)). With the variable
`ORG_FEAT_TEST == 'true'`, only the `feature-test` branch runs, `main` is inert, and every
org-wide workflow is confined to this repository at `feature-test` (resolved by
`claude/targets.py`). `_workflow-tests.yml` exercises the reusable workflows against this
repo on every push there; `claude/tests/test_workflows.py` checks the YAML policy offline.
The loop is in [`docs/workflow-testing.md`](docs/workflow-testing.md).

[`WORKFLOWS.md`](WORKFLOWS.md) is generated from the `# @doc:` comments in each public
workflow (files not starting with `_`) by `_generate_workflow-docs.yml` on push to `main`.

Status at a glance:

| Workflow | Status |
|----------|--------|
| `promote-pr.yml` | **Deprecated** by [ADR-002](docs/adr/adr-002-agent-aware-promotion-pipeline.md). Keeps working for `@v1` callers, warns on every run, removed once v2 ships and callers migrate. |
| `create-branches.yml` | Active (INFRA-270): seeds the `staging` integration branch from the default branch; no longer seeds `dev/qa/prod`. |
| `sync-release.yml` | Active (INFRA-270): releases when a PR merges into `main`/`master`; every other event is a no-op. **Semantics changed from v1** (no more `prod → main` sync) — callers that relied on the sync must migrate; consider cutting `v2` before merging this. |
| `version-and-package-source.yml` | Active. |
| `sync-agent-config.yml` | Active (INFRA-272, ADR-004): distributes `claude/` to the repos in `claude/repos.txt` as one PR per repo. Needs `ORG_SYNC_TOKEN`. |
| `org-gate.yml`, `org-guardrails.yml`, `org-deploy.yml`, `org-oci.yml` | **v2 reusable pipeline** (INFRA-276, ADR-003): gate tiers, deterministic guardrails, environment-protected deploy with health-gated rollback proposal, OCI artifact. Called from consuming repos via the `workflow-templates/org-*.yml` starters. Unverified in Actions until the first real PR. |
| `org-rulesets.yml`, `org-scorecard.yml`, `org-bootstrap.yml`, `org-adr-consolidate.yml` | Org-level (INFRA-276): branch protection as code (**needs GitHub Team/Enterprise or public repos — GitHub Free returns 403 for private repos**), weekly conformance scorecard, one-shot repo bootstrap, ADR consolidation for the docs repo. |
| `org-branch-guard.yml` | **Free-plan branch enforcement** (INFRA-277, [ADR-007](docs/adr/adr-007-free-plan-branch-enforcement.md)): detects a direct or forced push to a deploy branch, opens an issue and a revert PR. Paired with the `verify` jobs in `sync-release.yml`/`org-deploy.yml` (approval + check-run verification via `claude/hooks/verify_pr.py`) and the git hooks the dev shell activates. Unverified in Actions. |
| `org-branch-sweep.yml` | **Branch ↔ ticket sweep** (INFRA-278, [ADR-008](docs/adr/adr-008-org-jira-cli-and-branch-sweep.md)): weekly / on-demand report of which branches carry which Jira tickets, per owner, with merge state and flags; refreshes the pinned `branch-sweep` issue. Same script locally: `jira sweep`. Needs `JIRA_EMAIL`/`JIRA_API_TOKEN` and a cross-repo read token. Unverified in Actions. |
| `rollback.yml` | Reworked (INFRA-276): tag-based rollback PR for the v2 model. |
| `_compute-version.yml`, `_version-workflows-repo.yml`, `_generate_workflow-docs.yml`, `_claude-config-ci.yml` | Internal to this repository. |
| `_workflow-tests.yml` | Exercises the reusable workflows against this repo on push to `feature-test` (INFRA-283, ADR-009). |
| `org-runner-smoke.yml` | Weekly canary for the runner policy (INFRA-290): picks the runner as the trusted jobs do, proves the tool set and that the org stack renders there. |
| `org-platform-github.yml` | **GitHub org config plan/apply** (INFRA-289, ADR-010/011): renders `nix/github/`, plans on PRs, applies on `main`; runs on the variable-selected runner. Unverified in Actions. |

The `loveable-promotion` starter template was withdrawn from the Actions gallery with
ADR-002. Repositories that already copied it continue to work until v1 is removed.

### Calling a reusable workflow

```yaml
# .github/workflows/release.yml in a consuming repository
on:
  pull_request:
    types: [closed]
    branches: [main, master]
jobs:
  release:
    uses: <org>/.github/.github/workflows/sync-release.yml@v1
    with:
      version_bump: patch
    secrets: inherit
```

The called workflow checks that the caller's event is a PR merged into a deploy branch
and does nothing otherwise, so wiring it to a broader trigger is safe but pointless.

Pin a tag (`@v1`) or a SHA. Breaking changes ship as a new major tag; the previous tag
stays callable.

### Slack composite

`.github/actions/slack/slack-notify-simple.yml` defines a composite step that posts a
message with automatic run context and is a no-op when `SLACK_WEBHOOK_URL` is unset.
Note: as committed it is a bare YAML file, not an `action.yml` inside a directory, so it is
not yet referenceable with `uses:` from another repository. Wiring it properly is part of
the v2 pipeline work (ADR-002, R11).

## The v2 pipeline (ADR-003)

A consuming repository needs three things: an `org.toml` (`python3 claude/orgfile.py --example`),
the two starter workflows from `workflow-templates/` (the bootstrap workflow writes them),
and its own gate command. Then:

- **PR into `staging` or `main`** → `gate / gate` (fast or full tier) + `guardrails / secrets|commits|provenance|pr|supply-chain`.
- **Push to `staging`** → gate → OCI artifact (`:<sha>`) → `deploy.staging` in the `staging` environment → health check.
- **PR merged into `main`** → tag + Release → back-merge PR into `staging` → OCI `:<tag>` + `:latest` → `deploy.production` behind the `production` environment (required reviewers = approver set) → health check → on failure a rollback PR to the previous tag.
- **Weekly** → conformance scorecard PR here; rulesets drift-corrected (plan permitting).
- **Direct push to `main`** (ADR-007) → refused by the `pre-push` hook in the dev shell; if it lands anyway, `org-branch-guard.yml` opens an issue + revert PR, and the release/production-deploy `verify` jobs refuse a commit that did not merge through an approved, gated PR.

Same hook scripts run locally from `.claude/hooks/org/` (the sync ships them):

```bash
python3 .claude/hooks/org/check_secrets.py --root . --range origin/staging..HEAD
python3 .claude/hooks/org/check_commits.py --root . --range origin/staging..HEAD
python3 .claude/hooks/org/check_pr.py --root . --range origin/staging..HEAD --body-file pr.md
```

Secrets/variables the org-level workflows use: `ORG_SYNC_TOKEN` (contents + PRs on targets),
`ORG_ADMIN_TOKEN` (repository administration, for rulesets once the plan allows them), optional `ORG_READ_TOKEN` (only if
this repo is private), optional `ANTHROPIC_API_KEY` + variable `ORG_RELEASE_NOTES_MODEL`,
optional `ORG_METRICS_URL`/`ORG_METRICS_TOKEN` (Loki push), `JIRA_EMAIL`/`JIRA_API_TOKEN` + optional variable
`JIRA_BASE_URL` for the Jira CLI and the branch sweep (ADR-008).

## Agent configuration

[`claude/`](claude/) holds the org baseline `CLAUDE.md` (a lean, hard-rules-only managed
BEGIN/END block), `settings.json`, `mcp.json`, the org **skills** (procedures, loaded on
demand) and **subagents** (bounded jobs with scoped tools) per ADR-006, `hooks/`, and
`sync.py`, which puts all of it into consuming repositories. Every repository ends up with **org baseline + repo
overlay**; the baseline is changed here, never in a consuming repository.
`sync-agent-config.yml` opens one PR per repository in `claude/repos.txt` whenever the
baseline changes (ADR-001, ADR-004). Details in [`claude/README.md`](claude/README.md).

## Nix

`flake.nix` is the org baseline developer environment ([ADR-005](docs/adr/adr-005-dev-environment-baseline-flake.md)).
Every repository in the organization has a `flake.nix`; the org one gives them the shared floor:

```nix
# in a consuming repository's flake.nix
inputs.org-baseline.url = "github:<org>/.github";
inputs.org-baseline.inputs.nixpkgs.follows = "nixpkgs";
# ...
devShells.default = org-baseline.lib.mkDevShell {
  inherit pkgs;
  extraPackages = [ pkgs.nodejs_22 ];   # repo-specific tools
};
```

That shell carries the Infisical CLI (developer secrets: `infisical login`, then
`infisical run -- <cmd>`), sops, age, jq, just, python3, gitleaks and the org `jira` CLI. From anywhere:

```bash
nix run github:<org>/.github#org-check            # what is this repo missing?
nix run github:<org>/.github#sync-agent-config -- apply --target . --version manual
nix run github:<org>/.github#jira -- sync             # token-cheap Jira: sync / show / find / apply / sweep (ADR-008)
nix develop github:<org>/.github                   # the org shell itself
nix flake check                                      # this repo's gate: sync tests + org-check
```

## GitHub organization configuration as code (ADR-010)

`nix/github/` is a Terranix module set for the `platform.github` OpenTofu stack:
repositories (from `claude/repos.txt`, adopted by `import`, never destroyed), default
branches, the standard labels, Actions variables and secrets, teams, webhooks, and
rulesets behind a flag. Values are in `nix/github/manifest.nix`; the token and secret
values arrive only as `TF_VAR_github_token` / `TF_VAR_org_secrets`.

```bash
nix build .#platform-github && jq '.resource | map_values(length)' result
TF_VAR_github_token=… nix run .#platform-github -- plan        # local state under .tofu/ unless ORG_TF_STATE_BUCKET is set
```

Importable into the deployments Terranix pipeline as a leaf stack; see
[`nix/github/README.md`](nix/github/README.md).

`org-platform-github.yml` runs it from here ([ADR-011](docs/adr/adr-011-runner-policy.md)):
plan on PRs touching the manifest (posted as a comment), apply on push to `main` behind the
kill switch, remote state required. Needs `ORG_ADMIN_TOKEN`, `ORG_TF_AWS_ACCESS_KEY_ID` /
`ORG_TF_AWS_SECRET_ACCESS_KEY` and the variable `ORG_TF_STATE_BUCKET`.

## Runners (ADR-011)

Trusted org-level jobs run on the label `_runner.yml` picks: `vars.ORG_RUNNER` unset →
hosted; `auto` → the fleet runner when one is online, else hosted; a label → that label. PR
gates always stay hosted. The runner itself is `nixosModules.org-runner` from this flake
(ephemeral, replace-on-register, org tool set, no Docker). Bring-up is one PAT plus one
command, `claude/runner_bootstrap.py`, which stores the secret, writes the fleet host,
deploys it, waits for it online, sets `ORG_RUNNER=auto` and runs the smoke canary; see
[`nix/runner/README.md`](nix/runner/README.md).

## Versioning

- Pushes to `main` are tagged automatically by `_version-workflows-repo.yml`
  (`v1.0.N`, plus the moving `v1` and `latest` tags).
- Don't force-move tags. Publish `v2` for breaking changes and keep `v1` callable.

## Security and permissions

- Workflows request the minimum permissions they need.
- Pin third-party actions to a major tag or SHA.
- Branch protection: rulesets as code where the plan allows; otherwise the free
  substitute in [ADR-007](docs/adr/adr-007-free-plan-branch-enforcement.md) (git hooks
  from the dev shell, detect-and-revert branch guard, approval verification before every
  release and production deploy).
- No secrets in code. Callers provide `SLACK_WEBHOOK_URL` via repository or organization
  secrets and use `secrets: inherit`.
- Nothing under `claude/` may contain credentials; it is copied into every repository.

## Contributing

- Keep reusable workflows passive (`on: workflow_call`) and parameterized.
- Every workflow carries at least one `# @doc:` line; regenerate `WORKFLOWS.md` locally
  with `python3 .github/scripts/generate-workflow-docs.py` if you want to preview it.
- Significant changes to workflows or to `claude/` get an ADR in `docs/adr/`.
- Commits carry a Jira key prefix (`INFRA-NNN:`), as everywhere in the organization.
- Test a workflow change from a sandbox repository by calling `@<branch>` before tagging.

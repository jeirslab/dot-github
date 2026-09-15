# ADR-003: The v2 org pipeline — `org.toml` declaration, reusable gate/guardrails/deploy, rulesets as code

- **Date:** 2026-09-09
- **Status:** Proposed
- **Implements:** ADR-002 (requirements R1–R15)

---

## Context

ADR-002 deprecated the v1 branch-promotion pipeline and fixed fifteen requirements for its
replacement without designing it. Since then the operator decided the branch model
(`staging` integration branch with direct pushes; `main`/`master` PR-only deploy branch;
a merge into it is a release), ADR-004/005/006 put the org agent configuration, the dev
environment flake and the lean baseline in place, and the 2026-09-08 tech call confirmed
staging and production auto-deploy, artifact publishing, and secrets/dependency checks
as wanted. This ADR is the design, and it ships with its implementation.

Two constraints shaped every choice: **forge portability** (`deployments` ADR-068, R12:
logic in scripts, Actions as a thin shell) and **company-agnosticism** (the org is being
renamed; nothing in the mechanism carries its name).

## Decision

### 1. One declaration per repository: `org.toml`

A repository declares *what* to run and *where* to deploy; the org workflows decide *when*
and *how*. `claude/orgfile.py` (stdlib, `tomllib`) loads it with defaults, validates it,
and answers dotted queries for shell. Sections: `branches` (integration, deploy list),
`gate` (`fast`, `full`, `uses_nix`, timeout), `deploy.<env>` (command, runner labels,
GitHub Environment, health URL/command, timeout), `artifacts.oci`, `adr.required_paths`,
`secrets` (SOPS paths, allow paths), `generated.files`, `pr` limits, `codeowners`,
`labels`. `orgfile.py --example` prints the starter. **R2, R14, R15.**

### 2. Reusable workflows, each a stable check name

| Workflow | Job/check names | Does |
|---|---|---|
| `org-gate.yml` | `gate` | picks the tier (fast on the integration branch, full for a PR into a deploy branch), installs nix when declared, runs the repo's command. Empty full gate = failure. **R1, R3** |
| `org-guardrails.yml` | `secrets`, `commits`, `provenance`, `pr`, `supply-chain` | org secret patterns + SOPS-encrypted + `.env` tracked + gitleaks; ticket prefix on every commit; `author:*` labels from commit trailers (Claude-Session, bot identities), with a notice that agent PRs need human approval; PR size, one ticket, Verification section, ADR-required paths (or the waiver label), generated files changed with their inputs; actions pinned, new dependencies exist on their registry and are older than N days (slopsquatting). **R4, R8, R5 (notice), ideas 18/19/21, 1 (enforcement)** |
| `org-deploy.yml` | `plan`, `deploy`, `health`, `rollback proposal` | runs `deploy.<env>.command` on the declared runner labels inside the declared GitHub Environment (required reviewers = approver set, **R9**); polls health; on a production-class failure opens a rollback PR restoring the previous release tag's tree — proposed, never merged. **R10, idea 8/16** |
| `sync-release.yml` | `guard`, `release`, `AI release notes`, `back-merge` | merge into deploy branch = tag + Release; then a `main → staging` back-merge PR (**idea 17**); then, only when `ANTHROPIC_API_KEY` and the `ORG_RELEASE_NOTES_MODEL` variable exist, appends notes drafted with the `org-release-notes` agent prompt (**idea 14, R7**: the model is an operator variable, never in the repo) |
| `org-oci.yml` | `oci` | builds `artifacts.oci.attr` with nix and pushes it with skopeo as `<registry>/<owner>/<repo>:<tag>` (**idea 26**) |
| `org-adr-consolidate.yml` | `consolidate` | for the docs repo: gathers every allowlisted repo's `docs/adr/` into `<out>/<repo>/` with one index (**idea 1**) |
| `rollback.yml` | `Propose rollback` | dispatchable tag-based rollback PR (replaces the v1 branch-ladder rollback) |

All hook logic lives in `claude/hooks/*.py`, distributed to every repository as
`.claude/hooks/org/` by the agent-config sync (ADR-004), so the same script is the local
guardrail and the CI guardrail. **R8, R12.**

### 3. Org-level workflows

- `org-rulesets.yml` + `claude/rulesets/*.json` + `claude/apply_rulesets.py`: deploy
  branches PR-only, one approval, code-owner review, required checks `gate / gate` and
  `guardrails / *`, no force-push/deletion, linear history; integration branch protected
  against rewrites only. Applied to `claude/repos.txt`, diffed before writing. **R5, R15.**
- `org-scorecard.yml`: weekly `org-check` + sync check + `org.toml` validation per repo →
  `SCORECARD.md` PR here. **Idea 10.**
- `org-bootstrap.yml`: one-shot per repo — seed `staging`, apply the baseline, starter
  `org.toml`, `docs/adr/`, `flake.nix` on `mkDevShell`, the two pipeline workflows with
  `<org>` rendered, CODEOWNERS block, `.env` ignored, standard labels, rulesets if an
  admin token exists, one PR. **Idea 23.**
- `.github/actions/org-metrics`: one Loki push-API line per job (event, result, repo,
  actor, extra) when `ORG_METRICS_URL` is set; used by gate, guardrails, deploy, release
  (AI token usage), OCI. **Idea 24.**
- `workflow-templates/org-pipeline.yml` and `org-release.yml`: the consuming repository's
  entire CI is two ~30-line callers. Their job names are load-bearing for the rulesets.
  **R13.**

### 4. CODEOWNERS from the declaration

`org.toml [codeowners]` maps sensitive path globs to reviewer handles; the sync writes a
managed `# BEGIN/END ORG CODEOWNERS` block into `.github/CODEOWNERS`. Combined with
`require_code_owner_review` in the deploy-branch ruleset, that is the named approver set
for BTC-touching paths. **R9, idea 20.**

### 5. Provenance and approval policy

Commits carry provenance in trailers already (`Claude-Session:`); the `provenance` job
turns that into `author:*` labels. Policy: no auto-approve input exists anywhere in v2;
the deploy-branch ruleset requires a human approval; the `auto-merge` label is a human
act after review (as agreed on the call). **R4, R5.**

### 6. Requirement coverage

R1 gate before promotion · R2 forge-neutral entrypoint · R3 tiers · R4 provenance ·
R5 no agent auto-approve · R6 first-pass review (`org-reviewer` subagent, ADR-006; CI
wiring deferred, see below) · R7 cheap-model routing via operator variables · R8 same
scripts locally and in CI · R9 environments + CODEOWNERS · R10 health-gated rollback ·
R11 metrics + step summaries + labels (failure routing to sessions relies on the
session's own PR subscription) · R12 thin YAML · R13 small callers · R14 both branch
models via `org.toml` · R15 branch classes declared.

## Consequences

### Positive

- A repository opts in with `org.toml` plus two starter workflows; everything else is
  org-owned and versioned here.
- Every check is deterministic, runs locally from `.claude/hooks/org/`, and has a stable
  name a ruleset can require.
- Rollback and back-merge are automatic *proposals*, so `main` and `staging` never
  silently diverge and a bad release has a one-click path back.

### Negative

- **GitHub Free blocks rulesets on private repositories.** The first dry run answered
  403 "Upgrade to GitHub Pro or make this repository public" for every private repo.
  Until the org is on Team/Enterprise (or the repos are public), `org-rulesets.yml`
  cannot enforce PR-only `main`; the model is then a convention plus the guardrails,
  not a hard block. This is the single most important gap to close before flipping the
  kill switch. *Closed by ADR-007: client git hooks, a detect-and-revert
  branch guard, and approval/check verification before release and production deploy.*
- Nothing here has run in Actions yet; it was authored without a runner. Scripts are
  unit-tested (27 tests) and exercised against this repository and the deployments
  checkout, YAML parses, but expression edge cases will surface on the first real PR.
- Self-hosted runners on the tailnet are assumed for fleet deploys; none exist yet.
- The AI release-notes job calls the Messages API directly from a workflow. It is optional
  and off until a key and model variable are set.

## Alternatives Considered

### One monolithic reusable workflow

**Rejected:** separate workflows give separate, stable check names for rulesets and let a
repository adopt guardrails before it has a gate, or a gate before it has a deploy.

### YAML or JSON for the declaration

**Rejected:** TOML is readable, has a stdlib parser, and supports comments; JSON does not
support comments and YAML needs a dependency.

### GitHub Environments alone for production approval, no CODEOWNERS

**Rejected:** environments gate the *deploy job*, CODEOWNERS gates the *merge*; BTC-touching
changes need both.

## Implementation Notes

- Shipped; see the workflow table above and `README.md`.
- Order to go live: 1) ~~plan upgrade or public repos for rulesets~~ (ADR-007 substitutes); 2) `ORG_SYNC_TOKEN`,
  `ORG_ADMIN_TOKEN`, optional `ORG_READ_TOKEN`; 3) bootstrap one low-risk repository with
  `dry_run` then for real; 4) open a trivial PR there and watch `gate`/`guardrails`;
  5) tag `v2` here; 6) flip `ORG_WORKFLOWS_ENABLED`.
- Deferred: running `org-reviewer` as a CI check (R6 in CI) — the agent exists for
  sessions; a headless CI run needs an API key budget decision.

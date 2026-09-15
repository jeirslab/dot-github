# ADR-014: Configuration boundary and graceful workflow credentials

- **Date:** 2026-09-15
- **Status:** Accepted
- **Ticket:** INFRA-301

---

## Context

The org workflows need two kinds of input: **configuration** (the kill switch, feature-test
mode, the runner label, model names, API endpoints, the bot's ticket prefix, which workflows
a repo runs) and **credentials** (API keys, cross-repo tokens). Two problems had accumulated:

1. **No rule for where non-secret config lives.** Some sat in GitHub repository *variables*
   (`ORG_WORKFLOWS_ENABLED`, `ORG_FEAT_TEST`, `ORG_RELEASE_NOTES_MODEL`, `ANTHROPIC_BASE_URL`,
   `ORG_CHORE_TICKET`), some in `org.toml`. A model name or endpoint is not a secret and does
   not belong in variable storage; it belongs in versioned, reviewable config.

2. **Credentials were a hard dependency.** The fleet workflows *required* `ORG_SYNC_TOKEN`
   and exited 1 without it, and a target the token could not reach failed the run. That blocks
   a contributor from exercising the system at all until an org-wide token is provisioned.

Two GitHub facts constrain the design:

- A workflow/job `if:` is evaluated **before any checkout**, from the `vars`/`secrets`/`github`
  contexts only — it **cannot read a file**. So anything that gates whether a job *starts* has
  to be a variable, not `org.toml`.
- Actions never exposes the **triggering user's identity or credentials** (`github.actor` is
  only a name), and the default `GITHUB_TOKEN` is **repo-scoped** to the running repo and
  **cannot push workflow files**. Reaching any other repo requires a supplied PAT/App token.

## Decision

1. **The boundary.** GitHub repository **variables** hold only values that must be read
   *before checkout* — the kill switch `ORG_WORKFLOWS_ENABLED`, `ORG_FEAT_TEST` /
   `ORG_FEAT_TEST_BRANCH`, and the runner label `ORG_RUNNER` — plus operator toggles that must
   survive a Terranix apply (`ignore_changes` on value). **Secrets** hold only true secrets
   (API keys, tokens). **Everything else non-secret is configuration and lives in `org.toml`**,
   read at runtime by the step that needs it. Implementations that follow from this rule:
   `ORG_RELEASE_NOTES_MODEL` and `ANTHROPIC_BASE_URL` move to an `org.toml [claude]` table, and
   an `org.toml [org_workflows].enabled` gate selects which org-level workflows run in the
   engine repo (a step-level check, layered *under* the kill switch).

2. **The kill switch stays a variable, deliberately.** Gating at the job `if:` makes a disabled
   job never start (true inertness, no runner spin-up), and a variable is an instant UI flip
   that a code change cannot revert — the right shape for an emergency stop. Moving it into
   `org.toml` would make it start-then-bail and turn the panic button into a PR.

3. **Token resolution + graceful degradation.** Org-wide workflows use `secrets.ORG_SYNC_TOKEN`
   when present, else fall back to `GITHUB_TOKEN` (repo-scoped; cannot push workflow files).
   A target the current token cannot reach is **skipped with a warning and listed in the job
   summary — never a hard failure of the sweep**. A contributor can therefore drop in their own
   fine-grained PAT (scoped to the repos they own) and exercise the system before the org-wide
   token exists; out-of-scope repos are skipped, not failed.

4. **There is no "act as the triggering user."** Because Actions exposes no actor credential,
   "run it as me" is realized as *supply your own PAT + graceful skip*, not an automatic token.

5. **Same-org confinement.** A supplied token may be broader than one org (a personal token
   spans every org its owner belongs to), so the token cannot be the guardrail. Every org-wide
   workflow resolves targets through `claude/targets.py`, which drops any `owner/repo` — from
   the allowlist or the dispatch override — whose owner is not the org that owns `.github`
   (`GITHUB_REPOSITORY_OWNER`); dropped targets are reported, never acted on. Workflows that
   check out a dispatch input directly (e.g. `org-bootstrap`) carry the same owner check
   explicitly. The org-wide automation therefore cannot act outside its own org regardless of
   token scope.

6. **Related structure (recorded, implementation deferred).** Reusable logic that bundles a
   prompt or template with its action belongs in a **composite action** (`.github/actions/<name>/`,
   a real directory), while runnable workflows stay flat and thin in `.github/workflows/`.
   Shared prompt/output-schema files under `claude/` (`prompts/`, `schemas/`) are introduced
   *lazily*, the first time content is genuinely shared across a workflow and a subagent.

## Consequences

### Positive
- Adoption is incremental: teams test with a personal PAT (or none) and swap in the org-wide
  token later, without a hard blocker.
- Non-secret config is versioned and reviewable in `org.toml`, with one clear rule for where a
  setting goes.
- The kill switch keeps its safety properties (never-starts inertness, instant reversible flip).

### Negative / trade-offs
- The `GITHUB_TOKEN` fallback reaches only the running repo and cannot push workflow files, so a
  token-less run is intentionally limited — documented in each workflow's "Token mode" step.
- `org.toml` config is read post-checkout, so it is unusable for job-`if` gating — hence the
  small, explicit set of values that remain variables.
- Graceful skip could mask a genuinely misconfigured token; mitigated by a loud warning and a
  per-run summary of skipped repos.

### Neutral
- `ORG_CHORE_TICKET` remains a variable for now (it is consumed inside a `${{ }}` expression);
  moving it to `org.toml` would require a read-step and is not worth it yet.

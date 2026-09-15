# ADR-007: Enforce the branch model without paid branch protection

- **Date:** 2026-09-09
- **Status:** Proposed

---

## Context

ADR-003 makes `main`/`master` a PR-only deploy branch and enforces that with GitHub
rulesets applied as code (`org-rulesets.yml`, `claude/rulesets/*.json`). The first dry run
against the organization's private repositories answered HTTP 403, "Upgrade to GitHub Pro
or make this repository public", for every one of them. On the GitHub Free plan, private
repositories get none of:

- branch rulesets or classic branch protection (PR required, required status checks,
  no force-push, no deletion, bypass lists);
- environment protection rules (required reviewers on the `production` environment);
- required code-owner review (CODEOWNERS is honoured for review *requests* only);
- secret scanning push protection (needs Advanced Security).

The operator's direction was: keep the model, pay nothing, and be functionally equivalent
where possible. The v2 pipeline already has the pieces needed to reason about a commit
after the fact: every release and every production deploy is a workflow run with the
GitHub API in reach, and every developer and agent enters a repository through the org
Nix dev shell (ADR-005).

What a ruleset actually gives us is three guarantees. (1) Nobody can *land* an unreviewed,
ungated commit on the deploy branch. (2) Nobody can rewrite or delete the deploy branch.
(3) Production deploys need a named approver. The first can be reproduced by refusing to
*act on* such a commit; the second and third only partially.

## Decision

Enforce the branch model in three layers, none of which costs money. The paid rulesets
stay in the repository and are applied automatically the moment the plan allows it; this
ADR is the substitute until then, and it stays on afterwards as defence in depth.

### 1. Client side: versioned git hooks activated by the dev shell

Three hooks live in `claude/hooks/git/` and ship with the sync to every repository's
`.claude/hooks/org/git/` (ADR-004). The org shell hook in `flake.nix` sets
`core.hooksPath` to that directory the first time the dev shell is entered, and the
`install-hooks` app does the same outside the shell. They are ordinary versioned files, so
a change to the policy is a PR here, distributed by the sync, with no per-machine step.

- `pre-commit` exports the staged files to a temporary tree and runs the same
  `check_secrets.py` the guardrails workflow runs; a secret-shaped string or an
  unencrypted SOPS file refuses the commit.
- `commit-msg` requires the `<PROJECT>-NNN:` ticket prefix (merge, back-merge, rollback,
  revert, fixup and squash subjects are exempt, as in `check_commits.py`).
- `pre-push` reads `branches.deploy`, `branches.integration` and `gate.fast` from the
  repository's `org.toml` (defaults `main`/`master`, `staging`, none) and refuses any push
  that updates or deletes a deploy branch. Before a push to the integration branch it runs
  the declared fast gate.

`ORG_HOOKS_SKIP=1` bypasses all three for one command and `git --no-verify` bypasses the
first two; that is fine because the hooks are the *convenient* layer, not the
*authoritative* one.

### 2. Server side: detect and revert, never release

`org-branch-guard.yml` is a reusable workflow that consuming repositories call on
`push: branches: [main, master]` (the `org-release.yml` starter does). It asks the API
which pull request the pushed commit is the merge of. When there is none, or the push was
forced, it opens an issue labelled `policy-violation` naming the pusher and the commit,
opens a revert PR that restores the tree the branch had before the push, and emits a
metrics event. It never force-pushes or rewrites the branch itself; the revert is a
forward commit that a human merges, so history stays intact and the incident stays
visible.

### 3. Verification before anything irreversible

`claude/hooks/verify_pr.py` answers one question about a commit: did it land through a
pull request that had at least one approval from someone other than its author, with the
required check runs concluded `success` on its head, and, for paths matched by
`[codeowners]` in `org.toml`, an approval from a listed owner (users or resolvable team
members)? The latest review per reviewer wins, so a later "changes requested" cancels an
earlier approval, mirroring GitHub's own rule.

- `sync-release.yml` runs it in a `verify` job between the guard and the release. A merge
  that fails verification produces no tag, no GitHub Release, no back-merge and no
  release notes.
- `org-deploy.yml` runs it for production-class environments (`production`, `prod`)
  before the deploy job, so a commit that bypassed the hook and the guard still never
  reaches production. Staging deploys are not gated by it, matching the decision that
  `staging` accepts direct pushes.
- The required check names default to the gate and guardrail job names and are an input
  on both workflows, so a repository with a different gate shape can adjust them.

Together: a direct push to `main` is refused locally; if forced through, it is reverted
by PR and reported; and whatever happens, it is never released or deployed. Bypassing the
hook therefore gains nothing, which is what the ruleset guaranteed.

## Consequences

### Positive

- Zero cost; no plan change and no public repositories needed.
- The policy is code in one place, unit-tested, distributed by the existing sync, and
  runs identically on a laptop and in Actions (ADR-002 R12).
- Stronger than a ruleset in one respect: verification happens at *release and deploy
  time* against the API, so even a ruleset misconfiguration or a bypass-list member cannot
  ship an unapproved commit once the plan upgrade lands.
- The client hooks add secret scanning and ticket-prefix checks at commit time, which
  rulesets never provided.

### Negative

- **Not a hard block on the branch.** An unapproved commit can exist on `main` for the
  time between the push and the revert PR being merged, and history is not rewritten. A
  ruleset prevents; this detects. Force-pushes and branch deletions are only detected
  (a deleted branch produces no push event a reusable workflow can catch from the branch
  itself; recovery is from the last release tag).
- **The `production` GitHub Environment has no required reviewers on Free.** The
  "named approver set" collapses to "an approval on the PR from a non-author, plus a
  code-owner approval for owned paths". A code-owner team that the token cannot resolve
  degrades to a warning, not a failure, to avoid blocking releases on a token scope.
- **Verification depends on the API and on stable check names.** Renaming a gate job
  without updating `required_checks` blocks releases until fixed. That is the same
  coupling the rulesets had (ADR-003), now in one input rather than in JSON.
- The client hooks are only active for people who enter the dev shell or run
  `install-hooks`; a raw clone with `git push` goes straight to layer 2. That is by
  design, but it means the hooks are a courtesy, not evidence.
- None of the workflows has run in Actions yet; the hooks and `verify_pr.py` are covered
  by unit tests and were exercised on this repository, the workflow YAML is validated but
  unexecuted.

## Alternatives Considered

### Upgrade to GitHub Team

Roughly four dollars per user per month gives real rulesets and environment reviewers.

**Rejected because:** the operator's constraint was to pay nothing. The rulesets stay in
the repository so an upgrade is a one-line change (flip the `org-rulesets.yml` dry-run
default), not a redesign.

### Make the repositories public

Rulesets are free on public repositories.

**Rejected because:** these repositories contain infrastructure and secret layout for a
company that moves real Bitcoin. Not an option.

### A GitHub App that rejects pushes

A pre-receive hook is not available on github.com; the nearest thing is an App that
force-resets the branch when it sees a direct push.

**Rejected because:** rewriting the branch from automation destroys the evidence, races
with legitimate merges, and needs a bot identity with `contents: write` on everything.
Detect-and-revert by PR is slower but auditable and reversible.

### Client hooks only

**Rejected because:** anything client-side is bypassable in one flag; without the server
verification it is advice, not enforcement.

## Implementation Notes

- Shipped: `claude/hooks/git/{pre-commit,commit-msg,pre-push}`,
  `claude/hooks/verify_pr.py`, `.github/workflows/org-branch-guard.yml`, the `verify` jobs
  in `sync-release.yml` and `org-deploy.yml`, the `branch-guard` job in the
  `org-release.yml` starter, hook activation in `flake.nix` and the `install-hooks` app,
  distribution in `sync.py`, and the "how PR-only is enforced" section of the
  `org-branching` skill.
- The `policy-violation` and `rollback` labels must exist in a consuming repository for the
  issue and PRs to carry them (`org-bootstrap.yml` creates the standard label set).
- When the plan changes: run `org-rulesets.yml` for real and leave everything here in
  place. ADR-003's "single most important gap" note is closed by this ADR, not by the
  upgrade.

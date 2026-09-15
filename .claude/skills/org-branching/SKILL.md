---
name: org-branching
description: The organization branch model and release flow — staging as the integration branch, main/master as the PR-only deploy branch, what a release is, hotfixes, and the workflow kill switch. Use before creating branches, opening PRs into main, or reasoning about deploys and releases.
---

# Branch model and releases

## The two long-lived branches

- **`staging`** — integration branch. Direct pushes are allowed; feature branches may also
  come in as PRs when the author wants review or a cleaner history. Merging or pushing to
  `staging` deploys to the staging environment where one exists. The fast gate runs here.
- **`main`** (or `master`) — deploy branch. **PR-only, for everyone including bots.** A
  PR merged into `main` *is* a release: it is tagged, a GitHub Release is created, and
  whatever deploy the repository declares runs. The full gate, human approval, guardrails
  and first-pass review run on that PR.

Repositories not yet migrated may still use `nightly` as their working branch; follow the
repository's own rules in its `CLAUDE.md` overlay until it migrates.

## Working

1. Branch from `staging` for a ticket (`<ticket>-<slug>` or `claude/<slug>` for agent
   sessions). Commit with the ticket prefix. Push to `staging` directly or open a PR into
   it.
2. When `staging` is ready for production, open a PR `staging → main`. That PR is the
   release candidate; its body should say what is in it and what was verified.
3. An `auto-merge` label on a `staging → main` PR means "merge when the checks are
   green". It is applied by a human after review; it is never a substitute for the review
   and never applied by an agent.
4. Hotfixes: a `hotfix/*` branch may PR straight into `main` with the full gate and
   approval, never a reduced one; afterwards `main` is back-merged into `staging` so they
   never diverge.

## Releases

Handled by the org `sync-release.yml` workflow (reusable). It refuses every event other
than a PR merged into a deploy branch, so a direct push never releases. Version bump
defaults to patch; the changelog is everything since the previous tag. Release notes are
drafted by the `org-release-notes` subagent when asked.

## How "PR-only" is enforced without paid branch protection (ADR-007)

- **Locally:** the dev shell activates versioned git hooks (`.claude/hooks/org/git`):
  `pre-commit` scans staged files for secrets, `commit-msg` requires the ticket prefix,
  `pre-push` refuses a push to a deploy branch and runs the fast gate before a push to
  `staging`. `ORG_HOOKS_SKIP=1` bypasses them for one command; do not make a habit of it.
- **On the server:** a direct push to a deploy branch is detected by the branch guard,
  gets an issue naming the pusher and a revert PR, and never releases or deploys — the
  release and production-deploy workflows refuse any commit that did not land through a
  pull request with a non-author approval, green `gate`/`guardrails` checks, and (for
  code-owner paths) an owner's approval.
So bypassing the hook gains nothing; open the PR.

## Kill switch

Automatic workflow triggers in the org `.github` repository are inert until the variable
`ORG_WORKFLOWS_ENABLED` is `true`. Do not flip it; that is an operator decision with
preconditions listed in that repository's README.

# ADR-004: Distribute the org agent baseline as a managed block via per-repo sync PRs

- **Date:** 2026-09-09
- **Status:** Proposed
- **Implements:** ADR-001 Decision 5

---

## Context

ADR-001 made `jeirslab/.github` `claude/` the home for the organization's agent
configuration and deferred *how it reaches repositories* to a later decision. Since then
the operator fixed the shape: the baseline `CLAUDE.md` is inserted at the **top** of each
repository's `CLAUDE.md` as a block between `BEGIN`/`END` markers, the way a managed
section is inserted into an `authorized_keys` file, and a workflow refreshes that block
whenever the template changes. MCP server settings and Claude Code settings ride along.

The mechanism has to respect three things already decided:

- **Layering.** Everything outside the markers is the repository's own and must never be
  touched (ADR-001 Decision 3).
- **Forge portability.** The logic must run identically outside GitHub Actions
  (`deployments` ADR-068; ADR-002 R12).
- **Branch model.** `main`/`master` is PR-only; `staging` accepts direct pushes but a PR
  keeps a review trail (`ideas.md`, Emerging decisions).

## Decision

1. **One script owns the logic: `claude/sync.py`.** Standard-library Python, no
   dependencies, runnable against any checkout:
   `sync.py apply --source claude --target <repo> --version <stamp>`, plus `check`
   (exit 1 when out of sync), `render`, and `--dry-run`/`--json`. The workflow is a thin
   shell around it. Unit tests live in `claude/tests/` and run on every PR that touches
   `claude/`.

2. **`CLAUDE.md` gets a managed block.** Markers are
   `<!-- BEGIN ORG BASELINE -->` and `<!-- END ORG BASELINE -->` (HTML
   comments: invisible when rendered, trivially greppable). Rules:
   - markers absent → the block is inserted at line 1 (file created if missing);
   - markers present → the content between them is replaced wholesale;
   - content outside the markers is never modified, including anything above the block;
   - the block carries a version stamp that is **only updated when the block content
     changes**, so re-tagging this repository without touching the template produces no
     diff in consuming repositories;
   - a `BEGIN` without an `END` aborts the sync for that repo rather than guessing.
   The target file is `.claude/CLAUDE.md` if the repository already uses it, else
   `CLAUDE.md` at the root (created there when neither exists).

3. **`.claude/settings.json` and `.mcp.json` are merged, not replaced.** Settings: nested
   objects merge recursively, lists (the `permissions.allow`/`deny` arrays) are unioned
   with org entries first, org scalars win, keys the baseline does not define are kept.
   MCP: `mcpServers` merged by server name, org servers written verbatim, repo servers
   with other names kept. Only servers that work in every repository belong in the
   baseline (today: `nixos`).

4. **Opt-in allowlist.** A repository is synced only if it is listed in
   `claude/repos.txt`. Adding a repository is a reviewed PR to this repo.

5. **Delivery is a PR per repository, never a direct push.** The workflow
   `sync-agent-config.yml` runs on push to `main` touching `claude/`, weekly as a drift
   check, and on dispatch (optionally dry-run or for a subset). For each target it checks
   out `staging` if the branch exists, else the default branch, applies the script, and
   opens or updates a single PR on the branch `chore/org-agent-config`. A repo already in
   sync produces no PR. Drift (an edit inside the block) is corrected by the same PR, whose
   body tells the author where the edit belongs.

6. **Identity and permissions.** The workflow needs `ORG_SYNC_TOKEN`, a fine-grained PAT
   or GitHub App token with `contents: write` and `pull-requests: write` on the target
   repositories only. Commits are authored as `org-config-sync` and carry the standing
   ticket prefix so the org commit rule holds for bot commits too.

## Consequences

### Positive

- Every rule added to the baseline reaches every opted-in repository as a reviewable PR
  within one workflow run, and the boundary between org and repo rules is visible in the
  file itself.
- The same script is the bootstrap for a new repository (run it once) and the drift
  detector (run it again), which `ideas.md` 10 and 23 build on.
- No GitHub-specific logic in the script; a developer on any forge can run it by hand.

### Negative

- Repositories that already restate org rules in their own section (`deployments` does)
  will carry the rule twice until their overlay is trimmed. ADR-001 lists that trim as a
  follow-up; the sync does not attempt it.
- One PR per repo per baseline change is noise if the baseline churns. Batch changes to
  `claude/` and let the weekly run pick up the rest.
- A token with write access to every consuming repository is a credential worth
  protecting: keep it fine-grained, scoped to the allowlisted repos, and rotate it.

## Alternatives Considered

### Claude Code plugin reference instead of copying files

Publish `claude/` as a plugin and have repositories reference it, so nothing is copied.

**Deferred, not rejected:** it removes the copy step but does not give the operator the
"block at the top of the file" shape they asked for, and it cannot merge a repo's own
`settings.json`. Revisit if the sync PRs become noisy.

### Push directly to `staging` instead of opening PRs

`staging` accepts direct pushes, so the sync could commit there.

**Rejected for now:** a PR leaves a trail of what changed in the baseline and when,
which matters when the block rewrites rules that agents follow. Can be revisited per
repository once the mechanism has run for a while.

### Merge the block into the repo's prose instead of a marker-delimited section

**Rejected because:** there is no way to update it later without the markers; the
authorized_keys pattern exists precisely because in-place managed sections need
boundaries.

## Implementation Notes

- Shipped: `claude/sync.py`, `claude/tests/test_sync.py`,
  `claude/repos.txt`, `.github/workflows/sync-agent-config.yml`,
  `.github/workflows/_claude-config-ci.yml`.
- Verified locally: 13 unit tests; a dry run against a copy of the `deployments` checkout
  inserts the block at the top of `.claude/CLAUDE.md`, unions the permission lists, adds
  the `nixos` server next to the fleet servers, and a second run is a no-op.
- Not yet exercised: a real workflow run against a target repository. That needs
  `ORG_SYNC_TOKEN` set as an organization or repository secret; the first run should be a
  `workflow_dispatch` with `dry_run: true`.
- Follow-ups: trim the org-wide sections out of `deployments/.claude/CLAUDE.md` once its
  sync PR merges (ADR-001); distribute `hooks/` and `skills/` when they have content.

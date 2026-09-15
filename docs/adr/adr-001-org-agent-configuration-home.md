# ADR-001: `jeirslab/.github` is the home for organizational agent configuration

- **Date:** 2026-09-07
- **Status:** Proposed

---

## Context

jeirslab's engineering work is increasingly done with coding agents (Claude Code in the
terminal, in the cloud, and headless per `deployments` ADR-068). What those agents do well
or badly is dominated by the *harness* around the model: rule files, permissions, hooks,
skills, MCP server definitions, and the conventions those files encode. Google's May 2026
"The New SDLC with Vibe Coding" paper (Osmani, Saboo, Kartakis) makes the point bluntly:
"most agent failures, examined honestly, are configuration failures," and it recommends
that teams "invest in the harness components as a shared team asset ... treat them as
infrastructure: documented, maintained, and improved deliberately," reviewed in PRs and
versioned like code.

Today that harness exists in exactly one place. `jeirslab/deployments` carries a 280-line
`.claude/CLAUDE.md` and a `.claude/settings.json`. It encodes rules that are actually
organization-wide (the mandatory Jira ticket workflow, the `<PROJECT>-NNN:` commit prefix,
concurrent-agent staging discipline, never committing secrets, ADR practice) mixed with
rules that are specific to the fleet repo (the `fleet` CLI, Terranix, SOPS layout). Every
other repository in scope (`backend-api-v2`, `etl-pipeline`, the Lovable-driven frontends)
has **no** agent configuration at all. An agent working in those repos does not know the
Jira rule exists.

A `jeirslab/claude` repository was created to hold shared agent configuration. It is empty
(no commits, no default branch). Meanwhile `jeirslab/.github` already is the organization's
shared-automation home: reusable workflows, composite actions, starter templates, tagged
`v1`..`v1.0.7`, with a self-documenting `WORKFLOWS.md` generator. The plan is for one of
its workflows to distribute the shared agent configuration into every repository, so the
configuration and the mechanism that ships it should live together.

Two constraints shape the decision:

- **Layering, not overwriting.** Repo-specific rules (the `fleet` CLI in `deployments`, the
  no-cross-package-imports rule in `backend-api-v2`) must survive distribution. The org
  baseline is the floor, not the whole file.
- **Forge portability.** `deployments` ADR-068 rejects GitHub Actions as an *engine* for
  agents while accepting it as a PR destination and downstream CI step. Configuration
  stored here must be plain files a self-hosted forge or a local clone can consume without
  GitHub-specific tooling.

## Decision

1. **`jeirslab/.github` is the single, primary home for organizational agent configuration.**
   The `jeirslab/claude` repository is folded in: it has no content to migrate, so the
   action is to archive it once this ADR is accepted and to point any references here.

2. **The configuration lives under `claude/` at the repository root**, outside `.github/`,
   so it is not mistaken for workflow code and so a distribution mechanism can copy the
   directory as a unit:

   ```
   claude/
   ├── README.md        # what this is, how it is distributed, how to change it
   ├── CLAUDE.md        # org baseline rule file (the floor every repo inherits)
   ├── settings.json    # org baseline Claude Code settings (permissions, hooks)
   ├── mcp.json         # org baseline MCP servers (only ones that work in every repo)
   ├── hooks/           # deterministic guardrail scripts referenced by settings.json
   └── skills/          # shared skills (one directory per skill, SKILL.md inside)
   ```
   `hooks/` and `skills/` are declared now and populated by follow-up tickets; this ADR
   fixes the layout so those tickets do not each invent one.

3. **Two-layer model.** Every repository ends up with the org baseline plus an optional
   repo overlay. The baseline is *general*: rules that are true in every jeirslab repository
   regardless of stack. Anything that names a repo-specific tool, path, or service belongs
   in that repository's own `CLAUDE.md`. The baseline must never be edited inside a
   consuming repository; changes go through a PR here and flow out. Concretely, the
   baseline is a marker-delimited block (`<!-- BEGIN/END ORG BASELINE -->`)
   inserted at the top of the consuming repository's `CLAUDE.md` and replaced wholesale on
   sync, so the boundary between layers is visible in the file itself.

4. **Versioned and reviewed like code.** Changes to `claude/` are ordinary PRs to `main`
   in this repository, subject to the same review as workflow changes, and they ride the
   repository's existing tag stream (`_version-workflows-repo.yml`). Consuming
   repositories will be able to pin a tag.

5. **Distribution is a separate decision.** The workflow that copies `claude/` into every
   repository (push-based PRs, a scheduled sync, or a Claude Code plugin/marketplace
   reference) gets its own ADR when it is designed. This ADR only guarantees that whatever
   it is, it reads from `claude/` in this repository and preserves the two-layer model.
   *Decided in ADR-004: a managed block plus per-repo sync PRs.*

6. **Plain files only.** Nothing under `claude/` may require GitHub to be interpreted.
   `settings.json` is standard Claude Code configuration; hooks are shell or Python
   scripts; skills are Markdown. A developer can `cp -r` the directory into a local clone
   on any forge and get identical agent behaviour.

## Consequences

### Positive

- One reviewed, versioned source of truth for how agents behave across the organization.
  A rule added once (for example after an agent does something it should not do again,
  the paper's suggested cadence) reaches every repository.
- Repositories that currently have no agent configuration inherit the Jira workflow,
  commit-prefix, secrets and staging rules for free.
- Hooks become the place for rules that are "hook-shaped" in the deployments `CLAUDE.md`
  today but only enforced by prose: SOPS files must be encrypted, `hosts.json` is never
  hand-edited, commits carry a ticket prefix, `git add -A` is forbidden.
- The empty `jeirslab/claude` repository stops being a second place people look.

### Negative

- Two layers means two files to read to know an agent's full rule set. The `claude/README.md`
  must state the precedence clearly, and the distribution workflow must make the boundary
  visible in the consuming repository (a header comment or a separate file rather than a
  merged blob).
- Until the distribution workflow ships, the baseline is aspirational for every repo except
  the ones where someone copies it by hand. That gap is bounded by a follow-up ticket, not
  left open.
- Baseline changes can break repo-specific expectations in a consuming repository. Tag
  pinning mitigates this; the distribution ADR must decide the default (float on `latest`
  versus pin).

## Alternatives Considered

### Keep `jeirslab/claude` as a separate repository

A dedicated repository for agent configuration, distributed from there.

**Rejected because:** it splits the configuration from the automation that ships it, adds a
second tag stream and a second place to look, and the repository has zero content today so
there is nothing to preserve by keeping it.

### Per-repository configuration only, no shared baseline

Each repository maintains its own `CLAUDE.md` and settings, copying from `deployments` as a
starting point.

**Rejected because:** that is the status quo, and it produced one repository with a rich
harness and every other repository with none. Copies drift the day after they are made.

### Git submodule pointing at the shared configuration

Consuming repositories add `jeirslab/.github` (or a subtree of it) as a submodule.

**Rejected because:** Claude Code reads `CLAUDE.md` and `.claude/settings.json` at fixed
paths, so a submodule still needs a copy or symlink step; submodules in the Lovable-driven
repositories are impractical; and the deployments repo already carries enough submodule
maintenance burden.

### Claude Code plugin marketplace as the only distribution

Publish `claude/` as a plugin and have every repo reference it.

**Not rejected, deferred:** a plugin reference is a strong candidate for the distribution
mechanism and keeps consuming repositories thin. It does not change where the source lives,
which is what this ADR decides. The distribution ADR will evaluate it.

## Implementation Notes

- This ADR ships with the initial `claude/` tree: `README.md`, a baseline `CLAUDE.md`
  extracted from the organization-wide sections of `deployments/.claude/CLAUDE.md`, and a
  baseline `settings.json`. `hooks/` and `skills/` are created empty (with a `.gitkeep`) and
  populated under follow-up tickets.
- Follow-ups to file when this ADR is accepted:
  - Archive `jeirslab/claude`.
  - Distribution workflow ADR (see Decision 5).
  - First hooks: encrypted-SOPS check, commit-prefix check, forbidden `git add -A`.
  - Trim the organization-wide sections out of `deployments/.claude/CLAUDE.md` once the
    baseline reaches that repository, so the rules exist in one place.
- The v1 promotion pipeline in this repository is deprecated by ADR-002; the replacement
  will consume the shared configuration (agent identity, review rubric) from `claude/`.

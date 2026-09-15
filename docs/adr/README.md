# Architecture Decision Records — `jeirslab/.github`

This repository is the organization-level home for two things:

1. **Reusable GitHub Actions building blocks** (`.github/workflows`, `.github/actions`).
2. **Organizational agent configuration** (`claude/`) — the shared harness every jeirslab
   repository's coding agents run under (ADR-001).

Decisions that shape either of those live here, numbered sequentially. Repository-specific
decisions stay in their own repository (for example `deployments/docs/adr/`), and are
cross-referenced by number with a repo prefix when needed (e.g. `deployments ADR-068`).

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](adr-001-org-agent-configuration-home.md) | `jeirslab/.github` is the home for organizational agent configuration | Proposed |
| [ADR-002](adr-002-agent-aware-promotion-pipeline.md) | Replace the v1 branch-promotion pipeline with an agent-aware promotion pipeline | Proposed |
| [ADR-003](adr-003-v2-pipeline-design.md) | The v2 org pipeline — `org.toml`, reusable gate/guardrails/deploy, rulesets as code | Proposed |
| [ADR-004](adr-004-agent-config-distribution.md) | Distribute the org agent baseline as a managed block via per-repo sync PRs | Proposed |
| [ADR-005](adr-005-dev-environment-baseline-flake.md) | Every repository's developer environment is a Nix flake built on the org baseline | Proposed |
| [ADR-006](adr-006-lean-baseline-skills-subagents.md) | Lean baseline block; procedures as on-demand skills, specialised work as subagents | Proposed |
| [ADR-007](adr-007-free-plan-branch-enforcement.md) | Enforce the branch model without paid branch protection: git hooks, branch guard, release/deploy verification | Proposed |
| [ADR-008](adr-008-org-jira-cli-and-branch-sweep.md) | The org Jira interface lives in `.github`: snapshot-first CLI and branch ↔ ticket sweep | Proposed |
| [ADR-009](adr-009-feature-test-mode.md) | Feature-test mode: org workflows are exercised on this repository (`feature-test` + `ORG_FEAT_TEST`) before they go org-wide | Proposed |
| [ADR-010](adr-010-github-org-config-as-terranix.md) | The organization's GitHub configuration is a Terranix stack (`platform.github`) under `nix/github/` | Proposed |
| [ADR-011](adr-011-runner-policy.md) | Runners: trusted org jobs on an ephemeral fleet runner selected by variable; gates stay hosted | Proposed |
| [ADR-012](adr-012-repos-toml-fleet-registry.md) | The org fleet is one `repos.toml`, read by both the workflow selector and the terranix stack | Proposed |
| [ADR-013](adr-013-repo-creation-and-templates.md) | The stack can create repositories and generate them from templates, registry-driven | Proposed |
| [ADR-014](adr-014-config-boundary-and-graceful-credentials.md) | Non-secret config lives in `org.toml` (vars only for pre-checkout gating); org-wide workflows fall back to `GITHUB_TOKEN` and skip-not-fail unreachable repos | Accepted |

Conventions:

- Use [`adr-template.md`](adr-template.md). File name `adr-NNN-<slug>.md`.
- Every ADR carries the Jira ticket that drove it.
- Superseding an ADR: set the old one's status to `Superseded by ADR-NNN`; never edit its decision text.

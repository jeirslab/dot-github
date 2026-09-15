# ADR-002: Replace the v1 branch-promotion pipeline with an agent-aware promotion pipeline

- **Date:** 2026-09-07
- **Status:** Proposed
- **Deprecates:** `.github/workflows/promote-pr.yml` (v1), `workflow-templates/loveable-promotion.yml`

---

## Context

### What v1 is

The organization's promotion pipeline was built in this repository in mid-2026 and tagged
`v1` (`v1.0.0` .. `v1.0.7`). It consists of:

| Piece | What it does |
|-------|--------------|
| `create-branches.yml` | Seeds `dev`, `qa`, `prod` from `main` in a new repository. |
| `promote-pr.yml` | Opens a PR from one branch to the next in the fixed path `main → dev → qa → prod → main`, after a merge simulation to detect conflicts. Optionally **auto-approves** the PR when there are no conflicts. |
| `loveable-promotion.yml` (starter template) | Wires `promote-pr` so that a push to `main` by the Lovable bot auto-opens and auto-approves a `main → qa` PR, a merged `qa` PR opens `qa → prod`, and a merged `prod` PR triggers `sync-release`. |
| `rollback.yml` | Opens an audited revert PR from a broken branch to a known-good one. |
| `sync-release.yml`, `version-and-package-source.yml`, `_compute-version.yml` | Fast-forward `prod → main`, compute the next semver, tag, create a GitHub Release. |
| `.github/actions/slack/*` | Slack notification composites. |
| `_generate_workflow-docs.yml` | Regenerates `WORKFLOWS.md` from `# @doc:` comments. |

It is a clean, well-structured **branch-movement** pipeline. It was designed for one
consumer shape: a Lovable-generated frontend whose `main` is written by a bot, promoted
through environments by branch, and released by tag.

### What v1 does not do

`promote-pr.yml` verifies exactly one thing: that the source merges into the target
without conflicts. It then labels the PR "Safe to merge" and, when `auto_approve` is set,
approves it. Nothing in the path runs tests, lint, a type check, a build, a contract
check, or a deployment health check. The PR body carries a manual checklist ("Tests
passing", "No breaking changes") that nobody is required to tick.

That was tolerable when the only author on `main` was a single bot producing UI code.
It is not tolerable now, for three reasons.

### Why now

1. **Agents are now the majority author.** Claude Code sessions (terminal, cloud, and the
   headless alert-response agent of `deployments` ADR-068), the Lovable bot, and humans all
   open PRs into jeirslab repositories. Google's May 2026 "The New SDLC with Vibe Coding"
   paper (Osmani, Saboo, Kartakis) draws the line between "vibe coding" and production-grade
   "agentic engineering" at exactly one thing: whether "automated test suites, CI/CD gates,
   LM judges" verify the output. By that definition the v1 pipeline turns every consumer
   into a vibe-coded repository, whatever discipline the author applied locally. The paper
   also warns explicitly against the v1 `auto_approve` pattern: AI-generated code "requires
   the same or greater scrutiny than human-written code."

2. **The consumers are heterogeneous.** `deployments` (Nix flakes, Terranix, Colmena;
   verification is `nix eval`/`nix flake check`; working branch `nightly`), `backend-api-v2`
   (Node/TypeScript packages behind OpenAPI/AsyncAPI contracts; working branch `nightly`),
   `etl-pipeline` (Python, Docker Compose, `just`), and the Lovable frontends
   (`main/dev/qa/prod`). A promotion pipeline that hard-codes one branch model and no
   verification hook cannot serve them, so today three of the four have **no** pipeline.

3. **The stakes are real BTC.** `deployments` runs mainnet nodes and inscription services
   that move real funds, with strict mainnet/testnet separation (`deployments` ADR-004).
   "Merges cleanly" is not an acceptable production gate for that codebase.

### Constraints inherited from existing decisions

- **Forge portability** (`deployments` ADR-068): GitHub Actions is acceptable as a PR
  destination and as a downstream CI step, and explicitly rejected as the *engine* for any
  agent. The operator does not want lock-in to the Actions runtime. Pipeline logic must
  therefore live in scripts and Nix that run identically on a laptop, a self-hosted runner,
  or another forge; Actions YAML is a thin shell.
- **No interim fixes, everything declared** (`deployments` CLAUDE.md rule 2): a pipeline
  that patches a live host to get green is forbidden. Deploy steps go through `fleet`.
- **Destruction safety** (`deployments` fleet manifest `protect`, `fleet deploy tf destroy`
  preflight): the pipeline must never gain the ability to bypass these.
- **Jira workflow**: every change is tied to a ticket and every commit carries a
  `<PROJECT>-NNN:` prefix. The pipeline can and should check this.
- **Shared agent configuration** (ADR-001): review rubrics, hooks, and agent identity
  conventions live in `claude/` in this repository; the pipeline consumes them rather than
  re-defining them.

### What the paper contributes, concretely

The paper is not a CI/CD design. The parts that transfer are principles, and they are
the requirement sources below:

- **Verification is the definition of production-grade**, so promotion without a gate is
  not promotion (R1..R3).
- **The factory model**: specs in, agents implement, tests and quality gates verify,
  **feedback loops route failures back to agents**, guardrails constrain (R10, R11).
- **Hooks are deterministic code at lifecycle points** for things an agent must never
  forget: its own example is blocking a commit that contains a hard-coded password (R8).
- **AI as first-pass reviewer, never the only reviewer**; reviewers of AI code need a
  checklist tuned to hallucinated dependencies, thin error handling, and "looks right"
  correctness gaps (R5, R6).
- **AI-aware deployment**: monitor deploy health, roll back bad releases, predict risk
  from the scope of a change (R10).
- **Model routing**: cheap models for review, test generation and pipeline monitoring;
  frontier models for architecture (R7).
- **Make the prototype/production boundary explicit** by project, branch, and environment
  (R15).
- **Treat the harness as code**, reviewed and versioned (ADR-001, R8).

## Decision

1. **The v1 promotion pipeline is deprecated as of this ADR.** `promote-pr.yml` keeps
   working for existing `@v1` callers but announces its deprecation on every run, and the
   `loveable-promotion` starter template is withdrawn from the Actions gallery so no new
   repository adopts it. It will be removed when the replacement reaches `v2` and every
   caller has migrated. `rollback.yml`, the versioning workflows, and the Slack composites
   are **not** deprecated; the replacement is expected to keep them.

2. **A new, agent-aware promotion pipeline will be designed from scratch** in a follow-up
   design ADR (ADR-003) and shipped as `v2` of this repository. This ADR does not design
   it. It fixes the requirements the design must satisfy, the constraints it must respect,
   and the questions it must answer.

3. **Requirements the v2 design must satisfy.** Each is numbered so the design ADR can
   show compliance line by line.

   **A. Verification gates**

   - **R1** No promotion PR may be marked safe, approved, or merged unless a verification
     gate has passed on the exact commit being promoted. Conflict-free merge is a
     precondition, never a gate.
   - **R2** Each consuming repository declares its gate through a single forge-neutral
     entrypoint the pipeline invokes without knowing the stack (for example
     `nix flake check`, `just ci`, `npm test`). The pipeline never embeds repository logic.
   - **R3** Gates are tiered by target environment. The design ADR defines the tiers; at
     minimum, promotion into a production-class branch or environment requires the full
     gate plus the post-deploy check (R10), while promotion into development-class targets
     may run a fast subset.

   **B. Agents as first-class participants**

   - **R4** Every promoted change carries machine-readable provenance: which class of
     author produced it (human, Claude Code session, Lovable bot, alert-response agent),
     with a link to the session or run where one exists. Commit trailers and PR labels are
     the expected carriers. The pipeline reads provenance and applies policy by author
     class.
   - **R5** No change authored or co-authored by an agent is auto-approved. The v1
     `auto_approve` input has no v2 equivalent. Promotion into a production-class target
     always requires at least one human approval from a named approver set.
   - **R6** An AI first-pass review runs on every promotion PR using the organization
     rubric from `claude/` (ADR-001). Its output is advisory but not ignorable: each finding
     must be resolved by a change or explicitly dismissed by a human before merge. Findings
     from the review are treated as bug reports, not opinions.
   - **R7** Agents may be *invoked* by the pipeline (review, PR summarization, failure
     diagnosis, changelog generation) and are routed to the cheapest adequate model for
     those tasks. Agents are never the pipeline's engine, and the pipeline never grants an
     invoked agent write access beyond commenting on the PR (`deployments` ADR-068).

   **C. Guardrails**

   - **R8** Deterministic guardrails run inside the pipeline using the same scripts that run
     as local hooks (`claude/hooks/`, ADR-001), so local and CI enforcement cannot drift.
     Initial set: secret and plaintext-credential scan, SOPS files encrypted, commit
     message carries a valid ticket prefix, generated files (lockfiles, `hosts.json`) not
     hand-edited, no `.env` committed.
   - **R9** For repositories that touch Bitcoin funds or keys, promotion into a mainnet
     class target additionally requires a protected environment with a named approver set
     distinct from the change author, and the pipeline must be unable to trigger any
     destructive infrastructure operation (it may plan, never apply-destroy).

   **D. Deployment and feedback loop**

   - **R10** A production-class promotion is complete only when a post-deploy health
     verification passes (`deployments` ADR-035 declarative health checks, or the
     repository's equivalent). A failed health check automatically opens a rollback PR via
     the existing `rollback.yml` with the failing evidence attached. Rollback is proposed,
     never auto-merged.
   - **R11** Every run emits a structured summary (gate results, author class, review
     verdict, health result) to the step summary and to Slack via the existing composites.
     A failure is routed back to the authoring agent where a session or bot identity is
     known, so the factory loop closes without a human copy-pasting logs.

   **E. Portability and structure**

   - **R12** Actions YAML is a thin shell. All decision logic (gate selection, provenance
     parsing, policy by author class, health evaluation) lives in versioned scripts or Nix
     that run locally with the same inputs and outputs.
   - **R13** The pipeline is a reusable `workflow_call` workflow, tagged like v1, and a
     consuming repository's caller is small enough to read in one screen.
   - **R14** The pipeline supports both branch models in use today, `main/dev/qa/prod`
     and `nightly/main`, or the design ADR explicitly decides to converge them and
     schedules the migration. It must not silently assume one.
     *Operator direction (2026-09-07, recorded in `ideas.md`):* the intended target is
     `staging → main`, with `main` as the deploy branch and no direct pushes to it. The
     design ADR should treat that as the default and plan the migration from both
     current models.

   **F. Explicit boundaries**

   - **R15** Each consuming repository declares which branches and environments are
     prototype-class (fast gate, agent changes flow freely) and which are production-class
     (full gate, human approval, health check). The declaration is data the pipeline reads,
     not convention in a README.

4. **Non-goals of this ADR.** It does not choose the AI review tool or model, does not
   pick the branch model, does not decide how repositories declare their gate (file
   format, location), and does not decide whether the merge queue or rulesets features of
   GitHub are used. Those are design decisions for ADR-003.

## Consequences

### Positive

- Every jeirslab repository can use one pipeline, because verification is delegated to a
  per-repo entrypoint rather than embedded.
- Agent-authored PRs become safer to accept at volume: provenance, first-pass review, and
  mandatory human approval into production are structural rather than a reviewer's habit.
- Guardrails written once (ADR-001 hooks) enforce the same rules locally and in CI.
- The rollback path gains a trigger (health check) instead of depending on someone
  noticing.
- Forge portability is preserved by construction (R12), which keeps `deployments` ADR-068
  honest.

### Negative

- Consuming repositories must do work before they can use v2: declare a gate entrypoint
  (R2), classify their branches (R15), and adopt provenance trailers (R4). Repositories
  with no tests today get no benefit from R1 until they have some.
- The Lovable frontends lose the frictionless `main → qa` auto-approve flow. That is the
  intent, but it changes the team's day-to-day for those repositories and needs a
  migration note.
- AI review (R6) costs tokens on every promotion PR. Model routing (R7) bounds it; the
  design ADR should estimate it.
- Two pipelines coexist until v1 is removed. The deprecation notice and the withdrawn
  template limit new adoption, but existing callers stay on v1 until migrated.

## Alternatives Considered

### Keep v1 and bolt verification onto it

Add a `run_checks` input to `promote-pr.yml` that executes a repository command before
the merge simulation.

**Rejected because:** it patches R1 only. Provenance, author-class policy, AI review,
health-gated rollback and branch-model flexibility are structural and do not fit a
workflow whose shape is "open a PR between two fixed branches." Bolting them on produces
the eight-hundred-line YAML that R12 forbids.

### Per-repository pipelines, no organization-level reusable workflow

Each repository writes its own Actions workflows tuned to its stack.

**Rejected because:** three of four repositories have written nothing in the months the
option has existed, and the parts that matter most (provenance policy, guardrails, AI
review rubric, health-gated rollback) are organization policy, not stack detail. Stack
detail is exactly what R2 pushes into the repository.

### Self-hosted CI only (revived Hydra, Woodpecker, or Forgejo Actions)

Move the whole pipeline off GitHub to satisfy ADR-068's portability concern fully.

**Rejected for now, kept open:** ADR-068 accepts Actions as a CI step and PR destination,
and GitHub is the current remote for every repository. R12 keeps the logic portable so
this remains a later switch rather than a rewrite. Hydra on `nix-builder` is
decommissioned pending a jobset-discovery story and is not a base to build on today.

### GitHub merge queue and repository rulesets instead of a promotion workflow

Rely on required status checks, rulesets, and the merge queue to gate branches.

**Partially adopted, not sufficient:** rulesets are the right tool for R5 and R9 (required
approvals, protected environments) and the design ADR should use them. They do not provide
provenance parsing, AI review, health-gated rollback, Slack summaries, or the reusable
caller that lets a repository opt in with a small file (R13).

### Drop promotion branches entirely (trunk-based, tag-to-deploy)

Replace `main/dev/qa/prod` with a single trunk and environment deploys keyed by tag.

**Deferred to ADR-003 via R14:** this may be the right end state for the Nix repositories,
which already work from `nightly` and deploy with `fleet`. The Lovable repositories are
constrained by the bot writing to `main`. The design ADR decides; this ADR only requires
that the pipeline does not assume one model.

## Open Questions for the Design ADR (ADR-003)

1. Where does a repository declare its gate entrypoint and branch classes (R2, R15): a
   `promotion.toml`/`.yaml` at the root, a flake output, or inputs on the caller workflow?
2. Which AI review tool and model (R6, R7), and what is the token budget per PR?
3. How is provenance carried for Lovable commits, which do not pass through a Claude
   session (R4)?
4. Does the `deployments` repository use v2 for `nightly → main`, or only for the
   post-deploy health and rollback loop, given that deploys happen through `fleet` from an
   operator machine and not from a runner (R10)?
5. Does the health check for R10 run from a runner with fleet network access, or does the
   runner poll a signal published by the fleet (Grafana alert state, ADR-035 result)?
6. Sunset date for the `v1` tag, and the migration path for each existing caller.
7. ~~Whether the `create-branches.yml` seeding workflow survives R14, and what
   `sync-release.yml` becomes when `main` is the deploy branch.~~ Answered:
   `create-branches.yml` seeds `staging`; `sync-release.yml` releases on a PR
   merged into `main`/`master` and refuses every other event. Remaining: the deploy step
   itself and the back-merge/hotfix path (`ideas.md` 16, 17).

## Implementation Notes

Done with this ADR:

- `promote-pr.yml`: renamed to mark deprecation, `# @doc:` updated, and a leading job
  that emits a workflow warning pointing here on every run. Behaviour otherwise unchanged
  so `@v1` callers keep working.
- `workflow-templates/loveable-promotion.{yml,properties.json}` removed, which withdraws
  the starter from the organization's Actions gallery. Existing repositories that copied
  it are unaffected until v1 is removed.
- `README.md` and `WORKFLOWS.md` updated to reflect the deprecation and the new `claude/`
  and `docs/adr/` trees.

Done (2026-09-08), ahead of the design ADR because the branch model was
decided by the operator and both changes are prerequisites for it:

- `sync-release.yml` re-based on "a PR merged into `main`/`master` is a release": no more
  `prod → main` fast-forward; a guard job turns any other event into a no-op with a notice
  so a direct push can never release. Filename kept so `@v1` callers still resolve.
- `create-branches.yml` seeds `staging` from the default branch instead of `dev/qa/prod`.
- `_compute-version.yml` changelog range corrected to "since the previous release".

To be filed when this ADR is accepted:

- ADR-003 (design), answering the open questions above and
  mapping each R-number to a mechanism.
- One migration ticket per existing v1 caller.
- The `deployments` ADR-002 testing strategy is stale (it describes Pulumi mocks) and
  should be superseded before ADR-003 relies on it for the R2 entrypoint of that repository.

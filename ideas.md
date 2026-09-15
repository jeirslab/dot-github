# Workflow ideas — the checklist

The parking lot for organization-level workflow ideas, kept as a checklist so it doubles
as the map of what the current setup actually implements. An idea graduates by getting a
Jira ticket and, if it changes how the org works, an ADR in `docs/adr/`. `R-numbers` are
the requirements in [ADR-002](docs/adr/adr-002-agent-aware-promotion-pipeline.md).

Conventions: one screen per idea, problem before mechanism, link tickets and ADRs. When
a piece ships, tick its box and name the file that implements it. When something is
decided against, move it to **Rejected / superseded** with a one-line reason instead of
letting it rot; never delete the reasoning.

Legend: `[x]` shipped and verified locally or in tests · `[ ]` open · **(Actions)** = not
yet exercised in a real GitHub Actions run · **(plan)** = blocked by the GitHub Free plan.

## Status board

| # | Idea | Status | Where |
|---|------|--------|-------|
| 1 | ADR everywhere + consolidation | shipped | INFRA-276 · `pr` guardrail, `org-adr-consolidate.yml` |
| 2 | Org agent-config as a managed block | shipped | INFRA-262/272 · ADR-004 · `claude/sync.py` |
| 3 | Verification-gate contract (`org.toml` + gate runner) | shipped | INFRA-276 · ADR-003 · `org-gate.yml` |
| 4 | Provenance labels and author-class policy | shipped | INFRA-276 · `hooks/provenance.py` |
| 5 | AI first-pass review with the org rubric | partly | ADR-006 · subagent `org-reviewer`; as a PR check → idea 31 |
| 6 | Guardrails from the same scripts locally and in CI | shipped | INFRA-276/277 · `org-guardrails.yml`, git hooks |
| 7 | Jira linkage | partly | commit prefix shipped; stale data via idea 28; auto-transition rejected |
| 8 | Post-deploy health → rollback proposal | shipped | INFRA-276 · `org-deploy.yml`, `rollback.yml` |
| 9 | Route CI failures back to the authoring agent | open | depends on idea 31 |
| 10 | Org conformance scorecard | shipped | INFRA-273/276 · `org-check.sh`, `org-scorecard.yml` |
| 11 | Contract drift check for `backend-api-v2` | open | |
| 12 | Dependency freshness PRs with the gate attached | open | |
| 13 | Harness evals for `claude/` | partly | 51 unit tests; behaviour evals open |
| 14 | Agent-generated release notes | shipped | INFRA-276 · `sync-release.yml` `release_notes` |
| 15 | Branch protection as code | shipped (plan) | INFRA-276/277 · rulesets stay; ADR-007 substitutes |
| 16 | Deploy on merge to `main` | shipped | INFRA-276/289/290 · `org-deploy.yml`; runner module + one-command bootstrap (ADR-011); first real bring-up open |
| 17 | Hotfix path and back-merge | shipped | INFRA-276 · `sync-release.yml` `back_merge` |
| 18 | Supply-chain checks incl. slopsquatting | shipped | INFRA-276 · `supply-chain` guardrail |
| 19 | Secrets that should not be there | shipped | INFRA-276/277 · `secrets` guardrail, `pre-commit` hook |
| 20 | CODEOWNERS for sensitive paths | shipped | INFRA-276/277 · managed block; `verify_pr.py` enforces |
| 21 | PR hygiene for agent PRs | shipped | INFRA-276 · `pr` guardrail; merge queue open |
| 22 | Stale branch cleanup | superseded | by idea 28 (report, never delete) |
| 23 | Repo bootstrap | shipped | INFRA-276 · `org-bootstrap.yml` |
| 24 | CI observability and AI cost metering | partly | transport shipped (`org-metrics`); dashboards open |
| 25 | Nix binary cache from CI | open | |
| 26 | Nix-built OCI artifacts | partly | build+push shipped (`org-oci.yml`); Proxmox pull open |
| 27 | Nix vs distroless image comparison | open | |
| 28 | Branch ↔ ticket sweep | shipped | INFRA-278 · ADR-008 · `jira sweep`, `org-branch-sweep.yml` |
| 29 | Declarative GitHub org config via OpenTofu | partly | INFRA-286/289 · ADR-010/011 · `nix/github/`, `org-platform-github.yml`; first plan open |
| 30 | Event ingress: Jira and GitHub webhooks → dispatch router | open | proposed 2026-09-09 |
| 31 | Agent action endpoint (OpenAPI) on the fleet | open | proposed 2026-09-09 |
| 32 | Verification agent (terminal + Playwright MCP) and the Done-with-evidence rule | shipped | INFRA-282 · `org-verifier`, `check_pr.py`, `org-jira` skill |
| 33 | Feature-test mode + workflow test suite | shipped | INFRA-283 · ADR-009 · `targets.py`, `_workflow-tests.yml`, `test_workflows.py` |

Every "shipped" row above carries the **(Actions)** caveat until the kill switch flips
(`ORG_WORKFLOWS_ENABLED`) and the first real runs happen — see `README.md`.

## Decisions taken while ideating (ratified in ADR-003 … ADR-008)

- `main`/`master` is the deploy branch; a PR merged into it *is* a release. PR-only for
  everyone, bots included. `staging` is the integration branch and accepts direct pushes.
- Lovable-driven repositories are out of scope until someone decides otherwise.
- Org agent config is a managed block at the top of each repo's `CLAUDE.md` (ADR-004);
  the block is lean, procedures are `org-*` skills, bounded jobs are `org-*` subagents
  (ADR-006, engineering lead's direction from the 2026-09-08 call).
- Every repo's dev environment is a Nix flake on the org baseline flake (ADR-005). Org
  scripts are shell for simple checks, Python stdlib where structured data is merged.
- `auto-merge` on a `staging → main` PR means "merge when green", applied by a human
  after review, never by an agent.
- The org is being renamed and consolidated (~34 repos → ~8 monorepos): nothing in the
  mechanism may carry the company name; `claude/repos.txt` will churn.
- GitHub Free blocks rulesets, environment reviewers, required code-owner review and
  push protection on private repos. No paid plan; ADR-007 substitutes all four.
- The Jira interface is the org `jira` CLI in this repo, snapshot-first; the MCP is the
  fallback (ADR-008).

---

## 1. ADR everywhere: enforce `docs/adr/` in every repo and consolidate into the docs repo

**Problem.** ADR discipline exists in `deployments` (100+ ADRs) and here, and nowhere
else. Decisions made in application repos are invisible to the fleet handbook.

- [x] Presence: `org-check` reports a missing `docs/adr/`; `org-bootstrap.yml` seeds the
      template and index.
- [x] Enforcement: the `pr` guardrail requires an ADR when `org.toml [adr].required_paths`
      are touched, waivable with the `adr:not-needed` label recorded in the run summary.
- [x] Consolidation: `org-adr-consolidate.yml` + `claude/adr_consolidate.py` gather every
      repo's `docs/adr/` into a namespaced tree with one index, for the docs repo to call.
- [ ] Front-matter lint on touched ADRs (Date / Status / Ticket present, `Superseded by`
      resolves).
- [ ] The docs repo actually calling the consolidator (docs-side wiring).
- ~~AI classifier deciding whether a diff needs an ADR~~ → rejected, see below.

**Open.** Whether the docs repo becomes the only read side and `deployments`' handbook
projection a consumer of it. Numbering stays per-repo, namespaced.

---

## 2. Org agent-config distribution as a managed block

**Problem.** ADR-001 puts the org agent baseline in `claude/`, but nothing shipped it.

- [x] `<!-- BEGIN ORG BASELINE -->` … `<!-- END ORG BASELINE -->` block inserted at the
      top of each repo's `CLAUDE.md`, version-stamped only when content changes
      (`claude/sync.py`, ADR-004).
- [x] `settings.json` and `.mcp.json` merged by key; `org-*` skills, agents and hooks
      replaced wholesale, repo-owned entries untouched.
- [x] `sync-agent-config.yml`: one PR per repo in `claude/repos.txt` on change; drift
      check in the weekly scorecard.
- [ ] `ORG_SYNC_TOKEN` set and the first real sync PR merged **(Actions)**.
- [ ] Trim the duplicated org rules out of `deployments/.claude/CLAUDE.md` after its sync
      PR merges.

---

## 3. Verification-gate contract (`org.toml` + reusable gate runner)

**Problem.** R1/R2/R15: each repo must declare its gate and branch classes; the pipeline
must run it without knowing the stack.

- [x] `org.toml` (`claude/orgfile.py`: defaults, validation, `--example`) with
      `gate.fast` / `gate.full` / `uses_nix`, branches, deploy targets, ADR paths, secrets
      paths, generated files, PR limits, codeowners, labels, jira.
- [x] `org-gate.yml` runs the tier for the target branch on the declared runner labels.
- [ ] Self-hosted tailnet runners for gates that need fleet network access.

---

## 4. Provenance labeling and author-class policy

**Problem.** R4/R5: PRs from humans, Claude Code sessions, the Lovable bot and agents all
looked the same.

- [x] `hooks/provenance.py` reads trailers and author, applies `author:*` labels, and the
      `provenance` guardrail job records the class in the run summary.
- [x] Agent-class PRs cannot be auto-approved: `verify_pr.py` requires a non-author
      approval before release and production deploy (ADR-007).
- [ ] Exclude Bot accounts from counting as the human approval (prerequisite for idea 31).

---

## 5. AI first-pass review with the org rubric

**Problem.** R6/R7: hallucinated dependencies, thin error handling, "looks right"
correctness gaps and security issues should be caught before a human looks.

- [x] Rubric and read-only reviewer as the `org-reviewer` subagent (ADR-006), usable from
      any session.
- [ ] Run it as a PR check with comment-only write access, findings tracked to resolution
      — deferred in ADR-003 on the API-key budget question; now the first consumer of the
      agent endpoint (idea 31).
- [ ] Skip docs-only diffs; do not re-review its own comments.

---

## 6. Guardrail hooks run in CI from the same scripts as local hooks

**Problem.** R8: rules that lived as prose in `CLAUDE.md` were enforced by an agent
remembering them.

- [x] `claude/hooks/*.py` (secrets, commits, provenance, PR hygiene, actions pinned, new
      deps, CODEOWNERS) run in `org-guardrails.yml` and ship to `.claude/hooks/org/`.
- [x] Client git hooks (`pre-commit` secrets scan, `commit-msg` ticket prefix, `pre-push`
      deploy-branch refusal + fast gate) activated by the dev shell (ADR-007).
- [x] Generated-file rule (`org.toml [generated]`): lockfiles change only with inputs.
- ~~Wire the checks as Claude Code `PreToolUse` hooks by default~~ → rejected, see below.

---

## 7. Jira linkage: prefix validation, auto-transition, stale-ticket sweeper

**Problem.** The Jira workflow is mandatory but was enforced by convention; tickets rot
In Progress after the work shipped.

- [x] Commit-prefix validation: the `commits` guardrail and the `commit-msg` hook.
- [x] Stale-ticket data: idea 28 flags `stale`, `ticket-done`, `unassigned` per branch.
- [x] Token-cheap Jira from every repo: the org `jira` CLI (ADR-008).
- [ ] On PR open, comment the PR link on the ticket (via idea 30's router).
- ~~Auto-transition tickets on PR events~~ → rejected, see below.

---

## 8. Post-deploy health verification that proposes a rollback

**Problem.** R10: rollback depended on someone noticing.

- [x] `org-deploy.yml`: `health` job polls the declared URL or command; on failure for a
      production-class environment, a rollback PR to the previous release tag is opened,
      never merged.
- [x] Tag-based `rollback.yml` for the manual path.
- [ ] Grafana alert state as an alternative health signal.

---

## 9. Close the factory loop: route CI failures back to the authoring agent

**Problem.** R11: when CI fails on an agent-authored PR, a human copies logs into a new
session.

- [ ] On a failed check for an `author:claude-code` PR, post a structured failure comment
      (failing check, first error, log link).
- [ ] Hand the failure to an agent that pushes a fix, with a retry cap — the agent
      endpoint (idea 31) is the only realistic way to "wake" one from a workflow.
- [ ] Loop guard between the reviewer (idea 5) and the fixer.

---

## 10. Org conformance scorecard

**Problem.** Ideas 1, 2, 3 and 6 each add a per-repo requirement with no single view.

- [x] `claude/org-check.sh` (flake, baseline block, settings, ADR folder, staging, `.env`
      ignored) and the `org-conformance` subagent.
- [x] `org-scorecard.yml` weekly: org-check + sync check + `org.toml` validation per repo
      → `SCORECARD.md` by PR here.
- [ ] Extra checks: ADR index parseable, baseline at the claimed tag, CODEOWNERS present.
- ~~One issue per gap in every repo~~ → rejected as noise; the scorecard PR is the digest.

---

## 11. Contract drift check for `backend-api-v2`

**Problem.** The repo's README says "contracts are the boundary" and "CI enforces" it;
no CI exists.

- [ ] Lint `contracts/*.yaml` (OpenAPI, AsyncAPI) on PR; diff against the last release
      tag → `contract:breaking` / `contract:additive` label.
- [ ] Import-boundary check.
- [ ] On tag, dispatch consumers to regenerate clients (via idea 30).

---

## 12. Flake input and dependency freshness PRs with the gate attached

**Problem.** `nix flake update` and lockfile bumps happen by hand and get bundled into
unrelated commits, which the generated-file rule now rejects.

- [ ] Scheduled workflow: one PR per input with the fast gate attached.
- [ ] Decide whether Renovate/Dependabot covers the non-Nix repos and only the flake side
      needs custom work.

---

## 13. Harness evals: test changes to `claude/` before they ship

**Problem.** A change to the org `CLAUDE.md` or a hook can silently make every agent in
the org worse.

- [x] Unit tests for every hook, the sync, `verify_pr.py`, the git hooks and the Jira CLI
      (51 tests, `_claude-config-ci.yml`, `nix flake check`).
- [ ] Agent-behaviour evals (given the baseline, does an agent search Jira before
      editing?) scored by a cheap model, nightly.

---

## 14. Agent-generated release notes on merge to `main`

**Problem.** Every merge to `main` is a release and deserved a real release note.

- [x] `sync-release.yml`: deterministic tag + GitHub Release + changelog; `release_notes`
      job drafts prose with `ANTHROPIC_API_KEY` and `ORG_RELEASE_NOTES_MODEL` when set;
      `org-release-notes` subagent for sessions.
- [ ] Post the note to the Jira tickets it mentions.
- [ ] Route the drafting through the agent endpoint (idea 31) instead of a raw API key.

---

## 15. Branch protection as code (org rulesets)

**Problem.** The branch model depends on protection configured by hand per repo and
silently absent on new ones.

- [x] Rulesets as JSON (`claude/rulesets/`) applied by `org-rulesets.yml` with a diff.
- [ ] **(plan)** They return 403 on private Free repos; nothing to do until the plan
      changes or idea 29 carries them behind a flag.
- [x] Substitute (ADR-007): git hooks, `org-branch-guard.yml` detect-and-revert,
      `verify_pr.py` before release and production deploy.

---

## 16. Deploy on merge to `main`: what actually deploys, and where

**Problem.** "`main` is the deploy branch" said when, not what.

- [x] `org.toml [deploy.<env>]` (command, runner labels, GitHub Environment, health) run
      by `org-deploy.yml`; `staging` → staging environment, `main` → production.
- [x] Production deploy verified as an approved, gated merge first (ADR-007).
- [x] Runner module: `nixosModules.org-runner` (ephemeral, labelled, org tool set; ADR-011).
- [x] One-command bring-up (`claude/runner_bootstrap.py`), `auto` selection with hosted fallback (`_runner.yml`), weekly smoke canary (INFRA-290).
- [ ] Run the bootstrap for real (one PAT) **(operator, INFRA-284 step 8)**.
- [ ] Confirm GitHub Environments exist on private Free repos at all; if not, drop the
      `environment:` key (idea 29's tofu pass is the place to check).
- ~~`repository_dispatch` `release-published` between release and deploy~~ → superseded:
  the `org-release.yml` starter chains release → artifact → deploy in one run.

---

## 17. Hotfix path and back-merge

- [x] `hotfix/*` PRs into `main` run the full gate like any PR.
- [x] `sync-release.yml` opens the `main → staging` back-merge PR after every release;
      back-merge commits are exempt from the ticket-prefix rule.

---

## 18. Supply-chain checks, including slopsquatting

**Problem.** AI-generated code imports packages that do not exist or were registered
after a model hallucinated the name. For a BTC company a malicious dependency is a
wallet drain.

- [x] `supply-chain` guardrail: every new npm / PyPI / flake / Action dependency must
      exist on its registry and be older than N days; Actions pinned (`check_actions_pinned.py`).
- [ ] Nightly `npm audit` / `pip-audit` / Nix vulnerability scan on `staging`.
- [ ] Dependabot alerts on in every repo (a setting; idea 29 can declare it).

---

## 19. Secrets that should not be there

**Problem.** A leaked key in a BTC company is a loss event.

- [x] `secrets` guardrail and `pre-commit` hook: org patterns (AWS, private/age keys,
      xprv, WIF, mnemonics), SOPS metadata required under declared paths, `.env`
      blocked, allowlist for documented test vectors; gitleaks in the dev shell.
- [ ] **(plan)** GitHub push protection needs Advanced Security; the `pre-commit` hook is
      the substitute.
- [ ] Scheduled full-history scan on a tailnet runner (history should not leave the
      org's machines).
- [ ] On a hit: Slack + a Jira ticket with the rotation checklist.

---

## 20. CODEOWNERS and required reviewers for sensitive paths

- [x] `org.toml [codeowners]` → managed block in `.github/CODEOWNERS` by the sync.
- [ ] **(plan)** Required code-owner review in the ruleset.
- [x] Substitute: `verify_pr.py` refuses a release or production deploy when a touched
      owner path lacks a listed owner's approval (ADR-007).

---

## 21. PR hygiene for agent-authored PRs

- [x] `pr` guardrail: one ticket per PR, a non-empty Verification section, size ceiling
      with `needs-split`.
- [ ] Merge queue or `concurrency` group on `staging` so parallel agent sessions do not
      race each other's merges.

---

## 22. Stale branch cleanup

**Superseded by idea 28.** The sweep reports `merged` and `stale` branches per owner;
deletion stays a human action by decision (see Rejected).

---

## 23. Repo bootstrap (one-shot "make this repo conformant")

- [x] `org-bootstrap.yml`: seeds `staging`, `docs/adr/`, the agent baseline, a starter
      `flake.nix` and `org.toml`, the starter workflows, CODEOWNERS, standard labels,
      rulesets where the plan allows; idempotent.
- [ ] Run it for real on one low-risk repository **(Actions)**.
- [ ] Idea 29 takes over labels, secrets and settings; bootstrap keeps the file side.

---

## 24. CI observability and AI cost metering

- [x] `org-metrics` composite action posts one Loki line per job (event, result, repo,
      author class) when `ORG_METRICS_URL` is set.
- [ ] Token usage of every AI step (review, release notes, standup) on the same line.
- [ ] Dashboards: gate pass rate by author class, findings per PR, tokens per merged PR.
- [ ] Alert when a cheap-model step starts costing like a frontier one.

---

## 25. Nix binary cache populated from CI

- [ ] Gate runner pushes built paths to the attic cache (`deployments` ADR-019) after a
      green run; needs the cache token and a reachable endpoint.

---

## 26. Artifacts: push Nix-built OCI images and deploy them to Proxmox

- [x] `org-oci.yml` builds the declared `artifacts.oci.attr` and pushes `:<sha>` on
      staging, `:<tag>` + `:latest` on release.
- [ ] Proxmox pulling OCI images (registry connected to PVE) or keep deploying closures.
- [ ] Developers `docker run` the staging image locally.

---

## 27. Lean-and-secure comparison: Nix-built images vs distroless

- [ ] One-off, then recurring: size, package count, CVE scan, shell present, for one
      service both ways; the outcome decides the production image strategy in ADR form.

---

## 28. Branch ↔ ticket sweep: the bird's-eye view of pending work

**Problem.** Nobody could see, across twenty repositories, which branches still carry
work for which tickets, who owns them, and whether the ticket's status is honest.

- [x] `claude/jira/branch_sweep.py` (`jira sweep`): branches → Jira keys → ticket
      summary/status/assignee; per owner with ahead/behind, last-commit age, open PR and
      flags `merged` / `ticket-done` / `stale` / `no-ticket` / `unassigned` (ADR-008).
- [x] `org-branch-sweep.yml` weekly + dispatch, refreshes the pinned `branch-sweep` issue.
- [x] `org-branch-sweep` subagent: "what is pending for X", "what can be cleaned up",
      "are the tickets honest" → a `jira apply` plan.
- [x] Verified live against the four in-scope repositories (GitHub side).
- [ ] `JIRA_EMAIL` / `JIRA_API_TOKEN` secrets and the first run with ticket data **(Actions)**.
- ~~Auto-delete `merged` branches after a grace period~~ → rejected, see below.

---

## 29. Declarative GitHub organization configuration via OpenTofu

**Problem.** Labels, secrets, variables, webhooks, teams, repository settings and
rulesets are set by hand or by ad-hoc scripts (`apply_rulesets.py`, the label step in
bootstrap). There is no plan/diff, no state, and no drift detection for the GitHub side
of the org.

**Sketch.** A `platform.github` stack in `deployments` (the Terranix → OpenTofu pipeline,
S3 state and SOPS credentials already exist there; `fleet deploy tf` wraps it) using the
`integrations/github` provider. The manifest is a Nix attribute set of repositories; from
it the stack declares:

- repository settings (default branch, delete-branch-on-merge, merge methods,
  Dependabot alerts, secret scanning where the plan allows);
- the standard label set (`author:*`, `needs-adr`, `adr:not-needed`, `needs-split`,
  `rollback`, `policy-violation`, `branch-sweep`, `automated`);
- Actions secrets and variables (`ORG_WORKFLOWS_ENABLED`, `JIRA_*`, `ORG_METRICS_URL`,
  tokens) sourced from SOPS/Infisical, so a new repo gets them on creation;
- teams for CODEOWNERS (`platform`, `security`, `bitcoin-ops`);
- repository and organization webhooks (→ idea 30 and idea 31 endpoints) with HMAC secrets;
- rulesets behind `count = var.rulesets_enabled ? 1 : 0` for the day the plan changes;
- `claude/repos.txt` generated from the manifest instead of hand-edited.

Safety: `import` blocks for every existing repository before the first apply,
`prevent_destroy` on repositories (the `protect = true` pattern from the fleet manifest),
the provider token from SOPS under `integrations/github/*`, and no apply from Actions —
an operator runs `fleet deploy tf apply platform.github` like any other stack.

- [x] ADR-010 and INFRA-286. Terranix module set in `nix/github/` (schema, emitter,
      manifest) rendered by `nix build .#platform-github`; `nix run .#platform-github`
      wraps OpenTofu with local/S3 state and `TF_VAR_*` credentials; `nix flake check`
      asserts it renders with the token as a variable.
- [x] Repositories from `claude/repos.txt` (adopted by `import`, `prevent_destroy`),
      default branches, labels, Actions variables (operator toggles `ignore_changes`),
      secrets (values from `TF_VAR_org_secrets`, missing → plan fails), teams + repo
      permissions, webhooks (declared, disabled), rulesets from `claude/rulesets/*.json`
      behind `github.rulesets.enable`.
- [x] `org-platform-github.yml`: plan on PRs (comment), apply on `main`, variable-selected runner (ADR-011).
- [ ] First `plan`/`apply` against the real organization with a token **(operator)**.
- [ ] Import into the deployments pipeline as a `platform.github` leaf stack; then retire
      the label step in bootstrap and `org-rulesets.yml`.
- [ ] Verify GitHub Environments on private Free repos (see idea 16).

**Open.** Whether the manifest lives in `deployments` (pipeline is there) or here (org
config is here) — start in `deployments`, move if the org consolidation makes `.github`
the natural monorepo home. Provider token: a PAT now, a GitHub App when idea 31 creates one.

---

## 30. Event ingress: Jira and GitHub webhooks into a dispatch router

**Problem.** The workflows only react to git events. Jira transitions, releases and
external systems cannot trigger anything, so the sweep issue goes stale between Mondays
and the standup is a manual command in three places (`fleet devtools standup`,
`docs jira standup`, `jira standup`).

**Sketch.** GitHub's `repository_dispatch` is the ingress; everything upstream of it is
configuration, everything downstream is code in this repo.

- Jira: an Automation rule "on transition to Done → send web request" to
  `POST /repos/<org>/.github/dispatches` with `event_type: jira-ticket-done` and the key,
  status and assignee as `client_payload`. (Plain Jira webhooks cannot set an
  `Authorization` header; Automation rules can. The rule is declared in Jira, not in
  code; document it in the ADR.)
- `org-events.yml`: `on: repository_dispatch: types: [jira-ticket-done, standup-requested,
  sweep-requested]`, routing each type to a script with the payload: `jira-ticket-done` →
  refresh the branch-sweep issue (the `ticket-done` flag appears the same day) and post
  to Slack; `standup-requested` → `jira standup` through idea 31; `sweep-requested` →
  the sweep with `publish: issue`.
- GitHub → fleet: the webhooks declared in idea 29 deliver `pull_request` and `release`
  events straight to the agent endpoint (idea 31) without an Actions hop.
- Standup stays on a **cron** (it is a weekly window); events are for reactions, not
  reports.

- [ ] `org-events.yml` router + the `jira-ticket-done` handler.
- [ ] Jira Automation rule (documented, with the PAT scoped to `dispatches` only).
- [ ] Standup on a schedule via the endpoint; retire two of the three standup copies.

**Open.** Whether a small receiver on the fleet (netgate) should front all inbound hooks
so the PAT never lives in Jira — it would also serve idea 31.

---

## 31. Agent action endpoint (OpenAPI) on the fleet

**Problem.** Every AI step in the pipeline (review, release notes, standup, sweep
interpretation) either needs a raw `ANTHROPIC_API_KEY` in Actions or a human in a
session. ADR-003 deferred "run `org-reviewer` as a CI check" on exactly this. The paper's
factory model wants the agent to be a service the pipeline calls, with the org harness
(skills, subagents, hooks) loaded.

**Sketch.** One service on the fleet (a NixOS container, declared like any other) that
runs Claude Code headless with this repo's `claude/` configuration and exposes an
OpenAPI contract:

- `POST /v1/review` `{repo, pr, sha}` → job id; the agent checks out the PR, runs
  `org-reviewer`, posts findings as a review, calls the callback.
- `POST /v1/release-notes` `{repo, range, tag}` → drafts and attaches the notes.
- `POST /v1/standup` `{window, scope}` → runs `jira standup`, posts to Slack.
- `POST /v1/sweep/interpret` `{report}` → the `org-branch-sweep` reading, as a comment
  on the pinned issue.

Design points that are not optional:

- **Identity:** a GitHub App ("<org> reviewer"), not a PAT, so its reviews are Bot
  reviews. `verify_pr.py` must exclude Bot approvals from the human-approval count first
  (idea 4).
- **Async contract:** POST returns a job id; the agent writes its result to GitHub itself
  and hits a callback. Never a synchronous review inside a job.
- **Reachability:** hosted runners cannot reach the tailnet. Either GitHub calls the
  endpoint directly through a declared webhook exposed via netgate with HMAC
  verification (preferred: no Actions minutes), or the workflows call it from a tailnet
  runner.
- **Untrusted input:** PR content is attacker-controlled. The reviewer reads, never
  executes; runs in an ephemeral container with read-only tokens except review posting.
- **Cost:** model routing per endpoint (cheap for review triage, frontier for release
  notes), token usage on the `org-metrics` line (idea 24), a per-repo monthly budget
  that degrades to "skipped, over budget" rather than failing the PR.

- [ ] ADR: identity, contract, hosting, budget.
- [ ] `verify_pr.py` Bot exclusion (small, do first).
- [ ] The container + GitHub App + `/v1/review`; wire idea 5 as the first consumer.
- [ ] Release notes and standup routed through it; retire the raw API key in Actions.

---

## 32. Verification agent and the Done-with-evidence rule

- [x] `org-verifier` subagent: reruns the gate, exercises the change from the terminal,
      drives screens through the Playwright MCP where `org.toml [verify] ui = true`,
      writes the "Verification performed" report with a verdict per claim (INFRA-282).
- [x] The `pr` guardrail rejects a template-only Verification section.
- [x] `org-jira`: To Do → In Progress → Done; Done = pushed + verified with evidence;
      the unverifiable remainder is one follow-up ticket. `commit-msg` warns on a ticket
      that is not In Progress; the sweep flags `in-progress-merged`.
- [ ] `[verify] command` as a post-deploy verification step in `org-deploy.yml`
      (replaces the health URL with a real scenario; runs through idea 31).

---

## 33. Feature-test mode and the workflow test suite

- [x] `feature-test` branch; `ORG_FEAT_TEST` confines every org-wide workflow to this
      repository at that branch (`claude/targets.py`); `main` inert while it is on (ADR-009).
- [x] `claude/tests/test_workflows.py`: guards, docs, templates, target resolution,
      exercised-or-excused, offline on every PR.
- [x] `_workflow-tests.yml`: gate, guardrails, deploy (`[deploy.test]`), sync, scorecard,
      sweep and `verify_pr` against this repo on every push to `feature-test`.
- [ ] First run in Actions with `ORG_FEAT_TEST=true` **(Actions)**.
- [ ] Exercise `org-oci` once a registry login exists; `sync-release` on the first real merge.

---

## Rejected / superseded

Recorded so they are not re-proposed; each carries the reason.

- **AI classifier deciding whether a diff needs an ADR** (idea 1). Rejected 2026-09-09:
  path rules plus a waiver label are deterministic and auditable; a classifier adds cost
  and argument. Revisit only if waivers pile up.
- **Auto-transitioning Jira tickets on PR events** (idea 7). Rejected: the baseline rule
  already requires the agent to move the ticket at task start and close it at task end
  with a comment; Done vs Review is a judgement about *who verifies*, which a webhook
  cannot make. The router (idea 30) may *comment* on tickets, never transition them.
- **One issue per gap in every repository** (idea 10). Rejected as noise; the weekly
  scorecard PR in this repo is the digest, bootstrap is the fix.
- **Auto-deleting merged or stale branches** (ideas 22, 28). Rejected: report only; a
  human runs the delete. Branch deletion on a BTC company's repos is not something a
  cron should do.
- **Claude Code `PreToolUse` hooks wired by default** (idea 6). Rejected: a broken hook
  blocks every command in every session; git hooks in the dev shell give the same
  coverage at commit/push time (ADR-007). Documented as opt-in.
- **`repository_dispatch` between release and deploy** (idea 16). Superseded by the
  starter workflow chaining release → artifact → deploy in one run.
- **Weekly org digest as part of the release-notes workflow** (idea 14). Superseded by
  the scorecard PR (idea 10) and the pinned sweep issue (idea 28).
- **Paying for GitHub Team to get rulesets / environment reviewers / push protection**
  (ideas 15, 16, 19, 20). Rejected by operator decision 2026-09-09; ADR-007 substitutes.
  The resources stay declared so an upgrade is a flag flip (idea 29).
- **Making repositories public to get free rulesets.** Rejected: infrastructure and
  secret layout of a company that moves real Bitcoin.
- **Lovable-driven repositories in the branch model.** Deferred, not rejected: out of
  scope until the org consolidation decides their future.

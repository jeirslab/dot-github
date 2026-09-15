# ADR-011: Runners — trusted org jobs on an ephemeral fleet runner selected by variable, gates on hosted runners

- **Date:** 2026-09-10
- **Status:** Proposed

---

## Context

The org's GitHub configuration (ADR-010) and its org-wide workflows (sync, scorecard,
rulesets, sweep, bootstrap, ADR consolidation) need to run from this repository. On
GitHub-hosted runners each run installs Nix and pulls OpenTofu, which works but costs a
minute per job and keeps every secret and token on machines the org does not control.
The deployments repository separately needs a runner on the tailnet so `fleet` deploys
can run from Actions at all (idea 16). Self-hosted runners are free on every plan, and
NixOS ships a `services.github-runners` module.

The alternatives the operator raised were a Python + Pulumi stack (rejected: it brings
back the tool ADR-015 removed, needs its own state backend, and does not make the
runner lighter, since the CLI and provider plugin are downloaded per run) and a
prebuilt container image with OpenTofu (usable, but the official image is Alpine-based
and GitHub's JavaScript actions fail on musl; a custom image has a bootstrap loop).

## Decision

1. **The runner label is a variable.** Trusted org-level jobs declare
   `runs-on: ${{ vars.ORG_RUNNER || 'ubuntu-latest' }}`. Unset means hosted; set to the
   fleet runner's label means on-prem. Switching is a variable flip, and hosted remains
   the fallback when the fleet is unavailable. Steps that only make sense on hosted
   runners (the Nix installer) are conditioned on `runner.environment == 'github-hosted'`.
2. **Gates stay hosted.** `org-gate`, `org-guardrails`, `_claude-config-ci` and the
   workflow test suite run untrusted branches and never read the variable. A
   self-hosted runner that executes pull-request code is remote code execution on the
   network it sits in.
3. **The runner is a NixOS module in this repository** (`nix/runner/module.nix`,
   exported as `nixosModules.org-runner`) with the policy baked in: ephemeral (one job
   per registration, then re-register), `replace = true`, fixed labels (default
   `org-fleet`), the org tool set on the job PATH (nix, opentofu, python3, git, jq, gh),
   flakes enabled, an assertion against Docker on the host, and the registration token
   as a file from the secrets store. Any fleet host imports it with a handful of lines;
   the deployments host declaration is documented in `nix/runner/README.md` and authored
   in that repository.
4. **`org-platform-github.yml` runs the ADR-010 stack from here.** Plan on PRs touching
   the manifest (posted as a PR comment), apply on push to `main` behind the kill switch,
   plan-only in feature-test mode, remote state required for apply (a local-state apply
   is refused because the runner's disk is gone after the job), token from
   `ORG_ADMIN_TOKEN`, state credentials from `ORG_TF_AWS_*` secrets.
5. **Private repositories only.** The organization runner group must not include public
   repositories; this is a GitHub setting the operator checks when registering.

## Consequences

### Positive

- The org configuration runs from the org repository, with plan/apply in the PR flow,
  on hosted runners today and on the fleet runner later with no workflow change.
- One runner serves this repository's trusted jobs and the deployments deploys; the
  module is the same for both.
- Free minutes, secrets stay on org machines when the fleet runner is selected, and no
  per-run tool installation there.

### Negative

- Two places to look when a job misbehaves: the variable decides where it ran. The job
  summary and `runner.environment` make it explicit.
- A self-hosted runner is a machine that executes what `main` says. That is why gates
  never run there and why the module refuses Docker; the remaining trust is in the
  repository's own branch protection substitutes (ADR-007).
- The fleet host itself is not declared in this repository (fleet manifest, vm id, IP);
  the operator authors it in deployments from the documented example and validates it
  there. Until then `ORG_RUNNER` stays unset and everything runs hosted.
- None of the workflows has run in Actions yet; the module was evaluated inside a NixOS
  system (two ephemeral units, labels, packages, assertions) but not deployed.

## Alternatives Considered

### Pulumi (Python) for the org configuration

**Rejected because:** see Context. ADR-015 in deployments already retired it.

### A custom runner image with the tools baked in

**Deferred:** worthwhile only if the org stays on hosted runners long-term and the
setup minute matters; the OCI pipeline (idea 26) can build it then.

### One shared runner for gates and trusted jobs

**Rejected because:** gates run untrusted code; mixing them on the fleet runner
defeats the isolation the fleet relies on.

## Implementation Notes

- Shipped: `nix/runner/{module,README}.md/.nix`, `nixosModules.org-runner`
  and the `org-runner-evals` flake check, `org-platform-github.yml`, the variable runner
  on the six org-level workflows, policy tests in `claude/tests/test_workflows.py`.
- A follow-up automated the bring-up and the selection: `claude/runner_bootstrap.py` (one
  PAT, one command: secret → host file with a free vm_id/IP and a vendored module → tf →
  nixos → DNS → wait online → `ORG_RUNNER=auto` → smoke), `_runner.yml` +
  `claude/runner_pick.py` (unset → hosted, `auto` → fleet when online else hosted, label →
  forced) feeding `runs-on: ${{ needs.runner.outputs.label }}` on every trusted job,
  `org-runner-smoke.yml` as the weekly canary, and `lib.mkRunnerHost` for fleetkit fleets.
  Decision 1's "variable flip" is therefore `auto` with a hosted fallback, so a fleet
  outage never strands a job.

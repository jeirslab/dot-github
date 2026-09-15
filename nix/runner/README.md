# `nix/runner` — the org's self-hosted GitHub Actions runner (ADR-011)

`module.nix` is a NixOS module, exported from this flake as `nixosModules.org-runner`,
that registers one or more **ephemeral** GitHub Actions runners with the org policy
baked in. The org workflows pick it with a repository variable:

| Variable | Effect |
|---|---|
| `ORG_RUNNER` unset | trusted org-level jobs run on `ubuntu-latest` (hosted); setup steps install Nix and OpenTofu |
| `ORG_RUNNER=org-fleet` | the same jobs run on the fleet runner; setup steps are skipped (`runner.environment != 'github-hosted'`) |

PR gates (`org-gate`, `org-guardrails`, `_claude-config-ci`) never read the variable:
they run untrusted branches and stay on hosted runners.

## Bring-up: one credential, one command (INFRA-290)

The only manual step GitHub leaves us is creating a credential: a fine-grained PAT on the
organization with **Self-hosted runners: write** (to register), plus **Variables: write**
and **Actions: write** on this repository (to flip the selector and dispatch the smoke).
Export it and run the bootstrap from a checkout of this repo, with deployments beside it:

```bash
export GITHUB_RUNNER_PAT=github_pat_…
nix develop ../deployments -c python3 claude/runner_bootstrap.py \
  --deployments ../deployments --owner <org> --cluster <pve cluster> --node <pve node>
```

It runs, idempotently and in order: `secret` (PAT into SOPS via `fleet devtools secrets`),
`host` (writes `nix/hosts/pve/gh-runner.nix` with a free vm_id/IP and vendors
`module.nix` into `nix/modules/org-runner/`; you commit), `tf` (`fleet deploy tf apply
platform.core --yes`), `nixos` (`fleet deploy nixos apply host gh-runner`), `dns`
(`fleet deploy nixos apply host netcore`), `wait` (polls the org runners API until the
runners are online), `select` (sets `ORG_RUNNER=auto` on this repo), `smoke` (dispatches
`org-runner-smoke.yml` and waits for green). `--dry-run` prints every command; `--skip`
and `--only` pick steps; re-running after an upstream change refreshes the vendored module.

Fleets built on fleetkit can skip the generated file and add
`(org-baseline.lib.mkRunnerHost { cluster; node; vm_id; internal_ip; url; })` to
`mkFleet`'s modules instead.

## Runner selection (`vars.ORG_RUNNER`)

| Value | Trusted org-level jobs run on |
|---|---|
| unset | `ubuntu-latest` (hosted); the Nix installer step runs |
| `auto` | the fleet label when an **online** runner carries it, else hosted with a warning — needs `ORG_ADMIN_TOKEN` or `ORG_READ_TOKEN` able to list org runners |
| any label | that label, unconditionally |

The choice is made by `_runner.yml` (`claude/runner_pick.py`) once per run, on a hosted
runner, and every trusted job uses `runs-on: ${{ needs.runner.outputs.label }}`. PR gates
(`org-gate`, `org-guardrails`, `_claude-config-ci`, the workflow tests) never read it. The
weekly `org-runner-smoke.yml` proves the selection and the tool set.

## Policy (ADR-011)

- Ephemeral only, replaced on re-registration; a job never sees a previous job's files.
- Private repositories only. Never let a public repository target this runner.
- No Docker on the host (asserted). Jobs that need containers run on hosted runners.
- Trusted jobs only: apply, deploy, sync, scorecard, sweep, bootstrap. Gates stay hosted.
- The registration token lives in the secrets store and reaches the unit as a file; with a PAT there, the unit fetches fresh registration tokens itself on every ephemeral restart.

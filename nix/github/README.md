# `nix/github` — the org's GitHub configuration as code (ADR-010)

Terranix modules that render the `platform.github` OpenTofu stack: repositories,
default branches, the standard label set, Actions variables and secrets, teams,
webhooks, and (behind a flag) rulesets, for every repository the organization manages.

| File | Role |
|---|---|
| `options.nix` | Schema (`github.*` options). Generic. |
| `emit.nix` | Terranix emitter → `integrations/github` provider resources, `import` blocks. Generic. |
| `manifest.nix` | This organization's values. The only file here that may name the company. |
| `default.nix` | Imports all three. |

## Use from this repository

```bash
nix build .#platform-github && jq '.resource | map_values(length)' result   # what would be managed
export TF_VAR_github_token=$(infisical secrets get GITHUB_TERRANIX_TOKEN --plain)    # repo admin (+ org admin for teams/webhooks)
export TF_VAR_org_secrets="$(infisical export --format json)"                       # only if github.secrets is non-empty
export ORG_TF_STATE_BUCKET=<bucket>                                                   # unset → local state under .tofu/
nix run .#platform-github -- plan
nix run .#platform-github -- apply
```

The first `apply` adopts the existing repositories through the generated `import`
blocks; nothing is created or destroyed for them, only settings converge. Repositories
carry `prevent_destroy`; `destroy` is refused by the wrapper unless `ORG_TF_ALLOW_DESTROY=1`.

## Use from the deployments Terranix pipeline

Import `./options.nix` and `./emit.nix` (and this `manifest.nix` or your own) into the
stack's module list. The provider-instance shape (`github.provider.{source,version,
secrets.token,state.prefix}`) mirrors `fleet.providers.<type>.<instance>`, so fleetkit
can supply the token from the SOPS path in `github.provider.secrets.token` and the S3
state key from `github.provider.state.prefix`; drop the wrapper's `backend.tf.json`.

## What is and is not managed

- **Repositories**: settings only (visibility, merge methods, delete-on-merge, issues,
  wiki, vulnerability alerts, archived) and the default branch. Content, branches,
  pages, topics and security-and-analysis are `ignore_changes`.
- **Labels**: created in every managed repository. If `org-bootstrap.yml` already
  created them, set `github.imports.labels = true` for the first apply.
- **Actions variables**: set everywhere; `operatorToggle = true` entries
  (`ORG_WORKFLOWS_ENABLED`, `ORG_FEAT_TEST`) are created once and then left alone.
- **Actions secrets**: names only; values from `TF_VAR_org_secrets`. A missing value
  fails the plan (precondition) rather than writing an empty secret.
- **Teams**: name, description, privacy, optional members and repository permissions.
- **Webhooks**: declared with `enable = false` until their receivers exist (ideas 30, 31).
- **Rulesets**: rendered from `claude/rulesets/*.json` only when
  `github.rulesets.enable = true`; GitHub Free returns 403 on private repositories
  (ADR-007 substitutes until then).
- **Not here**: repository creation (a new repository is created by hand or by the
  future bootstrap, then added to `claude/repos.txt`), branch creation (`create-branches.yml`),
  environments (unavailable on the plan), organization settings (later).

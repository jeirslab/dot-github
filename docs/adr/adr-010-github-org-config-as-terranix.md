# ADR-010: The organization's GitHub configuration is a Terranix stack (`platform.github`)

- **Date:** 2026-09-10
- **Status:** Proposed

---

## Context

Labels, Actions variables and secrets, teams, webhooks, repository settings and rulesets
were being set by hand or by single-purpose scripts: `apply_rulesets.py`,
the label loop in `org-bootstrap.yml`, and instructions in the README telling the
operator which variables to create. None of it had a plan, a diff, state, or drift
detection, and each new repository re-did the same clicks.

The deployments repository already provisions the fleet through Terranix → OpenTofu
with an S3 state backend, SOPS credentials and the `fleet deploy tf` wrapper (its
ADR-015). The `integrations/github` provider covers the whole GitHub surface above.
Idea 29 proposed using it; the operator asked for the configuration to live here, under
`nix/`, so the org repository stays the home of org configuration, and to be importable
into the deployments pipeline later.

## Decision

`nix/github/` is a Terranix module set with three files: a generic **schema**
(`options.nix`, the `github.*` options), a generic **emitter** (`emit.nix`, producing
`github_repository`, `github_branch_default`, `github_issue_label`,
`github_actions_variable`, `github_actions_secret`, `github_team*`,
`github_organization_webhook` / `github_repository_webhook`, `github_repository_ruleset`,
and `import` blocks), and this organization's **manifest** (`manifest.nix`, the only
file that names the company).

- **Repositories come from `claude/repos.txt`** — the sync allowlist is already the list
  of repositories the org manages, so there is one file to edit when a repository joins.
  Per-repository overrides live in `manifest.nix`; everything else takes `repoDefaults`.
- **The flake exposes it two ways**: `packages.platform-github` is the rendered
  `config.tf.json`; `apps.platform-github` wraps OpenTofu with a local or S3 backend from
  the environment, the provider token from `TF_VAR_github_token`, and secret values from
  `TF_VAR_org_secrets`. `nix flake check` asserts the stack renders and that the token
  is a variable, never a literal.
- **Safety by construction**: every repository has `prevent_destroy`; `import` blocks
  adopt existing repositories (and optionally teams and labels) on the first apply;
  operator toggles (`ORG_WORKFLOWS_ENABLED`, `ORG_FEAT_TEST`) are created once and then
  `ignore_changes` so a UI flip is not reverted by the next apply; rulesets are emitted
  only with `github.rulesets.enable = true` because the plan returns 403 (ADR-007
  substitutes); a secret without a supplied value fails the plan instead of being
  written empty; `destroy` is refused by the wrapper unless explicitly allowed.
- **Importable into deployments**: the provider-instance options mirror fleetkit's
  `fleet.providers.<type>.<instance>` shape (source, version, `secrets.token` as a
  secrets-store path, `state.prefix`). Importing means adding `options.nix` and
  `emit.nix` to a stack's module list and letting fleetkit supply backend and token.
- **Retires**: the label step in `org-bootstrap.yml` and, once the plan allows
  rulesets, `org-rulesets.yml` + `apply_rulesets.py`. Both stay until the stack has been
  applied for real; the ruleset JSON files remain the single source for both paths.

## Consequences

### Positive

- One plan/diff for the GitHub side of the org, with state and drift detection, and a
  new repository gets labels, variables, secrets and settings from one line in
  `repos.txt` plus an apply.
- The manifest is the readable answer to "what does the org expect every repository to
  have", which the scorecard and bootstrap were approximating.
- The mechanism is company-agnostic and lives next to the workflows that consume the
  variables it creates, so a rename or a consolidation is a manifest edit.

### Negative

- The provider token is powerful (repository admin, organization admin for teams and
  webhooks). It lives in the secrets store under `github.provider.secrets.token` and
  reaches OpenTofu only as an environment variable; the wrapper never writes it to disk.
  A GitHub App installation token would be narrower and is the follow-up once idea 31
  creates an App.
- Terraform state contains secret values in plaintext (Actions secrets, webhook HMACs).
  The S3 bucket is encrypted and access-controlled like the fleet's; local state under
  `.tofu/` is git-ignored. A secret-free plan is possible by leaving `github.secrets`
  empty and keeping secrets in the UI or Infisical sync.
- The stack has not run against the real organization from this environment. It was
  rendered with Nix and passed `tofu validate` against `integrations/github` v6.13.0
  with every gated path enabled (rulesets, webhooks, team membership, label and team
  imports), so the resource shapes are right; what a real `plan` adds is the API's view
  (existing settings, permissions of the token). That first plan is operator work.
- Two sources of truth for labels and rulesets until the scripts are retired.

## Alternatives Considered

### Keep the scripts (`apply_rulesets.py`, bootstrap labels) and add more

**Rejected because:** each script re-implements diff/apply for one resource with no
state; the provider already does that for all of them.

### Put the stack only in deployments

**Rejected because:** the org repository is where the variables, labels and rulesets are
defined and consumed; deployments imports the modules rather than owning them. The
operator's direction.

### Manage repository creation here too

**Deferred:** creating repositories from a plan is easy but couples the sync allowlist to
resource creation in a way that could create a repository from a typo. Adopt-existing
only, for now.

## Implementation Notes

- Shipped: `nix/github/{options,emit,manifest,default}.nix`,
  `nix/github/README.md`, the `terranix` flake input, `packages.platform-github`,
  `apps.platform-github`, `checks.platform-github-renders`, OpenTofu and the wrapper in
  the dev shell, `.tofu/` ignored.
- First real use: a token in the secrets store, `nix run .#platform-github -- plan`
  with local state, read the plan (expect: adopt N repositories, create labels and
  variables), then `apply`; then set `ORG_TF_STATE_BUCKET` and migrate state.
- Deployments import: a `platform.github` leaf stack whose modules are
  `../../../.github/nix/github/{options,emit}.nix` (or a flake input on this repo) plus
  a manifest; map `github.provider.secrets.token` to the SOPS entry.

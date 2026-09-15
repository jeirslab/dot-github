# ADR-013: The stack can create repositories (and generate them from templates), registry-driven

- **Date:** 2026-09-11
- **Status:** Proposed
- **Amends:** ADR-010 (GitHub org config as terranix); builds on ADR-012 (`repos.toml` fleet registry)

---

## Context

The `platform.github` stack (ADR-010) was **adopt-only**: `imports.repos = true` emits a
Terraform `import` block for *every* managed repository, and importing a repository that
does not exist fails the plan. So a repo had to be created out-of-band (by
`org-bootstrap.yml`, which does the `gh`-level creation and seeds `staging`) before the
stack could manage its settings.

`github_repository` is a full CRUD resource, though — it can create repositories and even
generate them from a template repo. Making that registry-driven turns the fleet registry
(`repos.toml`, ADR-012) into a genuine "repositories as code" control plane: add an entry,
open one PR, and the repository is created — optionally stamped from a template — and born
conformant (labels, rulesets, branch protection, team access), with `prevent_destroy`
keeping it un-deletable by the stack.

Validated in the `jeirslab` sandbox (throwaway org, full admin): the stack adopted three
existing repos and then **created** `repo-template` (a template repo) and `demo-svc`
*generated from it* — all with `0 destroyed` throughout.

## Decision

Add four per-repository fields (schema in `options.nix`, consumed by `emit.nix`):

| Field | Meaning |
|---|---|
| `create` | `true` = the stack creates the repo (no `import` block emitted); `false` = adopt an existing repo via import (the default, unchanged behaviour). |
| `template` | `"owner/repo"` to generate the new repo from at creation (`create = true` only). |
| `isTemplate` | mark this repo as a template others can be generated from. |
| `autoInit` | initialize with a README on creation (gives a blank created repo a default branch). |

Emitter changes:

- **Conditional import.** The `import` block is emitted only for managed repos where
  `create = false`. Created repos get no import (which would otherwise fail the plan),
  so `tofu apply` creates them; adopted repos are imported exactly as before.
- **Repo resource** gains `is_template`, an optional `template { owner, repository }`
  block (parsed from the `"owner/repo"` string), and optional `auto_init`. `template` and
  `auto_init` stay in `ignore_changes` — they only apply at creation.
- **`prevent_destroy` is unchanged** and still applies to created repos: the stack can
  make a repo but can never delete one; removing it from the registry errors the plan.

Also fixes a latent bug the sandbox apply surfaced: **GitHub rejects empty Actions
variable values (422)**, but the schema allowed `value = ""`. `emit.nix` now filters out
empty-valued variables.

### Registry usage (ADR-012 `repos.toml`)

```toml
[repos."repo-template"]
create = true
is_template = true          # → isTemplate
auto_init = true            # → autoInit

[repos."new-service"]
create = true
template = "jeirslab/repo-template"
```

Ordering: a template repo must exist before repos are generated from it. Within a single
apply the provider has no dependency edge between two `github_repository` resources, so
introduce the template in one PR/apply and the generated repos in a later one (as the
sandbox did). A future refinement could add an explicit `depends_on`.

## Consequences

**Positive**
- Registry-driven repo creation: one `repos.toml` entry + one PR = a new repo, conformant
  from birth (labels, rulesets, branch protection, team). Templates give every new repo a
  consistent starting shape.
- Org-agnostic: identical in `jeirslab` (where this was proven) and downstream consumers.
- No safety regression: `prevent_destroy` still guards every repo; adoption is still the
  default.

**Negative / risks**
- Template-before-generated ordering is manual (two applies) until a `depends_on` is added.
- `create = true` is a sharper tool than adoption; keep it behind the same reviewed-PR +
  plan-first flow as everything else, and remember the stack still cannot delete what it
  creates.

**Follow-ups**
- Optional `depends_on` so a template and its generated repos can land in one apply.
- Investigate a small non-idempotency on the repo resource (a re-apply reports "N changed"
  with no manifest change) — identify the drifting attribute and add it to `ignore_changes`
  or set it explicitly.

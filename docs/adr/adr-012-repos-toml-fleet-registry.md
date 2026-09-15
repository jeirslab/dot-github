# ADR-012: The org fleet is one `repos.toml`, read by both the workflow selector and the terranix stack

- **Date:** 2026-09-11
- **Status:** Proposed
- **Amends:** ADR-010 (GitHub org config as terranix); relates ADR-003 (v2 pipeline), ADR-004 (agent-config distribution), ADR-009 (feature-test mode)

---

## Context

Which repositories the org manages, and how it treats each one, is described by **two**
central artifacts today:

1. **`claude/repos.txt`** — a flat `owner/repo`-per-line allowlist, parsed by
   `claude/targets.py` (the single selector every org-wide workflow routes through) and
   read directly by a few scripts (`apply_rulesets.py`, `jira/branch_sweep.py`,
   `adr_consolidate.py`).
2. **`nix/github/manifest.nix`** — `github.repos.<name>`, a *typed* per-repository
   override map (visibility, default branch, `rulesets`, `manage`, per-repo
   variables/secrets, …) that the terranix `platform.github` stack applies (ADR-010).
   `manifest.nix` already defers to `repos.txt` for membership (`reposFile`) and layers
   the typed knobs on top.

Two problems follow:

- **They drift.** After the org consolidation (ADR-099) both still name the
  pre-consolidation world — `deployments@nightly`, `backend-api-v2`, `etl-pipeline` —
  none of which match the current 10-repo fleet.
- **The split is forced by format, not by design.** The Python workflow layer wants a
  trivial, dependency-free list; the Nix/terranix layer wants typed structure. `.txt` is
  too weak for Nix (hence the separate `manifest.nix` block); Nix is unreadable by
  `targets.py`. So the same fact — the fleet — lives in two places that must be edited in
  lockstep and aren't.

There is also a **capability gap**: rollout is a single global switch
(`ORG_WORKFLOWS_ENABLED` + `ORG_FEAT_TEST`, ADR-009) — all-or-nothing. There is no way to
say "governance is live for `infrastructure`, still feature-test for the rest."

## Decision

Introduce **`claude/repos.toml`** as the single source of truth for the fleet, read
natively by both layers:

- **Python** parses it with `tomllib` (3.11+ stdlib — no dependency).
- **Nix** parses it with `builtins.fromTOML`.

TOML is the only format both consume without a new dependency or a code generator, so one
file replaces the two drifting ones.

### Scope line (do not re-centralize per-repo build config)

`repos.toml` holds the **org's view of each repo**: membership + org-level flags
(`enabled`, `stage`, `tier`, `rulesets`, `manage`, `default_branch`, `visibility`,
`team`, `description`). It **must not** absorb a repo's own build/gate/deploy config — the
`[gate]` command, deploy target, ADR/secret/CODEOWNERS paths stay in each repository's
root `org.toml` (ADR-003), for the locality, ownership and blast-radius reasons that
motivated per-repo `org.toml` in the first place. The division is:

| Concern | Home |
|---|---|
| Which repos exist + how the org treats them | `repos.toml` (central) |
| Org-wide rules (hooks, guardrails, rulesets) | `claude/hooks/*`, `claude/rulesets/*` (central) |
| How a repo builds / gates / deploys itself | that repo's `org.toml` (local) |

### Schema

Field names are `snake_case`; the Nix emitter maps them to its `camelCase` options
(`default_branch` → `defaultBranch`). A `[defaults]` table mirrors `github.repoDefaults`;
every `[repos."<name>"]` field is optional and falls back to it. New fields beyond what
`manifest.nix` already had: `enabled` and `stage` (rollout), `tier` and `team`
(organizing/ownership). Seeded with the current 10-repo fleet — see `claude/repos.toml`.

### `targets.py` (sketch)

`resolve()` keeps its signature and its flat `repos` list output (so every current caller
and the confinement test are unchanged), and gains: prefer `repos.toml` when present,
honour `enabled`/`stage` for rollout, and expose the per-repo table.

```python
def load_registry(path: Path) -> dict:
    """repos.toml → {name: {...}} keyed by short repo name. {} if absent."""
    import tomllib
    if not path or not path.is_file():
        return {}
    data = tomllib.loads(path.read_text())
    defaults = data.get("defaults", {})
    return {name: {**defaults, **attrs} for name, attrs in data.get("repos", {}).items()}

def resolve(owner, repos_file, override, env=None, registry_file=None):
    ...
    if not repos and registry_file:                     # prefer repos.toml
        reg = load_registry(registry_file)
        repos = [f"{owner}/{n}" for n, a in reg.items()
                 if a.get("enabled", True)
                 and (not feat or a.get("stage") == "feature-test")]
    if not repos and repos_file:                        # legacy fallback
        repos = read_repos_file(repos_file)
    ...
```

Callers pass `--registry-file claude/repos.toml`; the existing `--repos-file
claude/repos.txt` remains as fallback until retired.

### `nix/github` (sketch)

Add a `reposToml` option; `emit.nix` builds both the name list and the per-repo settings
from one `fromTOML`, replacing `reposFile` + the hand-written `repos` block:

```nix
# options.nix
reposToml = mkOption { type = types.nullOr types.path; default = null; };

# emit.nix
reg       = if cfg.reposToml == null then {} else builtins.fromTOML (builtins.readFile cfg.reposToml);
defaults  = reg.defaults or {};
tomlRepos = lib.mapAttrs (_: a: mapKeys (defaults // a)) (reg.repos or {});   # snake→camel
# tomlRepos then merges with cfg.repos exactly as fileRepos did, over repoDefaults.
```

`manifest.nix` switches `reposFile = ../../claude/repos.txt;` → `reposToml =
../../claude/repos.toml;` and drops its now-redundant `repos` overrides (they move into
the TOML).

### Migration / deprecation

1. This PR: add `repos.toml` (seeded + correct) + this ADR. `repos.txt` stays as the live
   fallback so nothing breaks.
2. Follow-up (same ticket): wire `targets.py` (+ `--registry-file`) and `nix/github`;
   migrate the direct readers (`apply_rulesets.py`, `branch_sweep.py`,
   `adr_consolidate.py`) to `targets.py` / `fromTOML`; update `test_workflows.py`
   (`test_org_wide_workflows_resolve_targets_through_targets_py` learns about
   `repos.toml`).
3. Retire `repos.txt` once no consumer reads it. Until then, if both exist, `repos.toml`
   wins and CI asserts they agree on membership (a one-line drift test).

## Consequences

**Positive**
- One fleet source; the two-registry drift class (which caused the stale entries) is gone.
- Per-repo, graduated rollout (`enabled`/`stage`) — the granular control the global kill
  switch cannot express — without weakening ADR-009's confinement.
- Still typed on the Nix side: `options.nix` validates the `fromTOML` data and the emitter
  is unchanged in shape; the module stays importable into the deployments Terranix pipeline.
- Central selection/policy preserved; per-repo build config stays local (no re-centralizing).

**Negative / risks**
- A transition window with both files; mitigated by "toml wins + drift test" and a bounded
  consumer-migration list.
- `targets.py` gains a `tomllib` dependency (stdlib on 3.11+; the dev shell and CI runners
  are 3.13 — verified).
- One more schema to keep in step with `options.nix`; mitigated by the emitter validating
  it and by keeping the field set small and org-level only.

**Rejected alternatives**
- *Keep two files.* The drift is structural, not accidental; it will recur.
- *One central `org.toml` map in `.github`.* Folds per-repo build config into the org
  plane — the bottleneck/blast-radius this ADR and ADR-003 deliberately avoid.
- *Make `manifest.nix` the sole source.* Nix is unreadable by the Python selector, so the
  workflow layer would still need a derived list — back to two artifacts.

# ADR-005: Every repository's developer environment is a Nix flake built on the org baseline

- **Date:** 2026-09-09
- **Status:** Proposed

---

## Context

jeirslab's repositories already lean on Nix: `deployments` is a flake with a `nix develop`
shell and a `ci` profile, `backend-api-v2` and `etl-pipeline` each ship a `flake.nix`, and
the fleet builds from flakes. What is missing is the *organization* layer: nothing says a
repository must have a flake, nothing gives every developer the same baseline tools, and
the org-level scripts introduced under ADR-004 (`claude/sync.py`) and the conformance
check need a home a developer can call from any repository without installing anything.

Two concrete needs drove this:

- **Secrets.** Developers are moving to Infisical for secrets management (an Infisical
  instance runs in the fleet; `deployments` already has an `infisical-render` app). Every
  developer environment needs the Infisical CLI, and the org rule "secrets come from
  Infisical, not files" only holds if the CLI is always present.
- **Dependencies.** The operator's preference is fewer moving parts. The agent-config sync
  was written in Python (standard library only) and the conformance check in shell. Neither
  should require a developer to install an interpreter by hand; the flake provides them.

## Decision

1. **Every jeirslab repository has a `flake.nix` at its root that provides its developer
   environment.** `nix develop` is the way into a repository. The conformance check
   (`org-check`) reports a missing flake and the future conformance workflow (ideas.md
   10, 23) enforces it.

2. **`jeirslab/.github` ships the org baseline flake.** It exposes:
   - `lib.orgPackages pkgs` — the tools every developer environment carries: Infisical
     CLI, sops, age, jq, git, just, python3, gitleaks. Short and universal by design;
     stack-specific tools stay in the repository's own flake.
   - `lib.mkDevShell { pkgs, extraPackages, shellHook, name }` — the constructor
     consuming flakes use so the baseline and the repo's additions form one shell.
   - `packages`/`apps` `org-check` and `sync-agent-config`, so
     `nix run github:jeirslab/.github#org-check` works from any directory.
   - `checks` that run the sync unit tests and the conformance script, so
     `nix flake check` is this repository's own verification gate (ADR-002 R2).

3. **Consuming repositories reference the org flake as an input** and build their
   default shell with `mkDevShell`. They pin it through `flake.lock` like any input and
   update deliberately with `nix flake update jeirslab-org`.

4. **Infisical is the developer secrets path.** The baseline `CLAUDE.md` says so; the
   shell hook reminds on entry; `.env` files are git-ignored conveniences and never an
   input agents or CI read. SOPS stays for the fleet's declarative secrets (`deployments`),
   which is why sops and age remain in the baseline.

5. **Language of org scripts.** Simple checks are shell (`org-check.sh`). Logic that
   manipulates structured data (the JSON merges in the agent-config sync) stays Python
   standard library, because a shell port would need `jq` plus fragile text handling for
   the same result. Both interpreters come from the flake, so the dependency count a
   developer experiences is the same: one, Nix.

## Consequences

### Positive

- One command to enter any repository, with the same baseline tools everywhere.
- Org scripts are runnable from anywhere with a flake reference; no per-repo install.
- Adding a tool to every developer's environment is a one-line PR here.
- `nix flake check` gives this repository a real gate.

### Negative

- Nix becomes a hard prerequisite for contributing to any repository. It already is for
  the fleet; this extends it to application repositories.
- The org flake pins `nixpkgs` (`nixos-unstable`, matching `deployments` and
  `etl-pipeline`); a consuming repository on a different channel (`backend-api-v2` is on
  `nixos-25.05`) gets two nixpkgs evaluations unless it sets `inputs.org-baseline.inputs.nixpkgs.follows`.
- The flake was authored without a Nix installation at hand. `nix flake check` and the
  initial `flake.lock` must be produced by the person merging this (see Implementation
  Notes).

## Alternatives Considered

### A shared `shell.nix`/`devshell` module copied into each repository by the sync

**Rejected because:** copies drift; a flake input is versioned and updated with intent.

### Rewrite the agent-config sync in shell to avoid Python

**Rejected for now:** the block handling ports cleanly, the JSON merges do not without
`jq`, and `jq` is one more dependency than `python3` on the same machines. With the flake
providing the interpreter the difference disappears. Revisit if Python ever becomes a
problem in practice.

### Leave secrets tooling to each repository

**Rejected because:** "use Infisical" is an org rule, and rules that depend on a tool that
may not be installed are not rules.

## Implementation Notes

- Shipped: `flake.nix`, `claude/org-check.sh`, the developer-environment
  section in `claude/CLAUDE.md`, a `nix flake check` job in `_claude-config-ci.yml`.
- **Unverified at authoring time:** no Nix binary was available in the session. Before
  merging, run `nix flake lock` (commit `flake.lock`), `nix flake check`, and
  `nix run .#org-check`. The CI job will do the same on the PR.
- Consuming-repo snippet:

  ```nix
  inputs.org-baseline.url = "github:<org>/.github";
  inputs.org-baseline.inputs.nixpkgs.follows = "nixpkgs";

  outputs = { self, nixpkgs, org-baseline, ... }:
    let pkgs = nixpkgs.legacyPackages.x86_64-linux; in {
      devShells.x86_64-linux.default = org-baseline.lib.mkDevShell {
        inherit pkgs;
        extraPackages = [ pkgs.nodejs_22 ];
      };
    };
  ```
- Follow-ups: migrate `deployments`, `backend-api-v2`, `etl-pipeline` shells onto
  `mkDevShell` (one ticket each); the conformance workflow (ideas.md 10) that runs
  `org-check` across the allowlist and opens issues.

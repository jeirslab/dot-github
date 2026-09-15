---
name: org-dev-env
description: Entering and using a repository's developer environment in this organization — the Nix flake dev shell built on the org baseline, Infisical for secrets, the org-check conformance script, and the agent-config sync. Use when setting up, when a tool is missing, or when secrets are needed.
---

# Developer environment

## Enter through the flake

Every repository provides its developer environment as a Nix flake. Enter it before
running anything:

```bash
nix develop            # the repo's shell: org baseline tools + repo-specific tools
```

Repository flakes build on the org baseline (`github:jeirslab/.github`,
`lib.mkDevShell`), so these are always present: Infisical CLI, sops, age, jq, git, just,
python3, gitleaks. If a tool you need is missing, add it to the repository's flake
(`extraPackages`), not to your machine.

If a repository has no `flake.nix`, that is a conformance gap: start from
`lib.mkDevShell` (see the org `.github` README → Nix) and open a PR.

## Secrets: Infisical, never files

```bash
infisical login                      # once per machine
infisical run -- <command>           # injects the project's secrets as env vars
```

- Do not create `.env` files for an agent or a tool to read; do not paste secret values
  into files, prompts, commits, or tickets. `.env` is git-ignored as a last line of
  defence, not a workflow.
- Fleet declarative secrets in `deployments` remain SOPS-managed (age key derived from the
  sysadmin SSH key); only encrypted files are committed.

## Conformance and the agent baseline

```bash
nix run github:jeirslab/.github#org-check                    # what is this repo missing?
nix run github:jeirslab/.github#sync-agent-config -- check --target .
nix run github:jeirslab/.github#sync-agent-config -- apply --target . --version manual
```

`org-check` reports: `flake.nix`, the org baseline block in `CLAUDE.md`,
`.claude/settings.json`, `docs/adr/`, the `staging` branch, `.env` ignored. The sync
inserts or refreshes the baseline block, settings, MCP servers, org skills and org
subagents; everything outside the org-owned parts is left alone.

## MCP servers: only what the repo needs

The org baseline `.mcp.json` carries only servers that work everywhere. Repositories add
their own (fleet CLI, Grafana, Tempo) in their `.mcp.json`; nothing else is loaded. Keep
that list to what the repository's work actually needs — every server's tool definitions
cost context on every turn.

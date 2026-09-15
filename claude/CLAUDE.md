<!-- BEGIN ORG BASELINE -->
<!-- Managed by the organization's .github repository (claude/CLAUDE.md, ADR-001). Version: {{ORG_BASELINE_VERSION}}
     Do not edit between the BEGIN/END markers: the org sync workflow overwrites this block
     whenever the upstream template changes. Repository-specific rules go BELOW the END marker. -->

# Organization baseline for coding agents

Hard rules only (ADR-006). Procedures are **skills** in `.claude/skills/org-*` and
specialised work is done by **subagents** in `.claude/agents/org-*`; load them when the
task calls for it, never all at once. Repository-specific rules follow below the END
marker and take precedence where they are more specific.

## Non-negotiables

- This company handles real Bitcoin. Prefer the safer option; never mix mainnet and
  testnet in code, config, or fixtures; no ad-hoc fixes on live systems, diagnosis only.
- Every code change is tied to a Jira ticket **before** the first edit, every commit is
  prefixed `<PROJECT>-NNN:`, and the ticket's status matches reality when you stop.
  Procedure: skill `org-jira`.
- This checkout is shared with other agent sessions: stage files explicitly
  (`git add <path>`), never `git add -A`/`-u` or `commit -a`, leave unrelated changes alone.
- Never commit secrets, `.env` files, private or age keys, or unencrypted SOPS files.
  Secrets come from Infisical. Procedure: skill `org-dev-env`.
- `main`/`master` is the deploy branch and is PR-only; work lands on `staging`.
  Procedure: skill `org-branching`.
- Significant decisions get an ADR in `docs/adr/`. Procedure: skill `org-adr`.
- Enter a repository through its Nix flake (`nix develop`) and validate with its own
  checks before pushing.
- Do not write model names or identifiers into commits, PRs, or code comments.

## On demand

| When you need to… | Load |
|---|---|
| Find, create, transition or close a Jira ticket; decide Done vs Review | skill `org-jira` |
| Write or supersede an ADR | skill `org-adr` |
| Set up or use the dev environment, Infisical secrets, `org-check` | skill `org-dev-env` |
| Understand the branch model, releases, promotion, the kill switch | skill `org-branching` |
| Review a diff before it goes to `main` | subagent `org-reviewer` |
| Know what a repository is missing against the org standard | subagent `org-conformance` |
| Draft release notes for a merge to `main` | subagent `org-release-notes` |
| Prove a change works before closing a ticket or opening a PR into `main` | subagent `org-verifier` |
| See what work is pending merge, per person, across repositories | subagent `org-branch-sweep` |
<!-- END ORG BASELINE -->

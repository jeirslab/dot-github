# ADR-008: The org Jira interface lives in `.github` — snapshot-first CLI and branch ↔ ticket sweep

- **Date:** 2026-09-09
- **Status:** Proposed

---

## Context

Every code change in the organization is tied to a Jira ticket (baseline rule; skill
`org-jira`). Agents therefore touch Jira in every session: find or create the ticket,
move it to In Progress, comment and close it. Done through the interactive Atlassian MCP,
each of those is a network round-trip that returns kilobytes of boilerplate per issue,
and a session that manages a ticket honestly spends a noticeable share of its context on
it.

An earlier effort solved this once, in the docs repository: `docs jira` syncs a trimmed JSON
snapshot of every visible project (reads are then local greps), batches writes into a
single validated YAML change-set, and can draft an activity summary. It works, but it
lives in a submodule of one repository, depends on click and pydantic, reads a
`docs-cli.toml` that names the company, and is invisible to an agent working in any
other checkout. The org-level rule needs an org-level tool.

Separately, the operator asked for a bird's-eye view of pending work: which branches
across the organization carry which tickets, who owns them, and what is still waiting to
merge. That needs the same Jira correlation, plus a token that can read every repository.

The organization will be renamed; nothing in the mechanism may carry the current name
(a standing constraint).

## Decision

### The Jira CLI is org tooling, in this repository, standard library only

`claude/jira/` holds the port: `jira_cli.py` (entry point), `jira_export.py` (snapshot
exporter), `jira_models.py` (indexed catalog over the snapshot), `jira_client.py`
(REST transport + validated writer), `jira_apply.py` (batch change-sets),
`jira_standup.py` (activity summary through `claude -p`), `branch_sweep.py` (below), and
`jira_config.py`. It is standard-library Python, like every other org script (ADR-002
R12): click became argparse, pydantic became plain dicts with an index, and a change-set
may be JSON when PyYAML is absent. It runs with bare `python3`, and the org flake exposes
it as `nix run github:<org>/.github#jira` and puts `jira` in every org dev shell with
PyYAML and certifi.

**Configuration is generic code + specific config.** The base URL and project scopes are
the `[jira]` table of the nearest `org.toml` (or `JIRA_BASE_URL`); credentials are only
`JIRA_EMAIL` / `JIRA_API_TOKEN` in the environment (Infisical locally, repository secrets
in Actions). The snapshot defaults to `$XDG_CACHE_HOME/org-jira/<site>/summary.json`, so
one sync serves every checkout on a machine and nothing lands in a working tree unless a
repository opts in with `[jira].snapshot`.

**It is not distributed by the sync.** Skills and agents are copied into every repository
because they are content an agent must find in its checkout; a tool is invoked, not
read, so it is reached through the flake or a path. This keeps consuming repositories
at seven org files instead of fifteen.

The `org-jira` skill now points at this CLI first, the Atlassian MCP second. The docs
repository's copy is superseded; its `docs-cli.toml` habits (`sync/jira/summary.json`
committed in the repo) become a per-repo `[jira].snapshot` choice.

### The branch ↔ ticket sweep is a script, a workflow, and a subagent

`branch_sweep.py` lists the branches of the given repositories through the GitHub API,
extracts Jira keys from branch names, and for each ticket branch records ahead/behind
against the repository's integration branch (read from its own `org.toml`, default
`staging`, else the default branch), the last commit's age and author, the open PR, and
the ticket's summary / status / assignee (live when credentials exist, else from the
snapshot). It groups by owner (the ticket's assignee, else the last committer) and flags
`merged`, `ticket-done`, `stale`, `no-ticket`, `unassigned`.

Key extraction: an upper-case key always counts; a lower-case one
(`infra-266-thing`) counts only when the project is known, from `[jira.sweep].projects`,
`[jira.sync].projects`, or the snapshot. This is what stops `release-2026-09` from
becoming a ticket.

`org-branch-sweep.yml` runs it weekly (kill-switched) and on demand, writes the report
to the job summary and an artifact, and on the schedule refreshes one pinned issue
labelled `branch-sweep` in this repository. The `org-branch-sweep` subagent runs the same
script locally and answers the actual questions: what is pending for a person, what can
be deleted, which tickets are dishonest, proposing a `jira apply` plan for the last.

### Secrets and tokens

Two new repository secrets, `JIRA_EMAIL` and `JIRA_API_TOKEN`, and one variable,
`JIRA_BASE_URL` (optional; `[jira].base_url` in this repository's `org.toml` covers it).
Cross-repository branch listing uses `ORG_SYNC_TOKEN` or `ORG_READ_TOKEN`; the default
Actions token reads only its own repository, so without one the sweep covers `.github`
alone. Credentials never appear in any file in any tree; the secrets scan already
refuses them.

## Consequences

### Positive

- Ticket management costs one `jira sync` per session plus local greps and one batched
  write, in every repository, for every agent, with no submodule and no MCP.
- One tool, one config table, tested (16 unit tests against fixtures and fakes), and
  company-agnostic like the rest of the mechanism.
- The sweep gives the "what is still pending" view for free from data that already
  exists, and its flags feed the ticket-hygiene rule in the baseline (status matches
  reality).

### Negative

- Two copies exist until the docs repository drops its `pkgs/` — the port changes
  behaviour slightly (no `default_author` requirement, cache-located snapshot, JSON
  plans), so scripts written against `docs jira` need a path change and nothing else.
- The snapshot is a cache that can be stale; `jira apply` validates against it, so a
  ticket created elsewhere since the last sync is "not in snapshot" until the next
  `jira sync`. The error says so.
- The sweep is read-heavy: three API calls per ticket branch. At the organization's
  current size that is well under a hundred calls; a much larger org would want the
  GraphQL API.
- `jira standup` shells out to `claude -p`; it is a local convenience, not something the
  workflows run.
- The workflow has not run in Actions; the sweep was exercised live against the four
  in-scope repositories with the session token (offline ticket mode), the Jira side
  against fakes. `jira sync` itself was not run in this session (no API token here).

## Alternatives Considered

### Keep the tool in the docs repository and check the submodule out everywhere

**Rejected because:** the rule is org-wide, the submodule is not, and the tool's config
named the company.

### Distribute `claude/jira/` into every repository with the sync

**Rejected because:** it doubles the org footprint in each repository for something that
is executed, not read. The flake app and a path are enough; the sync still ships the
skill that tells the agent how to call it.

### Keep the MCP as the primary interface

**Rejected because:** the token cost is the problem being solved. The MCP remains the
fallback for one-off interactive lookups and rich-text comments.

### A GitHub App or GraphQL for the sweep

**Rejected because:** REST with a PAT is enough at this scale and needs no new identity;
revisit if the sweep ever takes more than a minute.

## Implementation Notes

- Shipped: `claude/jira/*`, `claude/tests/test_jira.py`,
  `.github/workflows/org-branch-sweep.yml`, `claude/agents/org-branch-sweep.md`, the
  `[jira]` table in `orgfile.py` defaults and example, the `jira` flake app and org-shell
  package, and the `org-jira` skill's tooling section.
- To go live: set `JIRA_EMAIL` / `JIRA_API_TOKEN` secrets (a dedicated service account is
  better than a person's token), confirm `ORG_SYNC_TOKEN` reads every repository in
  `claude/repos.txt`, dispatch `org-branch-sweep.yml` once with `publish: issue`.
- Follow-up for the docs repository: replace `pkgs/` with a `nix run
  github:<org>/.github#jira` wrapper and set `[jira].snapshot = "sync/jira/summary.json"`
  in its `org.toml` if the committed snapshot is still wanted.

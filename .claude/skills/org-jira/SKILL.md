---
name: org-jira
description: Organization Jira workflow — find or create the ticket before editing, keep it In Progress while working, and close it (Done vs Review) when the work ships. Use at the start, pause, and end of any task that changes code.
---

# Jira ticket workflow

Every conversation that changes code is tied to a ticket. This skill is the procedure; the
rule itself is in the org baseline block.

## Projects

The organization's Jira projects are declared in this repository's `org.toml` under
`[jira].sync` (`projects`): that list is both the snapshot scope and the set of keys
recognised in branch names. Pick the project whose scope matches the *implementation
surface* of the work; repository-specific guidance on which project to prefer (and what
each is for) lives below the baseline block in the repo's own `CLAUDE.md`. Other keys may
exist — check the visible projects if unsure.

When work spans projects, prefix commits with the project that describes the
*implementation surface* and cross-link the other ticket in a comment.

## Starting

1. If the user named a ticket, use it. Otherwise search the most relevant project first,
   then broaden to the legacy backlog; present the best matches and confirm rather than
   silently picking one.
2. Nothing fits → propose a new Task/Story in the most relevant project and confirm.
   **Always set the assignee** (default: Jeirmeister,
   `712020:f4a48735-4966-44ab-95fc-b8871a707d8a`, unless the user names someone else).
   Unassigned tickets vanish from the standup summary.
3. Transition to **In Progress** once confirmed (fetch valid transitions first; ids differ
   per project). Already In Progress → leave it.
4. Prefix every commit: `<PROJECT>-NNN: <description>`.

## Tooling: the org `jira` CLI first (ADR-008)

The interactive Jira MCP costs a round-trip and kilobytes of boilerplate per call. The
org ships a snapshot-first CLI instead: reads come from a local JSON snapshot, writes go
out as one validated batch. It is in the org dev shell as `jira`, or:

```bash
nix run github:jeirslab/.github#jira -- <command>     # anywhere
python3 <org .github checkout>/claude/jira/jira_cli.py <command>   # bare python3 also works
```

Credentials are `JIRA_EMAIL` / `JIRA_API_TOKEN` in the environment (`infisical run --
jira sync`), never a file. The base URL and project scope come from the repository's
`org.toml` `[jira]` table (or `JIRA_BASE_URL`).

```bash
jira sync --open-only            # refresh the snapshot (once per session; ~seconds)
jira find "standup" --open       # keys + summaries; zero API calls
jira show INFRA-207              # summary, status, assignee, last comments, children
jira apply plan.yaml --dry-run && jira apply plan.yaml
jira sweep --repos-file claude/repos.txt   # branch ↔ ticket report (subagent org-branch-sweep)
```

A change-set (`plan.yaml`) is a list of ops; `ref:` names a created issue and `$name`
references it later. Start and finish of a task are one plan each:

```yaml
- create: {project: INFRA, type: Task, summary: "…", assignee: <name>, ref: t}
- transition: {key: $t, to: In Progress}
# … later …
- comment: {key: INFRA-123, body: "Shipped: … Verified: …"}
- transition: {key: INFRA-123, to: Done}
```

The Atlassian MCP tools are the fallback for one-off interactive lookups or rich comments.
If neither is available, the REST API with the same credentials is still not an excuse to
skip ticket management.

## Finishing — closing the ticket is part of the task

The lifecycle is **To Do → In Progress → Done**. In Progress the moment work starts;
Done the moment the deliverable is committed, pushed and verified as far as you can
verify it. Nothing else is a resting state for finished work.

1. **Comment and transition together.** A comment that says shipped/deployed/verified
   without a status change is a workflow failure.
2. **Done means: pushed + verified with evidence.** Verification is the `org-verifier`
   subagent's report (or your own equivalent: the commands you ran and their results),
   pasted into the closing comment and the PR's "Verification performed" section. If a
   project has a *Review* status and a human must exercise the result before it counts
   (product-facing UX, security-sensitive change, ops sign-off, anything the user said
   they want to check), use Review with a note on what to look at.
3. **What you could not verify does not hold the ticket open.** File the remainder as
   one follow-up ticket ("first run in Actions", "human to check X"), cross-link it in
   the closing comment, and close the original as Done. A ticket parked at To Do with
   shipped work on it is a lie on the board.
4. **To Do is only for work that actually stopped** part-way, with a comment on where
   it stopped and what is next. Blocked externally → Blocker naming the dependency.
   Deprioritised → Backlog.
5. **Never end a conversation with a stale status.** Before you stop, every ticket you
   touched is at the state that matches reality. The `commit-msg` hook warns when you
   commit against a ticket that is not In Progress; the weekly sweep flags a merged
   branch whose ticket is still In Progress (`in-progress-merged`).

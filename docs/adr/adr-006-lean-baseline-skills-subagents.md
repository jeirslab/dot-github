# ADR-006: Lean baseline block; procedures as on-demand skills, specialised work as subagents

- **Date:** 2026-09-09
- **Status:** Proposed
- **Refines:** ADR-001 (layout), ADR-004 (distribution)

---

## Context

The first version of the org baseline `CLAUDE.md` block (ADR-001, ADR-004) carried
everything an agent might need: the full Jira procedure, ADR practice, branch model, dev
environment, working style. About 85 lines, loaded into every conversation in every
repository on every turn, whether the task was a one-line fix or a release.

On the 2026-09-08 tech call the engineering lead set the direction: keep the core
`CLAUDE.md` very light, split the rest into agent configurations that load only when the
agent is doing that kind of work, and keep token burn down. This is the static-versus-
dynamic context split from the Google SDLC paper the team adopted as its framework:
static context is expensive because every token is present in every interaction; dynamic
context is loaded on demand through skills and sub-agents with progressive disclosure.

Claude Code has first-class mechanisms for exactly this:

- **Skills** (`.claude/skills/<name>/SKILL.md`): only the name and description are in
  context at start; the body loads when the task matches. Procedural knowledge belongs
  here.
- **Subagents** (`.claude/agents/<name>.md`): a separate system prompt with its own tool
  scope, invoked for a bounded job; its prompt never sits in the main conversation.
- **MCP servers** (`.mcp.json`): every configured server's tool definitions cost context
  on every turn, so the set must be per-repository and minimal.

## Decision

1. **The baseline block contains hard rules only.** Non-negotiables an agent must never
   forget, stated in one line each, plus an "on demand" table that names the skill or
   subagent for everything else. Target: under 45 lines. Anything procedural, explanatory,
   or situational is moved out.

2. **Procedures are org skills**, shipped from `claude/skills/org-*/SKILL.md`:
   - `org-jira` — find/create/transition/close, Done vs Review, tooling order;
   - `org-adr` — when an ADR is required, where it lives, shape, lifecycle;
   - `org-dev-env` — flake dev shell, Infisical, `org-check`, MCP minimalism;
   - `org-branching` — staging/main model, releases, hotfixes, auto-merge label semantics,
     kill switch.

3. **Specialised, bounded work is done by org subagents**, shipped from
   `claude/agents/org-*.md`, each read-only by prompt and scoped by its `tools` list:
   - `org-reviewer` — first-pass review against the org rubric (ADR-002 R6);
   - `org-conformance` — what a repository is missing against the org standard;
   - `org-release-notes` — drafts the release body for a merge into `main` (ideas.md 14).
   Model routing (ADR-002 R7) is an operator setting on the agent frontmatter, not
   something the baseline hard-codes.

4. **Naming and ownership.** Org-shipped skills and agents carry the `org-` prefix. The
   sync replaces org-prefixed entries wholesale, removes files that dropped out of an org
   skill, and never touches anything without the prefix; repositories add their own
   skills and agents freely. `{{ORG_GITHUB}}` is rendered in skills the same way as in
   the block.

5. **MCP stays minimal.** The org baseline `.mcp.json` carries only servers that work in
   every repository. Repositories add what their work needs and nothing more; the
   `org-dev-env` skill says so.

## Consequences

### Positive

- Static context drops from ~85 lines to ~40 in every conversation in every repository;
  the procedures are still one skill-load away and are richer than before because they
  no longer compete for space.
- Subagents give the reviewer, conformance and release-note jobs their own tool scope and
  keep their prompts out of the main conversation.
- The same sync mechanism (ADR-004) distributes all of it; no new workflow.

### Negative

- An agent that ignores the "on demand" table and never loads `org-jira` can miss
  procedure detail it used to see by default. The hard rule ("ticket before edit, prefix,
  status matches reality") remains in the block, so the failure mode is a missing
  nuance, not a skipped ticket.
- Skill and agent frontmatter formats are Claude Code specific; other coding agents will
  read the block and ignore the rest until an equivalent exists.
- Three more directories in every consuming repository's `.claude/`.

## Alternatives Considered

### Keep one comprehensive block

**Rejected:** it is the thing the lead asked to stop doing, and the token cost is paid on
every turn by everyone.

### `@import` the procedures from the block

Claude Code can include files into `CLAUDE.md` at load time.

**Rejected:** imports are static context too; they load every time. Skills load on match.

### One "org" subagent that knows everything

**Rejected:** a subagent is for bounded jobs with a tool scope; procedures are knowledge
the main agent needs inline while it works, which is what skills are for.

## Implementation Notes

- Shipped: lean `claude/CLAUDE.md`, four skills, three subagents,
  `sync.py` skills/agents distribution with tests, CI frontmatter check, this ADR.
- Verified: unit tests; dogfood sync of this repository shows the block at ~40 lines and
  the skills/agents under `.claude/`.
- Follow-ups: an `org-secrets` skill or hook once the guardrail scripts exist
  (ideas.md 6, 19); per-repo pruning of MCP servers when the sync PRs land; evaluate
  whether the reviewer subagent should run in CI as the R6 first-pass review.

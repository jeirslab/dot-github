---
name: org-adr
description: How and when to write an Architecture Decision Record in this organization — what counts as significant, where ADRs live, the template, numbering, superseding, and org-level vs repository-level records. Use before adding services, changing data flow, infrastructure, security/auth, or pipelines.
---

# Architecture Decision Records

## When

Create an ADR for every significant decision, before or alongside the change:

- adding a service, container, or infrastructure component (queues, databases, caches);
- changing data flow between services or the indexer pipeline;
- security or auth changes (network isolation, identity, secrets handling);
- CI/CD or promotion pipeline changes; branch-model changes;
- anything a future reader would ask "why is it like this?" about.

If unsure, write a short one. A one-page ADR that turns out unnecessary costs minutes; a
missing one costs an archaeology session.

## Where

- Repository decisions: that repository's `docs/adr/`, numbered sequentially within the
  repo (`adr-NNN-<slug>.md`), using the repo's template (`docs/adr/adr-template.md` or
  `.md.j2`).
- Organization decisions (workflows, agent configuration, branch model, tooling that
  applies everywhere): the org `.github` repository's `docs/adr/`.
- Cross-reference with a repo prefix when needed, e.g. `deployments ADR-068`.
- Every repository should have `docs/adr/`; the conformance check reports its absence.

## Shape

Header with **Date**, **Status** (Proposed | Accepted | Superseded by ADR-NNN |
Deprecated) and **Ticket** (the Jira key). Sections: Context (facts, constraints, prior
art), Decision (stated so compliance can be checked later), Consequences (positive and
negative), Alternatives Considered (each with why rejected), Implementation Notes.

## Lifecycle

- A new ADR starts **Proposed**; it becomes **Accepted** when merged into the deploy
  branch after review.
- Never edit a superseded ADR's decision text. Write a new ADR, mark the old one
  `Superseded by ADR-NNN`, and link both ways.
- If the repository generates documentation from ADRs (the fleet handbook does), run its
  regeneration command after adding one and do not commit generated copies.

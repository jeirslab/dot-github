# ADR-015: GitHub App as the org automation identity; root credential provisioned locally

- **Date:** 2026-09-15
- **Status:** Accepted
- **Ticket:** INFRA-303

---

## Context

ADR-014 established that org-wide workflows take a token (`ORG_SYNC_TOKEN`), fall back to
`GITHUB_TOKEN`, and are confined to the org that owns `.github`. Two questions remained:
what *is* that credential, and how is it provisioned without a person hand-managing a PAT?

Constraints (all confirmed against GitHub):
- **A workflow cannot provision its own credential.** `GITHUB_TOKEN` cannot manage secrets,
  and Actions never exposes the triggering user's credentials (`github.actor` is only a
  name). So no workflow — and no "run as the actor" trick — can set the sync secret.
- **Neither a PAT nor a GitHub App can be created headlessly.** PATs have no creation API
  (UI only). An App is created through the App-manifest flow, which needs a browser and a
  redirect that returns a one-time `code`; the private key is returned exactly once, to
  whoever holds that code. A CI runner has no browser and no endpoint the browser can reach.
- A personal token spans every org its owner belongs to, so the token cannot be the org
  guardrail (ADR-014 confinement handles that in `targets.py`).

## Decision

1. **The durable automation identity is a GitHub App**, owned by the org (or user) that owns
   `.github`, with exactly the fleet permissions (Contents/PRs/Workflows/Issues: write,
   Metadata: read). Workflows mint **short-lived installation tokens per run**
   (`actions/create-github-app-token`, gated on `vars.ORG_APP_ID`); no long-lived PAT is
   stored. The token slot is `steps.app-token.outputs.token || secrets.ORG_SYNC_TOKEN ||
   github.token`, so App, PAT, and `GITHUB_TOKEN` are interchangeable and adoption is gradual.

2. **The root credential is always provisioned locally, never by a workflow or the actor.**
   Two stdlib commands (flake apps), mirroring `runner_bootstrap.py`:
   - `set-sync-token` — set `ORG_SYNC_TOKEN` (repo or org secret) from a PAT, using local
     admin `gh` auth. The interim/simple path.
   - `app-bootstrap` — run the App-manifest flow with a **localhost callback**: it opens the
     browser (the one human step), captures the redirect `code` on `127.0.0.1`, exchanges it
     for the App ID + private key, writes the key, and can set `ORG_APP_ID` /
     `ORG_APP_PRIVATE_KEY`. Detects org vs user account for the correct create-app URL.

3. **No workflow creates the App.** A runner cannot capture the manifest-flow code or the
   one-time private key, so App creation stays the local one-click bootstrap. Everything
   *after* creation (minting tokens, and later distributing/rotating secrets *via the App*)
   is automatable.

4. **Confinement covers user accounts.** The `.github` owner may be an organization or a
   user; `targets.py` keys on `GITHUB_REPOSITORY_OWNER`, so a user-owned `.github` confines
   to that user's repos exactly as an org confines to the org's.

## Consequences

### Positive
- No long-lived org PAT to store or rotate; installation tokens are short-lived and scoped.
- One clear, one-click bootstrap; the same App can later manage/rotate other secrets.
- App, PAT, and `GITHUB_TOKEN` share one token slot — teams adopt in any order.

### Negative / trade-offs
- The App still requires one manual browser step to create (GitHub offers no headless path).
- `app-bootstrap`'s live flow (browser + manifest exchange) can't be unit-tested; only its
  pure parts are. It is exercised by hand, like `runner_bootstrap.py`.

### Deferred
- Terranix wiring: publish `ORG_APP_ID`/`ORG_APP_PRIVATE_KEY`, manage
  `github_app_installation_repositories`, and optionally authenticate the platform stack as
  the App instead of `ORG_ADMIN_TOKEN`.

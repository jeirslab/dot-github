#!/usr/bin/env bash
# Organization conformance check — "does this repository have what every repository in
# the org must have?" (ADR-005; ideas.md 10 and 23). Prints one line per
# requirement and exits 1 when anything is missing. Plain bash + git so it runs the
# same from a dev shell, `nix run github:<org>/.github#org-check`, or a workflow.
#
# Usage: org-check.sh [repo-root]      (default: current directory)
set -euo pipefail

root="${1:-.}"
cd "$root"
fail=0

# The org name is derived, never hard-coded, so a company/org rename changes nothing here.
org="$(git remote get-url origin 2>/dev/null | sed -E 's#.*[:/]([^/:]+)/[^/]+$#\1#' || true)"
org="${org:-<org>}"

ok()   { printf '  ok    %s\n' "$1"; }
miss() { printf '  MISS  %s\n' "$1"; fail=1; }

echo "org-check: $(pwd) (org: $org)"

# 1. Developer environment is a flake (ADR-005).
if [ -f flake.nix ]; then
  ok "flake.nix — developer environment via 'nix develop'"
else
  miss "flake.nix — every repository provides its developer environment as a flake (ADR-005); start from lib.mkDevShell in github:$org/.github"
fi

# 2. Agent rules carry the org baseline block (ADR-001 / ADR-004).
claude_md=""
if [ -f .claude/CLAUDE.md ]; then claude_md=.claude/CLAUDE.md; elif [ -f CLAUDE.md ]; then claude_md=CLAUDE.md; fi
if [ -z "$claude_md" ]; then
  miss "CLAUDE.md — no agent rules at all; run sync-agent-config (ADR-004)"
elif grep -q 'BEGIN ORG BASELINE' "$claude_md"; then
  ok "$claude_md — carries the org baseline block"
else
  miss "$claude_md — exists but lacks the org baseline block; run sync-agent-config (ADR-004)"
fi

# 3. Claude Code settings present.
if [ -f .claude/settings.json ]; then
  ok ".claude/settings.json"
else
  miss ".claude/settings.json — run sync-agent-config (ADR-004)"
fi

# 4. Architecture decision records live in the repo.
if [ -d docs/adr ]; then
  ok "docs/adr/"
else
  miss "docs/adr/ — every repository keeps its ADRs here (ideas.md 1)"
fi

# 5. Branch model: staging exists (INFRA-270).
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git show-ref --verify --quiet refs/heads/staging \
     || git show-ref --verify --quiet refs/remotes/origin/staging \
     || git ls-remote --exit-code --heads origin staging >/dev/null 2>&1; then
    ok "staging branch"
  else
    miss "staging branch — integration branch of the v2 model; run create-branches.yml (INFRA-270)"
  fi
else
  miss "not a git repository"
fi

# 6. .env never committed.
if [ -f .gitignore ] && grep -qE '^\.env(\b|$)' .gitignore; then
  ok ".env is git-ignored"
else
  miss ".gitignore does not ignore .env — secrets come from Infisical, not files (ADR-005)"
fi

if [ "$fail" -eq 0 ]; then
  echo "conformant"
else
  echo "gaps found — see MISS lines above"
  exit 1
fi

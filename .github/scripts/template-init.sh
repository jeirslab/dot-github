#!/usr/bin/env bash
# template-init.sh — one-shot cleanup for a repository created from this template.
#
# This repository is a GitHub *template* (main IS the template). "Use this template"
# copies the entire tree, including docs/adr/ — which holds THIS project's own design
# history and is only useful to the maintainers of the template. A freshly generated
# repository does not need it.
#
# This script strips the template's own content from a generated repository and leaves it
# conformant with the org standard (an empty docs/adr/ skeleton the way org-bootstrap seeds
# one). It is org-name agnostic on purpose: it must run in any downstream organization.
#
# It is invoked two ways (ADR: keep the logic here, the workflow a thin trigger):
#   - automatically by .github/workflows/template-cleanup.yml on the `create` event, and
#   - manually as step 1 of adoption:  bash .github/scripts/template-init.sh
#
# Idempotent: a marker (.github/.template-initialized) makes a second run a no-op.
#
# Usage:
#   template-init.sh            apply (default)
#   template-init.sh --check    report whether cleanup is still pending; exit 1 if so
#   template-init.sh --force    apply even if the marker is present
set -euo pipefail

if ! root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  root="$(cd "$(dirname "$0")/../.." && pwd)"
fi
marker="$root/.github/.template-initialized"
adr="$root/docs/adr"

mode="apply"
case "${1:-}" in
  --check) mode="check" ;;
  --force) mode="force" ;;
  "") ;;
  *) echo "usage: template-init.sh [--check|--force]" >&2; exit 2 ;;
esac

if [[ -f "$marker" && "$mode" != "force" ]]; then
  [[ "$mode" == "check" ]] && { echo "template-init: already initialized"; exit 0; }
  echo "template-init: already initialized (marker present); nothing to do"
  exit 0
fi

# Numbered decision records that belong to the template, not to a fresh repo.
mapfile -t decisions < <(find "$adr" -maxdepth 1 -type f -name 'adr-[0-9]*.md' 2>/dev/null | sort || true)

if [[ "$mode" == "check" ]]; then
  if (( ${#decisions[@]} )); then
    echo "template-init: cleanup pending — ${#decisions[@]} template ADR(s) still present"
    printf '  %s\n' "${decisions[@]#$root/}"
    exit 1
  fi
  echo "template-init: clean"
  exit 0
fi

if (( ${#decisions[@]} )); then
  rm -f "${decisions[@]}"
  echo "template-init: removed ${#decisions[@]} template ADR(s)"
fi

# Reset the ADR index to the same empty skeleton org-bootstrap seeds, keeping the template.
if [[ -d "$adr" ]]; then
  cat > "$adr/README.md" <<'EOF'
# Architecture Decision Records

Decisions for this repository, numbered sequentially. Template:
[adr-template.md](adr-template.md). Org-level decisions live in the org `.github`
repository.

| ADR | Title | Status |
|-----|-------|--------|
EOF
  echo "template-init: reset docs/adr/README.md to an empty skeleton"
fi

mkdir -p "$root/.github"
cat > "$marker" <<'EOF'
This repository was initialized from a template; template-only content (the template's
own docs/adr history) has been removed. Delete this file only if you want the cleanup to
run again. You may also delete .github/workflows/template-cleanup.yml and
.github/scripts/template-init.sh — they are only needed once.
EOF
echo "template-init: wrote $marker"

cat <<'EOF'

Template initialized. Remaining adoption steps (see README "Adopting this template"):
  1. Set repository variables/secrets (nothing runs until you do):
       vars:    ORG_WORKFLOWS_ENABLED (leave false until ready), ORG_FEAT_TEST(_BRANCH),
                ORG_RELEASE_NOTES_MODEL, ANTHROPIC_BASE_URL (if using a proxy)
       secrets: ANTHROPIC_API_KEY, ORG_SYNC_TOKEN, ORG_ADMIN_TOKEN (optional)
  2. Edit the fleet + platform data for your org:
       claude/repos.txt, claude/repos.toml, nix/github/manifest.nix, org.toml
  3. Rename this repository to `.github` under your org for org-wide defaults to apply.
  4. Once green on your feature-test branch, flip ORG_WORKFLOWS_ENABLED to true.
EOF

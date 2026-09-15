#!/usr/bin/env bash
# shakedown.sh — end-to-end test of the org engine + template. Idempotent, sandbox-only.
#
# Run from a checkout of <owner>/.github (uses ./claude, ./flake.nix), with gh authenticated:
#
#     GH_TOKEN="$(gh auth token)" bash .github/scripts/shakedown.sh            # local proofs + dispatch
#     GH_TOKEN="$(gh auth token)" bash .github/scripts/shakedown.sh --local    # local proofs only, no Actions
#
# It exercises what can be automated headlessly. Two things need YOU (a browser) and are only
# printed, never attempted: the GitHub App bootstrap and the "Use this template" button. A few
# workflows need infrastructure not present in a sandbox (deploy env, OCI registry, fleet
# runner, OpenTofu state) and are reported as such rather than failed.
set -uo pipefail

# Owner = this repo's owner (org or user); never hardcoded, so this script names no company
# and passes the "no org name in a mechanism file" check (it lives under .github/scripts/).
OWNER="${OWNER:-$(gh repo view --json owner -q .owner.login 2>/dev/null)}"
[ -z "$OWNER" ] && { echo "set OWNER=<org-or-user> (could not detect from gh)"; exit 1; }
ENGINE="$OWNER/.github"
SRC="$(cd "$(dirname "$0")/../.." && pwd)"      # engine checkout root
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DISPATCH=1; [ "${1:-}" = "--local" ] && DISPATCH=0
PASS=0; FAIL=0
ok(){ echo "  ✅ $*"; PASS=$((PASS+1)); }
no(){ echo "  ❌ $*"; FAIL=$((FAIL+1)); }
sec(){ echo; echo "=== $* ==="; }

command -v gh >/dev/null || { echo "gh not found"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh not authenticated (set GH_TOKEN or run gh auth login)"; exit 1; }
TOKEN="$(gh auth token)"

sec "0. preconditions — sandboxes, v2 ref, ORG_SYNC_TOKEN"
for r in test-alpha test-beta; do
  gh repo view "$OWNER/$r" >/dev/null 2>&1 || gh repo create "$OWNER/$r" --private --add-readme >/dev/null 2>&1
  gh repo view "$OWNER/$r" >/dev/null 2>&1 && ok "$r present" || no "$r missing"
done
git -C "$SRC" fetch -q origin main 2>/dev/null
git -C "$SRC" tag -f v2 origin/main >/dev/null 2>&1 && git -C "$SRC" push -f origin v2 >/dev/null 2>&1
gh api "repos/$ENGINE/git/matching-refs/tags/v2" --jq '.[].ref' 2>/dev/null | grep -q v2 && ok "v2 ref present" || no "v2 ref missing"
gh secret set ORG_SYNC_TOKEN -R "$ENGINE" --body "$TOKEN" >/dev/null 2>&1 && ok "ORG_SYNC_TOKEN set" || no "could not set ORG_SYNC_TOKEN"

sec "1. local baseline sync + workflow-caller generation"
T="$TMP/ta"; git clone -q "https://x-access-token:$TOKEN@github.com/$OWNER/test-alpha" "$T"
printf '[workflows]\nenabled = ["gate", "guardrails"]\n' > "$T/org.toml"
python3 "$SRC/claude/sync.py" apply --source "$SRC/claude" --target "$T" --org "$OWNER" --version shakedown >/dev/null 2>&1
[ -f "$T/.github/workflows/org-gate.yml" ] && [ -f "$T/.github/workflows/org-guardrails.yml" ] && ok "selected callers generated" || no "callers missing"
grep -q "$ENGINE/.github/workflows/org-gate.yml@v2" "$T/.github/workflows/org-gate.yml" && ok "caller pins @v2" || no "caller ref wrong"
grep -q "BEGIN ORG BASELINE" "$T/CLAUDE.md" && [ -d "$T/.claude/skills" ] && ok "baseline block + skills/agents" || no "baseline missing"

sec "2. propagation — edit engine → consumer changes; selection change; repo-owned untouched"
E="$TMP/eng"; cp -r "$SRC/claude" "$E"
printf '\n<!-- SHAKEDOWN-MARKER -->\n' >> "$E/skills/org-jira/SKILL.md"
python3 "$SRC/claude/sync.py" apply --source "$E" --target "$T" --org "$OWNER" --version s2 >/dev/null 2>&1
grep -q SHAKEDOWN-MARKER "$T/.claude/skills/org-jira/SKILL.md" && ok "engine skill edit propagated" || no "skill edit did not propagate"
printf '[workflows]\nenabled = ["gate", "release"]\n' > "$T/org.toml"
python3 "$SRC/claude/sync.py" apply --source "$SRC/claude" --target "$T" --org "$OWNER" --version s3 >/dev/null 2>&1
[ -f "$T/.github/workflows/org-release.yml" ] && [ ! -f "$T/.github/workflows/org-guardrails.yml" ] && ok "selection change added/pruned callers" || no "selection change wrong"
mkdir -p "$T/.claude/skills/my-own"; echo mine > "$T/.claude/skills/my-own/SKILL.md"; echo mine > "$T/.github/workflows/my-ci.yml"
python3 "$SRC/claude/sync.py" apply --source "$SRC/claude" --target "$T" --org "$OWNER" --version s4 >/dev/null 2>&1
[ -f "$T/.claude/skills/my-own/SKILL.md" ] && [ -f "$T/.github/workflows/my-ci.yml" ] && ok "repo-owned skill + workflow untouched" || no "repo-owned content clobbered"

sec "3. same-org confinement"
python3 - "$SRC" <<PY
import sys; sys.path.insert(0, sys.argv[1]+"/claude"); import targets
r = targets.resolve("$OWNER", None, "$OWNER/test-alpha someoneelse/x", {})
print("  ✅ confinement drops foreign target" if r["repos"]==["$OWNER/test-alpha"] and "someoneelse/x" in r["note"] else "  ❌ confinement failed")
PY

sec "4. template constructor (external build for a throwaway org)"
C="$TMP/tc"
if command -v nix >/dev/null; then
  nix develop "$SRC" --command bash -c "python3 $SRC/claude/construct.py --org testcorp --profile external --target $C --force" >/dev/null 2>&1
else
  python3 "$SRC/claude/construct.py" --org testcorp --profile external --target "$C" --force >/dev/null 2>&1
fi
[ -d "$C" ] && [ "$(grep -rIi "$OWNER" "$C" 2>/dev/null | grep -vc '/.git/')" = "0" ] && ok "constructor output clean (0 source-org leaks)" || no "constructor leak or build failed"

if [ "$DISPATCH" = 0 ]; then
  echo; echo "local-only mode: skipping Actions dispatch."; echo "PASS=$PASS FAIL=$FAIL"; exit $(( FAIL>0 ))
fi

sec "5. dispatch org workflows (workflow_dispatch always runs; kill switch stays off)"
# name:input pairs; blank input = none
declare -a WF=(
  "sync-agent-config.yml=-f dry_run=true"
  "sync-agent-config.yml="
  "org-scorecard.yml="
  "org-adr-consolidate.yml="
  "org-branch-sweep.yml="
  "org-runner-smoke.yml="
  "create-branches.yml="
  "org-bootstrap.yml=-f repo=$OWNER/test-beta"
)
for entry in "${WF[@]}"; do
  wf="${entry%%=*}"; args="${entry#*=}"
  # shellcheck disable=SC2086
  gh workflow run "$wf" -R "$ENGINE" $args >/dev/null 2>&1 && echo "  dispatched $wf $args" || echo "  ⚠️ could not dispatch $wf"
done
echo "  …waiting for runs to settle…"; sleep 25
echo; printf "  %-26s %-10s %s\n" WORKFLOW STATUS CONCLUSION
for wf in sync-agent-config org-scorecard org-adr-consolidate org-branch-sweep org-runner-smoke create-branches org-bootstrap; do
  row="$(gh run list -R "$ENGINE" --workflow "$wf.yml" -L1 --json status,conclusion --jq '.[0] | "\(.status) \(.conclusion)"' 2>/dev/null)"
  printf "  %-26s %s\n" "$wf" "${row:-no-run}"
done

sec "6. NEEDS INFRASTRUCTURE (reported, not run) — expected in a sandbox"
cat <<TXT
  org-platform-github  needs ORG_TF_STATE_BUCKET + AWS creds + ORG_ADMIN_TOKEN (OpenTofu)
  org-rulesets         needs ORG_ADMIN_TOKEN; rulesets need a paid plan for PRIVATE repos
  org-deploy / org-oci reusable; need a deploy environment / OCI registry (org.toml)
  _runner / fleet      needs the self-hosted runner online (ORG_RUNNER)
  reusable org-gate/guardrails/deploy/oci  exercised via _workflow-tests.yml, not dispatched directly
TXT

sec "7. NEEDS YOUR BROWSER (printed, never attempted)"
cat <<TXT
  GitHub App:   nix run github:$ENGINE#app-bootstrap -- --owner $OWNER --set-secrets
  Template:     https://github.com/$OWNER/dot-github  →  "Use this template"  →  then run
                the "Template Cleanup" workflow (or bash .github/scripts/template-init.sh)
TXT

echo; echo "SUMMARY: PASS=$PASS FAIL=$FAIL (see the dispatch table + notes above)"
exit $(( FAIL>0 ))

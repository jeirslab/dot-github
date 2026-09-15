# Testing the org workflows before they go org-wide (ADR-009)

Two repository variables decide what runs:

| `ORG_FEAT_TEST` | `ORG_WORKFLOWS_ENABLED` | push to `main` | push to `feature-test` | dispatch |
|---|---|---|---|---|
| unset / false | unset / false | nothing | nothing | runs, real targets |
| unset / false | `true` | runs, real targets | nothing | runs, real targets |
| `true` | any | **nothing** | runs, **confined to this repo** | runs, **confined to this repo** |

"Confined" means every org-wide workflow (agent-config sync, scorecard, rulesets, ADR
consolidation, branch sweep, bootstrap) resolves its targets through `claude/targets.py`
to exactly `<org>/.github@feature-test`. It opens its PRs against `feature-test`, checks
`feature-test` out, and touches no other repository.

## The loop

```bash
git fetch origin && git checkout -b INFRA-123-my-change origin/main
# … edit .github/workflows/*.yml, claude/*.py …
python3 -m unittest claude.tests.test_workflows      # static policy: guards, docs, templates, exercised set
python3 -m unittest discover -s claude/tests         # everything
git push origin HEAD:feature-test                    # direct push allowed; or open a PR into feature-test
```

With `ORG_FEAT_TEST=true`, the push starts:

- `_workflow-tests.yml` — policy tests, `actionlint`, then the reusable workflows called
  against this repository: `org-gate` (fast tier), `org-guardrails`, `org-deploy` with the
  no-op `[deploy.test]` target, the sync check, `org-check`, the branch sweep, and
  `verify_pr` on the pushed commit. The `result` job is the one to watch.
- the org-wide workflows whose triggers matched (`sync-agent-config` on `claude/**`,
  `org-rulesets` on its paths, `_claude-config-ci`, `_generate_workflow-docs`), each
  running for real but only against `.github@feature-test`. Expect a sync PR into
  `feature-test` when the baseline changed; merge or close it there.

When green: open `feature-test → main`, merge, let `_version-workflows-repo.yml` tag,
move `v2` when the change is a consumer-facing one, and set `ORG_FEAT_TEST=false` (or
delete it) so `main` is live again.

## What the test workflow cannot prove

- `sync-release.yml` (a release is a PR merged into a deploy branch), `org-oci.yml`
  (registry), `org-branch-guard.yml` (would revert every feature-test push). Their
  scripts are unit-tested; the first real merge on a consumer proves the rest.
- Consumers' shapes: feature-test runs this repository's `org.toml`. A repo with a
  different gate or deploy command still needs its first real run watched.
- Anything requiring `ORG_SYNC_TOKEN` / `ORG_ADMIN_TOKEN` / `JIRA_*` when they are not
  set; the runs say so and skip.

## Keeping the suite honest

`claude/tests/test_workflows.py` fails when a new reusable workflow is neither called by
`_workflow-tests.yml` nor listed in `NOT_EXERCISED` with a reason, when an automatic job
lacks the guard, or when a workflow reads `repos.txt` on its own. Add the workflow to the
exercise set first; excuse it second.

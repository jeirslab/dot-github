{ ... }:

# VALUES for the org's GitHub configuration (ADR-010). This is the one file in nix/github/
# that is content rather than mechanism: it may name the organization, its repositories,
# teams and endpoints. Everything else in this folder is generic.
#
# The token and every secret VALUE are not here and never will be: they arrive as
# TF_VAR_github_token and TF_VAR_org_secrets (a JSON map) at plan/apply time, from
# SOPS / Infisical through the wrapper (see ../../flake.nix `platform-github`).

{
  github = {
    owner = "jeirslab";

    # Repositories: the sync allowlist is the source of truth (one file, already edited
    # when a repo joins the org). Per-repo overrides below; everything else gets
    # repoDefaults.
    reposFile = ../../claude/repos.txt;

    repoDefaults = {
      visibility = "private";
      defaultBranch = "main";
      deleteBranchOnMerge = true;
      allowSquashMerge = true;
      allowMergeCommit = true;
      allowRebaseMerge = false;
      allowAutoMerge = true;
      vulnerabilityAlerts = true;
    };

    repos = {
      ".github" = {
        description = "jeirslab org engine: reusable workflows, agent baseline, dev-env flake, ADRs (sandbox home of the org tooling)";
        # Feature-test mode and the kill switch live here (ADR-009); they are operator
        # toggles below, so a UI flip is not reverted by the next apply.
      };
      test-alpha = { description = "throwaway sandbox repo (terranix adoption experiment)"; };
      test-beta = { description = "throwaway sandbox repo (terranix adoption experiment)"; };
      # ADR-013 demo: this stack CREATES these (create = true → no import block).
      repo-template = {
        description = "Template repository — new repos are generated from this (ADR-013)";
        create = true; isTemplate = true; autoInit = true;
      };
      demo-svc = {
        description = "Demo service generated from repo-template via the registry (ADR-013)";
        create = true; template = "jeirslab/repo-template";
      };
    };

    # The label set the bootstrap workflow used to create by hand (org-bootstrap.yml) plus
    # the ones later workflows expect (rollback, policy-violation, branch-sweep).
    labels = {
      "author:human" = { color = "5319e7"; description = "provenance: human-authored commits"; };
      "author:claude-code" = { color = "5319e7"; description = "provenance: Claude Code session commits"; };
      "author:lovable" = { color = "5319e7"; description = "provenance: Lovable bot commits"; };
      "author:alert-agent" = { color = "5319e7"; description = "provenance: alert-response agent commits"; };
      "author:bot" = { color = "5319e7"; description = "provenance: dependency/org bot commits"; };
      "author:mixed" = { color = "5319e7"; description = "provenance: humans and agents both contributed"; };
      "needs-adr" = { color = "d93f0b"; description = "change under ADR-required paths without an ADR"; };
      "needs-split" = { color = "d93f0b"; description = "PR too large or spans several tickets"; };
      "adr:not-needed" = { color = "0e8a16"; description = "reviewer waived the ADR requirement (say why)"; };
      "auto-merge" = { color = "0e8a16"; description = "human-applied: merge staging → main when green"; };
      "rollback" = { color = "b60205"; description = "rollback proposal (org-deploy / rollback.yml)"; };
      "policy-violation" = { color = "b60205"; description = "direct push to a deploy branch (org-branch-guard, ADR-007)"; };
      "branch-sweep" = { color = "5319e7"; description = "pinned branch ↔ ticket sweep issue (ADR-008)"; };
      "automated" = { color = "ededed"; description = "opened by an org workflow"; };
      "org-scorecard" = { color = "ededed"; description = "weekly conformance scorecard PR"; };
      "org-agent-config" = { color = "ededed"; description = "agent baseline sync PR (ADR-004)"; };
    };

    # Actions variables every managed repository carries. The two toggles are created
    # off and then left to the operator (lifecycle ignore_changes on value).
    variables = {
      ORG_WORKFLOWS_ENABLED = { value = "false"; operatorToggle = true; };
      ORG_FEAT_TEST = { value = "false"; operatorToggle = true; };
      ORG_FEAT_TEST_BRANCH = { value = "feature-test"; };
      # NOTE: GitHub rejects empty Actions variable values (422). JIRA_BASE_URL and
      # ORG_RELEASE_NOTES_MODEL were empty here and are omitted in the sandbox.
      # Engine follow-up: emit.nix should filter out empty-valued variables.
    };

    # Actions secret NAMES every managed repository carries; values from TF_VAR_org_secrets.
    secrets = [ ];  # sandbox: no Actions secrets yet (added when workflows are activated)

    # Teams referenced by CODEOWNERS blocks (org.toml [codeowners]) and by verify_pr.py's
    # code-owner check. Members are GitHub logins; fill in or manage membership elsewhere.
    teams = {
      platform = { description = "Platform / infrastructure owners"; repos = { ".github" = "admin"; test-alpha = "maintain"; test-beta = "maintain"; }; };
    };

    # Webhooks stay declared but disabled until their receivers exist (ideas 30/31).
    webhooks = {
      agent-endpoint = {
        enable = false;
        url = "https://agent.example.internal/v1/github";   # idea 31: the fleet agent endpoint via netgate
        events = [ "pull_request" "pull_request_review" "release" "check_suite" ];
        secretVar = "AGENT_WEBHOOK_SECRET";
        scope = "organization";
      };
    };

    # Rulesets: emitted from claude/rulesets/*.json once the plan allows them.
    rulesets = { enable = false; dir = ../../claude/rulesets; };

    imports = { repos = true; teams = false; labels = false; };
  };
}

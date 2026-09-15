{ config, lib, ... }:

# Schema for the org's GitHub configuration (ADR-010, INFRA-286). Values live in
# ./manifest.nix; the Terranix emitter is ./emit.nix. Nothing here names a company: the
# owner, the token and every secret value arrive as OpenTofu variables at plan time.
#
# The provider-instance shape (source / version / secrets / state.prefix) mirrors
# fleetkit's `fleet.providers.<type>.<instance>` so this folder can be imported into the
# deployments Terranix pipeline with a rename rather than a rewrite.

let
  inherit (lib) mkOption types;

  # Repository settings come in two layers: `github.repoDefaults` (concrete defaults below)
  # and `github.repos.<name>` whose every option defaults to the corresponding repoDefaults
  # value, so an override only names what differs.
  mkRepoOptions = d: {
    description = mkOption { type = types.nullOr types.str; default = d.description; };
    visibility = mkOption { type = types.enum [ "private" "public" "internal" ]; default = d.visibility; };
    defaultBranch = mkOption { type = types.str; default = d.defaultBranch; };
    deleteBranchOnMerge = mkOption { type = types.bool; default = d.deleteBranchOnMerge; };
    allowMergeCommit = mkOption { type = types.bool; default = d.allowMergeCommit; };
    allowSquashMerge = mkOption { type = types.bool; default = d.allowSquashMerge; };
    allowRebaseMerge = mkOption { type = types.bool; default = d.allowRebaseMerge; };
    allowAutoMerge = mkOption { type = types.bool; default = d.allowAutoMerge; };
    hasIssues = mkOption { type = types.bool; default = d.hasIssues; };
    hasProjects = mkOption { type = types.bool; default = d.hasProjects; };
    hasWiki = mkOption { type = types.bool; default = d.hasWiki; };
    vulnerabilityAlerts = mkOption { type = types.bool; default = d.vulnerabilityAlerts; };
    archived = mkOption { type = types.bool; default = d.archived; };
    # Actions variables/secrets declared for this repository IN ADDITION to
    # github.variables / github.secrets (which apply to every repository).
    variables = mkOption { type = types.attrsOf types.str; default = d.variables; };
    secrets = mkOption { type = types.listOf types.str; default = d.secrets; };
    rulesets = mkOption {
      type = types.bool; default = d.rulesets;
      description = "Apply the org rulesets to this repository when github.rulesets.enable is true.";
    };
    manage = mkOption {
      type = types.bool; default = d.manage;
      description = "false = keep the repository out of the plan entirely (listed for the sync but not managed here).";
    };
    create = mkOption {
      type = types.bool; default = d.create;
      description = "true = this stack CREATES the repository (no import block emitted); false = adopt an existing repository via import. ADR-013.";
    };
    template = mkOption {
      type = types.nullOr types.str; default = d.template;
      description = "owner/repo to generate this repository from at creation (create = true only). null = a blank repository.";
    };
    isTemplate = mkOption {
      type = types.bool; default = d.isTemplate;
      description = "Mark this repository as a template repository others can be generated from.";
    };
    autoInit = mkOption {
      type = types.bool; default = d.autoInit;
      description = "Initialize with a README on creation (create = true, no template). Gives the repo a default branch.";
    };
  };

  baseDefaults = {
    description = null; visibility = "private"; defaultBranch = "main"; deleteBranchOnMerge = true;
    allowMergeCommit = true; allowSquashMerge = true; allowRebaseMerge = false; allowAutoMerge = true;
    hasIssues = true; hasProjects = false; hasWiki = false; vulnerabilityAlerts = true; archived = false;
    variables = { }; secrets = [ ]; rulesets = true; manage = true;
    create = false; template = null; isTemplate = false; autoInit = false;
  };

  repoDefaultsType = types.submodule { options = mkRepoOptions baseDefaults; };
  repoType = types.submodule { options = mkRepoOptions config.github.repoDefaults; };
in
{
  options.github = {
    owner = mkOption {
      type = types.str;
      description = "GitHub organization (or user) that owns every repository below.";
    };

    provider = {
      source = mkOption { type = types.str; default = "integrations/github"; };
      version = mkOption { type = types.str; default = "~> 6.6"; };
      secrets.token = mkOption {
        type = types.str;
        default = "integrations/github/terranix_token";
        description = "Secrets-store path of the provider token (fleetkit convention). Supplied as TF_VAR_github_token.";
      };
      state.prefix = mkOption { type = types.str; default = "tf/platform-github"; };
    };

    reposFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "A repos.txt (owner/name per line, # comments). Entries whose owner matches github.owner become managed repositories with default settings unless overridden in github.repos.";
    };

    repos = mkOption {
      type = types.attrsOf repoType;
      default = { };
      description = "Per-repository settings, keyed by repository name (without owner). Merged over the reposFile entries.";
    };

    repoDefaults = mkOption {
      type = repoDefaultsType;
      default = { };
      description = "Settings applied to every repository unless overridden.";
    };

    labels = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          color = mkOption { type = types.str; };
          description = mkOption { type = types.str; default = ""; };
        };
      });
      default = { };
      description = "Standard label set created in every managed repository.";
    };

    variables = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          value = mkOption { type = types.str; };
          operatorToggle = mkOption {
            type = types.bool; default = false;
            description = "true = created with this value but later UI changes are not reverted (lifecycle ignore_changes on value). For kill switches.";
          };
        };
      });
      default = { };
      description = "Actions variables set in every managed repository.";
    };

    secrets = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Names of Actions secrets set in every managed repository. Values come from the TF_VAR_org_secrets map (JSON) at plan time; never from this tree.";
    };

    teams = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          description = mkOption { type = types.str; default = ""; };
          privacy = mkOption { type = types.enum [ "closed" "secret" ]; default = "closed"; };
          members = mkOption { type = types.listOf types.str; default = [ ]; description = "GitHub logins (member role)."; };
          maintainers = mkOption { type = types.listOf types.str; default = [ ]; };
          repos = mkOption {
            type = types.attrsOf (types.enum [ "pull" "triage" "push" "maintain" "admin" ]);
            default = { };
            description = "Repository name → permission granted to this team.";
          };
        };
      });
      default = { };
    };

    webhooks = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          enable = mkOption { type = types.bool; default = false; };
          url = mkOption { type = types.str; };
          events = mkOption { type = types.listOf types.str; default = [ "push" ]; };
          contentType = mkOption { type = types.enum [ "json" "form" ]; default = "json"; };
          secretVar = mkOption {
            type = types.nullOr types.str; default = null;
            description = "Name of the entry in TF_VAR_org_secrets holding the HMAC secret.";
          };
          scope = mkOption { type = types.enum [ "organization" "repositories" ]; default = "organization"; };
          repos = mkOption { type = types.listOf types.str; default = [ ]; description = "For scope = repositories: which ones (default all managed)."; };
          insecureSsl = mkOption { type = types.bool; default = false; };
        };
      });
      default = { };
    };

    rulesets = {
      enable = mkOption {
        type = types.bool; default = false;
        description = "Emit github_repository_ruleset from rulesetsDir. GitHub Free returns 403 for private repositories; leave off until the plan allows it (ADR-007 substitutes meanwhile).";
      };
      dir = mkOption {
        type = types.nullOr types.path; default = null;
        description = "Directory of ruleset JSON files in the GitHub REST shape (claude/rulesets/).";
      };
    };

    imports = {
      repos = mkOption {
        type = types.bool; default = true;
        description = "Emit `import` blocks for every managed repository so the first apply adopts them instead of failing on them (OpenTofu ≥ 1.5). Harmless once the state holds them.";
      };
      teams = mkOption {
        type = types.bool; default = false;
        description = "Also import teams by slug. Only when they already exist; an import of a missing object fails the plan.";
      };
      labels = mkOption {
        type = types.bool; default = false;
        description = "Also import labels (repo:name). Set after org-bootstrap created them by hand; otherwise apply creates them.";
      };
    };
  };
}

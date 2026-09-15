{ config, lib, ... }:

# Terranix emitter for the org's GitHub configuration (ADR-010). Reads the `github.*`
# options (schema: ./options.nix; values: ./manifest.nix) and produces the
# integrations/github provider resources. Generic: nothing here names an organization.
#
# Safety, in order: `prevent_destroy` on every repository; `import` blocks so the first
# apply adopts existing repositories rather than erroring; `ignore_changes` on operator
# toggles; rulesets behind `github.rulesets.enable`; secrets only from TF_VAR_org_secrets.

let
  cfg = config.github;
  inherit (lib) mapAttrs mapAttrs' mapAttrsToList nameValuePair filterAttrs listToAttrs
    concatLists concatMap optional optionalAttrs unique attrNames hasSuffix removeSuffix;

  # ---- helpers -----------------------------------------------------------------------------
  # Terraform identifiers: letters, digits, underscores; must not start with a digit.
  san = s:
    let r = builtins.replaceStrings [ "." "-" "/" ":" " " "@" ] [ "_" "_" "_" "_" "_" "_" ] s;
    in if builtins.match "[0-9].*" r != null then "r_${r}" else r;

  trim = s: let m = builtins.match "[[:space:]]*(.*[^[:space:]])[[:space:]]*" s; in if m == null then "" else builtins.head m;

  # repos.txt → repository names owned by cfg.owner (case-insensitive owner match).
  parseReposFile = file:
    let
      lines = lib.splitString "\n" (builtins.readFile file);
      clean = map (l: trim (builtins.head (lib.splitString "#" l))) lines;
      entries = builtins.filter (l: l != "" && lib.hasInfix "/" l) clean;
      owned = builtins.filter (l: lib.toLower (builtins.head (lib.splitString "/" l)) == lib.toLower cfg.owner) entries;
    in map (l: lib.last (lib.splitString "/" l)) owned;

  fileRepos = if cfg.reposFile == null then [ ] else parseReposFile cfg.reposFile;
  names = unique (fileRepos ++ attrNames cfg.repos);
  settingsOf = n: if cfg.repos ? ${n} then cfg.repos.${n} else cfg.repoDefaults;
  managed = filterAttrs (_: s: s.manage) (listToAttrs (map (n: nameValuePair n (settingsOf n)) names));
  managedNames = attrNames managed;

  repoRef = n: "\${github_repository.${san n}.name}";
  secretRef = name: "\${var.org_secrets[\"${name}\"]}";

  # cross product helper: for every managed repo × every item → resource attrset
  perRepo = f: listToAttrs (concatLists (mapAttrsToList (n: s: f n s) managed));

  # ---- rulesets: GitHub REST JSON (claude/rulesets/*.json) → provider resource ------------
  rulesetFiles =
    if cfg.rulesets.dir == null then [ ]
    else builtins.filter (f: hasSuffix ".json" f) (attrNames (builtins.readDir cfg.rulesets.dir));
  loadRuleset = f: builtins.fromJSON (builtins.readFile (cfg.rulesets.dir + "/${f}"));
  ruleByType = rs: t: lib.findFirst (r: r.type == t) null rs.rules;
  toRulesetResource = repo: file:
    let
      rs = loadRuleset file;
      pr = ruleByType rs "pull_request";
      rsc = ruleByType rs "required_status_checks";
      has = t: ruleByType rs t != null;
    in {
      name = rs.name;
      repository = repoRef repo;
      target = rs.target or "branch";
      enforcement = rs.enforcement or "active";
      conditions.ref_name = {
        include = rs.conditions.ref_name.include or [ ];
        exclude = rs.conditions.ref_name.exclude or [ ];
      };
      rules = { }
        // optionalAttrs (has "deletion") { deletion = true; }
        // optionalAttrs (has "non_fast_forward") { non_fast_forward = true; }
        // optionalAttrs (has "required_linear_history") { required_linear_history = true; }
        // optionalAttrs (has "creation") { creation = true; }
        // optionalAttrs (pr != null) {
          pull_request = {
            required_approving_review_count = pr.parameters.required_approving_review_count or 1;
            dismiss_stale_reviews_on_push = pr.parameters.dismiss_stale_reviews_on_push or false;
            require_code_owner_review = pr.parameters.require_code_owner_review or false;
            require_last_push_approval = pr.parameters.require_last_push_approval or false;
            required_review_thread_resolution = pr.parameters.required_review_thread_resolution or false;
          };
        }
        // optionalAttrs (rsc != null) {
          required_status_checks = {
            required_check = map (c: { context = c.context; } // optionalAttrs (c ? integration_id) { integration_id = c.integration_id; })
              (rsc.parameters.required_status_checks or [ ]);
            strict_required_status_checks_policy = rsc.parameters.strict_required_status_checks_policy or false;
          };
        };
      bypass_actors = map (a: {
        actor_id = a.actor_id; actor_type = a.actor_type; bypass_mode = a.bypass_mode or "always";
      }) (rs.bypass_actors or [ ]);
    };

  # ---- webhooks ---------------------------------------------------------------------------
  hookConfig = h: {
    url = h.url;
    content_type = h.contentType;
    insecure_ssl = h.insecureSsl;
  } // optionalAttrs (h.secretVar != null) { secret = secretRef h.secretVar; };
  enabledHooks = filterAttrs (_: h: h.enable) cfg.webhooks;
  orgHooks = filterAttrs (_: h: h.scope == "organization") enabledHooks;
  repoHooks = filterAttrs (_: h: h.scope == "repositories") enabledHooks;
in
{
  terraform.required_providers.github = {
    source = cfg.provider.source;
    version = cfg.provider.version;
  };

  provider.github = {
    owner = cfg.owner;
    token = "\${var.github_token}";
  };

  variable = {
    github_token = {
      type = "string";
      sensitive = true;
      description = "Provider token (repo admin, org admin for teams/webhooks). Secrets-store path: ${cfg.provider.secrets.token}. Supplied as TF_VAR_github_token; never in a file in this tree.";
    };
    org_secrets = {
      type = "map(string)";
      sensitive = true;
      default = { };
      description = "Values for github.secrets and webhook HMAC secrets, keyed by name. Supplied as TF_VAR_org_secrets (JSON).";
    };
  };

  # ---- repositories --------------------------------------------------------------------------
  resource.github_repository = mapAttrs' (n: s: nameValuePair (san n) ({
    name = n;
    visibility = s.visibility;
    has_issues = s.hasIssues;
    has_projects = s.hasProjects;
    has_wiki = s.hasWiki;
    delete_branch_on_merge = s.deleteBranchOnMerge;
    allow_merge_commit = s.allowMergeCommit;
    allow_squash_merge = s.allowSquashMerge;
    allow_rebase_merge = s.allowRebaseMerge;
    allow_auto_merge = s.allowAutoMerge;
    archived = s.archived;
    is_template = s.isTemplate;
    lifecycle = {
      prevent_destroy = true;
      # Things GitHub sets or people change in the UI that this stack does not own.
      # `template`/`auto_init` only apply at creation, so ignoring later changes is safe.
      ignore_changes = [ "template" "auto_init" "gitignore_template" "license_template" "pages" "security_and_analysis" "homepage_url" "topics" ];
    };
  }
  // optionalAttrs (s.description != null) { description = s.description; }
  // optionalAttrs s.autoInit { auto_init = true; }
  // optionalAttrs (s.template != null) {
       template = {
         owner = builtins.head (lib.splitString "/" s.template);
         repository = lib.last (lib.splitString "/" s.template);
       };
     })) managed;

  resource.github_branch_default = mapAttrs' (n: s: nameValuePair (san n) {
    repository = repoRef n;
    branch = s.defaultBranch;
  }) managed;

  # `vulnerability_alerts` on github_repository is deprecated by the provider (v6); the
  # dedicated resource is the supported way. Only emitted where enabled.
  resource.github_repository_vulnerability_alerts = mapAttrs' (n: _: nameValuePair (san n) {
    repository = repoRef n;
    enabled = true;
  }) (filterAttrs (_: s: s.vulnerabilityAlerts) managed);

  # ---- labels (every managed repository × the standard set) -----------------------------------
  resource.github_issue_label = perRepo (n: _: mapAttrsToList (label: l: nameValuePair "${san n}__${san label}" {
    repository = repoRef n;
    name = label;
    color = l.color;
    description = l.description;
  }) cfg.labels);

  # ---- Actions variables and secrets -----------------------------------------------------------
  resource.github_actions_variable = perRepo (n: s:
    # GitHub rejects empty Actions variable values (422); drop them here (ADR-013).
    (mapAttrsToList (v: spec: nameValuePair "${san n}__${san v}" ({
      repository = repoRef n;
      variable_name = v;
      value = spec.value;
    } // optionalAttrs spec.operatorToggle { lifecycle.ignore_changes = [ "value" ]; })
    ) (filterAttrs (_: spec: spec.value != "") cfg.variables))
    ++ (mapAttrsToList (v: value: nameValuePair "${san n}__${san v}" {
      repository = repoRef n;
      variable_name = v;
      inherit value;
    }) s.variables));

  resource.github_actions_secret = perRepo (n: s: map (name: nameValuePair "${san n}__${san name}" {
    repository = repoRef n;
    secret_name = name;
    plaintext_value = secretRef name;
    lifecycle.precondition = {
      condition = "\${contains(keys(var.org_secrets), \"${name}\")}";
      error_message = "TF_VAR_org_secrets has no entry for ${name}";
    };
  }) (unique (cfg.secrets ++ s.secrets)));

  # ---- teams --------------------------------------------------------------------------------
  resource.github_team = mapAttrs' (slug: t: nameValuePair (san slug) {
    name = slug;
    description = t.description;
    privacy = t.privacy;
  }) cfg.teams;

  resource.github_team_membership = listToAttrs (concatLists (mapAttrsToList (slug: t:
    (map (login: nameValuePair "${san slug}__${san login}" {
      team_id = "\${github_team.${san slug}.id}"; username = login; role = "member";
    }) t.members)
    ++ (map (login: nameValuePair "${san slug}__${san login}" {
      team_id = "\${github_team.${san slug}.id}"; username = login; role = "maintainer";
    }) t.maintainers)) cfg.teams));

  resource.github_team_repository = listToAttrs (concatLists (mapAttrsToList (slug: t:
    mapAttrsToList (repo: perm: nameValuePair "${san slug}__${san repo}" {
      team_id = "\${github_team.${san slug}.id}";
      repository = if managed ? ${repo} then repoRef repo else repo;
      permission = perm;
    }) t.repos) cfg.teams));

  # ---- webhooks -------------------------------------------------------------------------------
  resource.github_organization_webhook = mapAttrs' (n: h: nameValuePair (san n) {
    active = true;
    events = h.events;
    configuration = hookConfig h;
  }) orgHooks;

  resource.github_repository_webhook = listToAttrs (concatLists (mapAttrsToList (n: h:
    map (repo: nameValuePair "${san n}__${san repo}" {
      repository = if managed ? ${repo} then repoRef repo else repo;
      active = true;
      events = h.events;
      configuration = hookConfig h;
    }) (if h.repos == [ ] then managedNames else h.repos)) repoHooks));

  # ---- rulesets (plan-gated) -------------------------------------------------------------------
  resource.github_repository_ruleset = optionalAttrs cfg.rulesets.enable (perRepo (n: s:
    if s.rulesets
    then map (f: nameValuePair "${san n}__${san (removeSuffix ".json" f)}" (toRulesetResource n f)) rulesetFiles
    else [ ]));

  # ---- imports: adopt what already exists --------------------------------------------------------
  # Repos flagged create = true are made by this stack, so they get NO import block
  # (importing a not-yet-existing repo fails the plan). Existing repos are adopted.
  import = concatLists [
    (if cfg.imports.repos then map (n: { to = "github_repository.${san n}"; id = n; })
      (builtins.filter (n: !(managed.${n}.create or false)) managedNames) else [ ])
    (if cfg.imports.teams then mapAttrsToList (slug: _: { to = "github_team.${san slug}"; id = slug; }) cfg.teams else [ ])
    (if cfg.imports.labels then concatLists (map (n: mapAttrsToList (label: _: {
      to = "github_issue_label.${san n}__${san label}"; id = "${n}:${label}";
    }) cfg.labels) managedNames) else [ ])
  ];
}

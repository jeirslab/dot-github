{
  # Organization baseline flake (ADR-005, INFRA-273). Company-agnostic by design: the org
  # name appears only in `github:<org>/.github` references that consumers write themselves.
  #
  # Two audiences:
  #   * Consuming repositories: build their dev shell on `lib.mkDevShell` so every
  #     developer gets the org tooling (Infisical CLI for secrets, sops/age for the
  #     SOPS-managed files that still exist, jq, just, gitleaks, python3 for the
  #     agent-config sync) plus whatever the repo adds. See README → "Nix".
  #   * Anyone, anywhere: `nix run github:<org>/.github#org-check` to see what a repo is
  #     missing, `nix run github:<org>/.github#sync-agent-config -- apply ...` to pull
  #     the agent baseline in by hand, `nix run github:<org>/.github#jira -- sync` for
  #     the token-cheap Jira interface (ADR-008).
  description = "Organization baseline: developer-environment tooling, agent-config sync, conformance checks";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    # ADR-010: the org's GitHub configuration (nix/github/) is a Terranix module set.
    terranix = { url = "github:terranix/terranix"; inputs.nixpkgs.follows = "nixpkgs"; };
  };

  outputs = { self, nixpkgs, flake-utils, terranix }:
    let
      # Tools every developer environment in the org provides, whatever the repository's stack.
      # Keep this list short and universal; stack-specific tools belong in the repo's flake.
      # The org Jira CLI (ADR-008): `jira sync|show|find|apply|standup|sweep`. Standard-library
      # Python; PyYAML lets change-set plans be YAML, certifi gives a CA bundle on bare NixOS.
      mkJira = pkgs: pkgs.writeShellApplication {
        name = "jira";
        runtimeInputs = [ (pkgs.python3.withPackages (ps: [ ps.pyyaml ps.certifi ])) pkgs.git ];
        text = ''
          exec python3 ${self}/claude/jira/jira_cli.py "$@"
        '';
      };

      orgPackages = pkgs: (with pkgs; [
        infisical # secrets: `infisical login`, `infisical run -- <cmd>` (ADR-005)
        sops      # SOPS-managed files still exist (deployments); inspect/decrypt
        age
        jq
        git
        just
        python3   # runs claude/sync.py
        gitleaks  # local secret scan (ideas.md 19)
      ]) ++ [ (mkJira pkgs) ];

      # Printed once when a shell opens; nudges rather than blocks. Silence with
      # ORG_SHELL_QUIET=1.
      orgShellHook = ''
        # Org git hooks (ADR-007): activate the versioned hooks the sync ships. Idempotent;
        # a repo without them is left alone. ORG_HOOKS_SKIP=1 bypasses them per command.
        if git rev-parse --show-toplevel >/dev/null 2>&1 && [ -d "$(git rev-parse --show-toplevel)/.claude/hooks/org/git" ]; then
          chmod +x "$(git rev-parse --show-toplevel)"/.claude/hooks/org/git/* 2>/dev/null || true
          if [ "$(git config --get core.hooksPath || true)" != ".claude/hooks/org/git" ]; then
            git config core.hooksPath .claude/hooks/org/git
            echo "org git hooks activated (core.hooksPath = .claude/hooks/org/git)"
          fi
        fi
        if [ -z "''${ORG_SHELL_QUIET:-}" ]; then
          echo "org dev shell — secrets via 'infisical login' / 'infisical run -- <cmd>'; Jira via 'jira sync' / 'jira show KEY-1'"
          if [ ! -f CLAUDE.md ] && [ ! -f .claude/CLAUDE.md ]; then
            echo "  note: no CLAUDE.md here — run 'nix run github:<org>/.github#org-check'"
          fi
        fi
      '';

      # Consuming repositories call this from their own flake:
      #
      #   inputs.org-baseline.url = "github:<org>/.github";
      #   ...
      #   devShells.default = org-baseline.lib.mkDevShell {
      #     inherit pkgs;
      #     extraPackages = [ pkgs.nodejs_22 ];
      #     shellHook = "echo repo-specific setup";
      #   };
      #
      #   ui = true adds Node (for the Playwright MCP the org-verifier subagent drives) and
      #   nixpkgs' Playwright browsers, with PLAYWRIGHT_BROWSERS_PATH set. The npm package
      #   `@playwright/mcp` pulls a Playwright whose version must match these browsers; pin
      #   it in the repo's .mcp.json if the nixpkgs bump and the npm release drift apart.
      mkDevShell =
        { pkgs
        , extraPackages ? [ ]
        , shellHook ? ""
        , name ? "org-dev"
        , ui ? false
        }:
        pkgs.mkShell {
          inherit name;
          packages = orgPackages pkgs ++ extraPackages
            ++ pkgs.lib.optionals ui [ pkgs.nodejs pkgs.playwright-driver.browsers ];
          shellHook = orgShellHook
            + pkgs.lib.optionalString ui ''
              export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
              export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1
            ''
            + "\n" + shellHook;
        };
    in
    {
      lib = { inherit orgPackages mkDevShell; mkRunnerHost = import ./nix/runner/host.nix; };
      # ADR-011: the org's ephemeral self-hosted runner, importable by any NixOS host.
      nixosModules.org-runner = import ./nix/runner/module.nix;
      nixosModules.default = import ./nix/runner/module.nix;
      # lib.mkRunnerHost (above): a complete fleet host (Proxmox LXC entry + NixOS config) for
      # the runner, for fleets built on fleetkit: add `(org-baseline.lib.mkRunnerHost { … })`
      # to mkFleet's modules. runner_bootstrap.py writes the equivalent file instead.
    }
    // flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        syncAgentConfig = pkgs.writeShellApplication {
          name = "sync-agent-config";
          runtimeInputs = [ pkgs.python3 ];
          text = ''
            exec python3 ${self}/claude/sync.py "$@"
          '';
        };

        orgCheck = pkgs.writeShellApplication {
          name = "org-check";
          runtimeInputs = [ pkgs.git pkgs.gnugrep pkgs.coreutils ];
          text = builtins.readFile ./claude/org-check.sh;
        };

        jiraCli = mkJira pkgs;

        # ADR-010 / INFRA-286: declarative GitHub org configuration. The rendered
        # config.tf.json for the `platform.github` stack, and a wrapper that runs OpenTofu
        # against it with a local or S3 backend and the token from the environment.
        # Company-agnostic: owner and values are in nix/github/manifest.nix; the token and
        # every secret value arrive as TF_VAR_* at run time.
        platformGithub = terranix.lib.terranixConfiguration {
          inherit system;
          modules = [ ./nix/github ];
        };

        platformGithubApp = pkgs.writeShellApplication {
          name = "platform-github";
          runtimeInputs = [ pkgs.opentofu pkgs.coreutils pkgs.jq ];
          text = ''
            # platform-github <init|plan|apply|import|show|state|output|validate> [tofu args...]
            #
            #   ORG_TF_DIR            work dir (default ./.tofu/platform-github, git-ignored)
            #   ORG_TF_STATE_BUCKET   S3 backend bucket; unset → local state in the work dir
            #   ORG_TF_STATE_KEY      state key (default tf/platform-github/terraform.tfstate)
            #   ORG_TF_STATE_REGION   bucket region (default us-east-1)
            #   TF_VAR_github_token   provider token (or GITHUB_TOKEN); never a file in the tree
            #   TF_VAR_org_secrets    JSON map of Actions secret values / webhook HMACs (default {})
            #   ORG_TF_ALLOW_DESTROY  =1 to permit `destroy` (repositories are prevent_destroy anyway)
            set -euo pipefail
            cmd="''${1:-plan}"; shift || true
            dir="''${ORG_TF_DIR:-$PWD/.tofu/platform-github}"
            mkdir -p "$dir"
            cp -f ${platformGithub} "$dir/config.tf.json"
            if [ -n "''${ORG_TF_STATE_BUCKET:-}" ]; then
              jq -n --arg b "$ORG_TF_STATE_BUCKET" --arg k "''${ORG_TF_STATE_KEY:-tf/platform-github/terraform.tfstate}" \
                    --arg r "''${ORG_TF_STATE_REGION:-us-east-1}" \
                    '{terraform:{backend:{s3:{bucket:$b,key:$k,region:$r}}}}' > "$dir/backend.tf.json"
            else
              jq -n '{terraform:{backend:{local:{path:"terraform.tfstate"}}}}' > "$dir/backend.tf.json"
              echo "platform-github: local state in $dir (set ORG_TF_STATE_BUCKET for S3)" >&2
            fi
            if [ -z "''${TF_VAR_github_token:-}" ] && [ -n "''${GITHUB_TOKEN:-}" ]; then export TF_VAR_github_token="$GITHUB_TOKEN"; fi
            if [ -z "''${TF_VAR_github_token:-}" ] && [ "$cmd" != "validate" ] && [ "$cmd" != "show" ]; then
              echo "platform-github: set TF_VAR_github_token (or GITHUB_TOKEN): a token with repo admin (+ org admin for teams/webhooks)" >&2; exit 2
            fi
            export TF_VAR_org_secrets="''${TF_VAR_org_secrets:-{\}}"
            if [ "$cmd" = "destroy" ] && [ "''${ORG_TF_ALLOW_DESTROY:-}" != "1" ]; then
              echo "platform-github: destroy refused (set ORG_TF_ALLOW_DESTROY=1; repositories stay prevent_destroy)" >&2; exit 2
            fi
            cd "$dir"
            if [ ! -d .terraform ] || [ "$cmd" = "init" ]; then tofu init -input=false -upgrade >&2; fi
            [ "$cmd" = "init" ] && exit 0
            exec tofu "$cmd" -input=false "$@"
          '';
        };

        installHooks = pkgs.writeShellApplication {
          name = "install-hooks";
          runtimeInputs = [ pkgs.git pkgs.coreutils ];
          text = ''
            root="$(git rev-parse --show-toplevel)"
            if [ ! -d "$root/.claude/hooks/org/git" ]; then
              echo "no .claude/hooks/org/git here — run sync-agent-config first"; exit 1
            fi
            chmod +x "$root"/.claude/hooks/org/git/*
            git config core.hooksPath .claude/hooks/org/git
            echo "org git hooks active: $(ls "$root/.claude/hooks/org/git" | tr '\n' ' ')"
          '';
        };
      in
      {
        packages = {
          inherit syncAgentConfig orgCheck installHooks jiraCli;
          sync-agent-config = syncAgentConfig;
          org-check = orgCheck;
          install-hooks = installHooks;
          jira = jiraCli;
          platform-github = platformGithub;          # the rendered config.tf.json
          platform-github-cli = platformGithubApp;
          default = orgCheck;
        };

        apps = {
          sync-agent-config = flake-utils.lib.mkApp { drv = syncAgentConfig; };
          org-check = flake-utils.lib.mkApp { drv = orgCheck; };
          install-hooks = flake-utils.lib.mkApp { drv = installHooks; };
          jira = flake-utils.lib.mkApp { drv = jiraCli; };
          platform-github = flake-utils.lib.mkApp { drv = platformGithubApp; };
          default = flake-utils.lib.mkApp { drv = orgCheck; };
        };

        # `nix flake check` — the R2 gate for this repository (ADR-002).
        checks = {
          sync-tests = pkgs.runCommand "sync-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            cd ${self}
            python3 -m unittest discover -s claude/tests -v
            touch "$out"
          '';
          # ADR-010: the GitHub stack renders and is valid JSON with the expected resources.
          platform-github-renders = pkgs.runCommand "platform-github-renders" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            python3 - ${platformGithub} <<'PY'
            import json, sys
            d = json.load(open(sys.argv[1]))
            assert "github_repository" in d["resource"], "no repositories rendered"
            assert d["provider"]["github"]["token"] == "''${var.github_token}", "token must come from a variable"
            assert not any("ghp_" in json.dumps(v) for v in d["resource"].values()), "credential-shaped string in the plan"
            print("platform-github:", {k: len(v) for k, v in d["resource"].items()})
            PY
            touch "$out"
          '';
          # ADR-011: the runner module evaluates inside a NixOS system with the policy applied.
          org-runner-evals = pkgs.writeText "org-runner-evals" (builtins.toJSON (
            let
              sys = nixpkgs.lib.nixosSystem {
                inherit system;
                modules = [
                  ./nix/runner/module.nix
                  {
                    networking.hostName = "runner-check";
                    fileSystems."/" = { device = "/dev/null"; fsType = "ext4"; };
                    boot.loader.grub.enable = false;
                    system.stateVersion = "24.11";
                    org.runner = { enable = true; url = "https://github.com/example-org"; tokenFile = "/run/secrets/runner"; count = 2; };
                  }
                ];
              };
              r = sys.config.services.github-runners;
            in
            assert builtins.length (builtins.attrNames r) == 2;
            assert builtins.all (v: v.ephemeral && v.replace && builtins.elem "org-fleet" v.extraLabels) (builtins.attrValues r);
            builtins.mapAttrs (_: v: { inherit (v) url ephemeral replace extraLabels; }) r
          ));
          org-check-self = pkgs.runCommand "org-check-self" { nativeBuildInputs = [ orgCheck pkgs.git ]; } ''
            # This repo is not a git checkout inside the sandbox, so only the file checks
            # are meaningful; the script must at least run and report.
            cd ${self}
            org-check . > "$out" || true
            grep -q 'flake.nix' "$out"
          '';
        };

        devShells.default = mkDevShell {
          inherit pkgs;
          name = "org-github";
          extraPackages = [ pkgs.actionlint pkgs.yamllint pkgs.opentofu platformGithubApp ];
          shellHook = ''
            echo "  workflows: actionlint .github/workflows/*.yml; docs: python3 .github/scripts/generate-workflow-docs.py"
          '';
        };
      });
}

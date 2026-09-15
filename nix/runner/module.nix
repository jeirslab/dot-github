{ config, lib, pkgs, ... }:

# The organization's self-hosted GitHub Actions runner as a NixOS module (ADR-011).
#
# Wraps `services.github-runners` with the org policy baked in: ephemeral (one job per
# registration, then re-register), replaced on re-registration, a fixed label set the
# workflows select with `runs-on: ${{ vars.ORG_RUNNER }}`, the tools the org workflows
# need (nix, opentofu, python3, git, jq, gh) and no Docker socket. Generic: the URL,
# token file and labels are options; nothing here names an organization.
#
# Import from a fleet host:
#   imports = [ org-baseline.nixosModules.org-runner ];
#   org.runner = { enable = true; url = "https://github.com/<org>"; tokenFile = config.sops.secrets."github/runner_token".path; };

let
  cfg = config.org.runner;
  inherit (lib) mkOption mkEnableOption mkIf types listToAttrs nameValuePair range optional;
in
{
  options.org.runner = {
    enable = mkEnableOption "the organization's self-hosted GitHub Actions runner (ADR-011)";

    url = mkOption {
      type = types.str;
      example = "https://github.com/example-org";
      description = ''
        Organization URL (org-scoped runner: the token needs `admin:org` (classic) or
        "Self-hosted runners: write" (fine-grained)) or a single repository URL.
        Never register a runner that public repositories can target.
      '';
    };

    tokenFile = mkOption {
      type = types.path;
      description = "File containing a PAT or registration token, provided by the secrets store at runtime (sops-nix). Never a path in the Nix store.";
    };

    labels = mkOption {
      type = types.listOf types.str;
      default = [ "org-fleet" ];
      description = "Labels the workflows select with `runs-on` (set `vars.ORG_RUNNER` to one of them). Default labels (self-hosted, linux, x64) are kept.";
    };

    count = mkOption {
      type = types.ints.positive;
      default = 1;
      description = "Parallel runner registrations on this host (one systemd unit each).";
    };

    namePrefix = mkOption {
      type = types.str;
      default = config.networking.hostName;
      defaultText = "config.networking.hostName";
      description = "Runner names are <namePrefix>-<n>.";
    };

    runnerGroup = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Runner group (organization runners only).";
    };

    extraPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = "Packages on the job PATH in addition to the org set.";
    };

    workDir = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Working directory root; null = the module default under /var/lib. Put it on a volume with space for checkouts and the Nix store is shared anyway.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = !(config.virtualisation.docker.enable or false) || !(config.virtualisation.docker.enableOnBoot or true);
        message = "org.runner: do not run Docker on a runner host; jobs would reach the socket (ADR-011).";
      }
    ];
    warnings = optional (lib.hasInfix "/" (lib.removePrefix "https://github.com/" cfg.url))
      "org.runner: ${cfg.url} is a repository-scoped runner; an organization URL lets every private repo use it.";

    # Job steps run `nix build` / `nix run` against this host's daemon: flakes on, and the
    # org binary cache can be added by the host.
    nix.settings.experimental-features = lib.mkDefault [ "nix-command" "flakes" ];

    services.github-runners = listToAttrs (map (i: nameValuePair "${cfg.namePrefix}-${toString i}" {
      enable = true;
      url = cfg.url;
      tokenFile = cfg.tokenFile;
      name = "${cfg.namePrefix}-${toString i}";
      ephemeral = true;          # one job per registration; the unit re-registers afterwards
      replace = true;            # a stale registration with the same name is replaced, not duplicated
      extraLabels = cfg.labels;
      noDefaultLabels = false;
      runnerGroup = cfg.runnerGroup;
      workDir = cfg.workDir;
      extraPackages = (with pkgs; [
        nix opentofu python3 git jq gh cacert coreutils gnutar gzip curl bash
      ]) ++ cfg.extraPackages;
      extraEnvironment = {
        NIX_CONFIG = "experimental-features = nix-command flakes";
      };
    }) (range 1 cfg.count));
  };
}

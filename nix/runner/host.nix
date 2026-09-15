# A complete fleet host for the org runner (ADR-011, INFRA-290): the Proxmox LXC entry plus
# the NixOS configuration importing ./module.nix. Parameterised so the deployments side is
# one call with the values only that fleet knows (cluster, node, vm id, IP).
#
#   org-baseline.lib.mkRunnerHost {
#     cluster = "<pve cluster>"; node = "<pve node>"; vm_id = 126; internal_ip = "10.40.0.126";
#     url = "https://github.com/<org>";
#   }
#
# The result is a fleet module (add it to mkFleet's `modules`). The runner_bootstrap.py
# script instead writes an equivalent self-contained host file into the deployments tree
# (with a vendored copy of module.nix) so no flake edit is needed there.
{ cluster
, node
, vm_id
, internal_ip
, url
, name ? "gh-runner"
, count ? 2
, labels ? [ "org-fleet" ]
, tokenSecret ? "integrations/github/runner_token"
, env ? "platform"
, stack ? "core"
, cpu_cores ? 4
, memory_mb ? 8192
, swap_mb ? 2048
, root_disk_datastore ? "local-lvm"
, tags ? [ "tools" "ci" ]
, extraNixos ? { }
}:
{ ... }:
{
  config.fleet.providers.proxmox.${cluster}.nodes.${node}.resources.lxc.${name} = {
    inherit env stack vm_id tags cpu_cores memory_mb swap_mb root_disk_datastore internal_ip;
    ip = "";
    network_mode = "single-internal";
    features = { nesting = true; fuse = false; keyctl = false; };
    notes = "Ephemeral GitHub Actions runner for the org's trusted jobs (ADR-011). Stateless; rebuilds identical.";

    nixos = { config, helpers, ... }: {
      imports = [ ./module.nix ];
      infra.networking.singleInterface = true;
      infra.network.tailnet.fleetNode = true;
      infra.auth.sssd.enable = true;
      infra.auth.sssd.allowedGroups = helpers.sshGroupsOf name;
      sops.secrets.${tokenSecret} = { };
      org.runner = {
        enable = true;
        inherit url count labels;
        tokenFile = config.sops.secrets.${tokenSecret}.path;
      };
    } // extraNixos;
  };
}

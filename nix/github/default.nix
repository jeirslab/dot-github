# The org's GitHub configuration as a Terranix module set (ADR-010, INFRA-286).
#
#   ./options.nix   schema (generic)
#   ./emit.nix      Terranix emitter → integrations/github resources (generic)
#   ./manifest.nix  this organization's values (content)
#
# From this repository: `nix build .#platform-github` renders config.tf.json;
# `nix run .#platform-github -- plan` runs OpenTofu against it (see flake.nix).
# From the deployments Terranix pipeline: import ./options.nix and ./emit.nix, keep or
# replace ./manifest.nix, and let fleetkit supply the backend and the provider token from
# `github.provider.secrets.token`.
{
  imports = [ ./options.nix ./emit.nix ./manifest.nix ];
}

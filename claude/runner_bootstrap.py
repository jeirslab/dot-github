#!/usr/bin/env python3
"""One-command bring-up of the org's fleet runner (ADR-011, INFRA-290).

The only thing a human does first: create ONE credential — a fine-grained PAT (org
permission "Self-hosted runners: write" + "Variables: write" + "Actions: write" on the org
.github repo) — and export it as GITHUB_RUNNER_PAT. Then, from a checkout of this repo,
with the deployments checkout beside it:

  python3 claude/runner_bootstrap.py --deployments ../deployments --owner <org> \\
      --cluster <pve cluster> --node <pve node>            # vm id and IP are picked free

Steps, each idempotent, each skippable with --skip <step>, all printed with --dry-run:

  secret   store the PAT in SOPS: `fleet devtools secrets keys add integrations/github/runner_token`
  host     write nix/hosts/pve/<name>.nix (self-contained: vendors nix/runner/module.nix
           into nix/modules/org-runner/) with a free vm_id/IP; commit is yours
  tf       `fleet deploy tf apply <stack> --yes`         (creates the LXC)
  nixos    `fleet deploy nixos apply host <name>`        (installs the runner units)
  dns      `fleet deploy nixos apply host netcore`       (the new name resolves)
  wait     poll the org runners API until <count> runners with the label are online
  select   set the repository variable ORG_RUNNER=auto (trusted jobs pick the fleet when online)
  smoke    dispatch org-runner-smoke.yml on the org .github repo and wait for it to go green

`fleet` commands run inside the deployments checkout; they need that repo's dev shell
(nix develop) on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STEPS = ["secret", "host", "tf", "nixos", "dns", "wait", "select", "smoke"]
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


# ---- GitHub API ----------------------------------------------------------------------------------

class GitHub:
    def __init__(self, token: str, api: str = API) -> None:
        self.token, self.api = token, api.rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode()
                return r.status, (json.loads(txt) if txt else None)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "null")

    def runners(self, owner: str) -> list[dict]:
        code, data = self.request("GET", f"/orgs/{owner}/actions/runners?per_page=100")
        if code != 200:
            raise RuntimeError(f"listing org runners failed: HTTP {code} {data}")
        return data.get("runners", [])  # type: ignore[union-attr]

    def set_variable(self, repo: str, name: str, value: str) -> str:
        code, _ = self.request("PATCH", f"/repos/{repo}/actions/variables/{name}", {"name": name, "value": value})
        if code == 204:
            return "updated"
        code, data = self.request("POST", f"/repos/{repo}/actions/variables", {"name": name, "value": value})
        if code == 201:
            return "created"
        raise RuntimeError(f"setting variable {name} failed: HTTP {code} {data}")

    def dispatch(self, repo: str, workflow: str, ref: str) -> None:
        code, data = self.request("POST", f"/repos/{repo}/actions/workflows/{workflow}/dispatches", {"ref": ref})
        if code != 204:
            raise RuntimeError(f"dispatch failed: HTTP {code} {data}")

    def latest_run(self, repo: str, workflow: str, since_iso: str) -> dict | None:
        code, data = self.request("GET", f"/repos/{repo}/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=5&created=>={since_iso}")
        if code != 200:
            return None
        runs = data.get("workflow_runs", [])  # type: ignore[union-attr]
        return runs[0] if runs else None


# ---- local helpers -------------------------------------------------------------------------------

def used_ids_and_ips(hosts_dir: Path) -> tuple[set[int], set[str]]:
    ids, ips = set(), set()
    for f in hosts_dir.rglob("*.nix"):
        t = f.read_text(errors="replace")
        ids.update(int(x) for x in re.findall(r"vm_id\s*=\s*(\d+)", t))
        ips.update(re.findall(r'internal_ip\s*=\s*"([0-9.]+)"', t))
    return ids, ips


def pick_free(hosts_dir: Path, subnet: str, lo: int, hi: int) -> tuple[int, str]:
    """Lowest n in [lo, hi] where neither vm_id n nor <subnet>.n is used."""
    ids, ips = used_ids_and_ips(hosts_dir)
    for n in range(lo, hi + 1):
        if n not in ids and f"{subnet}.{n}" not in ips:
            return n, f"{subnet}.{n}"
    raise SystemExit(f"no free vm_id/IP pair in {lo}..{hi} under {hosts_dir}")


def render_host(name: str, cluster: str, node: str, vm_id: int, ip: str, url: str, count: int, labels: list[str],
                token_secret: str, source_rev: str, cpu: int = 4, mem: int = 8192, swap: int = 2048) -> str:
    labels_nix = " ".join(f'"{l}"' for l in labels)
    return f'''{{ ... }}:

# {name} — the org's ephemeral GitHub Actions runner (ADR-011 in the org .github repo).
# GENERATED by claude/runner_bootstrap.py from {source_rev}; re-running the bootstrap
# rewrites this file and the vendored module. Stateless; rebuilds identical.

{{
  config.fleet.providers.proxmox.{cluster}.nodes.{node}.resources.lxc.{name} = {{
    env = "platform"; stack = "core";
    vm_id = {vm_id};
    tags = [ "tools" "ci" ];
    ip = ""; internal_ip = "{ip}";
    cpu_cores = {cpu}; memory_mb = {mem}; swap_mb = {swap};
    root_disk_datastore = "local-lvm";
    network_mode = "single-internal";
    features = {{ nesting = true; fuse = false; keyctl = false; }};
    notes = "Ephemeral GitHub Actions runner for the org's trusted jobs (ADR-011). Stateless.";

    nixos = {{ config, helpers, ... }}: {{
      imports = [ ../../modules/org-runner ];
      infra.networking.singleInterface = true;
      infra.network.tailnet.fleetNode = true;
      infra.auth.sssd.enable = true;
      infra.auth.sssd.allowedGroups = helpers.sshGroupsOf "{name}";
      sops.secrets."{token_secret}" = {{ }};
      org.runner = {{
        enable = true;
        url = "{url}";
        tokenFile = config.sops.secrets."{token_secret}".path;
        labels = [ {labels_nix} ];
        count = {count};
      }};
    }};
  }};
}}
'''


def run(cmd: list[str], cwd: Path | None, dry: bool, env: dict | None = None) -> None:
    print(f"+ {' '.join(cmd)}" + (f"   (in {cwd})" if cwd else ""))
    if dry:
        return
    r = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise SystemExit(f"bootstrap: `{cmd[0]}` exited {r.returncode}; fix and re-run (steps are idempotent, use --skip for the ones done)")


def source_rev() -> str:
    try:
        return "org .github@" + subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "org .github"


# ---- steps -----------------------------------------------------------------------------------------

def step_secret(a, dry):
    pat = os.environ.get(a.token_env)
    if not pat:
        raise SystemExit(f"bootstrap: export {a.token_env} (the one manual step: a PAT with 'Self-hosted runners: write' on the org)")
    print(f"+ fleet devtools secrets keys add {a.token_secret} <{a.token_env}>   (in {a.deployments})")
    if not dry:
        r = subprocess.run(["fleet", "devtools", "secrets", "keys", "add", a.token_secret, pat], cwd=a.deployments)
        if r.returncode != 0:
            raise SystemExit("bootstrap: storing the secret failed (is the deployments dev shell active?)")


def step_host(a, dry):
    hosts = a.deployments / "nix" / "hosts"
    host_file = hosts / "pve" / f"{a.name}.nix"
    existing = host_file.read_text() if host_file.exists() else ""
    m_id, m_ip = re.search(r"vm_id\s*=\s*(\d+)", existing), re.search(r'internal_ip\s*=\s*"([0-9.]+)"', existing)
    if a.vm_id and a.ip:
        vm_id, ip = a.vm_id, a.ip
    elif m_id and m_ip:
        vm_id, ip = int(m_id.group(1)), m_ip.group(1)
        print(f"  keeping the existing host's vm_id {vm_id}, ip {ip}")
    else:
        vm_id, ip = pick_free(hosts, a.subnet, a.id_range[0], a.id_range[1])
        print(f"  free pair: vm_id {vm_id}, ip {ip}")
    module_dir = a.deployments / "nix" / "modules" / "org-runner"
    text = render_host(a.name, a.cluster, a.node, vm_id, ip, a.url, a.count, a.labels, a.token_secret, source_rev())
    module_src = (ROOT / "nix" / "runner" / "module.nix").read_text()
    module_text = f"# VENDORED from the org .github repository nix/runner/module.nix ({source_rev()}) by\n# claude/runner_bootstrap.py — edit upstream, re-run the bootstrap.\n" + module_src
    for path, content in ((host_file, text), (module_dir / "default.nix", module_text)):
        state = "unchanged" if path.exists() and path.read_text() == content else ("update" if path.exists() else "create")
        print(f"  {state:9} {path.relative_to(a.deployments)}")
        if not dry and state != "unchanged":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    print(f"  then: git -C {a.deployments} add nix/hosts/pve/{a.name}.nix nix/modules/org-runner && commit with the ticket prefix; validate: nix eval .#colmena --apply 'c: builtins.attrNames c'")


def step_tf(a, dry):
    run(["fleet", "deploy", "tf", "apply", a.stack, "--yes"], a.deployments, dry)


def step_nixos(a, dry):
    run(["fleet", "deploy", "nixos", "apply", "host", a.name], a.deployments, dry)


def step_dns(a, dry):
    run(["fleet", "deploy", "nixos", "apply", "host", "netcore"], a.deployments, dry)


def step_wait(a, dry, gh: GitHub | None):
    print(f"+ wait until {a.count} online runner(s) with label '{a.labels[0]}' in org {a.owner} (timeout {a.timeout}s)")
    if dry or gh is None:
        return
    deadline = time.time() + a.timeout
    while True:
        online = [r["name"] for r in gh.runners(a.owner)
                  if r.get("status") == "online" and any(l.get("name") == a.labels[0] for l in r.get("labels", []))]
        print(f"  online: {online or 'none'}")
        if len(online) >= a.count:
            return
        if time.time() > deadline:
            raise SystemExit("bootstrap: runners did not come online in time; check `fleet remote " + a.name + " 'journalctl -u github-runner-*'`")
        time.sleep(a.poll)


def step_select(a, dry, gh: GitHub | None):
    print(f"+ set variable ORG_RUNNER=auto on {a.owner}/.github")
    if dry or gh is None:
        return
    print("  " + gh.set_variable(f"{a.owner}/.github", "ORG_RUNNER", "auto"))


def step_smoke(a, dry, gh: GitHub | None):
    print(f"+ dispatch org-runner-smoke.yml on {a.owner}/.github@{a.ref} and wait")
    if dry or gh is None:
        return
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
    gh.dispatch(f"{a.owner}/.github", "org-runner-smoke.yml", a.ref)
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        time.sleep(a.poll)
        r = gh.latest_run(f"{a.owner}/.github", "org-runner-smoke.yml", since)
        if r and r.get("status") == "completed":
            print(f"  {r.get('conclusion')}: {r.get('html_url')}")
            if r.get("conclusion") != "success":
                raise SystemExit("bootstrap: the smoke run failed; read it, then re-run with --only smoke")
            return
        print(f"  {r.get('status') if r else 'not started yet'} …")
    raise SystemExit("bootstrap: the smoke run did not finish in time")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deployments", type=Path, required=True, help="path to the deployments checkout")
    ap.add_argument("--owner", required=True, help="GitHub organization")
    ap.add_argument("--cluster", required=True, help="fleet Proxmox cluster key (fleet.providers.proxmox.<cluster>)")
    ap.add_argument("--node", required=True, help="Proxmox node key")
    ap.add_argument("--name", default="gh-runner")
    ap.add_argument("--stack", default="platform.core", help="leaf stack for `fleet deploy tf apply`")
    ap.add_argument("--vm-id", type=int, default=None); ap.add_argument("--ip", default=None)
    ap.add_argument("--subnet", default="10.40.0"); ap.add_argument("--id-range", type=int, nargs=2, default=[126, 199])
    ap.add_argument("--count", type=int, default=2); ap.add_argument("--labels", nargs="+", default=["org-fleet"])
    ap.add_argument("--url", default=None, help="runner registration URL (default https://github.com/<owner>)")
    ap.add_argument("--token-env", default="GITHUB_RUNNER_PAT", help="env var holding the PAT (never passed on the command line)")
    ap.add_argument("--token-secret", default="integrations/github/runner_token", help="SOPS key path")
    ap.add_argument("--ref", default="main", help="ref of the org .github repo to dispatch the smoke workflow on")
    ap.add_argument("--timeout", type=int, default=900); ap.add_argument("--poll", type=int, default=20)
    ap.add_argument("--only", nargs="+", choices=STEPS, default=None); ap.add_argument("--skip", nargs="+", choices=STEPS, default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    a.url = a.url or f"https://github.com/{a.owner}"
    a.deployments = a.deployments.resolve()
    if not (a.deployments / "nix" / "hosts").is_dir():
        raise SystemExit(f"bootstrap: {a.deployments} does not look like the deployments checkout (no nix/hosts)")
    steps = [s for s in (a.only or STEPS) if s not in a.skip]
    pat = os.environ.get(a.token_env)
    gh = GitHub(pat) if pat and not a.dry_run else None
    if not a.dry_run and any(s in steps for s in ("wait", "select", "smoke")) and gh is None:
        raise SystemExit(f"bootstrap: {a.token_env} is needed for the GitHub API steps (wait/select/smoke)")
    if not a.dry_run and any(s in steps for s in ("secret", "tf", "nixos", "dns")) and not shutil.which("fleet"):
        raise SystemExit("bootstrap: `fleet` not on PATH — run inside the deployments dev shell (nix develop) or pass --dry-run")
    for s in steps:
        print(f"\n== {s}")
        {"secret": lambda: step_secret(a, a.dry_run), "host": lambda: step_host(a, a.dry_run), "tf": lambda: step_tf(a, a.dry_run),
         "nixos": lambda: step_nixos(a, a.dry_run), "dns": lambda: step_dns(a, a.dry_run), "wait": lambda: step_wait(a, a.dry_run, gh),
         "select": lambda: step_select(a, a.dry_run, gh), "smoke": lambda: step_smoke(a, a.dry_run, gh)}[s]()
    print("\nbootstrap: done" + (" (dry run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

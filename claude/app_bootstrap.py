#!/usr/bin/env python3
"""One-command creation of the org's sync GitHub App (ADR-015).

A GitHub App cannot be created headlessly — GitHub's App-manifest flow needs a browser and
a redirect that hands back a one-time code (the private key is returned exactly once, to
whoever holds that code). A CI runner has neither a browser nor an endpoint your browser can
reach, so this is a LOCAL command: it opens your browser to GitHub's "create app" page
pre-filled from a manifest, and captures the redirect on 127.0.0.1.

  app-bootstrap --owner <org-or-user>            # one browser click → App ID + private key
  app-bootstrap --owner <org> --set-secrets      # also set ORG_APP_ID (var) + ORG_APP_PRIVATE_KEY (secret) on <owner>/.github
  app-bootstrap --owner <org> --dry-run          # print the manifest + URLs, do nothing

After it prints the App ID and writes the private key, install the App on the org/repos via
the printed URL. The workflows then mint short-lived tokens per run (actions/create-github-app-token);
no long-lived PAT is stored. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

# The org-sync App needs exactly what the fleet workflows do (ADR-014 boundary).
DEFAULT_PERMISSIONS = {"contents": "write", "pull_requests": "write", "workflows": "write",
                       "issues": "write", "metadata": "read"}


def build_manifest(name: str, redirect_url: str, permissions: dict | None = None,
                   events: list | None = None) -> dict:
    return {
        "name": name,
        "url": redirect_url,
        "redirect_url": redirect_url,
        "public": False,
        "default_permissions": permissions or DEFAULT_PERMISSIONS,
        "default_events": events or [],
    }


def new_app_url(owner: str, account_type: str) -> str:
    """The 'create a GitHub App' endpoint — org-scoped or user-scoped."""
    if account_type.lower() in ("org", "organization"):
        return f"https://github.com/organizations/{owner}/settings/apps/new"
    return "https://github.com/settings/apps/new"


def detect_account_type(owner: str) -> str:
    """'Organization' or 'User' from the public API (no auth needed)."""
    try:
        with urllib.request.urlopen(f"https://api.github.com/users/{owner}", timeout=10) as r:
            return json.load(r).get("type", "User")
    except Exception:
        return "User"


def parse_conversion(data: dict) -> tuple[str, str, str]:
    """(app_id, slug, pem) from the /app-manifests/{code}/conversions response."""
    app_id = str(data["id"])
    return app_id, data.get("slug", ""), data["pem"]


def exchange_code(code: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/app-manifests/{code}/conversions",
        method="POST", headers={"Accept": "application/vnd.github+json", "User-Agent": "app-bootstrap"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _serve_and_capture(manifest: dict, submit_url: str, state: str) -> str:
    """Serve the auto-submitting form, open the browser, return the captured code."""
    import html
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    captured: dict = {}
    manifest_json = json.dumps(manifest)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            path = urlparse(self.path)
            if path.path == "/callback":
                q = parse_qs(path.query)
                captured["code"] = (q.get("code") or [""])[0]
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(b"<h3>GitHub App created. You can close this tab and return to the terminal.</h3>")
            else:
                form = (f'<form id="f" action="{html.escape(submit_url)}?state={html.escape(state)}" method="post">'
                        f'<input type="hidden" name="manifest" value="{html.escape(manifest_json)}"></form>'
                        f'<p>Redirecting you to GitHub to create the App…</p>'
                        f'<script>document.getElementById("f").submit()</script>')
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(form.encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    # The manifest's redirect_url must match the port we actually bound.
    manifest["redirect_url"] = manifest["url"] = f"http://localhost:{port}/callback"
    manifest_json = json.dumps(manifest)
    url = f"http://localhost:{port}/"
    print(f"Opening {url} — if no browser opens, visit it manually (you must be logged into GitHub).")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    while "code" not in captured:
        server.handle_request()
    return captured["code"]


def set_secrets(owner: str, app_id: str, pem_path: Path) -> None:
    repo = f"{owner}/.github"
    subprocess.run(["gh", "variable", "set", "ORG_APP_ID", "--repo", repo, "--body", app_id], check=True)
    subprocess.run(["gh", "secret", "set", "ORG_APP_PRIVATE_KEY", "--repo", repo],
                   input=pem_path.read_text(), text=True, check=True)
    print(f"set ORG_APP_ID (variable) and ORG_APP_PRIVATE_KEY (secret) on {repo}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="org or user login that will own the App")
    ap.add_argument("--name", help="App name (default: <owner>-org-sync)")
    ap.add_argument("--account-type", choices=("auto", "org", "user"), default="auto")
    ap.add_argument("--key-out", type=Path, help="where to write the private key (default: ./<slug>.private-key.pem)")
    ap.add_argument("--set-secrets", action="store_true", help="also set ORG_APP_ID + ORG_APP_PRIVATE_KEY on <owner>/.github")
    ap.add_argument("--dry-run", action="store_true", help="print the manifest and URLs; create nothing")
    a = ap.parse_args(argv)

    name = a.name or f"{a.owner}-org-sync"
    acct = detect_account_type(a.owner) if a.account_type == "auto" else a.account_type
    submit_url = new_app_url(a.owner, acct)
    manifest = build_manifest(name, "http://localhost/callback")

    if a.dry_run:
        print(f"account type: {acct}\ncreate-app URL: {submit_url}\nmanifest:\n{json.dumps(manifest, indent=2)}")
        return 0

    code = _serve_and_capture(manifest, submit_url, state="app-bootstrap")
    if not code:
        raise SystemExit("app-bootstrap: no code returned from GitHub (App not created)")
    app_id, slug, pem = parse_conversion(exchange_code(code))
    key_out = a.key_out or Path(f"./{slug or name}.private-key.pem")
    key_out.write_text(pem)
    key_out.chmod(0o600)
    print(f"\nApp created: id={app_id} slug={slug}\nprivate key: {key_out}")
    install = (f"https://github.com/organizations/{a.owner}/settings/apps/{slug}/installations"
               if acct.lower().startswith("org") else f"https://github.com/apps/{slug}/installations/new")
    if a.set_secrets:
        set_secrets(a.owner, app_id, key_out)
    print(f"\nNext: install the App and grant it your repos → {install}")
    print("Then the workflows mint tokens automatically (actions/create-github-app-token); no PAT needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

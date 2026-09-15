#!/usr/bin/env python3
"""Supply chain, part 2 (ideas.md 18): new dependencies must exist and not be brand new.

The paper's "slopsquatting" risk: AI-generated code imports a package that does not
exist, or that an attacker registered after a model hallucinated the name. For every
dependency *added* in the range (npm package.json, Python pyproject.toml, Nix flake
inputs) this script checks the registry: does it exist, how old is it, how many
versions. Young (< --min-age-days) or missing packages are errors.

  new_deps.py --range base..head [--root .] [--min-age-days 30] [--offline]
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import annotate, git, parse_common  # noqa: E402

MANIFESTS = ("package.json", "pyproject.toml", "flake.nix")


def _show(root: Path, ref: str, path: str) -> str:
    try:
        return git(root, "show", f"{ref}:{path}")
    except Exception:
        return ""


def npm_deps(text: str) -> set[str]:
    try:
        d = json.loads(text or "{}")
    except json.JSONDecodeError:
        return set()
    out: set[str] = set()
    for k in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        out |= set((d.get(k) or {}).keys())
    return out


def py_deps(text: str) -> set[str]:
    try:
        d = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return set()
    specs: list[str] = list((d.get("project") or {}).get("dependencies") or [])
    for grp in ((d.get("project") or {}).get("optional-dependencies") or {}).values():
        specs += list(grp)
    for grp in ((d.get("dependency-groups") or {})).values():
        specs += [s for s in grp if isinstance(s, str)]
    names = set()
    for s in specs:
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", s)
        if m:
            names.add(m.group(1).lower())
    return names


def flake_inputs(text: str) -> set[str]:
    return set(re.findall(r'url\s*=\s*"([^"]+)"', text or ""))


def _get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "org-guardrails"}), timeout=15) as r:
            return json.load(r)
    except Exception:
        return None


def check_npm(name: str) -> tuple[bool, float | None, int]:
    d = _get(f"https://registry.npmjs.org/{name}")
    if not d or "time" not in d:
        return False, None, 0
    created = d["time"].get("created")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z", "+00:00"))).days if created else None
    return True, age, len(d.get("versions") or {})


def check_pypi(name: str) -> tuple[bool, float | None, int]:
    d = _get(f"https://pypi.org/pypi/{name}/json")
    if not d:
        return False, None, 0
    times = []
    for files in (d.get("releases") or {}).values():
        for f in files:
            if f.get("upload_time_iso_8601"):
                times.append(datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00")))
    age = (datetime.now(timezone.utc) - min(times)).days if times else None
    return True, age, len(d.get("releases") or {})


def main(argv: list[str] | None = None) -> int:
    ap = parse_common(argv or sys.argv[1:], __doc__)
    ap.add_argument("--min-age-days", type=int, default=30)
    ap.add_argument("--offline", action="store_true", help="list new deps without querying registries")
    args = ap.parse_args(argv)
    if not args.rng or ".." not in args.rng:
        print("new_deps: --range base..head is required")
        return 2
    base, head = args.rng.split("..", 1)
    root: Path = args.root.resolve()
    added: list[tuple[str, str, str]] = []  # (ecosystem, name, manifest)

    for path in git(root, "diff", "--name-only", args.rng).splitlines():
        name = Path(path).name
        if name not in MANIFESTS:
            continue
        before, after = _show(root, base, path), _show(root, head, path)
        if name == "package.json":
            for n in sorted(npm_deps(after) - npm_deps(before)):
                added.append(("npm", n, path))
        elif name == "pyproject.toml":
            for n in sorted(py_deps(after) - py_deps(before)):
                added.append(("pypi", n, path))
        else:
            for n in sorted(flake_inputs(after) - flake_inputs(before)):
                added.append(("nix", n, path))

    errors = 0
    report = []
    for eco, name, manifest in added:
        if eco == "nix":
            level = "notice" if re.match(r"^(github:|git\+https://|https://)", name) else "warning"
            annotate(level, f"new flake input {name} — confirm the source is the intended upstream and is pinned in flake.lock", manifest)
            report.append({"ecosystem": eco, "name": name, "status": "review"})
            continue
        if args.offline:
            annotate("notice", f"new {eco} dependency {name} (registry check skipped: --offline)", manifest)
            report.append({"ecosystem": eco, "name": name, "status": "unchecked"})
            continue
        exists, age, nver = (check_npm if eco == "npm" else check_pypi)(name)
        if not exists:
            annotate("error", f"new {eco} dependency {name!r} does not exist on the registry — hallucinated or typo (slopsquatting risk)", manifest)
            errors += 1
            report.append({"ecosystem": eco, "name": name, "status": "missing"})
        elif age is not None and age < args.min_age_days:
            annotate("error", f"new {eco} dependency {name!r} is only {age} days old ({nver} versions) — verify by hand before trusting it", manifest)
            errors += 1
            report.append({"ecosystem": eco, "name": name, "status": "young", "age_days": age})
        else:
            annotate("notice", f"new {eco} dependency {name} — {nver} versions, {age} days old", manifest)
            report.append({"ecosystem": eco, "name": name, "status": "ok", "age_days": age})

    if args.json:
        print(json.dumps({"errors": errors, "added": report}))
    else:
        print(f"new-deps: {len(added)} added, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
